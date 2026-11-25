"""Remplissage des lettres par reaction-diffusion (overlay Texte, #199) : la tuile de motif.

Un motif de Gray-Scott est calcule UNE fois sur une petite tuile periodique (`core.textfx.TILE_SIZE` cellules de cote), avec, pour chaque
cellule, l'instant ou elle a ete colonisee. `scene.frag` la rejoue ensuite avec une vague (lettre par lettre, du bas vers le haut, voir
`core/textfx.py` : `fill_tau`, `fill_reveal`) : aucune simulation par image. La tuile est calculee par petites tranches (quelques pas par image,
`TextPattern.advance`) puis gardee en cache sur disque : aux lancements suivants elle est rechargee tout de suite. Etat de calcul RGBA
(r = U, g = V, b = instant d'arrivee) ; texture finale RG (V, instant d'arrivee normalise : 1 = le 95e centile des cellules colonisees).
Possede par le `Renderer`, cree quand la case est cochee avec la source Texte.
"""
from __future__ import annotations

import os
from pathlib import Path

from core import params as core_params
from core import reaction as rc
from core import textfx
from engine.deps import moderngl, np

SEED_RADIUS = 2                       # rayon d'un germe, en cellules
SEED = 7                              # graine fixe : la tuile est la meme a chaque calcul (elle est mise en cache)


def tile_cache_path() -> Path:
    """Fichier du cache de la tuile, a cote de gl_params.json (les tests remplacent `core.params.PARAMS_PATH`)."""
    return Path(core_params.PARAMS_PATH).parent / "text_fill_tile.npz"


