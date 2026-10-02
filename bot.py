"""Bot de Telegram de drama-ia: controla todo desde el móvil."""
import asyncio
import http.client
import json
import re
import subprocess
import time
from datetime import datetime
from pathlib import Path

from telegram import InlineKeyboardButton as Boton, InlineKeyboardMarkup as Teclado, Update
from telegram.ext import Application, CallbackQueryHandler, CommandHandler, ContextTypes, MessageHandler, filters

import config
import evaluar

RAIZ = Path(__file__).resolve().parent
PY_COMFY = config.PY_COMFY
ESTADO = RAIZ / "bot_estado.json"
LIMITE_TELEGRAM = 49 * 1024 * 1024  # los bots pueden enviar archivos de hasta 50 MB
SECRETOS = config.leer_secretos()
CHAT = SECRETOS.get("TELEGRAM_CHAT_ID", "").strip()
AVISADO = {"clave": None}  # último fallo ya contado por el propio bot (para no avisar dos veces)
AYUDA = ("/topic <tema> [| segundos] — la IA (Claude) investiga, verifica cada dato en 2 fuentes y escribe el guion\n"
         "/estado — trabajo en marcha y GPU\n/proyectos — elegir un proyecto con botones (o escribe su nombre)\n"
         "/narrador <proyecto> — de qué proyecto se copia la voz del narrador\n"
         "/cancelar — cancela el trabajo en marcha\n/apagar — apaga ComfyUI y Ollama (libera la GPU)")


def api(ruta, datos=None, timeout=60):
    c = http.client.HTTPConnection("127.0.0.1", 8190, timeout=timeout)
    try:
        c.request("POST" if datos is not None else "GET", ruta, json.dumps(datos) if datos is not None else None,
                  {"Content-Type": "application/json"})
        r = c.getresponse()
        cuerpo = json.loads(r.read() or b"{}")
    finally:
        c.close()
    if r.status >= 400 or (isinstance(cuerpo, dict) and cuerpo.get("error")):
        raise RuntimeError(cuerpo.get("error", r.status))
    return cuerpo


def interfaz_viva():
    try:
        api("/api/trabajo", timeout=3)
        return True
    except Exception:
        return False


def asegurar_interfaz():
    if interfaz_viva():
        return
    subprocess.Popen([PY_COMFY, "interfaz.py", "--sin-navegador"], cwd=RAIZ, creationflags=subprocess.CREATE_NO_WINDOW)
    for _ in range(40):
        time.sleep(1)
        if interfaz_viva():
            return
    raise RuntimeError("No pude arrancar la interfaz web")


def asegurar_servicios():
    """ComfyUI (imagen/vídeo/sonido) y Ollama (plan de sonido local) encendidos."""
    for s in ("comfyui", "ollama"):
        api("/api/servicios", {"servicio": s, "accion": "arrancar"})
    for _ in range(180):  # la interfaz reintenta sola si Smart App Control bloquea torch un momento
        estado = api("/api/servicios")
        if all(estado.values()):
            return
        time.sleep(2)
    caidos = [s for s, ok in estado.items() if not ok]
    pista = ""
    log = RAIZ / "setup" / "comfyui.log"
    if "comfyui" in caidos and log.exists():
        texto = log.read_text(encoding="utf-8", errors="replace")
        if "4551" in texto:
            pista = ("\nSmart App Control de Windows bloqueó torch.dll varias veces seguidas. Suele ser pasajero: "
                     "vuelve a intentarlo en unos minutos.")
        else:
            pista = "\nÚltimas líneas del registro de ComfyUI:\n" + "\n".join(texto.strip().splitlines()[-4:])[-600:]
    raise RuntimeError(f"No arrancó {' ni '.join(caidos)} en 6 minutos.{pista}")


