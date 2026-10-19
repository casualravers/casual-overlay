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


def require_deps() -> None:
    if MISSING:
        print(f"Dependances manquantes: {', '.join(dict.fromkeys(MISSING))}.\n"
              "Installe-les avec:  pip install -r requirements-gl.txt", file=sys.stderr)
        sys.exit(1)


SCRIPT_DIR = Path(__file__).resolve().parent
SHADER_DIR = SCRIPT_DIR / "gl_shaders"


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

def _hash01(i: int, seed: int) -> float:
    """Hachage entier -> [0, 1) (deterministe, sans etat)."""
    h = (int(i) * 374761393 + (seed + 1) * 668265263) & 0xFFFFFFFF
    h = ((h ^ (h >> 13)) * 1274126177) & 0xFFFFFFFF
    h ^= h >> 16
    return (h & 0xFFFFFF) / float(0xFFFFFF)


def noise_value(x: float, seed: int, smooth: float) -> float:
    """Noise 1D de valeur dans [-1, 1] au point `x` (en valeurs de reseau) : `smooth` = 1 interpole en douceur entre deux
    valeurs au hasard, 0 garde la valeur du reseau jusqu'au suivant (par a-coups)."""
    i = math.floor(x)
    f = x - i
    a = _hash01(i, seed) * 2.0 - 1.0
    b = _hash01(i + 1, seed) * 2.0 - 1.0
    s = f * f * (3.0 - 2.0 * f)
    return a + (b - a) * s * min(max(float(smooth), 0.0), 1.0)


NOISE_SPEED = 3.0          # valeurs tirees par seconde (le noise n'est pas reglable : un seul comportement, prereglé)
NOISE_SMOOTH = 0.8


class NoiseDrive:
    """Le noise unique. Une valeur continue au hasard (`n`, [-1, 1]) dont on tire deux signaux, comme une musique
    imaginaire : `swell()` = houle douce 0..1 (comme les basses) et des COUPS au hasard (`beat()` = enveloppe 1 -> 0
    apres chaque coup, `since` = secondes depuis le dernier, comme le kick). Un coup part quand le noise remonte
    franchement (hysterese + delai minimum : jamais de rafale). La phase est integree image par image."""

    HIT_ON, HIT_OFF, REFRACTORY = 0.30, -0.10, 0.45

    def __init__(self) -> None:
        self.phase = 11.0
        self.n = noise_value(self.phase, 0, NOISE_SMOOTH)
        self.since = 9.0
        self.armed = True

    def step(self, dt: float) -> None:
        self.phase += max(dt, 0.0) * NOISE_SPEED
        self.n = noise_value(self.phase, 0, NOISE_SMOOTH)
        self.since += max(dt, 0.0)
        if self.n < self.HIT_OFF:
            self.armed = True
        if self.armed and self.n > self.HIT_ON and self.since > self.REFRACTORY:
            self.since = 0.0
            self.armed = False

    def swell(self) -> float:
        x = min(max((self.n * 0.5 + 0.5 - 0.35) / 0.6, 0.0), 1.0)
        return x * x * (3.0 - 2.0 * x)

    def beat(self) -> float:
        return math.exp(-self.since / 0.2)


def layer_effects(params: dict) -> tuple[list[float], list[float]]:
    """Intensites effectives (interrupteur x intensite x intensite globale) des quatre effets de
    deformation : (fond, logo). Lie, le logo reprend celles du fond."""
    master = float(params["master"])
    bg = [float(params["fx_on"][i]) * float(params["fx_int"][i]) * master for i in range(FX_EFFECTS)]
    if float(params["fx_link"]) >= 0.5:
        return bg, list(bg)
    return bg, [float(params["fxl_on"][i]) * float(params["fxl_int"][i]) * master for i in range(FX_EFFECTS)]


# --- Rendu ------------------------------------------------------------------------

class ShaderError(Exception):
    pass


def load_logo(path: Path):
    """PNG -> tableau uint8 RGBA premultiplie (premiere ligne = haut), <= 2048 px."""
    return _premultiplied(PIL_Image.open(path).convert("RGBA"))


# Polices proposees dans la GUI (nom affiche -> fichier Windows). Seules celles presentes sur le
# poste sont listees; un chemin vers un .ttf/.otf quelconque est aussi accepte.
TEXT_FONTS = {
    "Bahnschrift": "bahnschrift.ttf", "Arial Black": "ariblk.ttf", "Impact": "impact.ttf",
    "Segoe UI Black": "seguibl.ttf", "Segoe UI Bold": "segoeuib.ttf", "Arial Bold": "arialbd.ttf",
    "Verdana Bold": "verdanab.ttf", "Trebuchet MS Bold": "trebucbd.ttf", "Tahoma Bold": "tahomabd.ttf",
    "Georgia Bold": "georgiab.ttf", "Times New Roman Bold": "timesbd.ttf", "Comic Sans MS Bold": "comicbd.ttf",
    "Consolas Bold": "consolab.ttf", "Courier New Bold": "courbd.ttf",
}
AUTO_FONT_ORDER = ("Bahnschrift", "Arial Black", "Impact", "Segoe UI Black", "Arial Bold")


