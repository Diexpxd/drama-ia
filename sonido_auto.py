"""Plan de sonido automático: el LLM local propone música, ambientes y efectos a partir de planos.json, y este
script lo valida y lo convierte en sonido.json. Sirve para cualquier género e idioma (no usa listas de palabras).

  python sonido_auto.py piloto              # necesita Ollama encendido
  python sonido_auto.py piloto --reemplazar # sobrescribe un sonido.json editado (guarda copia en .copias)

Si ya tienes un sonido.json rellenado a mano, NO se toca: la propuesta se guarda en sonido_sugerido.json.
"""
import argparse
import hashlib
import json
import re
import shutil
import sys
import time
import urllib.request

import config

TONALIDADES = [f"{n} {m}" for m in ("minor", "major") for n in
               ("C", "C#", "Db", "D", "D#", "Eb", "E", "F", "F#", "Gb", "G", "G#", "Ab", "A", "A#", "Bb", "B")]
DB = {"musica": -16, "ambiente": -12, "fuerte": -4, "normal": -8, "suave": -12}

ESQUEMA = {
    "type": "object",
    "properties": {
        "musica": {"type": "array", "items": {"type": "object", "properties": {
            "desde_escena": {"type": "string"}, "hasta_escena": {"type": "string"},
            "descripcion_en": {"type": "string"}, "bpm": {"type": "integer"}, "tonalidad": {"type": "string"}},
            "required": ["desde_escena", "hasta_escena", "descripcion_en", "bpm", "tonalidad"]}},
        "ambientes": {"type": "array", "items": {"type": "object", "properties": {
            "escena": {"type": "string"}, "descripcion_en": {"type": "string"}},
            "required": ["escena", "descripcion_en"]}},
        "efectos": {"type": "array", "items": {"type": "object", "properties": {
            "plano": {"type": "string"}, "descripcion_en": {"type": "string"}, "segundo": {"type": "number"},
            "intensidad": {"type": "string", "enum": ["fuerte", "normal", "suave"]}},
            "required": ["plano", "descripcion_en", "segundo", "intensidad"]}},
    },
    "required": ["musica", "ambientes", "efectos"],
}

SISTEMA = """Eres diseñador de sonido de cine y compositor. Te paso la lista de planos de un vídeo ({genero}) y
propones su banda sonora para generarla con IA. Reglas:
- "musica": 1 a 3 temas; cada uno cubre un tramo de escenas consecutivas ("desde_escena"/"hasta_escena" con el título
  EXACTO de la escena). "descripcion_en" en INGLÉS: género musical, instrumentos, emoción, ritmo, y termina con "no vocals".
  "tonalidad" del estilo "D minor". "bpm" entre 50 y 160.
- "ambientes": un fondo continuo por escena si el lugar lo tiene (lluvia, ciudad, bosque, oficina...). "escena" = título
  EXACTO. "descripcion_en" en INGLÉS, describe solo el ambiente, sin música ni voces. Si el lugar es silencioso, no pongas nada.
- "efectos": sonidos puntuales de lo que OCURRE en un plano (golpes, pasos, puertas, disparos, choques, caídas...).
  "plano" = id exacto (P001...). "segundo" = en qué segundo del plano suena (0 = al empezar), menor que su duración.
  "descripcion_en" en INGLÉS, un único sonido concreto. "intensidad": fuerte, normal o suave. El efecto tiene que ser
  el sonido de la ACCIÓN de ese mismo plano (lo que se ve moverse en él); si en el plano nada se mueve o hace ruido,
  no le pongas efecto. Nunca sonidos de objetos que no aparecen en el plano. No pongas un efecto en cada plano.
"""


def ollama(proyecto, planos):
    lineas = [f"{p['id']} | escena: {p['escena']} | {p['duracion_s']} s | ambientación: {p.get('ambientacion', '')} | "
              f"acción: {p['accion']} | sonido sugerido: {p.get('sonido', '')}" for p in planos]
    datos = {"model": config.OLLAMA_MODELO, "stream": False, "think": False, "format": ESQUEMA,
             "options": {"temperature": 0.4, "num_ctx": 16384},
             "messages": [{"role": "system", "content": SISTEMA.replace("{genero}", proyecto["genero"])},
                          {"role": "user", "content": "Planos:\n" + "\n".join(lineas)}]}
    req = urllib.request.Request(config.OLLAMA_URL + "/api/chat", data=json.dumps(datos).encode(),
                                 headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=600) as r:
        res = json.loads(json.load(r)["message"]["content"])
    # sacar el modelo de la VRAM enseguida (la GPU la necesita ComfyUI)
    urllib.request.urlopen(urllib.request.Request(
        config.OLLAMA_URL + "/api/generate", data=json.dumps({"model": config.OLLAMA_MODELO, "keep_alive": 0}).encode(),
        headers={"Content-Type": "application/json"}), timeout=30).read()
    return res


def _slug(texto, n=18):
    s = re.sub(r"[^a-z0-9]+", "_", texto.lower()).strip("_")
    return s[:n].strip("_") or "sfx"


