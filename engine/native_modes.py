"""Modes Snap et Ridge d'audio2wave rendus en GL, au rythme de la fenetre (60 images/s) au lieu de Python pur (quelques images/s).

audio2wave dessine ces deux modes pixel par pixel en Python, puis envoie chaque image dans un tube. Ici l'ANALYSE reste celle d'audio2wave
(`pencil_heights`, `detect_kicks`, `render_ridge_line`, `resolve_gain`... appelees telles quelles : memes hauteurs, memes kicks), mais le DESSIN
est fait par des shaders : une photo (a chaque `args.interval`) devient quelques tableaux de hauteurs de colonnes envoyes en texture, et la
revelation colonne par colonne qui occupait tout l'intervalle n'est plus qu'un reglage anime (`u_drawn`).

- **Snap** : pencil (trait, bande de video interieure / exterieure, halo des kicks, halo temps reel), simple et rekordbox (les traces
  de `showwavespic`, filtres passe-bas du rekordbox refaits en numpy).
- **Ridge** : canevas persistant (ping-pong), decalage de `ridge_spacing` rangees a chaque ligne, ligne revelee colonne par colonne.
- Le son vient du tampon de l'analyse (`session.audio.ring`, mono) : pas de troisieme ouverture du micro par ffmpeg. `--stereo` et
  `--split-channels` n'existent donc pas ici.
- `NativeModeSource` joue le meme role que `py_modes.PyModeSource` (memes attributs pour les `build_gui` d'audio2wave) ; le fil GL
  l'appelle (`render`) pour obtenir la texture du fond. Les ressources GL se creent au premier appel, dans le fil GL, et se refont si le
  contexte change (projection fermee puis rouverte).
"""
from __future__ import annotations

import math
import random
import sys
import threading
import time
from pathlib import Path

import moderngl

from engine.deps import np
from engine.video import BgVideo

SHADER_DIR = Path(__file__).resolve().parent.parent / "gl_shaders"
STYLES = ("pencil", "simple", "rekordbox", "ridge")
RING_SECONDS = 24            # le tampon de l'analyse doit contenir au moins une photo (jusqu'a ~20 s : 1 temps a 3 BPM)


def available() -> bool:
    """Les modes GL ont besoin de numpy et de moderngl (sinon, repli sur les rendus Python d'audio2wave)."""
    return np is not None and moderngl is not None


# --- couleurs ---------------------------------------------------------------------------------------

def parse_color(module, spec: str) -> tuple[float, float, float]:
    """Une couleur d'audio2wave (`0xRRGGBB`, `#rrggbb`, nom ffmpeg) en trois flottants 0..1. Les formes hexadecimales sont lues
    directement ; un nom passe par PIL puis, a defaut, par ffmpeg (`probe_color`, un sous-processus : seulement quand la couleur change)."""
    text = str(spec).strip()
    try:
        if text.lower().startswith("0x"):
            text = "#" + text[2:]
        if text.startswith("#") and len(text) == 7:
            return tuple(int(text[i:i + 2], 16) / 255.0 for i in (1, 3, 5))
        from PIL import ImageColor
        return tuple(v / 255.0 for v in ImageColor.getrgb(text)[:3])
    except (ValueError, ImportError):
        raw = module.probe_color(str(spec))
        return tuple(b / 255.0 for b in raw[:3])


# --- analyse (fil de la source) ---------------------------------------------------------------------

def to_pcm(x) -> bytes:
    """Echantillons flottants (-1..1) -> PCM s16le mono, ce que les fonctions d'audio2wave attendent."""
    return (np.clip(np.asarray(x, dtype=np.float32), -1.0, 1.0) * 32767.0).astype("<i2").tobytes()


def bar_scale(a, scale: str):
    """Hauteur relative (0..1) d'une amplitude (0..1) selon `--scale` (mesure sur showwavespic : lin, log10(1 + 32767 a) / log10(32768),
    racine carree, racine cubique)."""
    a = np.clip(a, 0.0, 1.0)
    if scale == "log":
        return np.log10(1.0 + 32767.0 * a) / math.log10(32768.0)
    if scale == "sqrt":
        return np.sqrt(a)
    if scale == "cbrt":
        return np.cbrt(a)
    return a