def font_dirs() -> list[Path]:
    dirs = [Path(os.environ.get("WINDIR", r"C:\Windows")) / "Fonts"]
    local = os.environ.get("LOCALAPPDATA")
    if local:
        dirs.append(Path(local) / "Microsoft" / "Windows" / "Fonts")
    return dirs


def available_fonts() -> dict[str, Path]:
    """Polices de TEXT_FONTS effectivement installees: nom -> chemin."""
    found = {}
    for name, filename in TEXT_FONTS.items():
        for directory in font_dirs():
            if (directory / filename).is_file():
                found[name] = directory / filename
                break
    return found


def load_font(font: str, size: int):
    """Police PIL pour un nom de TEXT_FONTS, un chemin de fichier, ou "auto"; repli sur la police
    integree de Pillow si rien n'est trouve (jamais d'exception: un texte doit toujours s'afficher)."""
    installed = available_fonts()
    candidates: list[Path] = []
    if font and font != "auto":
        if font in installed:
            candidates.append(installed[font])
        elif Path(font).is_file():
            candidates.append(Path(font))
    if not candidates:
        candidates += [installed[name] for name in AUTO_FONT_ORDER if name in installed]
    for path in candidates:
        try:
            return PIL_ImageFont.truetype(str(path), size)
        except OSError:
            continue
    try:
        return PIL_ImageFont.load_default(size)
    except TypeError:          # Pillow < 10.1: pas de taille pour la police integree
        return PIL_ImageFont.load_default()


def render_text(text: str, font: str = "auto", color: str = "#ffffff", align: str = "center",
                size: int = 256):
    """Texte (plusieurs lignes possibles) -> tableau uint8 RGBA premultiplie, comme load_logo().
    None si le texte est vide. Une marge transparente de 0,18 x la taille entoure le texte, pour
    que le contour lumineux ne soit pas coupe par le bord du rectangle."""
    if not text or not text.strip():
        return None
    fnt = load_font(font, size)
    spacing = int(size * 0.15)
    align = align if align in ("left", "center", "right") else "center"
    draw = PIL_ImageDraw.Draw(PIL_Image.new("L", (8, 8)))
    box = draw.multiline_textbbox((0, 0), text, font=fnt, align=align, spacing=spacing)
    # Pillow renvoie des flottants pour un texte sur plusieurs lignes (et des entiers pour une ligne).
    left, top, right, bottom = math.floor(box[0]), math.floor(box[1]), math.ceil(box[2]), math.ceil(box[3])
    pad = int(size * 0.18)
    img = PIL_Image.new("RGBA", (max(right - left, 1) + 2 * pad, max(bottom - top, 1) + 2 * pad), (0, 0, 0, 0))
    r, g, b = (int(round(c * 255)) for c in hex_to_rgb(color, "#ffffff"))
    PIL_ImageDraw.Draw(img).multiline_text((pad - left, pad - top), text, font=fnt, fill=(r, g, b, 255),
                                           align=align, spacing=spacing)
    return _premultiplied(img)


def _premultiplied(img):
    """Image PIL RGBA -> uint8 RGBA a alpha premultiplie, reduite a 2048 px maximum."""
    if max(img.size) > 2048:
        ratio = 2048 / max(img.size)
        img = img.resize((max(1, round(img.width * ratio)), max(1, round(img.height * ratio))),
                         PIL_Image.LANCZOS)
    arr = np.asarray(img, dtype=np.float32)
    arr[:, :, :3] *= arr[:, :, 3:4] / 255.0
    return np.rint(arr).astype(np.uint8)


def logo_layout(width: int, height: int, aspect: float, scale: float, pos: tuple[float, float],
                pulse: float = 0.0, jitter: tuple[float, float] = (0.0, 0.0), fit: str = "box"):
    """(cx, cy, demi-largeur, demi-hauteur) en uv (origine haut gauche). pulse (0.12 = +12 %) et
    jitter ne le font jamais sortir du cadre.
    fit="box" (images): le logo tient dans un carre de cote scale*hauteur, ratio conserve.
    fit="height" (texte): le bloc a pour hauteur scale*hauteur, sa largeur suit le ratio (un
    carre de cote scale*hauteur ecraserait une ligne de texte); reduit s'il depasse le cadre."""
    if fit == "height":
        h_px = max(min(scale * height, 0.95 * height), 2.0)
        w_px = h_px * aspect
        if w_px > 0.95 * width:
            w_px, h_px = 0.95 * width, 0.95 * width / aspect
    else:
        box = max(min(scale * height, 0.95 * min(width, height)), 2.0)
        w_px, h_px = (box, box / aspect) if aspect >= 1.0 else (box * aspect, box)
        if w_px > 0.95 * width:
            w_px, h_px = 0.95 * width, 0.95 * width / aspect
    hw0, hh0 = w_px / width / 2.0, h_px / height / 2.0
    cx = min(max(pos[0] + jitter[0], hw0), 1.0 - hw0)
    cy = min(max(pos[1] + jitter[1], hh0), 1.0 - hh0)
    limit = max(1.0, min(cx / hw0, (1.0 - cx) / hw0, cy / hh0, (1.0 - cy) / hh0))
    s = min(1.0 + pulse, limit) if pulse > 0 else 1.0
    hw, hh = hw0 * s, hh0 * s
    cx = min(max(cx, hw), 1.0 - hw)
    cy = min(max(cy, hh), 1.0 - hh)
    return cx, cy, hw, hh


