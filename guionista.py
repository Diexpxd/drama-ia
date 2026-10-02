"""Guionista en la nube (Claude o Gemini) con datos VERIFICADOS en la web. Crea un proyecto listo para producir.

  python guionista.py crear kodak "The fall of Kodak" [--segundos 60]
        [--idioma en] [--voz-de CorporateDocumentaryThriller] [--estilo "..."]
  python guionista.py aprobar kodak

Proveedor: config.GUIONISTA_PROVEEDOR ("auto" = Claude si hay ANTHROPIC_API_KEY en secretos.env; si no, Gemini).

Cadena anti-invenciones (ningún dato sin fuente):
  1. INVESTIGAR  la IA busca en la web -> hechos, cada uno con sus cifras/fechas y 2-4 fuentes. Con Claude solo se
                 aceptan URLs que la búsqueda devolvió de verdad (una URL inventada se descarta).
  2. VERIFICAR   ESTE PROGRAMA descarga cada página (si una web bloquea la descarga, la baja Claude con web fetch)
                 y le pasa a la IA solo los fragmentos relevantes. La IA devuelve la cita textual que lo prueba y
                 Python comprueba que esa cita EXISTE LITERALMENTE en la página descargada y contiene TODAS las
                 cifras del hecho. Un hecho necesita >= 2 dominios distintos que lo prueben así.
  3. ESCRIBIR    la IA redacta usando SOLO los hechos verificados; cada frase dice qué hechos usa y qué cifras.
  4. VALIDAR     Python comprueba que cada frase cita hechos verificados y que toda cifra que se dice (en dígitos o
                 en palabras: "nineteen seventy-five") está en esos hechos. Si falla, se pide una corrección; si
                 vuelve a fallar, no se crea el proyecto.
  5. APROBAR     el proyecto queda "pendiente de aprobación" (con las fuentes a la vista): el orquestador no gasta
                 GPU hasta que lo apruebes (web, bot de Telegram o `guionista.py aprobar`).
Se guardan fuentes.md (lista lista para la descripción de YouTube), fuentes.json e investigacion.json, y el gasto
de cada llamada en salidas\\<proyecto>\\gastos_ia.jsonl.
"""
import argparse
import copy
import json
import math
import re
import shutil
import unicodedata
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from html.parser import HTMLParser
from pathlib import Path
from urllib.parse import urlparse

import config


ESQ_INVESTIGACION = {
    "type": "object",
    "properties": {"hechos": {"type": "array", "items": {"type": "object", "properties": {
        "id": {"type": "string"},
        "afirmacion": {"type": "string"},
        "cifras": {"type": "array", "items": {"type": "string"}},
        "fuentes": {"type": "array", "items": {"type": "object", "properties": {
            "url": {"type": "string"}, "titulo": {"type": "string"}}, "required": ["url"]}},
    }, "required": ["id", "afirmacion", "cifras", "fuentes"]}}},
    "required": ["hechos"],
}

ESQ_VERIFICACION = {
    "type": "object",
    "properties": {"comprobaciones": {"type": "array", "items": {"type": "object", "properties": {
        "par": {"type": "integer"}, "respalda": {"type": "boolean"}, "cita_textual": {"type": "string"}},
        "required": ["par", "respalda", "cita_textual"]}}},
    "required": ["comprobaciones"],
}

ESQ_GUION = {
    "type": "object",
    "properties": {
        "titulo": {"type": "string"},
        "escenas": {"type": "array", "items": {"type": "object", "properties": {
            "titulo_es": {"type": "string"},
            "descripcion_es": {"type": "string"},
            "ambientacion_en": {"type": "string"},
            "frases": {"type": "array", "items": {"type": "object", "properties": {
                "texto": {"type": "string"},
                "hechos": {"type": "array", "items": {"type": "string"}},
                "cifras": {"type": "array", "items": {"type": "string"}}},
                "required": ["texto", "hechos", "cifras"]}},
            "planos": {"type": "array", "items": {"type": "object", "properties": {
                "tipo_plano": {"type": "string"}, "accion_es": {"type": "string"},
                "prompt_imagen": {"type": "string"}, "prompt_video": {"type": "string"},
                "duracion_s": {"type": "number"}, "sonido_es": {"type": "string"}},
                "required": ["tipo_plano", "accion_es", "prompt_imagen", "prompt_video", "duracion_s", "sonido_es"]}},
        }, "required": ["titulo_es", "descripcion_es", "ambientacion_en", "frases", "planos"]}},
    },
    "required": ["titulo", "escenas"],
}

INSTR_INVESTIGAR = """You are a meticulous fact-checker and researcher for a business/finance documentary channel.
Search the web to research the topic. Return 10 to 18 concrete, relevant FACTS for a short documentary.
Rules:
- Every fact must be supported by 2 to 4 independent, reputable sources on DIFFERENT domains (official company
  filings or press releases, major newspapers/agencies, encyclopedias, academic or government sites). No forums,
  no social media, no content farms, no AI-generated sites. A program will download each page and look for the
  fact in it, so prefer plain HTML articles readable without login or paywall (e.g. Wikipedia, Britannica,
  company newsrooms, government sites); avoid PDFs, videos and pages that need JavaScript.
- "fuentes": URLs copied exactly from your search results. Never invent, guess or shorten a URL.
- "cifras": every number, date, year, percentage or amount that appears in the fact, written exactly as in the
  fact (e.g. "1975", "80%", "$6.7 billion"). Empty list if the fact has no numbers.
- ATOMIC facts: each fact states ONE thing, with at most two figures that appear TOGETHER in the same sentence of
  every source (a program checks that one sentence of each page contains all the fact's figures). Split compound
  facts ("opened in 1985 and had 9,000 stores by 2004" -> two facts). Use the figures exactly as the sources
  write them (if they say "about 9,000", the fact says "about 9,000", not 9,094). Do not add exact days unless the
  sources give them.
- Prefer facts with clear dates and figures; avoid opinions, rumors and disputed claims (or skip them).
- "afirmacion" in English, one sentence, precise and neutral, phrased close to how the sources state it.
  "id": H1, H2, H3...
When you finish searching, answer with ONLY this JSON object (no markdown fences, no text before or after):
{"hechos": [{"id": "H1", "afirmacion": "...", "cifras": ["..."], "fuentes": [{"url": "https://...", "titulo": "..."}]}]}
"""

