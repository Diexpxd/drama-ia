"""Tarjetas de datos animadas ("rótulos"): fechas, cifras y títulos dibujados por el programa sobre el vídeo."""
import re
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

FUENTE_GRANDE = [r"C:\Windows\Fonts\impact.ttf", r"C:\Windows\Fonts\ariblk.ttf", r"C:\Windows\Fonts\arialbd.ttf"]
FUENTE_SUB = [r"C:\Windows\Fonts\arialbd.ttf", r"C:\Windows\Fonts\arial.ttf"]
AMARILLO, BLANCO, NEGRO = (255, 210, 40), (255, 255, 255), (0, 0, 0)
ENTRADA, CUENTA, SALIDA = 0.25, 0.9, 0.25  # segundos de animación
MARGEN_FIN = 0.4  # el rótulo sigue en pantalla un poco después de que acaba la frase
_cache = {}


def fuente(lista, tam):
    clave = (tuple(lista), tam)
    if clave not in _cache:
        ruta = next((f for f in lista if Path(f).exists()), None)
        _cache[clave] = ImageFont.truetype(ruta, tam) if ruta else ImageFont.load_default()
    return _cache[clave]


def ventanas(L, planos, sr=48000):
    """[(inicio, fin, rotulo)] en segundos del vídeo, a partir de la voz (narración) de cada plano con rótulo."""
    inicio_plano = {q.id: q for q in L.planos}
    res = []
    for p in planos:
        r = p.get("rotulo")
        if not r or not r.get("texto"):
            continue
        voz = next((v for v in L.voces if v.plano == p["id"] and v.tipo == "narr"), None)
        if voz is not None and voz.audio is not None:
            res.append((voz.inicio, voz.inicio + len(voz.audio) / sr + MARGEN_FIN, r))
        elif p["id"] in inicio_plano:
            q = inicio_plano[p["id"]]
            res.append((q.inicio, q.inicio + q.dur, r))
    return sorted(res, key=lambda x: x[0])


def _cuenta(texto, avance):
    m = re.match(r"^(\D*?)(\d[\d,]*(?:\.\d+)?)(.*)$", texto)
    if not m or avance >= 1:
        return texto
    pre, num, suf = m.groups()
    dec = len(num.split(".")[1]) if "." in num else 0
    valor = float(num.replace(",", "")) * avance
    cuerpo = f"{valor:,.{dec}f}" if "," in num else f"{valor:.{dec}f}"
    return pre + cuerpo + suf


def _suave(x):
    x = max(0.0, min(1.0, x))
    return 1 - (1 - x) ** 3


def dibujar(img, rotulo, t, dur, vertical=True):
    """Devuelve la imagen con el rótulo en el instante t (segundos desde que aparece) de una ventana de `dur` s."""
    if t < 0 or t > dur:
        return img
    w, h = img.size
    entrada = _suave(t / ENTRADA)
    salida = _suave((dur - t) / SALIDA)
    alfa = min(entrada, salida)
    if alfa <= 0.01:
        return img
    texto = rotulo["texto"]
    if rotulo.get("tipo") == "cifra":
        texto = _cuenta(texto, _suave(t / CUENTA))
    sub = (rotulo.get("sub") or "").upper()

    capa = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    d = ImageDraw.Draw(capa)
    # franja oscura degradada detrás del texto (legible sobre cualquier imagen)
    y_centro = int(h * (0.22 if vertical else 0.2))
    alto_franja = int(h * (0.2 if vertical else 0.28))
    for k in range(alto_franja):
        dist = abs(k - alto_franja / 2) / (alto_franja / 2)
        a = int(150 * (1 - dist ** 2) * alfa)
        d.line([(0, y_centro - alto_franja // 2 + k), (w, y_centro - alto_franja // 2 + k)], fill=(0, 0, 0, a))

    tam = int(w * (0.15 if vertical else 0.085))
    f_grande = fuente(FUENTE_GRANDE, tam)
    while d.textlength(rotulo["texto"], font=f_grande) > w * 0.88 and tam > 12:  # que quepa el valor FINAL
        tam = int(tam * 0.92)
        f_grande = fuente(FUENTE_GRANDE, tam)
    borde = max(2, tam // 14)
    desliz = int((1 - entrada) * h * 0.03)  # entra subiendo un poco
    ancho_txt = d.textlength(texto, font=f_grande)
    y_txt = y_centro - tam // 2 - (int(tam * 0.25) if sub else 0) + desliz
    color = AMARILLO if rotulo.get("tipo") in ("cifra", "fecha") else BLANCO
    d.text(((w - ancho_txt) / 2, y_txt), texto, font=f_grande, fill=color + (int(255 * alfa),),
           stroke_width=borde, stroke_fill=NEGRO + (int(255 * alfa),))
    # línea de acento que crece
    largo = int(w * 0.28 * _suave(t / (ENTRADA + 0.2)))
    abajo = d.textbbox(((w - ancho_txt) / 2, y_txt), rotulo["texto"], font=f_grande, stroke_width=borde)[3]
    y_linea = abajo + max(4, tam // 10)  # debajo de las letras (medido), no encima
    d.rectangle([(w - largo) // 2, y_linea, (w + largo) // 2, y_linea + max(2, tam // 22)],
                fill=AMARILLO + (int(230 * alfa),))
    if sub:
        tam_s = int(w * (0.04 if vertical else 0.024))
        f_sub = fuente(FUENTE_SUB, tam_s)
        while d.textlength(sub, font=f_sub) > w * 0.9 and tam_s > 10:
            tam_s = int(tam_s * 0.92)
            f_sub = fuente(FUENTE_SUB, tam_s)
        ancho_s = d.textlength(sub, font=f_sub)
        d.text(((w - ancho_s) / 2, y_linea + int(tam * 0.18)), sub, font=f_sub, fill=BLANCO + (int(235 * alfa),),
               stroke_width=max(1, tam_s // 12), stroke_fill=NEGRO + (int(200 * alfa),))
    base = img.convert("RGBA")
    base.alpha_composite(capa)
    return base.convert("RGB")
