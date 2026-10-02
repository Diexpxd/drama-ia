"""Orquestador: produce la primera versión de TODO sin esperar clics y regenera por plano respetando dependencias.

Punto de control A (lo haces tú antes): guion, planos, retratos de personajes y voces elegidas.
Después, un solo comando hace imágenes -> vídeos -> voces -> labios -> música/efectos -> vista rápida, eligiendo
automáticamente la versión generada. Tú revisas el resultado (Punto de control B) y regeneras solo lo que falle.

  python orquestador.py piloto                   # todo lo que falte o esté desactualizado
  python orquestador.py piloto --simular         # solo dice qué haría
  python orquestador.py piloto --planos P003 --rehacer video

Dependencias (se detectan solas con "firmas", huellas de lo que se usó para generar cada cosa):
  imagen  <- prompt, ambientación, estilo, retratos de referencia
  vídeo   <- imagen elegida, movimiento, duración          (rehacer la imagen deja el vídeo desactualizado)
  voz     <- texto de la frase, voz elegida del personaje
  labios  <- imagen elegida, voz de la frase               (rehacer imagen o voz deja los labios desactualizados)
Las firmas se guardan en salidas\\<proyecto>\\firmas.json. Nunca se borra nada: las versiones
anteriores siguen en sus carpetas y se pueden volver a elegir.
"""
import argparse
import hashlib
import json
import re
import shutil
import subprocess
import sys
import time
from pathlib import Path

import config

PY_VOZ = config.PY_VOZ
RAIZ = Path(__file__).resolve().parent
TIPOS = ("imagen", "video", "voz", "narr", "labios")


_cache_hash = {}


def hash_archivo(ruta: Path):
    if not ruta.exists():
        return None
    st = ruta.stat()
    clave = (str(ruta), st.st_size, st.st_mtime_ns)
    if clave not in _cache_hash:
        _cache_hash[clave] = hashlib.sha1(ruta.read_bytes()).hexdigest()
    return _cache_hash[clave]


def huella(*partes):
    return hashlib.sha1(json.dumps(partes, ensure_ascii=False, sort_keys=True).encode("utf-8")).hexdigest()[:16]


