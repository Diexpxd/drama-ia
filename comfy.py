"""Cliente mínimo de la API de ComfyUI y los flujos (workflows) del proyecto en formato API."""
import http.client
import json
import random
import shutil
import time
import uuid
from pathlib import Path
from urllib.parse import urlparse

import config


def _peticion(ruta, datos=None, timeout=30):
    url = urlparse(config.COMFY_URL)
    con = http.client.HTTPConnection(url.hostname, url.port, timeout=timeout)
    try:
        if datos is None:
            con.request("GET", ruta)
        else:
            con.request("POST", ruta, json.dumps(datos).encode(), {"Content-Type": "application/json"})
        r = con.getresponse()
        cuerpo = r.read()
    finally:
        con.close()
    if r.status >= 400:
        raise RuntimeError(f"ComfyUI {ruta}: {r.status} {cuerpo.decode(errors='replace')}")
    return json.loads(cuerpo) if cuerpo.strip() else None


def disponible():
    try:
        _peticion("/system_stats", timeout=3)
        return True
    except Exception:
        return False


def liberar_vram():
    """Descarga los modelos de ComfyUI de la GPU (p. ej. antes de usar Ollama)."""
    _peticion("/free", {"unload_models": True, "free_memory": True})


def encolar(flujo):
    """Mete un flujo en la cola de ComfyUI y devuelve su prompt_id."""
    return _peticion("/prompt", {"prompt": flujo, "client_id": "drama-ia"})["prompt_id"]


def esperar(prompt_id, cada=3):
    """Espera a que termine un flujo y devuelve la lista de archivos generados (rutas en disco)."""
    while True:
        hist = _peticion(f"/history/{prompt_id}").get(prompt_id)
        if hist and hist.get("status", {}).get("completed"):
            break
        if hist and hist.get("status", {}).get("status_str") == "error":
            msgs = [m for m in hist["status"].get("messages", []) if m[0] == "execution_error"]
            raise RuntimeError(f"Error en ComfyUI: {msgs[-1][1].get('exception_message') if msgs else hist['status']}")
        time.sleep(cada)
    archivos = []
    for salida in hist["outputs"].values():
        for lista in salida.values():
            if not isinstance(lista, list):
                continue
            for a in lista:
                if isinstance(a, dict) and "filename" in a and a.get("type") == "output":
                    archivos.append(config.COMFY_OUTPUT / a.get("subfolder", "") / a["filename"])
    return archivos


def ejecutar(flujo, destino: Path):
    """Encola, espera y mueve el (primer) archivo generado a `destino`."""
    archivos = esperar(encolar(flujo))
    if not archivos:
        raise RuntimeError("ComfyUI no devolvió ningún archivo")
    destino.parent.mkdir(parents=True, exist_ok=True)
    shutil.move(str(archivos[0]), destino)
    return destino


def subir_imagen(ruta: Path) -> str:
    """Copia una imagen a la carpeta input de ComfyUI (es local, no hace falta subirla por HTTP)."""
    nombre = f"drama-ia_{uuid.uuid4().hex[:8]}{ruta.suffix}"
    shutil.copy2(ruta, config.COMFY_INPUT / nombre)
    return nombre


def semilla():
    return random.randint(1, 2**50)


class _Flujo(dict):
    """Ayuda a montar flujos en formato API: add() devuelve el id del nodo."""

    def add(self, clase, **entradas):
        nid = str(len(self) + 1)
        self[nid] = {"class_type": clase, "inputs": entradas}
        return nid


def _o(nodo, salida=0):
    return [nodo, salida]