INSTR_VERIFICAR = """You are a strict, independent fact verifier. A program downloaded web pages and gives you, for each
numbered PAIR, one fact and EXCERPTS from one page. Use ONLY those excerpts (not your own knowledge).
- "respalda": true only if the excerpts explicitly state the WHOLE fact, including every figure and date. A different
  or rounded number, a different year, or only partial support = false.
- "cita_textual": copy ONE contiguous passage from the excerpts, character for character (no ellipsis, no edits, no
  added words), that proves the fact and contains every figure. Empty string if "respalda" is false.
  A program will check that your quote appears literally in the page.
Return one entry per PAIR ("par" = the pair number).
"""

INSTR_ESCRIBIR = """You are the head scriptwriter of a vertical (9:16) business documentary channel for YouTube Shorts.
Write a gripping, fast-paced script of about {segundos} seconds of narration (~{palabras} words total) in {idioma}.
STRICT RULES (a program will check them):
- You may ONLY use the VERIFIED FACTS given below. Do not add any other fact, number, date, name, quote or claim,
  not even well-known ones. Storytelling words and rhetorical questions are fine; new facts are not.
- Each sentence ("frases") lists the fact ids it relies on ("hechos") and every number/date it mentions ("cifras",
  in digits exactly as in the facts, e.g. "1975", "80%"). A sentence with no fact at all must be a pure hook or
  question with no factual content.
- Write numbers in the narration as words for the voice actor ("nineteen seventy-five", "eighty percent").
- 4 to 6 scenes. For each scene: "titulo_es" and "descripcion_es" in SPANISH (what we see); "ambientacion_en" in
  English: place, YEAR/era, weather and lighting (period-accurate: no technology newer than the era).
- PACE: each plano covers at most ~8 words of narration (fast cuts keep viewers). A scene with N narration words
  needs at least N/8 planos (a program checks it). "duracion_s": 4 for every plano.
- 2 to 6 "planos" per scene, B-roll only. "prompt_imagen" (English): the first frame, photorealistic,
  cinematic, period-accurate, vertical framing (close-ups and medium shots, one centered subject). "prompt_video"
  (English): physically plausible motion and camera movement. No magic, no morphing, no impossible physics.
  "accion_es": what happens, in Spanish. "sonido_es": sound effects/ambience idea, in Spanish.
- VISUAL RULES (AI video cannot draw letters or hands; a program rejects prompts that break them):
  * NOTHING WITH WRITING: never show signs, storefronts, logos, labels, posters, billboards, newspapers, documents,
    papers, contracts, books, screens, monitors, computers, phones, keyboards or printed packaging. Do not even
    mention them (not even "blurred" or "no text").
  * NO HANDS, FINGERS OR FACES: nobody holding, typing, signing or shaking hands. People only as distant
    silhouettes or shadows.
  * Use instead: silhouettes, shadows, empty rooms and corridors, architecture at dusk, rain on glass, smoke, dust
    in light beams, macro shots of plain objects (metal, glass, plastic cases, coins, keys, film reels), weather,
    abstract light and bokeh, objects falling, rolling or breaking, period vehicles without markings.
- THE HOOK: the FIRST plano of scene 1 must stop the scroll: an extreme close-up or macro of a striking object and an
  energetic but plausible camera move in "prompt_video" (e.g. "fast push-in", "rapid dolly in", "snap zoom in",
  "object falling toward the camera"). Every other plano keeps calm, subtle motion.
- Hook in the first sentence; end with a short lesson or twist.
VERIFIED FACTS:
{hechos}
"""


def dominio(url):
    d = urlparse(url).netloc.lower()
    return d[4:] if d.startswith("www.") else d


def clave_url(url):
    """Para comparar URLs: sin esquema, www, fragmento ni barra final."""
    p = urlparse(url.strip())
    return (dominio(url) + p.path.rstrip("/") + (f"?{p.query}" if p.query else "")).lower()


def normal_cifra(c):
    """'$6.7 billion' -> '6.7'; '80 %' -> '80'; '1,000' -> '1000'; 'US$ 1.5' -> '1.5'. Solo la parte numérica."""
    m = re.findall(r"\d[\d,]*(?:\.\d+)?", c)
    return [x.replace(",", "") for x in m]


UNIDADES = {w: i for i, w in enumerate("zero one two three four five six seven eight nine ten eleven twelve thirteen "
                                       "fourteen fifteen sixteen seventeen eighteen nineteen".split())}
DECENAS = {w: 10 * i for i, w in enumerate("_ _ twenty thirty forty fifty sixty seventy eighty ninety".split()) if w != "_"}
ESCALAS = {"hundred": 100, "thousand": 1000, "million": 1_000_000, "billion": 1_000_000_000}
ORDINALES = dict(zip("first second third fourth fifth sixth seventh eighth ninth tenth eleventh twelfth thirteenth "
                     "fourteenth fifteenth sixteenth seventeenth eighteenth nineteenth".split(),
                     "one two three four five six seven eight nine ten eleven twelve thirteen fourteen fifteen "
                     "sixteen seventeen eighteen nineteen".split())) | {"twentieth": "twenty", "thirtieth": "thirty"}


def _valor(w):
    return UNIDADES[w] if w in UNIDADES else DECENAS[w]


def _dos_cifras(ws):
    if len(ws) == 1:
        return _valor(ws[0])
    if len(ws) == 2 and ws[0] in DECENAS and ws[1] in UNIDADES and UNIDADES[ws[1]] < 10:
        return DECENAS[ws[0]] + UNIDADES[ws[1]]
    return None


