"""Convierte proyectos/<nombre>/guion.md en proyectos/<nombre>/planos.json usando un LLM local (Ollama)."""
import json
import re
import sys
import urllib.request

import config

ESQUEMA = {
    "type": "object",
    "properties": {
        "ambientacion": {"type": "string"},
        "planos": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "duracion_s": {"type": "number"},
                    "tipo_plano": {"type": "string"},
                    "personajes": {"type": "array", "items": {"type": "string"}},
                    "accion": {"type": "string"},
                    "dialogo": {"type": ["object", "null"], "properties": {
                        "personaje": {"type": "string"}, "texto": {"type": "string"}}},
                    "narracion": {"type": ["string", "null"]},
                    "prompt_imagen": {"type": "string"},
                    "prompt_video": {"type": "string"},
                    "sonido": {"type": "string"},
                },
                "required": ["duracion_s", "tipo_plano", "personajes", "accion", "dialogo", "narracion",
                             "prompt_imagen", "prompt_video", "sonido"],
            },
        }
    },
    "required": ["ambientacion", "planos"],
}

REGLA_ACCION_POR_DEFECTO = ("- Los combates se resuelven con planos cortos: detalle de la espada, reacción, silueta, impacto. "
                            "No coreografías largas.\n")
REGLA_ACCION_GENERAL = ("- Las escenas de acción rápida (peleas, persecuciones, caídas, golpes) se resuelven con planos cortos: "
                        "detalle, reacción, silueta, impacto. No coreografías largas.\n")
REGLA_VERTICAL = ("- Formato VERTICAL 9:16 (móvil): prioriza planos medios, primeros planos y detalles con un solo sujeto "
                  "centrado; evita los planos muy abiertos con varias personas a los lados. Descríbelo así en prompt_imagen.\n")


REGLA_REALISMO = (
    "- REALISMO ESTRICTO (esto es un documental): CERO FANTASÍA y CERO ANACRONISMOS.\n"
    "  Deduce del guion el AÑO o la época de cada escena y escríbelo en \"ambientacion\" junto a su estética concreta\n"
    "  (formato de ejemplo, NO lo copies: \"in 1975, 1970s aesthetics, analog technology, CRT monitors, film cameras,\n"
    "  period-accurate clothing, furniture and cars\"). Prohibido cualquier objeto, tecnología, ropa o vehículo posterior\n"
    "  a esa fecha (LEDs, pantallas planas, smartphones, portátiles modernos...): si dudas, elige lo más antiguo.\n"
    "  Nada mágico ni imposible: sin brillos sobrenaturales, hologramas, partículas mágicas, objetos que flotan o se\n"
    "  transforman, ni edificios que se rompen solos. Todo 100 % fotorrealista, como rodado con una cámara real.\n"
    "- \"prompt_video\" con REALISMO: solo movimientos físicamente posibles y sutiles (movimiento de cámara, lluvia,\n"
    "  humo, luz, personas u objetos moviéndose con naturalidad y gravedad real). Nada se deforma ni se transforma.\n")


def instrucciones(proyecto):
    """Instrucciones del LLM adaptadas al género y formato del proyecto."""
    genero = proyecto.get("genero", config.GENERO_POR_DEFECTO)
    accion = REGLA_ACCION_POR_DEFECTO if genero == config.GENERO_POR_DEFECTO else REGLA_ACCION_GENERAL
    vertical = REGLA_VERTICAL if proyecto.get("formato") == "9:16" else ""
    vertical += REGLA_REALISMO if proyecto.get("realismo") else ""  # opcional: en un wuxia la fantasía es el género
    return (SISTEMA_PLANTILLA.replace("{genero}", genero).replace("{regla_accion}", accion)
            .replace("{regla_vertical}", vertical))


