# drama-ia

Pipeline para producir vídeos cortos documentales (Shorts, Reels, YouTube) con modelos de IA que corren en local. A partir de un tema genera el guion con datos verificados, los planos, las imágenes, el vídeo, las voces, la música y los efectos, y monta, mezcla y subtitula el resultado. Un modelo externo hace de evaluador del vídeo final.

Es un proyecto personal. Solo Windows (usa `os.startfile` y fuentes del sistema).

## Qué hace cada etapa

| Etapa | Script | Modelo / servicio |
|---|---|---|
| Guion con datos verificados | `guionista.py` | Claude o Gemini (API), con búsqueda web |
| Guion a planos | `guion_a_planos.py` | LLM local con Ollama |
| Imágenes | `generar.py` | FLUX.2 Klein 4B en ComfyUI |
| Vídeo | `generar.py` | Wan 2.2 I2V 14B en ComfyUI |
| Voces | `voces.py` | VoxCPM2 y Qwen3-TTS |
| Sincronía de labios | `labios.py` | InfiniteTalk en ComfyUI |
| Música y efectos | `audio.py`, `sonido_auto.py` | ACE-Step 1.5 y Stable Audio 3 |
| Montaje y mezcla | `montar.py`, `mezclar.py`, `linea_tiempo.py` | PyAV y NumPy |
| Subtítulos | `subtitulos.py` | faster-whisper |
| Acabado a 1080p | `acabado.py` | SeedVR2 y FILM en ComfyUI |
| Evaluación | `evaluar.py`, `juez_externo.py` | Gemini u otros modelos que ven el vídeo y oyen el audio |

`orquestador.py` encadena todo. Guarda una firma de lo que usó para generar cada pieza (imagen, prompt, frase, voz) y, si algo cambia, solo rehace lo que depende de ello. Nunca borra versiones: numera la siguiente.

El guionista exige que cada dato aparezca literalmente, con sus cifras, en al menos dos dominios distintos. El programa descarga las páginas por su cuenta; el modelo no puede dar por buena una cita que no esté en el texto descargado. Si el guion rompe las reglas, se pide una corrección, y si sigue sin cumplirlas no se crea el proyecto.

## Interfaces

- `interfaz.py` levanta una web local en `http://127.0.0.1:8190` para crear proyectos, editar guion y personajes, lanzar cada paso y elegir resultados. Ejecuta los mismos scripts de línea de comandos, de uno en uno, porque comparten la GPU.
- `bot.py` es un bot de Telegram que solo obedece a un chat concreto: pide guiones con `/topic`, avisa del estado y envía borradores para aprobar.

## Instalación

Requisitos: Windows, GPU NVIDIA con 16 GB de VRAM o más, [ComfyUI](https://github.com/comfyanonymous/ComfyUI) y [Ollama](https://ollama.com).

Se usan varios entornos virtuales porque las dependencias de cada modelo chocan entre sí. Cada script se lanza con el intérprete que corresponde, que se configura con variables de entorno (por defecto, el Python que ejecuta el script):

| Variable | Para qué |
|---|---|
| `DRAMA_IA_SALIDAS` | Carpeta de todo lo generado (por defecto `salidas/`) |
| `COMFYUI_DIR` | Instalación de ComfyUI (por defecto `~/ComfyUI`) |
| `DRAMA_IA_PY_COMFY` | Python del entorno de ComfyUI (PyAV, Pillow, NumPy) |
| `DRAMA_IA_PY_VOZ` | Python del entorno de voces (VoxCPM2, Qwen3-TTS) |
| `DRAMA_IA_PY_WHISPER` | Python del entorno de subtítulos |
| `DRAMA_IA_PY_NUBE` | Python del entorno de guionista, evaluador y bot |
| `DRAMA_IA_MODELOS_TTS` | Carpeta de los modelos de voz |
| `OLLAMA_MODELS`, `HF_HOME` | Carpetas de modelos de Ollama y de Hugging Face |

```
pip install -r requirements.txt
copy secretos.env.example secretos.env      # y rellenar las claves
python setup/descargar_modelos.py imagen_video
```

## Uso

```
python nuevo_proyecto.py mi-proyecto --genero "documental de negocios" --idioma en --formato 9:16
python guionista.py crear mi-proyecto "The fall of Blockbuster" --segundos 60
python guionista.py aprobar mi-proyecto
python orquestador.py mi-proyecto --simular      # muestra qué haría, sin tocar nada
python orquestador.py mi-proyecto                # produce lo que falte o esté desactualizado
python evaluar.py mi-proyecto                    # nota del evaluador (visual, ritmo, audio)
```

Con `--planos P003 --rehacer imagen` el orquestador rehace solo una pieza. Cada proyecto es una carpeta en `proyectos/<nombre>/` con `proyecto.json` (estilo, personajes, opciones), `guion.md`, `planos.json` y `sonido.json`. Opciones de `proyecto.json`: `genero`, `idioma`, `formato` (`16:9` o `9:16`), `ritmo`, `realismo`, `subtitulos`, `cortes_ritmo`.

## Estado

Funciona de punta a punta en mi equipo (RTX 5070 Ti, 16 GB de VRAM). Un clip de vídeo tarda unos 7 minutos en modo `completo` y 1,5 en `rapido`. No hay tests automáticos: se probó ejecutando el pipeline completo, y `--simular` sirve para revisar el plan sin gastar GPU.

Limitaciones conocidas:

- La IA de vídeo deforma manos, caras y texto, por eso el guionista rechaza planos que los pidan.
- El evaluador automático puntúa con criterios propios y distintos modelos no son comparables entre sí. Es una guía, no una medida de calidad objetiva.
- Las rutas y los modelos están pensados para una máquina concreta; en otro equipo hay que ajustar las variables de entorno y los nombres de modelo en `config.py`.

## Licencias de los modelos

FLUX.2 Klein 4B, Wan 2.2, VoxCPM2, Qwen3-TTS y ACE-Step son Apache 2.0. Stable Audio 3 es gratuito con ingresos inferiores a 1 M USD al año. Revisa la licencia de cada uno antes de un uso comercial.