def numeros_en_palabras(texto):
    """Números escritos en inglés dentro de un texto -> set de strings en dígitos."""
    palabras = re.findall(r"[a-z]+|[,;:.!?]", texto.lower().replace("-", " "))
    # décadas en plural: "the nineteen nineties" -> "nineteen ninety" (-> 1990)
    palabras = [w[:-3] + "y" if w.endswith("ties") and w[:-3] + "y" in DECENAS else w for w in palabras]
    palabras = [x for w in palabras for x in ((ORDINALES[w], ",") if w in ORDINALES else (w,))]
    # "a hundred / a million" = "one hundred / one million"
    palabras = ["one" if w == "a" and i + 1 < len(palabras) and palabras[i + 1] in ESCALAS else w
                for i, w in enumerate(palabras)]
    res, i = set(), 0
    numerica = lambda w: w in UNIDADES or w in DECENAS or w in ESCALAS or w in ("and", "point")
    while i < len(palabras):
        if palabras[i] not in UNIDADES and palabras[i] not in DECENAS:
            i += 1
            continue
        grupo = []
        while i < len(palabras) and numerica(palabras[i]):
            grupo.append(palabras[i])
            i += 1
        while grupo and grupo[-1] in ("and", "point"):  # "... and" final no es parte del número
            grupo.pop()
        if 2 <= len(grupo) <= 3 and not any(w in ESCALAS or w in ("and", "point") for w in grupo):
            siglo, resto = _valor(grupo[0]), _dos_cifras(grupo[1:])
            if resto is not None and ((10 <= siglo <= 19 and resto >= 10) or (siglo == 20 and resto >= 10)):
                res.add(str(siglo * 100 + resto))
                continue
        # Cantidad normal con escalas y decimales
        total, actual, decimal, en_decimal = 0.0, 0, "", False
        for w in grupo:
            if w == "and":
                continue
            if w == "point":
                en_decimal = True
                continue
            if en_decimal and w in UNIDADES and UNIDADES[w] < 10:
                decimal += str(UNIDADES[w])
                continue
            if w == "hundred":
                actual = (actual or 1) * 100
            elif w in ESCALAS:
                base = float(f"{actual}.{decimal}") if decimal else float(actual or 1)
                if decimal:
                    res.add(f"{actual}.{decimal}")  # "six point seven billion" también es 6.7
                total += base * ESCALAS[w]
                actual, decimal, en_decimal = 0, "", False
            else:
                actual += _valor(w)
        total += float(f"{actual}.{decimal}") if decimal else actual
        if total:
            res.add(str(int(total)) if float(total).is_integer() else str(round(total, 6)))
    return res


def cifras_dichas(texto):
    """Todas las cifras que se DICEN en una frase: en dígitos y en palabras (inglés)."""
    return set(n for c in re.findall(r"\d[\d,]*(?:\.\d+)?", texto) for n in normal_cifra(c)) | numeros_en_palabras(texto)


def json_de_texto(texto, paso):
    """Saca el objeto JSON de una respuesta de texto (a veces viene con ```json o con una frase delante)."""
    try:
        return json.loads(texto)
    except json.JSONDecodeError:
        a, b = texto.find("{"), texto.rfind("}")
        if a < 0 or b <= a:
            raise RuntimeError(f"La IA ({paso}) no devolvió JSON: {texto[:200]}")
        return json.loads(texto[a:b + 1])


UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) "
      "Chrome/140.0.0.0 Safari/537.36")


class _Texto(HTMLParser):
    SALTAR = {"script", "style", "noscript", "svg", "template", "iframe", "head"}
    BLOQUE = {"p", "div", "li", "br", "tr", "td", "th", "section", "article", "blockquote", "figcaption",
              "h1", "h2", "h3", "h4", "h5", "h6", "dd", "dt", "caption"}

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.partes, self.salto = [], 0

    def handle_starttag(self, tag, attrs):
        if tag in self.SALTAR:
            self.salto += 1
        elif tag in self.BLOQUE:
            self.partes.append("\n")

    def handle_endtag(self, tag):
        if tag in self.SALTAR:
            self.salto = max(0, self.salto - 1)
        elif tag in self.BLOQUE:
            self.partes.append("\n")

    def handle_data(self, d):
        if not self.salto:
            self.partes.append(d)


def html_a_texto(html):
    p = _Texto()
    p.feed(html)
    lineas = (re.sub(r"[ \t\r\f\v\xa0]+", " ", l).strip() for l in "".join(p.partes).split("\n"))
    return "\n".join(l for l in lineas if l)


def descargar(url):
    """-> (texto de la página, None) o (None, motivo del fallo)."""
    texto, motivo = _descargar(url)
    if texto or "archive.org" in url:
        return texto, motivo
    copia, _ = _descargar(f"https://web.archive.org/web/2026id_/{url}", timeout=40)
    return (copia, None) if copia else (None, motivo)


def _descargar(url, timeout=25):
    try:
        req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept-Language": "en-US,en;q=0.9",
                                                   "Accept": "text/html,application/xhtml+xml,text/plain"})
        with urllib.request.urlopen(req, timeout=timeout) as r:
            tipo = r.headers.get_content_type()
            if tipo not in ("text/html", "application/xhtml+xml", "text/plain"):
                return None, f"tipo {tipo}"
            datos = r.read(5_000_000)
            charset = r.headers.get_content_charset() or "utf-8"
        texto = datos.decode(charset, errors="replace")
        if tipo != "text/plain":
            texto = html_a_texto(texto)
        if len(texto) < 400:
            return None, "página casi vacía (¿necesita JavaScript?)"
        return texto, None
    except Exception as e:  # noqa: BLE001 - cualquier fallo de red = esa fuente no cuenta
        return None, f"{type(e).__name__}: {str(e)[:80]}"


def normalizar(s):
    """Para comparar citas con la página: sin mayúsculas, comillas/guiones unificados, sin [12] de Wikipedia."""
    s = unicodedata.normalize("NFKC", s)
    s = s.translate(str.maketrans({"\u2018": "'", "\u2019": "'", "\u201c": '"', "\u201d": '"', "\u2013": "-",
                                   "\u2014": "-", "\u2212": "-", "\xa0": " "}))
    s = re.sub(r"\[(?:\d+|[a-z]|citation needed|note \d+)\]", "", s.lower())
    return re.sub(r"\s+", " ", s).strip()


def cita_en_pagina(cita, pagina_normal):
    """La cita tiene que estar LITERALMENTE en la página (si la IA usó '...', cada trozo por separado)."""
    trozos = [t for t in re.split(r"\s*(?:\.\.\.|\u2026)\s*", normalizar(cita)) if len(t) >= 8]
    return bool(trozos) and all(t in pagina_normal for t in trozos)


VACIAS = set("that this with from were was have has had which their they them into than then when what also more "
             "most over after before about under while where there these those been being would could should its "
             "the and for per".split())


def claves_de(afirmacion):
    return {w for w in re.findall(r"[a-z]{4,}", afirmacion.lower()) if w not in VACIAS}