class TextPattern:
    def __init__(self, ctx, vbo, shader_dir: Path):
        self.ctx = ctx
        self.vbo = vbo
        self.shader_dir = shader_dir
        self.size = int(textfx.TILE_SIZE)
        self.done = 0                                      # pas de calcul faits
        self.ready = False                                 # vrai quand `texture` contient le motif fini
        self.failed = False                                # allocation impossible : l'effet reste coupe
        self.from_cache = False
        self.texture = None                                # RG : V, instant d'arrivee normalise (repeat, LINEAR)
        self.tex: list = []                                # etat de calcul (ping-pong), libere une fois la tuile finie
        self.fbo: list = []
        self.cur = 0
        self.prog = self.vao = None
        self.dtype = "f4"

    # ---- cache
    def load_cache(self) -> bool:
        """Recharge la tuile du disque si sa cle (`textfx.tile_key`) et sa taille sont les bonnes. Rend True si elle est prete."""
        try:
            with np.load(tile_cache_path(), allow_pickle=False) as z:
                if str(z["key"]) != textfx.tile_key():
                    return False
                v, t = z["v"].astype(np.float32), z["t"].astype(np.float32)
        except (OSError, ValueError, KeyError):
            return False
        if v.shape != (self.size, self.size) or t.shape != v.shape:
            return False
        self._publish(v, t)
        self.from_cache = True
        return self.ready

    def _save_cache(self, v, t) -> None:
        path = tile_cache_path()
        tmp = path.with_name(path.stem + ".tmp.npz")
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            np.savez(tmp, key=np.asarray(textfx.tile_key()), v=v.astype(np.float16), t=t.astype(np.float16))
            os.replace(tmp, path)
        except OSError:
            try:
                tmp.unlink()
            except OSError:
                pass

    def _publish(self, v, t) -> None:
        """Ecrit la tuile finie (V, instant d'arrivee normalise) dans la texture d'affichage."""
        data = np.stack([v, t], axis=2)
        for dtype in ("f4", "f2"):
            try:
                tex = self.ctx.texture((self.size, self.size), 2, data=np.ascontiguousarray(data.astype(np.float16 if dtype == "f2" else np.float32)).tobytes(),
                                       dtype=dtype)
            except Exception:
                continue
            tex.filter = (moderngl.LINEAR, moderngl.LINEAR)
            tex.repeat_x = tex.repeat_y = True
            self.texture, self.ready = tex, True
            return
        self.failed = True

    # ---- calcul par tranches
    def begin(self) -> None:
        """Prepare le calcul : programme, textures d'etat et germes (un par bloc, position au hasard, tuile periodique)."""
        s = self.size
        vert = (self.shader_dir / "quad.vert").read_text(encoding="utf-8")
        frag = (self.shader_dir / "textfill_step.frag").read_text(encoding="utf-8")
        try:
            self.prog = self.ctx.program(vertex_shader=vert, fragment_shader=frag)
            self.vao = self.ctx.vertex_array(self.prog, [(self.vbo, "2f", "in_pos")])
            for dtype in ("f4", "f2"):
                try:
                    tex = [self.ctx.texture((s, s), 4, dtype=dtype) for _ in range(2)]
                    fbo = [self.ctx.framebuffer(color_attachments=[t]) for t in tex]
                except Exception:
                    continue
                self.tex, self.fbo, self.dtype = tex, fbo, dtype
                break
        except Exception:
            self.failed = True
            return
        if not self.tex:
            self.failed = True
            return
        for t in self.tex:
            t.filter = (moderngl.NEAREST, moderngl.NEAREST)
            t.repeat_x = t.repeat_y = False
        rng = np.random.default_rng(SEED)
        st = np.zeros((s, s, 4), np.float32)
        st[..., 0] = 1.0
        st[..., 3] = 1.0
        block = textfx.TILE_SEED_BLOCK
        nb = -(-s // block)
        cy = (np.arange(nb)[:, None] * block + rng.integers(0, block, (nb, nb))) % s
        cx = (np.arange(nb)[None, :] * block + rng.integers(0, block, (nb, nb))) % s
        for dy in range(-SEED_RADIUS, SEED_RADIUS + 1):
            for dx in range(-SEED_RADIUS, SEED_RADIUS + 1):
                if dx * dx + dy * dy <= SEED_RADIUS ** 2:
                    st[(cy + dy) % s, (cx + dx) % s, 0:2] = 0.5
        data = np.ascontiguousarray(st.astype(np.float16 if self.dtype == "f2" else np.float32)).tobytes()
        for t in self.tex:
            t.write(data)
        self.cur, self.done = 0, 0
        self.prog["u_state"].value = 0
        self.prog["u_fk"].value = rc.shape_feed_kill(textfx.FILL_SHAPE)
        self.prog["u_diff"].value = float(textfx.FILL_DIFF)
        self.prog["u_threshold"].value = float(textfx.TILE_THRESHOLD)

    def advance(self, n: int) -> None:
        """Fait `n` pas de calcul ; au dernier, publie la tuile, la met en cache et libere l'etat de calcul."""
        if self.ready or self.failed or n <= 0:
            return
        if not self.tex:
            self.begin()
            if self.failed:
                return
        prev = self.ctx.fbo
        s = self.size
        for _ in range(min(n, textfx.TILE_STEPS - self.done)):
            self.done += 1
            self.prog["u_stepn"].value = self.done / float(textfx.TILE_STEPS)
            dst = 1 - self.cur
            self.fbo[dst].viewport = (0, 0, s, s)
            self.fbo[dst].use()
            self.tex[self.cur].use(0)
            self.vao.render(moderngl.TRIANGLE_STRIP)
            self.cur = dst
        if prev is not None:
            prev.use()
        if self.done >= textfx.TILE_STEPS:
            self._finish()

    def _finish(self) -> None:
        s = self.size
        raw = np.frombuffer(self.tex[self.cur].read(), dtype=np.float16 if self.dtype == "f2" else np.float32).reshape(s, s, 4).astype(np.float32)
        v, arrival = raw[..., 1].copy(), raw[..., 2]
        pos = arrival[arrival > 0.0]
        p95 = float(np.quantile(pos, 0.95)) if pos.size else 1.0
        t = np.where(arrival > 0.0, np.minimum(arrival / max(p95, 1e-6), 1.0), 1.0).astype(np.float32)      # jamais colonisee = en dernier (matiere nulle)
        self._publish(v, t)
        self._save_cache(v, t)
        self.release_state()

    def release_state(self) -> None:
        for obj in (*self.fbo, *self.tex):
            obj.release()
        self.tex, self.fbo = [], []

    def release(self) -> None:
        self.release_state()
        if self.texture is not None:
            self.texture.release()
            self.texture = None
        self.ready = False

    def read(self):
        """Tuile finie (h, w, 2) float32 : V, instant d'arrivee normalise (tests)."""
        raw = np.frombuffer(self.texture.read(), dtype=np.float16 if self.texture.dtype == "f2" else np.float32)
        return raw.reshape(self.size, self.size, 2).astype(np.float32)
