"""Interfaz visual de drama-ia: una página local en el navegador para todo el proceso."""
import json
import re
import mimetypes
import os
import shutil
import subprocess
import sys
import threading
import time
import urllib.request
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import quote, unquote, urlparse

import config
import nuevo_proyecto
import orquestador

PUERTO = 8190
RAIZ = Path(__file__).resolve().parent
PY = sys.executable  # el python del venv de ComfyUI
PY_VOZ = config.PY_VOZ
PY_WHISPER = config.PY_WHISPER
PY_NUBE = config.PY_NUBE
EDITABLES = {"proyecto.json", "guion.md", "planos.json", "sonido.json", "montaje.json", "labios.json"}
RAICES = {"s": config.SALIDAS, "p": config.PROYECTOS}  # lo único que se sirve por /media/


def responde(url):
    try:
        urllib.request.urlopen(url, timeout=2).read()
        return True
    except Exception:
        return False


SERVICIOS = {
    "comfyui": {"url": config.COMFY_URL + "/system_stats",
                "cmd": ["powershell", "-ExecutionPolicy", "Bypass", "-File", str(RAIZ / "arrancar_comfyui.ps1")],
                "log": RAIZ / "setup" / "comfyui.log"},
    "ollama": {"url": config.OLLAMA_URL + "/api/version", "cmd": ["ollama", "serve"],
               "log": RAIZ / "setup" / "ollama.log", "env": {"OLLAMA_MODELS": config.OLLAMA_MODELOS} if config.OLLAMA_MODELOS else {}},
}
procesos_servicio = {}


def arrancar_servicio(nombre, intento=1):
    s = SERVICIOS[nombre]
    if responde(s["url"]):
        return
    env = {**os.environ, **s.get("env", {})}
    log = open(s["log"], "w", encoding="utf-8", errors="replace")
    p = subprocess.Popen(s["cmd"], stdout=log, stderr=subprocess.STDOUT, env=env,
                         creationflags=subprocess.CREATE_NO_WINDOW)
    procesos_servicio[nombre] = p
    threading.Thread(target=vigilar_arranque, args=(nombre, p, intento), daemon=True).start()


def vigilar_arranque(nombre, p, intento):
    """Smart App Control a veces bloquea un momento torch.dll al arrancar (WinError 4551) y al reintentar"""
    try:
        p.wait(timeout=240)
    except subprocess.TimeoutExpired:
        return  # sigue vivo: arrancó bien
    texto = SERVICIOS[nombre]["log"].read_text(encoding="utf-8", errors="replace")
    if "4551" in texto and intento < 4 and procesos_servicio.get(nombre) is p:
        time.sleep(5 * intento)
        if procesos_servicio.get(nombre) is p:  # nadie lo ha apagado a propósito mientras tanto
            arrancar_servicio(nombre, intento + 1)


def matar_arbol(pid):
    subprocess.run(["taskkill", "/PID", str(pid), "/T", "/F"], capture_output=True)


def detener_servicio(nombre):
    """Solo apaga lo NUESTRO: puede haber otros ComfyUI (p."""
    p = procesos_servicio.pop(nombre, None)
    if p and p.poll() is None:
        matar_arbol(p.pid)
        return "apagado"
    if nombre == "ollama":
        return "Ollama no lo arrancó drama-ia: no se apaga (puede estar usándolo otro programa)"
    subprocess.run(["powershell", "-NoProfile", "-Command",
                    "Get-CimInstance Win32_Process -Filter \"Name='python.exe'\" | Where-Object { "
                    "$_.CommandLine -like '*main.py*--port 8188*' -and $_.CommandLine -like '*IA-Video-Salidas*' } | "
                    "ForEach-Object { Stop-Process -Id $_.ProcessId -Force }"],
                   capture_output=True)
    return "apagado"