def cita_habla_del_hecho(cita, hecho, minimo=0.35):
    """Contra citas que solo tienen la cifra ('By X, September 23, 2010' = firma del artículo): la cita debe"""
    claves = {w[:5] for w in claves_de(hecho["afirmacion"])}
    en_cita = {w[:5] for w in re.findall(r"[a-z]{4,}", normalizar(cita))}
    return not claves or len(claves & en_cita) >= max(2, round(minimo * len(claves)))


def fragmentos(texto, hecho, limite=3500):
    """Trozos de la página que hablan del hecho (sus cifras y palabras clave), para no pagar la página entera."""
    oraciones = [o for o in re.split(r"(?<=[.!?])\s+|\n+", texto) if o.strip()]
    claves = claves_de(hecho["afirmacion"])
    cifras = [n for c in hecho.get("cifras", []) for n in normal_cifra(c)]
    puntos = []
    for i, o in enumerate(oraciones):
        nums = set(n for x in re.findall(r"\d[\d,]*(?:\.\d+)?", o) for n in normal_cifra(x))
        baja = o.lower()
        p = 3 * sum(1 for c in cifras if c in nums) + sum(1 for w in claves if w in baja)
        if p >= 3:
            puntos.append((p, i))
    elegidos, total = set(), 0
    for p, i in sorted(puntos, reverse=True):
        ventana = range(max(0, i - 1), min(len(oraciones), i + 2))
        nuevo = sum(len(oraciones[k]) for k in ventana if k not in elegidos)
        if total + nuevo > limite:
            continue
        elegidos.update(ventana)
        total += nuevo
    if not elegidos:
        return ""
    salida, anterior = [], None
    for k in sorted(elegidos):
        if anterior is not None and k != anterior + 1:
            salida.append("\n[...]\n")
        salida.append(oraciones[k] + " ")
        anterior = k
    return "".join(salida).strip()


def esquema_estricto(esq):
    """Claude exige additionalProperties: false en cada objeto del esquema."""
    esq = copy.deepcopy(esq)

    def recorrer(n):
        if isinstance(n, dict):
            if n.get("type") == "object":
                n["additionalProperties"] = False
            for v in n.values():
                recorrer(v)
        elif isinstance(n, list):
            for v in n:
                recorrer(v)
    recorrer(esq)
    return esq


class Registro:
    def __init__(self, ruta):
        self.ruta, self.gasto, self.busquedas = ruta, 0.0, 0

    def apuntar(self, paso, modelo, ent, sal, busq, usd):
        self.gasto += usd
        self.busquedas += busq
        print(f"  [{paso}] {modelo}: {ent} tokens entrada, {sal} salida, {busq} búsquedas, ~${usd:.4f}", flush=True)
        if self.ruta:
            Path(self.ruta).parent.mkdir(parents=True, exist_ok=True)  # la carpeta solo nace si hay gasto
            with open(self.ruta, "a", encoding="utf-8") as f:
                f.write(json.dumps({"fecha": datetime.now().isoformat(timespec="seconds"), "paso": paso,
                                    "modelo": modelo, "tokens_entrada": ent, "tokens_salida": sal,
                                    "busquedas": busq, "usd": round(usd, 5)}) + "\n")


class Claude:
    nombre = "Claude"

    def __init__(self, cliente=None, registro=None):
        if cliente is None:
            import anthropic
            clave = config.leer_secretos().get("ANTHROPIC_API_KEY")
            if not clave:
                raise SystemExit(f"Falta ANTHROPIC_API_KEY en {config.SECRETOS}")
            cliente = anthropic.Anthropic(api_key=clave, max_retries=4)
        self.cliente, self.reg = cliente, Registro(registro)

    def _crear(self, paso, modelo, mensajes, **kw):
        bloques = []
        for _ in range(8):
            r = self.cliente.messages.create(model=modelo, messages=mensajes, **kw)
            self._apuntar(paso, modelo, r)
            bloques += list(r.content)
            if r.stop_reason != "pause_turn":
                break
            mensajes = mensajes + [{"role": "assistant", "content": r.content}]
        if r.stop_reason == "max_tokens":
            raise RuntimeError(f"Claude ({paso}) se quedó sin espacio para responder (max_tokens)")
        if r.stop_reason == "refusal":
            raise RuntimeError(f"Claude ({paso}) rechazó la petición")
        return r, bloques

    def llamar(self, paso, entrada, instruccion, esquema, buscar=False):
        modelo = config.CLAUDE_MODELOS[paso]
        kw = {"max_tokens": 16000, "system": instruccion}
        if paso == "verificar":
            kw["thinking"] = {"type": "disabled"}
        if buscar:
            kw["tools"] = [{"type": "web_search_20250305", "name": "web_search", "max_uses": 6}]
            kw["extra_body"] = {"cache_control": {"type": "ephemeral"}}
        else:
            kw["extra_body"] = {"output_config": {"format": {"type": "json_schema",
                                                             "schema": esquema_estricto(esquema)}}}
        r, bloques = self._crear(paso, modelo, [{"role": "user", "content": entrada}], **kw)
        urls = set()
        for b in bloques:
            if getattr(b, "type", "") == "web_search_tool_result" and isinstance(getattr(b, "content", None), list):
                urls.update(x.url for x in b.content if getattr(x, "url", None))
            for c in getattr(b, "citations", None) or []:
                if getattr(c, "url", None):
                    urls.add(c.url)
        # el JSON es el texto que viene después de la última herramienta
        ultimo = max([i for i, b in enumerate(bloques) if b.type != "text"], default=-1)
        texto = "".join(b.text for b in bloques[ultimo + 1:] if b.type == "text")
        return json_de_texto(texto, paso), urls

    def descargar_remoto(self, urls):
        textos = {}
        modelo = config.CLAUDE_MODELOS["verificar"]
        for i in range(0, len(urls), 5):
            tanda = urls[i:i + 5]
            try:
                _, bloques = self._crear(
                    "descargar", modelo,
                    [{"role": "user", "content": "Fetch each of these URLs (all of them, in parallel):\n" + "\n".join(tanda)}],
                    max_tokens=1024, thinking={"type": "disabled"},
                    system="You are a page downloader. Use web_fetch on every URL the user gives. Do not summarize. "
                           "When done, reply only DONE.",
                    tools=[{"type": "web_fetch_20250910", "name": "web_fetch", "max_uses": len(tanda) + 2,
                            "max_content_tokens": 6000}])
            except Exception as e:  # noqa: BLE001
                print(f"  aviso: descarga remota falló ({type(e).__name__}: {str(e)[:80]})", flush=True)
                continue
            pedida = {}
            for b in bloques:
                d = b.model_dump() if hasattr(b, "model_dump") else b
                if d.get("type") == "server_tool_use" and d.get("name") == "web_fetch":
                    pedida[d["id"]] = (d.get("input") or {}).get("url")
                elif d.get("type") == "web_fetch_tool_result":
                    c = d.get("content") or {}
                    src = ((c.get("content") or {}).get("source") or {})
                    if c.get("type") == "web_fetch_result" and src.get("type") == "text" and src.get("data"):
                        url = pedida.get(d.get("tool_use_id")) or c.get("url")
                        textos[url] = src["data"]
        return textos

    def _apuntar(self, paso, modelo, r):
        u = r.usage
        normal = u.input_tokens or 0
        escritos = getattr(u, "cache_creation_input_tokens", 0) or 0  # caché 5 min: 1,25x
        leidos = getattr(u, "cache_read_input_tokens", 0) or 0        # lectura de caché: 0,1x
        ent = normal + escritos + leidos
        sal = u.output_tokens or 0
        stu = getattr(u, "server_tool_use", None)
        busq = (getattr(stu, "web_search_requests", 0) or 0) if stu else 0
        p = config.CLAUDE_PRECIOS.get(modelo, {"entrada": 0, "salida": 0})
        usd = (normal + 1.25 * escritos + 0.1 * leidos) / 1e6 * p["entrada"] + sal / 1e6 * p["salida"] \
            + busq * config.CLAUDE_BUSQUEDA_USD
        self.reg.apuntar(paso, modelo, ent, sal, busq, usd)


