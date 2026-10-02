"""Música (ACE-Step 1.5) y efectos (Stable Audio 3) según proyectos/<proyecto>/sonido.json, vía ComfyUI.

  python audio.py generar piloto [--variantes 3] [--solo choque,tema]
      -> salidas\\piloto\\sonido\\<id>_v1.flac ... y abre una página para escucharlas.
      Para usar otra variante que no sea v1, pon "elegida": "v2" en ese elemento de sonido.json.
"""
import argparse
import html
import json
import os

import comfy
import config
from generar import lote


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    g = sub.add_parser("generar")
    g.add_argument("proyecto")
    g.add_argument("--variantes", type=int, default=3)
    g.add_argument("--solo", help="ids separados por comas")
    a = ap.parse_args()
    if not comfy.disponible():
        raise SystemExit("ComfyUI no responde. Arráncalo con arrancar_comfyui.ps1")

    plan = json.loads((config.PROYECTOS / a.proyecto / "sonido.json").read_text(encoding="utf-8"))
    solo = set(a.solo.split(",")) if a.solo else None
    salida = config.SALIDAS / a.proyecto / "sonido"
    tmp = f"drama-ia/{a.proyecto}/tmp"
    trabajos = []
    for m in plan.get("musica", []):
        if solo and m["id"] not in solo:
            continue
        for v in range(1, a.variantes + 1):
            s = comfy.semilla()
            trabajos.append((comfy.flujo_musica(m["etiquetas"], tmp, m["segundos"], m["bpm"], m["tonalidad"], seed=s),
                             salida / f"{m['id']}_v{v}.flac", {"tipo": "musica", "id": m["id"], "seed": s}))
    for e in plan.get("efectos", []):
        if solo and e["id"] not in solo:
            continue
        for v in range(1, a.variantes + 1):
            s = comfy.semilla()
            trabajos.append((comfy.flujo_sfx(e["prompt"], tmp, e["segundos"], seed=s),
                             salida / f"{e['id']}_v{v}.flac", {"tipo": "efecto", "id": e["id"], "seed": s}))
    lote(trabajos, config.SALIDAS / a.proyecto / "registro.jsonl")
    pagina(a.proyecto, plan, salida)


def pagina(proyecto, plan, carpeta):
    bloques = []
    for grupo, elementos in (("Música", plan.get("musica", [])), ("Efectos", plan.get("efectos", []))):
        bloques.append(f"<h2>{grupo}</h2>")
        for e in elementos:
            desc = e.get("etiquetas") or e.get("prompt")
            donde = f"{e['desde']}–{e['hasta']}" if "desde" in e else f"{e['plano']} a los {e['en']} s"
            audios = "".join(f'<figure><figcaption>{w.stem}</figcaption><audio controls preload="none" src="sonido/{w.name}">'
                             f"</audio></figure>" for w in sorted(carpeta.glob(f"{e['id']}_v*.flac")))
            bloques.append(f"<section><h3>{e['id']} <small>({donde}, {e['db']} dB)</small></h3>"
                           f"<p class='d'>{html.escape(desc)}</p><div class='a'>{audios}</div></section>")
    pag = f"""<!doctype html><html lang="es"><head><meta charset="utf-8"><title>Sonido · {proyecto}</title>
<meta name="viewport" content="width=device-width, initial-scale=1"><style>
body{{background:#111;color:#ddd;font:15px/1.45 system-ui,sans-serif;margin:0;padding:16px}}
section{{border-top:1px solid #333;padding:8px 0}} h3{{margin:4px 0;font-size:16px}} small{{color:#999;font-weight:400}}
.d{{color:#999;margin:2px 0}} .a{{display:flex;flex-wrap:wrap;gap:12px}} figure{{margin:0}} figcaption{{color:#fc6;font-weight:600}}
</style></head><body><h1>Música y efectos · {proyecto}</h1>{''.join(bloques)}</body></html>"""
    ruta = carpeta.parent / "sonido.html"
    ruta.write_text(pag, encoding="utf-8")
    print(ruta)
    config.abrir(ruta)


if __name__ == "__main__":
    main()
