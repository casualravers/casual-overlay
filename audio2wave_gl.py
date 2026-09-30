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
import random
import shlex
import shutil
import subprocess
import sys
import threading
import time
from collections import deque
from pathlib import Path

MISSING: list[str] = []


def _optional_import(module: str, package: str):
    try:
        return __import__(module, fromlist=["_"])
    except Exception:  # ImportError, ou OSError si PortAudio manque pour sounddevice
        MISSING.append(package)
        return None


np = _optional_import("numpy", "numpy")
glfw = _optional_import("glfw", "glfw")
moderngl = _optional_import("moderngl", "moderngl")
sd = _optional_import("sounddevice", "sounddevice")
PIL_Image = _optional_import("PIL.Image", "Pillow")
PIL_ImageDraw = _optional_import("PIL.ImageDraw", "Pillow")
PIL_ImageFont = _optional_import("PIL.ImageFont", "Pillow")


def require_deps() -> None:
    if MISSING:
        print(f"Dependances manquantes: {', '.join(dict.fromkeys(MISSING))}.\n"
              "Installe-les avec:  pip install -r requirements-gl.txt", file=sys.stderr)
        sys.exit(1)


SCRIPT_DIR = Path(__file__).resolve().parent
SHADER_DIR = SCRIPT_DIR / "gl_shaders"
DEFAULT_LOGO = SCRIPT_DIR / "asset" / "Casual Ravers - Kit_Sigle - Blanc.png"
DEFAULT_A2W_DIR = SCRIPT_DIR.parent / "audio2wave"
PARAMS_PATH = Path.home() / ".audio2wave" / "gl_params.json"

FX_NAMES = ["wobble", "ripple", "chroma", "glitch", "logo"]
DEFAULT_PARAMS = {
    "fx_on": [1, 1, 1, 1, 1],
    "fx_int": [1.0, 1.0, 1.0, 1.0, 1.0],
    # Effets de la COUCHE LOGO (wobble, ripple, chroma, glitch). fx_on/fx_int[0..3] sont ceux du FOND ;
    # l'indice 4 (reaction du logo: pulsation, tremblement, contour) reste a part. Avec fx_link = 1
    # le logo subit les memes effets que le fond (reglages fx_*, centre de l'image), exactement comme
    # avant la separation des couches ; a 0 il a les siens (fxl_*) et ses ondes partent de son centre.
    "fx_link": 1.0,
    "fxl_on": [1, 1, 1, 1],
    "fxl_int": [1.0, 1.0, 1.0, 1.0],
    "master": 1.0,
    "sensitivity": 1.0,
    # Incrustation du logo (pilotee par la GUI ou --logo*, relue a chaque image)
    "logo_source": "image",           # "image" (PNG), "text" (texte tape a la main) ou "video" (logo anime)
    "logo_video": "",                 # fichier video/GIF anime (source "video")
    "logo_key": "",                   # #rrggbb a detourer si le fichier n'a pas d'alpha ("" = aucun)
    "logo_path": str(DEFAULT_LOGO),   # "" = aucun logo
    "text_content": "CASUAL RAVERS",  # source texte: le texte (plusieurs lignes possibles)
    "text_font": "auto",              # nom de police de TEXT_FONTS, chemin d'un .ttf/.otf, ou "auto"
    "text_color": "#ffffff",
    "text_align": "center",           # "left" | "center" | "right" (texte sur plusieurs lignes)
    "text_scale": 0.16,               # hauteur du bloc de texte, fraction de la hauteur de l'image
    "logo_x": 0.5,                    # centre, fraction de la largeur (origine a gauche)
    "logo_y": 0.5,                    # centre, fraction de la hauteur (origine en haut)
    "logo_scale": 0.35,               # cote du carre englobant, fraction de la hauteur
    "logo_opacity": 1.0,
    "logo_pulse": 0.12,               # zoom au kick (0,12 = +12 %)
    "logo_jitter": 0.004,             # tremblement sur les aigus, fraction de l'ecran
    "logo_glow": 1.0,                 # intensite du contour lumineux
    "logo_glow_radius": 1.0,          # multiplicateur du rayon du contour
    "logo_glow_color": "#5fd4c8",
    # Fond: "live" = spectre ffmpeg, "pattern" = motif genere dans le shader (degrades + damier)
    "bg_mode": "live",
    "bg_palette": "classic",          # "classic" (arc-en-ciel d'origine) ou "duo" (color1 -> color2)
    "bg_color1": "#3a1cff",           # palette duo
    "bg_color2": "#14f0d8",
    "bg_angle": 0.0,                  # direction du degrade duo, en degres
    "bg_hue": 0.0,                    # rotation de teinte, 0..1 (un tour)
    "bg_speed": 1.0,                  # vitesse de defilement des degrades
    "bg_tile": 80.0,                  # cote d'un carreau, en pixels pour 720 px de haut
    "bg_checker": 0.24,               # contraste du damier
    "bg_flip": 2.0,                   # bascules du damier par seconde
    "bg_react": 1.0,                  # reaction a l'audio: flash + damier au kick, defilement aux basses
    "auto_master": 1.0,               # interrupteur general des automations (1 = actives)
}

# --- Automations de reglages ------------------------------------------------------------
# Meme principe que "Variation automatique" d'audio2wave (voir son CLAUDE.md): chaque reglage
# automatise suit sa PROPRE courbe (AUTOMATION_POINTS points de controle boucles, interpoles
# lineairement) a sa PROPRE vitesse, et la valeur est une fonction pure du temps. Le moteur
# tourne ici, dans le fil de rendu (precision a l'image, et ca marche aussi sans --gui) ;
# la GUI reprend l'editeur de courbes d'audio2wave et ne fait que lire/ecrire
# params["_automation"].
AUTOMATION_POINTS = 12       # meme valeur que AUTOMATE_CURVE_POINTS d'audio2wave (compatibilite des editeurs)

# cle de params -> (libelle, min, max, courbe, periode en s, active par defaut). Min/max = plage
# de l'automation (plus etroite que celle du curseur: un reglage automatise est PILOTE, le curseur
# suit). Periodes volontairement premieres entre elles (pas de multiple commun) pour que
# l'ensemble ne se repete jamais a l'identique. Actives par defaut: ce qui donne de la vie sans
# rien deplacer (teinte, angle, carreaux, intensites d'effets); position, taille et opacite du
# logo sont proposees mais coupees (une marque ne doit pas se promener sans qu'on l'ait voulu).
AUTOMATION_SPECS: dict[str, tuple] = {
    "bg_hue":           ("Teinte", 0.0, 0.5, "sinus", 79, True),
    "bg_angle":         ("Angle du degrade", 20.0, 160.0, "sinus", 53, True),
    "bg_speed":         ("Vitesse", 0.4, 1.8, "sinus", 37, True),
    "bg_tile":          ("Taille des carreaux", 50.0, 150.0, "sinus", 29, True),
    "bg_checker":       ("Contraste du damier", 0.06, 0.32, "sinus", 23, True),
    "bg_flip":          ("Cadence du damier", 0.5, 3.5, "triangle", 47, True),
    "logo_x":           ("Position X", 0.35, 0.65, "sinus", 43, False),
    "logo_y":           ("Position Y", 0.42, 0.58, "sinus", 31, False),
    "logo_scale":       ("Taille (image)", 0.30, 0.42, "sinus", 13, False),
    "text_scale":       ("Taille du texte", 0.12, 0.20, "sinus", 13, False),
    "logo_opacity":     ("Opacite", 0.6, 1.0, "sinus", 19, False),
    "logo_pulse":       ("Pulsation", 0.05, 0.30, "sinus", 21, True),
    "logo_glow":        ("Contour lumineux", 0.4, 1.5, "sinus", 11, True),
    "logo_glow_radius": ("Rayon du contour", 0.6, 1.6, "sinus", 17, True),
    "fx_int0":          ("Intensite wobble", 0.4, 1.2, "sinus", 19, True),
    "fx_int1":          ("Intensite onde de choc", 0.4, 1.3, "sinus", 41, True),
    "fx_int2":          ("Intensite aberration", 0.3, 1.2, "sinus", 7, True),
    "fx_int3":          ("Intensite glitch", 0.15, 1.0, "aleatoire", 61, True),
    "fx_int4":          ("Intensite logo", 0.6, 1.2, "sinus", 59, True),
    # Effets de la couche logo (utiles seulement quand fx_link = 0). Periodes differentes de celles
    # du fond: les deux couches ne respirent pas en phase.
    "fxl_int0":         ("Logo: intensite wobble", 0.4, 1.2, "sinus", 67, True),
    "fxl_int1":         ("Logo: intensite onde de choc", 0.4, 1.3, "sinus", 31, True),
    "fxl_int2":         ("Logo: intensite aberration", 0.3, 1.2, "sinus", 71, True),
    "fxl_int3":         ("Logo: intensite glitch", 0.15, 1.0, "aleatoire", 83, True),
}