class Gemini:
    nombre = "Gemini"
    NIVEL = {"investigar": "high", "verificar": "medium", "escribir": "high"}

    def __init__(self, cliente=None, registro=None):
        if cliente is None:
            from google import genai
            clave = config.leer_secretos().get("GEMINI_API_KEY")
            if not clave:
                raise SystemExit(f"Falta ANTHROPIC_API_KEY o GEMINI_API_KEY en {config.SECRETOS}")
            cliente = genai.Client(api_key=clave)
        self.cliente, self.reg = cliente, Registro(registro)

    def llamar(self, paso, entrada, instruccion, esquema, buscar=False):
        modelo = config.GEMINI_MODELOS[paso]
        r = self.cliente.interactions.create(
            model=modelo, input=entrada, system_instruction=instruccion,
            tools=[{"type": "google_search"}] if buscar else [],
            response_format={"type": "text", "mime_type": "application/json", "schema": esquema},
            generation_config={"thinking_level": self.NIVEL[paso]}, store=False)
        if getattr(r, "status", "completed") not in ("completed", None):
            raise RuntimeError(f"Gemini ({paso}) terminó con estado {r.status}")
        self._apuntar(paso, modelo, r)
        urls = set()
        for s in getattr(r, "steps", None) or []:
            for bloque in getattr(s, "content", None) or []:
                for a in getattr(bloque, "annotations", None) or []:
                    if getattr(a, "type", "") == "url_citation" and getattr(a, "url", None):
                        urls.add(a.url)
        return json_de_texto(r.output_text or "", paso), urls

    def descargar_remoto(self, urls):
        return {}

    def _apuntar(self, paso, modelo, r):
        u = getattr(r, "usage", None)
        ent = (getattr(u, "total_input_tokens", 0) or 0) + (getattr(u, "total_tool_use_tokens", 0) or 0)
        sal = (getattr(u, "total_output_tokens", 0) or 0) + (getattr(u, "total_thought_tokens", 0) or 0)
        busq = sum(g.count or 0 for g in (getattr(u, "grounding_tool_count", None) or []) if g.type == "google_search")
        p = config.GEMINI_PRECIOS.get(modelo, {"entrada": 0, "salida": 0})
        antes_2027 = datetime.now() < datetime(2027, 1, 1)
        pe = p["entrada"] if antes_2027 else p.get("entrada_2027", p["entrada"])
        ps = p["salida"] if antes_2027 else p.get("salida_2027", p["salida"])
        self.reg.apuntar(paso, modelo, ent, sal, busq, ent / 1e6 * pe + sal / 1e6 * ps)  # 5.000 búsquedas/mes gratis


def proveedor(registro=None, elegido=None):
    elegido = elegido or config.GUIONISTA_PROVEEDOR
    if elegido == "auto":
        elegido = "claude" if config.leer_secretos().get("ANTHROPIC_API_KEY") else "gemini"
    return Claude(registro=registro) if elegido == "claude" else Gemini(registro=registro)


def investigar(ia, tema):
    print(f"1/4 Investigando en la web ({ia.nombre})...", flush=True)
    datos, devueltas = ia.llamar("investigar", f"Topic: {tema}", INSTR_INVESTIGAR, ESQ_INVESTIGACION, buscar=True)
    reales = {clave_url(u) for u in devueltas}
    hechos = datos.get("hechos", [])
    for h in hechos:
        fuentes = [f for f in h.get("fuentes", []) if f.get("url", "").startswith("http")]
        h["fuentes_no_citadas"] = [f for f in fuentes if reales and clave_url(f["url"]) not in reales]
        h["fuentes"] = [f for f in fuentes if not reales or clave_url(f["url"]) in reales]
    print(f"  {len(hechos)} hechos propuestos, {len(reales)} páginas encontradas", flush=True)
    return hechos, sorted(devueltas)