class Proyecto:
    """Todo lo que el orquestador y la interfaz necesitan saber de un proyecto, leído del disco."""

    def __init__(self, nombre):
        from generar import refs_del_proyecto
        self.nombre = nombre
        self.carpeta = config.PROYECTOS / nombre
        self.base = config.SALIDAS / nombre
        self.p = config.cargar_proyecto(nombre)
        self.planos = self._json(self.carpeta / "planos.json", [])
        self.montaje = self._json(self.carpeta / "montaje.json", {})
        self.cajas = self._json(self.carpeta / "labios.json", {})
        self.refs = refs_del_proyecto(self.carpeta, self.p)
        self.ruta_firmas = self.base / "firmas.json"
        self.firmas = self._json(self.ruta_firmas, None)

    @staticmethod
    def _json(ruta, defecto):
        try:
            return json.loads(ruta.read_text(encoding="utf-8"))
        except FileNotFoundError:
            return defecto

    def guardar_firmas(self):
        self.base.mkdir(parents=True, exist_ok=True)
        self.ruta_firmas.write_text(json.dumps(self.firmas, ensure_ascii=False, indent=2), encoding="utf-8")

    def guardar_montaje(self):
        orden = {p["id"]: i for i, p in enumerate(self.planos)}
        m = dict(sorted(self.montaje.items(), key=lambda kv: orden.get(kv[0], 999)))
        (self.carpeta / "montaje.json").write_text(json.dumps(m, ensure_ascii=False, indent=2), encoding="utf-8")

    def elegida(self, pid):
        return self.base / "elegidas" / f"{pid}.png"

    def linea(self, pid, tipo):
        return self.base / "voces" / "lineas" / (f"{pid}.wav" if tipo == "voz" else f"{pid}_narr.wav")

    def ficha_voz(self, hablante):
        return self._json(self.carpeta / "voces" / f"{hablante}.json", None)

    def firma_actual(self, plano, tipo):
        from generar import prompt_de_imagen
        pid = plano["id"]
        if tipo == "imagen":
            prompt, con_ref = prompt_de_imagen(self.p, plano, self.refs)
            return huella(prompt, [hash_archivo(self.refs[n]) for n in con_ref], config.dims(self.p, "img"))
        if tipo == "video" and plano.get("tipo_visual") == "grafico":
            return huella(json.dumps(plano.get("grafico"), sort_keys=True), plano["duracion_s"], "grafico-v2")
        if tipo == "video" and plano.get("tipo_visual") == "imagen":
            return huella(hash_archivo(self.elegida(pid)), plano.get("prompt_video"), plano["duracion_s"], "foto-v2")
        if tipo == "video":
            extra = ["realismo"] if self.p.get("realismo") else []  # condicional: no cambia firmas antiguas
            if config.VID_MODO != "rapido":  # un clip del modo rápido queda desactualizado al cambiar de modo
                extra.append(f"modo:{config.VID_MODO}")
            return huella(hash_archivo(self.elegida(pid)), plano["prompt_video"], plano["duracion_s"],
                          config.dims(self.p, "vid"), *extra)
        if tipo in ("voz", "narr"):
            hablante, texto = ((plano["dialogo"]["personaje"], plano["dialogo"]["texto"]) if tipo == "voz"
                               else ("Narrador", plano["narracion"]))
            ficha = self.ficha_voz(hablante) or {}
            extra = []
            if ficha.get("motor") == "voxcpm" and (config.VOZ_CFG, config.VOZ_PASOS) != (2.0, 10):
                extra.append(f"voxcpm:{config.VOZ_CFG}/{config.VOZ_PASOS}")
            return huella(texto, ficha.get("motor"), ficha.get("muestra"), self.p["idioma"], *extra)
        if tipo == "labios":
            return huella(hash_archivo(self.elegida(pid)), hash_archivo(self.linea(pid, "voz")),
                          self.cajas.get(pid), plano["duracion_s"])
        raise ValueError(tipo)

    def aplica(self, plano, tipo):
        if tipo == "imagen" and plano.get("tipo_visual") == "grafico":
            return False
        return {"imagen": True, "video": True, "voz": bool(plano.get("dialogo")),
                "narr": bool(plano.get("narracion")), "labios": bool(plano.get("dialogo"))}[tipo]

    def existe(self, plano, tipo):
        pid = plano["id"]
        if tipo == "imagen":
            return self.elegida(pid).exists()
        if tipo in ("voz", "narr"):
            return self.linea(pid, tipo).exists()
        reg = (self.firmas or {}).get(f"{tipo}:{pid}")
        if reg and (self.base / "videos" / f"{pid}_{reg['archivo']}.mp4").exists():
            return True
        toma = self.montaje.get(pid) or ""
        es_labios = toma.startswith("labios")
        return bool(toma) and (es_labios == (tipo == "labios")) and (self.base / "videos" / f"{pid}_{toma}.mp4").exists()

    def estado(self, plano, tipo):
        if not self.aplica(plano, tipo):
            return "no_aplica"
        if not self.existe(plano, tipo):
            return "falta"
        reg = (self.firmas or {}).get(f"{tipo}:{plano['id']}")
        if reg is None:
            return "ok"  # hecho antes de que existieran las firmas: se da por bueno
        return "ok" if reg["firma"] == self.firma_actual(plano, tipo) else "desactualizado"

    def registrar(self, plano, tipo, archivo):
        if self.firmas is None:
            self.firmas = {}
        self.firmas[f"{tipo}:{plano['id']}"] = {"archivo": archivo, "firma": self.firma_actual(plano, tipo),
                                                "fecha": time.strftime("%Y-%m-%d %H:%M:%S")}

    def adoptar(self, guardar=True):
        if self.firmas is not None:
            return False
        self.firmas = {}
        for plano in self.planos:
            pid = plano["id"]
            toma = self.montaje.get(pid) or ""
            for tipo in TIPOS:
                if not self.aplica(plano, tipo):
                    continue
                if tipo == "imagen":
                    archivo = "elegidas" if self.elegida(pid).exists() else None
                elif tipo in ("voz", "narr"):
                    archivo = self.linea(pid, tipo).name if self.linea(pid, tipo).exists() else None
                elif tipo == "labios":
                    archivo = toma if toma.startswith("labios") else None
                else:
                    normales = sorted((t for t in tomas(self.base, pid) if not t.startswith("labios")),
                                      key=lambda t: int(t[1:]) if t[1:].isdigit() else 0)  # t10 después de t2
                    archivo = toma if toma and not toma.startswith("labios") else (normales[-1] if normales else None)
                if tipo in ("video", "labios") and archivo and not (self.base / "videos" / f"{pid}_{archivo}.mp4").exists():
                    archivo = None
                if archivo:
                    self.registrar(plano, tipo, archivo)
        if guardar:
            self.guardar_firmas()
        return bool(self.firmas)  # True solo si había algo que adoptar

    def punto_a(self):
        faltas = []
        aprobacion = self._json(self.carpeta / "aprobacion.json", None)  # guiones hechos por la IA (guionista.py)
        if aprobacion and aprobacion.get("estado") != "aprobado":
            faltas.append("Guion generado por IA pendiente de aprobación: revisa guion.md y fuentes.md y apruébalo")
        if not self.planos:
            faltas.append("No hay planos: genera los planos a partir del guion")
        en_cuadro = {n for p in self.planos for n in p["personajes"]}
        sin_ref = [c["nombre"] for c in self.p["personajes"] if c["nombre"] in en_cuadro and not self.refs[c["nombre"]].exists()]
        if sin_ref:
            faltas.append(f"Falta elegir el retrato de: {', '.join(sin_ref)}")
        hablantes = {p["dialogo"]["personaje"] for p in self.planos if p.get("dialogo")}
        hablantes |= {"Narrador" for p in self.planos if p.get("narracion")}
        sin_voz = sorted(h for h in hablantes if not self.ficha_voz(h))
        if sin_voz:
            faltas.append(f"Falta elegir la voz de: {', '.join(sin_voz)}")
        return faltas