FX_EFFECTS = 4               # wobble, ripple, chroma, glitch: les effets qui se deforment par couche


def curve_points(name: str, n: int = AUTOMATION_POINTS, seed: int = 7) -> list[float]:
    """Courbe de depart (valeurs 0..1, bouclee): memes formes que les presets d'audio2wave."""
    if name == "triangle":
        return [2 * (i / n) if i / n <= 0.5 else 2 * (1 - i / n) for i in range(n)]
    if name == "carre":
        return [1.0 if i / n < 0.5 else 0.0 for i in range(n)]
    if name == "dents":
        return [i / n for i in range(n)]
    if name == "aleatoire":
        rng = random.Random(seed)
        return [rng.random() for _ in range(n)]
    return [0.5 + 0.5 * math.sin(2 * math.pi * i / n) for i in range(n)]


def default_automation() -> dict:
    """Etat initial {cle: {"enabled", "points", "period"}}: celui de AUTOMATION_SPECS."""
    return {key: {"enabled": bool(spec[5]), "points": curve_points(spec[3], seed=i), "period": float(spec[4])}
            for i, (key, spec) in enumerate(AUTOMATION_SPECS.items())}


def merge_automation(saved) -> dict:
    """Etat sauve valide, complete par les valeurs par defaut (cle absente ou entree invalide)."""
    merged = default_automation()
    if isinstance(saved, dict):
        for key, entry in saved.items():
            if key not in merged or not isinstance(entry, dict):
                continue
            points = entry.get("points")
            if isinstance(points, list) and len(points) == AUTOMATION_POINTS \
                    and all(isinstance(v, (int, float)) for v in points):
                merged[key]["points"] = [min(max(float(v), 0.0), 1.0) for v in points]
            if isinstance(entry.get("period"), (int, float)) and entry["period"] > 0:
                merged[key]["period"] = float(entry["period"])
            if "enabled" in entry:
                merged[key]["enabled"] = bool(entry["enabled"])
    return merged


def _list_key(key: str):
    """'fx_int2' -> ('fx_int', 2), 'fxl_int0' -> ('fxl_int', 0), sinon None (reglage scalaire)."""
    for prefix in ("fx_int", "fxl_int"):
        if key.startswith(prefix) and key[len(prefix):].isdigit():
            return prefix, int(key[len(prefix):])
    return None


def get_param(params: dict, key: str):
    lk = _list_key(key)
    return params[lk[0]][lk[1]] if lk else params[key]


def set_param(params: dict, key: str, value: float) -> None:
    lk = _list_key(key)
    if lk:
        params[lk[0]][lk[1]] = value
    else:
        params[key] = value


def set_fx_link(params: dict, linked: bool) -> None:
    """Lie ou delie les effets du logo et du fond. En deliant, les reglages du logo partent de ceux du
    fond (rien ne saute a l'ecran)."""
    if not linked and float(params["fx_link"]) >= 0.5:
        for i in range(FX_EFFECTS):
            params["fxl_on"][i] = params["fx_on"][i]
            params["fxl_int"][i] = params["fx_int"][i]
    params["fx_link"] = 1.0 if linked else 0.0


def layer_effects(params: dict) -> tuple[list[float], list[float]]:
    """Intensites effectives (interrupteur x intensite x intensite globale) des quatre effets de
    deformation : (fond, logo). Lie, le logo reprend celles du fond."""
    master = float(params["master"])
    bg = [float(params["fx_on"][i]) * float(params["fx_int"][i]) * master for i in range(FX_EFFECTS)]
    if float(params["fx_link"]) >= 0.5:
        return bg, list(bg)
    return bg, [float(params["fxl_on"][i]) * float(params["fxl_int"][i]) * master for i in range(FX_EFFECTS)]


class AutomationEngine:
    """Applique les automations a `params` (fil de rendu, une fois par image). Une automation
    repart du debut de sa courbe chaque fois qu'elle est (re)activee ; `auto_master` = 0 fige
    toutes les valeurs la ou elles sont (sans toucher aux interrupteurs individuels)."""

    def __init__(self):
        self._start: dict[str, float] = {}

    def step(self, params: dict, now: float) -> None:
        data = params.get("_automation")
        if not isinstance(data, dict):
            return
        if float(params.get("auto_master", 1.0)) < 0.5:
            self._start.clear()            # a la reprise, chaque courbe repart de son debut
            return
        for key, entry in data.items():
            spec = AUTOMATION_SPECS.get(key)
            if spec is None or not entry.get("enabled"):
                self._start.pop(key, None)
                continue
            points = entry["points"]
            n = len(points)
            start = self._start.setdefault(key, now)
            pos = ((now - start) / max(0.5, float(entry["period"])) % 1.0) * n
            i0 = int(pos) % n
            frac = pos - int(pos)
            v = points[i0] + (points[(i0 + 1) % n] - points[i0]) * frac
            set_param(params, key, spec[1] + (spec[2] - spec[1]) * v)


# --- Analyse audio -----------------------------------------------------------------

BANDS = {"bass": (20.0, 150.0), "mid": (150.0, 2000.0), "high": (2000.0, 10000.0)}
# Seuils absolus (amplitude de bin FFT, 1.0 = sinus pleine echelle) sous lesquels une
# bande est consideree comme silence : sans ca l'auto-gain ampliferait le bruit du micro.
BAND_GATE = {"bass": 0.0005, "mid": 0.0005, "high": 0.0005, "rms": 0.003}
PEAK_HALF_LIFE_S = 8.0       # decroissance du maximum glissant de l'auto-gain
ATTACK_TAU_S = 0.02          # lissage: attaque rapide
RELEASE_TAU_S = 0.25         # lissage: relachement lent
KICK_MIN = 0.002             # energie basses minimale pour declarer un kick
# Regles par balayage sur signaux synthetiques (voir CLAUDE.md): a sensibilite 1.0, zero
# faux positif sur bruit blanc constant, kicks francs detectes meme a -30 dB de micro.
KICK_RATIO_EXTRA = 1.2       # seuil: energie basses > base * (1 + extra / sensibilite)
KICK_K = 3.0                 # ... ET ecart a la base > k / sensibilite ecarts-types
KICK_REFRACTORY_S = 0.15
KICK_WARMUP_S = 0.5
BASELINE_RISE_TAU_S = 0.8    # ligne de base glissante (symetrique: une base qui redescend
BASELINE_FALL_TAU_S = 0.8    # plus vite qu'elle ne monte donne des faux positifs sur bruit)
DEV_TAU_S = 1.0              # fenetre de l'ecart-type de l'energie basses
BEAT_DECAY_TAU_S = 0.20      # decroissance de l'enveloppe u_beat
BEAT_IDLE_S = 10.0           # valeur de since_beat tant qu'aucun kick n'est vu


def _coef(dt: float, tau: float) -> float:
    return 1.0 - math.exp(-dt / tau)


