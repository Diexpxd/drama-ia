"""Rutas y parámetros compartidos por todos los scripts."""
import os
import sys
from pathlib import Path

RAIZ = Path(__file__).resolve().parent
PROYECTOS = RAIZ / "proyectos"
SALIDAS = Path(os.environ.get("DRAMA_IA_SALIDAS", RAIZ / "salidas"))

COMFY_DIR = Path(os.environ.get("COMFYUI_DIR", Path.home() / "ComfyUI"))
COMFY_URL = "http://127.0.0.1:8188"
COMFY_OUTPUT = SALIDAS / "comfy"
COMFY_INPUT = COMFY_DIR / "input"

PY_COMFY = os.environ.get("DRAMA_IA_PY_COMFY", sys.executable)
PY_VOZ = os.environ.get("DRAMA_IA_PY_VOZ", sys.executable)
PY_WHISPER = os.environ.get("DRAMA_IA_PY_WHISPER", sys.executable)
PY_NUBE = os.environ.get("DRAMA_IA_PY_NUBE", sys.executable)
MODELOS_TTS = Path(os.environ.get("DRAMA_IA_MODELOS_TTS", RAIZ / "modelos_tts"))
OLLAMA_MODELOS = os.environ.get("OLLAMA_MODELS", "")
HF_HOME = os.environ.get("HF_HOME", str(RAIZ / "cache" / "huggingface"))

OLLAMA_URL = "http://127.0.0.1:11434"
OLLAMA_MODELO = "qwen3.5:9b"

IMG_MODELO = "flux-2-klein-4b.safetensors"
IMG_TEXT_ENCODER = "qwen_3_4b.safetensors"
IMG_VAE = "flux2-vae.safetensors"
IMG_ANCHO, IMG_ALTO = 1280, 720
IMG_PASOS = 4

# Vídeo: Wan 2.2 I2V 14B + LoRA lightx2v de 4 pasos (2 pasos por experto).
VID_ALTO_RUIDO = "wan2.2_i2v_high_noise_14B_fp8_scaled.safetensors"
VID_BAJO_RUIDO = "wan2.2_i2v_low_noise_14B_fp8_scaled.safetensors"
VID_LORA_ALTO = "wan2.2_i2v_lightx2v_4steps_lora_v1_high_noise.safetensors"
VID_LORA_BAJO = "wan2.2_i2v_lightx2v_4steps_lora_v1_low_noise.safetensors"
VID_TEXT_ENCODER = "umt5_xxl_fp8_e4m3fn_scaled.safetensors"
VID_VAE = "wan_2.1_vae.safetensors"
VID_FPS = 16
VID_PASOS, VID_CAMBIO = 4, 2
VID_SHIFT = 5.0
VID_MODO = "completo"

# Voz clonada con VoxCPM2.
VOZ_CFG, VOZ_PASOS = 2.5, 25
VID_ANCHO, VID_ALTO = 832, 480

# Campos opcionales de proyecto.json; si faltan se usan estos valores.
GENERO_POR_DEFECTO = "dramas históricos chinos (wuxia/xianxia)"
IDIOMA_POR_DEFECTO = "es"
FORMATO_POR_DEFECTO = "16:9"

# Wan genera a 480p (cabe en 16 GB de VRAM) y SeedVR2 reescala a 1080p.
FORMATOS = {
    "16:9": {"img": (1920, 1088), "vid": (832, 480), "final": (1920, 1080)},
    "9:16": {"img": (720, 1280), "vid": (480, 832), "final": (1080, 1920)},
}

IDIOMAS = {"es": "Spanish", "en": "English", "pt": "Portuguese", "fr": "French", "de": "German",
           "it": "Italian", "ja": "Japanese", "ko": "Korean", "zh": "Chinese", "ru": "Russian"}


