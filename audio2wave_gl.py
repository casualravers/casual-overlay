#!/usr/bin/env python3
"""POC casual-overlay : visuel audio2wave_live en fenetre OpenGL, logo incruste et
deformations reactives a l'audio du micro.

    python audio2wave_gl.py --list-devices            # entrees dshow (ffmpeg)
    python audio2wave_gl.py --list-audio-devices      # entrees sounddevice
    python audio2wave_gl.py -d "Microphone (Realtek(R) Audio)" --dry-run
    python audio2wave_gl.py -d "Microphone (Realtek(R) Audio)"
    python audio2wave_gl.py --synthetic --no-fullscreen   # sans ffmpeg ni micro

Dependances : pip install -r requirements-gl.txt (en plus de ffmpeg dans le PATH).

Reutilise audio2wave (copie interne : sources/audio2wave/, ou --a2w-dir pour en essayer une autre) :
  - audio2wave_live.parse_args()      : toutes les options du visuel (via --live-args),
  - audio2wave_live.producer_command() / spawn_producer() : commande ffmpeg identique,
  - common.list_audio_devices()       : inventaire dshow.
Reimplemente ici : lecteur de frames (derniere frame ENTIERE gagne), fenetre glfw,
rendu moderngl, capture sounddevice, analyse FFT/kick. Les modifications de la copie d'audio2wave
sont notees dans sources/audio2wave/VENDORED.md.
"""

from __future__ import annotations

import argparse
import copy
import ctypes
import functools
import json
import math
import os
import queue
import shlex
import shutil
import subprocess
import sys
import threading
import time
from collections import deque
from pathlib import Path

# La GUI (gl_gui, gui_*, skin, theme, icons, py_modes) vit dans gui/ : modules plats, importes par leur nom.
sys.path.insert(0, str(Path(__file__).resolve().parent / "gui"))
from engine.deps import MISSING, _optional_import, np, glfw, moderngl, sd, PIL_Image, PIL_ImageDraw, PIL_ImageFont  # noqa: E402,F401
from engine.audio import (  # noqa: E402,F401
    ATTACK_TAU_S, AudioAnalyzer, BANDS, BAND_GATE, BASELINE_FALL_TAU_S, BASELINE_RISE_TAU_S, BEAT_DECAY_TAU_S, BEAT_IDLE_S, DEV_TAU_S, FeatureExtractor, KICK_K, KICK_MIN, KICK_RATIO_EXTRA, KICK_REFRACTORY_S, KICK_WARMUP_S, MIC_WORDS, NOT_A_MIC, PEAK_HALF_LIFE_S, RELEASE_TAU_S, RingBuffer, SoundDeviceInput, SyntheticAudio, _coef, find_input_device, pick_default_device, print_audio_devices, synth_audio_block, system_default_input_name,
)
from engine.video import (  # noqa: E402,F401
    BG_VIDEO_FITS, BgVideo, BgVideoManager, FrameReader, HEX_DIGITS, LOGO_KEY_BLEND, LOGO_KEY_SIMILARITY, LOGO_VIDEO_EXTENSIONS, LOGO_VIDEO_MAX_SIDE, LogoVideo, PIPE_BUFFER, PRODUCER_WARMUP_CAP_S, ProducerManager, SyntheticVideoStream, bg_video_command, build_live_args, ffmpeg_color_names, format_command, invalid_colors, is_ffmpeg_color, load_live, logo_video_command, probe_video, spawn_big_pipe, spawn_producer, stop_process,
)
from engine.window import (  # noqa: E402,F401
    Window, _addr, fullscreen_blocked, hide_cursor, monitor_contains, pick_monitor, resolve_fullscreen, windowed_rect,
)


