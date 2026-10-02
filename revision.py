"""Genera salidas\\<proyecto>\\revision.html: cada plano con todas sus tomas en bucle, lado a lado, para elegir la buena."""
import argparse
import html
import json
import os

import config


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("proyecto")
    ap.add_argument("--final", action="store_true", help="usar videos_final en lugar de videos")
    a = ap.parse_args()
    base = config.SALIDAS / a.proyecto
    carpeta = "videos_final" if a.final else "videos"
    planos = json.loads((config.PROYECTOS / a.proyecto / "planos.json").read_text(encoding="utf-8"))

    filas = []
    for p in planos:
        tomas = sorted((base / carpeta).glob(f"{p['id']}_t*.mp4"))
        texto = [f"<b>{p['id']}</b> · {html.escape(p['tipo_plano'])} · {p['duracion_s']:.0f} s",
                 html.escape(p["accion"])]
        if p.get("dialogo"):
            texto.append(f"<i>{html.escape(p['dialogo']['personaje'])}: «{html.escape(p['dialogo']['texto'])}»</i>")
        if p.get("narracion"):
            texto.append(f"<i>Narración: «{html.escape(p['narracion'])}»</i>")
        videos = "".join(
            f'<figure><video src="{carpeta}/{t.name}" autoplay loop muted playsinline></video>'
            f"<figcaption>{t.stem}</figcaption></figure>" for t in tomas) or "<p>(sin tomas)</p>"
        filas.append(f'<section><div class="info">{"<br>".join(texto)}</div><div class="tomas">{videos}</div></section>')

    pagina = f"""<!doctype html><html lang="es"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1"><title>Revisión {html.escape(a.proyecto)}</title>
<style>
body{{background:#111;color:#ddd;font:15px/1.45 system-ui,sans-serif;margin:0;padding:16px}}
h1{{font-size:20px;margin:0 0 4px}} .ayuda{{color:#999;margin:0 0 16px}}
section{{border-top:1px solid #333;padding:12px 0}} .info{{margin-bottom:8px}}
.tomas{{display:flex;gap:10px;flex-wrap:wrap}} figure{{margin:0;flex:1 1 360px;max-width:640px}}
video{{width:100%;border-radius:6px;background:#000}} figcaption{{color:#fc6;font-weight:600}}
</style></head><body>
<h1>Revisión de tomas · {html.escape(a.proyecto)}</h1>
<p class="ayuda">Anota la mejor toma de cada plano (p. ej. P001_t2). Clic derecho en un vídeo para verlo con sonido o a pantalla completa.</p>
{''.join(filas)}
</body></html>"""
    salida = base / ("revision_final.html" if a.final else "revision.html")
    salida.write_text(pagina, encoding="utf-8")
    print(salida)
    config.abrir(salida)


if __name__ == "__main__":
    main()