def tomas(base, pid):
    """Nombres de toma existentes de un plano: ['t1', 't2', 'labios_t1', ...]"""
    return [f.stem[len(pid) + 1:] for f in (base / "videos").glob(f"{pid}_*.mp4")]


def siguiente(existentes, prefijo):
    """Primer número libre: siguiente(['t1','t2'], 't') -> 't3' (nunca pisa una versión anterior)."""
    nums = [int(m.group(1)) for e in existentes if (m := re.fullmatch(re.escape(prefijo) + r"(\d+)", e))]
    return f"{prefijo}{max(nums, default=0) + 1}"


def plan(pr: Proyecto, planos, rehacer):
    """Qué hay que generar en cada paso, con la cascada de dependencias ya aplicada."""
    cascada = {"imagen": {"imagen", "video", "labios"}, "video": {"video"}, "voz": {"voz", "narr", "labios"},
               "labios": {"labios"}}
    forzados = cascada.get(rehacer, set())
    res = {t: [] for t in TIPOS}
    for plano in planos:
        cambia = set()  # lo que se va a regenerar en este plano: arrastra a sus dependientes
        for tipo in TIPOS:
            if not pr.aplica(plano, tipo):
                continue
            estado = pr.estado(plano, tipo)
            depende = {"video": {"imagen"}, "labios": {"imagen", "voz"}}.get(tipo, set())
            if tipo in forzados:
                motivo = "pedido" if tipo == rehacer or (rehacer == "voz" and tipo == "narr") else f"depende de {rehacer}"
            elif estado in ("falta", "desactualizado"):
                motivo = estado
            elif depende & cambia:
                motivo = "cambia " + "/".join(sorted(depende & cambia))
            else:
                continue
            res[tipo].append((plano, motivo))
            cambia.add(tipo)
    return res


def _movimiento_de(plano):
    t = (plano.get("prompt_video") or "").lower()
    if "pull-out" in t or "pull out" in t or "pull back" in t or "zoom out" in t:
        return "alejar"
    if "left" in t:
        return "izq"
    if "right" in t:
        return "der"
    if "push" in t or "zoom in" in t or "dolly in" in t:
        return "empuje"
    return None


