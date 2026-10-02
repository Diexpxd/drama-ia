"""Descarga los modelos de cada fase directamente en las carpetas de ComfyUI."""
import os
import shutil
import sys
import time
from pathlib import Path

os.environ.setdefault("HF_HOME", str(Path(__file__).resolve().parent.parent / "cache" / "huggingface"))
os.environ.setdefault("HF_HUB_DISABLE_XET", "1")
try:
    import truststore  # noqa: E402
    truststore.inject_into_ssl()
except ImportError:
    pass
from huggingface_hub import HfApi, hf_hub_download, snapshot_download  # noqa: E402

COMFY = Path(os.environ.get("COMFYUI_DIR", Path.home() / "ComfyUI"))
MODELOS = str(COMFY / "models")
TMP = str(Path(__file__).resolve().parent.parent / "cache" / "descargas")

TTS = os.environ.get("DRAMA_IA_MODELOS_TTS", str(Path(__file__).resolve().parent.parent / "modelos_tts"))

# (repo, ruta en el repo, subcarpeta de ComfyUI/models)
ARCHIVOS = {"imagen_video": [
    ("Comfy-Org/flux2-klein", "split_files/diffusion_models/flux-2-klein-4b.safetensors", "diffusion_models"),
    ("Comfy-Org/flux2-klein", "split_files/text_encoders/qwen_3_4b.safetensors", "text_encoders"),
    ("Comfy-Org/flux2-dev", "split_files/vae/flux2-vae.safetensors", "vae"),
    # Vídeo: Wan 2.2 I2V 14B (Apache 2.0), dos expertos + LoRAs de 4 pasos
    ("Comfy-Org/Wan_2.2_ComfyUI_Repackaged", "split_files/diffusion_models/wan2.2_i2v_high_noise_14B_fp8_scaled.safetensors", "diffusion_models"),
    ("Comfy-Org/Wan_2.2_ComfyUI_Repackaged", "split_files/diffusion_models/wan2.2_i2v_low_noise_14B_fp8_scaled.safetensors", "diffusion_models"),
    ("Comfy-Org/Wan_2.2_ComfyUI_Repackaged", "split_files/loras/wan2.2_i2v_lightx2v_4steps_lora_v1_high_noise.safetensors", "loras"),
    ("Comfy-Org/Wan_2.2_ComfyUI_Repackaged", "split_files/loras/wan2.2_i2v_lightx2v_4steps_lora_v1_low_noise.safetensors", "loras"),
    ("Comfy-Org/Wan_2.2_ComfyUI_Repackaged", "split_files/vae/wan_2.1_vae.safetensors", "vae"),
    ("Comfy-Org/Wan_2.1_ComfyUI_repackaged", "split_files/text_encoders/umt5_xxl_fp8_e4m3fn_scaled.safetensors", "text_encoders"),
], "audio": [
    # Música: ACE-Step 1.5 turbo (Apache 2.0)
    ("Comfy-Org/ace_step_1.5_ComfyUI_files", "split_files/diffusion_models/acestep_v1.5_turbo.safetensors", "diffusion_models"),
    ("Comfy-Org/ace_step_1.5_ComfyUI_files", "split_files/text_encoders/qwen_0.6b_ace15.safetensors", "text_encoders"),
    ("Comfy-Org/ace_step_1.5_ComfyUI_files", "split_files/text_encoders/qwen_1.7b_ace15.safetensors", "text_encoders"),
    ("Comfy-Org/ace_step_1.5_ComfyUI_files", "split_files/vae/ace_1.5_vae.safetensors", "vae"),
    ("Comfy-Org/stable-audio-3", "checkpoints/stable_audio_3_medium.safetensors", "checkpoints"),
    ("Comfy-Org/stable-audio-3", "text_encoders/t5gemma_b_b_ul2.safetensors", "text_encoders"),
], "labios": [
    ("Kijai/WanVideo_comfy_fp8_scaled", "I2V/Wan2_1-I2V-14B-480p_fp8_e4m3fn_scaled_KJ.safetensors", "diffusion_models"),
    ("Comfy-Org/Wan_2.1_ComfyUI_repackaged", "split_files/model_patches/wan2.1_infiniteTalk_single_fp16.safetensors", "model_patches"),
    ("Comfy-Org/Wan_2.1_ComfyUI_repackaged", "split_files/model_patches/wan2.1_infiniteTalk_multi_fp16.safetensors", "model_patches"),
    ("Kijai/WanVideo_comfy", "Lightx2v/lightx2v_I2V_14B_480p_cfg_step_distill_rank64_bf16.safetensors", "loras"),
    ("Kijai/wav2vec2_safetensors", "wav2vec2-chinese-base_fp16.safetensors", "audio_encoders"),
], "acabado": [
    ("Comfy-Org/frame_interpolation", "frame_interpolation/film_net_fp16.safetensors", "frame_interpolation"),
    ("Comfy-Org/SeedVR2", "diffusion_models/seedvr2_3b_int8_convrot.safetensors", "diffusion_models"),
    ("Comfy-Org/SeedVR2", "vae/seedvr2_ema_vae_fp16.safetensors", "vae"),
]}

REPOS = {"audio": [
    ("openbmb/VoxCPM2", "VoxCPM2"),  # Apache 2.0: diseño de voz + clonación con estilo
    ("Qwen/Qwen3-TTS-12Hz-1.7B-VoiceDesign", "Qwen3-TTS-VoiceDesign"),  # Apache 2.0
    ("Qwen/Qwen3-TTS-12Hz-1.7B-Base", "Qwen3-TTS-Base"),  # Apache 2.0: clonación
]}


def tamanos(api, repo):
    info = api.model_info(repo, files_metadata=True)
    return {s.rfilename: s.size for s in info.siblings}


def main():
    fase = sys.argv[1] if len(sys.argv) > 1 else "imagen_video"
    api = HfApi()
    cache_tamanos = {}
    total = 0
    for repo, ruta, sub in ARCHIVOS[fase]:
        if repo not in cache_tamanos:
            cache_tamanos[repo] = tamanos(api, repo)
        esperado = cache_tamanos[repo][ruta]
        total += esperado
        destino = os.path.join(MODELOS, sub, os.path.basename(ruta))
        if os.path.exists(destino) and os.path.getsize(destino) == esperado:
            print(f"[ya está] {os.path.basename(ruta)}", flush=True)
            continue
        os.makedirs(os.path.dirname(destino), exist_ok=True)
        print(f"[bajando] {os.path.basename(ruta)} ({esperado / 1e9:.2f} GB)", flush=True)
        t = time.time()
        local = hf_hub_download(repo, ruta, local_dir=TMP)
        shutil.move(local, destino)
        print(f"[ok] {os.path.basename(ruta)} en {time.time() - t:.0f} s", flush=True)
    for repo, carpeta in REPOS.get(fase, []):
        t = time.time()
        print(f"[bajando repo] {repo}", flush=True)
        snapshot_download(repo, local_dir=os.path.join(TTS, carpeta))
        print(f"[ok] {repo} en {time.time() - t:.0f} s", flush=True)
    shutil.rmtree(TMP, ignore_errors=True)
    print(f"TERMINADO fase {fase}. Archivos sueltos: {total / 1e9:.1f} GB", flush=True)


if __name__ == "__main__":
    sys.exit(main())