HOLO_DOWNSCALE = 4      # le champ du halo holographique est calcule a 1/4 de la resolution de l'ecran


class Renderer:
    """Deux passes : scene (video + logo) dans un FBO, puis post-traitement vers `target`."""

    def __init__(self, ctx, video_size: tuple[int, int], logo_rgba=None, logo_path: str = "",
                 shader_dir: Path = SHADER_DIR):
        self.ctx = ctx
        self.shader_dir = shader_dir
        self.video_size = video_size
        self.hud = False
        self.rng = np.random.default_rng(3)
        # Phases d'animation du motif de fond, integrees image par image (un changement de
        # vitesse ne fait ainsi jamais sauter le motif, contrairement a speed * temps).
        self._last_t: float | None = None
        self._bg_scroll = 0.0
        self._bg_flip_t = 0.0
        self._bg_clock = 0.0
        self.noise = NoiseDrive()
        self._strobe_last, self._strobe_beats, self._strobe_seen = -10.0, 0, 0     # strobe : dernier eclair, kicks vus, appuis vus
        self.strobe_limiter = StrobeLimiter()
        self._breath_t = self._hue_phase = self._comp_t = self._comp_rot = 0.0        # phases integrees (respiration, teinte, composition)
        self._prev_ok = False
        self.fb_tex: list = []                  # trainee : deux images finales hors ecran (on lit l'une, on ecrit l'autre, puis on echange)
        self.fb_fbo: list = []
        self._fb_i = 0
        self.flash_t = -10.0              # instant (time.monotonic) du dernier flash de transition du VJ
        self.noise_out = (0.0, 0.0)      # (houle, coup) x force, pour l'indicateur de la GUI
        self._melt_t = 0.0          # cycles de fonte ecoules (partie entiere = cycles complets)
        self._melt_clock = 0.0      # secondes (derive du bruit)
        self._cell_t = 0.0          # temps integre de la derive des cellules

        self.video_tex = ctx.texture(video_size, 3, data=bytes(video_size[0] * video_size[1] * 3))
        self.video_tex.filter = (moderngl.LINEAR, moderngl.LINEAR)
        self.video_tex.repeat_x = self.video_tex.repeat_y = False

        self.logo_tex = None
        self.logo_aspect = 1.0
        self.has_logo = False
        self.logo_kind = "image"
        self.logo_path = ""
        self.logo_video: LogoVideo | None = None
        self._holo_t = 0.0                 # phase d'animation du halo holographique, integree image par image
        # Dernier chemin DEMANDE (charge ou non): evite de retenter un fichier invalide a chaque image.
        self.logo_requested = logo_path
        self.set_logo(logo_rgba, logo_path)

        self.vbo = ctx.buffer(np.array([-1, -1, 1, -1, -1, 1, 1, 1], dtype="f4").tobytes())
        self.scene_prog = self.post_prog = self.scene_vao = self.post_vao = None
        self.scene_tex = self.scene_fbo = self.layer_tex = self.layer_fbo = self.holo_tex = self.holo_fbo = None
        self.reload_shaders()

    def set_logo(self, logo_rgba, path: str = "", kind: str = "image") -> None:
        """Remplace la texture du logo (None = aucun). `kind` = "image" ou "text" (mise en page
        differente, voir logo_layout). A appeler depuis le fil GL."""
        self.release_logo_video()
        self.logo_kind = kind
        old = self.logo_tex
        if logo_rgba is not None:
            h, w = logo_rgba.shape[:2]
            tex = self.ctx.texture((w, h), 4, data=logo_rgba.tobytes())
            tex.build_mipmaps()
            tex.filter = (moderngl.LINEAR_MIPMAP_LINEAR, moderngl.LINEAR)
            tex.anisotropy = 8.0
            self.logo_aspect = w / h
        else:
            tex = self.ctx.texture((1, 1), 4, data=bytes(4))
            self.logo_aspect = 1.0
        tex.repeat_x = tex.repeat_y = False
        self.logo_tex = tex
        self.has_logo = logo_rgba is not None
        self.logo_path = path if logo_rgba is not None else ""
        if old is not None:
            old.release()

    def set_logo_video(self, video: LogoVideo, path: str = "") -> None:
        """Logo anime: la texture (rgba, alpha droit) est reecrite par `video.reader` a chaque
        nouvelle image (voir draw). Le Renderer possede `video` et l'arrete quand il est remplace."""
        self.set_logo(None, "")                 # libere l'ancien logo / l'ancienne video
        old = self.logo_tex
        w, h = video.size
        tex = self.ctx.texture((w, h), 4, data=bytes(w * h * 4))
        tex.filter = (moderngl.LINEAR, moderngl.LINEAR)
        tex.repeat_x = tex.repeat_y = False
        self.logo_tex = tex
        self.logo_aspect = w / h
        self.logo_kind = "video"
        self.logo_video = video
        self.has_logo = True
        self.logo_path = path
        old.release()

    def release_logo_video(self) -> None:
        if self.logo_video is not None:
            self.logo_video.stop()
            self.logo_video = None

    def _read(self, name: str) -> str:
        return (self.shader_dir / name).read_text(encoding="utf-8")

    def reload_shaders(self) -> None:
        """Recompile les GLSL depuis le disque. En cas d'erreur, l'ancien programme reste
        actif et ShaderError porte le message du compilateur."""
        try:
            vert = self._read("quad.vert")
            scene = self.ctx.program(vertex_shader=vert, fragment_shader=self._read("scene.frag"))
            post = self.ctx.program(vertex_shader=vert, fragment_shader=self._read("post.frag"))
            holo = self.ctx.program(vertex_shader=vert, fragment_shader=self._read("holo.frag"))
        except Exception as exc:
            raise ShaderError(str(exc)) from exc
        self.scene_prog, self.post_prog, self.holo_prog = scene, post, holo
        self.scene_vao = self.ctx.vertex_array(scene, [(self.vbo, "2f", "in_pos")])
        self.post_vao = self.ctx.vertex_array(post, [(self.vbo, "2f", "in_pos")])
        self.holo_vao = self.ctx.vertex_array(holo, [(self.vbo, "2f", "in_pos")])

    def _ensure_scene(self, size: tuple[int, int]) -> None:
        if self.scene_tex is not None and self.scene_tex.size == size:
            return
        for old in (self.scene_tex, self.layer_tex, self.holo_tex, *self.fb_tex):
            if old is not None:
                old.release()
        self._prev_ok = False
        # scene_* = couche FOND (opaque), layer_* = couche LOGO (rgba premultiplie, transparente)
        self.scene_tex = self.ctx.texture(size, 4)
        self.layer_tex = self.ctx.texture(size, 4)
        for tex in (self.scene_tex, self.layer_tex):
            tex.filter = (moderngl.LINEAR, moderngl.LINEAR)
            tex.repeat_x = tex.repeat_y = False
        self.scene_fbo = self.ctx.framebuffer(color_attachments=[self.scene_tex])
        self.layer_fbo = self.ctx.framebuffer(color_attachments=[self.layer_tex])
        # Champ du halo holographique: basse resolution (1/HOLO_DOWNSCALE), flottants demi-precision.
        self.holo_tex = self.ctx.texture((max(size[0] // HOLO_DOWNSCALE, 1), max(size[1] // HOLO_DOWNSCALE, 1)), 4,
                                         dtype="f2")
        self.holo_tex.filter = (moderngl.LINEAR, moderngl.LINEAR)
        self.holo_tex.repeat_x = self.holo_tex.repeat_y = False
        self.holo_fbo = self.ctx.framebuffer(color_attachments=[self.holo_tex])
        self.fb_tex = [self.ctx.texture(size, 4) for _ in range(2)]
        for tex in self.fb_tex:
            tex.filter = (moderngl.LINEAR, moderngl.LINEAR)
            tex.repeat_x = tex.repeat_y = False
        self.fb_fbo = [self.ctx.framebuffer(color_attachments=[tex]) for tex in self.fb_tex]

    def _strobe(self, p: dict, state: dict) -> float:
        """Eclair blanc court du strobe : 1 -> 0 en 70 ms. Declenche a chaque appui, puis (arme / maintenu) en cadence fixe ou
        a chaque kick selon `strobe_mode`. Hors ecran noir : il blanchit l'image comme le flash du VJ, sans bandes de glitch."""
        now = time.monotonic()
        beats = int(state.get("beats", 0))
        kicked, self._strobe_beats = beats != self._strobe_beats, beats
        hits = int(p["strobe_hits"])
        fire = hits != self._strobe_seen
        self._strobe_seen = hits
        if float(p["strobe_on"]) >= 0.5:
            if p["strobe_mode"] == "kick":
                fire = fire or kicked
            else:
                fire = fire or now - self._strobe_last >= 1.0 / min(max(float(p["strobe_rate"]), 1.0), 20.0)
        build = _unit(p.get("build", 0.0))
        if build > 0.05:                                              # montee : eclairs automatiques de 2 a 16 Hz
            fire = fire or now - self._strobe_last >= 1.0 / (2.0 + 14.0 * build)
        if fire and self.strobe_limiter.allow(now, float(p["strobe_max_hz"]), float(p["strobe_max_s"])):
            self._strobe_last = now
        elif fire and self.strobe_limiter.blocked(now) and self.strobe_limiter.trips != getattr(self, "_trips_logged", 0):
            self._trips_logged = self.strobe_limiter.trips
            sessionlog.event(f"Strobe limite : rafale de plus de {float(p['strobe_max_s']):.0f} s, pause de {STROBE_COOLDOWN_S:.0f} s")
        return max(0.0, 1.0 - (now - self._strobe_last) / 0.07)

    @staticmethod
    def _set(prog, name: str, value) -> None:
        if name in prog:   # un uniform inutilise est elimine par le compilateur
            prog[name].value = value

    def draw(self, target, size: tuple[int, int], state: dict, params: dict, t: float) -> None:
        width, height = size
        p = {**DEFAULT_PARAMS, **params}       # un dict partiel (tests) reste valide
        fm = _unit(p["fx_master"], 0.0, 2.0)   # master des effets : x l'intensite de tous les effets (copie : `params` n'est pas touche)
        if fm != 1.0:
            for key in ("master", "melt_depth", "cell_amount", "noise_amount", "holo_intensity", "holo_warp", "holo_dust"):
                p[key] = float(p[key]) * fm
        fx_on, fx_int, master = p["fx_on"], p["fx_int"], p["master"]
        # Masters du Mode VJ : reactivite = tout ce qui vient de l'audio est multiplie (0 = le visuel ne reagit plus au son) ;
        # tempo = les animations (halo, fonte, cellules, fond, noise) avancent plus ou moins vite.
        build = _unit(p["build"])                                      # montee en cours : plus de reactivite, strobe, teinte qui derive
        react_m = _unit(float(p["react_master"]) * (1.0 + build), 0.0, 3.0)
        tempo = effective_tempo(p)                                      # master, ralenti par le mode silence
        idle = _unit(p["idle"])
        if react_m != 1.0:
            state = {**state, **{k: float(state[k]) * react_m for k in ("bass", "mid", "high", "rms", "beat") if k in state}}
        bass, high, beat = state["bass"], state["high"], state["beat"]
        dt = 0.0 if self._last_t is None else min(max(t - self._last_t, 0.0), 0.1)
        self._last_t = t
        dt *= tempo
        # Respiration (overlay et fond en opposition de phase), cycle de teinte, phases de la composition : integres image par image.
        self._breath_t += dt / max(float(p["breath_period"]), 1.0)
        wave = 0.5 + 0.5 * math.sin(2.0 * math.pi * self._breath_t)
        ov = _unit(p["overlay_master"]) * (1.0 - idle) * (1.0 - _unit(p["breath_overlay"]) * wave)      # presence de l'overlay (0 = efface)
        bg_level = _unit(p["bg_master"]) * (1.0 - _unit(p["breath_bg"]) * (1.0 - wave))
        self._hue_phase = (self._hue_phase + dt * float(p["hue_cycle"])) % 360.0
        hue_deg = (float(p["hue_master"]) + self._hue_phase + build * 120.0) % 360.0
        self._comp_t += dt
        self._comp_rot += dt * math.radians(float(p["comp_rot"]))
        # Noise: houle et coups au hasard (x force) qui s'ajoutent a l'audio reel pour les effets coches.
        self.noise.step(dt)
        n_amount = min(max(float(p["noise_amount"]), 0.0), 1.0)
        n_fx = p["noise_fx"]
        nz, nzb = self.noise.swell() * n_amount, self.noise.beat() * n_amount
        self.noise_out = (nz, nzb)
        eff = [float(fx_on[i]) * float(fx_int[i]) * master for i in range(5)]
        if int(n_fx[4]):                                   # reaction du logo (pulsation, tremblement, contour)
            beat_l, high_l, bass_l = max(beat, nzb), max(high, nz), max(bass, nz)
        else:
            beat_l, high_l, bass_l = beat, high, bass

        self._ensure_scene(size)

        # Passe 1 : video + logo (pulse, jitter, contour lumineux). L'interrupteur/intensite
        # "logo" (effet 5) module les trois reactions a l'audio; position, taille et
        # opacite restent celles des reglages meme effet coupe.
        logo_fx = eff[4]
        pulse = p["logo_pulse"] * beat_l * logo_fx
        jit = p["logo_jitter"] * high_l * logo_fx
        jitter = (float(self.rng.uniform(-1, 1)) * jit, float(self.rng.uniform(-1, 1)) * jit)
        is_text = self.logo_kind == "text"
        rect = logo_layout(width, height, self.logo_aspect, p["text_scale"] if is_text else p["logo_scale"],
                           (p["logo_x"], p["logo_y"]), pulse, jitter, "height" if is_text else "box")
        sp = self.scene_prog
        self._set(sp, "u_video", 0)
        self._set(sp, "u_logo", 1)
        self._set(sp, "u_res", (float(width), float(height)))
        self._set(sp, "u_logo_rect", rect)
        self._set(sp, "u_logo_on", 1.0 if self.has_logo else 0.0)
        self._set(sp, "u_logo_straight", 1.0 if self.logo_video is not None else 0.0)
        if self.logo_video is not None:
            self.logo_video.reader.upload_to(self.logo_tex)
        self._set(sp, "u_logo_opacity", min(max(float(p["logo_opacity"]), 0.0), 1.0) * ov)
        self._set(sp, "u_bass", float(bass_l))
        self._set(sp, "u_glow", min(float(p["logo_glow"]) * logo_fx, 3.0) * ov)
        self._set(sp, "u_glow_radius", float(p["logo_glow_radius"]))
        self._set(sp, "u_glow_color", hex_to_rgb(p["logo_glow_color"]))
        self._set(sp, "u_beat", float(beat))

        # Fond: video ffmpeg ou motif genere. Les phases avancent meme quand le motif est cache,
        # pour qu'un retour au motif ne reparte pas d'un etat fige.
        # Halo holographique: phase integree (changer la vitesse ne fait pas sauter les reflets). Sans
        # holo_react (defaut), ni l'intensite ni la vitesse ne dependent de l'audio.
        h_react = max(float(p["holo_react"]), 0.0)
        self._holo_t += dt * float(p["holo_speed"]) * (1.0 + h_react * float(bass) * 3.0)
        holo_on = float(p["holo_on"]) >= 0.5 and self.has_logo
        # Fonte acide: phase integree (changer la periode ne fait pas sauter l'animation). Triangle 0 -> 1 -> 0 avec
        # un palier intact et un palier dissous, adouci: le logo reste lisible un moment, puis fond, puis se reforme.
        m_react = max(float(p["melt_react"]), 0.0)
        self._melt_t += dt / max(float(p["melt_period"]), 1.0) * (1.0 + m_react * float(bass) * 3.0)
        self._melt_clock += dt
        tri = 1.0 - abs(2.0 * (self._melt_t % 1.0) - 1.0)
        s_ = min(max((tri - 0.15) / 0.7, 0.0), 1.0)
        melt = s_ * s_ * (3.0 - 2.0 * s_) * min(max(float(p["melt_depth"]), 0.0), 1.0)
        if float(p["melt_on"]) < 0.5:
            melt = 0.0
        self._set(sp, "u_melt", melt)
        self._set(sp, "u_melt_scale", max(float(p["melt_scale"]), 0.1))
        self._set(sp, "u_melt_edge", max(float(p["melt_edge"]), 0.0))
        self._set(sp, "u_melt_color", hex_to_rgb(p["melt_color"], DEFAULT_PARAMS["melt_color"]))
        self._set(sp, "u_melt_t", self._melt_clock)
        # Cellules du logo: derive integree (changer la vitesse ne fait pas sauter les gouttes).
        c_react = max(float(p["cell_react"]), 0.0)
        self._cell_t += dt * float(p["cell_speed"]) * (1.0 + c_react * float(bass) * 3.0)
        cell_on = float(p["cell_on"]) >= 0.5
        self._set(sp, "u_cell", min(max(float(p["cell_amount"]), 0.0), 1.0) if cell_on else 0.0)
        self._set(sp, "u_cell_scale", max(float(p["cell_scale"]), 0.1))
        self._set(sp, "u_cell_fusion", max(float(p["cell_fusion"]), 0.05) * (1.0 + c_react * float(beat) * 0.6))
        self._set(sp, "u_cell_flat", min(max(float(p["cell_flat"]), 0.0), 1.0))
        self._set(sp, "u_cell_ink", hex_to_rgb(p["cell_ink"], DEFAULT_PARAMS["cell_ink"]))
        self._set(sp, "u_cell_t", self._cell_t)
        react = max(float(p["bg_react"]), 0.0)
        self._bg_scroll += dt * float(p["bg_speed"]) * (1.0 + react * float(bass) * 1.5)
        self._bg_flip_t += dt * float(p["bg_flip"])
        flips = self._bg_flip_t + (float(state.get("beats", 0)) if react > 0 and react_m > 0 else 0.0)
        self._set(sp, "u_bg_mode", 1.0 if p["bg_mode"] == "pattern" else 0.0)
        self._bg_clock += dt                       # horloge reelle: la palette "test" tourne a 30 images/s fixes
        self._set(sp, "u_bg_palette", {"duo": 1.0, "test": 2.0}.get(p["bg_palette"], 0.0))
        self._set(sp, "u_bg_frame", float(int(self._bg_clock * 30.0)))
        self._set(sp, "u_bg_c1", hex_to_rgb(p["bg_color1"], DEFAULT_PARAMS["bg_color1"]))
        self._set(sp, "u_bg_c2", hex_to_rgb(p["bg_color2"], DEFAULT_PARAMS["bg_color2"]))
        self._set(sp, "u_bg_phase", (self._bg_scroll, flips))
        self._set(sp, "u_bg_tile", float(p["bg_tile"]))
        self._set(sp, "u_bg_checker", float(p["bg_checker"]))
        self._set(sp, "u_bg_react", react)
        self._set(sp, "u_bg_hue", float(p["bg_hue"]))
        self._set(sp, "u_bg_angle", float(p["bg_angle"]))
        self.video_tex.use(0)
        self.logo_tex.use(1)
        # Passe 1a: couche fond. 1b: couche logo seule (videe si pas de logo: le post-traitement
        # ne la lit alors pas, mais elle reste coherente).
        self._set(sp, "u_pass", 0.0)
        self.scene_fbo.viewport = (0, 0, width, height)
        self.scene_fbo.use()
        self.scene_vao.render(moderngl.TRIANGLE_STRIP)
        self.layer_fbo.viewport = (0, 0, width, height)
        self.layer_fbo.use()
        if self.has_logo:
            self._set(sp, "u_pass", 1.0)
            self.scene_vao.render(moderngl.TRIANGLE_STRIP)
            if holo_on:
                # Le halo holographique lit l'alpha de cette couche, flou a de nombreuses echelles (mipmaps
                # a la taille de l'ecran: la portee ne depend ni du PNG ni de la marge de sa texture); le
                # champ est calcule a basse resolution (holo.frag) puis lu par le post-traitement.
                self.layer_tex.build_mipmaps()
                self.layer_tex.filter = (moderngl.LINEAR_MIPMAP_LINEAR, moderngl.LINEAR)
                hp = self.holo_prog
                self._set(hp, "u_logo", 0)
                self._set(hp, "u_res", (float(width), float(height)))
                self._set(hp, "u_holo_reach", max(float(p["holo_reach"]), 0.05))
                self._set(hp, "u_holo_lod0", math.log2(max(height, 1) / 720.0))
                self._set(hp, "u_holo_center", (float(rect[0]), float(1.0 - rect[1])))
                self.layer_tex.use(0)
                self.holo_fbo.viewport = (0, 0) + tuple(self.holo_tex.size)
                self.holo_fbo.use()
                self.holo_vao.render(moderngl.TRIANGLE_STRIP)
        else:
            self.layer_fbo.clear(0.0, 0.0, 0.0, 0.0)
        if not holo_on:
            # Sans mipmaps construites, le filtre a mipmaps rendrait la texture incomplete (noire).
            self.layer_tex.filter = (moderngl.LINEAR, moderngl.LINEAR)

        # Passe 2 : post-traitement de chaque couche avec ses effets, logo par-dessus, vers la cible.
        fx_bg, fx_logo = layer_effects(p)
        linked = float(p["fx_link"]) >= 0.5
        # Decorreles, les ondes du logo partent de son centre (origine GL = bas gauche) et son glitch
        # tire d'autres bandes; lies, tout est identique au fond (comme un seul post-traitement).
        center = (0.5, 0.5) if linked else (rect[0], 1.0 - rect[1])
        pp = self.post_prog
        self._set(pp, "u_scene", 0)
        self._set(pp, "u_logo", 1)
        self._set(pp, "u_logo_on", 1.0 if self.has_logo else 0.0)
        self._set(pp, "u_fx_bg", tuple(fx_bg))
        self._set(pp, "u_fx_logo", tuple(fx_logo))
        self._set(pp, "u_center_logo", (float(center[0]), float(center[1])))
        self._set(pp, "u_salt_logo", 0.0 if linked else 13.0)
        self._set(pp, "u_res", (float(width), float(height)))
        self._set(pp, "u_time", float(t))
        for name in ("bass", "mid", "high", "rms"):
            self._set(pp, f"u_{name}", float(state[name]))
        self._set(pp, "u_beat", float(beat))
        flash = max(0.0, 1.0 - (time.monotonic() - self.flash_t) / 0.55)
        self._set(pp, "u_flash", flash * flash)
        self._set(pp, "u_strobe", self._strobe(p, state))
        self._set(pp, "u_hue", math.radians(hue_deg))
        self._set(pp, "u_bg_level", bg_level)
        # Composition : symetrie / kaleidoscope, pixelisation, deformation globale (UV) et trainee (image precedente)
        self._set(pp, "u_csym", float(int(round(float(p["comp_sym"])))))
        self._set(pp, "u_cseg", float(max(2, min(16, int(round(float(p["comp_seg"])))))))
        self._set(pp, "u_crot", self._comp_rot)
        self._set(pp, "u_cpix", _unit(p["comp_pix"]))
        self._set(pp, "u_cwarp", _unit(p["comp_warp"]))
        self._set(pp, "u_ctime", self._comp_t)
        trail = _unit(p["comp_trail"], 0.0, 0.97)
        self._set(pp, "u_trail", trail)
        self._set(pp, "u_prev", 3)
        self._set(pp, "u_nz", float(nz))
        self._set(pp, "u_nz_beat", float(nzb))
        self._set(pp, "u_nz_since", float(self.noise.since))
        self._set(pp, "u_nz_amount", float(n_amount))
        self._set(pp, "u_nz_fx", tuple(float(int(n_fx[i])) for i in range(4)))
        self._set(pp, "u_since_beat", float(state["since_beat"]))
        self._set(pp, "u_holo", (1.0 + h_react * float(beat) * 1.5) * ov if holo_on else 0.0)
        self._set(pp, "u_holo_light", float(p["holo_intensity"]) * ov)
        self._set(pp, "u_holo_reach", max(float(p["holo_reach"]), 0.05))
        self._set(pp, "u_holo_warp", float(p["holo_warp"]))
        self._set(pp, "u_holo_dust", float(p["holo_dust"]))
        self._set(pp, "u_holo_phase", self._holo_t)
        self._set(pp, "u_holo_tex", 2)
        self._set(pp, "u_holo_center", (float(rect[0]), float(1.0 - rect[1])))    # toujours le centre du logo
        self._set(pp, "u_hud", 1.0 if self.hud else 0.0)
        self._set(pp, "u_fx_state", tuple(float(v) for v in fx_on))
        self.scene_tex.use(0)
        self.layer_tex.use(1)
        self.holo_tex.use(2)
        dest = target
        if trail > 0.001:
            # Trainee : on ecrit l'image dans l'une de deux textures hors ecran (l'ecran est multi-echantillonne : on ne peut pas le lire
            # en texture), on lit l'autre (l'image d'avant), puis on recopie l'image finale sur la cible et on echange.
            read_i = 1 - self._fb_i
            if not self._prev_ok:
                self.fb_fbo[read_i].clear(0.0, 0.0, 0.0, 1.0)            # la trainee repart d'une image noire
                self._prev_ok = True
            self.fb_tex[read_i].use(3)
            dest = self.fb_fbo[self._fb_i]
        else:
            self._prev_ok = False
            self.fb_tex[0].use(3)
        dest.viewport = (0, 0, width, height)
        dest.use()
        self.post_vao.render(moderngl.TRIANGLE_STRIP)
        if dest is not target:
            self.ctx.copy_framebuffer(target, dest)
            self._fb_i = 1 - self._fb_i


def hex_to_rgb(text: str, fallback: str = DEFAULT_PARAMS["logo_glow_color"]) -> tuple[float, float, float]:
    """'#rrggbb' -> (r, g, b) en 0..1 ; valeur invalide -> couleur de repli."""
    for candidate in (text, fallback):
        c = candidate.strip().lstrip("#")
        if len(c) == 6:
            try:
                return tuple(int(c[i:i + 2], 16) / 255.0 for i in (0, 2, 4))
            except ValueError:
                pass
    return (0.37, 0.83, 0.78)


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


class PerfGuard:
    """Garde-fou de charge : previent quand le GPU ne tient plus l'image. Le budget est une image a 60 Hz au plus (un
    projecteur a 59 Hz ne demande pas plus, un ecran a 144 Hz ne rend pas le budget impossible). Moyenne lissee des temps
    GPU mesures ; alerte apres `HOLD_S` secondes au-dessus de `HIGH` x budget, levee sous `LOW` x budget (hysterese :
    pas de message qui clignote). `update()` renvoie le texte a afficher, ou None quand tout va bien. Pure (testable)."""

    HIGH, LOW, HOLD_S, ALPHA = 0.90, 0.80, 2.0, 0.2
    # (cle de reglage, libelle, touche), du plus cher au moins cher (mesure : cellules > halo > fonte)
    COSTLY = (("cell_on", "cellules (V)"), ("holo_on", "halo (C)"), ("melt_on", "fonte (M)"))

    def __init__(self, refresh_hz: float) -> None:
        self.budget_ms = 1000.0 / max(min(float(refresh_hz or 60.0), 60.0), 20.0)
        self.avg = 0.0
        self.over_since: float | None = None
        self.active = False

    def update(self, gpu_ms: float, now: float, params: dict) -> str | None:
        self.avg = gpu_ms if self.avg == 0.0 else self.avg + self.ALPHA * (gpu_ms - self.avg)
        if self.avg > self.HIGH * self.budget_ms:
            if self.over_since is None:
                self.over_since = now
            if now - self.over_since >= self.HOLD_S:
                self.active = True
        else:
            self.over_since = None
            if self.avg < self.LOW * self.budget_ms:
                self.active = False
        if not self.active:
            return None
        on = [label for key, label in self.COSTLY if float(params.get(key, 0.0)) >= 0.5]
        advice = ("coupe " + " ou ".join(on)) if on else "baisse la taille du rendu (--render-size)"
        return f"GPU trop charge: {self.avg:.0f} ms par image pour {self.budget_ms:.0f} ms disponibles - {advice}"


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
