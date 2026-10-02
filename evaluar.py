"""Evaluador automático: Gemini (API, nivel gratuito) ve el vídeo CON su audio y lo puntúa con prompt_evaluacion.txt.

  python evaluar.py the-fall-of-blockbuster [--video ruta.mp4]

ESTÁNDAR (META: igualar a los Shorts de referencia, escala real 1-10) en dos fases: para el render final, el
BORRADOR necesita la meta en ritmo y audio; para publicar, la VERSIÓN FINAL 1080p la necesita en visual, ritmo y
audio (--final para evaluarla). Sin --video evalúa el borrador del proyecto. Guarda el informe en
salidas\\<proyecto>\\evaluaciones\\<fecha>.md y una línea con las notas en evaluaciones.jsonl (para comparar
versiones). Avisa si alguna nota baja de la META. Nivel gratuito: no cuesta nada, pero Google puede usar lo
que se le envía para mejorar sus productos.
"""
import argparse
import hashlib
import json
import re
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

import config

RAIZ = Path(__file__).resolve().parent
PROMPT = RAIZ / "prompt_evaluacion.txt"
MODELO = config.GEMINI_MODELOS.get("evaluar", "gemini-3.8-flash")
MODELOS_RESERVA = ["gemini-3.7-flash", "gemini-3.6-flash", "gemini-3.5-flash", "gemini-3-flash-preview", "gemini-2.5-flash"]
AGOTADAS = RAIZ / "setup" / "gemini_agotadas.json"


def _dia_cuota():
    return (datetime.now(timezone.utc) - timedelta(hours=8)).strftime("%Y-%m-%d")  # hora del Pacífico (sin horario de verano: margen de 1 h)


def _huella(clave):
    return hashlib.sha256(clave.encode()).hexdigest()[:10]


def _leer_agotadas():
    try:
        datos = json.loads(AGOTADAS.read_text(encoding="utf-8"))
        return set(datos["agotadas"]) if datos.get("dia") == _dia_cuota() else set()
    except Exception:
        return set()


def _marcar_agotada(clave, modelo):
    agotadas = _leer_agotadas() | {f"{_huella(clave)}|{modelo}"}
    AGOTADAS.write_text(json.dumps({"dia": _dia_cuota(), "agotadas": sorted(agotadas)}), encoding="utf-8")


def _generar(preparar, cliente=None):
    if cliente is not None:
        clientes = [(None, cliente)]
    else:
        from google import genai
        claves = config.claves_gemini()
        if not claves:
            raise SystemExit(f"Falta GEMINI_API_KEY en {config.SECRETOS}")
        clientes = [(c, None) for c in claves]
    creados, saturado = {}, False
    for modelo in [MODELO] + [m for m in MODELOS_RESERVA if m != MODELO]:
        agotadas = _leer_agotadas()
        for n, (clave, fijo) in enumerate(clientes, 1):
            if clave and f"{_huella(clave)}|{modelo}" in agotadas:
                continue
            cli = fijo or creados.get(clave) or creados.setdefault(clave, genai.Client(api_key=clave))
            print(f"Evaluando con {modelo} (clave {n}/{len(clientes)})...", flush=True)
            try:
                return cli.models.generate_content(model=modelo, contents=preparar(cli)), modelo, cli
            except Exception as e:
                codigo = getattr(e, "code", None) or getattr(e, "status_code", None)
                if codigo == 429:
                    if clave and "perday" in str(e).lower().replace(" ", ""):
                        _marcar_agotada(clave, modelo)
                    print(f"  clave {n}: sin cuota en {modelo} (429); paso a la siguiente clave", flush=True)
                    continue
                if codigo in (500, 503):
                    print(f"  {modelo} saturado ({codigo}); paso al siguiente modelo", flush=True)
                    saturado = True
                    break
                raise
    if saturado:
        raise RuntimeError("Gemini saturado (503, alta demanda en Google): suele pasar en un rato")
    raise RuntimeError("Gemini: ninguna clave tiene cuota libre en los modelos gratuitos (se renueva a la "
                       "medianoche del Pacífico, ~01:00 en México)")
META = {"visual": 5, "ritmo": 6, "audio": 6}
JUEZ_META = "gemini-3.8-flash"
EXIGE = {"final": ("ritmo", "audio"), "publicar": ("visual", "ritmo", "audio")}
DIMENSIONES = {"visual": r"calidad\s+visual", "ritmo": r"ritmo", "audio": r"audio"}