def gpu_ocupada_por_otro():
    """MB de VRAM en uso si NUESTRO ComfyUI está apagado (entonces lo usa otro programa, p. ej. Wan-Studio)."""
    try:
        if api("/api/servicios").get("comfyui"):
            return 0
        mb = int(subprocess.run(["nvidia-smi", "--query-gpu=memory.used", "--format=csv,noheader,nounits"],
                                capture_output=True, text=True, timeout=10).stdout.split()[0])
        return mb if mb > 4000 else 0
    except Exception:
        return 0


def leer_estado():
    try:
        return json.loads(ESTADO.read_text(encoding="utf-8"))
    except Exception:
        return {}


def guardar_estado(e):
    ESTADO.write_text(json.dumps(e, ensure_ascii=False, indent=2), encoding="utf-8")


def narrador_por_defecto():
    """Voz del narrador de la serie: la elegida con /narrador o, si no, el proyecto más reciente que tenga una."""
    elegido = leer_estado().get("narrador_de")
    if elegido and (config.PROYECTOS / elegido / "voces" / "Narrador.json").exists():
        return elegido
    con_voz = [p for p in config.PROYECTOS.iterdir() if (p / "voces" / "Narrador.json").exists()]
    return max(con_voz, key=lambda p: (p / "voces" / "Narrador.json").stat().st_mtime).name if con_voz else None


def autorizado(update: Update):
    return CHAT and str(update.effective_chat.id) == CHAT


async def rechazar(update: Update):
    quien = update.effective_chat.id
    print(f"Mensaje de un chat NO autorizado (id {quien}). Si eres tú, pon TELEGRAM_CHAT_ID={quien} en "
          f"secretos.env y reinicia el bot.", flush=True)
    if update.effective_message:
        await update.effective_message.reply_text(
            f"Este bot es privado. Tu id de chat es {quien}.\nSi eres el dueño, ponlo en secretos.env como "
            f"TELEGRAM_CHAT_ID={quien} y reinicia el bot.")


def solo_dueño(fn):
    async def envoltorio(update: Update, context: ContextTypes.DEFAULT_TYPE):
        if not autorizado(update):
            return await rechazar(update)
        try:
            return await fn(update, context)
        except Exception as e:  # p. ej. "Ya hay un trabajo en marcha": que se entere en el chat
            await update.effective_message.reply_text(f"⚠️ {e}")
    return envoltorio


def nombre_para(tema):
    base = re.sub(r"[^a-z0-9]+", "-", tema.lower()).strip("-")[:28].strip("-") or "video"
    return f"{base}-{datetime.now():%m%d-%H%M}"


async def esperar_trabajo(mensaje, titulo):
    """Sigue el trabajo en marcha editando un mensaje de estado. Devuelve el estado final."""
    ultimo_txt, ultimo_t = "", 0
    while True:
        t = await asyncio.to_thread(api, "/api/trabajo")
        linea = (t["log"][-1] if t["log"] else "")[:180]
        txt = f"⏳ {titulo} · {t['segundos'] // 60}:{t['segundos'] % 60:02d}\n{linea}"
        if not t["activo"] and t["codigo"] is not None:  # codigo None = aún arrancando
            return t
        if txt != ultimo_txt and time.time() - ultimo_t > 6:  # Telegram limita las ediciones
            try:
                await mensaje.edit_text(txt)
            except Exception:
                pass
            ultimo_txt, ultimo_t = txt, time.time()
        await asyncio.sleep(3)


async def lanzar(update_msg, n, paso, opciones, titulo):
    await asyncio.to_thread(asegurar_interfaz)
    await asyncio.to_thread(api, f"/api/proyecto/{n}/ejecutar", {"paso": paso, "opciones": opciones})
    estado = await update_msg.reply_text(f"⏳ {titulo}…")
    t = await esperar_trabajo(estado, titulo)
    if t["codigo"] != 0:
        AVISADO["clave"] = (t["paso"], t["proyecto"], t["segundos"])
        cola = "\n".join(t["log"][-8:])[-1500:]
        await estado.edit_text(f"⚠️ {titulo}: {'cancelado' if t['cancelado'] else 'error'}\n\n{cola}")
        return False, t
    await estado.edit_text(f"✅ {titulo} ({t['segundos'] // 60}:{t['segundos'] % 60:02d})")
    return True, t