class FeatureExtractor:
    """Fonctions pures d'analyse : fenetre de samples -> niveaux de bandes + kick.

    Aucun acces au temps reel ni aux fils : `process()` recoit `dt` (duree couverte par
    les nouveaux samples) et `now` (horloge, secondes), ce qui permet de le tester sur
    des signaux synthetiques (voir check_gl.py).
    """

    def __init__(self, rate: int, win_size: int = 2048, sensitivity: float = 1.0):
        self.rate = rate
        self.win_size = win_size
        self.sensitivity = sensitivity
        self.window = np.hanning(win_size).astype(np.float32)
        self.scale = 2.0 / float(self.window.sum())   # un sinus d'amplitude A donne un bin ~A
        freqs = np.fft.rfftfreq(win_size, 1.0 / rate)
        self.slices: dict[str, slice] = {}
        for name, (lo, hi) in BANDS.items():
            idx = np.nonzero((freqs >= lo) & (freqs < min(hi, rate / 2.0)))[0]
            self.slices[name] = slice(int(idx[0]), int(idx[-1]) + 1)
        self.peak = {k: 0.0 for k in (*BANDS, "rms")}
        self.smooth = {k: 0.0 for k in (*BANDS, "rms")}
        self.baseline: float | None = None
        self.dev2 = 0.0
        self.prev_bass = 0.0
        self.last_beat = -1e9
        self.elapsed = 0.0

    def process(self, samples, dt: float, now: float) -> tuple[dict, bool]:
        """Retourne (niveaux normalises lisses, kick_detecte)."""
        samples = np.asarray(samples, dtype=np.float32)
        mag = np.abs(np.fft.rfft(samples * self.window)) * self.scale
        raw = {name: float(math.sqrt(float(np.mean(mag[sl] ** 2)))) for name, sl in self.slices.items()}
        tail = samples[-512:]
        raw["rms"] = float(math.sqrt(float(np.mean(tail * tail))))

        levels = {}
        for name, e in raw.items():
            gate = BAND_GATE[name]
            self.peak[name] = max(e, self.peak[name] * 0.5 ** (dt / PEAK_HALF_LIFE_S))
            top = max(self.peak[name], gate * 6.0)
            n = min(max((e - gate) / (top - gate), 0.0), 1.0)
            tau = ATTACK_TAU_S if n > self.smooth[name] else RELEASE_TAU_S
            self.smooth[name] += (n - self.smooth[name]) * _coef(dt, tau)
            levels[name] = self.smooth[name]

        beat = self._detect_kick(raw["bass"], dt, now)
        levels["raw_bass"] = raw["bass"]
        return levels, beat

    def _detect_kick(self, b: float, dt: float, now: float) -> bool:
        """Kick = energie basses nettement au-dessus d'une ligne de base glissante :
        b - base > k * ecart-type ET b > base * ratio (seuil adaptatif, donc insensible
        au niveau du micro), avec periode refractaire. `sensitivity` > 1 abaisse les seuils.
        """
        self.elapsed += dt
        s = max(self.sensitivity, 0.05)
        ratio = 1.0 + KICK_RATIO_EXTRA / s
        k = KICK_K / s
        if self.baseline is None:
            self.baseline = b
        base = self.baseline
        dev = b - base
        std = math.sqrt(self.dev2)

        beat = (self.elapsed > KICK_WARMUP_S
                and now - self.last_beat > KICK_REFRACTORY_S
                and b > KICK_MIN
                and b > self.prev_bass
                and b > base * ratio
                and dev > k * std)
        if beat:
            self.last_beat = now

        # Statistiques mises a jour hors des kicks: l'ecart-type decrit le "bruit" ambiant.
        if now - self.last_beat > 0.3:
            self.dev2 += (dev * dev - self.dev2) * _coef(dt, DEV_TAU_S)
        tau = BASELINE_RISE_TAU_S if b > base else BASELINE_FALL_TAU_S
        self.baseline = base + (b - base) * _coef(dt, tau)
        self.prev_bass = b
        return beat


class RingBuffer:
    """Tampon circulaire mono float32, ecriture par le callback audio, lecture par l'analyse."""

    def __init__(self, size: int = 1 << 16):
        self.size = size
        self.buf = np.zeros(size, dtype=np.float32)
        self.pos = 0
        self.total = 0
        self.lock = threading.Lock()
        self.event = threading.Event()

    def write(self, x) -> None:
        x = np.asarray(x, dtype=np.float32)
        n = len(x)
        if n >= self.size:
            x = x[-self.size:]
            n = self.size
        with self.lock:
            end = self.pos + n
            if end <= self.size:
                self.buf[self.pos:end] = x
            else:
                k = self.size - self.pos
                self.buf[self.pos:] = x[:k]
                self.buf[:n - k] = x[k:]
            self.pos = end % self.size
            self.total += n
        self.event.set()

    def latest(self, n: int):
        out = np.zeros(n, dtype=np.float32)
        with self.lock:
            valid = min(n, self.total, self.size)
            if valid:
                idx = (self.pos - valid + np.arange(valid)) % self.size
                out[n - valid:] = self.buf[idx]
        return out


def synth_audio_block(start: int, n: int, rate: int, bpm: float = 120.0, rng=None):
    """Kick 60 Hz a `bpm` + tic aigu a contretemps + leger bruit de fond."""
    rng = rng or np.random.default_rng(0)
    t = (start + np.arange(n)) / rate
    period = 60.0 / bpm
    ph = t % period
    kick = 0.6 * np.sin(2 * np.pi * 60.0 * ph) * np.exp(-ph / 0.07)
    hp = ph - period / 2
    hat = np.where(hp > 0, rng.standard_normal(n) * 0.12 * np.exp(-np.clip(hp, 0, None) / 0.02), 0.0)
    return (kick + hat + rng.standard_normal(n) * 0.002).astype(np.float32)


class SyntheticAudio:
    """Source audio de test : ecrit des blocs synthetiques dans le ring, au rythme reel."""

    def __init__(self, ring: RingBuffer, rate: int = 48000, block: int = 512, bpm: float = 120.0):
        self.ring, self.rate, self.block, self.bpm = ring, rate, block, bpm
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._run, daemon=True)

    def start(self):
        self._thread.start()

    def stop(self):
        self._stop.set()

    def _run(self):
        rng = np.random.default_rng(1)
        idx = 0
        t_next = time.monotonic()
        while not self._stop.is_set():
            self.ring.write(synth_audio_block(idx, self.block, self.rate, self.bpm, rng))
            idx += self.block
            t_next += self.block / self.rate
            delay = t_next - time.monotonic()
            if delay > 0:
                time.sleep(delay)


def find_input_device(query: str | None, dshow_name: str | None = None):
    """Index sounddevice d'une entree, ou None pour le defaut. Correspondance par
    sous-chaine (insensible a la casse) ; WASAPI prefere. Un numero est pris tel quel."""
    devices = sd.query_devices()
    hostapis = sd.query_hostapis()
    inputs = [(i, d) for i, d in enumerate(devices) if d["max_input_channels"] > 0]

    def rank(item):
        return 0 if "WASAPI" in hostapis[item[1]["hostapi"]]["name"] else 1

    def match(name: str):
        q = name.lower()
        found = sorted((c for c in inputs if q in c[1]["name"].lower()), key=rank)
        return found[0][0] if found else None

    if query:
        if query.isdigit():
            return int(query)
        idx = match(query)
        if idx is None:
            print(f"Aucune entree sounddevice ne contient '{query}'. Disponibles:", file=sys.stderr)
            print_audio_devices(file=sys.stderr)
            sys.exit(2)
        return idx
    if dshow_name:
        idx = match(dshow_name)
        if idx is not None:
            return idx
        print(f"Note: '{dshow_name}' introuvable cote sounddevice, entree par defaut "
              "utilisee (voir --audio-device).", file=sys.stderr)
    for api in hostapis:
        if "WASAPI" in api["name"] and api["default_input_device"] >= 0:
            return api["default_input_device"]
    return None


def print_audio_devices(file=sys.stdout) -> None:
    hostapis = sd.query_hostapis()
    print("Entrees sounddevice (utilise --audio-device <sous-chaine ou numero>):", file=file)
    for i, d in enumerate(sd.query_devices()):
        if d["max_input_channels"] > 0:
            api = hostapis[d["hostapi"]]["name"]
            print(f"  {i:3d}  [{api}] {d['name']}  ({d['default_samplerate']:.0f} Hz)", file=file)


