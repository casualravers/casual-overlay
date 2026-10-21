"""Mode Sphere : le spectre du son projete sur une sphere, dessine en GL (module d'options et fenetre de reglages : `audio2wave_sphere`).

Les frequences partent du CENTRE de la sphere vue de face (graves au milieu, aigus vers le bord, la face arriere repete la face avant) et
deforment sa surface vers l'exterieur ; l'angle autour du centre decale l'historique (un bout du passe par direction : un relief en spirale).
La **taille de la sphere suit l'intensite du morceau** (niveau lisse rapporte au plus fort niveau recent) et pulse sur les graves.

- `SpectrumTracker` (numpy, sans GL) : tampon de l'analyse -> bandes 0..1 (FFT, echelle logarithmique, compensation de la pente du spectre,
  dynamique relative au plus fort niveau recent), historique, intensite et pulsation. Testable sans fenetre.
- `SphereGL` : maillage (anneaux de latitude x meridiens), un programme (`mode_sphere.vert` / `.frag`) et trois styles (fil de fer, surface
  eclairee, nuage de points). Le sommet lit l'historique dans une texture R32F ; camera fixe (inclinaison et rotation facultatives).
- `SphereSource` : meme interface que `NativeModeSource` (la boucle GL et la GUI ne voient pas la difference) ; l'analyse se fait dans le
  fil GL (une FFT de 2048 points par image : negligeable), donc aucun fil d'analyse.
"""
from __future__ import annotations

import math
import sys
import threading

import moderngl

from engine.deps import np
from engine.native_modes import SHADER_DIR, NativeModeSource, parse_color

HIST_ROWS = 128            # lignes de l'historique (une toutes les 1/60 s : 2 s au plus)
HIST_RATE = 60.0
MAX_BANDS = 64
WIN = 2048
F_LOW, F_HIGH = 40.0, 12000.0
TILT_DB_PER_OCT = 4.5      # un morceau perd ~4,5 dB par octave : sans compensation les aigus resteraient plats
STYLES = ("wire", "surface", "points")
BASE_RADIUS = 0.62
CAMERA_DIST = 5.0
FOV_DEG = 30.0


def _num(args, name: str, default: float, lo: float, hi: float) -> float:
    try:
        return min(hi, max(lo, float(getattr(args, name, default))))
    except (TypeError, ValueError):
        return default


