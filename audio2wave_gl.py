#!/usr/bin/env python3
"""POC casual-overlay : visuel audio2wave_live en fenetre OpenGL, logo incruste et
deformations reactives a l'audio du micro.

    python audio2wave_gl.py --list-devices            # entrees dshow (ffmpeg)
    python audio2wave_gl.py --list-audio-devices      # entrees sounddevice
    python audio2wave_gl.py -d "Microphone (Realtek(R) Audio)" --dry-run
    python audio2wave_gl.py -d "Microphone (Realtek(R) Audio)"
    python audio2wave_gl.py --synthetic --no-fullscreen   # sans ffmpeg ni micro

Dependances : pip install -r requirements-gl.txt (en plus de ffmpeg dans le PATH).

Reutilise du depot audio2wave (dependance, importee depuis --a2w-dir, par defaut
../audio2wave) :
  - audio2wave_live.parse_args()      : toutes les options du visuel (via --live-args),
  - audio2wave_live.producer_command() / spawn_producer() : commande ffmpeg identique,
  - common.list_audio_devices()       : inventaire dshow.
Reimplemente ici : lecteur de frames (derniere frame ENTIERE gagne), fenetre glfw,
rendu moderngl, capture sounddevice, analyse FFT/kick. Aucun fichier d'audio2wave
n'est modifie.
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


import diagnostics  # noqa: E402  (stdlib seulement : sans dependance a installer)
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


def require_deps() -> None:
    if MISSING:
        print(f"Dependances manquantes: {', '.join(dict.fromkeys(MISSING))}.\n"
              "Installe-les avec:  pip install -r requirements-gl.txt", file=sys.stderr)
        sys.exit(1)


SCRIPT_DIR = Path(__file__).resolve().parent


def bundled_paths(script_dir: Path, env_path: str = "") -> tuple[Path, str]:
    """Version portable : (dossier d'audio2wave, PATH) quand le programme est livre avec ses dependances. audio2wave/ a cote du
    programme l'emporte sur le depot voisin ; ffmpeg/ (ffmpeg.exe, ffprobe.exe) est ajoute en tete du PATH, donc `shutil.which`,
    audio2wave et nos sous-processus le trouvent sans installation. Sans ces dossiers : comportement d'origine."""
    a2w = script_dir / "audio2wave"
    a2w_dir = a2w if (a2w / "audio2wave_live.py").is_file() else script_dir.parent / "audio2wave"
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
                        "(fenetre OpenGL) ne s'ouvre qu'au bouton « Demarrer la projection »")
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
                   help=f"Depot audio2wave (defaut: {DEFAULT_A2W_DIR})")
    p.add_argument("--diagnostic", action="store_true",
                   help="Verifie ce PC (ffmpeg, OpenGL, micro, modules...) et quitte : code 1 s'il y a une erreur")
    p.add_argument("--list-devices", action="store_true", help="Liste les entrees DirectShow et quitte")
    p.add_argument("--list-audio-devices", action="store_true", help="Liste les entrees sounddevice et quitte")
    p.add_argument("--dry-run", action="store_true", help="Affiche la commande ffmpeg sans rien lancer")
    p.add_argument("--stats", action="store_true", help="Affiche fps, upload texture et temps GPU toutes les 5 s")
    p.add_argument("--max-seconds", type=float, default=None, help="Quitte apres N secondes (tests)")
    p.add_argument("--screenshot", default=None, help="Sauve la derniere image dans ce PNG a la sortie (tests)")
    return p.parse_args()


def parse_pair(text: str, what: str, sep: str) -> tuple[float, float]:
    try:
        a, b = (float(x) for x in text.lower().split(sep, 1))
        return a, b
    except ValueError:
        print(f"{what} invalide: {text}", file=sys.stderr)
        sys.exit(2)


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
    run_app(args, live, live_args, (render_w, render_h))