class SoundDeviceInput:
    """Capture sounddevice en callback (mono, blocs courts) vers un RingBuffer."""

    def __init__(self, device, ring: RingBuffer, blocksize: int = 512):
        info = sd.query_devices(device, "input")
        self.rate = int(info["default_samplerate"])
        self.name = info["name"]
        self.channels = max(1, min(2, int(info["max_input_channels"])))
        self.ring = ring
        self.stream = sd.InputStream(
            device=device, channels=self.channels, samplerate=self.rate, blocksize=blocksize,
            dtype="float32", latency="low", callback=self._callback)

    def _callback(self, indata, frames, time_info, status):
        if status:
            self.dropped = getattr(self, "dropped", 0) + 1
        self.ring.write(indata[:, 0] if self.channels == 1 else indata.mean(axis=1))

    def start(self):
        self.stream.start()

    def stop(self):
        self.stream.stop()
        self.stream.close()

    @property
    def latency_ms(self) -> float:
        return float(self.stream.latency) * 1000.0


class AudioAnalyzer:
    """Fil d'analyse : ring -> FeatureExtractor -> dernier etat lu par le fil GL."""

    def __init__(self, ring: RingBuffer, extractor: FeatureExtractor):
        self.ring = ring
        self.extractor = extractor
        self.lock = threading.Lock()
        self.levels = {"bass": 0.0, "mid": 0.0, "high": 0.0, "rms": 0.0, "raw_bass": 0.0}
        self.t_beat: float | None = None
        self.beats = 0
        self._last_total = 0
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._run, daemon=True)

    def start(self):
        self._thread.start()

    def stop(self):
        self._stop.set()
        self.ring.event.set()

    def _run(self):
        while not self._stop.is_set():
            if not self.ring.event.wait(0.1):
                continue
            rate = self.extractor.rate    # relu: l'entree audio peut changer en cours de route
            self.ring.event.clear()
            total = self.ring.total
            new = total - self._last_total
            if new <= 0:
                continue
            self._last_total = total
            dt = min(new / rate, 0.1)
            window = self.ring.latest(self.extractor.win_size)
            now = time.monotonic()
            levels, beat = self.extractor.process(window, dt, now)
            with self.lock:
                self.levels = levels
                if beat:
                    self.t_beat = now
                    self.beats += 1

    def latest(self) -> dict:
        with self.lock:
            state = dict(self.levels)
            t_beat = self.t_beat
            state["beats"] = self.beats
        since = BEAT_IDLE_S if t_beat is None else min(time.monotonic() - t_beat, BEAT_IDLE_S)
        state["since_beat"] = since
        state["beat"] = math.exp(-since / BEAT_DECAY_TAU_S) if t_beat is not None else 0.0
        return state


# --- Video ------------------------------------------------------------------------

class FrameReader:
    """Lit un flux rawvideo et publie la derniere frame ENTIERE (latest wins).

    Le fil lecteur accumule exactement `frame_size` octets avant de publier (jamais un
    bloc de taille arbitraire, cf. relay_loop() dans le CLAUDE.md d'audio2wave), puis
    echange les deux tampons sous verrou. Une frame non consommee est ecrasee : pas de
    file, donc pas de latence croissante. Une frame partielle en fin de flux est jetee.
    """

    CHUNK = 1 << 20

    def __init__(self, stream, frame_size: int, on_frame=None, hold_on_eof: bool = False):
        self._stream = stream
        self.frame_size = frame_size
        self.on_frame = on_frame          # crochet de test, appele avec la frame complete
        self.hold_on_eof = hold_on_eof    # fin de flux: attendre un remplacant au lieu de s'arreter
        self._front = bytearray(frame_size)
        self._back = bytearray(frame_size)
        self._lock = threading.Lock()
        self._pending = None
        self._stop = threading.Event()
        self._seq = 0
        self._uploaded = 0
        self.frames_read = 0
        self.ended = False
        self._thread = threading.Thread(target=self._run, daemon=True)

    def start(self):
        self._thread.start()

    def stop(self):
        self._stop.set()

    def join(self, timeout: float | None = None):
        self._thread.join(timeout)

    def switch_stream(self, stream) -> None:
        """Passe a un nouveau flux (producteur remplace a chaud). Le lecteur finit le flux
        courant jusqu'a sa fin (l'appelant termine l'ancien producteur), jette toute frame
        PARTIELLE de l'ancien flux, puis repart sur le nouveau: aucune frame melangee."""
        self._pending = stream

    def _take_pending(self) -> bool:
        if self._pending is None:
            return False
        old, self._stream, self._pending = self._stream, self._pending, None
        try:
            old.close()
        except Exception:
            pass
        self.ended = False
        return True

    def _run(self):
        size = self.frame_size
        while not self._stop.is_set():
            stream = self._stream
            use_readinto = hasattr(stream, "readinto")
            mv = memoryview(self._back)
            got = 0
            eof = False
            while got < size:
                try:
                    if use_readinto:
                        n = stream.readinto(mv[got:])
                    else:
                        chunk = stream.read(min(size - got, self.CHUNK))
                        n = len(chunk)
                        mv[got:got + n] = chunk
                except (ValueError, OSError):
                    n = 0
                if not n:
                    eof = True
                    break
                got += n
            if eof:
                mv.release()          # frame partielle: jetee
                if self._take_pending():
                    continue
                self.ended = True
                if not self.hold_on_eof:
                    return
                while not self._stop.is_set() and not self._take_pending():
                    time.sleep(0.05)
                continue
            self.frames_read += 1
            if self.on_frame:
                self.on_frame(mv)
            mv.release()
            with self._lock:
                self._front, self._back = self._back, self._front
                self._seq += 1

    def upload_to(self, texture) -> bool:
        """Copie la derniere frame publiee dans la texture GL si elle est nouvelle."""
        with self._lock:
            if self._seq == self._uploaded:
                return False
            texture.write(self._front, alignment=1)
            self._uploaded = self._seq
            return True

    def take(self) -> bytes | None:
        """Derniere frame publiee (copie), ou None si aucune nouvelle. Pour les tests."""
        with self._lock:
            if self._seq == self._uploaded:
                return None
            self._uploaded = self._seq
            return bytes(self._front)


