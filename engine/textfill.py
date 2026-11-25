"""Remplissage des lettres par reaction-diffusion (overlay Texte, #199) : Gray-Scott sur le GPU, limite a l'interieur des lettres.

Calendrier et tailles : `core/textfx.py` (pur). Le masque vient de l'alpha du texte (reduit a la grille) ; il sert de mur : rien ne
diffuse dehors (`gl_shaders/textfill_step.frag`). Etat RGBA (r = U, g = V, b = masque) sur une grille a l'aspect du texte, orientee comme
la texture du texte (rangee 0 = haut) : `scene.frag` l'echantillonne avec la meme coordonnee que le logo. Possede par le `Renderer`,
cree quand la case est cochee avec la source Texte, detruit sinon.
"""
from __future__ import annotations

from pathlib import Path

from core import reaction as rc
from core import textfx
from engine.deps import moderngl, np

MASK_MIN = 0.25                       # part d'une cellule couverte par une lettre pour qu'elle compte dans le masque
SEED_RADIUS = 2.0                     # rayon d'un germe, en cellules


def reduce_mask(alpha, size: tuple[int, int]):
    """Masque booleen (h, w) de la grille `size` = (w, h) : une cellule est dans une lettre si, en moyenne, `MASK_MIN` de ses pixels
    d'alpha le sont. `alpha` : tableau (H, W) uint8, haut en premier."""
    gw, gh = size
    H, W = alpha.shape
    # image integrale : somme de l'alpha sur le bloc de pixels de chaque cellule ; une cellule plus petite qu'un pixel du texte (grille plus
    # fine que la texture) prend le pixel qui la contient
    integral = np.zeros((H + 1, W + 1), np.float64)
    integral[1:, 1:] = alpha.astype(np.float64).cumsum(axis=0).cumsum(axis=1)
    y0 = np.floor(np.arange(gh) * H / gh).astype(np.int64)
    y1 = np.minimum(np.maximum(np.ceil((np.arange(gh) + 1) * H / gh).astype(np.int64), y0 + 1), H)
    x0 = np.floor(np.arange(gw) * W / gw).astype(np.int64)
    x1 = np.minimum(np.maximum(np.ceil((np.arange(gw) + 1) * W / gw).astype(np.int64), x0 + 1), W)
    sums = (integral[np.ix_(y1, x1)] - integral[np.ix_(y0, x1)] - integral[np.ix_(y1, x0)] + integral[np.ix_(y0, x0)])
    area = np.outer(y1 - y0, x1 - x0).astype(np.float64)
    return (sums / area) >= MASK_MIN * 255.0


