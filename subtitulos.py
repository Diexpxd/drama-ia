"""Subtítulos dinámicos quemados en el vídeo (estilo Shorts/Reels): 2-3 palabras a la vez, mayúsculas, fuente gruesa
con borde negro, en blanco con la palabra que suena en amarillo. Centro de la pantalla en vertical; tercio inferior
en horizontal.

Se ejecuta con su propio venv (Whisper, solo CPU: no le quita GPU a nada):
  python subtitulos.py piloto [--fuente videos_720_x2] [--modelo small]

Usa lo que deja mezclar.py en montaje\\: <proyecto>_con_sonido*.mp4, <proyecto>_voces*.wav (pista solo de voces) y
<proyecto>_frases*.json (texto y tiempos de cada frase). Whisper solo aporta CUÁNDO suena cada palabra; el TEXTO sale
del guion, así los nombres propios salen bien escritos aunque Whisper los entienda mal.
Salida: montaje\\<proyecto>_subtitulado*.mp4
"""
import argparse
import difflib
import json
import re
import unicodedata
from fractions import Fraction
from pathlib import Path

import av
import numpy as np
from PIL import Image, ImageDraw, ImageFont

import config
from montar import sufijo

MAX_PALABRAS = 3
MAX_CARACTERES = 16
AMARILLO, BLANCO, NEGRO = (255, 214, 10, 255), (255, 255, 255, 255), (0, 0, 0, 255)
FUENTES = [r"C:\Windows\Fonts\seguibl.ttf", r"C:\Windows\Fonts\impact.ttf", r"C:\Windows\Fonts\arialbd.ttf"]


def normal(palabra):
    p = unicodedata.normalize("NFKD", palabra.lower())
    return re.sub(r"[^\w]", "", "".join(c for c in p if not unicodedata.combining(c)))


def audio_mono_16k(ruta):
    with av.open(str(ruta)) as c:
        rs = av.AudioResampler(format="fltp", layout="mono", rate=16000)
        partes = [f.to_ndarray()[0] for fr in c.decode(audio=0) for f in rs.resample(fr)]
        partes += [f.to_ndarray()[0] for f in rs.resample(None)]
    return np.concatenate(partes).astype(np.float32)


def tiempos_palabras(modelo, audio16, frase, idioma):
    """[(palabra_del_guion, inicio, fin)] con los tiempos de Whisper emparejados al texto del guion."""
    guion = frase["texto"].split()
    a, b = frase["inicio"], frase["fin"]
    trozo = audio16[max(0, int((a - 0.05) * 16000)): int((b + 0.1) * 16000)]
    oidas = []
    if modelo is not None and len(trozo) > 1600:
        segmentos, _ = modelo.transcribe(trozo, language=idioma, word_timestamps=True, beam_size=5,
                                         vad_filter=False, initial_prompt=frase["texto"], condition_on_previous_text=False)
        oidas = [(w.word.strip(), a - 0.05 + w.start, a - 0.05 + w.end) for s in segmentos for w in (s.words or [])]
    tiempos = [None] * len(guion)
    sm = difflib.SequenceMatcher(a=[normal(w) for w in guion], b=[normal(w) for w, _, _ in oidas], autojunk=False)
    for bloque in sm.get_matching_blocks():
        for k in range(bloque.size):
            _, t0, t1 = oidas[bloque.b + k]
            tiempos[bloque.a + k] = (max(a, t0), min(b + 0.1, t1))
    # palabras sin emparejar: se reparten entre sus vecinas según su longitud
    i = 0
    while i < len(guion):
        if tiempos[i] is not None:
            i += 1
            continue
        j = i
        while j < len(guion) and tiempos[j] is None:
            j += 1
        t_ini = tiempos[i - 1][1] if i > 0 else a
        t_fin = tiempos[j][0] if j < len(guion) else b
        pesos = [max(1, len(normal(w))) for w in guion[i:j]]
        paso, t = (t_fin - t_ini) / sum(pesos), t_ini
        for k, peso in zip(range(i, j), pesos):
            tiempos[k] = (t, t + paso * peso)
            t += paso * peso
        i = j
    return [(w, t0, t1) for w, (t0, t1) in zip(guion, tiempos)]


def bloques(palabras, fin_frase):
    """Agrupa en bloques de 2-3 palabras; corta tras un signo de puntuación."""
    res, actual = [], []
    for k, (w, t0, t1) in enumerate(palabras):
        actual.append((w, t0, t1))
        largo = sum(len(x[0]) + 1 for x in actual)
        siguiente = palabras[k + 1][0] if k + 1 < len(palabras) else ""
        if (len(actual) >= MAX_PALABRAS or re.search(r"[.,;:!?…]$", w) or
                largo + len(siguiente) > MAX_CARACTERES):
            res.append(actual)
            actual = []
    if actual:
        res.append(actual)
    salida = []
    for n, b in enumerate(res):
        ini = b[0][1]
        fin = res[n + 1][0][1] if n + 1 < len(res) else min(fin_frase + 0.3, b[-1][2] + 0.25)
        salida.append({"palabras": b, "inicio": ini, "fin": max(fin, ini + 0.25)})
    return salida


def cargar_fuente(tam):
    for f in FUENTES:
        if Path(f).exists():
            return ImageFont.truetype(f, tam)
    return ImageFont.load_default()