from engine import diagnostics  # noqa: E402  (stdlib seulement : sans dependance a installer)
from core import params as core_params, params_schema, sessionlog  # noqa: E402,F401  (stdlib : table des reglages, journal de session)
# Facade de compatibilite : ces noms vivent dans core/ (Python pur, sans GL ni Tk) et restent lisibles ici (`gl.X`).
# Les chemins de fichiers se remplacent dans le module qui les possede (core.params.PARAMS_PATH, core.autosave.AUTOSAVE_PATH...).
from core.params import (  # noqa: E402,F401
    DEFAULT_LOGO_NAME, default_logo, DEFAULT_LOGO, PARAMS_PATH, FX_NAMES, DEFAULT_PARAMS, ROOM_KEYS, TRANSIENT_KEYS, MEMORY_KEYS,
    coerce_params, OVERLAY_PRESETS_PATH, BACKGROUND_PRESETS_PATH, OVERLAY_PRESET_EXCLUDED, BG_KEYS, BG_AUTOMATION_KEYS,
    OVERLAY_PRESETS, BACKGROUND_PRESETS, PresetBook, OVERLAY_BOOK, BACKGROUND_BOOK, _split_automation, capture_overlay,
    capture_background, overlay_preset_values, background_preset_values, load_params, save_params
)
from core.automation import (  # noqa: E402,F401
    AUTOMATION_POINTS, AUTOMATION_SPECS, FX_EFFECTS, curve_points, default_automation, merge_automation, _list_key, get_param,
    set_param, set_fx_link, AutomationEngine
)
from core.controls import (  # noqa: E402,F401
    strobe_press, strobe_release, STROBE_COOLDOWN_S, StrobeLimiter, PANIC_RESET, panic, tap_bpm, _unit, effective_tempo,
    SilenceGuard
)
from core.autosave import (  # noqa: E402,F401
    AUTOSAVE_PATH, AUTOSAVE_EVERY_S, write_autosave, read_autosave, clear_autosave, recover_autosave
)


from engine.render import (  # noqa: E402,F401
    SHADER_DIR, AUTO_FONT_ORDER, HOLO_DOWNSCALE, NOISE_SMOOTH, NOISE_SPEED, NoiseDrive, PerfGuard, Renderer, ShaderError, TEXT_FONTS, _hash01, _premultiplied, available_fonts, font_dirs, hex_to_rgb, layer_effects, load_font, load_logo, logo_layout, noise_value, render_text,
)


from engine.session import AudioController, Session, NoDeviceManager, _EmptyStream  # noqa: E402,F401


from engine.projection import Projection, _GL_SLOT, _gl_main, apply_logo_request, gl_main, logo_signature, loop  # noqa: E402,F401


from engine.app import parse_pair, run_app  # noqa: E402,F401


def require_deps() -> None:
    if MISSING:
        print(f"Dependances manquantes: {', '.join(dict.fromkeys(MISSING))}.\n"
              "Installe-les avec:  pip install -r requirements-gl.txt", file=sys.stderr)
        sys.exit(1)


SCRIPT_DIR = Path(__file__).resolve().parent


def bundled_paths(script_dir: Path, env_path: str = "") -> tuple[Path, str]:
    """(dossier d'audio2wave, PATH). audio2wave est interne : `audio2wave/` a cote du programme (version portable) l'emporte, puis
    `sources/audio2wave/` (le depot) ; le depot voisin `../audio2wave` n'est plus qu'un dernier recours (ancien poste de dev). ffmpeg/
    (ffmpeg.exe, ffprobe.exe), s'il est livre avec le programme, est ajoute en tete du PATH : `shutil.which`, audio2wave et nos
    sous-processus le trouvent sans installation."""
    candidates = (script_dir / "audio2wave", script_dir / "sources" / "audio2wave", script_dir.parent / "audio2wave")
    a2w_dir = next((c for c in candidates if (c / "audio2wave_live.py").is_file()), candidates[1])
    ffmpeg = script_dir / "ffmpeg"
    if (ffmpeg / "ffmpeg.exe").is_file() or (ffmpeg / "ffmpeg").is_file():
        env_path = str(ffmpeg) + os.pathsep + env_path
    return a2w_dir, env_path


DEFAULT_A2W_DIR, _bundled_path = bundled_paths(SCRIPT_DIR, os.environ.get("PATH", ""))
os.environ["PATH"] = _bundled_path

# --- Application ---------------------------------------------------------------------