def verificar(ia, hechos, max_car_tanda=40000):
    """Cada hecho necesita >= 2 dominios distintos cuya página DESCARGADA contenga literalmente una cita que lo"""
    print("2/4 Verificando: descargando las páginas y buscando cada dato en ellas...", flush=True)
    urls = sorted({f["url"] for h in hechos for f in h["fuentes"]})
    with ThreadPoolExecutor(8) as ex:
        bajadas = dict(zip(urls, ex.map(descargar, urls)))
    paginas = {u: t for u, (t, _) in bajadas.items() if t}
    fallos = {u: m for u, (t, m) in bajadas.items() if not t}
    print(f"  {len(paginas)} de {len(urls)} páginas descargadas", flush=True)

    resultado = {h["id"]: {"buenas": {}, "motivos": []} for h in hechos}

    def comprobar(lista_hechos):
        pares = []  # (hecho, url, fragmentos)
        for h in lista_hechos:
            for f in h["fuentes"]:
                if f["url"] not in paginas or dominio(f["url"]) in resultado[h["id"]]["buenas"]:
                    continue
                trozo = fragmentos(paginas[f["url"]], h)
                if trozo:
                    pares.append((h, f["url"], trozo))
                else:
                    resultado[h["id"]]["motivos"].append(f"{dominio(f['url'])}: la página no menciona el dato")
        tandas, actual, tam = [], [], 0
        for p in pares:
            if actual and tam + len(p[2]) > max_car_tanda:
                tandas.append(actual)
                actual, tam = [], 0
            actual.append(p)
            tam += len(p[2])
        if actual:
            tandas.append(actual)
        for tanda in tandas:
            entrada = "\n\n".join(
                f"PAIR {k}\nFACT: {h['afirmacion']}\nFIGURES: {', '.join(h.get('cifras', [])) or 'none'}\n"
                f"SOURCE: {dominio(url)}\nEXCERPTS:\n{trozo}" for k, (h, url, trozo) in enumerate(tanda, 1))
            datos, _ = ia.llamar("verificar", entrada, INSTR_VERIFICAR, ESQ_VERIFICACION)
            for c in datos.get("comprobaciones", []):
                k = c.get("par")
                if not isinstance(k, int) or not 1 <= k <= len(tanda):
                    continue
                h, url, _ = tanda[k - 1]
                r = resultado[h["id"]]
                cita = (c.get("cita_textual") or "").strip()
                if not c.get("respalda") or not cita:
                    r["motivos"].append(f"{dominio(url)}: no lo respalda")
                    continue
                if not cita_en_pagina(cita, normalizar(paginas[url])):
                    r["motivos"].append(f"{dominio(url)}: la cita no está literalmente en la página")
                    continue
                en_cita = cifras_dichas(cita)
                faltan = [n for c_ in h.get("cifras", []) for n in normal_cifra(c_) if n not in en_cita]
                if faltan:
                    r["motivos"].append(f"{dominio(url)}: la cita no contiene {faltan}")
                    continue
                if not cita_habla_del_hecho(cita, h):
                    r["motivos"].append(f"{dominio(url)}: la cita tiene la cifra pero no habla del dato")
                    continue
                r["buenas"].setdefault(dominio(url), {"url": url, "cita": cita})

    comprobar(hechos)
    cortos = [h for h in hechos if len(resultado[h["id"]]["buenas"]) < 2]
    pendientes = sorted({f["url"] for h in cortos for f in h["fuentes"] if f["url"] in fallos})[:15]
    if pendientes:
        print(f"  {len(pendientes)} páginas bloqueadas para este PC: se piden a {ia.nombre}...", flush=True)
        remotas = ia.descargar_remoto(pendientes)
        for u, t in remotas.items():
            if u in fallos and t and len(t) >= 400:
                paginas[u] = t
                fallos.pop(u)
        if remotas:
            comprobar(cortos)
    verificados, rechazados = [], []
    for h in hechos:
        r = resultado[h["id"]]
        motivos = r["motivos"] + [f"{dominio(f['url'])}: no se pudo descargar ({fallos[f['url']]})"
                                  for f in h["fuentes"] if f["url"] in fallos]
        registro = {**h, "respaldos": list(r["buenas"].values())}
        if len(r["buenas"]) >= 2:
            verificados.append(registro)
        else:
            registro["motivo_rechazo"] = f"{len(r['buenas'])} fuente(s) válida(s) de 2 necesarias. " + "; ".join(motivos[:5])
            rechazados.append(registro)
    print(f"  {len(verificados)} verificados, {len(rechazados)} descartados", flush=True)
    return verificados, rechazados


def escribir(ia, tema, verificados, segundos, idioma, correcciones=""):
    print("3/4 Escribiendo el guion solo con hechos verificados..." if not correcciones else
          "3/4 Corrigiendo el guion...", flush=True)
    lista = "\n".join(f"{h['id']}: {h['afirmacion']} (figures: {', '.join(h.get('cifras', [])) or 'none'})"
                      for h in verificados)
    instr = INSTR_ESCRIBIR.format(segundos=segundos, palabras=int(segundos * 2.6),
                                  idioma=config.IDIOMAS.get(idioma, "English"), hechos=lista)
    entrada = f"Topic: {tema}" + (f"\n\nYOUR PREVIOUS DRAFT BROKE THESE RULES, fix them:\n{correcciones}" if correcciones else "")
    guion, _ = ia.llamar("escribir", entrada, instr, ESQ_GUION)
    return guion


PROHIBIDO_VISUAL = re.compile(
    r"\b(signs?|signage|storefronts?|shopfronts?|logos?|labels?|posters?|banners?|billboards?|marquees?|neon|"
    r"newspapers?|headlines?|magazines?|documents?|papers?|paperwork|contracts?|books?|letters?|text|typography|"
    r"words?|writing|handwriting|screens?|monitors?|computers?|laptops?|phones?|smartphones?|keyboards?|"
    r"typewriters?|tickers?|charts?|graphs?|hands?|fingers?|fists?|faces?|typing|handshake|signing)\b", re.I)
PALABRAS_POR_PLANO = 9

LOOKS = [(1969, "vintage 35mm film, 1960s color stock, heavy natural grain"),
         (1979, "shot on 35mm Kodak film, 1970s color stock, warm faded tones, natural film grain"),
         (1989, "shot on 35mm Kodak film, 1980s color stock, warm slightly faded tones, soft natural film grain"),
         (1999, "shot on 35mm film, 1990s color grading, natural fine grain"),
         (2009, "shot on 35mm film, early-2000s cool color grading, fine grain"),
         (9999, "shot on 16mm documentary film, muted desaturated color grade, fine natural grain")]


def look_de_epoca(texto):
    m = re.search(r"\b(1[89]\d\d|20\d\d)\b", texto or "")
    if not m:
        return ""
    anio = int(m.group(1))
    return next(look for tope, look in LOOKS if anio <= tope)


def con_look(prompt, ambientacion):
    look = look_de_epoca(ambientacion) or look_de_epoca(prompt)
    return prompt if not look or look in prompt else f"{prompt.rstrip(', .')}, {look}"


CINETICO = re.compile(r"fast|rapid|snap|crash|whip|rush|falling|drops?|plunge|hurtl", re.I)