class AudioController:
    """Entree audio d'analyse (sounddevice ou synthetique) + analyseur, avec changement
    d'entree a chaud (GUI). L'extracteur est recree si la frequence d'echantillonnage change."""

    def __init__(self, ring: RingBuffer, sensitivity: float, synthetic: bool, device=None):
        self.ring = ring
        self.synthetic = synthetic
        if synthetic:
            self.src = SyntheticAudio(ring)
            self.rate = 48000
            self.name = "synthetique (kick 60 Hz, 120 BPM)"
        else:
            self.src = SoundDeviceInput(device, ring)
            self.rate = self.src.rate
            self.name = self.src.name
        self.analyzer = AudioAnalyzer(ring, FeatureExtractor(self.rate, sensitivity=sensitivity))

    def start(self) -> None:
        self.analyzer.start()
        self.src.start()

    def stop(self) -> None:
        self.analyzer.stop()
        try:
            self.src.stop()
        except Exception:
            pass

    def switch(self, device) -> str:
        """Bascule sur une autre entree sounddevice. Leve une exception si elle est refusee
        (l'entree precedente reste alors active)."""
        new = SoundDeviceInput(device, self.ring)
        new.start()
        old, self.src = self.src, new
        try:
            old.stop()
        except Exception:
            pass
        if new.rate != self.rate:
            self.rate = new.rate
            self.analyzer.extractor = FeatureExtractor(
                new.rate, sensitivity=self.analyzer.extractor.sensitivity)
        self.name = new.name
        return new.name

    @staticmethod
    def input_choices() -> list[tuple[int, str]]:
        hostapis = sd.query_hostapis()
        return [(i, f"[{hostapis[d['hostapi']]['name'].replace('Windows ', '')}] {d['name']}")
                for i, d in enumerate(sd.query_devices()) if d["max_input_channels"] > 0]


class Session:
    """Etat partage entre le fil GL, la GUI (fil principal) et les fils de fond.

    `params` (reglages du rendu) est relu a chaque image par le fil GL et muté librement par la
    GUI (affectations atomiques). `commands` transporte les actions qui doivent s'executer DANS
    le fil GL (tous les appels glfw doivent partir du fil qui a initialise glfw)."""

    def __init__(self, args, params, live, live_args, render_size, win_size, audio, manager, reader):
        self.args, self.params = args, params
        self.live, self.live_args = live, live_args
        self.render_size, self.win_size = render_size, win_size
        self.audio, self.manager, self.reader = audio, manager, reader
        self.status: dict = manager.status if manager else {}
        self.status.setdefault("fps", 0.0)
        self.commands: queue.SimpleQueue = queue.SimpleQueue()
        self.stop_event = threading.Event()               # fermeture de tout le programme
        self.finished_event = threading.Event()
        self.proj_stop = threading.Event()                # fermeture de la projection seule (la GUI reste ouverte)
        self.projection: Projection | None = None         # avec --gui : la fenetre de rendu s'ouvre a la demande
        self.gui_point_fn = None           # GUI : point de la fenetre de reglages (fil Tk) ; `gui_point` en est la copie lue par le fil GL
        self.gui_point: tuple[int, int] | None = None
        self.renderer: Renderer | None = None
        self.window: Window | None = None
        self.mode = "live"                 # source du fond : "live" (ffmpeg), "snap" ou "ridge" (py_modes)
        self.pysrc = None                  # PyModeSource courant (modes snap / ridge)
        self.mode_args: dict = {}          # options de chaque mode Snap / Ridge, gardees entre deux bascules
        self.scene_requests: queue.SimpleQueue = queue.SimpleQueue()   # F1..F9 (fil GL) -> la GUI applique la scene


