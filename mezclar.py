"""Mezcla el sonido del corte y lo une al vídeo de montar.py."""
import argparse
import json
import os
from pathlib import Path

import av
import numpy as np

import config
from montar import sufijo

SR = 48000
VOZ_RETARDO = 0.3
DUCK_DB = -9.0  # cuánto baja la música bajo la voz
DUCK_DB_NARRACION = -14.0  # ídem en ritmo "narracion" (Gemini: el drone competía con el narrador)
RESPIRO_DB = -14.0
ACENTO_CAIDA_S, ACENTO_CAIDA_DB = 0.6, -20.0      # acentos: la música casi desaparece antes de la frase clave...
ACENTO_SUBIDA_S, ACENTO_SUBIDA_DB = 3.0, 4.0      # ...y vuelve 4 dB más fuerte, relajándose en 3 s
SUBIDA_MAX_DB = 24.0
GRAVES_HZ = 160
PAUSA_S = 0.7


def leer_audio(ruta: Path) -> np.ndarray:
    """Cualquier formato -> float32 estéreo (n, 2) a 48 kHz."""
    with av.open(str(ruta)) as c:
        rs = av.AudioResampler(format="fltp", layout="stereo", rate=SR)
        partes = []
        for frame in c.decode(audio=0):
            for f in rs.resample(frame):
                partes.append(f.to_ndarray())
        for f in rs.resample(None):
            partes.append(f.to_ndarray())
    return np.concatenate(partes, axis=1).T.astype(np.float32)


def db(x):
    return 10 ** (x / 20)


def fundido(a, seg_in=0.0, seg_out=0.0):
    a = a.copy()
    n_in, n_out = int(seg_in * SR), int(seg_out * SR)
    if n_in:
        a[:n_in] *= np.linspace(0, 1, min(n_in, len(a)))[:, None]
    if n_out:
        n = min(n_out, len(a))
        a[-n:] *= np.linspace(1, 0, n)[:, None]
    return a


