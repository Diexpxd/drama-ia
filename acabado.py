"""Acabado de las tomas elegidas en montaje.json: reescalado a la resolución final del formato, 1080p (SeedVR2) y/o fluidez x2 (FILM).
Cada plano se procesa por separado para no mezclar fotogramas entre cortes.

  python acabado.py piloto --interpolar
      videos\\       -> videos_x2\\       (16 -> 32 fps; los de labios 25 -> 50)
  python acabado.py piloto --reescalar --interpolar
      videos\\       -> videos_720\\      -> videos_720_x2\\

Luego:  montar.py piloto --fuente videos_720_x2 --fps 32   y   mezclar.py piloto --fuente videos_720_x2
"""
import argparse
import json

import av

import comfy
import config
from generar import lote


def fps_de(ruta):
    with av.open(str(ruta)) as c:
        return float(c.streams.video[0].average_rate)


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("proyecto")
    ap.add_argument("--reescalar", action="store_true")
    ap.add_argument("--interpolar", action="store_true")
    ap.add_argument("--planos")
    a = ap.parse_args()
    if not (a.reescalar or a.interpolar):
        raise SystemExit("Indica --reescalar y/o --interpolar")
    if not comfy.disponible():
        raise SystemExit("ComfyUI no responde. Arráncalo con arrancar_comfyui.ps1")
    base = config.SALIDAS / a.proyecto
    proyecto = config.cargar_proyecto(a.proyecto)
    eleccion = json.loads((config.PROYECTOS / a.proyecto / "montaje.json").read_text(encoding="utf-8"))
    solo = set(a.planos.split(",")) if a.planos else None
    clips = [f"{pid}_{toma}.mp4" for pid, toma in eleccion.items() if toma and (not solo or pid in solo)]
    tmp = f"drama-ia/{a.proyecto}/tmp"
    registro = base / "registro.jsonl"

    origen = "videos"
    if a.reescalar:
        trabajos = []
        for c in clips:
            fps = fps_de(base / origen / c)
            trabajos.append((comfy.flujo_reescalar(comfy.subir_imagen(base / origen / c), tmp, fps,
                                                   *config.dims(proyecto, "final")),
                             base / "videos_720" / c, {"tipo": "reescalar", "clip": c}))
        print("Reescalando a %dx%d (SeedVR2)..." % config.dims(proyecto, "final"))
        lote(trabajos, registro)
        origen = "videos_720"
    if a.interpolar:
        trabajos = []
        for c in clips:
            fps = fps_de(base / origen / c)
            trabajos.append((comfy.flujo_interpolar(comfy.subir_imagen(base / origen / c), tmp, fps * 2),
                             base / f"{origen}_x2" / c, {"tipo": "interpolar", "clip": c, "fps": fps * 2}))
        print("Interpolando x2 (FILM)...")
        lote(trabajos, registro)


if __name__ == "__main__":
    main()
