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
import ctypes
import json
import math
import os
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


def require_deps() -> None:
    if MISSING:
        print(f"Dependances manquantes: {', '.join(MISSING)}.\n"
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
    "master": 1.0,
    "sensitivity": 1.0,
}

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
        rate = self.extractor.rate
        while not self._stop.is_set():
            if not self.ring.event.wait(0.1):
                continue
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

    def __init__(self, stream, frame_size: int, on_frame=None):
        self.stream = stream
        self.frame_size = frame_size
        self.on_frame = on_frame          # crochet de test, appele avec la frame complete
        self._front = bytearray(frame_size)
        self._back = bytearray(frame_size)
        self._lock = threading.Lock()
        self._seq = 0
        self._uploaded = 0
        self.frames_read = 0
        self.ended = False
        self._thread = threading.Thread(target=self._run, daemon=True)

    def start(self):
        self._thread.start()

    def join(self, timeout: float | None = None):
        self._thread.join(timeout)

    def _run(self):
        size = self.frame_size
        use_readinto = hasattr(self.stream, "readinto")
        while True:
            mv = memoryview(self._back)
            got = 0
            while got < size:
                try:
                    if use_readinto:
                        n = self.stream.readinto(mv[got:])
                    else:
                        chunk = self.stream.read(min(size - got, self.CHUNK))
                        n = len(chunk)
                        mv[got:got + n] = chunk
                except (ValueError, OSError):
                    n = 0
                if not n:
                    self.ended = True
                    return
                got += n
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
    img = PIL_Image.open(path).convert("RGBA")
    if max(img.size) > 2048:
        ratio = 2048 / max(img.size)
        img = img.resize((max(1, round(img.width * ratio)), max(1, round(img.height * ratio))),
                         PIL_Image.LANCZOS)
    arr = np.asarray(img, dtype=np.float32)
    arr[:, :, :3] *= arr[:, :, 3:4] / 255.0
    return np.rint(arr).astype(np.uint8)


