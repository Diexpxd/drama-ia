r"""Planos hechos por PROGRAMA (sin GPU) para los vídeos largos: fotos con movimiento de cámara y gráficos de datos.
Escriben un MP4 sin audio de la misma forma que un clip de Wan (los usa el orquestador y montar.py sin cambios).

  python visuales.py foto  <imagen.png> <salida.mp4> [--seg 8] [--mov empuje|alejar|izq|der|diag]
  python visuales.py grafico <grafico.json> <salida.mp4> [--seg 7]

grafico.json = el campo "grafico" de un plano: {"tipo": "linea|barras|cifra|linea_de_tiempo", "titulo", "unidad",
"datos": [{"etiqueta", "valor"}], ...}. Solo se dibujan los valores que trae (ya verificados por el guionista).
"""
import argparse
import json
import math
from fractions import Fraction
from pathlib import Path

import av
import numpy as np
from PIL import Image, ImageDraw, ImageFilter, ImageFont

W, H, FPS = 1920, 1080, 32
FUENTE_NEGRITA = r"C:/Windows/Fonts/segoeuib.ttf"
FUENTE = r"C:/Windows/Fonts/segoeui.ttf"
FUENTE_TITULAR = r"C:/Windows/Fonts/impact.ttf"
ROJO = (224, 32, 42)
BLANCO = (240, 240, 240)
GRIS = (140, 146, 158)
FONDO_ARRIBA, FONDO_ABAJO = (10, 12, 18), (22, 25, 34)
MOVIMIENTOS = ["empuje", "alejar", "izq", "der", "diag"]


def _escribir(frames, salida, fps=FPS):
    salida = Path(salida)
    salida.parent.mkdir(parents=True, exist_ok=True)
    with av.open(str(salida), "w", options={"movflags": "+faststart"}) as sal:
        v = sal.add_stream("libx264", rate=fps)
        v.width, v.height, v.pix_fmt = W, H, "yuv420p"
        v.options = {"crf": "17", "preset": "medium"}
        n = 0
        for f in frames:
            fr = av.VideoFrame.from_ndarray(f, format="rgb24")
            fr.pts, fr.time_base = n, Fraction(1, fps)
            n += 1
            for p in v.encode(fr):
                sal.mux(p)
        for p in v.encode(None):
            sal.mux(p)
    return salida


def _suave(t):
    t = min(1.0, max(0.0, t))
    return t * t * (3 - 2 * t)


def _fuente(ruta, tam):
    try:
        return ImageFont.truetype(ruta, tam)
    except OSError:
        return ImageFont.load_default()