def dibujar(bloque, activa, ancho, alto, vertical):
    """Capa RGBA con el bloque de palabras; `activa` = índice de la palabra que suena (en amarillo)."""
    tam = int(ancho * (0.085 if vertical else 0.05))
    capa = Image.new("RGBA", (ancho, alto), (0, 0, 0, 0))
    d = ImageDraw.Draw(capa)
    palabras = [w.strip("\"'“”‘’«»").upper() or w.upper() for w, _, _ in bloque["palabras"]]  # sin comillas del guion
    while True:
        fuente = cargar_fuente(tam)
        borde = max(3, tam // 9)
        espacio = d.textlength(" ", font=fuente)
        anchos = [d.textlength(w, font=fuente) + 2 * borde for w in palabras]
        lineas = [list(range(len(palabras)))]
        if sum(anchos) + espacio * (len(palabras) - 1) > ancho * 0.84 and len(palabras) > 1:
            corte = (len(palabras) + 1) // 2
            lineas = [list(range(corte)), list(range(corte, len(palabras)))]
        mas_ancha = max(sum(anchos[k] for k in l) + espacio * (len(l) - 1) for l in lineas)
        if mas_ancha <= ancho * 0.92 or tam <= 12:
            break
        tam = int(tam * 0.9)
    alto_linea = int(tam * 1.15)
    y = int(alto * (0.5 if vertical else 0.8)) - alto_linea * len(lineas) // 2
    for linea in lineas:
        total = sum(anchos[k] for k in linea) + espacio * (len(linea) - 1)
        x = (ancho - total) / 2
        for k in linea:
            d.text((x + borde, y), palabras[k], font=fuente, fill=AMARILLO if k == activa else BLANCO,
                   stroke_width=borde, stroke_fill=NEGRO)
            x += anchos[k] + espacio
        y += alto_linea
    return capa


def subtitular(video, voces_wav, frases_json, salida, idioma="es", vertical=True, modelo_nombre="small"):
    datos = json.loads(Path(frases_json).read_text(encoding="utf-8"))
    try:
        from faster_whisper import WhisperModel
        modelo = WhisperModel(modelo_nombre, device="cpu", compute_type="int8")
    except Exception as e:
        print(f"aviso: Whisper no disponible ({e}); tiempos aproximados")
        modelo = None
    audio16 = audio_mono_16k(voces_wav)
    todos = []
    for frase in datos["frases"]:
        palabras = tiempos_palabras(modelo, audio16, frase, idioma)
        todos += bloques(palabras, frase["fin"])
        print(f"  {frase['plano']}: {len(palabras)} palabras", flush=True)

    with av.open(str(video)) as entrada, av.open(str(salida), "w") as out:
        vin = entrada.streams.video[0]
        fps = vin.average_rate or Fraction(config.VID_FPS)
        ancho, alto = vin.codec_context.width, vin.codec_context.height
        vout = out.add_stream("libx264", rate=fps)
        vout.width, vout.height, vout.pix_fmt = ancho, alto, "yuv420p"
        vout.options = {"crf": "17", "preset": "medium"}
        ain = entrada.streams.audio[0] if entrada.streams.audio else None
        aout = out.add_stream_from_template(ain) if ain else None
        cache, n = {}, 0
        for paquete in entrada.demux(*(s for s in (vin, ain) if s)):
            if paquete.stream is ain:
                if paquete.dts is not None:
                    paquete.stream = aout
                    out.mux(paquete)
                continue
            for frame in paquete.decode():
                t = float(frame.pts * frame.time_base) if frame.pts is not None else n / float(fps)
                img = frame.to_image()
                bloque = next((b for b in todos if b["inicio"] <= t < b["fin"]), None)
                if bloque:
                    activa = max((k for k, (_, t0, _) in enumerate(bloque["palabras"]) if t0 <= t), default=0)
                    clave = (id(bloque), activa)
                    if clave not in cache:
                        cache[clave] = dibujar(bloque, activa, ancho, alto, vertical)
                    img = Image.alpha_composite(img.convert("RGBA"), cache[clave]).convert("RGB")
                nuevo = av.VideoFrame.from_image(img)
                nuevo.pts, nuevo.time_base = n, Fraction(1, 1) / fps
                out.mux(vout.encode(nuevo))
                n += 1
        out.mux(vout.encode())
    return salida, len(todos)


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("proyecto")
    ap.add_argument("--fuente", default="videos", help="la misma que en montar.py / mezclar.py")
    ap.add_argument("--modelo", default="small", help="modelo de Whisper: tiny, base, small, medium, large-v3...")
    a = ap.parse_args()
    proyecto = config.cargar_proyecto(a.proyecto)
    m = config.SALIDAS / a.proyecto / "montaje"
    suf = sufijo(a.fuente)
    salida, n = subtitular(m / f"{a.proyecto}_con_sonido{suf}.mp4", m / f"{a.proyecto}_voces{suf}.wav",
                           m / f"{a.proyecto}_frases{suf}.json", m / f"{a.proyecto}_subtitulado{suf}.mp4",
                           proyecto["idioma"], proyecto["formato"] == "9:16", a.modelo)
    print(f"{n} bloques de subtítulos -> {salida}")
    config.abrir(salida)


if __name__ == "__main__":
    main()