def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(
        description="Rendu OpenGL du visuel audio2wave_live avec logo et effets reactifs a l'audio.",
        formatter_class=argparse.RawDescriptionHelpFormatter, epilog=__doc__)
    p.add_argument("-d", "--device", help="Entree DirectShow pour ffmpeg (voir --list-devices)")
    p.add_argument("--audio-device", help="Entree sounddevice pour l'analyse: sous-chaine ou numero "
                   "(voir --list-audio-devices). Defaut: meme nom que --device si trouve, sinon defaut systeme")
    p.add_argument("--gui", action="store_true",
                   help="Ouvre la fenetre de reglages (tkinter): visuel live, logo et effets en direct. La projection "
                        "(fenetre OpenGL) ne s'ouvre qu'au bouton Â« Demarrer la projection Â»")
    p.add_argument("--projeter", action="store_true",
                   help="Avec --gui : ouvre aussi la projection tout de suite, sans attendre le bouton")
    p.add_argument("--background", choices=["live", "pattern", "video"], default=None,
                   help="Fond: live = spectre ffmpeg, pattern = motif genere, video = fichier video brut (--bg-video) "
                        "(defaut: reglage sauve, sinon live)")
    p.add_argument("--bg-video", default=None, metavar="FICHIER",
                   help="Fond video : fichier video ou animation lu en boucle plein cadre (active --background video)")
    p.add_argument("--text", default=None,
                   help='Incruste ce texte a la place du logo PNG (ex. --text "CASUAL RAVERS"; "\\n" = retour '
                        "a la ligne). Police, couleur, alignement et taille se reglent dans la GUI")
    p.add_argument("--logo", default=None,
                   help="PNG RGBA a incruster, ou 'none' (defaut: reglage sauve, sinon "
                        "asset/images/Casual Ravers - Kit_Sigle - Blanc.png)")
    p.add_argument("--logo-video", default=None,
                   help="Logo anime: video ou GIF en boucle (WebM VP9 / MOV ProRes 4444 / GIF / APNG avec alpha "
                        "pour un logo detoure). Remplace le PNG et le texte")
    p.add_argument("--logo-key", default=None,
                   help="Couleur #rrggbb a detourer dans --logo-video si le fichier n'a pas d'alpha "
                        "(ex. #00ff00 pour un fond vert), 'none' pour ne rien detourer")
    p.add_argument("--logo-scale", type=float, default=None,
                   help="Cote du carre englobant le logo, en fraction de la hauteur (defaut: 0.35)")
    p.add_argument("--logo-pos", default=None,
                   help="Centre du logo x,y en fraction, origine en haut a gauche (defaut: 0.5,0.5)")
    p.add_argument("--size", default=None,
                   help="Taille de la fenetre WxH en mode --no-fullscreen (defaut: 1280x720)")
    p.add_argument("--render-size", default="1280x720",
                   help="Resolution de rendu ffmpeg, independante de la fenetre (defaut: 1280x720)")
    p.add_argument("--fps", type=int, default=30, help="Images par seconde du producteur ffmpeg (defaut: 30)")
    p.add_argument("--fullscreen", action=argparse.BooleanOptionalAction, default=None,
                   help="Fenetre borderless plein ecran sur le moniteur cible. Defaut: NON, la projection s'ouvre "
                        "en fenetre (--fullscreen pour la demander, aussi possible par la case de la GUI ou la touche F)")
    p.add_argument("--monitor", type=int, default=None,
                   help="Index glfw du moniteur cible (defaut: premier moniteur non principal)")
    p.add_argument("--sensitivity", type=float, default=None,
                   help="Sensibilite du kick (defaut: valeur sauvee, sinon 1.0; plus haut = plus sensible)")
    p.add_argument("--fps-cap", type=float, default=None,
                   help="Cadence maximale du rendu (defaut: frequence du moniteur cible +2 %%, 0 = sans limite)")
    p.add_argument("--automation", action=argparse.BooleanOptionalAction, default=None,
                   help="Automations de reglages (courbes qui font varier teinte, carreaux, effets...: voir "
                        "AUTOMATION_SPECS, editables dans la GUI). Defaut: reglage sauve, sinon actives. "
                        "--no-automation fige tout (touche T)")
    p.add_argument("--hud", action="store_true", help="Affiche les barres de debug des le lancement (touche H)")
    p.add_argument("--synthetic", action="store_true",
                   help="Source video et audio synthetiques: ni ffmpeg ni micro requis")
    p.add_argument("--live-args", default=None,
                   help='Options transmises a audio2wave_live.py, ex: "--style radio --gain 20"')
    p.add_argument("--a2w-dir", default=os.environ.get("AUDIO2WAVE_DIR", str(DEFAULT_A2W_DIR)),
                   help=f"Dossier d'audio2wave (defaut: {DEFAULT_A2W_DIR}, copie interne)")
    p.add_argument("--diagnostic", action="store_true",
                   help="Verifie ce PC (ffmpeg, OpenGL, micro, modules...) et quitte : code 1 s'il y a une erreur")
    p.add_argument("--list-devices", action="store_true", help="Liste les entrees DirectShow et quitte")
    p.add_argument("--list-audio-devices", action="store_true", help="Liste les entrees sounddevice et quitte")
    p.add_argument("--dry-run", action="store_true", help="Affiche la commande ffmpeg sans rien lancer")
    p.add_argument("--stats", action="store_true", help="Affiche fps, upload texture et temps GPU toutes les 5 s")
    p.add_argument("--max-seconds", type=float, default=None, help="Quitte apres N secondes (tests)")
    p.add_argument("--screenshot", default=None, help="Sauve la derniere image dans ce PNG a la sortie (tests)")
    return p.parse_args()


