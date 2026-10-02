"""Generación por lotes con ComfyUI. Encola todo de golpe (ComfyUI lo procesa en orden) y va
recogiendo los resultados. Se puede relanzar: lo que ya existe no se vuelve a generar.

  python generar.py personajes piloto [--variantes 4]
      Retratos de referencia de cada personaje -> salidas\\piloto\\personajes\\
      Copia el mejor a proyectos\\piloto\\personajes\\<Nombre>.png para usarlo como referencia.

  python generar.py imagenes piloto [--variantes 3] [--planos P001,P002]
      Fotograma inicial de cada plano -> salidas\\piloto\\imagenes\\P001_v1.png ...
      Copia el mejor de cada plano a salidas\\piloto\\elegidas\\P001.png

  python generar.py videos piloto [--tomas 2] [--planos ...] [--final]
      Anima cada imagen elegida -> salidas\\piloto\\videos\\P001_t1.mp4 ...
      --final genera a 1280x720 (más lento) en lugar de 832x480 (en vertical 9:16: 720x1280 / 480x832).
"""
import argparse
import json
import time
from datetime import datetime
from pathlib import Path

import comfy
import config

MAX_REFERENCIAS = 3


def cargar(nombre):
    carpeta = config.PROYECTOS / nombre
    proyecto = config.cargar_proyecto(nombre)
    ruta_planos = carpeta / "planos.json"
    planos = json.loads(ruta_planos.read_text(encoding="utf-8")) if ruta_planos.exists() else []
    return carpeta, proyecto, planos


def filtrar(planos, ids):
    if not ids:
        return planos
    ids = set(ids.split(","))
    return [p for p in planos if p["id"] in ids]


def lote(trabajos, registro: Path):
    """trabajos: lista de (flujo, destino, info). Encola todos y recoge en orden."""
    trabajos = [t for t in trabajos if not t[1].exists()]
    resultados = []  # [(destino, "ok" | "error: ...")] para quien necesite saber qué salió bien
    if not trabajos:
        print("Nada que generar: todo existe ya.")
        return resultados
    print(f"Encolando {len(trabajos)} trabajos en ComfyUI...", flush=True)
    pendientes = [(comfy.encolar(f), destino, info) for f, destino, info in trabajos]
    inicio = time.time()
    for i, (pid, destino, info) in enumerate(pendientes, 1):
        t = time.time()
        try:
            archivos = comfy.esperar(pid)
            destino.parent.mkdir(parents=True, exist_ok=True)
            archivos[0].replace(destino)
            estado = "ok"
        except Exception as e:  # un fallo no para el lote
            estado = f"error: {e}"
        restante = (time.time() - inicio) / i * (len(pendientes) - i)
        print(f"[{i}/{len(pendientes)}] {destino.name} {estado} ({time.time() - t:.0f} s, "
              f"quedan ~{restante / 60:.0f} min)", flush=True)
        with registro.open("a", encoding="utf-8") as r:
            r.write(json.dumps({"fecha": datetime.now().isoformat(timespec="seconds"), "archivo": str(destino),
                                "estado": estado, **info}, ensure_ascii=False) + "\n")
        resultados.append((destino, estado))
    return resultados


def cmd_personajes(a):
    carpeta, proyecto, _ = cargar(a.proyecto)
    salida = config.SALIDAS / a.proyecto / "personajes"
    trabajos = []
    for p in proyecto["personajes"]:
        prompt = (f"Character reference portrait, medium shot, facing the camera, neutral background, "
                  f"even soft lighting. {p['descripcion']}. {proyecto['estilo']}")
        for v in range(1, a.variantes + 1):
            seed = comfy.semilla()
            destino = salida / f"{p['nombre']}_v{v}.png"
            trabajos.append((comfy.flujo_imagen(prompt, f"drama-ia/{a.proyecto}/tmp", (), *config.dims(proyecto, "img"), seed=seed), destino,
                             {"tipo": "personaje", "prompt": prompt, "seed": seed}))
    lote(trabajos, config.SALIDAS / a.proyecto / "registro.jsonl")


