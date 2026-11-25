"""Remplissage des lettres par reaction-diffusion (overlay Texte, #199) : Gray-Scott sur le GPU, limite a l'interieur des lettres.

Calendrier et tailles : `core/textfx.py` (pur). Le masque vient de l'alpha du texte (reduit a la grille) ; il sert de mur : rien ne
diffuse dehors (`gl_shaders/textfill_step.frag`). Etat RGBA (r = U, g = V, b = masque) sur une grille a l'aspect du texte, orientee comme
la texture du texte (rangee 0 = haut) : `scene.frag` l'echantillonne avec la meme coordonnee que le logo. Possede par le `Renderer`,
cree quand la case est cochee avec la source Texte, detruit sinon.
"""
from __future__ import annotations

from collections import deque
from pathlib import Path

from core import reaction as rc
from core import textfx
from engine.deps import moderngl, np

MASK_MIN = 0.25                       # part d'une cellule couverte par une lettre pour qu'elle compte dans le masque
SEED_RADIUS = 2.0                     # rayon d'un germe, en cellules
CELLS_PER_SEED = 400                  # un germe de plus par tranche de cellules d'une lettre (au moins un par morceau connexe)
MAX_SEEDS = 120


def reduce_mask(alpha, size: tuple[int, int]):
    """Masque booleen (h, w) de la grille `size` = (w, h) : une cellule est dans une lettre si, en moyenne, `MASK_MIN` de ses pixels
    d'alpha le sont. `alpha` : tableau (H, W) uint8, haut en premier."""
    gw, gh = size
    H, W = alpha.shape
    gw, gh = min(gw, W), min(gh, H)
    ey = np.linspace(0, H, gh + 1).astype(np.int64)
    ex = np.linspace(0, W, gw + 1).astype(np.int64)
    sums = np.add.reduceat(np.add.reduceat(alpha.astype(np.float32), ey[:-1], axis=0), ex[:-1], axis=1)
    area = np.outer(np.diff(ey), np.diff(ex)).astype(np.float32)
    return (sums / np.maximum(area, 1.0)) >= MASK_MIN * 255.0


def components(mask) -> list:
    """Morceaux connexes (4 voisins) du masque : liste de tableaux d'indices a plat. Un par lettre (ou par groupe de lettres qui se touchent)."""
    h, w = mask.shape
    flat = mask.ravel().tolist()
    seen = [False] * (h * w)
    out = []
    for start in range(h * w):
        if not flat[start] or seen[start]:
            continue
        seen[start] = True
        queue, cells = deque([start]), []
        while queue:
            i = queue.popleft()
            cells.append(i)
            x = i % w
            for j in ((i - 1) if x > 0 else -1, (i + 1) if x < w - 1 else -1, i - w, i + w):
                if 0 <= j < h * w and flat[j] and not seen[j]:
                    seen[j] = True
                    queue.append(j)
        out.append(np.asarray(cells, dtype=np.int64))
    return out


class TextFill:
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
        self.comps: list = []
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
        self.mask, self.comps, self.size, self.key = None, [], None, None

    def set_mask(self, alpha, scale: float, key) -> bool:
        """Construit la grille et le masque pour ce texte (`alpha` : (H, W) uint8) a cette echelle ; sans effet si `key` est deja celui de
        la grille. Rend True quand la grille a change (le motif repart de zero via `reset`). Allocation impossible : reste inutilisable."""
        if key == self.key and self.tex:
            return False
        H, W = alpha.shape
        gw, gh = textfx.fill_grid_size(W / max(H, 1), scale)
        gw, gh = min(gw, W), min(gh, H)
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
        self.comps = components(self.mask)
        return True

    def reset(self, seed: int) -> None:
        """Etat initial : U = 1, V = 0 dans les lettres, et des germes (disques) places au hasard (graine) : au moins un par morceau
        connexe du masque, plus un par tranche de CELLS_PER_SEED cellules."""
        if not self.tex or self.mask is None:
            return
        gw, gh = self.size
        rng = np.random.default_rng(int(seed) & 0xFFFFFFFF)
        u = np.ones((gh, gw), np.float32)
        v = np.zeros((gh, gw), np.float32)
        picks = []
        for cells in self.comps:
            k = 1 + len(cells) // CELLS_PER_SEED
            picks.extend(rng.choice(cells, size=min(k, len(cells)), replace=False).tolist())
        if len(picks) > MAX_SEEDS:
            picks = rng.permutation(picks)[:MAX_SEEDS].tolist()
        yy, xx = np.mgrid[0:gh, 0:gw]
        for i in picks:
            cy, cx = divmod(int(i), gw)
            disc = (xx - cx) ** 2 + (yy - cy) ** 2 <= SEED_RADIUS ** 2
            u[disc], v[disc] = 0.5, 0.5
        m = self.mask.astype(np.float32)
        u, v = np.where(self.mask, u, 1.0), np.where(self.mask, v, 0.0)
        data = np.stack([u, v, m, np.ones_like(m)], axis=2).astype(np.float16 if self.dtype == "f2" else np.float32)
        for t in self.tex:
            t.write(np.ascontiguousarray(data).tobytes())
        self.cur = 0

    def step(self, n: int, shape: float = 1.0) -> None:
        """`n` pas Gray-Scott. `shape` (axe 0..2 de `core.reaction.shape_feed_kill`) : 1 = lignes, qui colonisent un espace ferme."""
        if n <= 0 or not self.tex:
            return
        prev = self.ctx.fbo
        w, h = self.size
        self.prog["u_state"].value = 0
        self.prog["u_texel"].value = (1.0 / w, 1.0 / h)
        self.prog["u_fk"].value = rc.shape_feed_kill(shape)
        for _ in range(n):
            dst = 1 - self.cur
            self.fbo[dst].viewport = (0, 0, w, h)
            self.fbo[dst].use()
            self.tex[self.cur].use(0)
            self.vao.render(moderngl.TRIANGLE_STRIP)
            self.cur = dst
        if prev is not None:
            prev.use()

    def read(self):
        """Etat (h, w, 4) float32, haut en premier (tests)."""
        w, h = self.size
        raw = np.frombuffer(self.tex[self.cur].read(), dtype=np.float16 if self.dtype == "f2" else np.float32)
        return raw.reshape(h, w, 4).astype(np.float32)