def problemas_visuales(plano):
    """Palabras que llevan a texto ilegible o manos deformes en la imagen/vídeo."""
    texto = f"{plano.get('prompt_imagen', '')} {plano.get('prompt_video', '')}"
    return sorted({m.lower() for m in PROHIBIDO_VISUAL.findall(texto)})


def validar(guion, verificados):
    """Lista de problemas (vacía = guion válido)."""
    ids = {h["id"]: h for h in verificados}
    problemas = []
    for e, esc in enumerate(guion.get("escenas", []), 1):
        if not esc.get("planos"):
            problemas.append(f"Escena {e}: no tiene planos")
        for k, q in enumerate(esc.get("planos", []), 1):
            malas = problemas_visuales(q)
            if malas:
                problemas.append(f"Escena {e}, plano {k}: los prompts mencionan {malas} (prohibido: nada con letras, "
                                 "pantallas, manos ni caras; ni siquiera para decir que no se vean)")
        palabras = sum(len(f.get("texto", "").split()) for f in esc.get("frases", []))
        if esc.get("planos") and len(esc["planos"]) < math.ceil(palabras / PALABRAS_POR_PLANO):
            problemas.append(f"Escena {e}: {palabras} palabras de narración con solo {len(esc['planos'])} planos; "
                             f"necesita al menos {math.ceil(palabras / PALABRAS_POR_PLANO)} (si no, los clips van a "
                             "cámara lenta)")
        if e == 1 and esc.get("planos") and not CINETICO.search(esc["planos"][0].get("prompt_video", "")):
            problemas.append("Escena 1, plano 1 (el gancho): prompt_video necesita un movimiento enérgico "
                             "(fast push-in, rapid dolly in, snap zoom in, object falling toward the camera)")
        for f in esc.get("frases", []):
            texto, usados = f.get("texto", ""), f.get("hechos", [])
            desconocidos = [h for h in usados if h not in ids]
            if desconocidos:
                problemas.append(f"«{texto[:60]}»: usa hechos no verificados {desconocidos}")
            permitidas = set(n for h in usados if h in ids for c in ids[h].get("cifras", []) for n in normal_cifra(c))
            dichas = cifras_dichas(texto)
            sospechosas = {n for n in dichas if not (len(n) == 1 and "." not in n)} - permitidas
            sospechosas = {n for n in sospechosas if not any(n.startswith(p.replace(".", "")) or n == p for p in permitidas)}
            if sospechosas:
                problemas.append(f"«{texto[:60]}»: dice cifras que no están en sus hechos: {sorted(sospechosas)}")
            if not usados and dichas - {n for n in dichas if len(n) == 1}:
                problemas.append(f"«{texto[:60]}»: da cifras sin citar ningún hecho")
    return problemas


ESTILO_DOCUMENTAL = ("Cinematic corporate documentary B-roll, photorealistic, anamorphic lens, dark moody lighting, "
                     "high contrast, dramatic shadows, shallow depth of field, film grain, no text, no logos.")


def crear_proyecto(nombre, tema, guion, verificados, rechazados, citadas, idioma, estilo, voz_de):
    import nuevo_proyecto
    carpeta = nuevo_proyecto.crear(nombre, "business documentary (verified facts)", idioma, "9:16", guion["titulo"], True,
                                   avisar=False)
    p = json.loads((carpeta / "proyecto.json").read_text(encoding="utf-8"))
    p.update({"estilo": estilo, "personajes": [], "tema": tema, "guion_ia": True})
    lineas_guion, planos = [f"# {guion['titulo']}", ""], []
    for n, esc in enumerate(guion["escenas"], 1):
        titulo = f"Escena {n}: {esc['titulo_es']}"
        frases = [f["texto"].strip() for f in esc["frases"] if f.get("texto", "").strip()]
        lineas_guion += [f"## {titulo}", esc["descripcion_es"].strip()]
        if frases:
            lineas_guion.append(f'Narrador: "{" ".join(frases)}"')
        lineas_guion.append("")
        pl = esc["planos"]
        reparto = [[] for _ in pl]
        for k, fr in enumerate(frases):
            reparto[min(k, len(pl) - 1)].append(fr)
        for q, texto in zip(pl, reparto):
            planos.append({
                "id": f"P{len(planos) + 1:03d}", "escena": titulo, "ambientacion": esc["ambientacion_en"],
                "duracion_s": min(5.0, max(3.0, float(q.get("duracion_s", 4)))), "tipo_plano": q["tipo_plano"],
                "personajes": [], "accion": q["accion_es"], "dialogo": None,
                "narracion": " ".join(texto) or None,
                "prompt_imagen": con_look(q["prompt_imagen"], esc["ambientacion_en"]),
                "prompt_video": q["prompt_video"], "sonido": q["sonido_es"]})
    (carpeta / "guion.md").write_text("\n".join(lineas_guion), encoding="utf-8")
    (carpeta / "planos.json").write_text(json.dumps(planos, ensure_ascii=False, indent=2), encoding="utf-8")
    (carpeta / "proyecto.json").write_text(json.dumps(p, ensure_ascii=False, indent=2), encoding="utf-8")
    (carpeta / "investigacion.json").write_text(json.dumps(
        {"tema": tema, "fecha": datetime.now().isoformat(timespec="seconds"), "urls_citadas_por_la_busqueda": citadas,
         "verificados": verificados, "descartados": rechazados, "guion": guion}, ensure_ascii=False, indent=2), encoding="utf-8")
    fuentes = sorted({(r["url"], dominio(r["url"])) for h in verificados for r in h["respaldos"]})
    (carpeta / "fuentes.json").write_text(json.dumps(verificados, ensure_ascii=False, indent=2), encoding="utf-8")
    (carpeta / "fuentes.md").write_text(
        "Sources:\n" + "\n".join(f"- {d}: {u}" for u, d in fuentes) + "\n", encoding="utf-8")
    (carpeta / "aprobacion.json").write_text(json.dumps(
        {"estado": "pendiente", "motivo": "guion generado por IA: revisa el texto y sus fuentes antes de producir",
         "fecha": datetime.now().isoformat(timespec="seconds")}, ensure_ascii=False, indent=2), encoding="utf-8")
    if voz_de:  # narrador fijo de la serie: se copia la voz ya elegida de otro proyecto
        origen = config.PROYECTOS / voz_de / "voces"
        if (origen / "Narrador.json").exists():
            (carpeta / "voces").mkdir(exist_ok=True)
            ficha = json.loads((origen / "Narrador.json").read_text(encoding="utf-8"))
            shutil.copy2(origen / "Narrador.wav", carpeta / "voces" / "Narrador.wav")
            ficha["wav"] = str(carpeta / "voces" / "Narrador.wav")
            (carpeta / "voces" / "Narrador.json").write_text(json.dumps(ficha, ensure_ascii=False, indent=2), encoding="utf-8")
            print(f"  voz del narrador copiada de {voz_de}", flush=True)
        else:
            print(f"  aviso: {voz_de} no tiene voz de Narrador elegida", flush=True)
    return carpeta, len(planos), len(fuentes)