class SpectrumTracker:
    """Du son aux valeurs que la sphere affiche. `update(samples, rate, dt, args)` -> rien ; lire `bands`, `loudness`, `bass`, `rows`."""

    def __init__(self) -> None:
        self.hist = np.zeros((HIST_ROWS, MAX_BANDS), dtype=np.float32)
        self.head = 0                              # prochaine ligne a ecrire ; la plus recente est head - 1
        self.bands = np.zeros(MAX_BANDS, dtype=np.float32)
        self.peak_db = -60.0
        self.loud_peak_db = -50.0
        self.loudness = 0.0                        # 0..1 : intensite du morceau (lissee)
        self.bass = 0.0                            # 0..1 : enveloppe des graves (pulsation)
        self._acc = 0.0
        self._window = np.hanning(WIN).astype(np.float32)
        self._edges_key = None
        self._edges = None

    def _band_edges(self, bands: int, rate: float):
        key = (bands, int(rate))
        if key != self._edges_key:
            hz = np.geomspace(F_LOW, min(F_HIGH, rate / 2.0 - 100.0), bands + 1)
            bin_hz = rate / WIN
            lo = np.floor(hz[:-1] / bin_hz).astype(int)
            hi = np.maximum(np.ceil(hz[1:] / bin_hz).astype(int), lo + 1)
            centers = np.sqrt(hz[:-1] * hz[1:])
            tilt = TILT_DB_PER_OCT * np.log2(centers / 200.0)
            self._edges = (lo, hi, tilt.astype(np.float32))
            self._edges_key = key
        return self._edges

    def update(self, samples, rate: float, dt: float, args) -> None:
        bands = int(_num(args, "bands", 32, 8, MAX_BANDS))
        db_range = _num(args, "db_range", 40.0, 20.0, 70.0)
        smooth = _num(args, "smooth", 0.4, 0.0, 1.0)
        dt = min(max(dt, 1e-4), 0.25)
        if samples is None or len(samples) < WIN:
            level_db = -120.0
            target = np.zeros(bands, dtype=np.float32)
        else:
            x = np.asarray(samples[-WIN:], dtype=np.float32)
            rms = float(np.sqrt(np.mean(np.square(x))))
            level_db = 20.0 * math.log10(rms + 1e-9)
            mag = np.abs(np.fft.rfft(x * self._window)) * (4.0 / WIN)
            lo, hi, tilt = self._band_edges(bands, rate)
            vals = np.array([mag[a:b].max() for a, b in zip(lo, hi)], dtype=np.float32)
            db = 20.0 * np.log10(vals + 1e-9) + tilt
            self.peak_db = max(float(db.max()), self.peak_db - 1.2 * dt, -60.0)
            target = np.clip((db - (self.peak_db - db_range)) / db_range, 0.0, 1.0).astype(np.float32)
            if level_db < -75.0:                  # silence : pas de relief du bruit de fond
                target[:] = 0.0
        rel = 0.03 + 0.6 * smooth
        prev = self.bands[:bands]
        k = np.where(target > prev, 1.0 - math.exp(-dt / 0.02), 1.0 - math.exp(-dt / rel)).astype(np.float32)
        self.bands[:bands] = prev + (target - prev) * k
        self.bands[bands:] = 0.0
        # intensite du morceau : niveau rapporte au plus fort niveau recent (oubli lent), lisse lentement
        self.loud_peak_db = max(level_db, self.loud_peak_db - 0.8 * dt, -50.0)
        want = min(1.0, max(0.0, (level_db - (self.loud_peak_db - 24.0)) / 24.0)) if level_db > -75.0 else 0.0
        k_loud = 1.0 - math.exp(-dt / (0.15 if want > self.loudness else 0.7))
        self.loudness += (want - self.loudness) * k_loud
        low = float(self.bands[:max(2, bands // 4)].mean())
        k_bass = 1.0 - math.exp(-dt / (0.02 if low > self.bass else 0.25))
        self.bass += (low - self.bass) * k_bass
        self._acc += dt
        pushed = 0
        while self._acc >= 1.0 / HIST_RATE and pushed < 4:
            self._acc -= 1.0 / HIST_RATE
            self.hist[self.head] = self.bands
            self.head = (self.head + 1) % HIST_ROWS
            pushed += 1
        self._acc = min(self._acc, 1.0 / HIST_RATE)

    def scale(self, args) -> float:
        """Taille de la sphere : 1 = taille de repos ; suit l'intensite (`size_react`) et pulse sur les graves (`pulse`)."""
        react = _num(args, "size_react", 0.6, 0.0, 1.0)
        pulse = _num(args, "pulse", 0.4, 0.0, 1.0)
        return (1.0 - 0.45 * react + 0.9 * react * self.loudness) * (1.0 + 0.12 * pulse * min(1.0, self.bass * 1.5))


def mesh(detail: int):
    """Sommets (u, v) : u = azimut autour de l'axe de face (0..1), v = angle depuis le centre de face (0 = centre, 1 = arriere).
    Rend (sommets, indices des triangles, indices des lignes)."""
    rings, segs = detail, detail * 2
    uv = np.zeros(((rings + 1) * (segs + 1), 2), dtype="f4")
    for j in range(rings + 1):
        for i in range(segs + 1):
            uv[j * (segs + 1) + i] = (i / segs, j / rings)
    tri, lines = [], []
    stride = segs + 1
    for j in range(rings):
        for i in range(segs):
            a, b, c, d = j * stride + i, j * stride + i + 1, (j + 1) * stride + i, (j + 1) * stride + i + 1
            tri += [a, b, c, b, d, c]
    for j in range(rings + 1):                     # anneaux
        for i in range(segs):
            lines += [j * stride + i, j * stride + i + 1]
    step = max(1, segs // 32)
    for i in range(0, segs, step):                 # meridiens
        for j in range(rings):
            lines += [j * stride + i, (j + 1) * stride + i]
    return uv, np.array(tri, dtype="i4"), np.array(lines, dtype="i4")


def projection(aspect: float, tilt_deg: float, spin_deg: float):
    """Matrice 4x4 (ligne par ligne) : rotation (spin autour de la verticale, puis inclinaison), camera a CAMERA_DIST, image retournee
    en hauteur (la premiere rangee de la texture est le haut de l'image)."""
    cs, sn = math.cos(math.radians(spin_deg)), math.sin(math.radians(spin_deg))
    ct, st = math.cos(math.radians(tilt_deg)), math.sin(math.radians(tilt_deg))
    ry = np.array([[cs, 0, sn, 0], [0, 1, 0, 0], [-sn, 0, cs, 0], [0, 0, 0, 1]], dtype=np.float64)
    rx = np.array([[1, 0, 0, 0], [0, ct, -st, 0], [0, st, ct, 0], [0, 0, 0, 1]], dtype=np.float64)
    view = np.eye(4)
    view[2, 3] = -CAMERA_DIST
    f = 1.0 / math.tan(math.radians(FOV_DEG) / 2.0)
    near, far = 0.5, 20.0
    proj = np.array([[f / aspect, 0, 0, 0], [0, -f, 0, 0],
                     [0, 0, (far + near) / (near - far), 2 * far * near / (near - far)], [0, 0, -1, 0]], dtype=np.float64)
    return proj @ view @ rx @ ry


class SphereGL:
    """Ressources GL d'un contexte. `draw(tracker, args, t)` rend `out_tex` (RGB8, premiere rangee = haut)."""

    def __init__(self, ctx, size: tuple[int, int], module) -> None:
        self.ctx, self.size, self.module = ctx, size, module
        self.prog = ctx.program(vertex_shader=(SHADER_DIR / "mode_sphere.vert").read_text(encoding="utf-8"),
                                fragment_shader=(SHADER_DIR / "mode_sphere.frag").read_text(encoding="utf-8"))
        self.hist_tex = ctx.texture((MAX_BANDS, HIST_ROWS), 1, dtype="f4")
        self.hist_tex.filter = (moderngl.LINEAR, moderngl.LINEAR)
        self.hist_tex.repeat_x, self.hist_tex.repeat_y = False, True
        self.out_tex = ctx.texture(size, 3)
        self.out_tex.filter = (moderngl.LINEAR, moderngl.LINEAR)
        self.out_fbo = ctx.framebuffer(color_attachments=[self.out_tex], depth_attachment=ctx.depth_renderbuffer(size))
        self.ms_fbo = None
        try:
            self.ms_fbo = ctx.framebuffer(color_attachments=[ctx.renderbuffer(size, 4, samples=4)],
                                          depth_attachment=ctx.depth_renderbuffer(size, samples=4))
        except Exception:
            self.ms_fbo = None                      # pas de multi-echantillonnage : bords crenelés, mais ca marche
        self.detail = 0
        self.vbo = self.ibo_tri = self.ibo_line = None
        self.vaos: dict = {}
        self.colors_key = None
        self.colors = ((0.2, 0.8, 1.0), (1.0, 0.25, 0.9), (1.0, 1.0, 1.0), 2, (0.0, 0.0, 0.0))

    def _build(self, detail: int) -> None:
        for obj in (self.vbo, self.ibo_tri, self.ibo_line, *self.vaos.values()):
            if obj is not None:
                obj.release()
        uv, tri, lines = mesh(detail)
        self.vbo = self.ctx.buffer(uv.tobytes())
        self.ibo_tri = self.ctx.buffer(tri.tobytes())
        self.ibo_line = self.ctx.buffer(lines.tobytes())
        content = [(self.vbo, "2f", "in_uv")]
        self.vaos = {"surface": self.ctx.vertex_array(self.prog, content, self.ibo_tri),
                     "wire": self.ctx.vertex_array(self.prog, content, self.ibo_line),
                     "points": self.ctx.vertex_array(self.prog, content)}
        self.detail = detail

    def _colors(self, args):
        key = (str(getattr(args, "colors", "")), str(getattr(args, "bg_color", "")))
        if key != self.colors_key:
            parts = [c.strip() for c in key[0].split(",") if c.strip()][:3] or ["0x39c9ff", "0xff3df2"]
            try:
                cols = [parse_color(self.module, c) for c in parts]
                bg = parse_color(self.module, key[1] or "black")
            except Exception:
                cols, bg = [(0.2, 0.8, 1.0), (1.0, 0.25, 0.9)], (0.0, 0.0, 0.0)
            n = len(cols)
            while len(cols) < 3:
                cols.append(cols[-1])
            self.colors = (cols[0], cols[1], cols[2], n, bg)
            self.colors_key = key
        return self.colors

    def draw(self, tracker: SpectrumTracker, args, t: float):
        width, height = self.size
        detail = int(_num(args, "detail", 48, 16, 96))
        if detail != self.detail:
            self._build(detail)
        style = getattr(args, "style", "wire")
        style = style if style in STYLES else "wire"
        c0, c1, c2, ncol, bg = self._colors(args)
        bands = int(_num(args, "bands", 32, 8, MAX_BANDS))
        prog = self.prog
        self.hist_tex.write(tracker.hist.tobytes())
        self.hist_tex.use(0)
        mvp = projection(width / height, _num(args, "tilt", 20.0, -60.0, 60.0), (_num(args, "spin", 0.0, -90.0, 90.0) * t) % 360.0)
        values = {"u_mvp": None, "u_hist": 0, "u_bands": bands, "u_head": float(tracker.head - 1),
                  "u_depth": _num(args, "history", 0.6, 0.0, 2.0) * HIST_RATE, "u_relief": _num(args, "relief", 0.7, 0.0, 1.5),
                  "u_scale": BASE_RADIUS * tracker.scale(args), "u_style": STYLES.index(style),
                  "u_c0": c0, "u_c1": c1, "u_c2": c2, "u_ncol": ncol,
                  "u_shape": _num(args, "shape", 0.0, 0.0, 5.0),
                  "u_point": float(_num(args, "point_size", 2, 1, 8)), "u_rows": float(HIST_ROWS)}
        for name, value in values.items():
            if name in prog and value is not None:
                prog[name].value = value
        if "u_mvp" in prog:
            prog["u_mvp"].write(mvp.T.astype("f4").tobytes())
        target = self.ms_fbo or self.out_fbo
        target.use()
        self.ctx.viewport = (0, 0, width, height)
        target.clear(bg[0], bg[1], bg[2], 1.0)
        self.ctx.enable(moderngl.DEPTH_TEST | moderngl.PROGRAM_POINT_SIZE)
        if style == "surface":
            self.ctx.disable(moderngl.BLEND)
            self.vaos["surface"].render(moderngl.TRIANGLES)
        else:
            self.ctx.enable(moderngl.BLEND)
            self.ctx.blend_func = moderngl.ONE, moderngl.ONE            # additif : les lignes se cumulent comme de la lumiere
            self.ctx.disable(moderngl.DEPTH_TEST)
            try:
                self.ctx.line_width = float(_num(args, "line_width", 1, 1, 6))
            except Exception:
                pass
            if style == "wire":
                self.vaos["wire"].render(moderngl.LINES)
            else:
                self.vaos["points"].render(moderngl.POINTS)
            self.ctx.disable(moderngl.BLEND)
        self.ctx.disable(moderngl.DEPTH_TEST | moderngl.PROGRAM_POINT_SIZE)
        if self.ms_fbo is not None:
            self.ctx.copy_framebuffer(self.out_fbo, self.ms_fbo)
        return self.out_tex

    def release(self) -> None:
        for obj in (self.hist_tex, self.out_tex, self.out_fbo, self.ms_fbo, self.vbo, self.ibo_tri, self.ibo_line, *self.vaos.values()):
            try:
                if obj is not None:
                    obj.release()
            except Exception:
                pass


class SphereSource(NativeModeSource):
    """Mode Sphere : memes attributs que `NativeModeSource`, mais sans fil d'analyse (l'analyse est faite dans `render`, fil GL)."""

    def __init__(self, module, args, size: tuple[int, int], audio, status: dict | None = None) -> None:
        super().__init__("sphere", module, args, size, audio, status)
        self.tracker = SpectrumTracker()
        self._last = None
        self._t0 = None
        self._device = args.device
        self._following = False

    def start(self) -> None:
        self.status["text"] = "sphere: spectre du son sur une sphere"

    def render(self, ctx, now: float):
        if self._gl is None or self._gl_ctx is not ctx:
            if self._gl is not None:
                self._gl.release()
            self._gl, self._gl_ctx = SphereGL(ctx, self.size, self.module), ctx
        if self._t0 is None:
            self._t0 = now
        dt = 1.0 / 60.0 if self._last is None else now - self._last
        self._last = now
        if self.args.device != self._device and not self._following:      # l'entree choisie dans le panneau : l'analyse suit (hors fil GL)
            self._device, self._following = self.args.device, True

            def follow(name=self._device) -> None:
                try:
                    self._follow_device(name)
                finally:
                    self._following = False
            threading.Thread(target=follow, name="sphere-entree", daemon=True).start()
        rate = float(self.audio.rate)
        samples = self.audio.ring.latest(WIN) if self.audio.ring.total >= WIN else None
        self.tracker.update(samples, rate, dt, self.args)
        return self._gl.draw(self.tracker, self.args, now - self._t0)

    def stop(self) -> None:
        self.stop_event.set()
        self.finished_event.set()
