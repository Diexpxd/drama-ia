"""Copia ligera de un vídeo para mandarla por Telegram (los bots solo pueden enviar hasta 50 MB).
Misma resolución y sonido; solo sube la compresión hasta que quepa. El original no se toca (es el de YouTube).

  python copia_ligera.py <video.mp4> [--mb 48]
Salida: <video>_telegram.mp4 junto al original (imprime su ruta en la última línea).
"""
import argparse
from fractions import Fraction
from pathlib import Path

import av


def recomprimir(origen: Path, destino: Path, crf: int):
    with av.open(str(origen)) as ent, av.open(str(destino), "w", options={"movflags": "+faststart"}) as sal:
        vin = ent.streams.video[0]
        vout = sal.add_stream("libx264", rate=vin.average_rate)
        vout.width, vout.height = vin.codec_context.width, vin.codec_context.height
        vout.pix_fmt = "yuv420p"
        vout.options = {"crf": str(crf), "preset": "medium"}
        ain = ent.streams.audio[0] if ent.streams.audio else None
        aout = None
        if ain is not None:
            aout = sal.add_stream("aac", rate=ain.codec_context.sample_rate, layout="stereo")
            aout.bit_rate = 160000
        n_video = n_audio = 0
        for paquete in ent.demux(*[s for s in (vin, ain) if s is not None]):
            for frame in paquete.decode():
                if paquete.stream.type == "video":
                    frame.pts, frame.time_base = n_video, Fraction(1, 1) / vin.average_rate
                    n_video += 1
                    sal.mux(vout.encode(frame))
                else:
                    frame.pts, frame.time_base = n_audio, Fraction(1, frame.sample_rate)
                    n_audio += frame.samples
                    sal.mux(aout.encode(frame))
        sal.mux(vout.encode(None))
        if aout is not None:
            sal.mux(aout.encode(None))


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("video")
    ap.add_argument("--mb", type=float, default=48)
    a = ap.parse_args()
    origen = Path(a.video)
    destino = origen.with_name(origen.stem + "_telegram.mp4")
    for crf in (22, 25, 28, 31):
        recomprimir(origen, destino, crf)
        mb = destino.stat().st_size / 1024 / 1024
        print(f"CRF {crf}: {mb:.1f} MB", flush=True)
        if mb <= a.mb:
            break
    print(destino)


if __name__ == "__main__":
    main()
