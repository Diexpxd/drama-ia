"""Sincronía de labios con InfiniteTalk para los planos con diálogo.

  python labios.py piloto [--tomas 2] [--planos P006]

Usa la imagen elegida del plano y la voz locutada (voces\\lineas\\<plano>.wav), con el mismo retardo que
mezclar.py para que los labios caigan donde suena la voz. Si el plano tiene a dos personas, las zonas de las
caras salen de proyectos\\<proyecto>\\labios.json. Resultado: videos\\<plano>_labios_t1.mp4 (25 fps);
para usarlo, pon "labios_t1" como toma de ese plano en montaje.json.
"""
import argparse
import json
from pathlib import Path

import av
import numpy as np
from PIL import Image, ImageDraw, ImageFilter

import comfy
import config
from generar import lote
from mezclar import SR, VOZ_RETARDO, leer_audio


def guardar_wav(x, ruta: Path):
    with av.open(str(ruta), "w") as out:
        s = out.add_stream("pcm_s16le", rate=SR, layout="stereo")
        datos = (np.clip(x, -1, 1) * 32767).astype(np.int16)
        frame = av.AudioFrame.from_ndarray(datos.reshape(1, -1), format="s16", layout="stereo")
        frame.sample_rate = SR
        out.mux(s.encode(frame))
        out.mux(s.encode(None))


def audio_del_plano(voz: Path, segundos: float, destino: Path):
    """Voz con el retardo de la mezcla y rellena de silencio hasta la duración del plano."""
    v = leer_audio(voz)
    x = np.zeros((int(segundos * SR), 2), np.float32)
    i = int(VOZ_RETARDO * SR)
    x[i:i + len(v)] = v[: len(x) - i]
    guardar_wav(x, destino)


def mascara(caja, destino: Path, tam=(1280, 720)):
    m = Image.new("RGB", tam, "black")
    ImageDraw.Draw(m).rectangle(caja, fill="white")
    m.filter(ImageFilter.GaussianBlur(8)).save(destino)


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("proyecto")
    ap.add_argument("--tomas", type=int, default=2)
    ap.add_argument("--planos")
    a = ap.parse_args()
    if not comfy.disponible():
        raise SystemExit("ComfyUI no responde. Arráncalo con arrancar_comfyui.ps1")
    base = config.SALIDAS / a.proyecto
    carpeta = config.PROYECTOS / a.proyecto
    planos = json.loads((carpeta / "planos.json").read_text(encoding="utf-8"))
    proyecto = config.cargar_proyecto(a.proyecto)
    cajas = json.loads((carpeta / "labios.json").read_text(encoding="utf-8")) if (carpeta / "labios.json").exists() else {}
    solo = set(a.planos.split(",")) if a.planos else None

    trabajos = []
    for p in planos:
        if not p.get("dialogo") or (solo and p["id"] not in solo):
            continue
        destinos = [base / "videos" / f"{p['id']}_labios_t{t}.mp4" for t in range(1, a.tomas + 1)]
        nuevos = trabajos_labios(a.proyecto, proyecto, p, cajas, destinos)
        if nuevos is None:
            print(f"{p['id']}: falta la voz o la imagen elegida, se salta")
            continue
        trabajos += nuevos
    lote(trabajos, base / "registro.jsonl")


def trabajos_labios(nombre, proyecto, p, cajas, destinos):
    """Prepara (audio con retardo, máscaras, prompt) y devuelve un trabajo de lote por cada destino, o None si falta la voz o la imagen elegida."""
    base = config.SALIDAS / nombre
    tmp = base / "labios_tmp"
    tmp.mkdir(parents=True, exist_ok=True)
    tam_img = config.dims(proyecto, "img")  # las cajas de labios.json van sobre la imagen elegida
    voz = base / "voces" / "lineas" / f"{p['id']}.wav"
    imagen = base / "elegidas" / f"{p['id']}.png"
    if not voz.exists() or not imagen.exists():
        return None
    audio_del_plano(voz, p["duracion_s"], tmp / f"{p['id']}_voz.wav")
    extra = {}
    if p["id"] in cajas:
        mascara(cajas[p["id"]]["habla"], tmp / f"{p['id']}_habla.png", tam_img)
        mascara(cajas[p["id"]]["calla"], tmp / f"{p['id']}_calla.png", tam_img)
        guardar_wav(np.zeros((int(p["duracion_s"] * SR), 2), np.float32), tmp / f"{p['id']}_silencio.wav")
        extra = {"mascara_habla": comfy.subir_imagen(tmp / f"{p['id']}_habla.png"),
                 "mascara_calla": comfy.subir_imagen(tmp / f"{p['id']}_calla.png"),
                 "audio_silencio": comfy.subir_imagen(tmp / f"{p['id']}_silencio.wav")}
    img = comfy.subir_imagen(imagen)
    aud = comfy.subir_imagen(tmp / f"{p['id']}_voz.wav")
    habla = p["dialogo"]["personaje"]
    prompt = (f"{habla} is speaking, natural lip movement and subtle facial expression, the other person stays silent "
              f"and still. {p.get('ambientacion', '')}. Static camera, cinematic.")
    trabajos = []
    for destino in destinos:
        s = comfy.semilla()
        trabajos.append((comfy.flujo_labios(img, aud, prompt, f"drama-ia/{nombre}/tmp", p["duracion_s"],
                                             *config.dims(proyecto, "vid"), seed=s, **extra),
                         destino, {"tipo": "labios", "plano": p["id"], "seed": s, "dos_personas": bool(extra)}))
    return trabajos


if __name__ == "__main__":
    main()
