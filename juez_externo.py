"""Jueces de respaldo que VEN el vídeo y OYEN su audio, con API compatible con OpenAI y nivel gratuito.
Sirven cuando Gemini (nivel gratuito) está saturado (503) o sin cuota. Investigados el 2026-10-01:

  nvidia   NVIDIA NIM, build.nvidia.com: Nemotron 3 Nano Omni (30B-A3B), gratis, ~40 peticiones/min.
           Clave: NVIDIA_API_KEY en secretos.env (cuenta gratuita de NVIDIA Developer, requiere verificar teléfono).
  alibaba  Alibaba Model Studio (Singapur): Qwen3.8-Omni-Flash, 1 M de tokens gratis 90 días tras activarlo.
           Clave: DASHSCOPE_API_KEY en secretos.env.

El vídeo va en base64 dentro de la petición (Alibaba exige < 10 MB codificado), así que se reduce antes a ~8 MB
(540x960 conservando el audio). Cada juez puntúa distinto: NO mezclar sus notas con las de gemini-3.8-flash sin
calibrar antes con `calibrar_jueces.py` (los mismos Shorts de referencia).

  python juez_externo.py <video.mp4> [--proveedor nvidia|alibaba]
"""
import argparse
import base64
import json
import re
import sys
import time
from fractions import Fraction
from pathlib import Path

import requests

sys.path.insert(0, str(Path(__file__).resolve().parent))
import config  # noqa: E402

PROVEEDORES = {
    "nvidia": {"url": "https://integrate.api.nvidia.com/v1/chat/completions",
               "modelo": "nvidia/nemotron-3-nano-omni-30b-a3b-reasoning", "clave": "NVIDIA_API_KEY",
               "extra": {"max_tokens": 8000, "temperature": 0.2, "chat_template_kwargs": {"enable_thinking": False}},
               "stream": False, "audio_aparte": True},
    "alibaba": {"url": "https://dashscope-intl.aliyuncs.com/compatible-mode/v1/chat/completions",
                "modelo": "qwen3.8-omni-flash", "clave": "DASHSCOPE_API_KEY",
                "extra": {"modalities": ["text"], "max_tokens": 8000}, "stream": True},
}
MB_OBJETIVO = 8.0
PY_COMFY = config.PY_COMFY  # necesita PyAV


def disponibles():
    """Proveedores con clave en secretos.env."""
    s = config.leer_secretos()
    return [n for n, p in PROVEEDORES.items() if s.get(p["clave"])]