class TextFill:
    SHAPE = textfx.FILL_SHAPE          # forme du motif (axe 0..2 de core.reaction.shape_feed_kill : points, lignes, taches)
    DIFF = textfx.FILL_DIFF            # facteur de diffusion : plus petit = motif plus petit

    def __init__(self, ctx, vbo, shader_dir: Path):
        self.ctx = ctx
        vert = (shader_dir / "quad.vert").read_text(encoding="utf-8")
        frag = (shader_dir / "textfill_step.frag").read_text(encoding="utf-8")
        self.prog = ctx.program(vertex_shader=vert, fragment_shader=frag)
        self.vao = ctx.vertex_array(self.prog, [(vbo, "2f", "in_pos")])
        self.tex: list = []
        self.fbo: list = []
        self.cur = 0
        self.size: tuple[int, int] | None = None
        self.dtype = "f4"
        self.mask = None
        self.steps_done = 0                                # pas faits depuis la derniere remise a zero (le motif se fige apres FILL_SETTLE_STEPS)
        self.key = None                                    # (identifiant du texte, echelle arrondie) de la grille construite

    @property
    def texture(self):
        return self.tex[self.cur]

    def _release(self) -> None:
        for obj in (*self.fbo, *self.tex):
            obj.release()
        self.tex, self.fbo = [], []

    def release(self) -> None:
        self._release()
        self.mask, self.size, self.key = None, None, None

    def set_mask(self, alpha, scale: float, key) -> bool:
        """Construit la grille et le masque pour ce texte (`alpha` : (H, W) uint8) a cette echelle ; sans effet si `key` est deja celui de
        la grille. Rend True quand la grille a change (le motif repart de zero via `reset`). Allocation impossible : reste inutilisable."""
        if key == self.key and self.tex:
            return False
        H, W = alpha.shape
        gw, gh = textfx.fill_grid_size(W / max(H, 1), scale)
        self._release()
        for dtype in ("f4", "f2"):
            try:
                tex = [self.ctx.texture((gw, gh), 4, dtype=dtype) for _ in range(2)]
                fbo = [self.ctx.framebuffer(color_attachments=[t]) for t in tex]
            except Exception:
                continue
            self.tex, self.fbo, self.dtype = tex, fbo, dtype
            break
        self.size, self.cur, self.key = (gw, gh), 0, key
        for t in self.tex:
            t.filter = (moderngl.LINEAR, moderngl.LINEAR)
            t.repeat_x = t.repeat_y = False
        self.mask = reduce_mask(alpha, (gw, gh))
        return True

    def reset(self, seed: int) -> None:
        """Etat initial : U = 1, V = 0 dans les lettres, et des germes (disques) : un par bloc de FILL_SEED_BLOCK x FILL_SEED_BLOCK cellules
        du masque, a une position tiree au hasard (graine). Aucune zone d'une lettre n'est loin d'un germe (pas de plage blanche), et un
        morceau de lettre plus petit qu'un bloc a quand meme le sien."""
        if not self.tex or self.mask is None:
            return
        gw, gh = self.size
        rng = np.random.default_rng(int(seed) & 0xFFFFFFFF)
        u = np.ones((gh, gw), np.float32)
        v = np.zeros((gh, gw), np.float32)
        ys, xs = np.nonzero(self.mask)
        order = rng.permutation(len(ys))
        ys, xs = ys[order], xs[order]
        block = textfx.FILL_SEED_BLOCK
        _, first = np.unique((ys // block) * (gw // block + 2) + xs // block, return_index=True)
        r = int(SEED_RADIUS)
        for cy, cx in zip(ys[first].tolist(), xs[first].tolist()):
            y0, y1, x0, x1 = max(cy - r, 0), min(cy + r + 1, gh), max(cx - r, 0), min(cx + r + 1, gw)
            yy, xx = np.mgrid[y0:y1, x0:x1]
            disc = (xx - cx) ** 2 + (yy - cy) ** 2 <= SEED_RADIUS ** 2
            u[y0:y1, x0:x1][disc], v[y0:y1, x0:x1][disc] = 0.5, 0.5
        self.steps_done = 0
        m = self.mask.astype(np.float32)
        u, v = np.where(self.mask, u, 1.0), np.where(self.mask, v, 0.0)
        data = np.stack([u, v, m, np.ones_like(m)], axis=2).astype(np.float16 if self.dtype == "f2" else np.float32)
        for t in self.tex:
            t.write(np.ascontiguousarray(data).tobytes())
        self.cur = 0

    def step(self, n: int, shape: float | None = None, diff: float | None = None) -> None:
        """`n` pas Gray-Scott. `shape` (axe 0..2 de `core.reaction.shape_feed_kill`, defaut `SHAPE`) et `diff` (facteur de diffusion, defaut `DIFF`)."""
        if n <= 0 or not self.tex:
            return
        shape = self.SHAPE if shape is None else shape
        diff = self.DIFF if diff is None else diff
        prev = self.ctx.fbo
        w, h = self.size
        self.prog["u_state"].value = 0
        self.prog["u_fk"].value = rc.shape_feed_kill(shape)
        self.prog["u_diff"].value = float(diff)
        for _ in range(n):
            dst = 1 - self.cur
            self.fbo[dst].viewport = (0, 0, w, h)
            self.fbo[dst].use()
            self.tex[self.cur].use(0)
            self.vao.render(moderngl.TRIANGLE_STRIP)
            self.cur = dst
        self.steps_done += n
        if prev is not None:
            prev.use()

    @property
    def settled(self) -> bool:
        """Vrai quand le motif a fait ses FILL_SETTLE_STEPS pas : il est fige (aucun calcul) jusqu'a la prochaine remise a zero."""
        return self.steps_done >= textfx.FILL_SETTLE_STEPS

    def read(self):
        """Etat (h, w, 4) float32, haut en premier (tests)."""
        w, h = self.size
        raw = np.frombuffer(self.tex[self.cur].read(), dtype=np.float16 if self.dtype == "f2" else np.float32)
        return raw.reshape(h, w, 4).astype(np.float32)