def lowpass(x, rate: int, cutoff: float):
    """Passe-bas a 24 dB/octave (deux biquads de Butterworth en cascade chez ffmpeg) : module 1 / (1 + (f / fc)^4), sans phase (le
    trace n'utilise que l'enveloppe)."""
    spec = np.fft.rfft(x)
    f = np.fft.rfftfreq(len(x), 1.0 / rate)
    spec *= 1.0 / (1.0 + (f / float(cutoff)) ** 4)
    return np.fft.irfft(spec, len(x)).astype(np.float32)


def column_levels(x, columns: int, mode: str):
    """Niveau de chaque colonne du trace : crete (`peak`) ou moyenne de |x| (`average`, le defaut de showwavespic)."""
    n = len(x)
    columns = max(1, min(columns, n))
    edges = (np.arange(columns + 1, dtype=np.int64) * n // columns)
    mag = np.abs(x)
    if mode == "peak":
        return np.maximum.reduceat(mag, edges[:-1])
    return np.add.reduceat(mag, edges[:-1]) / np.maximum(np.diff(edges), 1)


def bars_photo(module, args, x, rate: int, size: tuple[int, int], gain_db: float) -> dict:
    """Photo des styles simple / rekordbox : une demi-hauteur de barre (px) par colonne et par trace."""
    width, height = size
    cols = module.resolve_columns(args, width)
    factor = 10.0 ** (gain_db / 20.0)
    rows = [x]
    if args.style == "rekordbox":
        low_hz, high_hz = module.resolve_crossover(args)
        rows = [x, lowpass(x, rate, high_hz), lowpass(x, rate, low_hz)]
    data = np.zeros((len(rows), cols), dtype=np.float32)
    for k, row in enumerate(rows):
        data[k] = bar_scale(column_levels(row * factor, cols, args.filter_mode), args.scale) * (height / 2.0)
    colors = [parse_color(module, c) for c in module.resolve_colors(args)]
    if args.style == "rekordbox":
        palette = [colors[2], colors[1], colors[0]]          # signal complet (aigus) dessous, graves au coeur
    else:
        palette = [colors[0]] * 3
    return {"style": args.style, "data": data, "cols": cols, "layers": len(rows), "palette": palette,
            "bg": parse_color(module, module.resolve_bg(args))}


def pencil_photo(module, args, pcm: bytes, rate: int, size: tuple[int, int], gain_db: float, glow_live: bool) -> dict:
    """Photo du style pencil : hauteurs de colonnes d'audio2wave (`pencil_heights`) + halo des kicks par colonne."""
    width, height = size
    cols = module.pencil_heights(args, pcm, gain_db, size)
    data = np.zeros((7, width), dtype=np.float32)
    ndraw = 1
    for x, (top, bottom, draw) in enumerate(cols):
        data[0, x], data[1, x] = top, bottom
        data[2, x] = draw[0]
        data[3, x] = draw[-1]
        ndraw = len(draw)
    if args.kick_glow and not glow_live:
        radius = max(1, int(args.kick_glow_size))
        xs = np.arange(width)
        glow = np.zeros(width, dtype=np.float64)
        for kx in module.detect_kicks(pcm, 1, rate, width):
            d = np.abs(xs - kx)
            glow = np.maximum(glow, np.where(d <= radius, 1.0 - d / radius, 0.0))
        data[4] = glow
        data[5] = np.round(glow * radius * module.KICK_GLOW_EXTRA_RATIO)       # np.round = arrondi pair, comme round()
        data[6] = np.round(glow * radius * module.KICK_GLOW_HALO_RATIO)
    return {"style": "pencil", "data": data, "ndraw": ndraw, "thick": max(1, int(args.line_width)),
            "ink": parse_color(module, module.resolve_colors(args)[0]), "bg": parse_color(module, module.resolve_bg(args)),
            "radius": float(args.kick_glow_size)}


def ridge_photo(module, args, pcm: bytes, size: tuple[int, int], gain_db: float, rng) -> dict:
    """Photo de Ridge : la hauteur du pic de chaque colonne (`render_ridge_line`), l'espacement des lignes et les couleurs."""
    heights = module.render_ridge_line(args, pcm, gain_db, size, rng)
    return {"style": "ridge", "data": np.asarray(heights, dtype=np.float32).reshape(1, -1), "thick": max(1, int(args.line_width)),
            "spacing": int(args.ridge_spacing), "ink": parse_color(module, args.colors or "white"),
            "bg": parse_color(module, args.bg_color or "black")}


# --- le GL (fil GL) ---------------------------------------------------------------------------------

class ModeGL:
    """Programmes, textures et FBO d'un contexte GL. Rend `out_tex` (RGB8, premiere rangee = haut de l'image, comme une frame video)."""

    def __init__(self, ctx, size: tuple[int, int], module) -> None:
        self.ctx, self.size, self.module = ctx, size, module
        vert = (SHADER_DIR / "quad.vert").read_text(encoding="utf-8")
        self.vbo = ctx.buffer(np.array([-1, -1, 1, -1, -1, 1, 1, 1], dtype="f4").tobytes())
        self.progs, self.vaos = {}, {}
        for name in ("pencil", "bars", "ridge"):
            prog = ctx.program(vertex_shader=vert, fragment_shader=(SHADER_DIR / f"mode_{name}.frag").read_text(encoding="utf-8"))
            self.progs[name] = prog
            self.vaos[name] = ctx.vertex_array(prog, [(self.vbo, "2f", "in_pos")])
        self.out_tex = ctx.texture(size, 3)
        self.out_tex.filter = (moderngl.LINEAR, moderngl.LINEAR)           # comme la texture video du rendu
        self.out_fbo = ctx.framebuffer(color_attachments=[self.out_tex])
        self.zero_tex = ctx.texture((size[0], 3), 1, data=bytes(4 * size[0] * 3), dtype="f4")      # « photo precedente » vide (aucune barre)
        self.zero_tex.filter = (moderngl.NEAREST, moderngl.NEAREST)
        self.cur = self.prev = None                    # (photo, texture)
        self.vid_tex = [self._rgb_tex(), self._rgb_tex()]
        self.videos: list = [None, None]
        self.video_keys: list = [None, None]
        # Ridge : canevas persistant (deux textures, on lit l'une pendant qu'on ecrit l'autre)
        self.canvas = [self._rgb_tex(linear=True) for _ in range(2)]
        self.canvas_fbo = [ctx.framebuffer(color_attachments=[t]) for t in self.canvas]
        self.canvas_i = 0
        self.ridge_drawn = 0
        self.ridge_ready = False
        self.heights_tex = None

    def _rgb_tex(self, linear: bool = False):
        tex = self.ctx.texture(self.size, 3, data=bytes(self.size[0] * self.size[1] * 3))
        f = moderngl.LINEAR if linear else moderngl.NEAREST
        tex.filter = (f, f)
        return tex

    def _data_tex(self, data):
        rows, cols = data.shape
        tex = self.ctx.texture((cols, rows), 1, data=np.ascontiguousarray(data, dtype="f4").tobytes(), dtype="f4")
        tex.filter = (moderngl.NEAREST, moderngl.NEAREST)
        return tex

    @staticmethod
    def _set(prog, name, value) -> None:
        if name in prog:
            prog[name].value = value

    # -- installation d'une nouvelle photo --------------------------------------------------------
    def install(self, photo: dict) -> None:
        style = photo["style"]
        if style == "ridge":
            self._install_ridge(photo)
            return
        if self.prev is not None:
            self.prev[1].release()
        if self.cur is not None and self.cur[0]["style"] == style:
            self.prev = self.cur
        else:
            if self.cur is not None:
                self.cur[1].release()
            self.prev = None
        self.cur = (photo, self._data_tex(photo["data"]))
        self.ridge_ready = False

    def _install_ridge(self, photo: dict) -> None:
        bg = photo["bg"]
        if not self.ridge_ready:
            for fbo in self.canvas_fbo:
                fbo.clear(*bg, 1.0)
            self.ridge_ready = True
        else:                                         # shift_canvas : le contenu monte de `spacing` rangees
            prog = self.progs["ridge"]
            self._set(prog, "u_pass", 0)
            self._set(prog, "u_size", self.size)
            self._set(prog, "u_spacing", photo["spacing"])
            self._set(prog, "u_bg", bg)
            self.canvas[self.canvas_i].use(0)
            self._set(prog, "u_src", 0)
            dst = 1 - self.canvas_i
            self.canvas_fbo[dst].use()
            self.vaos["ridge"].render(moderngl.TRIANGLE_STRIP)
            self.canvas_i = dst
        if self.heights_tex is not None:
            self.heights_tex.release()
        self.heights_tex = self._data_tex(photo["data"])
        self.ridge_drawn = 0
        self.cur = (photo, self.heights_tex)
        self.prev = None

    # -- une image --------------------------------------------------------------------------------
    def update_videos(self, paths: tuple) -> None:
        for i, path in enumerate(paths):
            if path == self.video_keys[i]:
                continue
            if self.videos[i] is not None:
                self.videos[i].stop()
                self.videos[i] = None
            self.video_keys[i] = path
            if path:
                try:
                    self.videos[i] = BgVideo(str(path), self.size, "cover")
                except OSError as exc:
                    print(f"Video du mode illisible ({path}): {exc}", file=sys.stderr)
        for i in range(2):
            if self.videos[i] is not None:
                self.videos[i].reader.upload_to(self.vid_tex[i])

    def draw(self, drawn_frac: float, live_glow: float | None, args) -> "moderngl.Texture | None":
        """Dessine l'image courante dans `out_tex` et la rend ; None s'il n'y a pas encore de photo."""
        if self.cur is None:
            return None
        photo, tex = self.cur
        width, height = self.size
        drawn = width if drawn_frac >= 1.0 else max(0, int(width * drawn_frac))
        style = photo["style"]
        if style == "ridge":
            prog = self.progs["ridge"]
            if drawn > self.ridge_drawn:
                self._set(prog, "u_pass", 1)
                self._set(prog, "u_size", self.size)
                self._set(prog, "u_thick", photo["thick"])
                self._set(prog, "u_x0", self.ridge_drawn)
                self._set(prog, "u_x1", drawn)
                self._set(prog, "u_bg", photo["bg"])
                self._set(prog, "u_ink", photo["ink"])
                tex.use(1)
                self._set(prog, "u_heights", 1)
                self.canvas_fbo[self.canvas_i].use()
                self.vaos["ridge"].render(moderngl.TRIANGLE_STRIP)
                self.ridge_drawn = drawn
            return self.canvas[self.canvas_i]
        self.out_fbo.use()
        self.ctx.viewport = (0, 0, width, height)
        prev_photo, prev_tex = self.prev if self.prev is not None else (None, None)
        if style == "pencil":
            prog = self.progs["pencil"]
            has_vid, has_out = self.videos[0] is not None, self.videos[1] is not None
            blank = prev_tex is None
            self._set(prog, "u_size", self.size)
            self._set(prog, "u_drawn", drawn)
            self._set(prog, "u_ndraw", photo["ndraw"])
            self._set(prog, "u_thick", photo["thick"])
            self._set(prog, "u_bg", photo["bg"])
            self._set(prog, "u_ink", photo["ink"])
            self._set(prog, "u_glow_col", (1.0, 1.0, 1.0))
            if live_glow is not None:                         # halo temps reel : memes arrondis qu'audio2wave (round pair, en double)
                self._set(prog, "u_live_extra", int(round(live_glow * photo["radius"] * self.module.KICK_GLOW_EXTRA_RATIO)))
                self._set(prog, "u_live_halo", int(round(live_glow * photo["radius"] * self.module.KICK_GLOW_HALO_RATIO)))
            self._set(prog, "u_live", -1.0 if live_glow is None else float(live_glow))
            self._set(prog, "u_has_vid", int(has_vid))
            self._set(prog, "u_has_out", int(has_out))
            # la photo precedente garde son trait, sauf quand la video ou le halo temps reel rendaient le balayage continu
            keep_ink = not (has_vid or has_out or live_glow is not None) and not blank
            self._set(prog, "u_prev_ink", int(keep_ink))
            tex.use(0)
            (prev_tex or tex).use(1)
            self.vid_tex[0].use(2)
            self.vid_tex[1].use(3)
            for name, unit in (("u_cur", 0), ("u_prev", 1), ("u_vid", 2), ("u_vid_out", 3)):
                self._set(prog, name, unit)
            self.vaos["pencil"].render(moderngl.TRIANGLE_STRIP)
        else:
            prog = self.progs["bars"]
            self._set(prog, "u_size", self.size)
            self._set(prog, "u_cols", photo["cols"])
            self._set(prog, "u_layers", photo["layers"])
            self._set(prog, "u_drawn", drawn)
            self._set(prog, "u_bg", photo["bg"])
            for name, color in zip(("u_c0", "u_c1", "u_c2"), photo["palette"]):
                self._set(prog, name, color)
            self._set(prog, "u_cols_prev", prev_photo["cols"] if prev_photo is not None else photo["cols"])
            tex.use(0)
            (prev_tex or self.zero_tex).use(1)
            self._set(prog, "u_cur", 0)
            self._set(prog, "u_prev", 1)
            self.vaos["bars"].render(moderngl.TRIANGLE_STRIP)
        return self.out_tex

    def release(self) -> None:
        for v in self.videos:
            if v is not None:
                v.stop()
        self.videos = [None, None]
        for obj in (self.out_fbo, self.out_tex, self.zero_tex, self.vbo, *self.vid_tex, *self.canvas_fbo, *self.canvas):
            try:
                obj.release()
            except Exception:
                pass
        for obj in (self.cur, self.prev):
            if obj is not None:
                try:
                    obj[1].release()
                except Exception:
                    pass


# --- la source (fil d'analyse + pont vers le fil GL) ------------------------------------------------

class NullReader:
    """Ce que la boucle GL attend d'un `s.reader` quand le mode est dessine en GL : jamais d'image a envoyer."""

    frames_read = 0
    ended = False

    def upload_to(self, texture) -> bool:
        return False

    def stop(self) -> None:
        pass


class RingCapture:
    """Ce que `build_gui` d'audio2wave attend de `capture_state["capture"]` (bouton « Mesurer » de Snap) : `latest()` = les `interval`
    dernieres secondes en PCM s16le mono ; `recent_level()` = niveau RMS d'une courte queue."""

    ended = False

    def __init__(self, audio, args) -> None:
        self.audio, self.args = audio, args

    def latest(self) -> bytes | None:
        n = int(self.audio.rate * self.args.interval)
        if self.audio.ring.total < n:
            return None
        return to_pcm(self.audio.ring.latest(n))

    def recent_level(self, tail_bytes: int) -> float | None:
        n = max(1, tail_bytes // 2)
        if self.audio.ring.total < n:
            return None
        x = self.audio.ring.latest(n)
        return float(np.sqrt(np.mean(np.square(x))))

    def set_window(self, window_bytes: int) -> None:
        pass


class NativeModeSource:
    """Un mode Snap ou Ridge dont le dessin est fait en GL. Memes attributs que `py_modes.PyModeSource` pour la GUI (`args`, `status`,
    `stop_event`, `finished_event`, `capture_state`, `module`, `thread`) ; `render(ctx, now)` rend la texture du fond."""

    native = True
    reader = NullReader()

    def __init__(self, name: str, module, args, size: tuple[int, int], audio, status: dict | None = None) -> None:
        self.name, self.module, self.args, self.size, self.audio = name, module, args, size, audio
        args.stereo = False
        args.split_channels = False
        self.status = status if status is not None else {}
        self.stop_event = threading.Event()
        self.finished_event = threading.Event()
        self.capture_state = {"capture": RingCapture(audio, args)}
        self.thread: threading.Thread | None = None
        self._lock = threading.Lock()
        self._pending: dict | None = None
        self._gl: ModeGL | None = None
        self._gl_ctx = None
        self._photo: dict | None = None
        self.photos = 0
        self._glow_meter = None
        self._gain = 0.0
        self._rng = random.Random()

    # -- fil d'analyse -------------------------------------------------------------------------
    def start(self) -> None:
        self.thread = threading.Thread(target=self._run, name=f"mode-{self.name}", daemon=True)
        self.thread.start()

    def _run(self) -> None:
        mod, args = self.module, self.args
        gain_tracker = mod.RidgeGain(args.gain_window) if self.name == "ridge" else None
        last_device = args.device
        next_at = time.monotonic() + args.interval
        try:
            while not self.stop_event.is_set():
                delay = next_at - time.monotonic()
                if delay > 0 and self.stop_event.wait(delay):
                    break
                while next_at <= time.monotonic():
                    next_at += args.interval
                if args.device != last_device:
                    last_device = args.device
                    self._follow_device(args.device)
                rate = int(self.audio.rate)
                n = int(rate * args.interval)
                if n < 16 or self.audio.ring.total < n:
                    continue                                   # fenetre pas encore pleine
                try:
                    photo = self._make_photo(self.audio.ring.latest(n), rate, gain_tracker)
                except (SystemExit, Exception) as exc:        # un reglage temporairement invalide saute la photo
                    self.status["text"] = f"reglage invalide, photo ignoree: {str(exc) or type(exc).__name__}"
                    continue
                photo["t0"], photo["t1"] = time.monotonic(), next_at
                with self._lock:
                    self._pending = photo
                self.photos += 1
        finally:
            self.finished_event.set()

    def _follow_device(self, name) -> None:
        """Le menu « Entree audio » du panneau change `args.device` (un nom DirectShow) : l'analyse suit, sur l'entree sounddevice du meme
        nom (comparaison par prefixe : DirectShow tronque les noms a 31 caracteres)."""
        choices = getattr(type(self.audio), "input_choices", None)
        if not name or self.audio.synthetic or choices is None:
            return
        wanted = str(name).lower()
        for index, label in choices():
            tail = label.split("] ", 1)[-1].lower()
            if tail.startswith(wanted[:31]) or wanted.startswith(tail[:31]):
                try:
                    self.status["text"] = f"entree audio: {self.audio.switch(index)}"
                except Exception as exc:
                    self.status["text"] = f"entree audio refusee: {exc}"
                return
        self.status["text"] = f"entree audio introuvable pour l'analyse: {name}"

    def _make_photo(self, x, rate: int, gain_tracker) -> dict:
        mod, args = self.module, self.args
        pcm = to_pcm(x)
        if self.name == "ridge":
            gain, peak = gain_tracker.resolve(args, pcm)
            photo = ridge_photo(mod, args, pcm, self.size, gain, self._rng)
            text = f"ligne {self.photos + 1}"
        else:
            gain, peak = mod.resolve_gain(args, pcm)
            glow_live = bool(args.kick_glow and args.glow_live)
            if args.style == "pencil":
                photo = pencil_photo(mod, args, pcm, rate, self.size, gain, glow_live)
            else:
                photo = bars_photo(mod, args, x, rate, self.size, gain)
            text = f"photo {self.photos + 1}"
        self._gain = gain
        level = "silence" if peak is None else f"crete {peak:5.1f} dBFS -> gain {gain:+5.1f} dB"
        self.status["text"] = f"[{time.strftime('%H:%M:%S')}] {text} {level}"
        return photo

    # -- fil GL --------------------------------------------------------------------------------
    def _live_glow(self) -> float | None:
        args, mod = self.args, self.module
        if self.name != "snap" or args.style != "pencil" or not (args.kick_glow and args.glow_live):
            return None
        if self._glow_meter is None:
            self._glow_meter = mod.LiveGlowMeter()
        n = max(1, int(self.audio.rate * mod.GLOW_LIVE_WINDOW_MS / 1000.0))
        if self.audio.ring.total < n:
            return None
        level = float(np.sqrt(np.mean(np.square(self.audio.ring.latest(n)))))
        return self._glow_meter.push(min(1.0, level * 10.0 ** (self._gain / 20.0)))

    def render(self, ctx, now: float):
        """Texture du fond (RGB8, premiere rangee = haut), ou None tant qu'il n'y a pas de photo. A appeler dans le fil GL."""
        if self._gl is None or self._gl_ctx is not ctx:
            if self._gl is not None:
                self._gl.release()
            self._gl, self._gl_ctx = ModeGL(ctx, self.size, self.module), ctx
            self._photo = None
        with self._lock:
            pending, self._pending = self._pending, None
        if pending is not None:
            self._gl.install(pending)
            self._photo = pending
        if self._photo is None:
            return None
        photo = self._photo
        if self.name == "snap" and photo["style"] == "pencil":
            self._gl.update_videos((getattr(self.args, "video", None), getattr(self.args, "video2", None)))
        span = max(photo["t1"] - photo["t0"], 1e-3)
        return self._gl.draw((now - photo["t0"]) / span, self._live_glow(), self.args)

    # -- arret ---------------------------------------------------------------------------------
    def stop(self) -> None:
        self.stop_event.set()

    def join(self, timeout: float | None = None) -> bool:
        if self.thread is not None:
            self.thread.join(timeout)
            return not self.thread.is_alive()
        return True

    def release_gl(self) -> None:
        """A appeler dans le fil GL a la fermeture (les ressources GL ne se liberent pas depuis un autre fil)."""
        if self._gl is not None:
            self._gl.release()
            self._gl = None