def render_programa(pr, lista, simular):
    """Planos hechos por programa (sin GPU): 'imagen' = foto con movimiento de cámara, 'grafico' = gráfico de datos."""
    import visuales
    for plano, _ in lista:
        pid = plano["id"]
        toma = siguiente(tomas(pr.base, pid), "t")
        print(f"  {pid} -> {pid}_{toma}.mp4 ({plano['tipo_visual']}, por programa)", flush=True)
        if simular:
            continue
        destino = pr.base / "videos" / f"{pid}_{toma}.mp4"
        seg = min(14.0, max(6.0, 1.6 * float(plano["duracion_s"])))
        if plano["tipo_visual"] == "grafico":
            visuales.grafico(plano["grafico"], destino, seg=seg)
        else:
            if not pr.elegida(pid).exists():
                print(f"  {pid}: sin imagen elegida, se salta")
                continue
            visuales.foto(pr.elegida(pid), destino, seg=seg, mov=_movimiento_de(plano),
                          indice=int(re.sub(r"[^0-9]", "", pid) or 0))
        for carpeta in ("videos_720", "videos_720_x2"):
            (pr.base / carpeta).mkdir(exist_ok=True)
            shutil.copy2(destino, pr.base / carpeta / destino.name)
        pr.registrar(plano, "video", toma)
        pr.montaje[pid] = toma
    if not simular:
        pr.guardar_firmas()
        pr.guardar_montaje()


def ejecutar(cmd, simular):
    print("$ " + " ".join(Path(c).name if i < 2 else c for i, c in enumerate(cmd)), flush=True)
    if simular:
        return 0
    return subprocess.run(cmd, cwd=RAIZ).returncode


