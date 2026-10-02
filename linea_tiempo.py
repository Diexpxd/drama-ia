"""Línea de tiempo común a montar.py y mezclar.py: cuándo empieza y cuánto dura cada plano, y dónde va cada voz."""
import json
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np

import config

SR = 48000
VOZ_RETARDO = 0.3        # ritmo normal: la frase empieza a 0,3 s del inicio de su plano
HUECO_ENTRE_FRASES = 0.12  # ritmo narración
ENTRADA_ESCENA = 0.2       # ritmo narración: respiro antes de la primera frase de cada escena
SALIDA_ESCENA = 0.15
MIN_PLANO = 1.2            # ritmo narración: ningún plano dura menos de esto
VELOCIDAD_MIN = 0.6        # ritmo narración: lo más que se ralentiza un clip corto
GANCHO_VELOCIDAD_MAX = 1.6
GANCHO_ZOOM = 1.35          # gancho: zoom de empuje digital (Wan apenas mueve la cámara con 4 pasos)


def comprimir_silencios(x, sr=SR, max_pausa=0.3, deja=0.12, umbral_db=-35.0):
    """Recorta las pausas internas de más de `max_pausa` s a `deja` s y quita el silencio inicial y final."""
    if len(x) == 0:
        return x
    ventana = int(0.01 * sr)  # bloques de 10 ms
    n = len(x) // ventana
    if n == 0:
        return x
    bloques = x[: n * ventana].reshape(n, ventana, -1)
    rms = np.sqrt((bloques ** 2).mean(axis=(1, 2))) + 1e-9
    voz = 20 * np.log10(rms / (np.abs(x).max() + 1e-9)) > umbral_db
    if not voz.any():
        return x
    ini, fin = int(np.argmax(voz)), int(n - np.argmax(voz[::-1]))
    margen = 3  # 30 ms antes y después de la voz
    trozos, i = [], max(0, ini - margen)
    j = ini
    while j < fin:
        if voz[j]:
            j += 1
            continue
        k = j
        while k < fin and not voz[k]:
            k += 1
        if (k - j) * 0.01 > max_pausa:  # pausa larga: se queda en `deja` segundos
            corte = int(deja / 0.01 / 2)
            trozos.append(x[i * ventana:(j + corte) * ventana])
            i = k - corte
        j = k
    trozos.append(x[i * ventana:min(len(x), (fin + margen) * ventana)])
    return np.concatenate(trozos)


@dataclass
class Plano:
    id: str
    escena: str
    inicio: float
    dur: float
    clip: Path
    velocidad: float = 1.0
    zoom: float = 1.0       # > 1: acercamiento progresivo hasta ese factor al final del plano (gancho)


@dataclass
class Voz:
    plano: str
    tipo: str      # "voz" (diálogo) o "narr"
    texto: str
    inicio: float
    audio: np.ndarray = field(repr=False, default=None)


@dataclass
class Linea:
    planos: list
    voces: list
    total: float
    ritmo: str

    @property
    def inicio(self):
        return {p.id: p.inicio for p in self.planos}

    @property
    def fin(self):
        return {p.id: p.inicio + p.dur for p in self.planos}


def _q(segundos):
    return round(segundos * config.VID_FPS) / config.VID_FPS


def duracion_clip(ruta):
    import av
    with av.open(str(ruta)) as c:
        s = c.streams.video[0]
        fps = float(s.average_rate or config.VID_FPS)
        frames = s.frames or sum(1 for _ in c.decode(s))
    return frames / fps


def calcular(nombre, fuente="videos", con_audio=True, leer_audio=None, carpeta=None, base=None, proyecto=None):
    """Línea de tiempo del proyecto."""
    carpeta = carpeta or config.PROYECTOS / nombre
    base = base or config.SALIDAS / nombre
    proyecto = proyecto or config.cargar_proyecto(nombre)
    ritmo = proyecto.get("ritmo", "normal")
    sin_pausas = proyecto.get("voz_sin_pausas", ritmo == "narracion")
    gancho = proyecto.get("gancho", False)
    planos = json.loads((carpeta / "planos.json").read_text(encoding="utf-8"))
    eleccion = json.loads((carpeta / "montaje.json").read_text(encoding="utf-8"))

    usados = []
    for p in planos:
        toma = eleccion.get(p["id"])
        clip = base / fuente / f"{p['id']}_{toma}.mp4"
        if toma and clip.exists():
            usados.append((p, clip))

    def voces_de(p):
        res = []
        for tipo, suf, texto in (("voz", "", (p.get("dialogo") or {}).get("texto")), ("narr", "_narr", p.get("narracion"))):
            if not texto:
                continue
            wav = base / "voces" / "lineas" / f"{p['id']}{suf}.wav"
            if not wav.exists():
                print(f"aviso: falta la voz {wav.name}")
                continue
            audio = None
            if (con_audio or ritmo == "narracion") and leer_audio:
                audio = leer_audio(wav)
                if sin_pausas:
                    audio = comprimir_silencios(audio)
            res.append(Voz(p["id"], tipo, texto, 0.0, audio))
        return res

    lista_planos, lista_voces, t = [], [], 0.0
    if ritmo != "narracion":
        for p, clip in usados:
            dur = _q(p["duracion_s"])
            lista_planos.append(Plano(p["id"], p["escena"], t, dur, clip,
                                      zoom=GANCHO_ZOOM if gancho and not lista_planos else 1.0))
            for v in voces_de(p):
                v.inicio = t + VOZ_RETARDO
                lista_voces.append(v)
            t += dur
        return Linea(lista_planos, lista_voces, t, ritmo)

    if leer_audio is None:
        raise ValueError("El ritmo 'narracion' necesita leer las voces (leer_audio)")
    # ritmo narración: escena a escena, la voz fija la duración
    escenas = []
    for p, clip in usados:
        if not escenas or escenas[-1][0] != p["escena"]:
            escenas.append((p["escena"], []))
        escenas[-1][1].append((p, clip))
    for _, grupo in escenas:
        voces = [v for p, _ in grupo for v in voces_de(p)]
        largo_voz = sum(len(v.audio) / SR for v in voces) + HUECO_ENTRE_FRASES * max(0, len(voces) - 1)
        if not voces:  # escena sin voz: cada plano con su duración, como en el ritmo normal
            duraciones = [_q(p["duracion_s"]) for p, _ in grupo]
        else:
            total = max(ENTRADA_ESCENA + largo_voz + SALIDA_ESCENA, MIN_PLANO * len(grupo))
            pesos = [duracion_clip(clip) or 1.0 for _, clip in grupo]
            duraciones = [max(_q(MIN_PLANO), _q(total * w / sum(pesos))) for w in pesos[:-1]]
            duraciones.append(max(_q(MIN_PLANO), _q(total - sum(duraciones))))
        tv = t + ENTRADA_ESCENA
        for v in voces:
            v.inicio = tv
            tv += len(v.audio) / SR + HUECO_ENTRE_FRASES
            lista_voces.append(v)
        for (p, clip), dur in zip(grupo, duraciones):
            largo = duracion_clip(clip)
            velocidad = 1.0 if largo >= dur else max(VELOCIDAD_MIN, largo / dur)
            zoom = 1.0
            if gancho and not lista_planos:  # primer plano del vídeo: toda su acción, más rápida, y empuje
                velocidad, zoom = max(velocidad, min(GANCHO_VELOCIDAD_MAX, largo / dur)), GANCHO_ZOOM
            lista_planos.append(Plano(p["id"], p["escena"], t, dur, clip, velocidad, zoom))
            t += dur
    return Linea(lista_planos, lista_voces, t, ritmo)