def run_app(args, live, live_args, render_size: tuple[int, int]) -> None:
    sessionlog.setup()
    sessionlog.install_hooks()
    sessionlog.event(f"Lancement (gui={bool(args.gui)}, synthetic={bool(args.synthetic)})")
    params = load_params()
    params, recovery_msg = recover_autosave(params)
    if args.sensitivity is not None:
        params["sensitivity"] = args.sensitivity
    params["_automation"] = merge_automation(params.get("_automation"))
    if args.automation is not None:
        params["auto_master"] = 1.0 if args.automation else 0.0
    if args.bg_video is not None:
        if not Path(args.bg_video).is_file():
            print(f"Video introuvable: {args.bg_video}", file=sys.stderr)
            sys.exit(2)
        params["bg_video"] = str(Path(args.bg_video).resolve())
        params["bg_mode"] = "video"
    if args.background is not None:
        params["bg_mode"] = args.background
    if args.text is not None:
        params["logo_source"] = "text"
        params["text_content"] = args.text.replace("\\n", "\n")
    if args.logo_video is not None:
        if not Path(args.logo_video).is_file():
            print(f"Video introuvable: {args.logo_video}", file=sys.stderr)
            sys.exit(2)
        params["logo_source"] = "video"
        params["logo_video"] = str(Path(args.logo_video).resolve())
    if args.logo_key is not None:
        params["logo_key"] = "" if args.logo_key.lower() == "none" else args.logo_key
    if args.logo is not None:
        params["logo_source"] = "image"
        if args.logo.lower() == "none":
            params["logo_path"] = ""
        elif not Path(args.logo).is_file():
            print(f"Logo introuvable: {args.logo} (--logo none pour s'en passer)", file=sys.stderr)
            sys.exit(2)
        else:
            params["logo_path"] = str(Path(args.logo).resolve())
    if args.logo_scale is not None:
        params["logo_scale"] = args.logo_scale
    if args.logo_pos is not None:
        params["logo_x"], params["logo_y"] = parse_pair(args.logo_pos, "--logo-pos", ",")
    win_size = tuple(int(v) for v in parse_pair(args.size or "1280x720", "--size", "x"))

    ring = RingBuffer()
    device = None if args.synthetic else find_input_device(args.audio_device, args.device)
    audio = AudioController(ring, params["sensitivity"], args.synthetic, device)
    print(f"Analyse audio: {audio.name} ({audio.rate} Hz)"
          + ("" if args.synthetic else f", latence capture ~{audio.src.latency_ms:.0f} ms") + ".")

    frame_size = render_size[0] * render_size[1] * 3
    manager = None
    if args.synthetic:
        reader = FrameReader(SyntheticVideoStream(render_size[0], render_size[1], args.fps), frame_size)
    elif live_args.device:
        manager = ProducerManager(live, live_args, frame_size, {})
        reader = manager.reader
    else:
        # --gui sans -d: pas de producteur tant qu'aucune entree n'est choisie dans la fenetre.
        manager = NoDeviceManager(live, live_args, frame_size)
        reader = manager.reader

    s = Session(args, params, live, live_args, render_size, win_size, audio, manager, reader)
    if recovery_msg:
        s.status["msg"] = recovery_msg
        print(recovery_msg, file=sys.stderr)
    clean = False
    try:
        audio.start()
        if manager is not None:
            manager.start()
        else:
            reader.start()
        if args.gui:
            import gl_gui
            gl_gui.require_audio2wave_api(live)          # depot audio2wave trop ancien : message clair, avant de lancer le rendu
            s.projection = Projection(s)                 # la GUI seule s'ouvre ; la projection part du bouton (ou de --projeter)
            if args.projeter:
                print(s.projection.start())
            gl_gui.run_gui(s, live)
        else:
            gl_main(s)
        clean = True
    finally:
        sessionlog.event("Arret propre" if clean else "Arret anormal (l'auto-sauvegarde est gardee)")
        if clean:
            clear_autosave()
        s.stop_event.set()
        if s.projection is not None:
            s.projection.stop(wait=5.0)
        if s.pysrc is not None:
            s.pysrc.stop()
        audio.stop()
        if manager is not None:
            manager.stop()
        else:
            reader.stop()


class Projection:
    """Fenetre de rendu (fil GL) ouverte et fermee a la demande, pour la GUI : `start()` / `stop()` autant de fois qu'on veut,
    la GUI, l'audio et le producteur ne sont pas touches. Par defaut elle s'ouvre EN FENETRE (sur un autre ecran que
    les reglages s'il y en a un) ; le plein ecran se demande (--fullscreen, case, touche F).
    `target` s'injecte pour les tests."""

    def __init__(self, s: Session, target=None):
        self.s = s
        self._target = target
        self.thread: threading.Thread | None = None

    def running(self) -> bool:
        return self.thread is not None and self.thread.is_alive()

    def start(self) -> str:
        """Ouvre la projection. Rend le message a afficher."""
        s = self.s
        if self.running():
            return "La projection est deja ouverte"
        a = s.args
        try:
            s.gui_point = s.gui_point_fn() if s.gui_point_fn else None      # appele depuis le fil de la GUI
        except Exception:
            s.gui_point = None
        a.fullscreen = resolve_fullscreen(getattr(a, "fullscreen_request", None))
        s.proj_stop.clear()
        while True:                                     # commandes envoyees pendant que la projection etait fermee : perimees
            try:
                s.commands.get_nowait()
            except queue.Empty:
                break
        self.thread = threading.Thread(target=self._run, name="gl", daemon=True)
        self.thread.start()
        if a.fullscreen:
            return "Projection ouverte en plein ecran"
        return "Projection ouverte en fenetre (F ou la case Plein ecran pour le plein ecran)"

    MAX_RESTARTS = 3                                      # reprises automatiques tolerees...
    RESTART_WINDOW_S = 120.0                              # ...sur cette fenetre (au-dela : on s'arrete, il y a un vrai probleme)
    RESTART_DELAY_S = 1.0

    def _run(self) -> None:
        """Fil de la projection. Si la fenetre meurt sur une erreur (pas une fermeture voulue : Echap, bouton, arret de la GUI), elle
        est relancee toute seule avec les reglages et la scene du moment (ils vivent dans `s.params`, pas dans la fenetre)."""
        starts: list[float] = []
        while True:
            try:
                sessionlog.event("Projection : fenetre ouverte")
                (self._target or gl_main)(self.s)
                sessionlog.event("Projection : fenetre fermee")
                return
            except SystemExit as exc:                    # gl_main quitte par sys.exit : la GUI continue
                self.s.status["msg"] = "Projection impossible (voir la console)" if exc.code else self.s.status.get("msg", "")
                sessionlog.event(f"Projection : arret (code {exc.code})")
                return
            except Exception as exc:
                sessionlog.error("Projection : la fenetre GL est morte", exc)
                print(f"Projection arretee: {exc}", file=sys.stderr)
                now = time.monotonic()
                starts = [t for t in starts if now - t < self.RESTART_WINDOW_S] + [now]
                if self.s.proj_stop.is_set() or self.s.stop_event.is_set() or len(starts) > self.MAX_RESTARTS:
                    self.s.status["msg"] = f"Projection arretee: {exc}" + (
                        " (trop de reprises : bouton Demarrer pour reessayer)" if len(starts) > self.MAX_RESTARTS else "")
                    return
                self.s.status["msg"] = f"Projection relancee apres une erreur ({exc})"
                sessionlog.event(f"Projection : reprise automatique {len(starts)}/{self.MAX_RESTARTS}")
                time.sleep(self.RESTART_DELAY_S)
                if self.s.proj_stop.is_set() or self.s.stop_event.is_set():
                    return

    def stop(self, wait: float = 0.0) -> None:
        """Ferme la projection (attend au plus `wait` secondes)."""
        self.s.proj_stop.set()
        if wait and self.thread is not None and self.thread is not threading.current_thread():
            self.thread.join(wait)


