"""Voces con TTS local, en el "idioma" de proyecto.json (español si no se indica). Se ejecuta con el venv de voz:
  python voces.py disenar piloto [--variantes 2] [--motores voxcpm,qwen]
      Crea voces nuevas a partir de la descripción "voz" de cada personaje (y del narrador) en proyecto.json,
      con los dos motores -> salidas\\piloto\\voces\\diseno\\ y abre una página para escucharlas.
      Elige una por personaje y dile a Claude cuál (o crea proyectos\\piloto\\voces\\<Nombre>.json a mano:
      {"motor": "voxcpm", "wav": "<ruta del wav elegido>", "texto": "<lo que dice ese wav>"}).

  python voces.py locutar piloto
      Genera cada diálogo y narración de planos.json clonando la voz elegida
      -> salidas\\piloto\\voces\\lineas\\P005.wav ...

Motores (ambos Apache 2.0): VoxCPM2 (diseño + clonación con estilo) y Qwen3-TTS (VoiceDesign + Base).
"""
import argparse
import gc
import html
import json
import os
import shutil
from pathlib import Path

import numpy as np
import soundfile as sf
import torch

import config

FRASE_PRUEBA = {"es": "Nadie sabe cuánto tiempo ha pasado desde aquella noche.",
                "en": "Nobody knows how much time has passed since that night.",
                "pt": "Ninguém sabe quanto tempo passou desde aquela noite.",
                "fr": "Personne ne sait combien de temps a passé depuis cette nuit-là."}

MODELOS_TTS = config.MODELOS_TTS


def voces_del_proyecto(proyecto, planos):
    """[(nombre, descripcion_voz, frase_de_prueba)] para personajes con voz y el narrador."""
    frases = {}
    for p in planos:
        if p.get("dialogo"):
            frases.setdefault(p["dialogo"]["personaje"], p["dialogo"]["texto"])
        if p.get("narracion"):
            frases.setdefault("Narrador", p["narracion"])
    lista = [(c["nombre"], c["voz"]) for c in proyecto["personajes"] if c.get("voz")]
    if proyecto.get("narrador"):
        lista.append((proyecto["narrador"]["nombre"], proyecto["narrador"]["voz"]))
    prueba = FRASE_PRUEBA.get(proyecto.get("idioma", config.IDIOMA_POR_DEFECTO), FRASE_PRUEBA["en"])
    return [(n, v, frases.get(n, prueba)) for n, v in lista]


def liberar():
    gc.collect()
    torch.cuda.empty_cache()


def liberar_comfyui():
    """Si ComfyUI tiene modelos en la GPU, que los suelte (la VRAM no da para todo a la vez)."""
    try:
        import comfy
        if comfy.disponible():
            comfy.liberar_vram()
    except Exception:
        pass


class VoxCPM:
    def __init__(self):
        from voxcpm import VoxCPM as _V
        self.m = _V.from_pretrained(str(MODELOS_TTS / "VoxCPM2"), load_denoiser=False)
        self.sr = self.m.tts_model.sample_rate

    def disenar(self, texto, descripcion):
        return self.m.generate(text=f"({descripcion}){texto}", cfg_value=2.0, inference_timesteps=10), self.sr

    def clonar(self, texto, ref_wav, ref_texto, estilo=None):
        t = texto
        wav = self.m.generate(text=t, prompt_wav_path=str(ref_wav), prompt_text=ref_texto,
                              reference_wav_path=str(ref_wav), cfg_value=config.VOZ_CFG,
                              inference_timesteps=config.VOZ_PASOS)
        return wav, self.sr


class Qwen:
    def __init__(self, variante, idioma=config.IDIOMA_POR_DEFECTO):
        from qwen_tts import Qwen3TTSModel
        carpeta = {"diseno": "Qwen3-TTS-VoiceDesign", "clon": "Qwen3-TTS-Base"}[variante]
        self.idioma = config.IDIOMAS[idioma]
        self.m = Qwen3TTSModel.from_pretrained(str(MODELOS_TTS / carpeta), device_map="cuda:0", dtype=torch.bfloat16)

    def disenar(self, texto, descripcion):
        wavs, sr = self.m.generate_voice_design(text=texto, instruct=descripcion, language=self.idioma)
        return wavs[0], sr

    def clonar(self, texto, ref_wav, ref_texto, estilo=None):  # la variante Base no admite estilo
        wavs, sr = self.m.generate_voice_clone(text=texto, language=self.idioma, ref_audio=str(ref_wav), ref_text=ref_texto)
        return wavs[0], sr


def cargar_motor(nombre, variante, idioma=config.IDIOMA_POR_DEFECTO):
    return VoxCPM() if nombre == "voxcpm" else Qwen(variante, idioma)  # VoxCPM2 detecta el idioma solo


def cmd_disenar(a, proyecto, planos):
    salida = config.SALIDAS / a.proyecto / "voces" / "diseno"
    salida.mkdir(parents=True, exist_ok=True)
    voces = voces_del_proyecto(proyecto, planos)
    liberar_comfyui()
    for motor in a.motores.split(","):
        print(f"Cargando {motor}...", flush=True)
        m = cargar_motor(motor, "diseno", proyecto["idioma"])
        for nombre, desc, frase in voces:
            for v in range(1, a.variantes + 1):
                destino = salida / f"{nombre}_{motor}_v{v}.wav"
                if destino.exists():
                    continue
                wav, sr = m.disenar(frase, desc)
                sf.write(destino, np.asarray(wav, dtype=np.float32), sr)
                destino.with_suffix(".txt").write_text(frase, encoding="utf-8")
                print(f"  {destino.name} ({len(wav) / sr:.1f} s)", flush=True)
        del m
        liberar()
    pagina_escucha(a.proyecto, voces, salida)