def cargar_proyecto(nombre):
    """proyecto.json con los campos opcionales rellenados (genero, idioma, formato)."""
    import json
    p = json.loads((PROYECTOS / nombre / "proyecto.json").read_text(encoding="utf-8"))
    p.setdefault("genero", GENERO_POR_DEFECTO)
    p.setdefault("idioma", IDIOMA_POR_DEFECTO)
    p.setdefault("formato", FORMATO_POR_DEFECTO)
    p.setdefault("ritmo", "normal")          # "narracion": manda la voz (Shorts / documentales narrados)
    p.setdefault("realismo", False)          # cero fantasía y cero anacronismos
    p.setdefault("subtitulos", p["formato"] == "9:16")  # subtítulos quemados en la versión final
    p.setdefault("cama_grave", bool(p.get("guion_ia")))  # drone grave continuo bajo todo (documentales)
    p.setdefault("gancho", bool(p.get("guion_ia")))  # 1er plano: acelerón + empuje digital + golpe sonoro
    if p["formato"] not in FORMATOS:
        raise SystemExit(f"formato '{p['formato']}' no válido; usa uno de {list(FORMATOS)}")
    if p["idioma"] not in IDIOMAS:
        raise SystemExit(f"idioma '{p['idioma']}' no válido; usa uno de {list(IDIOMAS)}")
    return p


SECRETOS = RAIZ / "secretos.env"
GEMINI_MODELOS = {"investigar": "gemini-3.8-flash", "verificar": "gemini-3.8-flash", "escribir": "gemini-3.8-flash",
                  "evaluar": "gemini-3.8-flash"}
# USD por 1 M de tokens (entrada, salida) y por búsqueda de Google.
GEMINI_PRECIOS = {
    "gemini-3.8-flash": {"entrada": 0.75, "salida": 3.75, "entrada_2027": 1.50, "salida_2027": 7.50},
    "gemini-3.1-pro-preview": {"entrada": 2.00, "salida": 12.00},
    "gemini-3.5-flash-lite": {"entrada": 0.30, "salida": 2.50},
}
GEMINI_BUSQUEDA_USD = 14 / 1000       # tras las 5.000 gratis al mes (compartidas entre modelos 3.x)
GEMINI_BUSQUEDAS_GRATIS_MES = 5000

GUIONISTA_PROVEEDOR = "auto"
CLAUDE_MODELOS = {"investigar": "claude-sonnet-5", "verificar": "claude-sonnet-5", "escribir": "claude-sonnet-5"}
CLAUDE_PRECIOS = {
    "claude-sonnet-5": {"entrada": 2.00, "salida": 10.00},
    "claude-haiku-4-5": {"entrada": 1.00, "salida": 5.00},
    "claude-opus-5-5": {"entrada": 4.00, "salida": 20.00},
}
CLAUDE_BUSQUEDA_USD = 10 / 1000       # web search; web fetch no se cobra aparte (solo los tokens)


def leer_secretos():
    """Lee secretos.env (CLAVE=valor por línea). Nunca se imprime ni se sube a ningún sitio."""
    datos = {}
    if SECRETOS.exists():
        for linea in SECRETOS.read_text(encoding="utf-8").splitlines():
            linea = linea.strip()
            if linea and not linea.startswith("#") and "=" in linea:
                k, v = linea.split("=", 1)
                datos[k.strip()] = v.strip().strip('"').strip("'")
    return datos


def claves_gemini():
    """GEMINI_API_KEY primero y luego las de GEMINI_API_KEYS (separadas por comas), sin repetir."""
    s = leer_secretos()
    claves = [s.get("GEMINI_API_KEY", "")] + s.get("GEMINI_API_KEYS", "").split(",")
    return list(dict.fromkeys(c.strip() for c in claves if c.strip()))


def abrir(ruta):
    """Abre un archivo con su programa (vídeo, página...), salvo si lo lanza la interfaz visual,"""
    import os
    if not os.environ.get("DRAMA_IA_NO_ABRIR"):
        os.startfile(ruta)


def dims(proyecto, fase):
    """(ancho, alto) de una fase ('img', 'vid' o 'final') según el formato del proyecto."""
    return FORMATOS[proyecto.get("formato", FORMATO_POR_DEFECTO)][fase]


# Negativo oficial de Wan (en chino, así lo entrenaron).
VID_NEGATIVO = (
    "色调艳丽，过曝，静态，细节模糊不清，字幕，风格，作品，画作，画面，静止，整体发灰，最差质量，低质量，"
    "JPEG压缩残留，丑陋的，残缺的，多余的手指，画得不好的手部，画得不好的脸部，畸形的，毁容的，形态畸形的肢体，"
    "手指融合，静止不动的画面，杂乱的背景，三条腿，背景人很多，倒着走"
)
VID_NEGATIVO_REALISMO = VID_NEGATIVO + "，魔法特效，变形，扭曲，超自然光效，物体漂浮，不符合物理规律的运动，物体变换"
IMG_REALISMO = " Photorealistic documentary photograph, real-world physics, period-accurate details, no fantasy elements."
