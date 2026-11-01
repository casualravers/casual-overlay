"""Palette « labyrinthe » du fond genere : simulation Gray-Scott sur le GPU (textures ping-pong, `gl_shaders/rd_step.frag`).

La logique pure (parametres selon la forme, grille, germes, pas par image) est dans `core/reaction.py`. Cette classe est possedee par le
`Renderer`, qui la pilote et lie sa texture a `scene.frag`. L'etat (r = U, g = V) vit sur une grille fixe, independante de la fenetre.
"""
from __future__ import annotations

import math
from pathlib import Path

from core import reaction as rc
from engine.deps import moderngl, np


class ReactionDiffusion:
    def __init__(self, ctx, vbo, shader_dir: Path):
        self.ctx = ctx
        vert = (shader_dir / "quad.vert").read_text(encoding="utf-8")
        frag = (shader_dir / "rd_step.frag").read_text(encoding="utf-8")
        self.prog = ctx.program(vertex_shader=vert, fragment_shader=frag)
        self.vao = ctx.vertex_array(self.prog, [(vbo, "2f", "in_pos")])
        self.tex: list = []
        self.fbo: list = []
        self.cur = 0
        self.size: tuple[int, int] | None = None
        self.seed = 0
        self._watch_t: float | None = None

    @property
    def texture(self):
        return self.tex[self.cur]

    def _alloc(self, size: tuple[int, int]) -> None:
        for obj in (*self.fbo, *self.tex):
            obj.release()
        self.tex, self.fbo = [], []
        # RG32F d'abord : en RG16F un increment F x (1 - U) plus petit que le demi-ulp pres de 1 est perdu et le motif se fige en un
        # point fixe d'arrondi apres ~15 s (mesure). La grille est petite (384 x 216 x 8 octets) : le cout est negligeable. Repli RG16F.
        for dtype in ("f4", "f2"):
            try:
                tex = [self.ctx.texture(size, 2, dtype=dtype) for _ in range(2)]
                fbo = [self.ctx.framebuffer(color_attachments=[t]) for t in tex]
            except Exception:
                continue
            self.tex, self.fbo, self.dtype = tex, fbo, dtype
            break
        for t in self.tex:
            t.filter = (moderngl.LINEAR, moderngl.LINEAR)
            t.repeat_x = t.repeat_y = True
        self.size, self.cur = size, 0

    def ensure(self, scale: float) -> None:
        """Grille a la taille voulue ; une grille existante de taille differente est recopiee (bilineaire), sans repartir de zero."""
        size = rc.grid_size(scale)
        if size == self.size:
            return
        old = (self._read(), self.size) if self.size else None
        self._alloc(size)
        if old is None:
            self.reset(self.seed)
            return
        (u, v), (ow, oh) = old
        w, h = size
        ys = (np.arange(h) + 0.5) * oh / h - 0.5
        xs = (np.arange(w) + 0.5) * ow / w - 0.5
        y0, x0 = np.floor(ys).astype(int), np.floor(xs).astype(int)
        fy, fx = (ys - y0)[:, None], (xs - x0)[None, :]

        def resample(a):
            a00, a01 = a[np.ix_(y0 % oh, x0 % ow)], a[np.ix_(y0 % oh, (x0 + 1) % ow)]
            a10, a11 = a[np.ix_((y0 + 1) % oh, x0 % ow)], a[np.ix_((y0 + 1) % oh, (x0 + 1) % ow)]
            return (a00 * (1 - fx) + a01 * fx) * (1 - fy) + (a10 * (1 - fx) + a11 * fx) * fy

        self.write(resample(u), resample(v))

    def reset(self, seed: int) -> None:
        """Etat initial : U = 1, V = 0, et des disques de V = 1 aux germes de la graine (positions deterministes)."""
        self.seed = seed
        w, h = self.size
        yy, xx = np.mgrid[0:h, 0:w].astype(np.float32)
        v = np.zeros((h, w), np.float32)
        for sx, sy in rc.initial_seeds(seed):
            dx = np.abs(xx + 0.5 - sx * w)
            dy = np.abs(yy + 0.5 - sy * h)
            dx, dy = np.minimum(dx, w - dx), np.minimum(dy, h - dy)            # grille en boucle
            v[dx * dx + dy * dy <= rc.SEED_RADIUS ** 2] = 1.0
        self.write(np.ones((h, w), np.float32), v)

    def write(self, u, v) -> None:
        """Ecrit l'etat (U, V : tableaux (h, w), haut en premier) dans la texture courante."""
        data = np.stack([u, v], axis=2)[::-1].astype(np.float16 if self.dtype == "f2" else np.float32)
        self.tex[self.cur].write(np.ascontiguousarray(data).tobytes())

    def _read(self):
        w, h = self.size
        raw = np.frombuffer(self.tex[self.cur].read(), dtype=np.float16 if self.dtype == "f2" else np.float32)
        a = raw.reshape(h, w, 2)[::-1].astype(np.float32)
        return a[:, :, 0].copy(), a[:, :, 1].copy()

    def read_v(self):
        """V en float32, forme (h, w), haut en premier."""
        return self._read()[1]

    def step(self, n: int, shape: float, seeds=(), zone: rc.Zone | None = None) -> list:
        """`n` pas Gray-Scott a la forme `shape` (axe 0..2 : points, lignes, taches) ; les germes (au plus 4, positions normalisees
        origine haut gauche) ne sont poses qu'au premier pas. `zone` : carte de forme qui derive (None = forme uniforme).
        Rend les germes qui n'ont pas pu etre poses."""
        if n <= 0 or not self.tex:
            return list(seeds)
        prev = self.ctx.fbo
        w, h = self.size
        prog = self.prog
        zone = zone or rc.Zone()
        prog["u_state"].value = 0
        prog["u_texel"].value = (1.0 / w, 1.0 / h)
        prog["u_grid"].value = (float(w), float(h))
        prog["u_fk"].value = rc.shape_feed_kill(shape)
        for name, value in (("u_mix", float(zone.mix)), ("u_base", float(shape)), ("u_zone_t", float(zone.t)),
                            ("u_fk3", [rc.SPOTS, rc.MAZE, rc.BLOB]), ("u_zone_cells", tuple(float(c) for c in rc.ZONE_CELLS))):
            if name in prog:
                prog[name].value = value
        seeds = list(seeds)[:4]
        flat = [(x, y, rc.SEED_RADIUS) for x, y in seeds] + [(0.0, 0.0, 0.0)] * (4 - len(seeds))
        if "u_seeds" in prog:
            prog["u_seeds"].value = flat
        for i in range(n):
            if "u_nseeds" in prog:
                prog["u_nseeds"].value = len(seeds) if i == 0 else 0
            dst = 1 - self.cur
            self.fbo[dst].viewport = (0, 0, w, h)
            self.fbo[dst].use()
            self.tex[self.cur].use(0)
            self.vao.render(moderngl.TRIANGLE_STRIP)
            self.cur = dst
        if prev is not None:                               # contexte autonome (tests) : pas de framebuffer actif a rendre
            prev.use()
        return []

    def watch(self, now: float) -> bool:
        """Toutes les WATCH_S s, moyenne de V (mipmaps, niveau 1 x 1) ; motif eteint = ressemee (graine suivante). Rend True si ressemee."""
        if self._watch_t is None:
            self._watch_t = now
            return False
        if now - self._watch_t < rc.WATCH_S or not self.tex:
            return False
        self._watch_t = now
        tex = self.tex[self.cur]
        tex.build_mipmaps()
        level = int(math.floor(math.log2(max(self.size))))
        raw = np.frombuffer(tex.read(level=level), dtype=np.float16 if self.dtype == "f2" else np.float32)
        tex.filter = (moderngl.LINEAR, moderngl.LINEAR)
        if float(raw[1]) < rc.DEAD_LEVEL:
            self.reset(self.seed + 1)
            return True
        return False