class NoDeviceManager(ProducerManager):
    """ProducerManager sans producteur (GUI lancee sans -d): le premier request_restart()
    avec une entree choisie demarre le flux."""

    def __init__(self, live, live_args, frame_size: int):
        self.live, self.args, self.frame_size, self.status = live, copy.copy(live_args), frame_size, {}
        self._spawn = lambda a: spawn_producer(live, a)
        self.producer = None
        self.reader = FrameReader(_EmptyStream(), frame_size, hold_on_eof=True)
        self.restarts = 0
        self._request = None
        self._lock = threading.Lock()
        self._wake = threading.Event()
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._run, daemon=True)
        self.status["live"] = "Choisis une entree audio dans la fenetre de reglages"


class _EmptyStream:
    def read(self, n: int) -> bytes:
        return b""


def logo_signature(params: dict) -> tuple:
    """Ce qui determine la texture du logo: si ca change, il faut la regenerer. Les autres
    reglages (position, taille, opacite, effets...) sont relus a chaque image, sans regeneration."""
    if params.get("logo_source") == "text":
        return ("text", params.get("text_content", ""), params.get("text_font", "auto"),
                params.get("text_color", "#ffffff"), params.get("text_align", "center"))
    if params.get("logo_source") == "video":
        key = str(params.get("logo_key", "")).strip().lstrip("#").lower()
        key = key if len(key) == 6 and all(c in HEX_DIGITS for c in key) else ""
        return ("video", params.get("logo_video", ""), key)
    return ("image", params.get("logo_path", ""))


def apply_logo_request(s: Session, renderer: Renderer) -> None:
    """(Re)genere la texture du logo si ce qui la determine a change (fil GL): image PNG, texte
    tape a la main rendu par Pillow, ou video animee (ffmpeg en boucle)."""
    if renderer.logo_video is not None:
        err = renderer.logo_video.error()
        if err:                                   # ffmpeg mort sans image (codec absent, fichier corrompu)
            s.status["logo"] = f"Video illisible: {err}"
            print(s.status["logo"], file=sys.stderr)
            renderer.set_logo(None, "")
    signature = logo_signature(s.params)
    if signature == renderer.logo_requested:
        return
    renderer.logo_requested = signature
    if signature[0] == "video":
        want = signature[1]
        if not want:
            renderer.set_logo(None, "")
            s.status["logo"] = "Aucune video"
            return
        if not Path(want).is_file():
            s.status["logo"] = f"Video introuvable: {want}"
            print(s.status["logo"], file=sys.stderr)
            return
        try:
            video = LogoVideo(want, signature[2])
        except Exception as exc:
            s.status["logo"] = f"Video illisible ({Path(want).name}): {exc}"
            print(s.status["logo"], file=sys.stderr)
            return
        renderer.set_logo_video(video, want)
        s.status["logo"] = f"Video: {Path(want).name}"
        return
    if signature[0] == "text":
        try:
            rgba = render_text(signature[1], signature[2], signature[3], signature[4])
        except Exception as exc:
            s.status["logo"] = f"Texte illisible: {exc}"
            print(s.status["logo"], file=sys.stderr)
            return
        renderer.set_logo(rgba, "", "text")
        first = signature[1].strip().splitlines()[0] if signature[1].strip() else ""
        s.status["logo"] = f"Texte: {first[:24]}" if rgba is not None else "Texte vide"
        return
    want = signature[1]
    if not want or want.lower() == "none":
        renderer.set_logo(None, "")
        s.status["logo"] = "Aucun logo"
        return
    path = Path(want)
    if not path.is_file():
        s.status["logo"] = f"Logo introuvable: {want}"
        print(s.status["logo"], file=sys.stderr)
        return
    try:
        renderer.set_logo(load_logo(path), want, "image")
        s.status["logo"] = f"Logo: {path.name}"
    except Exception as exc:
        s.status["logo"] = f"Logo illisible ({path.name}): {exc}"
        print(s.status["logo"], file=sys.stderr)


