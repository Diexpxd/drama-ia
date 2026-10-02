# Arranca ComfyUI en http://127.0.0.1:8188
# Variables opcionales: COMFYUI_DIR (por defecto ~\ComfyUI) y DRAMA_IA_SALIDAS (por defecto .\salidas)
$comfy = if ($env:COMFYUI_DIR) { $env:COMFYUI_DIR } else { Join-Path $HOME 'ComfyUI' }
$salidas = if ($env:DRAMA_IA_SALIDAS) { $env:DRAMA_IA_SALIDAS } else { Join-Path $PSScriptRoot 'salidas' }
$py = if ($env:DRAMA_IA_PY_COMFY) { $env:DRAMA_IA_PY_COMFY } else { 'python' }
New-Item -ItemType Directory -Force -Path (Join-Path $salidas 'comfy') | Out-Null
Set-Location $comfy
& $py main.py --listen 127.0.0.1 --port 8188 --output-directory (Join-Path $salidas 'comfy') @args