class SyntheticVideoStream:
    """Flux rawvideo rgb24 de test (degrade + damier animes), cadence temps reel.
    N'expose que read(), ce qui exerce le meme chemin que le vrai producteur."""

    def __init__(self, width: int, height: int, fps: int = 30):
        self.w, self.h, self.fps = width, height, fps
        self._gx = (np.arange(width, dtype=np.uint16) * 255 // max(width - 1, 1)).astype(np.uint8)[None, :]
        self._gy = (np.arange(height, dtype=np.uint16) * 255 // max(height - 1, 1)).astype(np.uint8)[:, None]
        self._xx = (np.arange(width) // 80)[None, :]
        self._yy = (np.arange(height) // 80)[:, None]
        self._data = b""
        self._off = 0
        self._n = 0
        self._t_next = time.monotonic()

    def _frame(self) -> bytes:
        n = self._n
        r = (self._gx + np.uint8((n * 3) & 255)) * np.ones((self.h, 1), np.uint8)
        g = (self._gy + np.uint8((n * 2) & 255)) * np.ones((1, self.w), np.uint8)
        check = (((self._xx + self._yy + n // 15) & 1) * 60).astype(np.uint8)
        b = (255 - self._gx) * np.ones((self.h, 1), np.uint8)
        img = np.stack([r, g, b], axis=2).astype(np.uint16) + check[:, :, None]
        return np.minimum(img, 255).astype(np.uint8).tobytes()

    def read(self, n: int) -> bytes:
        if self._off >= len(self._data):
            delay = self._t_next - time.monotonic()
            if delay > 0:
                time.sleep(delay)
            self._t_next = max(self._t_next + 1.0 / self.fps, time.monotonic() - 0.5)
            self._data = self._frame()
            self._off = 0
            self._n += 1
        chunk = self._data[self._off:self._off + n]
        self._off += len(chunk)
        return chunk


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

        self.video_tex = ctx.texture(video_size, 3, data=bytes(video_size[0] * video_size[1] * 3))
        self.video_tex.filter = (moderngl.LINEAR, moderngl.LINEAR)
        self.video_tex.repeat_x = self.video_tex.repeat_y = False

        self.logo_tex = None
        self.logo_aspect = 1.0
        self.has_logo = False
        self.logo_kind = "image"
        self.logo_path = ""
        self.logo_video: LogoVideo | None = None
        # Dernier chemin DEMANDE (charge ou non): evite de retenter un fichier invalide a chaque image.
        self.logo_requested = logo_path
        self.set_logo(logo_rgba, logo_path)

        self.vbo = ctx.buffer(np.array([-1, -1, 1, -1, -1, 1, 1, 1], dtype="f4").tobytes())
        self.scene_prog = self.post_prog = self.scene_vao = self.post_vao = None
        self.scene_tex = self.scene_fbo = self.layer_tex = self.layer_fbo = None
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
        except Exception as exc:
            raise ShaderError(str(exc)) from exc
        self.scene_prog, self.post_prog = scene, post
        self.scene_vao = self.ctx.vertex_array(scene, [(self.vbo, "2f", "in_pos")])
        self.post_vao = self.ctx.vertex_array(post, [(self.vbo, "2f", "in_pos")])

    def _ensure_scene(self, size: tuple[int, int]) -> None:
        if self.scene_tex is not None and self.scene_tex.size == size:
            return
        for old in (self.scene_tex, self.layer_tex):
            if old is not None:
                old.release()
        # scene_* = couche FOND (opaque), layer_* = couche LOGO (rgba premultiplie, transparente)
        self.scene_tex = self.ctx.texture(size, 4)
        self.layer_tex = self.ctx.texture(size, 4)
        for tex in (self.scene_tex, self.layer_tex):
            tex.filter = (moderngl.LINEAR, moderngl.LINEAR)
            tex.repeat_x = tex.repeat_y = False
        self.scene_fbo = self.ctx.framebuffer(color_attachments=[self.scene_tex])
        self.layer_fbo = self.ctx.framebuffer(color_attachments=[self.layer_tex])

    @staticmethod
    def _set(prog, name: str, value) -> None:
        if name in prog:   # un uniform inutilise est elimine par le compilateur
            prog[name].value = value

    def draw(self, target, size: tuple[int, int], state: dict, params: dict, t: float) -> None:
        width, height = size
        p = {**DEFAULT_PARAMS, **params}       # un dict partiel (tests) reste valide
        fx_on, fx_int, master = p["fx_on"], p["fx_int"], p["master"]
        eff = [float(fx_on[i]) * float(fx_int[i]) * master for i in range(5)]
        bass, high, beat = state["bass"], state["high"], state["beat"]

        self._ensure_scene(size)

        # Passe 1 : video + logo (pulse, jitter, contour lumineux). L'interrupteur/intensite
        # "logo" (effet 5) module les trois reactions a l'audio; position, taille et
        # opacite restent celles des reglages meme effet coupe.
        logo_fx = eff[4]
        pulse = p["logo_pulse"] * beat * logo_fx
        jit = p["logo_jitter"] * high * logo_fx
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
        self._set(sp, "u_logo_opacity", min(max(float(p["logo_opacity"]), 0.0), 1.0))
        self._set(sp, "u_bass", float(bass))
        self._set(sp, "u_glow", min(float(p["logo_glow"]) * logo_fx, 3.0))
        self._set(sp, "u_glow_radius", float(p["logo_glow_radius"]))
        self._set(sp, "u_glow_color", hex_to_rgb(p["logo_glow_color"]))
        self._set(sp, "u_beat", float(beat))

        # Fond: video ffmpeg ou motif genere. Les phases avancent meme quand le motif est cache,
        # pour qu'un retour au motif ne reparte pas d'un etat fige.
        dt = 0.0 if self._last_t is None else min(max(t - self._last_t, 0.0), 0.1)
        self._last_t = t
        react = max(float(p["bg_react"]), 0.0)
        self._bg_scroll += dt * float(p["bg_speed"]) * (1.0 + react * float(bass) * 1.5)
        self._bg_flip_t += dt * float(p["bg_flip"])
        flips = self._bg_flip_t + (float(state.get("beats", 0)) if react > 0 else 0.0)
        self._set(sp, "u_bg_mode", 1.0 if p["bg_mode"] == "pattern" else 0.0)
        self._set(sp, "u_bg_palette", 1.0 if p["bg_palette"] == "duo" else 0.0)
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
        else:
            self.layer_fbo.clear(0.0, 0.0, 0.0, 0.0)

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
        self._set(pp, "u_since_beat", float(state["since_beat"]))
        self._set(pp, "u_hud", 1.0 if self.hud else 0.0)
        self._set(pp, "u_fx_state", tuple(float(v) for v in fx_on))
        self.scene_tex.use(0)
        self.layer_tex.use(1)
        target.viewport = (0, 0, width, height)
        target.use()
        self.post_vao.render(moderngl.TRIANGLE_STRIP)


# --- Parametres en direct -------------------------------------------------------------

def load_params() -> dict:
    params = json.loads(json.dumps(DEFAULT_PARAMS))
    try:
        saved = json.loads(PARAMS_PATH.read_text(encoding="utf-8"))
        for key, default in DEFAULT_PARAMS.items():
            value = saved.get(key, default)
            if isinstance(default, list):
                if isinstance(value, list) and len(value) == len(default):
                    params[key] = value
            elif isinstance(default, str):
                if isinstance(value, str):
                    params[key] = value
            elif isinstance(value, (int, float)) and not isinstance(value, bool):
                params[key] = float(value)
        if isinstance(saved.get("_automation"), dict):
            params["_automation"] = merge_automation(saved["_automation"])
    except FileNotFoundError:
        pass
    except (OSError, ValueError) as exc:
        print(f"Reglages ignores ({PARAMS_PATH}): {exc}", file=sys.stderr)
    return params


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


def save_params(params: dict) -> None:
    PARAMS_PATH.parent.mkdir(parents=True, exist_ok=True)
    PARAMS_PATH.write_text(json.dumps(params, indent=2), encoding="utf-8")


# --- Pont vers audio2wave_live ---------------------------------------------------------

def load_live(a2w_dir: Path):
    if not (a2w_dir / "audio2wave_live.py").is_file():
        print(f"audio2wave_live.py introuvable dans {a2w_dir}. Indique le depot avec "
              "--a2w-dir ou la variable AUDIO2WAVE_DIR.", file=sys.stderr)
        sys.exit(2)
    sys.path.insert(0, str(a2w_dir))
    import audio2wave_live
    return audio2wave_live


def build_live_args(live, device: str | None, size: tuple[int, int], fps: int, extra: str | None,
                    fullscreen: bool = True):
    argv = ["audio2wave_live.py", "--size", f"{size[0]}x{size[1]}", "--fps", str(fps)]
    if not fullscreen:
        # Sans effet sur ffmpeg, mais la case "Plein ecran" de la GUI d'audio2wave la reflete.
        argv.append("--no-fullscreen")
    if device:
        argv += ["-d", device]
    argv += shlex.split(extra or "")
    old = sys.argv
    sys.argv = argv
    try:
        return live.parse_args()
    finally:
        sys.argv = old


def format_command(cmd: list[str]) -> str:
    return " ".join(f'"{c}"' if " " in c else c for c in cmd)


PIPE_BUFFER = 1 << 24   # 16 Mo: plusieurs frames 1080p
PRODUCER_WARMUP_CAP_S = 2.0


@functools.lru_cache(maxsize=1)
def ffmpeg_color_names() -> frozenset[str]:
    """Noms de couleur connus d'ffmpeg (`ffmpeg -colors`, en minuscules). Vide si la liste est illisible
    (on n'invalide alors rien)."""
    try:
        out = subprocess.run([shutil.which("ffmpeg") or "ffmpeg", "-hide_banner", "-colors"],
                             capture_output=True, text=True, timeout=10).stdout
    except (OSError, subprocess.SubprocessError):
        return frozenset()
    names = [line.split()[0].lower() for line in out.splitlines()[1:] if line.split()]
    return frozenset(names) | {"random"}


def is_ffmpeg_color(text: str) -> bool:
    """Couleur acceptee par ffmpeg : nom connu, 0xRRGGBB ou #RRGGBB (+ alpha 'AA' ou '@0.5' eventuel)."""
    base = text.strip().split("@")[0].lower()
    if not base:
        return False
    body = base[2:] if base.startswith("0x") else base[1:] if base.startswith("#") else None
    if body is not None:
        return len(body) in (6, 8) and all(c in HEX_DIGITS for c in body)
    names = ffmpeg_color_names()
    return not names or base in names


def invalid_colors(new_args, current_args) -> list[str]:
    """Couleurs des nouvelles options (trace, separees par |, et fond) qu'ffmpeg ne connait pas. Une
    valeur deja en service est toleree (le defaut 'grey' d'audio2wave_live n'existe pas pour ffmpeg mais
    n'arrete pas le producteur). Sert a NE PAS relancer ffmpeg sur un nom a moitie tape ('t' pour 'teal')."""
    bad = []
    current = {str(getattr(current_args, "colors", "")), str(getattr(current_args, "bg_color", ""))}
    current |= set(str(getattr(current_args, "colors", "")).split("|"))
    candidates = [c for c in str(getattr(new_args, "colors", "")).split("|")] + [str(getattr(new_args, "bg_color", ""))]
    for color in candidates:
        color = color.strip()
        if color and color not in current and not is_ffmpeg_color(color):
            bad.append(color)
    return bad


def spawn_big_pipe(cmd: list[str], **popen_kw) -> subprocess.Popen | None:
    """Popen(cmd) dont stdout est un pipe Windows a GROS tampon (16 Mo). Sous Windows un pipe
    anonyme fait 4 Ko : le lecteur devait alors reprendre le GIL ~700 fois par frame 720p, et
    des que la boucle de rendu tournait vite il ne suivait plus (mesure: 30 -> 7 frames/s).
    Avec 16 Mo, une frame se lit en un ou deux appels. None hors Windows ou en cas d'echec."""
    if os.name != "nt":
        return None
    try:
        import msvcrt
        from ctypes import wintypes
        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        read_h, write_h = wintypes.HANDLE(), wintypes.HANDLE()
        if not kernel32.CreatePipe(ctypes.byref(read_h), ctypes.byref(write_h), None, PIPE_BUFFER):
            raise OSError(ctypes.get_last_error(), "CreatePipe")
        write_fd = msvcrt.open_osfhandle(write_h.value, 0)
        read_fd = msvcrt.open_osfhandle(read_h.value, os.O_RDONLY)
        try:
            proc = subprocess.Popen(cmd, stdout=write_fd, **popen_kw)
        finally:
            os.close(write_fd)          # le parent ne garde pas l'extremite d'ecriture
        proc.stdout = open(read_fd, "rb", buffering=0)
        return proc
    except Exception as exc:
        print(f"Pipe a gros tampon indisponible ({exc}), repli sur le pipe standard.", file=sys.stderr)
        return None


def spawn_producer(live, live_args) -> subprocess.Popen:
    """Lance le producteur ffmpeg de audio2wave_live (meme commande), stdout relie a un
    pipe a gros tampon (voir spawn_big_pipe). Repli sur live.spawn_producer()."""
    return spawn_big_pipe(live.producer_command(live_args)) or live.spawn_producer(live_args)


# --- Logo anime (video) -----------------------------------------------------------------

LOGO_VIDEO_MAX_SIDE = 960       # cote max decode (un logo n'a pas besoin de plus)
LOGO_KEY_SIMILARITY = 0.3
LOGO_KEY_BLEND = 0.1
HEX_DIGITS = "0123456789abcdefABCDEF"
LOGO_VIDEO_EXTENSIONS = "*.webm *.mov *.mp4 *.mkv *.gif *.apng *.webp *.avi"


def probe_video(path: str) -> tuple[int, int, str]:
    """(largeur, hauteur, codec) du premier flux video, via ffprobe. Leve RuntimeError sinon."""
    ffprobe = shutil.which("ffprobe")
    if not ffprobe:
        raise RuntimeError("ffprobe introuvable (installe ffmpeg complet)")
    out = subprocess.run([ffprobe, "-v", "error", "-select_streams", "v:0", "-show_entries",
                          "stream=width,height,codec_name", "-of", "json", path],
                         capture_output=True, text=True, timeout=15)
    try:
        st = json.loads(out.stdout)["streams"][0]
        return int(st["width"]), int(st["height"]), st.get("codec_name", "")
    except (KeyError, IndexError, ValueError):
        raise RuntimeError(f"pas de flux video lisible ({out.stderr.strip()[:120] or 'fichier invalide'})")


def logo_video_command(path: str, size: tuple[int, int], codec: str, key: str = "") -> list[str]:
    """Commande ffmpeg: decode `path` en boucle, a sa cadence native (-re), en rawvideo rgba
    (alpha DROIT, non premultiplie: le shader le premultiplie). `key` (#rrggbb) detoure cette
    couleur pour un fichier sans canal alpha (fond vert...)."""
    cmd = [shutil.which("ffmpeg") or "ffmpeg", "-hide_banner", "-loglevel", "error", "-re", "-stream_loop", "-1"]
    # Le decodeur vp9/vp8 natif d'ffmpeg ignore l'alpha des .webm: il faut celui de libvpx.
    if codec == "vp9":
        cmd += ["-c:v", "libvpx-vp9"]
    elif codec == "vp8":
        cmd += ["-c:v", "libvpx"]
    filters = [f"scale={size[0]}:{size[1]}:flags=bilinear", "format=rgba"]
    if key:
        filters.append(f"colorkey=0x{key.lstrip('#')}:{LOGO_KEY_SIMILARITY}:{LOGO_KEY_BLEND}")
    return cmd + ["-i", path, "-an", "-vf", ",".join(filters), "-f", "rawvideo", "-pix_fmt", "rgba", "-"]


class LogoVideo:
    """Logo anime: un ffmpeg qui boucle sur le fichier + un FrameReader (latest wins). La texture
    est mise a jour depuis le fil GL par `upload_to` ; la cadence est celle du fichier."""

    def __init__(self, path: str, key: str = ""):
        w, h, codec = probe_video(path)
        ratio = min(1.0, LOGO_VIDEO_MAX_SIDE / max(w, h))
        self.size = (max(2, round(w * ratio)), max(2, round(h * ratio)))
        cmd = logo_video_command(path, self.size, codec, key)
        self.proc = spawn_big_pipe(cmd, stderr=subprocess.PIPE) or subprocess.Popen(
            cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, bufsize=0)
        self.reader = FrameReader(self.proc.stdout, self.size[0] * self.size[1] * 4)
        self.reader.start()

    def error(self) -> str:
        """Message d'ffmpeg si le processus est mort sans avoir produit une seule image."""
        if self.proc.poll() is None or self.reader.frames_read:
            return ""
        try:
            return self.proc.stderr.read().decode(errors="replace").strip().splitlines()[-1][:160]
        except Exception:
            return "ffmpeg s'est arrete"

    def stop(self) -> None:
        self.reader.stop()
        stop_process(self.proc)
        for stream in (self.proc.stdout, self.proc.stderr):
            try:
                stream.close()
            except Exception:
                pass


def stop_process(proc: subprocess.Popen | None) -> None:
    if proc is None or proc.poll() is not None:
        return
    proc.terminate()
    try:
        proc.wait(timeout=3)
    except subprocess.TimeoutExpired:
        proc.kill()
        proc.wait()


class ProducerManager:
    """Possede le producteur ffmpeg ET son FrameReader, et remplace le producteur a chaud
    quand un reglage du visuel change (GUI), sans jamais toucher a la fenetre GL.

    Meme principe que le redemarrage "doux" d'audio2wave_live (voir son CLAUDE.md) : le
    nouveau producteur est lance a cote de l'ancien puis "chauffe" (ses frames ENTIERES sont
    lues et jetees pendant `producer_warmup_seconds`, sinon ffmpeg bloque sur un pipe plein et
    sa fenetre --averaging ne converge pas: a-coup visible au basculement), et n'est montre
    qu'ensuite. S'il meurt pendant la chauffe (reglage invalide, ex. couleur inconnue),
    l'ancien reste en place et un message est laisse dans `status["live"]`.
    Les demandes rapprochees sont fusionnees: seule la derniere est jouee.
    """

    def __init__(self, live, live_args, frame_size: int, status: dict, spawn=None):
        self.live = live
        # COPIE: la GUI d'audio2wave mute ses options en place; si on gardait le meme objet, le gestionnaire
        # verrait toujours des options "deja appliquees" et ne redemarrerait plus jamais ffmpeg.
        self.args = copy.copy(live_args)
        self.frame_size = frame_size
        self.status = status
        self._spawn = spawn or (lambda a: spawn_producer(live, a))
        self.producer = self._spawn(live_args)
        self.reader = FrameReader(self.producer.stdout, frame_size, hold_on_eof=True)
        self.restarts = 0
        self._request: object | None = None
        self._lock = threading.Lock()
        self._wake = threading.Event()
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._run, daemon=True)

    def start(self) -> None:
        self.reader.start()
        self._thread.start()

    def request_restart(self, new_args) -> None:
        """Programme le remplacement du producteur par un lance avec `new_args`."""
        with self._lock:
            self._request = new_args
        self._wake.set()

    def stop(self) -> None:
        self._stop.set()
        self._wake.set()
        self.reader.stop()
        stop_process(self.producer)

    def _run(self) -> None:
        while not self._stop.is_set():
            self._wake.wait(0.2)
            if self._stop.is_set():
                return
            with self._lock:
                new_args, self._request = self._request, None
                self._wake.clear()
            if new_args is not None:
                self._replace(new_args)

    def _warmup_seconds(self, args) -> float:
        try:
            wanted = self.live.producer_warmup_seconds(args)
        except Exception:
            wanted = 0.5
        return max(getattr(self.live, "RESTART_GRACE_S", 0.3), min(wanted, PRODUCER_WARMUP_CAP_S))

    def _drain(self, proc, seconds: float) -> bool:
        """Lit et jette des frames ENTIERES pendant `seconds` (au moins une). False si le
        producteur s'arrete avant."""
        size = self.frame_size
        mv = memoryview(bytearray(size))
        deadline = time.monotonic() + seconds
        first = True
        while first or time.monotonic() < deadline:
            first = False
            got = 0
            while got < size:
                if self._stop.is_set():
                    return False
                try:
                    n = proc.stdout.readinto(mv[got:])
                except (ValueError, OSError):
                    n = 0
                if not n:
                    return False
                got += n
        return True

    def _replace(self, new_args) -> None:
        self.status["live"] = "Redemarrage du flux..."
        try:
            new = self._spawn(new_args)
        except Exception as exc:
            self.status["live"] = f"Lancement de ffmpeg impossible: {exc}"
            return
        if not self._drain(new, self._warmup_seconds(new_args)):
            stop_process(new)
            self.status["live"] = ("Reglage refuse (ffmpeg s'est arrete): ancien flux conserve. "
                                   "Verifie couleurs / valeurs.")
            return
        old = self.producer
        self.producer = new
        self.args = new_args
        self.reader.switch_stream(new.stdout)
        stop_process(old)          # EOF sur l'ancien flux -> le lecteur bascule sur le nouveau
        self.restarts += 1
        self.status["live"] = "Flux a jour"


# --- Fenetre glfw ------------------------------------------------------------------

def _addr(monitor) -> int:
    return ctypes.cast(monitor, ctypes.c_void_p).value or 0


def pick_monitor(index: int | None):
    """Moniteur cible : --monitor N (index glfw), sinon le premier NON principal (l'ordre
    des moniteurs n'est pas fiable), sinon le principal."""
    monitors = glfw.get_monitors()
    if index is not None:
        if not 0 <= index < len(monitors):
            print(f"--monitor {index} invalide ({len(monitors)} moniteur(s) detecte(s)).", file=sys.stderr)
            sys.exit(2)
        return monitors[index]
    primary = _addr(glfw.get_primary_monitor())
    for m in monitors:
        if _addr(m) != primary:
            return m
    return monitors[0]


def has_secondary_monitor() -> bool:
    """Vrai s'il y a plus d'un moniteur (Windows, EnumDisplayMonitors). Sert a decider du plein
    ecran par defaut AVANT d'ouvrir la moindre fenetre (la GUI doit connaitre l'etat au depart)."""
    if os.name != "nt":
        return False
    try:
        return ctypes.windll.user32.GetSystemMetrics(80) > 1     # SM_CMONITORS
    except Exception:
        return False


def resolve_fullscreen(requested: bool | None, monitor_index: int | None, has_secondary: bool) -> bool:
    """Plein ecran par defaut UNIQUEMENT s'il y a un second moniteur (le videoprojecteur) ou si
    --monitor en designe un explicitement. Avec un seul ecran, un plein ecran borderless
    recouvrirait le poste de travail et la GUI: il faut le demander (--fullscreen)."""
    if requested is not None:
        return requested
    return monitor_index is not None or has_secondary


def hide_cursor(fullscreen: bool, on_primary: bool) -> bool:
    """Le curseur n'est masque qu'en plein ecran sur un moniteur NON principal (projecteur).
    Sur le moniteur principal, le masquer ferait perdre la souris a l'utilisateur."""
    return fullscreen and not on_primary


class Window:
    def __init__(self, monitor, windowed_size: tuple[int, int], fullscreen: bool):
        self.monitor = monitor
        self.is_primary = _addr(monitor) == _addr(glfw.get_primary_monitor())
        self.mx, self.my = glfw.get_monitor_pos(monitor)
        mode = glfw.get_video_mode(monitor)
        self.mw, self.mh = mode.size.width, mode.size.height
        self.windowed_size = windowed_size
        self.fullscreen = fullscreen
        glfw.window_hint(glfw.CONTEXT_VERSION_MAJOR, 3)
        glfw.window_hint(glfw.CONTEXT_VERSION_MINOR, 3)
        glfw.window_hint(glfw.OPENGL_PROFILE, glfw.OPENGL_CORE_PROFILE)
        glfw.window_hint(glfw.AUTO_ICONIFY, False)
        glfw.window_hint(glfw.VISIBLE, False)
        glfw.window_hint(glfw.DECORATED, not fullscreen)
        w, h = (self.mw, self.mh) if fullscreen else windowed_size
        self.handle = glfw.create_window(w, h, "casual-overlay GL", None, None)
        if not self.handle:
            print("Impossible de creer la fenetre OpenGL 3.3 (pilote graphique ?).", file=sys.stderr)
            sys.exit(1)
        self._place()
        glfw.make_context_current(self.handle)
        glfw.swap_interval(1)
        glfw.show_window(self.handle)

    def _place(self) -> None:
        if self.fullscreen:
            glfw.set_window_attrib(self.handle, glfw.DECORATED, False)
            glfw.set_window_pos(self.handle, self.mx, self.my)
            glfw.set_window_size(self.handle, self.mw, self.mh)
            hidden = hide_cursor(True, self.is_primary)
            glfw.set_input_mode(self.handle, glfw.CURSOR, glfw.CURSOR_HIDDEN if hidden else glfw.CURSOR_NORMAL)
        else:
            w, h = self.windowed_size
            glfw.set_window_attrib(self.handle, glfw.DECORATED, True)
            glfw.set_window_size(self.handle, w, h)
            glfw.set_window_pos(self.handle, self.mx + (self.mw - w) // 2, self.my + (self.mh - h) // 2)
            glfw.set_input_mode(self.handle, glfw.CURSOR, glfw.CURSOR_NORMAL)

    def toggle_fullscreen(self) -> None:
        self.fullscreen = not self.fullscreen
        self._place()

    def framebuffer_size(self) -> tuple[int, int]:
        w, h = glfw.get_framebuffer_size(self.handle)
        return max(w, 1), max(h, 1)


# --- Application ---------------------------------------------------------------------

def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(
        description="Rendu OpenGL du visuel audio2wave_live avec logo et effets reactifs a l'audio.",
        formatter_class=argparse.RawDescriptionHelpFormatter, epilog=__doc__)
    p.add_argument("-d", "--device", help="Entree DirectShow pour ffmpeg (voir --list-devices)")
    p.add_argument("--audio-device", help="Entree sounddevice pour l'analyse: sous-chaine ou numero "
                   "(voir --list-audio-devices). Defaut: meme nom que --device si trouve, sinon defaut systeme")
    p.add_argument("--gui", action="store_true",
                   help="Ouvre la fenetre de reglages (tkinter): visuel live, logo et effets en direct")
    p.add_argument("--background", choices=["live", "pattern"], default=None,
                   help="Fond: live = spectre ffmpeg, pattern = motif genere (defaut: reglage sauve, sinon live)")
    p.add_argument("--text", default=None,
                   help='Incruste ce texte a la place du logo PNG (ex. --text "CASUAL RAVERS"; "\\n" = retour '
                        "a la ligne). Police, couleur, alignement et taille se reglent dans la GUI")
    p.add_argument("--logo", default=None,
                   help="PNG RGBA a incruster, ou 'none' (defaut: reglage sauve, sinon "
                        "asset/Casual Ravers - Kit_Sigle - Blanc.png)")
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
                   help="Fenetre borderless plein ecran sur le moniteur cible. Defaut: active seulement s'il "
                        "y a un SECOND moniteur (le projecteur); avec un seul ecran, fenetre normale "
                        "(--fullscreen pour forcer, --no-fullscreen pour l'interdire)")
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
    args.fullscreen = resolve_fullscreen(args.fullscreen, args.monitor, has_secondary_monitor())

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
        self.stop_event = threading.Event()
        self.finished_event = threading.Event()
        self.renderer: Renderer | None = None
        self.window: Window | None = None


def run_app(args, live, live_args, render_size: tuple[int, int]) -> None:
    params = load_params()
    if args.sensitivity is not None:
        params["sensitivity"] = args.sensitivity
    params["_automation"] = merge_automation(params.get("_automation"))
    if args.automation is not None:
        params["auto_master"] = 1.0 if args.automation else 0.0
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
    thread = None
    try:
        audio.start()
        if manager is not None:
            manager.start()
        else:
            reader.start()
        if args.gui:
            import gl_gui
            thread = threading.Thread(target=gl_main, args=(s,), name="gl", daemon=True)
            thread.start()
            gl_gui.run_gui(s, live)
        else:
            gl_main(s)
    finally:
        s.stop_event.set()
        if thread is not None:
            thread.join(5)
        audio.stop()
        if manager is not None:
            manager.stop()
        else:
            reader.stop()


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


def gl_main(s: Session) -> None:
    """Fenetre GL + boucle de rendu. Tous les appels glfw partent de CE fil (init, creation,
    evenements, destruction): avec --gui il tourne a cote de tkinter, qui garde le fil principal."""
    args = s.args
    window = None
    try:
        if not glfw.init():
            print("Initialisation de glfw impossible.", file=sys.stderr)
            sys.exit(1)
        monitor = pick_monitor(args.monitor)
        window = Window(monitor, s.win_size, args.fullscreen)
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
        if s.renderer is not None:
            s.renderer.release_logo_video()
        s.renderer = None
        if window is not None:
            glfw.destroy_window(window.handle)
        glfw.terminate()
        s.finished_event.set()


def loop(s: Session, window: Window, ctx, renderer: Renderer) -> None:
    args, params, reader = s.args, s.params, s.reader
    analyzer = s.audio.analyzer
    handle = window.handle
    quit_flag = {"v": False}
    automation = AutomationEngine()

    def status_line() -> str:
        on = "".join(str(int(v)) for v in params["fx_on"])
        return (f"intensite {params['master']:.1f} | sensibilite kick {params['sensitivity']:.2f} | "
                f"effets {on} ({'/'.join(FX_NAMES)})"
                + ("" if params["fx_link"] >= 0.5 else
                   f" | logo {''.join(str(int(v)) for v in params['fxl_on'])} (separes du fond)"))

    def act(name: str) -> None:
        """Actions communes aux touches et aux commandes de la GUI (executees dans ce fil)."""
        if name == "quit":
            quit_flag["v"] = True
        elif name == "fullscreen":
            window.toggle_fullscreen()
        elif name.startswith("fullscreen="):      # etat explicite (case "Plein ecran" de la GUI)
            if window.fullscreen != (name.endswith("1")):
                window.toggle_fullscreen()
        elif name == "hud":
            renderer.hud = not renderer.hud
        elif name == "bg":
            params["bg_mode"] = "live" if params["bg_mode"] == "pattern" else "pattern"
            print(f"fond: {params['bg_mode']}")
        elif name == "auto":
            params["auto_master"] = 0.0 if params["auto_master"] >= 0.5 else 1.0
            print(f"automations: {'actives' if params['auto_master'] >= 0.5 else 'figees'}")
        elif name == "reload":
            try:
                renderer.reload_shaders()
                # ne pas ecraser ce que la GUI vient de regler (ni l'etat des automations qu'elle edite)
                keep = {"sensitivity", "logo_path", "logo_video", "logo_key", "bg_mode", "logo_source", "_automation", "auto_master"}
                params.update({k: v for k, v in load_params().items() if k not in keep})
                print("Shaders et reglages recharges.")
                s.status["msg"] = "Shaders recharges"
            except ShaderError as exc:
                print(f"Erreur de shader (ancien programme conserve):\n{exc}", file=sys.stderr)
                s.status["msg"] = "Erreur de shader (voir la console), ancien programme conserve"
        elif name == "save":
            save_params(params)
            print(f"Reglages sauves dans {PARAMS_PATH}")
            s.status["msg"] = f"Reglages sauves ({PARAMS_PATH.name})"
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

    def on_key(win, key, scancode, action, mods):
        name = key_actions.get(key)
        if name and mods & glfw.MOD_SHIFT and name in ("fx0", "fx1", "fx2", "fx3"):
            name = "fxl" + name[2:]              # Maj + 1..4: meme effet sur la couche du logo
        if name and (action == glfw.PRESS or (action == glfw.REPEAT and name in repeatable)):
            act(name)

    glfw.set_key_callback(handle, on_key)
    print("Echap quitte | F plein ecran | H barres debug | B fond live/motif | T automations | 1-5 effets (Maj+1-4: "
          "logo) | L lier fond/logo | +/- intensite | haut/bas sensibilite kick | R recharge shaders | P sauve reglages")
    print(status_line())

    query = ctx.query(time=True) if args.stats else None
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

    while not glfw.window_should_close(handle) and not quit_flag["v"] and not s.stop_event.is_set():
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
        if reader.upload_to(renderer.video_tex):
            up_ms += (time.perf_counter() - u0) * 1000.0
            up_n += 1
        producer = s.manager.producer if s.manager else None
        if s.manager and s.manager.restarts != last_restarts:
            last_restarts, warned = s.manager.restarts, False
        if producer is not None and not warned and producer.poll() is not None:
            print(f"Le producteur ffmpeg s'est arrete (code {producer.returncode}): "
                  "derniere image conservee.", file=sys.stderr)
            s.status["live"] = f"ffmpeg arrete (code {producer.returncode}): derniere image conservee"
            warned = True

        state = analyzer.latest()
        size = window.framebuffer_size()
        if query is not None:
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
        if query is not None:
            gpu_ms += query.elapsed / 1e6
            gpu_n += 1

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