_GL_SLOT = threading.Lock()          # une seule fenetre de projection a la fois, quel que soit le chemin qui l'ouvre


def gl_main(s: Session) -> None:
    """Un seul `_gl_main` en marche : un second appel (demarrage pendant la fermeture du premier, double declenchement)
    attend la fin de l'ancien au plus 3 s, puis renonce plutot que d'ouvrir une deuxieme fenetre par-dessus la GUI."""
    if not _GL_SLOT.acquire(timeout=3.0):
        s.status["msg"] = "La projection est deja ouverte"
        return
    try:
        _gl_main(s)
    finally:
        _GL_SLOT.release()


def _gl_main(s: Session) -> None:
    """Fenetre GL + boucle de rendu. Tous les appels glfw partent de CE fil (init, creation,
    evenements, destruction): avec --gui il tourne a cote de tkinter, qui garde le fil principal."""
    args = s.args
    window = None
    try:
        if not glfw.init():
            print("Initialisation de glfw impossible.", file=sys.stderr)
            sys.exit(1)
        avoid = getattr(s, "gui_point", None)
        monitor = pick_monitor(args.monitor, avoid)
        window = Window(monitor, s.win_size, args.fullscreen, shared=monitor_contains(monitor, avoid))
        ctx = moderngl.create_context()
        mode = glfw.get_video_mode(monitor)
        wx, wy = glfw.get_window_pos(window.handle)
        ww, wh = glfw.get_window_size(window.handle)
        print(f"OpenGL: {ctx.info['GL_RENDERER']} | moniteur en ({window.mx},{window.my}) "
              f"{window.mw}x{window.mh} @ {mode.refresh_rate} Hz | fenetre en ({wx},{wy}) {ww}x{wh} | "
              f"rendu ffmpeg {s.render_size[0]}x{s.render_size[1]}")
        renderer = Renderer(ctx, s.render_size, None, "")
        renderer.logo_requested = None          # force le chargement du logo demande a la 1re image
        renderer.hud = args.hud
        s.renderer, s.window = renderer, window
        loop(s, window, ctx, renderer)
    finally:
        if getattr(s, "bgvideo", None) is not None:
            s.bgvideo.stop()
        if s.renderer is not None:
            s.renderer.release_logo_video()
        s.renderer = None
        if window is not None:
            glfw.destroy_window(window.handle)
        glfw.terminate()
        if getattr(args, "gui", False):
            s.status["msg"] = "Projection fermee"          # la GUI reste ouverte (bouton pour la rouvrir)
            s.status["fps"] = 0.0
        else:
            s.finished_event.set()


