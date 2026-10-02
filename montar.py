"""Corte de imagen: une en orden las tomas elegidas en proyectos/<proyecto>/montaje.json"""
import argparse
import json
import math
import os
from fractions import Fraction

import av
from PIL import Image

import config


ENCUADRES = [1.0, 1.28, 1.0, 1.16]


def sufijo(fuente):
    """'videos' -> '', 'videos_720_x2' -> '_720_x2' (lo comparte mezclar.py para encontrar el corte)."""
    return "" if fuente == "videos" else "_" + fuente.removeprefix("videos_")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("proyecto")
    ap.add_argument("--fuente", default="videos")
    ap.add_argument("--fps", type=int, default=config.VID_FPS, help="fps de salida (32 con clips interpolados)")
    a = ap.parse_args()
    salida = montar(a.proyecto, a.fuente, a.fps)
    config.abrir(salida)


def montar(nombre, fuente="videos", fps=config.VID_FPS, carpeta=None, base=None, proyecto=None, dir_salida=None):
    """carpeta/base/proyecto/dir_salida: solo para pruebas (por defecto, las del proyecto)."""
    import linea_tiempo
    base = base or config.SALIDAS / nombre
    p_json = proyecto or config.cargar_proyecto(nombre)
    leer = None
    if p_json.get("ritmo") == "narracion":  # con este ritmo las voces fijan la duración de los planos
        from mezclar import leer_audio as leer
    L = linea_tiempo.calcular(nombre, fuente, False, leer, carpeta=carpeta, base=base, proyecto=proyecto)
    import rotulos
    planos_json = json.loads(((carpeta or config.PROYECTOS / nombre) / "planos.json").read_text(encoding="utf-8"))
    carteles = rotulos.ventanas(L, planos_json)
    vertical = p_json.get("formato") == "9:16"
    toma_max = p_json.get("cortes_ritmo")
    import tratamiento  # color unificado ("grade" en proyecto.json); sin la opción no cambia nada
    grade = tratamiento.parametros(p_json.get("grade"))

    salida = (dir_salida or base / "montaje") / f"{nombre}_corte{sufijo(fuente)}.mp4"
    salida.parent.mkdir(parents=True, exist_ok=True)
    out = av.open(str(salida), "w")
    stream = None
    pts = 0
    total = 0.0

    def emitir(frame, zoom=1.0):
        nonlocal pts
        t = pts / fps
        activos = [(a, b, r) for a, b, r in carteles if a <= t <= b]
        if zoom > 1.0 or activos:
            img = frame.to_image()
            if zoom > 1.0:  # recorte central cada vez más cerrado = empuje de cámara
                w, h = img.size
                cw, ch = w / zoom, h / zoom
                img = img.crop(((w - cw) / 2, (h - ch) / 2, (w + cw) / 2, (h + ch) / 2)).resize((w, h), Image.LANCZOS)
            for a, b, r in activos:
                img = rotulos.dibujar(img, r, t - a, b - a, vertical)
            frame = av.VideoFrame.from_image(img)
        if grade:
            frame = av.VideoFrame.from_ndarray(
                tratamiento.aplicar(frame.reformat(width=stream.width, height=stream.height, format="rgb24").to_ndarray(), grade),
                format="rgb24")
        nuevo = frame.reformat(width=stream.width, height=stream.height, format="yuv420p")
        nuevo.pts, nuevo.time_base = pts, Fraction(1, fps)
        out.mux(stream.encode(nuevo))
        pts += 1

    for pl in L.planos:
        with av.open(str(pl.clip)) as entrada:
            vin = entrada.streams.video[0]
            if stream is None:
                stream = out.add_stream("libx264", rate=Fraction(fps))
                stream.width, stream.height = vin.codec_context.width, vin.codec_context.height
                stream.pix_fmt = "yuv420p"
                stream.options = {"crf": "16", "preset": "slow"}
            max_frames = round(pl.dur * fps)
            fps_in = float(vin.average_rate or fps)  # p. ej. los clips de labios van a 25 (o 50) fps
            # el gancho ya lleva su empuje: no se parte
            tomas = max(1, math.ceil(pl.dur / toma_max - 0.15)) if toma_max and pl.zoom == 1.0 else 1

            def zoom_de(n):
                encuadre = ENCUADRES[min(tomas - 1, n * tomas // max(1, max_frames)) % len(ENCUADRES)]
                return encuadre * (1 + (pl.zoom - 1) * (n / max(1, max_frames - 1)) ** 2)  # acelera hacia el final

            n, ultimo = 0, None
            for j, frame in enumerate(entrada.decode(vin)):
                hasta = min(max_frames, int((j + 1) * fps / (fps_in * pl.velocidad) + 1e-6))
                while n < hasta:
                    emitir(frame, zoom_de(n))
                    n += 1
                ultimo = frame
                if n >= max_frames:
                    break
            while n < max_frames and ultimo is not None:  # clip más corto que el plano: se congela el final
                emitir(ultimo, zoom_de(n))
                n += 1
        total += n / fps
        lento = f" (a {pl.velocidad:.2f}x)" if pl.velocidad != 1 else ""
        lento += f" (gancho: empuje hasta x{pl.zoom:.2f})" if pl.zoom > 1 else ""
        lento += f" ({tomas} tomas)" if tomas > 1 else ""
        print(f"{pl.id}: {n / fps:.2f} s{lento}")
    out.mux(stream.encode())
    out.close()
    print(f"\n{total:.1f} s -> {salida}  (ritmo {L.ritmo})")
    return salida


if __name__ == "__main__":
    main()