def en_bucle(a, n, cruce=1.0):
    """Repite `a` con fundido cruzado hasta tener n muestras."""
    c = min(int(cruce * SR), len(a) // 3)
    out = a[: min(len(a), n)].copy()
    while len(out) < n:
        sig = a.copy()
        sig[:c] *= np.linspace(0, 1, c)[:, None]
        out[-c:] *= np.linspace(1, 0, c)[:, None]
        out[-c:] += sig[:c]
        out = np.concatenate([out, sig[c:]])
    return out[:n]


def sin_silencio_inicial(clip, umbral=0.02, margen=0.02):
    """Los efectos generados no empiezan en el segundo 0: recorta hasta el ataque para que 'en' sea exacto."""
    activo = np.abs(clip).max(axis=1) > umbral
    if not activo.any():
        return clip
    return clip[max(0, activo.argmax() - int(margen * SR)):]


def normalizar(mezcla, rms_objetivo=-18.0, techo=-1.0):
    """Sube/baja todo a un RMS objetivo (YouTube baja lo que suena fuerte pero no sube lo flojo)"""
    rms = np.sqrt((mezcla ** 2).mean()) + 1e-9
    x = mezcla * (db(rms_objetivo) / rms)
    t, lim = db(techo - 6), db(techo)  # por encima de t se comprime suavemente hasta el techo
    a = np.abs(x)
    sobre = a > t
    x[sobre] = np.sign(x[sobre]) * (t + (lim - t) * np.tanh((a[sobre] - t) / (lim - t)))
    return x


def pegar(pista, clip, inicio):
    i = int(inicio * SR)
    if i >= len(pista):
        return
    fin = min(len(pista), i + len(clip))
    pista[i:fin] += clip[: fin - i]


def variante(base, e):
    return base / "sonido" / f"{e['id']}_{e.get('elegida', 'v1')}.flac"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("proyecto")
    ap.add_argument("--fuente", default="videos", help="la misma que en montar.py")
    a = ap.parse_args()
    salida = mezclar(a.proyecto, a.fuente)
    print(salida)
    config.abrir(salida)


def mezclar(nombre, fuente="videos", carpeta=None, base=None, proyecto=None, dir_salida=None):
    """carpeta/base/proyecto/dir_salida: solo para pruebas (por defecto, las del proyecto)."""
    import linea_tiempo
    base = base or config.SALIDAS / nombre
    carpeta = carpeta or config.PROYECTOS / nombre
    plan = json.loads((carpeta / "sonido.json").read_text(encoding="utf-8")) if (carpeta / "sonido.json").exists() else {}
    video = (dir_salida or base / "montaje") / f"{nombre}_corte{sufijo(fuente)}.mp4"

    L = linea_tiempo.calcular(nombre, fuente, True, leer_audio, carpeta=carpeta, base=base, proyecto=proyecto)
    inicio, fin, total = L.inicio, L.fin, L.total
    dur_plano = {q.id: q.dur for q in L.planos}
    n = int(total * SR)

    voces = np.zeros((n, 2), np.float32)
    musica = np.zeros((n, 2), np.float32)
    ambiente = np.zeros((n, 2), np.float32)
    efectos = np.zeros((n, 2), np.float32)

    subtitulos = []  # texto y tiempos de cada frase: lo usa subtitulos.py
    for v in L.voces:
        audio = v.audio * (db(-3) / (np.abs(v.audio).max() + 1e-9))  # mismo pico para todas, venga del motor que venga
        pegar(voces, audio, v.inicio)
        subtitulos.append({"plano": v.plano, "tipo": v.tipo, "texto": v.texto, "inicio": round(v.inicio, 3),
                           "fin": round(v.inicio + len(audio) / SR, 3)})

    import rotulos
    planos_json = json.loads((carpeta / "planos.json").read_text(encoding="utf-8"))
    carteles = rotulos.ventanas(L, planos_json)
    golpe_dato = next((e for e in plan.get("efectos", []) if e.get("rotulos")), None)
    piso = None
    if plan.get("piso_efectos_db") is not None:
        con_voz = voces[np.abs(voces).max(axis=1) > 0.02]
        if len(con_voz):
            piso = np.sqrt(np.mean(con_voz ** 2)) * db(plan["piso_efectos_db"])

    for grupo, destino in (("musica", musica), ("efectos", None)):
        for e in plan.get(grupo, []):
            if e.get("rotulos"):  # el golpe de las tarjetas se coloca abajo, en cada tarjeta
                continue
            ruta = variante(base, e)
            if not ruta.exists():
                print(f"aviso: falta {ruta.name}")
                continue
            clip = leer_audio(ruta) * db(e["db"])
            if "desde" in e:
                if e["desde"] not in inicio:
                    continue
                t0 = inicio[e["desde"]]
                t1 = fin.get(e["hasta"], total)
                tramo = fundido(en_bucle(clip, int((t1 - t0) * SR)), 0.5, 1.5)
                pegar(destino if destino is not None else (musica if e.get("duck") else ambiente), tramo, t0)
            elif e["plano"] in inicio:  # con ritmo narración el plano puede ser más corto: el efecto no se sale
                en = max(0.0, min(e["en"], dur_plano[e["plano"]] - 0.2))
                clip = sin_silencio_inicial(clip)
                ataque = np.sqrt(np.mean(clip[:int(0.6 * SR)] ** 2))
                if piso is not None and 0 < ataque < piso:
                    clip = clip * min(db(SUBIDA_MAX_DB), piso / ataque)
                pegar(efectos, fundido(clip, 0.0, 0.3), inicio[e["plano"]] + en)

    # Ducking: envolvente de la voz suavizada -> baja la música
    env = np.abs(voces).max(axis=1)
    ventana = int(0.25 * SR)
    acum = np.concatenate([[0.0], np.cumsum((env > 0.02).astype(np.float64))])
    i = np.arange(len(env))
    lo, hi = np.clip(i - ventana // 2, 0, len(env)), np.clip(i + ventana // 2, 0, len(env))
    env = (acum[hi] - acum[lo]) / ventana
    duck = DUCK_DB_NARRACION if (proyecto or config.cargar_proyecto(nombre)).get("ritmo") == "narracion" else DUCK_DB
    ganancia = 1 - (1 - db(duck)) * np.clip(env * 3, 0, 1)
    respiro = np.ones(n, np.float32)
    for a, _, r in carteles:
        if r.get("tipo") == "titulo":
            continue
        i0, i1, i2 = int((a - 0.45) * SR), int(a * SR), int((a + 0.35) * SR)
        i0, i1, i2 = max(0, i0), max(0, min(n, i1)), max(0, min(n, i2))
        if i1 > i0:
            respiro[i0:i1] = np.minimum(respiro[i0:i1], np.linspace(1, db(RESPIRO_DB), i1 - i0))
        if i2 > i1:
            respiro[i1:i2] = np.minimum(respiro[i1:i2], np.linspace(db(RESPIRO_DB), 1, i2 - i1))
    if golpe_dato and variante(base, golpe_dato).exists():
        clip = fundido(sin_silencio_inicial(leer_audio(variante(base, golpe_dato)) * db(golpe_dato["db"])), 0.0, 0.3)
        for a, _, r in carteles:
            pegar(efectos, clip, max(0.0, a))
    elif carteles:
        print("aviso: falta el golpe de las tarjetas de datos (efecto con \"rotulos\": true en sonido.json)")
    for pid in plan.get("acentos", []):
        voz = next((v for v in L.voces if v.plano == pid), None)
        if voz is None:
            print(f"aviso: acento en {pid} sin voz; se ignora")
            continue
        a = voz.inicio
        i0, i1 = max(0, int((a - ACENTO_CAIDA_S) * SR)), max(0, min(n, int(a * SR)))
        i2 = max(0, min(n, int((a + ACENTO_SUBIDA_S) * SR)))
        if i1 > i0:
            respiro[i0:i1] = np.minimum(respiro[i0:i1], np.linspace(1, db(ACENTO_CAIDA_DB), i1 - i0))
        if i2 > i1:  # vuelve desde casi silencio hasta +ACENTO_SUBIDA_DB y se relaja
            curva = np.concatenate([np.linspace(db(ACENTO_CAIDA_DB), db(ACENTO_SUBIDA_DB), max(1, (i2 - i1) // 4)),
                                    np.linspace(db(ACENTO_SUBIDA_DB), 1, (i2 - i1) - max(1, (i2 - i1) // 4))])
            respiro[i1:i2] = curva[: i2 - i1]
    if plan.get("contraste_db"):
        v = int(PAUSA_S * SR)
        hablando = np.concatenate([[0.0], np.cumsum((np.abs(voces).max(axis=1) > 0.02).astype(np.float64))])
        lo2, hi2 = np.clip(i - v // 2, 0, len(env)), np.clip(i + v // 2, 0, len(env))
        pausa = 1 - np.clip(((hablando[hi2] - hablando[lo2]) / v) * 3, 0, 1)  # 1 = pausa clara, 0 = hay voz
        ganancia = ganancia * (1 + (db(plan["contraste_db"]) - 1) * pausa)
    mezcla = voces + (musica * ganancia[:, None] + ambiente) * respiro[:, None] + efectos
    if plan.get("graves_db"):
        from scipy.signal import butter, sosfilt
        sos = butter(4, GRAVES_HZ, btype="low", fs=SR, output="sos")
        mezcla = mezcla + sosfilt(sos, mezcla, axis=0).astype(np.float32) * (db(plan["graves_db"]) - 1)
    mezcla = normalizar(fundido(mezcla, 0.3, 2.0)).astype(np.float32)
    rms = 20 * np.log10(np.sqrt((mezcla ** 2).mean()) + 1e-9)
    print(f"duración {total:.1f} s, RMS {rms:.1f} dB, pico {20 * np.log10(np.abs(mezcla).max() + 1e-9):.1f} dBFS")

    dir_salida = dir_salida or base / "montaje"
    salida = dir_salida / f"{nombre}_con_sonido{sufijo(fuente)}.mp4"
    guardar_wav(mezcla, salida.with_suffix(".wav"))
    # pista solo de voces + texto/tiempos de cada frase, para los subtítulos
    guardar_wav(normalizar(voces).astype(np.float32), dir_salida / f"{nombre}_voces{sufijo(fuente)}.wav")
    (dir_salida / f"{nombre}_frases{sufijo(fuente)}.json").write_text(
        json.dumps({"ritmo": L.ritmo, "total": total, "frases": subtitulos}, ensure_ascii=False, indent=2), encoding="utf-8")
    unir(video, mezcla, salida)
    return salida


def guardar_wav(mezcla, ruta):
    with av.open(str(ruta), "w") as out:
        s = out.add_stream("pcm_s16le", rate=SR, layout="stereo")
        escribir_audio(out, s, mezcla, "s16")


def escribir_audio(out, stream, mezcla, formato):
    bloque = 1024
    resto = -len(mezcla) % bloque  # AAC quiere bloques completos de 1024 muestras
    mezcla = np.concatenate([mezcla, np.zeros((resto, 2), np.float32)]).astype(np.float32)
    for i in range(0, len(mezcla), bloque):
        trozo = np.ascontiguousarray(mezcla[i:i + bloque].T)
        if formato == "s16":
            frame = av.AudioFrame.from_ndarray((np.clip(trozo, -1, 1) * 32767).astype(np.int16).T.reshape(1, -1),
                                               format="s16", layout="stereo")
        else:
            frame = av.AudioFrame.from_ndarray(trozo, format="fltp", layout="stereo")
        frame.sample_rate = SR
        frame.pts = i
        out.mux(stream.encode(frame))
    out.mux(stream.encode(None))


def unir(video, mezcla, salida):
    """Copia el vídeo tal cual (sin recomprimir) y añade la mezcla en AAC."""
    with av.open(str(video)) as entrada, av.open(str(salida), "w") as out:
        vin = entrada.streams.video[0]
        vout = out.add_stream_from_template(vin)
        aout = out.add_stream("aac", rate=SR, layout="stereo")
        aout.bit_rate = 192000
        for paquete in entrada.demux(vin):
            if paquete.dts is None:
                continue
            paquete.stream = vout
            out.mux(paquete)
        escribir_audio(out, aout, mezcla, "fltp")


if __name__ == "__main__":
    main()