def construir(planos, propuesta):
    """Convierte la propuesta del LLM en sonido.json, validando todo (función pura: se puede probar sin LLM)."""
    avisos = []
    escenas = []  # [(titulo, [ids de plano])] en orden
    for p in planos:
        if not escenas or escenas[-1][0] != p["escena"]:
            escenas.append((p["escena"], []))
        escenas[-1][1].append(p["id"])
    por_titulo = {t: ids for t, ids in escenas}
    orden_escena = {t: i for i, (t, _) in enumerate(escenas)}
    dur = {p["id"]: float(p["duracion_s"]) for p in planos}

    def escena(nombre):
        """El LLM a veces abrevia el título: se acepta si coincide el principio ("Escena 2")."""
        if nombre in por_titulo:
            return nombre
        cab = nombre.split(":")[0].strip().lower()
        return next((t for t, _ in escenas if t.split(":")[0].strip().lower() == cab), None)

    musica = []
    for i, m in enumerate(propuesta.get("musica", [])[:3], 1):
        e1, e2 = escena(m["desde_escena"]), escena(m["hasta_escena"])
        if not e1 or not e2:
            avisos.append(f"música {i}: escena desconocida ({m['desde_escena']} / {m['hasta_escena']}), se ignora")
            continue
        if orden_escena[e1] > orden_escena[e2]:
            e1, e2 = e2, e1
        ids = [pid for t, lst in escenas[orden_escena[e1]:orden_escena[e2] + 1] for pid in lst]
        segundos = min(180, round(sum(dur[x] for x in ids) + 5))
        tono = m["tonalidad"] if m["tonalidad"] in TONALIDADES else "D minor"
        musica.append({"id": "tema" if i == 1 else f"tema{i}", "etiquetas": m["descripcion_en"].strip(),
                       "segundos": segundos, "bpm": max(50, min(160, int(m["bpm"]))), "tonalidad": tono,
                       "desde": ids[0], "hasta": ids[-1], "db": DB["musica"]})

    efectos, usados = [], set()
    tramos = []
    amb = {escena(a["escena"]): a["descripcion_en"].strip() for a in propuesta.get("ambientes", []) if escena(a["escena"])}
    for titulo, ids in escenas:
        if titulo not in amb:
            continue
        if tramos and tramos[-1]["prompt"] == amb[titulo] and tramos[-1]["hasta"] == escenas[orden_escena[titulo] - 1][1][-1]:
            tramos[-1]["hasta"] = ids[-1]
        else:
            tramos.append({"prompt": amb[titulo], "desde": ids[0], "hasta": ids[-1]})
    for i, t in enumerate(tramos, 1):
        efectos.append({"id": f"ambiente{i}", "prompt": t["prompt"], "segundos": 30, "desde": t["desde"],
                        "hasta": t["hasta"], "db": DB["ambiente"]})

    for e in propuesta.get("efectos", []):
        pid = e["plano"].strip().upper()
        if pid not in dur:
            avisos.append(f"efecto en plano desconocido {pid}, se ignora")
            continue
        en = round(max(0.0, min(float(e["segundo"]), dur[pid] - 0.5)), 1)
        base_id = f"{pid.lower()}_{_slug(e['descripcion_en'])}"
        ident, k = base_id, 2
        while ident in usados:
            ident, k = f"{base_id}{k}", k + 1
        usados.add(ident)
        efectos.append({"id": ident, "prompt": e["descripcion_en"].strip(), "segundos": 3, "plano": pid, "en": en,
                        "db": DB.get(e.get("intensidad"), DB["normal"])})
    sonido = {"_ayuda": "Propuesto automáticamente por sonido_auto.py (LLM). Revísalo y edítalo si quieres. "
                        "'desde'/'hasta' = planos que cubre; 'plano' + 'en' = efecto puntual; 'db' = volumen; "
                        "'elegida' = variante a usar (v1 si no se indica).",
              "musica": musica, "efectos": efectos}
    return sonido, avisos


def firma_planos(planos):
    """Lo que el plan de sonido tuvo en cuenta: qué pasa en cada plano."""
    datos = [(q["id"], q.get("escena"), q.get("accion")) for q in planos]
    return hashlib.sha1(json.dumps(datos, ensure_ascii=False).encode()).hexdigest()[:12]


def firma_plan(plan):
    contenido = {k: v for k, v in plan.items() if not k.startswith("_firma")}
    return hashlib.sha1(json.dumps(contenido, ensure_ascii=False, sort_keys=True).encode()).hexdigest()[:12]


def desactualizado(plan, planos):
    """True si el plan lo hizo este script, nadie lo ha editado y los planos han cambiado desde entonces"""
    return bool(plan.get("_firma_planos")) and plan.get("_firma_plan") == firma_plan(plan)         and plan["_firma_planos"] != firma_planos(planos)


def es_plantilla(plan):
    """True si sonido.json no existe, está vacío o sigue siendo la plantilla de nuevo_proyecto."""
    if not plan:
        return True
    elementos = plan.get("musica", []) + plan.get("efectos", [])
    return not elementos or all("(EN INGLÉS)" in (e.get("etiquetas") or e.get("prompt") or "") for e in elementos)