def pagina_escucha(proyecto, voces, carpeta):
    bloques = []
    for nombre, desc, frase in voces:
        audios = "".join(
            f'<figure><figcaption>{w.stem}</figcaption><audio controls preload="none" src="diseno/{w.name}"></audio></figure>'
            for w in sorted(carpeta.glob(f"{nombre}_*.wav")))
        bloques.append(f"<section><h2>{html.escape(nombre)}</h2><p class='d'>{html.escape(desc)}</p>"
                       f"<p>«{html.escape(frase)}»</p><div class='a'>{audios}</div></section>")
    pagina = f"""<!doctype html><html lang="es"><head><meta charset="utf-8"><title>Voces · {proyecto}</title>
<meta name="viewport" content="width=device-width, initial-scale=1"><style>
body{{background:#111;color:#ddd;font:15px/1.45 system-ui,sans-serif;margin:0;padding:16px}}
section{{border-top:1px solid #333;padding:10px 0}} h2{{margin:4px 0;font-size:18px}} .d{{color:#999;margin:2px 0}}
.a{{display:flex;flex-wrap:wrap;gap:12px}} figure{{margin:0}} figcaption{{color:#fc6;font-weight:600}}
</style></head><body><h1>Voces diseñadas · {proyecto}</h1>
<p>Escucha y elige una por personaje (el nombre del archivo, p. ej. <b>Lin Feng_voxcpm_v2</b>).</p>
{''.join(bloques)}</body></html>"""
    ruta = carpeta.parent / "voces.html"
    ruta.write_text(pagina, encoding="utf-8")
    print(ruta)
    config.abrir(ruta)


def cmd_locutar(a, proyecto, planos):
    dir_voces = config.PROYECTOS / a.proyecto / "voces"
    salida = config.SALIDAS / a.proyecto / "voces" / "lineas"
    salida.mkdir(parents=True, exist_ok=True)
    lineas = []  # (id_plano, hablante, texto)
    for p in planos:
        if p.get("dialogo"):
            lineas.append((p["id"], p["dialogo"]["personaje"], p["dialogo"]["texto"]))
        if p.get("narracion"):
            lineas.append((f"{p['id']}_narr", "Narrador", p["narracion"]))
    elegidas = {}
    for _, hablante, _ in lineas:
        ficha = dir_voces / f"{hablante}.json"
        if not ficha.exists():
            raise SystemExit(f"Falta la voz elegida de {hablante}: {ficha}")
        elegidas[hablante] = json.loads(ficha.read_text(encoding="utf-8"))
    liberar_comfyui()
    for motor in sorted({v["motor"] for v in elegidas.values()}):
        m = cargar_motor(motor, "clon", proyecto["idioma"])
        for id_, hablante, texto in lineas:
            voz = elegidas[hablante]
            destino = salida / f"{id_}.wav"
            if voz["motor"] != motor or destino.exists():
                continue
            wav, sr = m.clonar(texto, voz["wav"], voz["texto"], voz.get("estilo"))
            sf.write(destino, np.asarray(wav, dtype=np.float32), sr)
            print(f"  {destino.name} {hablante}: «{texto}» ({len(wav) / sr:.1f} s)", flush=True)
        del m
        liberar()


def elegir(a):
    """voces.py elegir piloto "Lin Feng_voxcpm_v2" ...: copia la muestra y crea la ficha de la voz."""
    origen = config.SALIDAS / a.proyecto / "voces" / "diseno"
    destino = config.PROYECTOS / a.proyecto / "voces"
    destino.mkdir(parents=True, exist_ok=True)
    for muestra in a.muestras:
        nombre, motor, _ = muestra.rsplit("_", 2)
        wav = destino / f"{nombre}.wav"
        shutil.copy2(origen / f"{muestra}.wav", wav)
        texto = (origen / f"{muestra}.txt").read_text(encoding="utf-8")
        (destino / f"{nombre}.json").write_text(json.dumps(
            {"motor": motor, "wav": str(wav), "texto": texto, "muestra": muestra}, ensure_ascii=False, indent=2),
            encoding="utf-8")
        print(f"{nombre} -> {muestra}")


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    d = sub.add_parser("disenar"); d.add_argument("proyecto"); d.add_argument("--variantes", type=int, default=2)
    d.add_argument("--motores", default="voxcpm,qwen")
    e = sub.add_parser("elegir"); e.add_argument("proyecto"); e.add_argument("muestras", nargs="+")
    l = sub.add_parser("locutar"); l.add_argument("proyecto")
    a = ap.parse_args()
    if a.cmd == "elegir":
        return elegir(a)
    carpeta = config.PROYECTOS / a.proyecto
    proyecto = config.cargar_proyecto(a.proyecto)
    planos = json.loads((carpeta / "planos.json").read_text(encoding="utf-8"))
    {"disenar": cmd_disenar, "locutar": cmd_locutar}[a.cmd](a, proyecto, planos)


if __name__ == "__main__":
    main()