def logo_layout(width: int, height: int, aspect: float, scale: float, pos: tuple[float, float],
                pulse: float = 0.0, jitter: tuple[float, float] = (0.0, 0.0)):
    """(cx, cy, demi-largeur, demi-hauteur) en uv (origine haut gauche). Le logo tient dans
    un carre de cote scale*hauteur, ratio conserve ; pulse (0.12 = +12 %) et jitter
    ne le font jamais sortir du cadre."""
    box = min(scale * height, 0.95 * min(width, height))
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

    def __init__(self, ctx, video_size: tuple[int, int], logo_rgba=None,
                 logo_scale: float = 0.35, logo_pos: tuple[float, float] = (0.5, 0.5),
                 glow_color: tuple[float, float, float] = (0.37, 0.83, 0.78),
                 shader_dir: Path = SHADER_DIR):
        self.ctx = ctx
        self.shader_dir = shader_dir
        self.video_size = video_size
        self.logo_scale = logo_scale
        self.logo_pos = logo_pos
        self.glow_color = glow_color
        self.hud = False
        self.rng = np.random.default_rng(3)

        self.video_tex = ctx.texture(video_size, 3, data=bytes(video_size[0] * video_size[1] * 3))
        self.video_tex.filter = (moderngl.LINEAR, moderngl.LINEAR)
        self.video_tex.repeat_x = self.video_tex.repeat_y = False

        self.logo_tex = None
        self.logo_aspect = 1.0
        if logo_rgba is not None:
            h, w = logo_rgba.shape[:2]
            self.logo_aspect = w / h
            self.logo_tex = ctx.texture((w, h), 4, data=logo_rgba.tobytes())
            self.logo_tex.build_mipmaps()
            self.logo_tex.filter = (moderngl.LINEAR_MIPMAP_LINEAR, moderngl.LINEAR)
            self.logo_tex.anisotropy = 8.0
            self.logo_tex.repeat_x = self.logo_tex.repeat_y = False
        else:
            self.logo_tex = ctx.texture((1, 1), 4, data=bytes(4))

        self.vbo = ctx.buffer(np.array([-1, -1, 1, -1, -1, 1, 1, 1], dtype="f4").tobytes())
        self.scene_prog = self.post_prog = self.scene_vao = self.post_vao = None
        self.scene_tex = self.scene_fbo = None
        self.reload_shaders()

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
        self.scene_tex = self.ctx.texture(size, 4)
        self.scene_tex.filter = (moderngl.LINEAR, moderngl.LINEAR)
        self.scene_tex.repeat_x = self.scene_tex.repeat_y = False
        self.scene_fbo = self.ctx.framebuffer(color_attachments=[self.scene_tex])

    @staticmethod
    def _set(prog, name: str, value) -> None:
        if name in prog:   # un uniform inutilise est elimine par le compilateur
            prog[name].value = value

    def draw(self, target, size: tuple[int, int], state: dict, params: dict, t: float) -> None:
        width, height = size
        fx_on, fx_int, master = params["fx_on"], params["fx_int"], params["master"]
        eff = [float(fx_on[i]) * float(fx_int[i]) * master for i in range(5)]
        bass, high, beat = state["bass"], state["high"], state["beat"]

        self._ensure_scene(size)

        # Passe 1 : video + logo (pulse, jitter, contour lumineux).
        logo_fx = eff[4]
        pulse = 0.12 * beat * logo_fx
        jit = 0.004 * high * logo_fx
        jitter = (float(self.rng.uniform(-1, 1)) * jit, float(self.rng.uniform(-1, 1)) * jit)
        rect = logo_layout(width, height, self.logo_aspect, self.logo_scale, self.logo_pos, pulse, jitter)
        sp = self.scene_prog
        self._set(sp, "u_video", 0)
        self._set(sp, "u_logo", 1)
        self._set(sp, "u_res", (float(width), float(height)))
        self._set(sp, "u_logo_rect", rect)
        self._set(sp, "u_logo_on", 1.0 if self.logo_aspect and self.logo_tex.size != (1, 1) else 0.0)
        self._set(sp, "u_bass", float(bass))
        self._set(sp, "u_glow", min(logo_fx, 2.0))
        self._set(sp, "u_glow_color", self.glow_color)
        self.video_tex.use(0)
        self.logo_tex.use(1)
        self.scene_fbo.viewport = (0, 0, width, height)
        self.scene_fbo.use()
        self.scene_vao.render(moderngl.TRIANGLE_STRIP)

        # Passe 2 : post-traitement de l'image entiere vers la cible.
        pp = self.post_prog
        self._set(pp, "u_scene", 0)
        self._set(pp, "u_res", (float(width), float(height)))
        self._set(pp, "u_time", float(t))
        for name in ("bass", "mid", "high", "rms"):
            self._set(pp, f"u_{name}", float(state[name]))
        self._set(pp, "u_beat", float(beat))
        self._set(pp, "u_since_beat", float(state["since_beat"]))
        for i, name in enumerate(FX_NAMES[:4]):
            self._set(pp, f"u_fx_{name}", eff[i])
        self._set(pp, "u_hud", 1.0 if self.hud else 0.0)
        self._set(pp, "u_fx_state", tuple(float(v) for v in fx_on))
        self.scene_tex.use(0)
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
            elif isinstance(value, (int, float)):
                params[key] = float(value)
    except FileNotFoundError:
        pass
    except (OSError, ValueError) as exc:
        print(f"Reglages ignores ({PARAMS_PATH}): {exc}", file=sys.stderr)
    return params


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


def build_live_args(live, device: str | None, size: tuple[int, int], fps: int, extra: str | None):
    argv = ["audio2wave_live.py", "--size", f"{size[0]}x{size[1]}", "--fps", str(fps)]
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