SISTEMA_PLANTILLA = """Eres director de fotografía y storyboarder de {genero}.
Divides una escena en planos de 3 a 5 segundos para generarlos con IA (imagen fija y luego imagen a vídeo).

Reglas:
- "ambientacion": en INGLÉS, UNA frase para toda la escena con lugar concreto, momento del día, clima e iluminación
  (formato de ejemplo, NO lo copies: "in a bamboo forest at dawn, light mist, soft golden backlight").
  Fíjate en los detalles del guion que cambian el ambiente (niebla, fuego, amanecer, nieve...).
  Se añade a TODOS los planos de la escena para que no cambie entre planos. Si el guion no indica un cambio,
  copia la ambientación de la escena anterior; si indica un cambio (llega la niebla, amanece...), ajústala.
- Cada plano muestra UNA acción simple y visible. Nada de acciones largas o varias cosas a la vez.
- Varía el tipo de plano (gran plano general, plano general, plano medio, primer plano, plano detalle, contrapicado...).
{regla_accion}{regla_vertical}- "personajes": solo nombres exactos de la lista de personajes que aparezcan EN CUADRO.
- "accion": en español, qué pasa en el plano.
- "dialogo": si alguien habla en ese plano {personaje, texto}; si no, null.
- "narracion": voz en off si la hay, si no null.
- DIÁLOGOS Y NARRACIÓN: copia LITERALMENTE las frases que están en el guion, palabra por palabra.
  NUNCA inventes, resumas ni añadas frases: si el guion no pone una frase, ese plano va sin diálogo.
  Cada frase del guion aparece en un solo plano.
- "prompt_imagen": en INGLÉS. Describe el FOTOGRAMA INICIAL: encuadre, ángulo de cámara, personajes por su nombre,
  pose, vestuario, escenario, iluminación y clima. Es una imagen fija: nada de movimiento.
  No contradigas la ambientación (ni clima, ni luz, ni lugar) y no inventes lugares nuevos.
  NO incluyas el estilo visual general ni repitas la ambientación (se añaden automáticamente).
- "prompt_video": en INGLÉS. Describe SOLO el movimiento durante el plano: qué se mueve y cómo se mueve la cámara
  (slow push in, static shot, pan left...). Una o dos frases.
- "sonido": en español, efectos y ambiente para el montaje.
"""
SISTEMA = instrucciones({})  # las del piloto (se mantiene por compatibilidad)


def ollama(mensajes):
    datos = {"model": config.OLLAMA_MODELO, "messages": mensajes, "format": ESQUEMA, "stream": False,
             "think": False,  # en la comparativa, qwen3.5 con "pensar" rompía el JSON
             "options": {"temperature": 0.5, "num_ctx": 16384}}
    req = urllib.request.Request(config.OLLAMA_URL + "/api/chat", data=json.dumps(datos).encode(),
                                 headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=600) as r:
        return json.loads(json.load(r)["message"]["content"])


def _normal(texto):
    return " ".join(re.sub(r"[^\w\s]", " ", texto.lower()).split())


PREFIJO = re.compile(r"^\s*(narrador|narración|narracion|narrator|voz en off|[\w .'-]{1,30}?\s+dice)\s*:\s*", re.I)
COMILLAS = "\"'“”‘’«»"


def limpiar_frase(texto, nombres=()):
    """El LLM a veces copia 'Narrador: "..."' o 'Ana: "..."' tal cual: la voz leería 'Narrador'. Se quita."""
    if not texto:
        return texto
    t = PREFIJO.sub("", texto.strip())
    for n in nombres:
        if t.lower().startswith(n.lower() + ":"):
            t = t[len(n) + 1:]
    return t.strip().strip(COMILLAS).strip()


def frases_entre_comillas(texto):
    """Diálogos y narraciones del guion: todo lo que va entre comillas."""
    return [m.strip() for m in re.findall(r"[\"“«]([^\"”»]{3,})[\"”»]", texto)]


def recuperar_frases_perdidas(planos, escenas_guion):
    """Comprueba que cada frase entre comillas del guion está en algún plano de su escena."""
    for titulo, texto in escenas_guion:
        de_escena = [p for p in planos if p["escena"] == titulo]
        if not de_escena:
            continue
        dicho = _normal(" ".join((p.get("narracion") or "") + " " + ((p.get("dialogo") or {}).get("texto") or "")
                                 for p in de_escena))
        for frase in frases_entre_comillas(texto):
            if _normal(frase) in dicho:
                continue
            libre = next((p for p in de_escena if not p.get("narracion") and not p.get("dialogo")), None)
            destino = libre or de_escena[-1]
            destino["narracion"] = ((destino.get("narracion") or "") + " " + frase).strip()
            dicho += " " + _normal(frase)
            print(f"  aviso {destino['id']}: faltaba una frase del guion, se añade como narración: «{frase[:60]}...»",
                  flush=True)