def video_de(nombre, fase="borrador"):
    """Borrador 480p o versión final 1080p (los archivos finales llevan _720_x2 por herencia)."""
    m = config.SALIDAS / nombre / "montaje"
    sufijo = "_720_x2" if fase == "final" else ""
    for base in ("subtitulado", "con_sonido"):
        if (m / f"{nombre}_{base}{sufijo}.mp4").exists():
            return m / f"{nombre}_{base}{sufijo}.mp4"
    return None


def fase_de(fila):
    return fila.get("fase") or ("final" if "_720_x2" in fila.get("video", "") else "borrador")


def notas_de(informe):
    """'NOTAS: visual 8/10, ritmo 7/10, audio 8/10' (última línea que pide el prompt) o, si falta, cada apartado"""
    num = r"(\d+(?:[.,]\d)?)\s*/\s*10"
    linea = re.search(rf"NOTAS:\s*visual\W*{num}\W+ritmo\W*{num}\W+audio\W*{num}", informe, re.I)
    if linea:
        return {k: float(v.replace(",", ".")) for k, v in zip(DIMENSIONES, linea.groups())}
    notas = {}
    for clave, patron in DIMENSIONES.items():
        m = re.search(patron + r"(?:(?!\n\s*(?:#|\d+\.\s)).){0,300}?(\d+(?:[.,]\d)?)\s*/\s*10", informe, re.I | re.S)
        notas[clave] = float(m.group(1).replace(",", ".")) if m else None
    return notas


def _respaldo_externo(video, error):
    import juez_externo
    prompt = PROMPT.read_text(encoding="utf-8")
    for prov in juez_externo.disponibles():
        print(f"Gemini no respondió ({str(error)[:60]}); pruebo {prov}...", flush=True)
        try:
            return juez_externo.evaluar_con(prov, video, prompt)
        except Exception as e:  # noqa: BLE001
            print(f"  {prov} falló: {str(e)[:120]}", flush=True)
    raise error


def evaluar(nombre, video=None, cliente=None, fase="borrador"):
    """-> (informe markdown, notas, ruta del informe)"""
    video = Path(video) if video else video_de(nombre, fase)
    if not video or not video.exists():
        raise SystemExit(f"{nombre} no tiene {'versión final' if fase == 'final' else 'borrador'} que evaluar")
    fase = "final" if "_720_x2" in video.name else "borrador"
    subidos = {}

    def preparar(cli):
        if id(cli) not in subidos:
            print(f"Subiendo {video.name} ({video.stat().st_size / 1e6:.0f} MB) a Gemini...", flush=True)
            archivo = cli.files.upload(file=str(video))
            subidos[id(cli)] = (cli, archivo)
            for _ in range(120):  # Google procesa el vídeo antes de poder usarlo
                estado = str(getattr(archivo.state, "name", archivo.state))
                if estado == "ACTIVE":
                    break
                if estado == "FAILED":
                    raise RuntimeError("Gemini no pudo procesar el vídeo")
                time.sleep(3)
                archivo = cli.files.get(name=archivo.name)
            subidos[id(cli)] = (cli, archivo)
        return [subidos[id(cli)][1], PROMPT.read_text(encoding="utf-8")]

    try:
        try:
            r, modelo, _ = _generar(preparar, cliente)
            informe = (r.text or "").strip()
        except RuntimeError as e:
            informe, modelo = _respaldo_externo(video, e)
    finally:
        for cli, archivo in subidos.values():
            try:
                cli.files.delete(name=archivo.name)  # no dejar el vídeo en Google más de lo necesario
            except Exception:
                pass
    if not informe:
        raise RuntimeError("Gemini devolvió un informe vacío")
    notas = notas_de(informe)
    carpeta = config.SALIDAS / nombre / "evaluaciones"
    carpeta.mkdir(parents=True, exist_ok=True)
    fecha = datetime.now()
    ruta = carpeta / f"{fecha:%Y%m%d-%H%M%S}.md"
    ruta.write_text(f"# Evaluación de {video.name} ({fecha:%Y-%m-%d %H:%M}, {modelo})\n\n{informe}\n", encoding="utf-8")
    with open(config.SALIDAS / nombre / "evaluaciones.jsonl", "a", encoding="utf-8") as f:
        f.write(json.dumps({"fecha": fecha.isoformat(timespec="seconds"), "video": video.name, "modelo": modelo,
                            "notas": notas, "escala": escala_del_prompt(), "fase": fase, "informe": ruta.name},
                           ensure_ascii=False) + "\n")
    return informe, notas, ruta