def botones_borrador(n):
    return Teclado([[Boton("✅ Render final", callback_data=f"final|{n}")],
                    [Boton("🔄 Regenerar un plano", callback_data=f"regen|{n}"),
                     Boton("❌ Cancelar proyecto", callback_data=f"descartar|{n}")]])


def copia_para_telegram(ruta: Path):
    """Los bots solo pueden mandar 50 MB: copia más comprimida (misma resolución) solo para el móvil."""
    copia = ruta.with_name(ruta.stem + "_telegram.mp4")
    if not copia.exists() or copia.stat().st_mtime < ruta.stat().st_mtime:
        subprocess.run([PY_COMFY, "copia_ligera.py", str(ruta), "--mb", "48"], cwd=RAIZ, capture_output=True,
                       timeout=1800, creationflags=subprocess.CREATE_NO_WINDOW)
    return copia


async def enviar_video(msg, ruta: Path, texto, teclado=None):
    if not ruta or not ruta.exists():
        return await msg.reply_text(f"{texto}\n(no encuentro el vídeo)", reply_markup=teclado)
    if ruta.stat().st_size > LIMITE_TELEGRAM:
        await msg.reply_text(f"Pesa {ruta.stat().st_size / 1e6:.0f} MB (Telegram admite 50): preparo una copia "
                             "comprimida para el móvil. El original para YouTube sigue en el PC.")
        copia = await asyncio.to_thread(copia_para_telegram, ruta)
        if not copia.exists() or copia.stat().st_size > LIMITE_TELEGRAM:
            return await msg.reply_text(f"{texto}\nNo pude dejarlo en menos de 50 MB: está en {ruta}", reply_markup=teclado)
        texto += f"\n(copia comprimida para Telegram; original en {ruta.name})"
        ruta = copia
    with open(ruta, "rb") as f:
        await msg.reply_video(f, caption=texto, supports_streaming=True, reply_markup=teclado,
                              read_timeout=600, write_timeout=600)