def spawn_producer(live, live_args) -> subprocess.Popen:
    """Lance le producteur ffmpeg de audio2wave_live (meme commande), stdout relie a un
    pipe a GROS tampon. Sous Windows un pipe anonyme fait 4 Ko : le lecteur devait alors
    reprendre le GIL ~700 fois par frame 720p, et des que la boucle de rendu tournait vite
    il ne suivait plus (mesure: 30 -> 7 frames/s). Avec 16 Mo, une frame se lit en un
    ou deux appels. Repli sur live.spawn_producer() hors Windows ou en cas d'echec."""
    if os.name == "nt":
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
                proc = subprocess.Popen(live.producer_command(live_args), stdout=write_fd)
            finally:
                os.close(write_fd)          # le parent ne garde pas l'extremite d'ecriture
            proc.stdout = open(read_fd, "rb", buffering=0)
            return proc
        except Exception as exc:
            print(f"Pipe a gros tampon indisponible ({exc}), repli sur le pipe standard.", file=sys.stderr)
    return live.spawn_producer(live_args)


def stop_process(proc: subprocess.Popen | None) -> None:
    if proc is None or proc.poll() is not None:
        return
    proc.terminate()
    try:
        proc.wait(timeout=3)
    except subprocess.TimeoutExpired:
        proc.kill()
        proc.wait()


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


class Window:
    def __init__(self, monitor, windowed_size: tuple[int, int], fullscreen: bool):
        self.monitor = monitor
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
            glfw.set_input_mode(self.handle, glfw.CURSOR, glfw.CURSOR_HIDDEN)
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
    p.add_argument("--logo", default=str(DEFAULT_LOGO),
                   help="PNG RGBA a incruster, ou 'none' (defaut: asset/Casual Ravers - Kit_Sigle - Blanc.png)")
    p.add_argument("--logo-scale", type=float, default=0.35,
                   help="Cote du carre englobant le logo, en fraction de la hauteur (defaut: 0.35)")
    p.add_argument("--logo-pos", default="0.5,0.5",
                   help="Centre du logo x,y en fraction, origine en haut a gauche (defaut: 0.5,0.5)")
    p.add_argument("--size", default=None,
                   help="Taille de la fenetre WxH en mode --no-fullscreen (defaut: 1280x720)")
    p.add_argument("--render-size", default="1280x720",
                   help="Resolution de rendu ffmpeg, independante de la fenetre (defaut: 1280x720)")
    p.add_argument("--fps", type=int, default=30, help="Images par seconde du producteur ffmpeg (defaut: 30)")
    p.add_argument("--fullscreen", action=argparse.BooleanOptionalAction, default=True,
                   help="Fenetre borderless sur le moniteur cible (defaut: active, --no-fullscreen pour une fenetre)")
    p.add_argument("--monitor", type=int, default=None,
                   help="Index glfw du moniteur cible (defaut: premier moniteur non principal)")
    p.add_argument("--sensitivity", type=float, default=None,
                   help="Sensibilite du kick (defaut: valeur sauvee, sinon 1.0; plus haut = plus sensible)")
    p.add_argument("--fps-cap", type=float, default=None,
                   help="Cadence maximale du rendu (defaut: frequence du moniteur cible +2 %%, 0 = sans limite)")
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

    if args.list_audio_devices:
        require_deps()
        print_audio_devices()
        return

    render_w, render_h = (int(v) for v in parse_pair(args.render_size, "--render-size", "x"))
    live = None
    if not args.synthetic or args.list_devices:
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
    if not args.synthetic:
        if not args.device:
            print("Indique une entree avec -d/--device (ou --list-devices, ou --synthetic).", file=sys.stderr)
            sys.exit(2)
        live_args = build_live_args(live, args.device, (render_w, render_h), args.fps, args.live_args)
        if live.resolve_size(live_args) != (render_w, render_h):
            print(f"--live-args ne doit pas changer --size (rendu {render_w}x{render_h}).", file=sys.stderr)
            sys.exit(2)

    if args.dry_run:
        if live_args is None:
            print("Mode --synthetic: aucune commande ffmpeg, flux video/audio generes en Python.")
        else:
            print(format_command(live.producer_command(live_args)))
        return

    require_deps()
    if live_args is not None and shutil.which("ffmpeg") is None:
        print("ffmpeg introuvable dans le PATH (winget install --id Gyan.FFmpeg).", file=sys.stderr)
        sys.exit(1)
    run_app(args, live, live_args, (render_w, render_h))