def evaluar_url(url, cliente=None):
    """CALIBRACIÓN: evalúa con el mismo prompt un vídeo público de YouTube (Gemini lo ve por su enlace, sin descargarlo)."""
    from google.genai import types
    video = types.Part(file_data=types.FileData(file_uri=url, mime_type="video/*"))
    r, modelo, _ = _generar(lambda cli: [video, PROMPT.read_text(encoding="utf-8")], cliente)
    informe = (r.text or "").strip()
    return informe, notas_de(informe), modelo


def escala_del_prompt():
    return "3-7" if "3 a 7" in PROMPT.read_text(encoding="utf-8") else "1-10"


def pasa(notas, claves=("visual", "ritmo", "audio")):
    return bool(notas) and all(notas.get(k) is not None and notas[k] >= META[k] for k in claves)


def texto_meta(claves=("visual", "ritmo", "audio")):
    """-> 'ritmo 6+, audio 6+'"""
    return ", ".join(f"{k} {META[k]}+" for k in claves)


def estandar(nombre, para="final"):
    """para="final": ¿el borrador puede ir a render final? | para="publicar": ¿la versión final se puede publicar?"""
    fase = "borrador" if para == "final" else "final"
    video = video_de(nombre, fase)
    if not video:
        return False, "todavía no hay " + ("borrador" if fase == "borrador" else "versión final 1080p"), None
    hist = [h for h in historial(nombre) if fase_de(h) == fase]
    if not hist:
        return False, f"el {'borrador' if fase == 'borrador' else 'vídeo final'} todavía no se ha evaluado", None
    ult = hist[-1]
    notas = ult.get("notas") or {}
    if datetime.fromisoformat(ult["fecha"]).timestamp() < video.stat().st_mtime - 5:
        return False, "el vídeo cambió después de la última evaluación: hay que evaluarlo otra vez", notas
    if ult.get("escala", "3-7") != "1-10":
        return False, "la última evaluación usó la escala antigua (3-7): hay que evaluarlo otra vez", notas
    claves = EXIGE[para]
    otro_juez = f" (ojo: lo evaluó {ult.get('modelo')}; la meta está medida con {JUEZ_META})" \
        if ult.get("modelo") and ult["modelo"] != JUEZ_META else ""
    if not pasa(notas, claves):
        faltan = [f"{k} ({'?' if notas.get(k) is None else f'{notas[k]:g}'}, meta {META[k]})"
                  for k in claves if notas.get(k) is None or notas[k] < META[k]]
        return False, f"no llega a la meta en: {', '.join(faltan)}{otro_juez}", notas
    return True, f"pasa ({texto_meta(claves)}){otro_juez}", notas


def historial(nombre):
    ruta = config.SALIDAS / nombre / "evaluaciones.jsonl"
    if not ruta.exists():
        return []
    return [json.loads(l) for l in ruta.read_text(encoding="utf-8").splitlines() if l.strip()]


def resumen_notas(notas):
    return " · ".join(f"{k} {'?' if v is None else f'{v:g}'}/10" for k, v in notas.items())


def bajas(notas):
    return [k for k, v in notas.items() if v is not None and v < META.get(k, 0)]


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("proyecto")
    ap.add_argument("--video")
    ap.add_argument("--final", action="store_true", help="evaluar la versión final 1080p (para publicar)")
    a = ap.parse_args()
    informe, notas, ruta = evaluar(a.proyecto, a.video, fase="final" if a.final else "borrador")
    print("\n" + informe + "\n")
    print(f"Notas: {resumen_notas(notas)}")
    for para, texto in (("final", "render final"), ("publicar", "publicar")):
        ok, motivo, _ = estandar(a.proyecto, para)
        print(f"  {texto}: {'SÍ' if ok else 'NO'} ({motivo})")
    print(f"Informe: {ruta}")


if __name__ == "__main__":
    main()