def guardar(nombre, sonido, reemplazar=False):
    carpeta = config.PROYECTOS / nombre
    ruta = carpeta / "sonido.json"
    actual = json.loads(ruta.read_text(encoding="utf-8")) if ruta.exists() else None
    if actual is not None and not es_plantilla(actual) and not reemplazar:
        destino = carpeta / "sonido_sugerido.json"
        destino.write_text(json.dumps(sonido, ensure_ascii=False, indent=2), encoding="utf-8")
        return destino, False
    if ruta.exists():
        (carpeta / ".copias").mkdir(exist_ok=True)
        shutil.copy2(ruta, carpeta / ".copias" / f"sonido.json.{time.strftime('%Y%m%d-%H%M%S')}")
    ruta.write_text(json.dumps(sonido, ensure_ascii=False, indent=2), encoding="utf-8")
    return ruta, True


CAMA_PROMPT = ("Dark cinematic drone, deep low rumble with a warm low-mid body, slow evolving tension pad, "
               "steady and seamless, no melody, no percussion, no vocals")
CAMA_DB = -24  # con -20 y sin ducking competía con la voz (2ª evaluación de Gemini)


EFECTO_CINE = ", cinematic and punchy, deep low end, natural room reverb"
GOLPE_PROMPT = "Deep cinematic trailer boom impact with a short whoosh riser into it, powerful low hit, no music"


TRANSICION_PROMPT = "Fast cinematic whoosh sweeping into a short deep sub impact, trailer scene transition, no music"


def golpe_inicial(planos):
    """Impacto grave en el primer segundo: parte del gancho para frenar el scroll."""
    return {"id": "golpe_inicial", "prompt": GOLPE_PROMPT, "segundos": 3, "plano": planos[0]["id"], "en": 0.0,
            "db": -4}


DATO_PROMPT = "Short punchy cinematic hit with a bright metallic ring, impact for an on-screen statistic, no music"


def golpe_de_datos():
    """Golpe que suena cada vez que aparece una tarjeta de datos (mezclar.py lo coloca en cada una)."""
    return {"id": "dato", "prompt": DATO_PROMPT, "segundos": 2, "rotulos": True, "db": -9}


def transiciones(planos):
    """Whoosh + impacto al empezar cada escena (menos la 1ª, que ya tiene el golpe): marca los giros de la historia."""
    inicios = [q["id"] for k, q in enumerate(planos) if k > 0 and q.get("escena") != planos[k - 1].get("escena")]
    return [{"id": "transicion", "prompt": TRANSICION_PROMPT, "segundos": 3, "plano": pid, "en": 0.0, "db": -8}
            for pid in inicios]


def cama_grave(planos):
    """Base grave continua bajo todo el vídeo (drone de thriller): une los efectos sueltos y da peso a la voz."""
    return {"id": "cama", "prompt": CAMA_PROMPT, "segundos": 30, "desde": planos[0]["id"], "hasta": planos[-1]["id"],
            "db": CAMA_DB, "duck": True}


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("proyecto")
    ap.add_argument("--reemplazar", action="store_true", help="sobrescribir un sonido.json editado a mano")
    a = ap.parse_args()
    proyecto = config.cargar_proyecto(a.proyecto)
    planos = json.loads((config.PROYECTOS / a.proyecto / "planos.json").read_text(encoding="utf-8"))
    try:
        import comfy
        if comfy.disponible():
            comfy.liberar_vram()  # que el LLM quepa en la GPU
    except Exception:
        pass
    print("Pidiendo al LLM el plan de sonido...", flush=True)
    try:
        propuesta = ollama(proyecto, planos)
    except OSError as e:
        sys.exit(f"Ollama no responde ({e}). Enciéndelo y vuelve a intentarlo.")
    sonido, avisos = construir(planos, propuesta)
    if proyecto.get("cama_grave") and planos:
        sonido["efectos"].insert(0, cama_grave(planos))
        for e in sonido["efectos"]:  # efectos con cuerpo de cine, no de librería plana
            if "plano" in e:
                e["prompt"] = e["prompt"].rstrip(". ") + EFECTO_CINE
    if proyecto.get("gancho") and planos:
        sonido["efectos"].insert(1, golpe_inicial(planos))
        sonido["efectos"] += transiciones(planos)
    if any(q.get("rotulo") for q in planos):
        sonido["efectos"].append(golpe_de_datos())
    for av in avisos:
        print("  aviso: " + av)
    sonido["_firma_planos"] = firma_planos(planos)
    sonido["_firma_plan"] = firma_plan(sonido)
    ruta, es_principal = guardar(a.proyecto, sonido, a.reemplazar)
    print(f"{len(sonido['musica'])} temas, {sum('desde' in e for e in sonido['efectos'])} ambientes, "
          f"{sum('plano' in e for e in sonido['efectos'])} efectos -> {ruta}")
    if not es_principal:
        print("Tu sonido.json está editado a mano: no se ha tocado. Mira sonido_sugerido.json y copia lo que quieras "
              "(o usa --reemplazar).")


if __name__ == "__main__":
    main()