def run_app(args, live, live_args, render_size: tuple[int, int]) -> None:
    params = load_params()
    if args.sensitivity is not None:
        params["sensitivity"] = args.sensitivity
    logo_pos = parse_pair(args.logo_pos, "--logo-pos", ",")
    logo = None
    if args.logo.lower() != "none":
        if not Path(args.logo).is_file():
            print(f"Logo introuvable: {args.logo} (--logo none pour s'en passer)", file=sys.stderr)
            sys.exit(2)
        logo = load_logo(Path(args.logo))
    win_w, win_h = (int(v) for v in parse_pair(args.size or "1280x720", "--size", "x"))

    # Audio: ring + capture + analyse.
    ring = RingBuffer()
    audio_src = None
    if args.synthetic:
        rate = 48000
        audio_src = SyntheticAudio(ring, rate)
        print("Audio synthetique: kick 60 Hz a 120 BPM.")
    else:
        device = find_input_device(args.audio_device, args.device)
        audio_src = SoundDeviceInput(device, ring)
        rate = audio_src.rate
        print(f"Analyse audio sur '{audio_src.name}' ({rate} Hz, latence capture "
              f"~{audio_src.latency_ms:.0f} ms).")
    extractor = FeatureExtractor(rate, sensitivity=params["sensitivity"])
    analyzer = AudioAnalyzer(ring, extractor)

    # Video: producteur ffmpeg (ou flux synthetique) -> lecteur de frames.
    producer = None
    frame_size = render_size[0] * render_size[1] * 3
    if args.synthetic:
        stream = SyntheticVideoStream(render_size[0], render_size[1], args.fps)
    else:
        producer = spawn_producer(live, live_args)
        stream = producer.stdout
    reader = FrameReader(stream, frame_size)

    if not glfw.init():
        print("Initialisation de glfw impossible.", file=sys.stderr)
        sys.exit(1)
    window = None
    try:
        monitor = pick_monitor(args.monitor)
        window = Window(monitor, (win_w, win_h), args.fullscreen)
        ctx = moderngl.create_context()
        mode = glfw.get_video_mode(monitor)
        wx, wy = glfw.get_window_pos(window.handle)
        ww, wh = glfw.get_window_size(window.handle)
        print(f"OpenGL: {ctx.info['GL_RENDERER']} | moniteur en ({window.mx},{window.my}) "
              f"{window.mw}x{window.mh} @ {mode.refresh_rate} Hz | fenetre en ({wx},{wy}) {ww}x{wh} | "
              f"rendu ffmpeg {render_size[0]}x{render_size[1]}")
        renderer = Renderer(ctx, render_size, logo, args.logo_scale, logo_pos)
        renderer.hud = args.hud

        analyzer.start()
        audio_src.start()
        reader.start()
        loop(args, window, ctx, renderer, reader, analyzer, extractor, params, producer)
    finally:
        analyzer.stop()
        if audio_src is not None:
            try:
                audio_src.stop()
            except Exception:
                pass
        stop_process(producer)
        if window is not None:
            glfw.destroy_window(window.handle)
        glfw.terminate()