def foto(imagen, salida, seg=8.0, mov=None, indice=0, zoom=1.14):
    """Movimiento de cámara sobre una imagen fija (recorte que se mueve y se escala, con Lanczos subpíxel)."""
    mov = mov or MOVIMIENTOS[indice % len(MOVIMIENTOS)]
    im = Image.open(imagen).convert("RGB")
    ancho_img, alto_img = im.size
    # la imagen llena el encuadre 16:9 (recorta lo que sobre arriba y abajo)
    ratio = W / H
    if ancho_img / alto_img > ratio:
        nuevo = int(alto_img * ratio)
        im = im.crop(((ancho_img - nuevo) // 2, 0, (ancho_img - nuevo) // 2 + nuevo, alto_img))
    else:
        nuevo = int(ancho_img / ratio)
        im = im.crop((0, (alto_img - nuevo) // 2, ancho_img, (alto_img - nuevo) // 2 + nuevo))
    iw, ih = im.size
    n = max(2, int(round(seg * FPS)))
    z0, z1 = 1.0, 1.0 / zoom
    posiciones = {
        "empuje": ((0.5, 0.5, z0), (0.5, 0.5, z1)),
        "alejar": ((0.5, 0.5, z1), (0.5, 0.5, z0)),
        "izq":    ((0.5 + 0.5 * (1 - 1 / zoom) * 0.9, 0.5, z1), (0.5 - 0.5 * (1 - 1 / zoom) * 0.9, 0.5, z1)),
        "der":    ((0.5 - 0.5 * (1 - 1 / zoom) * 0.9, 0.5, z1), (0.5 + 0.5 * (1 - 1 / zoom) * 0.9, 0.5, z1)),
        "diag":   ((0.5 - 0.5 * (1 - 1 / zoom) * 0.8, 0.5 + 0.5 * (1 - 1 / zoom) * 0.8, z1),
                   (0.5 + 0.5 * (1 - 1 / zoom) * 0.8, 0.5 - 0.5 * (1 - 1 / zoom) * 0.8, z1 * 0.97)),
    }
    (ax, ay, az), (bx, by, bz) = posiciones[mov]
    # viñeta y grano suaves, iguales en todos los planos (da unidad visual)
    xx = np.linspace(-1, 1, W)[None, :]
    yy = np.linspace(-1, 1, H)[:, None]
    vineta = np.clip(1.0 - 0.28 * (xx ** 2 + yy ** 2) ** 1.2, 0.55, 1.0).astype(np.float32)[..., None]
    rng = np.random.default_rng(7)

    def cuadros():
        for k in range(n):
            t = _suave(k / (n - 1)) * 0.6 + (k / (n - 1)) * 0.4  # entra suave pero no se frena del todo
            cx, cy, z = ax + (bx - ax) * t, ay + (by - ay) * t, az + (bz - az) * t
            vw, vh = iw * z, ih * z
            x0 = min(max(cx * iw - vw / 2, 0), iw - vw)
            y0 = min(max(cy * ih - vh / 2, 0), ih - vh)
            fr = im.resize((W, H), Image.LANCZOS, box=(x0, y0, x0 + vw, y0 + vh))
            a = np.asarray(fr, dtype=np.float32) * vineta
            a += rng.normal(0, 1.3, (H, W, 1)).astype(np.float32)  # grano
            yield np.clip(a, 0, 255).astype(np.uint8)
    return _escribir(cuadros(), salida)


def _fondo():
    arriba, abajo = np.array(FONDO_ARRIBA, np.float32), np.array(FONDO_ABAJO, np.float32)
    t = np.linspace(0, 1, H, dtype=np.float32)[:, None, None]
    a = (arriba * (1 - t) + abajo * t) * np.ones((1, W, 1), np.float32)
    return Image.fromarray(a.astype(np.uint8))


def _num(v, unidad=""):
    s = f"{int(round(v)):,}" if abs(v - round(v)) < 1e-9 else f"{v:,.1f}"
    u = unidad.strip()
    dinero = u.startswith("$") or u.upper().startswith("USD")
    if dinero:
        u = u[1:] if u.startswith("$") else u[3:]
        s = "$" + s
    u = u.strip()
    if u.lower() in ("millions", "billions", "thousands"):
        u = u[:-1]  # "$1,020 million" (singular, como se dice)
    return f"{s} {u}".strip()


def _titulo(d, titulo, unidad):
    d.rectangle((90, 86, 100, 150), fill=ROJO)
    d.text((124, 74), titulo.upper(), font=_fuente(FUENTE_NEGRITA, 60), fill=BLANCO)
    if unidad:
        d.text((126, 150), unidad, font=_fuente(FUENTE, 36), fill=GRIS)


def _marca(d):
    d.text((W - 90, H - 70), "CORPORATE AUTOPSY", font=_fuente(FUENTE_NEGRITA, 26), fill=(95, 100, 112), anchor="ra")


def grafico(spec, salida, seg=7.0):
    tipo = spec.get("tipo", "cifra")
    datos = [x for x in spec.get("datos", []) if x.get("valor") is not None]
    if not datos:
        raise ValueError("el gráfico no tiene datos")
    n = max(2, int(round(seg * FPS)))
    fondo = _fondo()
    titulo, unidad = spec.get("titulo", ""), spec.get("unidad", "")

    def cuadro(t):  # t = 0..1 de la animación (la última parte queda quieta)
        im = fondo.copy()
        d = ImageDraw.Draw(im)
        _titulo(d, titulo, unidad if tipo != "cifra" else "")
        _marca(d)
        prog = _suave(min(1.0, t / 0.7))  # la animación ocupa el 70 %; el resto se mantiene
        if tipo == "cifra":
            _dibujar_cifra(d, datos, prog, unidad)
        elif tipo == "barras":
            _dibujar_barras(d, datos, prog, unidad)
        elif tipo == "linea":
            _dibujar_linea(d, datos, prog, unidad)
        else:
            _dibujar_linea_de_tiempo(d, datos, prog)
        return np.asarray(im, dtype=np.uint8)
    return _escribir((cuadro(k / (n - 1)) for k in range(n)), salida)


def _dibujar_cifra(d, datos, p, unidad):
    d0 = datos[0]
    v = d0["valor"] * p
    texto = _num(v, unidad)
    d.text((W // 2, H // 2 - 20), texto, font=_fuente(FUENTE_TITULAR, 230), fill=BLANCO, anchor="mm")
    d.rectangle((W // 2 - 140, H // 2 + 120, W // 2 + 140, H // 2 + 126), fill=ROJO)
    d.text((W // 2, H // 2 + 190), d0.get("etiqueta", ""), font=_fuente(FUENTE_NEGRITA, 54), fill=GRIS, anchor="mm")


def _dibujar_barras(d, datos, p, unidad):
    mx = max(x["valor"] for x in datos) or 1
    area_x0, area_x1, base_y, alto_max = 220, W - 160, H - 190, 560
    n = len(datos)
    paso = (area_x1 - area_x0) / n
    ancho = min(210, paso * 0.62)
    d.line((area_x0 - 30, base_y, area_x1 + 20, base_y), fill=(70, 75, 88), width=3)
    for i, x in enumerate(datos):
        ini = i / max(n, 1) * 0.55
        k = _suave((p - ini) / 0.45)
        h = alto_max * (x["valor"] / mx) * k
        cx = area_x0 + paso * (i + 0.5)
        color = ROJO if x["valor"] == mx else (200, 205, 215)
        d.rectangle((cx - ancho / 2, base_y - h, cx + ancho / 2, base_y), fill=color)
        d.text((cx, base_y - h - 18), _num(x["valor"] * k, unidad), font=_fuente(FUENTE_NEGRITA, 44), fill=BLANCO, anchor="ms")
        d.text((cx, base_y + 24), str(x["etiqueta"]), font=_fuente(FUENTE, 40), fill=GRIS, anchor="ma")


def _dibujar_linea(d, datos, p, unidad):
    x0, x1, y0, y1 = 230, W - 200, 280, H - 220
    vals = [x["valor"] for x in datos]
    vmin, vmax = min(0, min(vals)), max(vals) * 1.08 or 1
    n = len(datos)
    puntos = [(x0 + (x1 - x0) * i / max(n - 1, 1), y1 - (y1 - y0) * (v - vmin) / (vmax - vmin)) for i, v in enumerate(vals)]
    for g in range(5):  # rejilla
        y = y1 - (y1 - y0) * g / 4
        d.line((x0, y, x1, y), fill=(44, 48, 60), width=2)
        d.text((x0 - 18, y), _num(vmin + (vmax - vmin) * g / 4, ""), font=_fuente(FUENTE, 30), fill=(110, 116, 128), anchor="rm")
    hasta = p * (n - 1)
    trazo = [puntos[0]]
    for i in range(1, n):
        if hasta >= i:
            trazo.append(puntos[i])
        elif hasta > i - 1:
            f = hasta - (i - 1)
            trazo.append((puntos[i - 1][0] + (puntos[i][0] - puntos[i - 1][0]) * f, puntos[i - 1][1] + (puntos[i][1] - puntos[i - 1][1]) * f))
    if len(trazo) > 1:
        d.line(trazo, fill=ROJO, width=9, joint="curve")
    for i, (px, py) in enumerate(puntos):
        if hasta >= i - 1e-9:
            d.ellipse((px - 13, py - 13, px + 13, py + 13), fill=BLANCO, outline=ROJO, width=5)
            d.text((px, y1 + 26), str(datos[i]["etiqueta"]), font=_fuente(FUENTE, 36), fill=GRIS, anchor="ma")
            if i in (0, n - 1) or n <= 6:
                d.text((px, py - 30), _num(vals[i], unidad), font=_fuente(FUENTE_NEGRITA, 42), fill=BLANCO, anchor="ms")


def _dibujar_linea_de_tiempo(d, datos, p):
    n = len(datos)
    x0, x1, y = 180, W - 180, H // 2 + 20
    d.line((x0, y, x1, y), fill=(70, 75, 88), width=6)
    llegada = x0 + (x1 - x0) * p
    d.line((x0, y, llegada, y), fill=ROJO, width=6)
    for i, x in enumerate(datos):
        px = x0 + (x1 - x0) * (i / max(n - 1, 1))
        if px <= llegada + 1:
            d.ellipse((px - 16, y - 16, px + 16, y + 16), fill=BLANCO, outline=ROJO, width=6)
            arriba = i % 2 == 0
            d.text((px, y - 50 if arriba else y + 50), f"{int(x['valor'])}", font=_fuente(FUENTE_TITULAR, 76), fill=BLANCO,
                   anchor="ms" if arriba else "mt")
            fe = _fuente(FUENTE, 36)
            etiqueta = str(x["etiqueta"])
            mitad = d.textlength(etiqueta, font=fe) / 2
            ex = min(max(px, 90 + mitad), W - 90 - mitad)  # que el texto no se salga de la pantalla
            d.text((ex, y - 140 if arriba else y + 140), etiqueta, font=fe, fill=GRIS, anchor="ms" if arriba else "mt")


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    f = sub.add_parser("foto")
    f.add_argument("imagen")
    f.add_argument("salida")
    f.add_argument("--seg", type=float, default=8.0)
    f.add_argument("--mov", choices=MOVIMIENTOS)
    g_ = sub.add_parser("grafico")
    g_.add_argument("json")
    g_.add_argument("salida")
    g_.add_argument("--seg", type=float, default=7.0)
    a = ap.parse_args()
    if a.cmd == "foto":
        print(foto(a.imagen, a.salida, a.seg, a.mov))
    else:
        print(grafico(json.loads(Path(a.json).read_text(encoding="utf-8")), a.salida, a.seg))


if __name__ == "__main__":
    main()