def main() -> None:
    args = parse_args()
    if args.diagnostic:
        sys.exit(diagnostics.run_cli(Path(args.a2w_dir), args.synthetic, args.gui))
    if not (args.list_audio_devices or args.list_devices or args.dry_run):
        # Ce PC est-il pret ? Une erreur arrete le programme avec un message clair, les manques moins graves sont signales.
        args.diag_checks, ready = diagnostics.preflight(Path(args.a2w_dir), args.synthetic, args.gui)
        problems = diagnostics.format_report(args.diag_checks, only_problems=True)
        if problems:
            print(problems, file=sys.stderr)
        if not ready:
            print("Le programme ne peut pas demarrer : corrige les erreurs ci-dessus "
                  "(python audio2wave_gl.py --diagnostic donne le rapport complet).", file=sys.stderr)
            sys.exit(1)
    args.fullscreen_request = args.fullscreen        # None = pas demande : decide a l'ouverture de la projection
    args.fullscreen = resolve_fullscreen(args.fullscreen)

    if args.list_audio_devices:
        require_deps()
        print_audio_devices()
        return

    render_w, render_h = (int(v) for v in parse_pair(args.render_size, "--render-size", "x"))
    live = None
    if not args.synthetic or args.list_devices or args.gui:
        live = load_live(Path(args.a2w_dir))

    if args.list_devices:
        live.require_tools()
        devices = live.list_audio_devices()
        if not devices:
            print("Aucune entree audio DirectShow detectee.", file=sys.stderr)
            sys.exit(1)
        print("Entrees audio disponibles:")
        for name in devices:
            print(f'  -d "{name}"')
        return

    if not args.synthetic and not args.device and live is not None and shutil.which("ffmpeg") is not None:
        # Aucune entree demandee : on prend le micro du PC (le programme doit marcher sur n'importe quelle machine).
        require_deps()
        args.device = pick_default_device(live.list_audio_devices(), system_default_input_name())
        if args.device:
            print(f"Aucune entree indiquee: micro du PC utilise ({args.device}). -d pour en choisir une autre.")

    live_args = None
    if not args.synthetic or args.gui:       # la GUI d'audio2wave a besoin des options live, meme en synthetique
        if not args.synthetic and not args.device and not args.gui:
            print("Indique une entree avec -d/--device (ou --list-devices, --gui, --synthetic).", file=sys.stderr)
            sys.exit(2)
        live_args = build_live_args(live, args.device, (render_w, render_h), args.fps, args.live_args,
                                    args.fullscreen)
        if live.resolve_size(live_args) != (render_w, render_h):
            print(f"--live-args ne doit pas changer --size (rendu {render_w}x{render_h}).", file=sys.stderr)
            sys.exit(2)

    if args.dry_run:
        if args.synthetic:
            print("Mode --synthetic: aucune commande ffmpeg, flux video/audio generes en Python.")
        elif not args.device:
            print("--dry-run demande une entree (-d).", file=sys.stderr)
            sys.exit(2)
        else:
            print(format_command(live.producer_command(live_args)))
        return

    require_deps()
    if live_args is not None and shutil.which("ffmpeg") is None:
        print("ffmpeg introuvable dans le PATH (winget install --id Gyan.FFmpeg).", file=sys.stderr)
        sys.exit(1)
    if args.gui:
        try:
            import tkinter  # noqa: F401
        except ImportError:
            print("tkinter n'est pas disponible: --gui ne peut pas demarrer.", file=sys.stderr)
            sys.exit(1)
    run_app(args, live, live_args, (render_w, render_h), gui_loader=lambda: __import__("gl_gui"))


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        pass