def flujo_imagen(prompt, prefijo, referencias=(), ancho=config.IMG_ANCHO, alto=config.IMG_ALTO, seed=None):
    """FLUX.2 Klein 4B destilado."""
    f = _Flujo()
    unet = f.add("UNETLoader", unet_name=config.IMG_MODELO, weight_dtype="default")
    clip = f.add("CLIPLoader", clip_name=config.IMG_TEXT_ENCODER, type="flux2", device="default")
    vae = f.add("VAELoader", vae_name=config.IMG_VAE)
    pos = f.add("CLIPTextEncode", text=prompt, clip=_o(clip))
    neg = f.add("ConditioningZeroOut", conditioning=_o(pos))
    for ref in referencias:
        img = f.add("LoadImage", image=ref)
        esc = f.add("ImageScaleToTotalPixels", image=_o(img), upscale_method="nearest-exact",
                    megapixels=1.0, resolution_steps=1)
        lat = f.add("VAEEncode", pixels=_o(esc), vae=_o(vae))
        pos = f.add("ReferenceLatent", conditioning=_o(pos), latent=_o(lat))
        neg = f.add("ReferenceLatent", conditioning=_o(neg), latent=_o(lat))
    guia = f.add("CFGGuider", model=_o(unet), positive=_o(pos), negative=_o(neg), cfg=1.0)
    ruido = f.add("RandomNoise", noise_seed=seed or semilla())
    sampler = f.add("KSamplerSelect", sampler_name="euler")
    sigmas = f.add("Flux2Scheduler", steps=config.IMG_PASOS, width=ancho, height=alto)
    latente = f.add("EmptyFlux2LatentImage", width=ancho, height=alto, batch_size=1)
    muestra = f.add("SamplerCustomAdvanced", noise=_o(ruido), guider=_o(guia), sampler=_o(sampler),
                    sigmas=_o(sigmas), latent_image=_o(latente))
    dec = f.add("VAEDecode", samples=_o(muestra), vae=_o(vae))
    f.add("SaveImage", images=_o(dec), filename_prefix=prefijo)
    return f


def flujo_musica(etiquetas, prefijo, segundos=60.0, bpm=80, tonalidad="D minor", letra="[Instrumental]", seed=None):
    """ACE-Step 1.5 turbo (plantilla audio_ace_step_1_5_split): 8 pasos, CFG 1, shift 3. Guarda FLAC."""
    f = _Flujo()
    s = seed or semilla()
    unet = f.add("UNETLoader", unet_name="acestep_v1.5_turbo.safetensors", weight_dtype="default")
    modelo = f.add("ModelSamplingAuraFlow", model=_o(unet), shift=3.0)
    clip = f.add("DualCLIPLoader", clip_name1="qwen_0.6b_ace15.safetensors", clip_name2="qwen_1.7b_ace15.safetensors",
                 type="ace", device="default")
    vae = f.add("VAELoader", vae_name="ace_1.5_vae.safetensors")
    pos = f.add("TextEncodeAceStepAudio1.5", clip=_o(clip), tags=etiquetas, lyrics=letra, seed=s, bpm=bpm,
                duration=float(segundos), timesignature="4", language="en", keyscale=tonalidad,
                generate_audio_codes=True, cfg_scale=2.0, temperature=0.85, top_p=0.9, top_k=0, min_p=0.0)
    neg = f.add("ConditioningZeroOut", conditioning=_o(pos))
    lat = f.add("EmptyAceStep1.5LatentAudio", seconds=float(segundos), batch_size=1)
    mue = f.add("KSampler", model=_o(modelo), positive=_o(pos), negative=_o(neg), latent_image=_o(lat), seed=s,
                steps=8, cfg=1.0, sampler_name="euler", scheduler="simple", denoise=1.0)
    dec = f.add("VAEDecodeAudio", samples=_o(mue), vae=_o(vae))
    f.add("SaveAudio", audio=_o(dec), filename_prefix=prefijo)
    return f


def flujo_sfx(prompt, prefijo, segundos=5.0, seed=None):
    """Stable Audio 3 medium (plantilla audio_stable_audio_3_medium, sin el paso de reescritura del prompt):"""
    f = _Flujo()
    ckpt = f.add("CheckpointLoaderSimple", ckpt_name="stable_audio_3_medium.safetensors")
    clip = f.add("CLIPLoader", clip_name="t5gemma_b_b_ul2.safetensors", type="stable_audio", device="default")
    pos = f.add("CLIPTextEncode", text=prompt, clip=_o(clip))
    neg = f.add("CLIPTextEncode", text="", clip=_o(clip))
    lat = f.add("EmptyLatentAudio", seconds=float(segundos), batch_size=1)
    mue = f.add("KSampler", model=_o(ckpt, 0), positive=_o(pos), negative=_o(neg), latent_image=_o(lat),
                seed=seed or semilla(), steps=8, cfg=1.0, sampler_name="lcm", scheduler="simple", denoise=1.0)
    dec = f.add("VAEDecodeAudio", samples=_o(mue), vae=_o(ckpt, 2))
    f.add("SaveAudio", audio=_o(dec), filename_prefix=prefijo)
    return f


LABIOS_FPS = 25  # InfiniteTalk alinea el audio a 25 fps