class Trabajo:
    def __init__(self):
        self.lock = threading.Lock()
        self.proc = None
        self.paso = None
        self.proyecto = None
        self.lineas = []
        self.codigo = None
        self.inicio = None
        self.fin = None
        self.cancelado = False

    def estado(self):
        with self.lock:
            return {"activo": self.proc is not None and self.codigo is None, "paso": self.paso, "proyecto": self.proyecto,
                    "log": self.lineas[-300:], "codigo": self.codigo, "cancelado": self.cancelado,
                    "segundos": round((self.fin or time.time()) - self.inicio) if self.inicio else 0}

    def lanzar(self, proyecto, paso, comandos):
        with self.lock:
            if self.proc is not None and self.codigo is None:
                raise RuntimeError("Ya hay un trabajo en marcha: espera a que termine o cancélalo")
            self.paso, self.proyecto, self.lineas, self.codigo = paso, proyecto, [], None
            self.inicio, self.fin, self.cancelado = time.time(), None, False
        threading.Thread(target=self._correr, args=(comandos,), daemon=True).start()

    def _correr(self, comandos):
        env = {**os.environ, "PYTHONIOENCODING": "utf-8", "PYTHONUNBUFFERED": "1", "DRAMA_IA_NO_ABRIR": "1",
               "HF_HOME": config.HF_HOME}
        codigo = 0
        for cmd in comandos:
            self._log(f"$ {' '.join(Path(c).name if i < 2 else c for i, c in enumerate(cmd))}")
            with self.lock:
                if self.cancelado:
                    break
                self.proc = subprocess.Popen(cmd, cwd=RAIZ, env=env, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                                             creationflags=subprocess.CREATE_NO_WINDOW)
            for linea in iter(self.proc.stdout.readline, b""):
                self._log(linea.decode("utf-8", "replace").rstrip().split("\r")[-1])
            codigo = self.proc.wait()
            if codigo != 0:
                break
        with self.lock:
            self.codigo = -1 if self.cancelado else codigo
            self.fin = time.time()
        self._log("— cancelado —" if self.cancelado else ("— terminado —" if codigo == 0 else f"— error (código {codigo}) —"))

    def _log(self, texto):
        ruido = ("FutureWarning", "warnings.warn", "flash-attn", "SoX", "sox", "sourceforge", "path variables",
                 "reconoce como", "programa o archivo", "pad_token_id")
        if texto.strip() and not texto.strip("*") == "" and not any(r in texto for r in ruido):
            with self.lock:
                self.lineas.append(texto)

    def cancelar(self):
        with self.lock:
            self.cancelado = True
            if self.proc and self.proc.poll() is None:
                matar_arbol(self.proc.pid)


trabajo = Trabajo()


def comandos_del_paso(n, paso, op):
    planos = ["--planos", op["planos"]] if op.get("planos") else []
    if paso == "planos":
        return [[PY, "guion_a_planos.py", n]]
    if paso == "personajes":
        return [[PY, "generar.py", "personajes", n, "--variantes", str(op.get("variantes", 4))]]
    if paso == "imagenes":
        return [[PY, "generar.py", "imagenes", n, "--variantes", str(op.get("variantes", 3)), *planos]]
    if paso == "videos":
        return [[PY, "generar.py", "videos", n, "--tomas", str(op.get("tomas", 2)), *planos]]
    if paso == "voces_disenar":
        return [[PY_VOZ, "voces.py", "disenar", n, "--variantes", str(op.get("variantes", 2))]]
    if paso == "voces_locutar":
        return [[PY_VOZ, "voces.py", "locutar", n]]
    if paso == "labios":
        return [[PY, "labios.py", n, "--tomas", str(op.get("tomas", 2)), *planos]]
    if paso == "sonido":
        solo = ["--solo", op["solo"]] if op.get("solo") else []
        return [[PY, "audio.py", "generar", n, "--variantes", str(op.get("variantes", 3)), *solo]]
    if paso == "rapido":
        return [[PY, "montar.py", n], [PY, "mezclar.py", n]]
    if paso == "sonido_auto":
        return [[PY, "sonido_auto.py", n, *(["--reemplazar"] if op.get("reemplazar") else [])]]
    if paso == "orquestar":  # producción automática; con planos + rehacer = regeneración quirúrgica
        extra = (["--rehacer", op["rehacer"]] if op.get("rehacer") else []) + (["--hasta", op["hasta"]] if op.get("hasta") else [])
        return [[PY, "orquestador.py", n, *planos, *extra]]
    if paso == "guion_ia":  # proyecto NUEVO desde un tema: la IA investiga, verifica fuentes y escribe
        extra = (["--voz-de", op["voz_de"]] if op.get("voz_de") else []) + (["--idioma", op["idioma"]] if op.get("idioma") else [])
        extra += ["--proveedor", op["proveedor"]] if op.get("proveedor") else [] 
        return [[PY_NUBE, "guionista.py", "crear", n, op["tema"], "--segundos", str(op.get("segundos", 60)), *extra]]
    if paso == "subtitulos":
        return [[PY_WHISPER, "subtitulos.py", n, "--fuente", op.get("fuente", "videos_720_x2")]]
    if paso == "evaluar":  # 3er modelo: Gemini (gratis) ve el vídeo con audio y lo puntúa
        return [[PY_NUBE, "evaluar.py", n, *(["--final"] if op.get("final") else [])]]
    if paso == "final":
        if not op.get("forzar"):  # estándar de publicación: 8 o más en visual, ritmo y audio
            import evaluar
            ok, motivo, _ = evaluar.estandar(n, "final")
            if not ok:
                raise ValueError(f"No pasa el estándar para render final ({evaluar.texto_meta(evaluar.EXIGE['final'])} "
                                 f"en el borrador): {motivo}")
        subs =[[PY_WHISPER, "subtitulos.py", n, "--fuente", "videos_720_x2"]] if config.cargar_proyecto(n)["subtitulos"] else []
        return [[PY, "acabado.py", n, "--reescalar", "--interpolar"],
                [PY, "montar.py", n, "--fuente", "videos_720_x2", "--fps", "32"],
                [PY, "mezclar.py", n, "--fuente", "videos_720_x2"], *subs]
    raise ValueError(f"paso desconocido: {paso}")