def loop(s: Session, window: Window, ctx, renderer: Renderer) -> None:
    args, params, reader = s.args, s.params, s.reader
    analyzer = s.audio.analyzer
    handle = window.handle
    quit_flag = {"v": False}
    automation = AutomationEngine()
    silence = SilenceGuard()

    def status_line() -> str:
        on = "".join(str(int(v)) for v in params["fx_on"])
        return (f"intensite {params['master']:.1f} | sensibilite kick {params['sensitivity']:.2f} | "
                f"effets {on} ({'/'.join(FX_NAMES)})"
                + ("" if params["fx_link"] >= 0.5 else
                   f" | logo {''.join(str(int(v)) for v in params['fxl_on'])} (separes du fond)"))

    def gui_pt():
        return getattr(s, "gui_point", None)

    def act(name: str) -> None:
        """Actions communes aux touches et aux commandes de la GUI (executees dans ce fil)."""
        if name == "quit":
            quit_flag["v"] = True
        elif name == "fullscreen":
            if not window.fullscreen:
                window.retarget(pick_monitor(args.monitor, gui_pt()), gui_pt())     # un projecteur branche depuis le lancement est pris en compte
            window.toggle_fullscreen()
        elif name.startswith("fullscreen="):      # etat explicite (case « Plein ecran » de la GUI, preset qui la coche)
            want = name.endswith("1")
            if want and not window.fullscreen:
                window.retarget(pick_monitor(args.monitor, gui_pt()), gui_pt())
                # seul l'ecran des reglages est interdit (un plein ecran le recouvrirait) ; point inconnu : l'ecran principal
                on_gui = monitor_contains(window.monitor, gui_pt()) if gui_pt() is not None else None
                if fullscreen_blocked(on_gui, window.is_primary, args.monitor, getattr(args, "fullscreen_request", None)):
                    s.status["msg"] = "Plein ecran refuse sur l'ecran des reglages : projette sur un autre ecran (ou --fullscreen)"
                    print(s.status["msg"], file=sys.stderr)
                    return
            if window.fullscreen != want:
                window.toggle_fullscreen()
        elif name == "hud":
            renderer.hud = not renderer.hud
        elif name == "bg":
            modes = ["live", "pattern"] + (["video"] if params.get("bg_video") else [])     # video seulement si un fichier est choisi
            params["bg_mode"] = modes[(modes.index(params["bg_mode"]) + 1) % len(modes)] if params["bg_mode"] in modes else "live"
            print(f"fond: {params['bg_mode']}")
        elif name == "auto":
            params["auto_master"] = 0.0 if params["auto_master"] >= 0.5 else 1.0
            print(f"automations: {'actives' if params['auto_master'] >= 0.5 else 'figees'}")
        elif name == "reload":
            try:
                renderer.reload_shaders()
                # ne pas ecraser ce que la GUI vient de regler (ni l'etat des automations qu'elle edite)
                keep = {*params_schema.RELOAD_KEEP_KEYS, "_automation"}
                params.update({k: v for k, v in load_params().items() if k not in keep})
                print("Shaders et reglages recharges.")
                s.status["msg"] = "Shaders recharges"
            except ShaderError as exc:
                print(f"Erreur de shader (ancien programme conserve):\n{exc}", file=sys.stderr)
                s.status["msg"] = "Erreur de shader (voir la console), ancien programme conserve"
        elif name == "save":
            save_params(params)
            print(f"Reglages sauves dans {core_params.PARAMS_PATH}")
            s.status["msg"] = f"Reglages sauves ({core_params.PARAMS_PATH.name})"
        elif name == "holo":
            params["holo_on"] = 0.0 if float(params["holo_on"]) >= 0.5 else 1.0
            print(f"halo holographique: {'on' if params['holo_on'] >= 0.5 else 'off'}")
        elif name == "melt":
            params["melt_on"] = 0.0 if float(params["melt_on"]) >= 0.5 else 1.0
            print(f"fonte acide du logo: {'on' if params['melt_on'] >= 0.5 else 'off'}")
        elif name == "cell":
            params["cell_on"] = 0.0 if float(params["cell_on"]) >= 0.5 else 1.0
            print(f"cellules du logo: {'on' if params['cell_on'] >= 0.5 else 'off'}")
        elif name == "flash":
            renderer.flash_t = time.monotonic()           # transition du VJ (demandee par la GUI)
        elif name == "panic":
            changed = panic(params)                        # tout de suite, dans ce fil : pas d'attente de la GUI
            sessionlog.event("PANIQUE : " + (", ".join(changed) if changed else "rien a couper"))
            s.status["msg"] = "PANIQUE : effets, strobe et montee coupes"
            renderer.flash_t = time.monotonic()
            if args.gui:
                s.scene_requests.put("panic")              # la GUI met le VJ en pause et remet les curseurs a jour
        elif name in ("vj", "vjnext", "lock", "back", "tap", "help"):
            if args.gui:
                s.scene_requests.put(name)                # Espace / fleche droite : la GUI pilote le VJ
            else:
                print("VJ: disponible avec --gui seulement", file=sys.stderr)
        elif name.startswith("scene") and name[5:].isdigit():
            if args.gui:
                s.scene_requests.put(int(name[5:]))       # la GUI (fil principal) applique la scene
            else:
                print("Scenes: disponibles avec --gui seulement", file=sys.stderr)
        elif name == "link":
            set_fx_link(params, float(params["fx_link"]) < 0.5)
            print(f"effets fond/logo: {'lies' if params['fx_link'] >= 0.5 else 'separes'}")
        elif name.startswith("fxl") and name[3:].isdigit():
            if float(params["fx_link"]) >= 0.5:      # toucher a la couche du logo la delie (reglages copies)
                set_fx_link(params, False)
            i = int(name[3:])
            params["fxl_on"][i] = 0 if params["fxl_on"][i] else 1
            print(f"effet {FX_NAMES[i]} (logo): {'on' if params['fxl_on'][i] else 'off'}")
        elif name.startswith("fx") and name[2:].isdigit():
            i = int(name[2:])
            params["fx_on"][i] = 0 if params["fx_on"][i] else 1
            print(f"effet {FX_NAMES[i]}: {'on' if params['fx_on'][i] else 'off'}")
        elif name == "master+":
            params["master"] = min(params["master"] + 0.1, 2.0)
            print(status_line())
        elif name == "master-":
            params["master"] = max(params["master"] - 0.1, 0.0)
            print(status_line())
        elif name == "sens+":
            params["sensitivity"] = min(params["sensitivity"] * 1.15, 4.0)
            print(status_line())
        elif name == "sens-":
            params["sensitivity"] = max(params["sensitivity"] / 1.15, 0.25)
            print(status_line())

    key_actions = {glfw.KEY_ESCAPE: "quit", glfw.KEY_F: "fullscreen", glfw.KEY_H: "hud", glfw.KEY_B: "bg", glfw.KEY_T: "auto",
                   glfw.KEY_R: "reload", glfw.KEY_P: "save",
                   glfw.KEY_EQUAL: "master+", glfw.KEY_KP_ADD: "master+", glfw.KEY_PAGE_UP: "master+",
                   glfw.KEY_MINUS: "master-", glfw.KEY_KP_SUBTRACT: "master-", glfw.KEY_PAGE_DOWN: "master-",
                   glfw.KEY_UP: "sens+", glfw.KEY_DOWN: "sens-"}
    for i, k in enumerate((glfw.KEY_1, glfw.KEY_2, glfw.KEY_3, glfw.KEY_4, glfw.KEY_5)):
        key_actions[k] = f"fx{i}"
    for i, k in enumerate((glfw.KEY_KP_1, glfw.KEY_KP_2, glfw.KEY_KP_3, glfw.KEY_KP_4, glfw.KEY_KP_5)):
        key_actions[k] = f"fx{i}"
    repeatable = {"master+", "master-", "sens+", "sens-"}

    key_actions[glfw.KEY_L] = "link"
    key_actions[glfw.KEY_C] = "holo"
    key_actions[glfw.KEY_M] = "melt"
    key_actions[glfw.KEY_V] = "cell"
    key_actions[glfw.KEY_BACKSPACE] = "back"     # scene precedente (la GUI la gere : voir poll_scene_requests)
    key_actions[glfw.KEY_K] = "lock"             # verrou de scene (la GUI le gere : voir poll_scene_requests)
    key_actions[glfw.KEY_S] = "strobe"          # strobe : maintenu (manuel) ou arme / desarme (kick, cadence)
    key_actions[glfw.KEY_SPACE] = "vj"           # VJ : lecture / pause
    key_actions[glfw.KEY_X] = "panic"            # PANIQUE : coupe effets, strobe, montee... et rend une image propre
    key_actions[glfw.KEY_PERIOD] = "tap"         # tap tempo (la GUI le gere)
    key_actions[glfw.KEY_SLASH] = "help"         # aide des raccourcis (la GUI l'affiche)
    key_actions[glfw.KEY_RIGHT] = "vjnext"       # VJ : scene suivante
    for n in range(9):
        key_actions[getattr(glfw, f"KEY_F{n + 1}")] = f"scene{n}"       # F1..F9 : scenes

    def on_key(win, key, scancode, action, mods):
        name = key_actions.get(key)
        if name and mods & glfw.MOD_SHIFT and name in ("fx0", "fx1", "fx2", "fx3"):
            name = "fxl" + name[2:]              # Maj + 1..4: meme effet sur la couche du logo
        if name == "strobe":
            if action == glfw.PRESS:
                strobe_press(params)
            elif action == glfw.RELEASE:
                strobe_release(params)
        elif name and (action == glfw.PRESS or (action == glfw.REPEAT and name in repeatable)):
            act(name)

    glfw.set_key_callback(handle, on_key)
    print("Echap quitte | F plein ecran | H barres debug | B fond live/motif | T automations | 1-5 effets (Maj+1-4: "
          "logo) | L lier fond/logo | C halo holographique | +/- intensite | haut/bas sensibilite kick | R recharge shaders | P sauve reglages | F1-F9 scenes | Espace VJ | -> scene suivante | X PANIQUE | . tap tempo | / aide (avec --gui)")
    print(status_line())

    bgvideo = s.bgvideo = BgVideoManager(s.render_size, s.status)     # fond "video" : decodage lance / arrete selon les reglages
    query = ctx.query(time=True)      # une image sur 8 (toutes avec --stats) : le garde-fou de charge et les mesures
    guard = PerfGuard(glfw.get_video_mode(window.monitor).refresh_rate or 60)
    frame_i = 0
    perf_warned = False
    t0 = time.monotonic()
    last_title = t0
    frames = 0
    win_frames = 0
    up_ms = gpu_ms = 0.0
    up_n = gpu_n = 0
    last_stats = t0
    warned = False
    last_restarts = 0
    last_beats = 0
    refresh = glfw.get_video_mode(window.monitor).refresh_rate or 60
    cap = refresh * 1.02 if args.fps_cap is None else args.fps_cap
    period = 1.0 / cap if cap > 0 else 0.0
    deadline = time.monotonic()
    last_read = 0
    max_gap = 0.0
    prev_frame_t = t0
    shot_done = False
    if args.screenshot and args.max_seconds is None:
        args.max_seconds = 3.0

    while (not glfw.window_should_close(handle) and not quit_flag["v"] and not s.stop_event.is_set()
           and not s.proj_stop.is_set()):
        glfw.poll_events()
        while True:
            try:
                act(s.commands.get_nowait())
            except queue.Empty:
                break
        now = time.monotonic()
        max_gap = max(max_gap, now - prev_frame_t)
        prev_frame_t = now
        if args.max_seconds is not None and now - t0 >= args.max_seconds:
            break

        apply_logo_request(s, renderer)
        automation.step(params, now)
        analyzer.extractor.sensitivity = params["sensitivity"]

        u0 = time.perf_counter()
        reader = s.reader                       # change quand on bascule Live / Snap / Ridge
        bg_reader = bgvideo.reader_for(params)
        if bg_reader is not None:               # fond "video" pret : son flux remplace celui d'audio2wave
            reader = bg_reader
        if reader.upload_to(renderer.video_tex):
            up_ms += (time.perf_counter() - u0) * 1000.0
            up_n += 1
        producer = s.manager.producer if s.manager and s.mode == "live" else None
        if s.manager and s.manager.restarts != last_restarts:
            last_restarts, warned = s.manager.restarts, False
        if producer is not None and not warned and producer.poll() is not None:
            print(f"Le producteur ffmpeg s'est arrete (code {producer.returncode}): "
                  "derniere image conservee.", file=sys.stderr)
            s.status["live"] = f"ffmpeg arrete (code {producer.returncode}): derniere image conservee"
            warned = True

        state = analyzer.latest()
        silence.update(params, state, now)
        size = window.framebuffer_size()
        sample = args.stats or frame_i % 8 == 0
        frame_i += 1
        if sample:
            with query:
                renderer.draw(ctx.screen, size, state, params, now - t0)
        else:
            renderer.draw(ctx.screen, size, state, params, now - t0)
        if args.screenshot and not shot_done and now - t0 >= args.max_seconds - 0.2:
            # Lecture AVANT swap_buffers: apres, le tampon arriere est indefini.
            data = ctx.screen.read(viewport=(0, 0, size[0], size[1]), components=3)
            img = PIL_Image.frombytes("RGB", size, data).transpose(PIL_Image.FLIP_TOP_BOTTOM)
            Path(args.screenshot).parent.mkdir(parents=True, exist_ok=True)
            img.save(args.screenshot)
            print(f"Capture: {args.screenshot}")
            shot_done = True
        s.status["noise"] = renderer.noise_out
        glfw.swap_buffers(handle)
        if period:
            # Le vsync n'est pas garanti en borderless plein ecran (mesure: 108 fps sur un
            # moniteur 59 Hz): limiteur logiciel, inoperant quand le vsync tient deja le rythme.
            deadline += period
            delay = deadline - time.monotonic()
            if delay > 0:
                time.sleep(delay)
            elif delay < -period:
                deadline = time.monotonic()
        frames += 1
        win_frames += 1
        if sample:
            elapsed = query.elapsed / 1e6
            gpu_ms += elapsed
            gpu_n += 1
            warning = guard.update(elapsed, time.monotonic(), params)
            if warning:
                s.status["perf"] = warning
                if not perf_warned:
                    print(warning, file=sys.stderr)
                    perf_warned = True
            else:
                s.status.pop("perf", None)
                perf_warned = False

        now = time.monotonic()
        if now - last_title >= 0.5:
            fps = win_frames / (now - last_title)
            s.status["fps"] = fps
            glfw.set_window_title(handle, f"casual-overlay GL - {fps:.0f} fps - int {params['master']:.1f}"
                                          f" - sens {params['sensitivity']:.2f}")
            last_title = now
            win_frames = 0
        if args.stats and now - last_stats >= 5.0:
            span = now - last_stats
            print(f"[stats] {frames / span:.1f} fps | video {(reader.frames_read - last_read) / span:.1f} frames/s "
                  f"| upload {up_ms / max(up_n, 1):.2f} ms ({up_n / span:.1f}/s) "
                  f"| gpu {gpu_ms / max(gpu_n, 1):.2f} ms | pire intervalle {max_gap * 1000:.0f} ms "
                  f"| kicks {analyzer.beats - last_beats}", flush=True)
            max_gap = 0.0
            last_stats, frames, up_ms, up_n, gpu_ms, gpu_n = now, 0, 0.0, 0, 0.0, 0
            last_beats = analyzer.beats
            last_read = reader.frames_read


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        pass