def prompt_de_imagen(proyecto, plano, refs_disponibles):
    """Prompt final de la imagen de un plano y los personajes con referencia que lleva (máx."""
    con_ref = [n for n in plano["personajes"] if refs_disponibles.get(n, Path()).exists()][:MAX_REFERENCIAS]
    prompt = plano["prompt_imagen"]
    if con_ref:
        quien = ", ".join(f"image {i} is {n}" for i, n in enumerate(con_ref, 1))
        prompt = (f"Use the reference images only for the identity, face, hairstyle and costume of the "
                  f"characters ({quien}); create a completely new scene: {prompt}")
    if plano.get("ambientacion"):
        prompt = f"{prompt} Setting: {plano['ambientacion']}."
    realismo = config.IMG_REALISMO if proyecto.get("realismo") else ""
    return f"{prompt} {proyecto['estilo']}{realismo}", con_ref


def negativo_video(proyecto):
    return config.VID_NEGATIVO_REALISMO if proyecto.get("realismo") else config.VID_NEGATIVO


def refs_del_proyecto(carpeta, proyecto):
    return {p["nombre"]: carpeta / "personajes" / f"{p['nombre']}.png" for p in proyecto["personajes"]}


def cmd_imagenes(a):
    carpeta, proyecto, planos = cargar(a.proyecto)
    salida = config.SALIDAS / a.proyecto / "imagenes"
    refs_disponibles = refs_del_proyecto(carpeta, proyecto)
    subidas = {}
    trabajos = []
    for plano in filtrar(planos, a.planos):
        prompt, con_ref = prompt_de_imagen(proyecto, plano, refs_disponibles)
        refs = []
        for n in con_ref:
            if n not in subidas:
                subidas[n] = comfy.subir_imagen(refs_disponibles[n])
            refs.append(subidas[n])
        for v in range(1, a.variantes + 1):
            seed = comfy.semilla()
            destino = salida / f"{plano['id']}_v{v}.png"
            trabajos.append((comfy.flujo_imagen(prompt, f"drama-ia/{a.proyecto}/tmp", refs, *config.dims(proyecto, "img"), seed=seed), destino,
                             {"tipo": "imagen", "plano": plano["id"], "prompt": prompt, "refs": con_ref, "seed": seed}))
    lote(trabajos, config.SALIDAS / a.proyecto / "registro.jsonl")


def cmd_videos(a):
    _, proyecto, planos = cargar(a.proyecto)
    base = config.SALIDAS / a.proyecto
    ancho, alto = config.dims(proyecto, "final" if a.final else "vid")
    trabajos, sin_elegir = [], []
    for plano in filtrar(planos, a.planos):
        elegida = base / "elegidas" / f"{plano['id']}.png"
        if not elegida.exists():
            sin_elegir.append(plano["id"])
            continue
        imagen = comfy.subir_imagen(elegida)
        for t in range(1, a.tomas + 1):
            seed = comfy.semilla()
            destino = base / ("videos_final" if a.final else "videos") / f"{plano['id']}_t{t}.mp4"
            trabajos.append((comfy.flujo_video(imagen, plano["prompt_video"], f"drama-ia/{a.proyecto}/tmp",
                                               plano["duracion_s"], ancho, alto, seed, negativo_video(proyecto)), destino,
                             {"tipo": "video", "plano": plano["id"], "prompt": plano["prompt_video"], "seed": seed,
                              "resolucion": f"{ancho}x{alto}"}))
    if sin_elegir:
        print(f"Sin imagen elegida (se saltan): {', '.join(sin_elegir)}")
    lote(trabajos, base / "registro.jsonl")


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    for nombre, fn, n_def, n_nom in (("personajes", cmd_personajes, 4, "--variantes"),
                                     ("imagenes", cmd_imagenes, 3, "--variantes"),
                                     ("videos", cmd_videos, 2, "--tomas")):
        s = sub.add_parser(nombre)
        s.add_argument("proyecto")
        s.add_argument(n_nom, type=int, default=n_def, dest=n_nom.strip("-"))
        s.add_argument("--planos", help="ids separados por comas, p. ej. P001,P004")
        if nombre == "videos":
            s.add_argument("--final", action="store_true")
        s.set_defaults(fn=fn)
    a = ap.parse_args()
    if not comfy.disponible():
        raise SystemExit(f"ComfyUI no responde en {config.COMFY_URL}. Arráncalo con arrancar_comfyui.ps1")
    a.fn(a)


if __name__ == "__main__":
    main()