def leer_json(ruta, defecto):
    try:
        return json.loads(ruta.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return defecto


def url(ruta: Path):
    for clave, raiz in RAICES.items():
        try:
            rel = ruta.resolve().relative_to(raiz.resolve())
        except ValueError:
            continue
        return f"/media/{clave}/{quote(rel.as_posix())}?t={int(ruta.stat().st_mtime)}"  # quote: nombres con espacios
    raise ValueError(ruta)


def archivos(carpeta: Path, patron):
    return sorted(carpeta.glob(patron)) if carpeta.exists() else []


def cual(elegida: Path, candidatos):
    """Qué variante es la elegida: la que tiene el mismo tamaño y exactamente el mismo contenido"""
    if not elegida.exists():
        return None
    tam = elegida.stat().st_size
    mismos = [c for c in candidatos if c.stat().st_size == tam]
    if len(mismos) > 1 or (mismos and int(mismos[0].stat().st_mtime) != int(elegida.stat().st_mtime)):
        datos = elegida.read_bytes()
        mismos = [c for c in mismos if c.read_bytes() == datos]
    return mismos[0].name if mismos else None


def hablantes(planos):
    h = []
    for p in planos:
        if p.get("dialogo") and p["dialogo"]["personaje"] not in h:
            h.append(p["dialogo"]["personaje"])
        if p.get("narracion") and "Narrador" not in h:
            h.append("Narrador")
    return h


def datos_proyecto(n):
    carpeta = config.PROYECTOS / n
    base = config.SALIDAS / n
    proyecto = config.cargar_proyecto(n)
    planos = leer_json(carpeta / "planos.json", [])
    montaje = leer_json(carpeta / "montaje.json", {})
    sonido = leer_json(carpeta / "sonido.json", {"musica": [], "efectos": []})
    fichas_voz = {f.stem: leer_json(f, {}) for f in archivos(carpeta / "voces", "*.json")}

    personajes = [{"nombre": c["nombre"],
                   "elegida": url(carpeta / "personajes" / f"{c['nombre']}.png") if (carpeta / "personajes" / f"{c['nombre']}.png").exists() else None,
                   "elegida_archivo": cual(carpeta / "personajes" / f"{c['nombre']}.png", archivos(base / "personajes", f"{c['nombre']}_v*.png")),
                   "variantes": [{"archivo": f.name, "url": url(f)} for f in archivos(base / "personajes", f"{c['nombre']}_v*.png")]}
                  for c in proyecto["personajes"]]
    lista_planos = []
    for p in planos:
        pid = p["id"]
        elegida = base / "elegidas" / f"{pid}.png"
        lista_planos.append({
            **p,
            "imagen_elegida": url(elegida) if elegida.exists() else None,
            "imagen_elegida_archivo": cual(elegida, archivos(base / "imagenes", f"{pid}_v*.png")),
            "imagenes": [{"archivo": f.name, "url": url(f)} for f in archivos(base / "imagenes", f"{pid}_v*.png")],
            "tomas": [{"toma": f.stem[len(pid) + 1:], "url": url(f)} for f in archivos(base / "videos", f"{pid}_*.mp4")],
            "toma_elegida": montaje.get(pid),
            "voz": [url(f) for f in (base / "voces" / "lineas" / f"{pid}.wav", base / "voces" / "lineas" / f"{pid}_narr.wav") if f.exists()],
        })
    voces = [{"nombre": h, "elegida": fichas_voz.get(h, {}).get("muestra"),
              "muestras": [{"muestra": f.stem, "url": url(f)} for f in archivos(base / "voces" / "diseno", f"{h}_*.wav")]}
             for h in hablantes(planos)]
    sonidos = [{"id": e["id"], "grupo": g, "descripcion": e.get("etiquetas") or e.get("prompt"),
                "elegida": e.get("elegida", "v1"),
                "variantes": [{"variante": f.stem.rsplit("_", 1)[1], "url": url(f)} for f in archivos(base / "sonido", f"{e['id']}_v*.flac")]}
               for g in ("musica", "efectos") for e in sonido.get(g, [])]
    finales = [{"nombre": f.name, "url": url(f)} for f in archivos(base / "montaje", "*_con_sonido*.mp4")
               + archivos(base / "montaje", "*_subtitulado*.mp4")]

    n_planos = len(planos)
    estado = {
        "proyecto": bool(proyecto["personajes"]) and "(Qué pasa" not in (carpeta / "guion.md").read_text(encoding="utf-8"),
        "planos": n_planos > 0,
        "personajes": all(c["elegida"] for c in personajes),
        "imagenes": n_planos > 0 and all(p["imagen_elegida"] for p in lista_planos),
        "videos": n_planos > 0 and all(montaje.get(p["id"]) for p in planos),
        "voces": bool(voces) and all(v["elegida"] for v in voces) and all(p["voz"] for p in lista_planos if p.get("dialogo") or p.get("narracion")),
        "labios": all(str(montaje.get(p["id"], "")).startswith("labios") for p in planos if p.get("dialogo")) and n_planos > 0,
        "sonido": bool(sonidos) and all(s["variantes"] for s in sonidos),
        "final": any("720" in f["nombre"] for f in finales),
    }
    matriz, punto_a = datos_matriz(n, lista_planos)
    fuentes_md = (carpeta / "fuentes.md").read_text(encoding="utf-8") if (carpeta / "fuentes.md").exists() else ""
    return {"aprobacion": leer_json(carpeta / "aprobacion.json", None), "fuentes_md": fuentes_md,
            "hechos": leer_json(carpeta / "fuentes.json", []),"nombre": n, "proyecto": proyecto, "guion": (carpeta / "guion.md").read_text(encoding="utf-8"),
            "sonido_json": sonido, "labios_json": leer_json(carpeta / "labios.json", {}), "montaje": montaje,
            "planos": lista_planos, "personajes": personajes, "voces": voces, "sonidos": sonidos,
            "finales": finales, "estado": estado, "matriz": matriz, "punto_a": punto_a}


def num(nombre):
    """'P003_v12.png' / 't12' / 'labios_t12' -> 12 (para ordenar t2 antes que t10)."""
    m = re.search(r"(\d+)(?:\.\w+)?$", nombre)
    return int(m.group(1)) if m else 0


def datos_matriz(n, lista_planos):
    """Una fila por plano con el estado y las versiones de imagen, vídeo, voz y labios (vista de Revisión)."""
    pr = orquestador.Proyecto(n)
    pr.adoptar(guardar=False)  # solo para mostrar: no se escribe nada al mirar
    firmas = pr.firmas or {}
    filas = []
    for p, extra in zip(pr.planos, lista_planos):
        pid = p["id"]
        tomas = sorted(orquestador.tomas(pr.base, pid), key=num)
        normales = [t for t in tomas if not t.startswith("labios")]
        de_labios = [t for t in tomas if t.startswith("labios")]
        mont = pr.montaje.get(pid) or ""
        video = (firmas.get(f"video:{pid}") or {}).get("archivo") or (mont if mont in normales else (normales[-1] if normales else None))
        labios = mont if mont in de_labios else (firmas.get(f"labios:{pid}") or {}).get("archivo")
        vid = lambda t: url(pr.base / "videos" / f"{pid}_{t}.mp4") if t and (pr.base / "videos" / f"{pid}_{t}.mp4").exists() else None
        linea = lambda tipo: url(pr.linea(pid, tipo)) if pr.linea(pid, tipo).exists() else None
        filas.append({
            "id": pid, "escena": p["escena"], "tipo_plano": p["tipo_plano"], "duracion_s": p["duracion_s"],
            "accion": p["accion"], "dialogo": p.get("dialogo"), "narracion": p.get("narracion"),
            "estados": {t: pr.estado(p, t) for t in orquestador.TIPOS},
            "imagen": {"url": extra["imagen_elegida"], "actual": extra["imagen_elegida_archivo"],
                       "versiones": [i["archivo"] for i in sorted(extra["imagenes"], key=lambda i: num(i["archivo"]))]},
            "video": {"actual": video, "url": vid(video), "versiones": normales},
            "labios": {"actual": labios, "url": vid(labios), "versiones": de_labios} if p.get("dialogo") else None,
            "voz": {"url": linea("voz"), "hablante": p["dialogo"]["personaje"], "texto": p["dialogo"]["texto"]} if p.get("dialogo") else None,
            "narr": {"url": linea("narr"), "texto": p["narracion"]} if p.get("narracion") else None,
            "en_montaje": mont,
        })
    return filas, pr.punto_a()


def elegir(n, d):
    carpeta = config.PROYECTOS / n
    base = config.SALIDAS / n
    tipo = d["tipo"]
    if tipo in ("imagen", "toma", "version"):
        pr = orquestador.Proyecto(n)
        pr.adoptar()
        plano = next(p for p in pr.planos if p["id"] == d["plano"])
    if tipo == "version":  # desde la matriz: clase imagen | video | labios
        clase, valor = d["clase"], Path(d["valor"]).name
        if clase == "imagen":
            (base / "elegidas").mkdir(exist_ok=True)
            shutil.copy2(base / "imagenes" / valor, pr.elegida(plano["id"]))
        elif clase in ("video", "labios"):
            if not (base / "videos" / f"{plano['id']}_{valor}.mp4").exists():
                raise ValueError(f"No existe la toma {valor}")
            if clase == "labios" or not plano.get("dialogo") or not str(pr.montaje.get(plano["id"], "")).startswith("labios"):
                pr.montaje[plano["id"]] = valor
                pr.guardar_montaje()
        else:
            raise ValueError(clase)
        pr.registrar(plano, clase, valor)
        pr.guardar_firmas()
        return
    if tipo == "personaje":
        destino = carpeta / "personajes"
        destino.mkdir(exist_ok=True)
        shutil.copy2(base / "personajes" / Path(d["archivo"]).name, destino / f"{d['nombre']}.png")
    elif tipo == "imagen":
        (base / "elegidas").mkdir(exist_ok=True)
        shutil.copy2(base / "imagenes" / Path(d["archivo"]).name, base / "elegidas" / f"{d['plano']}.png")
        pr.registrar(plano, "imagen", Path(d["archivo"]).name)  # su vídeo y labios pasarán a desactualizados
        pr.guardar_firmas()
    elif tipo == "toma":
        pr.montaje[d["plano"]] = d["toma"]
        pr.guardar_montaje()
        pr.registrar(plano, "labios" if d["toma"].startswith("labios") else "video", d["toma"])
        pr.guardar_firmas()
    elif tipo == "voz":  # igual que "voces.py elegir"
        muestra = Path(d["muestra"]).name
        nombre, motor, _ = muestra.rsplit("_", 2)
        (carpeta / "voces").mkdir(exist_ok=True)
        wav = carpeta / "voces" / f"{nombre}.wav"
        shutil.copy2(base / "voces" / "diseno" / f"{muestra}.wav", wav)
        texto = (base / "voces" / "diseno" / f"{muestra}.txt").read_text(encoding="utf-8")
        (carpeta / "voces" / f"{nombre}.json").write_text(json.dumps(
            {"motor": motor, "wav": str(wav), "texto": texto, "muestra": muestra}, ensure_ascii=False, indent=2), encoding="utf-8")
    elif tipo == "sonido":
        ruta = carpeta / "sonido.json"
        plan = leer_json(ruta, {})
        for e in plan.get("musica", []) + plan.get("efectos", []):
            if e["id"] == d["id"]:
                e["elegida"] = d["variante"]
        ruta.write_text(json.dumps(plan, ensure_ascii=False, indent=2), encoding="utf-8")
    else:
        raise ValueError(f"tipo desconocido: {tipo}")


def guardar(n, nombre, contenido):
    if nombre not in EDITABLES:
        raise ValueError(f"No se puede editar {nombre}")
    if nombre.endswith(".json"):
        contenido = json.dumps(json.loads(contenido) if isinstance(contenido, str) else contenido, ensure_ascii=False, indent=2)
    ruta = config.PROYECTOS / n / nombre
    if ruta.exists():  # copia de seguridad de la versión anterior, por si acaso
        (ruta.parent / ".copias").mkdir(exist_ok=True)
        shutil.copy2(ruta, ruta.parent / ".copias" / f"{nombre}.{time.strftime('%Y%m%d-%H%M%S')}")
    ruta.write_text(contenido, encoding="utf-8")


PAPELERA_P = RAIZ / "papelera"             # papelera\<nombre>__<fecha>\
PAPELERA_S = config.SALIDAS / "_papelera"  # salidas\_papelera\<nombre>__<fecha>\
NOMBRE_OK = re.compile(r"[A-Za-z0-9_\-]+")
ID_OK = re.compile(r"[A-Za-z0-9_\-]+__\d{8}-\d{6}")


def tamano_carpeta(carpeta: Path):
    total = n = 0
    if carpeta.exists():
        for raiz, _, nombres in os.walk(carpeta):
            for f in nombres:
                try:
                    total += os.path.getsize(os.path.join(raiz, f))
                    n += 1
                except OSError:
                    pass
    return total, n


def proyecto_existente(n):
    if not NOMBRE_OK.fullmatch(n or "") or not (config.PROYECTOS / n / "proyecto.json").exists():
        raise ValueError(f"No existe el proyecto {n}")
    return config.PROYECTOS / n, config.SALIDAS / n


def info_eliminar(n):
    carpeta, salidas = proyecto_existente(n)
    (bp, fp), (bs, fs) = tamano_carpeta(carpeta), tamano_carpeta(salidas)
    return {"nombre": n, "proyecto_bytes": bp, "proyecto_archivos": fp, "salidas_bytes": bs, "salidas_archivos": fs,
            "ruta_proyecto": str(carpeta), "ruta_salidas": str(salidas)}


def eliminar_proyecto(n, confirmacion):
    carpeta, salidas = proyecto_existente(n)
    if confirmacion != n:
        raise ValueError("Para eliminar hay que escribir exactamente el nombre del proyecto")
    est = trabajo.estado()
    if est["activo"] and est["proyecto"] == n:
        raise ValueError("Este proyecto tiene un trabajo en marcha: cancélalo o espera a que termine")
    ident = f"{n}__{time.strftime('%Y%m%d-%H%M%S')}"
    while (PAPELERA_P / ident).exists() or (PAPELERA_S / ident).exists():  # dos eliminaciones en el mismo segundo
        time.sleep(1)
        ident = f"{n}__{time.strftime('%Y%m%d-%H%M%S')}"
    PAPELERA_P.mkdir(exist_ok=True)
    movida = None
    if salidas.exists():  # primero lo generado (lo más probable que falle por un archivo abierto)
        PAPELERA_S.mkdir(parents=True, exist_ok=True)
        movida = PAPELERA_S / ident
        try:
            os.rename(salidas, movida)
        except OSError as e:
            raise ValueError(f"No se pudo mover {salidas} (¿algún vídeo o imagen abierto en otro programa?): {e}")
    try:
        os.rename(carpeta, PAPELERA_P / ident)
    except OSError as e:
        if movida:  # deshacer: que nada quede a medias
            os.rename(movida, salidas)
        raise ValueError(f"No se pudo mover {carpeta} (¿algún archivo abierto?): {e}")
    (PAPELERA_P / ident / ".eliminado.json").write_text(json.dumps(
        {"nombre": n, "fecha": time.strftime("%Y-%m-%d %H:%M"), "con_salidas": bool(movida)}, ensure_ascii=False),
        encoding="utf-8")
    return ident


def lista_papelera():
    res = []
    if PAPELERA_P.exists():
        for c in sorted(PAPELERA_P.iterdir(), reverse=True):
            if c.is_dir() and ID_OK.fullmatch(c.name):
                meta = leer_json(c / ".eliminado.json", {})
                bp, _ = tamano_carpeta(c)
                bs, _ = tamano_carpeta(PAPELERA_S / c.name)
                res.append({"id": c.name, "nombre": meta.get("nombre", c.name.split("__")[0]), "fecha": meta.get("fecha", ""),
                            "bytes": bp + bs, "existe_ya": (config.PROYECTOS / c.name.split("__")[0]).exists()})
    return res


def restaurar_proyecto(ident):
    if not ID_OK.fullmatch(ident or "") or not (PAPELERA_P / ident).exists():
        raise ValueError("No está en la papelera")
    n = ident.split("__")[0]
    if (config.PROYECTOS / n).exists() or (config.SALIDAS / n).exists():
        raise ValueError(f"Ya existe un proyecto llamado {n}: renómbralo o elimínalo antes de restaurar este")
    os.rename(PAPELERA_P / ident, config.PROYECTOS / n)
    (config.PROYECTOS / n / ".eliminado.json").unlink(missing_ok=True)
    if (PAPELERA_S / ident).exists():
        os.rename(PAPELERA_S / ident, config.SALIDAS / n)
    return n


def vaciar_papelera(ident, confirmacion):
    """Borrado DEFINITIVO (no se puede deshacer). `ident` None = toda la papelera."""
    if confirmacion != "BORRAR":
        raise ValueError('Para borrar definitivamente hay que escribir BORRAR')
    ids = [ident] if ident else [x["id"] for x in lista_papelera()]
    for i in ids:
        if not ID_OK.fullmatch(i or ""):
            raise ValueError(f"Identificador no válido: {i}")
        for c in (PAPELERA_P / i, PAPELERA_S / i):
            if c.exists():
                shutil.rmtree(c)
    return len(ids)


def lista_proyectos():
    res = []
    for c in sorted(config.PROYECTOS.iterdir()):
        if (c / "proyecto.json").exists():
            p = config.cargar_proyecto(c.name)
            res.append({"nombre": c.name, "titulo": p.get("titulo", c.name), "genero": p["genero"],
                        "idioma": p["idioma"], "formato": p["formato"]})
    return res


class Manejador(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, *a):
        pass

    def _json(self, datos, codigo=200):
        cuerpo = json.dumps(datos, ensure_ascii=False).encode("utf-8")
        self.send_response(codigo)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(cuerpo)))
        self.end_headers()
        self.wfile.write(cuerpo)

    def _cuerpo(self):
        n = int(self.headers.get("Content-Length") or 0)
        return json.loads(self.rfile.read(n) or b"{}")

    def do_GET(self):
        u = urlparse(self.path)
        partes = [unquote(x) for x in u.path.strip("/").split("/")]
        try:
            if u.path == "/":
                return self._archivo(RAIZ / "interfaz.html")
            if partes[0] == "media":
                return self._media(partes[1], "/".join(partes[2:]))
            if u.path == "/api/proyectos":
                return self._json({"proyectos": lista_proyectos(), "generos_ejemplo": [
                    "dramas históricos chinos (wuxia/xianxia)", "thriller policiaco moderno", "documental de historia",
                    "cuento infantil animado estilo Pixar", "terror rural", "ciencia ficción", "romance de época",
                    "vídeo motivacional", "leyendas y mitos mexicanos"],
                    "idiomas": config.IDIOMAS, "formatos": list(config.FORMATOS)})
            if partes[:2] == ["api", "proyecto"] and len(partes) == 3:
                return self._json(datos_proyecto(partes[2]))
            if partes[:2] == ["api", "proyecto"] and len(partes) == 4 and partes[3] == "tamano":
                return self._json(info_eliminar(partes[2]))
            if u.path == "/api/papelera":
                return self._json({"papelera": lista_papelera()})
            if u.path == "/api/trabajo":
                return self._json(trabajo.estado())
            if u.path == "/api/servicios":
                return self._json({k: responde(v["url"]) for k, v in SERVICIOS.items()})
            self._json({"error": "no existe"}, 404)
        except Exception as e:
            self._json({"error": str(e)}, 500)

    def do_POST(self):
        partes = [unquote(x) for x in urlparse(self.path).path.strip("/").split("/")]
        try:
            d = self._cuerpo()
            if partes == ["api", "proyectos"]:
                nuevo_proyecto.crear(d["nombre"], d["genero"], d.get("idioma", "es"), d.get("formato", "16:9"), d.get("titulo"),
                                     bool(d.get("realismo")))
                return self._json({"ok": True})
            if partes == ["api", "papelera", "restaurar"]:
                return self._json({"ok": True, "nombre": restaurar_proyecto(d.get("id"))})
            if partes == ["api", "papelera", "vaciar"]:
                return self._json({"ok": True, "borrados": vaciar_papelera(d.get("id"), d.get("confirmacion"))})
            if partes[:2] == ["api", "proyecto"] and len(partes) == 4 and partes[3] == "eliminar":
                return self._json({"ok": True, "id": eliminar_proyecto(partes[2], d.get("confirmacion"))})
            if partes[:2] == ["api", "proyecto"] and len(partes) == 4:
                n, accion = partes[2], partes[3]
                if accion == "guardar":
                    guardar(n, d["archivo"], d["contenido"])
                elif accion == "aprobar":  # guion hecho por la IA: revisado por una persona
                    import guionista
                    guionista.aprobar(n, d.get("por", "web"))
                elif accion == "elegir":
                    elegir(n, d)
                elif accion == "ejecutar":
                    if d["paso"] == "planos" and (config.PROYECTOS / n / "planos.json").exists():
                        guardar(n, "planos.json", (config.PROYECTOS / n / "planos.json").read_text(encoding="utf-8"))
                    trabajo.lanzar(n, d["paso"], comandos_del_paso(n, d["paso"], d.get("opciones", {})))
                else:
                    raise ValueError(accion)
                return self._json({"ok": True})
            if partes == ["api", "trabajo", "cancelar"]:
                trabajo.cancelar()
                return self._json({"ok": True})
            if partes[:2] == ["api", "servicios"]:
                if d["accion"] == "arrancar":
                    arrancar_servicio(d["servicio"])
                    return self._json({"ok": True})
                return self._json({"ok": True, "mensaje": detener_servicio(d["servicio"])})
            if partes == ["api", "apagar"]:
                for s in SERVICIOS:
                    detener_servicio(s)
                self._json({"ok": True})
                threading.Thread(target=lambda: (time.sleep(0.5), os._exit(0)), daemon=True).start()
                return
            self._json({"error": "no existe"}, 404)
        except SystemExit as e:  # nuevo_proyecto usa SystemExit para sus mensajes
            self._json({"error": str(e)}, 400)
        except Exception as e:
            self._json({"error": f"{type(e).__name__}: {e}"}, 400)

    def _archivo(self, ruta: Path):
        datos = ruta.read_bytes()
        self.send_response(200)
        self.send_header("Content-Type", mimetypes.guess_type(ruta.name)[0] or "application/octet-stream")
        self.send_header("Content-Length", str(len(datos)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(datos)

    def _media(self, clave, rel):
        raiz = RAICES[clave].resolve()
        ruta = (raiz / rel).resolve()
        if not ruta.is_relative_to(raiz) or not ruta.is_file():  # nada fuera de salidas y proyectos
            return self._json({"error": "no permitido"}, 404)
        tam = ruta.stat().st_size
        tipo = {".flac": "audio/flac", ".wav": "audio/wav", ".mp4": "video/mp4"}.get(ruta.suffix.lower()) \
            or mimetypes.guess_type(ruta.name)[0] or "application/octet-stream"
        ini, fin = 0, tam - 1
        rango = self.headers.get("Range")
        if rango and rango.startswith("bytes="):  # los navegadores lo piden para poder avanzar en vídeo/audio
            a, _, b = rango[6:].partition("-")
            ini = int(a) if a else max(0, tam - int(b))
            fin = int(b) if a and b else tam - 1
            self.send_response(206)
            self.send_header("Content-Range", f"bytes {ini}-{fin}/{tam}")
        else:
            self.send_response(200)
        self.send_header("Content-Type", tipo)
        self.send_header("Accept-Ranges", "bytes")
        self.send_header("Content-Length", str(fin - ini + 1))
        self.end_headers()
        with open(ruta, "rb") as f:
            f.seek(ini)
            restante = fin - ini + 1
            try:
                while restante > 0:
                    trozo = f.read(min(1 << 20, restante))
                    if not trozo:
                        break
                    self.wfile.write(trozo)
                    restante -= len(trozo)
            except (ConnectionResetError, ConnectionAbortedError, BrokenPipeError):
                pass  # el navegador cortó la descarga (normal al saltar en un vídeo)


class Servidor(ThreadingHTTPServer):
    request_queue_size = 128
    daemon_threads = True


def main():
    servidor = Servidor(("127.0.0.1", PUERTO), Manejador)
    print(f"drama-ia: http://127.0.0.1:{PUERTO}  (cierra esta ventana o usa 'Apagar todo' para salir)")
    if "--sin-navegador" not in sys.argv:
        webbrowser.open(f"http://127.0.0.1:{PUERTO}")
    servidor.serve_forever()


if __name__ == "__main__":
    main()