def solo_frases_del_guion(p, texto_escena, nombres=()):
    """Quita diálogos/narraciones que no estén literalmente en el guion (el LLM a veces se los inventa)."""
    if p.get("narracion"):
        p["narracion"] = limpiar_frase(p["narracion"], nombres) or None
    if p.get("dialogo"):
        p["dialogo"]["texto"] = limpiar_frase(p["dialogo"].get("texto", ""), nombres)
    escena = _normal(texto_escena)
    if p.get("dialogo") and _normal(p["dialogo"].get("texto", "")) not in escena:
        print(f"  aviso {p['id']}: diálogo inventado, se quita: «{p['dialogo'].get('texto')}»", flush=True)
        p["dialogo"] = None
    if p.get("narracion") and _normal(p["narracion"]) not in escena:
        print(f"  aviso {p['id']}: narración inventada, se quita: «{p['narracion']}»", flush=True)
        p["narracion"] = None


def escenas(guion):
    partes = re.split(r"^## +", guion, flags=re.M)
    if len(partes) == 1:
        return [("Escena única", guion.strip())]
    return [(p.split("\n", 1)[0].strip(), p.split("\n", 1)[1].strip() if "\n" in p else "")
            for p in partes[1:]]


def main(nombre):
    carpeta = config.PROYECTOS / nombre
    proyecto = config.cargar_proyecto(nombre)
    guion = (carpeta / "guion.md").read_text(encoding="utf-8")
    fichas = "\n".join(f"- {p['nombre']}: {p['descripcion']}" for p in proyecto["personajes"])
    nombres = {p["nombre"] for p in proyecto["personajes"]}

    planos = []
    anterior = None
    for titulo, texto in escenas(guion):
        print(f"Escena: {titulo} ...", flush=True)
        contexto = (f"Escena anterior (para continuidad): {anterior['titulo']}\n{anterior['texto']}\n"
                    f"Ambientación de la escena anterior: {anterior['ambientacion']}\n\n") if anterior else ""
        res = ollama([
            {"role": "system", "content": instrucciones(proyecto)},
            {"role": "user", "content": f"Personajes:\n{fichas}\n\n{contexto}"
                                        f"ESCENA A DIVIDIR EN PLANOS: {titulo}\n{texto}"},
        ])
        anterior = {"titulo": titulo, "texto": texto, "ambientacion": res["ambientacion"]}
        print(f"  ambientación: {res['ambientacion']}", flush=True)
        for p in res["planos"]:
            p["id"] = f"P{len(planos) + 1:03d}"
            p["escena"] = titulo
            p["ambientacion"] = res["ambientacion"]
            solo_frases_del_guion(p, texto, nombres)
            p["duracion_s"] = min(5.0, max(3.0, float(p["duracion_s"])))
            desconocidos = set(p["personajes"]) - nombres
            if desconocidos:
                print(f"  aviso {p['id']}: personajes desconocidos {desconocidos}", flush=True)
                p["personajes"] = [n for n in p["personajes"] if n in nombres]
            planos.append({k: p[k] for k in ("id", "escena", "ambientacion", "duracion_s", "tipo_plano", "personajes", "accion",
                                              "dialogo", "narracion", "prompt_imagen", "prompt_video", "sonido")})
        print(f"  {len(res['planos'])} planos", flush=True)

    recuperar_frases_perdidas(planos, escenas(guion))

    urllib.request.urlopen(urllib.request.Request(
        config.OLLAMA_URL + "/api/generate", data=json.dumps({"model": config.OLLAMA_MODELO, "keep_alive": 0}).encode(),
        headers={"Content-Type": "application/json"}), timeout=30).read()

    (carpeta / "planos.json").write_text(json.dumps(planos, ensure_ascii=False, indent=2), encoding="utf-8")
    total = sum(p["duracion_s"] for p in planos)
    print(f"\n{len(planos)} planos, {total:.0f} s en total -> {carpeta / 'planos.json'}")


if __name__ == "__main__":
    main(sys.argv[1] if len(sys.argv) > 1 else "piloto")