@solo_dueño
async def cmd_ayuda(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(AYUDA)


@solo_dueño
async def cmd_topic(update: Update, context: ContextTypes.DEFAULT_TYPE):
    texto = " ".join(context.args).strip()
    if not texto:
        return await update.message.reply_text("Uso: /topic The fall of Blockbuster   (opcional: | 45 segundos)")
    tema, _, seg = texto.partition("|")
    segundos = int(re.sub(r"\D", "", seg) or 60)
    await nuevo_guion(update.message, tema.strip(), max(20, min(180, segundos)))


async def nuevo_guion(msg, tema, segundos, n=None, proveedor=None):
    n = n or nombre_para(tema)
    voz = narrador_por_defecto()
    await msg.reply_text(f"🔎 Investigando «{tema}» en internet y verificando cada dato en 2 fuentes…\n"
                         f"Proyecto: {n} · {segundos} s · narrador: {voz or 'sin elegir'}")
    opciones = {"tema": tema, "segundos": segundos, "idioma": "en", "voz_de": voz or ""}
    if proveedor:
        opciones["proveedor"] = proveedor
    ok, t = await lanzar(msg, n, "guion_ia", opciones, "Guion con datos verificados")
    if not ok:
        return
    gasto = next((l for l in reversed(t["log"]) if "Gasto:" in l), "")
    await enviar_guion(msg, n, gasto)
    await msg.reply_text("¿Qué hago?", reply_markup=Teclado([
        [Boton("✅ Aprobar y producir", callback_data=f"aprobar|{n}")],
        [Boton("🔁 Otro guion", callback_data=f"otro|{n}"), Boton("❌ Descartar", callback_data=f"descartar|{n}")]]))


async def enviar_guion(msg, n, gasto=""):
    """Texto de la narración por escenas + el archivo de fuentes (si lo hay)."""
    d = await asyncio.to_thread(api, f"/api/proyecto/{n}")
    guion = "\n\n".join(f"🎬 {p['escena'].split(': ', 1)[-1]}\n" + " ".join(x["narracion"] for x in d["planos"]
                         if x["escena"] == p["escena"] and x.get("narracion"))
                         for p in {x["escena"]: x for x in d["planos"]}.values())
    await msg.reply_text(f"📝 {d['proyecto']['titulo']}\n\n{guion or '(sin planos todavía)'}"[:3900])
    fuentes = config.PROYECTOS / n / "fuentes.md"
    if fuentes.exists():
        with open(fuentes, "rb") as f:
            await msg.reply_document(f, filename=f"fuentes_{n}.md", caption=(
                f"{len(d.get('hechos') or [])} datos verificados, cada uno en 2 fuentes. {gasto.strip()}").strip())


def videos_de(n):
    """(borrador, final) que existan, o None."""
    m = config.SALIDAS / n / "montaje"
    borrador = next((m / f for f in (f"{n}_subtitulado.mp4", f"{n}_con_sonido.mp4") if (m / f).exists()), None)
    final = next((m / f for f in (f"{n}_subtitulado_720_x2.mp4", f"{n}_con_sonido_720_x2.mp4") if (m / f).exists()), None)
    return borrador, final


async def ficha(msg, n):
    """Estado de un proyecto y botones con lo que se puede hacer ahora."""
    d = await asyncio.to_thread(api, f"/api/proyecto/{n}")
    p = d["proyecto"]
    aprob = (d.get("aprobacion") or {}).get("estado")
    borrador, final = videos_de(n)
    lineas = [f"📁 {n}", f"{p['titulo']} · {p.get('formato', '16:9')} · {p.get('idioma', '')} · {len(d['planos'])} planos"]
    if aprob == "pendiente":
        lineas.append("⏸ Guion pendiente de tu aprobación")
    lineas.append(f"Borrador: {'sí' if borrador else 'no'} · Final 1080p: {'sí' if final else 'no'}")
    if borrador:
        ok_std, motivo, _ = evaluar.estandar(n, "final")
        lineas.append("✅ Listo para render final" if ok_std else f"⛔ Render final: {motivo}")
    if final:
        ok_pub, motivo, _ = evaluar.estandar(n, "publicar")
        lineas.append("✅ LISTO PARA PUBLICAR" if ok_pub else f"⛔ Publicar: {motivo}")
    filas = [[Boton("📝 Ver guion", callback_data=f"guion|{n}")]]
    if aprob == "pendiente":
        filas.append([Boton("✅ Aprobar y producir", callback_data=f"aprobar|{n}")])
    else:
        filas.append([Boton("🚀 Producir lo que falte" if not borrador else "🚀 Rehacer lo que haya cambiado",
                            callback_data=f"producir|{n}")])
    if borrador:
        filas.append([Boton("🎞 Ver borrador", callback_data=f"verb|{n}"), Boton("✅ Render final", callback_data=f"final|{n}")])
        filas.append([Boton("🔄 Regenerar un plano", callback_data=f"regen|{n}"),
                      Boton("📊 Evaluar", callback_data=f"evaluar|{n}")])
    if final:
        filas.append([Boton("🏁 Ver final", callback_data=f"verf|{n}")])
    filas.append([Boton("🗑 Mandar a la papelera", callback_data=f"descartar?|{n}")])
    await msg.reply_text("\n".join(lineas), reply_markup=Teclado(filas))


async def lista_proyectos(msg):
    await asyncio.to_thread(asegurar_interfaz)
    ps = (await asyncio.to_thread(api, "/api/proyectos"))["proyectos"]
    ps = [p for p in ps if len(f"ver|{p['nombre']}".encode()) <= 64]  # límite de Telegram para los botones
    if not ps:
        return await msg.reply_text("No hay proyectos. Crea uno con /topic <tema>.")
    mtime = lambda p: (config.PROYECTOS / p["nombre"]).stat().st_mtime if (config.PROYECTOS / p["nombre"]).exists() else 0
    ps = sorted(ps, key=mtime, reverse=True)[:30]
    await msg.reply_text("Elige un proyecto (los más recientes primero):", reply_markup=Teclado(
        [[Boton(f"{p['nombre']} ({p['formato']})", callback_data=f"ver|{p['nombre']}")] for p in ps]))


async def producir(msg, n, forzar=False):
    ocupada = await asyncio.to_thread(gpu_ocupada_por_otro)
    if ocupada and not forzar:
        return await msg.reply_text(
            f"⚠️ La GPU ya tiene {ocupada / 1024:.1f} GB en uso por otro programa (¿Wan-Studio?). Si produzco ahora irá "
            "lento o fallará por memoria.", reply_markup=Teclado([
                [Boton("Producir igualmente", callback_data=f"forzar|{n}"), Boton("Esperar", callback_data=f"nada|{n}")]]))
    await msg.reply_text("🚀 Encendiendo ComfyUI y produciendo (imágenes, vídeo, voz, sonido)…")
    await asyncio.to_thread(asegurar_servicios)
    ok, _ = await lanzar(msg, n, "orquestar", {}, "Producción")
    if not ok:
        return
    await enviar_borrador(msg, n)


async def enviar_borrador(msg, n):
    p = config.cargar_proyecto(n)
    if p["subtitulos"]:
        await lanzar(msg, n, "subtitulos", {"fuente": "videos"}, "Subtítulos")
    m = config.SALIDAS / n / "montaje"
    video = m / f"{n}_subtitulado.mp4" if (m / f"{n}_subtitulado.mp4").exists() else m / f"{n}_con_sonido.mp4"
    await enviar_video(msg, video, f"🎞 Borrador 480p · {p['titulo']}", botones_borrador(n))
    await enviar_evaluacion(msg, n, video)


async def enviar_evaluacion(msg, n, video=None):
    """3er modelo: Gemini (gratis) ve el vídeo con audio y lo puntúa con prompt_evaluacion.txt."""
    if not config.leer_secretos().get("GEMINI_API_KEY"):
        return
    aviso = await msg.reply_text("📊 Gemini está evaluando el vídeo (1-2 min)…")
    try:
        informe, notas, _ = await asyncio.to_thread(evaluar.evaluar, n, video)
    except BaseException as e:  # SystemExit incluido: que no tumbe el bot
        return await aviso.edit_text(f"📊 No se pudo evaluar: {e}")
    hist = evaluar.historial(n)
    fase = evaluar.fase_de(hist[-1])
    previas = [h for h in hist[:-1] if evaluar.fase_de(h) == fase]  # comparar borrador con borrador, final con final
    antes = f"\nAntes: {evaluar.resumen_notas(previas[-1]['notas'])}" if previas else ""
    para = "final" if fase == "borrador" else "publicar"
    ok_std, motivo, _ = evaluar.estandar(n, para)
    exige = evaluar.texto_meta(evaluar.EXIGE[para])
    if para == "final":
        veredicto = ("\n✅ Listo para render final." if ok_std else
                     f"\n⛔ Aún no para render final (meta {exige}): {motivo}.")
    else:
        veredicto = ("\n✅ LISTO PARA PUBLICAR." if ok_std else
                     f"\n⛔ Aún no para publicar (meta {exige}): {motivo}.")
    titulo = "Borrador" if fase == "borrador" else "Versión final 1080p"
    await aviso.edit_text(f"📊 {titulo}: {evaluar.resumen_notas(notas)}{antes}{veredicto}")
    await msg.reply_text(informe[:3900])


@solo_dueño
async def cmd_estado(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await asyncio.to_thread(interfaz_viva):
        return await update.message.reply_text("La interfaz web está apagada (se enciende sola al pedir algo).")
    t = await asyncio.to_thread(api, "/api/trabajo")
    s = await asyncio.to_thread(api, "/api/servicios")
    try:
        gpu = subprocess.run(["nvidia-smi", "--query-gpu=memory.used,utilization.gpu,temperature.gpu",
                              "--format=csv,noheader"], capture_output=True, text=True, timeout=10).stdout.strip()
    except Exception:
        gpu = "?"
    trabajo = (f"{'▶' if t['activo'] else '■'} {t['paso']} · {t['proyecto']} · {t['segundos'] // 60}:{t['segundos'] % 60:02d}"
               f"\n{t['log'][-1] if t['log'] else ''}") if t["paso"] else "sin trabajos"
    await update.message.reply_text(f"{trabajo}\n\nComfyUI: {'on' if s['comfyui'] else 'off'} · "
                                    f"Ollama: {'on' if s['ollama'] else 'off'}\nGPU (usada, uso, temp): {gpu}")


@solo_dueño
async def cmd_proyectos(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await lista_proyectos(update.message)


@solo_dueño
async def texto_libre(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Mensaje sin comando: si es (parte de) el nombre de un proyecto, se abre su ficha."""
    completo = update.message.text.strip()
    t = completo.lower().lstrip("•- ").split(" ")[0]
    nombres = [p.name for p in config.PROYECTOS.iterdir() if (p / "proyecto.json").exists()]
    exacto = [x for x in nombres if x.lower() == t]
    parecidos = exacto or [x for x in nombres if t and t in x.lower()]
    if len(completo.split()) <= 2 and len(parecidos) == 1:
        return await ficha(update.message, parecidos[0])
    if len(completo.split()) <= 2 and len(parecidos) > 1:
        return await update.message.reply_text("¿Cuál?", reply_markup=Teclado(
            [[Boton(x, callback_data=f"ver|{x}")] for x in parecidos[:20] if len(f"ver|{x}".encode()) <= 64]))
    await update.message.reply_text("Para manejar proyectos: /proyectos o /topic <tema>.")


@solo_dueño
async def cmd_narrador(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not context.args:
        return await update.message.reply_text(f"Narrador actual: {narrador_por_defecto() or 'ninguno'}.\n"
                                               "Uso: /narrador <proyecto con la voz elegida>")
    n = context.args[0]
    if not (config.PROYECTOS / n / "voces" / "Narrador.json").exists():
        return await update.message.reply_text(f"{n} no tiene una voz de Narrador elegida")
    e = leer_estado()
    e["narrador_de"] = n
    guardar_estado(e)
    await update.message.reply_text(f"Narrador de la serie: la voz de {n}")


@solo_dueño
async def cmd_apagar(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await asyncio.to_thread(interfaz_viva):
        return await update.message.reply_text("Ya está todo apagado.")
    for s in ("comfyui", "ollama"):
        await asyncio.to_thread(api, "/api/servicios", {"servicio": s, "accion": "detener"})
    await update.message.reply_text("ComfyUI y Ollama apagados: la GPU queda libre.")


@solo_dueño
async def cmd_cancelar(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await asyncio.to_thread(api, "/api/trabajo/cancelar", {})
    await update.message.reply_text("Cancelando el trabajo en marcha…")


@solo_dueño
async def botones(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    accion, n, *resto = q.data.split("|")
    msg = q.message
    try:
        await q.edit_message_reply_markup(None)  # evita pulsar dos veces
    except Exception:
        pass
    if accion == "ver":
        await ficha(msg, n)
    elif accion == "guion":
        await enviar_guion(msg, n)
        await ficha(msg, n)
    elif accion == "producir":
        await producir(msg, n)
    elif accion in ("verb", "verf"):
        borrador, final = videos_de(n)
        v = borrador if accion == "verb" else final
        await enviar_video(msg, v, ("🎞 Borrador · " if accion == "verb" else "🏁 Final · ") + n,
                           botones_borrador(n) if accion == "verb" else None)
    elif accion == "evaluar":
        await enviar_evaluacion(msg, n)
        await ficha(msg, n)
    elif accion == "descartar?":
        await msg.reply_text(f"¿Seguro que mando {n} a la papelera? (se puede restaurar desde la web)",
                             reply_markup=Teclado([[Boton("🗑 Sí, a la papelera", callback_data=f"descartar|{n}"),
                                                    Boton("No", callback_data=f"ver|{n}")]]))
    elif accion == "aprobar":
        await asyncio.to_thread(api, f"/api/proyecto/{n}/aprobar", {"por": "telegram"})
        await msg.reply_text("✅ Guion aprobado.")
        await producir(msg, n)
    elif accion == "forzar":
        await producir(msg, n, forzar=True)
    elif accion == "otro":
        tema = config.cargar_proyecto(n).get("tema", "")
        await asyncio.to_thread(api, f"/api/proyecto/{n}/eliminar", {"confirmacion": n})
        await msg.reply_text(f"🗑 {n} a la papelera. Escribo otro guion…")
        await nuevo_guion(msg, tema, 60)
    elif accion == "descartar":
        await asyncio.to_thread(api, f"/api/proyecto/{n}/eliminar", {"confirmacion": n})
        await msg.reply_text(f"🗑 {n} movido a la papelera (se puede restaurar desde la web).")
    elif accion == "final":
        ok_std, motivo, _ = evaluar.estandar(n, "final")
        if not ok_std and resto != ["forzar"]:  # estándar del borrador: la meta en ritmo y audio
            return await msg.reply_text(
                f"⛔ El borrador aún no pasa el estándar para render final: {motivo}.\n"
                f"Hace falta {evaluar.texto_meta(evaluar.EXIGE['final'])} "
                "(la imagen se juzga después, en la versión 1080p).",
                reply_markup=Teclado([[Boton("📊 Evaluar ahora", callback_data=f"evaluar|{n}")],
                                      [Boton("🔄 Regenerar un plano", callback_data=f"regen|{n}")],
                                      [Boton("⚠️ Render final igualmente", callback_data=f"final|{n}|forzar")]]))
        ocupada = await asyncio.to_thread(gpu_ocupada_por_otro)
        if ocupada:
            return await msg.reply_text(f"⚠️ La GPU tiene {ocupada / 1024:.1f} GB en uso por otro programa. "
                                        "Vuelve a pulsar Render final cuando esté libre.", reply_markup=botones_borrador(n))
        await asyncio.to_thread(asegurar_servicios)
        ok, _ = await lanzar(msg, n, "final", {"forzar": True}, "Versión final 1080p (cerca de 1 hora)")
        if ok:
            m = config.SALIDAS / n / "montaje"
            v = next((m / f for f in (f"{n}_subtitulado_720_x2.mp4", f"{n}_con_sonido_720_x2.mp4") if (m / f).exists()),
                     m / f"{n}_con_sonido_720_x2.mp4")
            await enviar_video(msg, v, f"🏁 Versión final · {config.cargar_proyecto(n)['titulo']}")
            await enviar_evaluacion(msg, n, v)  # 2ª fase del estándar: ¿se puede publicar?
            fuentes = (config.PROYECTOS / n / "fuentes.md")
            if fuentes.exists():
                await msg.reply_text("Para la descripción de YouTube:\n\n" + fuentes.read_text(encoding="utf-8")[:3800])
    elif accion == "regen":
        d = await asyncio.to_thread(api, f"/api/proyecto/{n}")
        ids = [f["id"] for f in d["matriz"]]
        filas = [[Boton(i, callback_data=f"regenp|{n}|{i}") for i in ids[k:k + 5]] for k in range(0, len(ids), 5)]
        await msg.reply_text("¿Qué plano regenero?", reply_markup=Teclado(filas))
    elif accion == "regenp":
        pid = resto[0]
        d = await asyncio.to_thread(api, f"/api/proyecto/{n}")
        f = next(x for x in d["matriz"] if x["id"] == pid)
        opciones = [Boton("🖼 Imagen (y su vídeo)", callback_data=f"regenx|{n}|{pid}|imagen"),
                    Boton("🎞 Solo vídeo", callback_data=f"regenx|{n}|{pid}|video")]
        if f.get("voz") or f.get("narr"):
            opciones.append(Boton("🗣 Voz", callback_data=f"regenx|{n}|{pid}|voz"))
        await msg.reply_text(f"{pid}: {f['accion'][:150]}\n¿Qué parte?", reply_markup=Teclado([opciones]))
    elif accion == "regenx":
        pid, parte = resto
        await asyncio.to_thread(asegurar_servicios)
        ok, _ = await lanzar(msg, n, "orquestar", {"planos": pid, "rehacer": parte}, f"Regenerando {parte} de {pid}")
        if ok:
            await enviar_borrador(msg, n)


async def vigilar_fallos(app: Application):
    visto = None
    while True:
        await asyncio.sleep(15)
        try:
            if not await asyncio.to_thread(interfaz_viva):
                continue
            t = await asyncio.to_thread(api, "/api/trabajo")
            clave = (t["paso"], t["proyecto"], t["segundos"]) if not t["activo"] else None
            if (clave and clave != visto and clave != AVISADO["clave"] and t["codigo"] not in (0, None)
                    and not t["cancelado"]):
                visto = clave
                await app.bot.send_message(CHAT, f"🚨 Falló «{t['paso']}» en {t['proyecto']}:\n\n"
                                                 + "\n".join(t["log"][-8:])[-1500:])
            elif clave:
                visto = clave
        except Exception:
            pass


TAREAS = set()


async def al_arrancar(app: Application):
    if not CHAT:
        print("Falta TELEGRAM_CHAT_ID: escríbele al bot y te dirá tu id.", flush=True)
        return
    tarea = asyncio.get_running_loop().create_task(vigilar_fallos(app))
    TAREAS.add(tarea)
    try:
        await app.bot.send_message(CHAT, "🤖 Bot de drama-ia encendido. /ayuda para ver los comandos.")
    except Exception as e:  # noqa: BLE001 - el bot sigue funcionando aunque no pueda saludar
        print(f"\nAVISO: no pude escribirte a Telegram ({e}).\n"
              "  - Telegram no deja que un bot escriba primero: abre el chat con tu bot y pulsa INICIAR o escribe /start.\n"
              "  - Si ya lo hiciste, TELEGRAM_CHAT_ID en secretos.env no es tu id: escríbele al bot y te dirá el correcto.\n"
              "El bot sigue en marcha esperando tus mensajes.\n", flush=True)


def main():
    token = SECRETOS.get("TELEGRAM_TOKEN")
    if not token:
        raise SystemExit(f"Falta TELEGRAM_TOKEN en {config.SECRETOS}")
    app = (Application.builder().token(token).read_timeout(60).write_timeout(600).post_init(al_arrancar).build())
    for nombre, fn in (("start", cmd_ayuda), ("ayuda", cmd_ayuda), ("topic", cmd_topic), ("estado", cmd_estado),
                       ("proyectos", cmd_proyectos), ("narrador", cmd_narrador), ("cancelar", cmd_cancelar),
                       ("apagar", cmd_apagar)):
        app.add_handler(CommandHandler(nombre, fn))
    app.add_handler(CallbackQueryHandler(botones))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, texto_libre))
    print("Bot de drama-ia en marcha (Ctrl+C para salir)")
    app.run_polling(drop_pending_updates=True)


if __name__ == "__main__":
    main()