def audio_wav(video: Path) -> Path:
    """Extrae el audio como WAV 16 kHz mono (_juez.wav, junto al original; se reutiliza)."""
    destino = video.with_name(video.stem + "_juez.wav")
    if destino.exists() and destino.stat().st_mtime >= video.stat().st_mtime:
        return destino
    try:
        import av
    except ImportError:
        import subprocess
        r = subprocess.run([PY_COMFY, str(Path(__file__).resolve()), "--solo-wav", str(video)],
                           capture_output=True, text=True)
        if r.returncode != 0 or not destino.exists():
            raise RuntimeError("No se pudo extraer el audio: " + (r.stderr or "")[-300:])
        return destino
    import wave
    import numpy as np
    res = av.AudioResampler(format="s16", layout="mono", rate=16000)
    trozos = []
    with av.open(str(video)) as ent:
        for fr in ent.decode(ent.streams.audio[0]):
            for r in res.resample(fr):
                trozos.append(r.to_ndarray().reshape(-1))
    datos = np.concatenate(trozos).astype(np.int16)
    with wave.open(str(destino), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(16000)
        w.writeframes(datos.tobytes())
    return destino


def reducir(video: Path, mb=MB_OBJETIVO, mudo=False) -> Path:
    """Copia 540x960 con ~mb MB. Se guarda junto al original (_juez.mp4, o _juez_mudo.mp4 sin pista de audio) y se"""
    destino = video.with_name(video.stem + ("_juez_mudo.mp4" if mudo else "_juez.mp4"))
    if destino.exists() and destino.stat().st_mtime >= video.stat().st_mtime:
        return destino
    try:
        import av
    except ImportError:  # el venv "nube" no trae PyAV: se reduce con el de ComfyUI
        import subprocess
        r = subprocess.run([PY_COMFY, str(Path(__file__).resolve()), "--solo-reducir", str(video), "--mb", str(mb)]
                           + (["--mudo"] if mudo else []), capture_output=True, text=True)
        if r.returncode != 0 or not destino.exists():
            raise RuntimeError("No se pudo reducir el vídeo: " + (r.stderr or "")[-300:])
        return destino
    with av.open(str(video)) as ent:
        vin = ent.streams.video[0]
        dur = float(ent.duration) / 1e6 if ent.duration else 60.0
        audio_bps = 96_000
        video_bps = max(300_000, int(mb * 8e6 * 0.92 / dur) - audio_bps)
        ancho, alto = (540, 960) if vin.codec_context.height >= vin.codec_context.width else (960, 540)
        with av.open(str(destino), "w", options={"movflags": "+faststart"}) as sal:
            vout = sal.add_stream("libx264", rate=vin.average_rate)
            vout.width, vout.height, vout.pix_fmt, vout.bit_rate = ancho, alto, "yuv420p", video_bps
            vout.options = {"preset": "medium"}
            ain = ent.streams.audio[0] if ent.streams.audio and not mudo else None
            aout = None
            if ain is not None:
                aout = sal.add_stream("aac", rate=ain.codec_context.sample_rate, layout="stereo")
                aout.bit_rate = audio_bps
            nv = na = 0
            for paquete in ent.demux(*[s for s in (vin, ain) if s is not None]):
                for fr in paquete.decode():
                    if paquete.stream.type == "video":
                        fr = fr.reformat(width=ancho, height=alto, format="yuv420p")
                        fr.pts, fr.time_base = nv, Fraction(1, 1) / vin.average_rate
                        nv += 1
                        for p in vout.encode(fr):
                            sal.mux(p)
                    elif aout is not None:
                        fr.pts = na
                        na += fr.samples
                        fr.time_base = Fraction(1, fr.sample_rate)
                        for p in aout.encode(fr):
                            sal.mux(p)
            for st in (vout, aout):
                if st is not None:
                    for p in st.encode(None):
                        sal.mux(p)
    return destino


def _quitar_razonamiento(texto):
    return re.sub(r"<think>.*?</think>", "", texto or "", flags=re.S).strip()


def evaluar_con(proveedor, video: Path, prompt: str, reintentos=3):
    """-> (texto del informe, nombre del modelo). Lanza RuntimeError si falla."""
    p = PROVEEDORES[proveedor]
    clave = config.leer_secretos().get(p["clave"])
    if not clave:
        raise RuntimeError(f"Falta {p['clave']} en {config.SECRETOS}")
    aparte = p.get("audio_aparte", False)
    copia = reducir(Path(video), mudo=aparte)
    b64 = base64.b64encode(copia.read_bytes()).decode()
    contenido = [{"type": "video_url", "video_url": {"url": "data:video/mp4;base64," + b64}}]
    if aparte:
        wav = audio_wav(Path(video))
        contenido.append({"type": "audio_url", "audio_url": {
            "url": "data:audio/wav;base64," + base64.b64encode(wav.read_bytes()).decode()}})
    contenido.append({"type": "text", "text": prompt})
    cuerpo = {"model": p["modelo"], "stream": p["stream"],
              "messages": [{"role": "user", "content": contenido}], **p["extra"]}
    cab = {"Authorization": f"Bearer {clave}", "Content-Type": "application/json"}
    ultimo = None
    for intento in range(1, reintentos + 1):
        print(f"Evaluando con {p['modelo']} ({proveedor}, intento {intento}; {copia.stat().st_size / 1e6:.1f} MB)...", flush=True)
        try:
            r = requests.post(p["url"], headers=cab, json=cuerpo, timeout=600, stream=p["stream"])
            if r.status_code in (429, 500, 502, 503, 504):
                ultimo = f"{r.status_code}: {r.text[:200]}"
                time.sleep(20 * intento)
                continue
            if r.status_code >= 400:
                raise RuntimeError(f"{proveedor} {r.status_code}: {r.text[:300]}")
            if p["stream"]:
                partes = []
                for linea in r.iter_lines(decode_unicode=True):
                    if not linea or not linea.startswith("data:") or linea.strip() == "data: [DONE]":
                        continue
                    try:
                        d = json.loads(linea[5:])
                    except ValueError:
                        continue
                    for ch in d.get("choices", []):
                        partes.append((ch.get("delta") or {}).get("content") or "")
                texto = "".join(partes)
            else:
                texto = r.json()["choices"][0]["message"]["content"]
            texto = _quitar_razonamiento(texto)
            if not texto:
                raise RuntimeError(f"{proveedor} devolvió un informe vacío")
            return texto, f"{p['modelo']} ({proveedor})"
        except requests.RequestException as e:
            ultimo = str(e)[:200]
            time.sleep(10 * intento)
    raise RuntimeError(f"{proveedor} no respondió: {ultimo}")


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("video")
    ap.add_argument("--solo-reducir", action="store_true", help="(interno) solo crea la copia _juez.mp4")
    ap.add_argument("--mb", type=float, default=MB_OBJETIVO)
    ap.add_argument("--mudo", action="store_true", help="(interno) la copia sin pista de audio")
    ap.add_argument("--solo-wav", action="store_true", help="(interno) solo crea el WAV _juez.wav")
    ap.add_argument("--proveedor", choices=list(PROVEEDORES))
    a = ap.parse_args()
    if a.solo_reducir:
        print(reducir(Path(a.video), a.mb, a.mudo))
        return
    if a.solo_wav:
        print(audio_wav(Path(a.video)))
        return
    import evaluar
    prov = a.proveedor or (disponibles() or [None])[0]
    if not prov:
        raise SystemExit("Ningún proveedor tiene clave en secretos.env (NVIDIA_API_KEY o DASHSCOPE_API_KEY)")
    texto, modelo = evaluar_con(prov, Path(a.video), evaluar.PROMPT.read_text(encoding="utf-8"))
    print("\n" + texto + "\n")
    print(f"Notas ({modelo}): {evaluar.resumen_notas(evaluar.notas_de(texto))}")


if __name__ == "__main__":
    main()
