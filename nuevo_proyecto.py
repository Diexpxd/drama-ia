"""Crea un proyecto nuevo con plantillas listas para rellenar.

  python nuevo_proyecto.py episodio1 --genero "thriller policiaco en la Ciudad de México" [--idioma es] [--formato 16:9]

Crea proyectos\\<nombre>\\ con proyecto.json, guion.md y sonido.json de ejemplo. No sobrescribe nada:
si el proyecto ya existe, se detiene.
  --genero   texto libre: "documental de historia", "cuento infantil animado estilo Pixar", "terror rural"...
  --idioma   idioma de las voces: es, en, pt, fr, de, it, ja, ko, zh, ru (por defecto es)
  --formato  16:9 (YouTube) o 9:16 (Shorts/TikTok/Reels) (por defecto 16:9)
"""
import argparse
import json
import re

import config

ESTILO_EJEMPLO = ("Cinematic film still, photorealistic, 35mm lens, shallow depth of field, natural color grading, "
                  "dramatic lighting, highly detailed.")

GUION_EJEMPLO = """# {titulo}

<!--
CÓMO ESCRIBIR EL GUION (borra este bloque cuando quieras):
- Cada escena empieza con una línea "## Escena N: título". Di dónde y cuándo pasa (lugar, hora, clima).
- Cuenta lo que se VE, en orden, con frases sencillas.
- Diálogos: escribe la frase exacta. Ej.:  Ana dice: "No pienso volver."
- Narración: Narración: "Aquel invierno lo cambió todo."
- Solo se usan las frases que estén aquí literalmente: el sistema no inventa diálogos.
- Consejo: los diálogos salen mejor cuando se ve de cerca la cara de quien habla.
-->

## Escena 1: (lugar, momento del día)

(Qué pasa. Quién aparece. Qué se dice.)

## Escena 2: (lugar, momento del día)

(...)
"""


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("nombre", help="nombre de la carpeta, sin espacios (p. ej. episodio1)")
    ap.add_argument("--genero", required=True)
    ap.add_argument("--idioma", default=config.IDIOMA_POR_DEFECTO, choices=list(config.IDIOMAS))
    ap.add_argument("--formato", default=config.FORMATO_POR_DEFECTO, choices=list(config.FORMATOS))
    ap.add_argument("--titulo", default=None)
    ap.add_argument("--realismo", action="store_true", help="cero fantasía y cero anacronismos (documentales)")
    a = ap.parse_args()
    crear(a.nombre, a.genero, a.idioma, a.formato, a.titulo, a.realismo)


def crear(nombre, genero, idioma=config.IDIOMA_POR_DEFECTO, formato=config.FORMATO_POR_DEFECTO, titulo=None,
          realismo=False, avisar=True):
    """También lo usa la interfaz visual. Devuelve la carpeta creada."""
    if not re.fullmatch(r"[A-Za-z0-9_\-]+", nombre):
        raise SystemExit("El nombre solo puede tener letras sin tilde, números, - y _ (sin espacios)")
    carpeta = config.PROYECTOS / nombre
    if carpeta.exists():
        raise SystemExit(f"Ya existe {carpeta}: no se toca nada")
    acento = {"es": "neutral Latin American Spanish accent"}.get(idioma, f"native {config.IDIOMAS[idioma]} accent")
    proyecto = {
        "titulo": titulo or nombre,
        "genero": genero,
        "idioma": idioma,
        "formato": formato,
        "ritmo": "narracion" if formato == "9:16" else "normal",
        "subtitulos": formato == "9:16",
        "realismo": bool(realismo),
        "estilo": ESTILO_EJEMPLO,
        "personajes": [
            {
                "nombre": "Personaje 1",
                "descripcion": "(EN INGLÉS) age, face, hair, clothing, very concrete: e.g. a woman in her thirties, "
                               "short curly black hair, brown eyes, wearing a worn green military jacket",
                "voz": f"(EN INGLÉS) e.g. a woman in her thirties with a warm, slightly husky voice, calm and determined, {acento}",
            }
        ],
        "narrador": {
            "nombre": "Narrador",
            "voz": f"A mature man with a deep, warm narrator voice, slow and clear pacing, {acento}",
        },
    }
    sonido = {
        "_ayuda": "musica/efectos se generan con 'python audio.py generar <proyecto>'. Colocación: 'desde'/'hasta' = planos "
                  "que cubre (ambiente o música, en bucle si hace falta); 'plano' + 'en' = efecto puntual a X segundos del "
                  "inicio del plano. 'db' = volumen. 'elegida' = variante a usar (v1 si no se indica). Rellena los planos "
                  "(P001...) después de ejecutar guion_a_planos.py.",
        "musica": [{"id": "tema", "etiquetas": "(EN INGLÉS) genre, instruments, mood, tempo. e.g. tense cinematic score, "
                                               "low strings, soft piano, slow build, no vocals",
                    "segundos": 60, "bpm": 80, "tonalidad": "D minor", "desde": "P001", "hasta": "P010", "db": -16}],
        "efectos": [{"id": "ambiente", "prompt": "(EN INGLÉS) background ambience of the place, e.g. city street at night, "
                                                 "distant traffic, light rain",
                     "segundos": 30, "desde": "P001", "hasta": "P010", "db": -12}],
    }
    carpeta.mkdir(parents=True)
    (carpeta / "personajes").mkdir()
    (carpeta / "proyecto.json").write_text(json.dumps(proyecto, ensure_ascii=False, indent=2), encoding="utf-8")
    (carpeta / "guion.md").write_text(GUION_EJEMPLO.replace("{titulo}", titulo or nombre), encoding="utf-8")
    (carpeta / "sonido.json").write_text(json.dumps(sonido, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"Proyecto creado: {carpeta}")
    if avisar:
        print("Siguiente: edita proyecto.json (estilo y personajes) y escribe guion.md")
    return carpeta


if __name__ == "__main__":
    main()