def orquestar(nombre, planos_ids=None, rehacer=None, simular=False, hasta="final", forzar=False, sin_sonido=False):
    pr = Proyecto(nombre)
    faltas = pr.punto_a()
    if faltas and not forzar:
        print("Antes de producir falta completar el Punto de control A:")
        for f in faltas:
            print("  - " + f)
        return 2
    if pr.adoptar(guardar=not simular):  # en simulación, solo en memoria: no se escribe nada en disco
        print("Primera vez con el orquestador: se toman como buenas las elecciones que ya había.")
    planos = [p for p in pr.planos if not planos_ids or p["id"] in planos_ids]
    if rehacer and not planos_ids:
        print("--rehacer necesita --planos (regenerar es quirúrgico, plano a plano)")
        return 2
    trabajo = plan(pr, planos, rehacer)

    print(f"Proyecto {nombre}: {len(planos)} planos revisados")
    for tipo in TIPOS:
        if trabajo[tipo]:
            print(f"  {tipo:7s}: " + ", ".join(f"{p['id']} ({m})" for p, m in trabajo[tipo]))
    if not any(trabajo.values()):
        print("  Todo está al día: no hay nada que generar.")
    orden = ["imagen", "video", "voz", "labios", "sonido", "final"]
    pasos = orden[: orden.index(hasta) + 1]
    import comfy
    if not simular and any(trabajo[t] for t in ("imagen", "video", "labios")) and not comfy.disponible():
        print("ComfyUI no responde: enciéndelo (arrancar_comfyui.ps1 o el botón de la interfaz).")
        return 3

    registro = pr.base / "registro.jsonl"
    tmp = f"drama-ia/{nombre}/tmp"

    # 1) IMÁGENES: una versión nueva por plano; se elige sola
    if "imagen" in pasos and trabajo["imagen"]:
        from generar import lote, prompt_de_imagen
        print("\n== Imágenes ==", flush=True)
        trabajos, subidas = [], {}
        for plano, _ in trabajo["imagen"]:
            prompt, con_ref = prompt_de_imagen(pr.p, plano, pr.refs)
            existentes = [f.stem[len(plano["id"]) + 1:] for f in (pr.base / "imagenes").glob(f"{plano['id']}_v*.png")]
            destino = pr.base / "imagenes" / f"{plano['id']}_{siguiente(existentes, 'v')}.png"
            print(f"  {plano['id']} -> {destino.name}")
            if simular:
                continue
            refs = []
            for n in con_ref:
                subidas.setdefault(n, comfy.subir_imagen(pr.refs[n]))
                refs.append(subidas[n])
            s = comfy.semilla()
            trabajos.append((comfy.flujo_imagen(prompt, tmp, refs, *config.dims(pr.p, "img"), seed=s), destino,
                             {"tipo": "imagen", "plano": plano["id"], "prompt": prompt, "refs": con_ref, "seed": s,
                              "orquestador": True}))
        for destino, estado in ([] if simular else lote(trabajos, registro)):
            if estado == "ok":
                plano = next(p for p in pr.planos if destino.name.startswith(p["id"] + "_"))
                (pr.base / "elegidas").mkdir(exist_ok=True)
                shutil.copy2(destino, pr.elegida(plano["id"]))
                pr.registrar(plano, "imagen", destino.name)
        if not simular:
            pr.guardar_firmas()

    # 2) VÍDEOS: una toma nueva por plano con la imagen elegida
    if "video" in pasos and trabajo["video"]:
        from generar import lote, negativo_video
        print("\n== Vídeos ==", flush=True)
        trabajos = []
        render_programa(pr, [(p, m) for p, m in trabajo["video"] if p.get("tipo_visual") in ("imagen", "grafico")], simular)
        for plano, _ in [(p, m) for p, m in trabajo["video"] if p.get("tipo_visual") not in ("imagen", "grafico")]:
            pid = plano["id"]
            toma = siguiente(tomas(pr.base, pid), "t")
            print(f"  {pid} -> {pid}_{toma}.mp4")
            if simular:
                continue
            if not pr.elegida(pid).exists():
                print(f"  {pid}: sin imagen elegida, se salta")
                continue
            s = comfy.semilla()
            trabajos.append((comfy.flujo_video(comfy.subir_imagen(pr.elegida(pid)), plano["prompt_video"], tmp,
                                               plano["duracion_s"], *config.dims(pr.p, "vid"), s, negativo_video(pr.p)),
                             pr.base / "videos" / f"{pid}_{toma}.mp4",
                             {"tipo": "video", "plano": pid, "prompt": plano["prompt_video"], "seed": s, "orquestador": True}))
        for destino, estado in ([] if simular else lote(trabajos, registro)):
            if estado == "ok":
                pid, toma = destino.stem.split("_", 1)
                plano = next(p for p in pr.planos if p["id"] == pid)
                pr.registrar(plano, "video", toma)
                if not plano.get("dialogo") or not str(pr.montaje.get(pid, "")).startswith("labios"):
                    pr.montaje[pid] = toma  # los planos con diálogo pasarán a su toma de labios en el paso 4
        if not simular:
            pr.guardar_firmas()
            pr.guardar_montaje()

    if "voz" in pasos and (trabajo["voz"] or trabajo["narr"]):
        print("\n== Voces ==", flush=True)
        for tipo in ("voz", "narr"):
            for plano, _ in trabajo[tipo]:
                ruta = pr.linea(plano["id"], tipo)
                if ruta.exists():  # se aparta (no se borra) para que voces.py la vuelva a locutar
                    print(f"  se aparta la versión anterior de {ruta.name}")
                    if not simular:
                        aparte = ruta.parent / "anteriores"
                        aparte.mkdir(exist_ok=True)
                        ruta.replace(aparte / f"{ruta.stem}_{time.strftime('%Y%m%d-%H%M%S')}{ruta.suffix}")
        if ejecutar([PY_VOZ, "voces.py", "locutar", nombre], simular) == 0 and not simular:
            for plano, _ in trabajo["voz"] + trabajo["narr"]:
                for tipo in ("voz", "narr"):
                    if pr.aplica(plano, tipo) and pr.linea(plano["id"], tipo).exists():
                        pr.registrar(plano, tipo, pr.linea(plano["id"], tipo).name)
            pr.guardar_firmas()

    if "labios" in pasos and trabajo["labios"]:
        from generar import lote
        from labios import trabajos_labios
        print("\n== Labios ==", flush=True)
        trabajos = []
        for plano, _ in trabajo["labios"]:
            pid = plano["id"]
            toma = siguiente(tomas(pr.base, pid), "labios_t")
            print(f"  {pid} -> {pid}_{toma}.mp4")
            if simular:
                continue
            nuevos = trabajos_labios(nombre, pr.p, plano, pr.cajas, [pr.base / "videos" / f"{pid}_{toma}.mp4"])
            if nuevos is None:
                print(f"  {pid}: falta la voz o la imagen elegida, se salta")
                continue
            trabajos += nuevos
        for destino, estado in ([] if simular else lote(trabajos, registro)):
            if estado == "ok":
                pid, toma = destino.stem.split("_", 1)
                plano = next(p for p in pr.planos if p["id"] == pid)
                pr.registrar(plano, "labios", toma)
                pr.montaje[pid] = toma
        if not simular:
            pr.guardar_firmas()
            pr.guardar_montaje()

    pendientes = []
    if "sonido" in pasos and not sin_sonido:
        from sonido_auto import desactualizado, es_plantilla
        plan_sonido = Proyecto._json(pr.carpeta / "sonido.json", {})
        viejo = desactualizado(plan_sonido, pr.planos)  # planos cambiados y plan automático sin tocar: se rehace
        if es_plantilla(plan_sonido) or viejo:  # nunca pisa uno editado a mano
            print("\n== Plan de sonido automático (LLM) ==" + (" (los planos cambiaron)" if viejo else ""), flush=True)
            if ejecutar([sys.executable, "sonido_auto.py", nombre] + (["--reemplazar"] if viejo else []), simular) != 0:
                print("  No se pudo proponer (¿Ollama apagado?). El vídeo saldrá sin música ni efectos hasta que lo hagas.")
            plan_sonido = Proyecto._json(pr.carpeta / "sonido.json", {})
        pendientes = [e["id"] for e in plan_sonido.get("musica", []) + plan_sonido.get("efectos", [])
                      if not (pr.base / "sonido" / f"{e['id']}_{e.get('elegida', 'v1')}.flac").exists()
                      and "(EN INGLÉS)" not in (e.get("etiquetas") or e.get("prompt") or "")]  # plantilla sin rellenar
        pendientes = list(dict.fromkeys(pendientes))  # un mismo sonido puede usarse varias veces (transiciones)
        if pendientes:
            print("\n== Música y efectos ==", flush=True)
            ejecutar([sys.executable, "audio.py", "generar", nombre, "--variantes", "1", "--solo", ",".join(pendientes)],
                     simular)

    vista = pr.base / "montaje" / f"{nombre}_con_sonido.mp4"
    entradas = [pr.carpeta / f for f in ("planos.json", "sonido.json", "proyecto.json", "montaje.json")]
    vista_vieja = vista.exists() and any(e.exists() and e.stat().st_mtime > vista.stat().st_mtime for e in entradas)
    cambio = any(trabajo.values()) or bool(pendientes) or vista_vieja
    if "final" in pasos and (cambio or not vista.exists()):
        print("\n== Vista rápida ==", flush=True)
        if ejecutar([sys.executable, "montar.py", nombre], simular) == 0:
            ejecutar([sys.executable, "mezclar.py", nombre], simular)
    print("\nOrquestador terminado." + (" (simulación: no se ha generado ni cambiado nada)" if simular else ""))
    return 0


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("proyecto")
    ap.add_argument("--planos", help="solo estos planos, p. ej. P003,P007")
    ap.add_argument("--rehacer", choices=["imagen", "video", "voz", "labios"],
                    help="regenera esto en --planos aunque esté bien (y lo que dependa de ello)")
    ap.add_argument("--hasta", choices=["imagen", "video", "voz", "labios", "sonido", "final"], default="final")
    ap.add_argument("--simular", action="store_true", help="solo dice qué haría; no genera ni cambia nada")
    ap.add_argument("--forzar", action="store_true", help="seguir aunque falte algo del Punto de control A")
    ap.add_argument("--sin-sonido", action="store_true")
    a = ap.parse_args()
    ids = set(a.planos.upper().replace(" ", "").split(",")) if a.planos else None
    sys.exit(orquestar(a.proyecto, ids, a.rehacer, a.simular, a.hasta, a.forzar, a.sin_sonido))


if __name__ == "__main__":
    main()