def flujo_labios(imagen, audio, prompt, prefijo, segundos, ancho=832, alto=480, seed=None,
                 mascara_habla=None, mascara_calla=None, audio_silencio=None):
    """InfiniteTalk (plantilla video_wan2_1_infinitetalk, 6 pasos con la LoRA lightx2v)."""
    f = _Flujo()
    frames = int(-(-segundos * LABIOS_FPS // 1))  # redondeo hacia arriba
    frames = frames + (1 - frames) % 4  # 4n+1
    dos = mascara_habla is not None
    unet = f.add("UNETLoader", unet_name="Wan2_1-I2V-14B-480p_fp8_e4m3fn_scaled_KJ.safetensors", weight_dtype="default")
    modelo = f.add("LoraLoaderModelOnly", model=_o(unet),
                   lora_name="lightx2v_I2V_14B_480p_cfg_step_distill_rank64_bf16.safetensors", strength_model=1.0)
    parche = f.add("ModelPatchLoader", name=f"wan2.1_infiniteTalk_{'multi' if dos else 'single'}_fp16.safetensors")
    clip = f.add("CLIPLoader", clip_name=config.VID_TEXT_ENCODER, type="wan", device="default")
    vae = f.add("VAELoader", vae_name=config.VID_VAE)
    pos = f.add("CLIPTextEncode", text=prompt, clip=_o(clip))
    neg = f.add("ConditioningZeroOut", conditioning=_o(pos))
    enc = f.add("AudioEncoderLoader", audio_encoder_name="wav2vec2-chinese-base_fp16.safetensors")
    a1 = f.add("AudioEncoderEncode", audio_encoder=_o(enc), audio=_o(f.add("LoadAudio", audio=audio)))
    img = f.add("LoadImage", image=imagen)
    extra = {}
    if dos:
        a2 = f.add("AudioEncoderEncode", audio_encoder=_o(enc), audio=_o(f.add("LoadAudio", audio=audio_silencio)))
        m1 = f.add("LoadImageMask", image=mascara_habla, channel="red")
        m2 = f.add("LoadImageMask", image=mascara_calla, channel="red")
        extra = {"mode.audio_encoder_output_2": _o(a2), "mode.mask_1": _o(m1), "mode.mask_2": _o(m2)}
    it = f.add("WanInfiniteTalkToVideo", mode="two_speakers" if dos else "single_speaker", model=_o(modelo),
               model_patch=_o(parche), positive=_o(pos), negative=_o(neg), vae=_o(vae), width=ancho, height=alto,
               length=frames, audio_encoder_output_1=_o(a1), motion_frame_count=9, audio_scale=1.0,
               start_image=_o(img), **extra)
    guia = f.add("CFGGuider", model=_o(it, 0), positive=_o(it, 1), negative=_o(it, 2), cfg=1.0)
    sigmas = f.add("BasicScheduler", model=_o(it, 0), scheduler="normal", steps=6, denoise=1.0)
    mue = f.add("SamplerCustomAdvanced", noise=_o(f.add("RandomNoise", noise_seed=seed or semilla())), guider=_o(guia),
                sampler=_o(f.add("KSamplerSelect", sampler_name="euler")), sigmas=_o(sigmas), latent_image=_o(it, 3))
    dec = f.add("VAEDecode", samples=_o(mue), vae=_o(vae))
    vid = f.add("CreateVideo", images=_o(dec), fps=float(LABIOS_FPS))
    f.add("SaveVideo", video=_o(vid), filename_prefix=prefijo, format="auto", codec="auto", **{"format.codec": "auto"})
    return f


def flujo_interpolar(video, prefijo, fps_salida, multiplicador=2):
    """FILM (plantilla utility_video_frame_interpolation): inventa fotogramas intermedios. `video` en input/."""
    f = _Flujo()
    comp = f.add("GetVideoComponents", video=_o(f.add("LoadVideo", file=video)))
    modelo = f.add("FrameInterpolationModelLoader", model_name="film_net_fp16.safetensors")
    interp = f.add("FrameInterpolate", interp_model=_o(modelo), images=_o(comp, 0), multiplier=multiplicador)
    vid = f.add("CreateVideo", images=_o(interp), fps=float(fps_salida))
    f.add("SaveVideo", video=_o(vid), filename_prefix=prefijo, format="auto", codec="auto", **{"format.codec": "auto"})
    return f


def flujo_reescalar(video, prefijo, fps, ancho=1280, alto=720, seed=None):
    """SeedVR2 3B int8 (plantilla utility_seedvr2_3b_int8_upscale_video) con troceado temporal automático"""
    f = _Flujo()
    comp = f.add("GetVideoComponents", video=_o(f.add("LoadVideo", file=video)))
    grande = f.add("ResizeImageMaskNode", input=_o(comp, 0), scale_method="lanczos", resize_type="scale dimensions",
                   **{"resize_type.width": ancho, "resize_type.height": alto, "resize_type.crop": "center"})
    vae = f.add("VAELoader", vae_name="seedvr2_ema_vae_fp16.safetensors")
    unet = f.add("UNETLoader", unet_name="seedvr2_3b_int8_convrot.safetensors", weight_dtype="default")
    lat = f.add("VAEEncodeTiled", pixels=_o(f.add("SeedVR2Preprocess", resized_images=_o(grande))), vae=_o(vae),
                tile_size=512, overlap=128, temporal_size=64, temporal_overlap=8)
    trozos = f.add("SeedVR2TemporalChunk", latent=_o(lat), temporal_overlap=0, chunking_mode="auto")
    cond = f.add("SeedVR2Conditioning", model=_o(unet), vae_conditioning=_o(trozos, 0))
    mue = f.add("KSampler", model=_o(unet), positive=_o(cond, 0), negative=_o(cond, 1), latent_image=_o(trozos, 0),
                seed=seed or semilla(), steps=1, cfg=1.0, sampler_name="euler", scheduler="simple", denoise=1.0)
    unido = f.add("SeedVR2TemporalMerge", latents=_o(mue), temporal_overlap=_o(trozos, 1))
    dec = f.add("VAEDecodeTiled", samples=_o(unido), vae=_o(vae), tile_size=512, overlap=128, temporal_size=64,
                temporal_overlap=8)
    post = f.add("SeedVR2PostProcessing", images=_o(dec), original_resized_images=_o(grande), color_correction_method="lab")
    vid = f.add("CreateVideo", images=_o(post), fps=float(fps))
    f.add("SaveVideo", video=_o(vid), filename_prefix=prefijo, format="auto", codec="auto", **{"format.codec": "auto"})
    return f


def flujo_video(imagen, prompt, prefijo, segundos=5.0, ancho=config.VID_ANCHO, alto=config.VID_ALTO, seed=None,
                negativo=config.VID_NEGATIVO):
    """Wan 2.2 I2V 14B con LoRAs de 4 pasos: el experto de alto ruido hace los pasos 0-2 y el de"""
    if config.VID_MODO != "rapido":
        return flujo_video_modo(imagen, prompt, prefijo, config.VID_MODO, segundos, ancho, alto, seed, negativo)
    f = _Flujo()
    frames = int(segundos * config.VID_FPS) + 1  # Wan quiere 4n+1 frames: 81 = 5 s a 16 fps
    frames = (frames - 1) // 4 * 4 + 1
    modelos = []
    for unet_name, lora in ((config.VID_ALTO_RUIDO, config.VID_LORA_ALTO), (config.VID_BAJO_RUIDO, config.VID_LORA_BAJO)):
        m = f.add("UNETLoader", unet_name=unet_name, weight_dtype="default")
        m = f.add("LoraLoaderModelOnly", model=_o(m), lora_name=lora, strength_model=1.0)
        modelos.append(f.add("ModelSamplingSD3", model=_o(m), shift=config.VID_SHIFT))
    clip = f.add("CLIPLoader", clip_name=config.VID_TEXT_ENCODER, type="wan", device="default")
    vae = f.add("VAELoader", vae_name=config.VID_VAE)
    pos = f.add("CLIPTextEncode", text=prompt, clip=_o(clip))
    neg = f.add("CLIPTextEncode", text=negativo, clip=_o(clip))
    img = f.add("LoadImage", image=imagen)
    i2v = f.add("WanImageToVideo", positive=_o(pos), negative=_o(neg), vae=_o(vae), start_image=_o(img),
                width=ancho, height=alto, length=frames, batch_size=1)
    comun = dict(steps=config.VID_PASOS, cfg=1.0, sampler_name="euler", scheduler="simple",
                 positive=_o(i2v, 0), negative=_o(i2v, 1))
    alto_r = f.add("KSamplerAdvanced", model=_o(modelos[0]), add_noise="enable", noise_seed=seed or semilla(),
                   latent_image=_o(i2v, 2), start_at_step=0, end_at_step=config.VID_CAMBIO,
                   return_with_leftover_noise="enable", **comun)
    bajo_r = f.add("KSamplerAdvanced", model=_o(modelos[1]), add_noise="disable", noise_seed=0,
                   latent_image=_o(alto_r), start_at_step=config.VID_CAMBIO, end_at_step=10000,
                   return_with_leftover_noise="disable", **comun)
    dec = f.add("VAEDecode", samples=_o(bajo_r), vae=_o(vae))
    vid = f.add("CreateVideo", images=_o(dec), fps=float(config.VID_FPS))
    f.add("SaveVideo", video=_o(vid), filename_prefix=prefijo, format="auto", codec="auto", **{"format.codec": "auto"})
    return f


def flujo_video_modo(imagen, prompt, prefijo, modo, segundos=5.0, ancho=config.VID_ANCHO, alto=config.VID_ALTO,
                     seed=None, negativo=config.VID_NEGATIVO):
    """Variantes de Wan 2.2 con MÁS MOVIMIENTO (la evaluación veía los clips del modo rápido "como fotos"):"""
    f = _Flujo()
    frames = int(segundos * config.VID_FPS) + 1
    frames = (frames - 1) // 4 * 4 + 1
    muestreo = lambda m: f.add("ModelSamplingSD3", model=_o(m), shift=config.VID_SHIFT)
    base_alto = f.add("UNETLoader", unet_name=config.VID_ALTO_RUIDO, weight_dtype="default")
    base_bajo = f.add("UNETLoader", unet_name=config.VID_BAJO_RUIDO, weight_dtype="default")
    clip = f.add("CLIPLoader", clip_name=config.VID_TEXT_ENCODER, type="wan", device="default")
    vae = f.add("VAELoader", vae_name=config.VID_VAE)
    pos = f.add("CLIPTextEncode", text=prompt, clip=_o(clip))
    neg = f.add("CLIPTextEncode", text=negativo, clip=_o(clip))
    img = f.add("LoadImage", image=imagen)
    i2v = f.add("WanImageToVideo", positive=_o(pos), negative=_o(neg), vae=_o(vae), start_image=_o(img),
                width=ancho, height=alto, length=frames, batch_size=1)
    semilla_ = seed or semilla()

    def paso(modelo, latente, total, desde, hasta, cfg, primero, ultimo):
        return f.add("KSamplerAdvanced", model=_o(modelo), add_noise="enable" if primero else "disable",
                     noise_seed=semilla_ if primero else 0, latent_image=latente, steps=total, cfg=cfg,
                     sampler_name="euler", scheduler="simple", positive=_o(i2v, 0), negative=_o(i2v, 1),
                     start_at_step=desde, end_at_step=10000 if ultimo else hasta,
                     return_with_leftover_noise="disable" if ultimo else "enable")

    if modo == "completo":
        r1 = paso(muestreo(base_alto), _o(i2v, 2), 20, 0, 10, 3.5, True, False)
        final = paso(muestreo(base_bajo), _o(r1), 20, 10, 20, 3.5, False, True)
    elif modo == "ligero":
        alto_lora = f.add("LoraLoaderModelOnly", model=_o(base_alto), lora_name=config.VID_LORA_ALTO, strength_model=0.6)
        bajo_lora = f.add("LoraLoaderModelOnly", model=_o(base_bajo), lora_name=config.VID_LORA_BAJO, strength_model=0.6)
        r1 = paso(muestreo(alto_lora), _o(i2v, 2), 8, 0, 4, 1.0, True, False)
        final = paso(muestreo(bajo_lora), _o(r1), 8, 4, 8, 1.0, False, True)
    elif modo == "movimiento":
        alto_lora = f.add("LoraLoaderModelOnly", model=_o(base_alto), lora_name=config.VID_LORA_ALTO, strength_model=1.0)
        bajo_lora = f.add("LoraLoaderModelOnly", model=_o(base_bajo), lora_name=config.VID_LORA_BAJO, strength_model=1.0)
        r1 = paso(muestreo(base_alto), _o(i2v, 2), 8, 0, 2, 3.5, True, False)
        r2 = paso(muestreo(alto_lora), _o(r1), 8, 2, 4, 1.0, False, False)
        final = paso(muestreo(bajo_lora), _o(r2), 8, 4, 8, 1.0, False, True)
    else:
        raise ValueError(modo)
    dec = f.add("VAEDecode", samples=_o(final), vae=_o(vae))
    vid = f.add("CreateVideo", images=_o(dec), fps=float(config.VID_FPS))
    f.add("SaveVideo", video=_o(vid), filename_prefix=prefijo, format="auto", codec="auto", **{"format.codec": "auto"})
    return f