def crear(nombre, tema, segundos=60, idioma="en", estilo=ESTILO_DOCUMENTAL, voz_de=None, ia=None, reusar=True,
          elegido=None):
    if (config.PROYECTOS / nombre).exists():
        raise SystemExit(f"Ya existe el proyecto {nombre}")
    base = config.SALIDAS / nombre
    ia = ia or proveedor(registro=base / "gastos_ia.jsonl", elegido=elegido)
    gasto = lambda: f"~${ia.reg.gasto:.3f}"
    borrador = base / "investigacion_borrador.json"
    previa = json.loads(borrador.read_text(encoding="utf-8")) if borrador.exists() else {}
    if reusar and previa.get("tema") == tema and previa.get("hechos"):
        hechos, citadas = previa["hechos"], previa["citadas"]
        print(f"1/4 Reutilizando la investigación guardada ({len(hechos)} hechos, sin gasto)", flush=True)
    else:
        hechos, citadas = investigar(ia, tema)
        base.mkdir(parents=True, exist_ok=True)
        borrador.write_text(json.dumps({"tema": tema, "hechos": hechos, "citadas": citadas},
                                       ensure_ascii=False, indent=2), encoding="utf-8")
    ruta_ver = base / "verificacion.json"
    ver_previa = json.loads(ruta_ver.read_text(encoding="utf-8")) if ruta_ver.exists() else {}
    if reusar and ver_previa.get("tema") == tema and ver_previa.get("hechos") == hechos:
        verificados, rechazados = ver_previa["verificados"], ver_previa["descartados"]
        print(f"2/4 Reutilizando la verificación guardada ({len(verificados)} verificados, sin gasto)", flush=True)
    else:
        verificados, rechazados = verificar(ia, hechos)
        base.mkdir(parents=True, exist_ok=True)
        ruta_ver.write_text(json.dumps({"tema": tema, "hechos": hechos, "verificados": verificados,
                                        "descartados": rechazados}, ensure_ascii=False, indent=2), encoding="utf-8")
    if len(verificados) < 4:
        raise SystemExit(f"Solo {len(verificados)} hechos verificados (mínimo 4): el tema no tiene fuentes suficientes. "
                         f"Nada creado (motivos en {base / 'verificacion.json'}). Gasto: {gasto()}")
    guion = escribir(ia, tema, verificados, segundos, idioma)
    problemas = validar(guion, verificados)
    if problemas:
        print("  El guion rompió reglas; se pide una corrección:\n   - " + "\n   - ".join(problemas), flush=True)
        guion = escribir(ia, tema, verificados, segundos, idioma, "\n".join(problemas))
        problemas = validar(guion, verificados)
    print("4/4 Validación: " + ("OK" if not problemas else "FALLA"), flush=True)
    if problemas:
        base.mkdir(parents=True, exist_ok=True)
        (base / "guion_rechazado.json").write_text(json.dumps({"problemas": problemas, "guion": guion},
                                                              ensure_ascii=False, indent=2), encoding="utf-8")
        raise SystemExit("El guion seguía usando datos no verificados; NO se ha creado el proyecto:\n - "
                         + "\n - ".join(problemas) + f"\nGasto: {gasto()}")
    carpeta, n_planos, n_fuentes = crear_proyecto(nombre, tema, guion, verificados, rechazados, citadas, idioma,
                                                  estilo, voz_de)
    palabras = sum(len(f["texto"].split()) for e in guion["escenas"] for f in e["frases"])
    print(f"\nProyecto {nombre}: {len(guion['escenas'])} escenas, {n_planos} planos, ~{palabras} palabras, "
          f"{len(verificados)} hechos verificados con {n_fuentes} fuentes. Gasto: {gasto()} "
          f"({ia.reg.busquedas} búsquedas, {ia.nombre}).")
    print("Queda PENDIENTE DE APROBACIÓN: revisa guion.md y fuentes.md y apruébalo (web, Telegram o 'aprobar').")
    return carpeta


def aprobar(nombre, quien="consola"):
    ruta = config.PROYECTOS / nombre / "aprobacion.json"
    if not ruta.exists():
        raise SystemExit(f"{nombre} no tiene guion pendiente de aprobación")
    datos = json.loads(ruta.read_text(encoding="utf-8"))
    datos.update({"estado": "aprobado", "por": quien, "fecha_aprobacion": datetime.now().isoformat(timespec="seconds")})
    ruta.write_text(json.dumps(datos, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"{nombre}: guion aprobado")


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    c = sub.add_parser("crear")
    c.add_argument("proyecto")
    c.add_argument("tema")
    c.add_argument("--segundos", type=int, default=60)
    c.add_argument("--idioma", default="en", choices=list(config.IDIOMAS))
    c.add_argument("--estilo", default=ESTILO_DOCUMENTAL)
    c.add_argument("--voz-de", help="copiar la voz del Narrador ya elegida en otro proyecto (narrador fijo de la serie)")
    c.add_argument("--nueva-investigacion", action="store_true",
                   help="no reutilizar la investigación guardada de un intento anterior del mismo proyecto")
    c.add_argument("--proveedor", choices=["auto", "claude", "gemini"],
                   help="claude = API de Anthropic; gemini = API de Google; auto = claude si hay clave, si no gemini")
    a_ = sub.add_parser("aprobar")
    a_.add_argument("proyecto")
    a = ap.parse_args()
    if a.cmd == "crear":
        crear(a.proyecto, a.tema, a.segundos, a.idioma, a.estilo, a.voz_de, reusar=not a.nueva_investigacion,
              elegido=a.proveedor)
    else:
        aprobar(a.proyecto)


if __name__ == "__main__":
    main()
