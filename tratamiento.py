"""Tratamiento de color unificado (opción "grade" de proyecto.json, la aplica montar.py a TODOS los fotogramas)."""
import numpy as np

POR_DEFECTO = {"saturacion": 0.88, "contraste": 1.08, "negros": 0.02, "frio": 0.018, "calido": 0.016, "grano": 1.2}
_rng = np.random.default_rng(11)


def parametros(grade):
    if not grade:
        return None
    return {**POR_DEFECTO, **(grade if isinstance(grade, dict) else {})}


def aplicar(rgb, p):
    """rgb: np.uint8 (alto, ancho, 3) -> np.uint8 con el tratamiento p (de parametros())."""
    a = rgb.astype(np.float32) / 255.0
    luma = (a[..., 0] * 0.2126 + a[..., 1] * 0.7152 + a[..., 2] * 0.0722)[..., None]
    a = luma + (a - luma) * p["saturacion"]
    a = (a - 0.45) * p["contraste"] + 0.45                      # contraste alrededor de un gris medio
    a = p["negros"] + a * (1 - p["negros"])                      # negros levantados (aspecto de película)
    sombras = np.clip(1 - luma * 2.2, 0, 1)                      # tinte frío en sombras
    luces = np.clip(luma * 2.0 - 1.0, 0, 1)                      # tinte cálido en luces
    a[..., 0] += -p["frio"] * 0.4 * sombras[..., 0] + p["calido"] * luces[..., 0]
    a[..., 1] += p["frio"] * 0.15 * sombras[..., 0] + p["calido"] * 0.45 * luces[..., 0]
    a[..., 2] += p["frio"] * sombras[..., 0] - p["calido"] * 0.5 * luces[..., 0]
    if p["grano"]:
        a += _rng.normal(0, p["grano"] / 255.0, a.shape[:2] + (1,)).astype(np.float32)
    return (np.clip(a, 0, 1) * 255.0 + 0.5).astype(np.uint8)