def loop(args, window: Window, ctx, renderer: Renderer, reader: FrameReader, analyzer: AudioAnalyzer,
         extractor: FeatureExtractor, params: dict, producer) -> None:
    handle = window.handle
    quit_flag = {"v": False}

    def status() -> str:
        on = "".join(str(int(v)) for v in params["fx_on"])
        return (f"intensite {params['master']:.1f} | sensibilite kick {params['sensitivity']:.2f} | "
                f"effets {on} ({'/'.join(FX_NAMES)})")

    def on_key(win, key, scancode, action, mods):
        if action not in (glfw.PRESS, glfw.REPEAT):
            return
        press = action == glfw.PRESS
        digits = {glfw.KEY_1: 0, glfw.KEY_2: 1, glfw.KEY_3: 2, glfw.KEY_4: 3, glfw.KEY_5: 4,
                  glfw.KEY_KP_1: 0, glfw.KEY_KP_2: 1, glfw.KEY_KP_3: 2, glfw.KEY_KP_4: 3, glfw.KEY_KP_5: 4}
        if key == glfw.KEY_ESCAPE and press:
            quit_flag["v"] = True
        elif key == glfw.KEY_F and press:
            window.toggle_fullscreen()
        elif key == glfw.KEY_H and press:
            renderer.hud = not renderer.hud
        elif key == glfw.KEY_R and press:
            try:
                renderer.reload_shaders()
                params.update({k: v for k, v in load_params().items() if k != "sensitivity"})
                print("Shaders et reglages recharges.")
            except ShaderError as exc:
                print(f"Erreur de shader (ancien programme conserve):\n{exc}", file=sys.stderr)
        elif key == glfw.KEY_P and press:
            save_params(params)
            print(f"Reglages sauves dans {PARAMS_PATH}")
        elif key in digits and press:
            i = digits[key]
            params["fx_on"][i] = 0 if params["fx_on"][i] else 1
            print(f"effet {FX_NAMES[i]}: {'on' if params['fx_on'][i] else 'off'}")
        elif key in (glfw.KEY_EQUAL, glfw.KEY_KP_ADD, glfw.KEY_PAGE_UP):
            params["master"] = min(params["master"] + 0.1, 2.0)
            print(status())
        elif key in (glfw.KEY_MINUS, glfw.KEY_KP_SUBTRACT, glfw.KEY_PAGE_DOWN):
            params["master"] = max(params["master"] - 0.1, 0.0)
            print(status())
        elif key == glfw.KEY_UP:
            params["sensitivity"] = min(params["sensitivity"] * 1.15, 4.0)
            extractor.sensitivity = params["sensitivity"]
            print(status())
        elif key == glfw.KEY_DOWN:
            params["sensitivity"] = max(params["sensitivity"] / 1.15, 0.25)
            extractor.sensitivity = params["sensitivity"]
            print(status())

    glfw.set_key_callback(handle, on_key)
    print("Echap quitte | F plein ecran | H barres debug | 1-5 effets | +/- intensite | "
          "haut/bas sensibilite kick | R recharge shaders | P sauve reglages")
    print(status())

    query = ctx.query(time=True) if args.stats else None
    t0 = time.monotonic()
    last_title = t0
    frames = 0
    win_frames = 0
    up_ms = gpu_ms = 0.0
    up_n = gpu_n = 0
    last_stats = t0
    warned = False
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

    while not glfw.window_should_close(handle) and not quit_flag["v"]:
        glfw.poll_events()
        now = time.monotonic()
        max_gap = max(max_gap, now - prev_frame_t)
        prev_frame_t = now
        if args.max_seconds is not None and now - t0 >= args.max_seconds:
            break

        u0 = time.perf_counter()
        if reader.upload_to(renderer.video_tex):
            up_ms += (time.perf_counter() - u0) * 1000.0
            up_n += 1
        if producer is not None and not warned and producer.poll() is not None:
            print(f"Le producteur ffmpeg s'est arrete (code {producer.returncode}): "
                  "derniere image conservee.", file=sys.stderr)
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
