"""Recherche du regime « taches » (BLOB) de la palette labyrinthe par balayage de (F, K) (outil de reglage, non lance par check_gl.py).

Pour chaque (F, K) candidat : simulation a la forme 2 (contexte OpenGL autonome), puis criteres de la spec
`docs/plans/2026-11-01-labyrinthe-evolution-design.md` : couverture de V > 0,25 entre 0,45 et 0,85, aire moyenne des composantes
plus de 5 fois celle des lignes (forme 1), motif qui change encore, chemin lignes -> taches vivant (formes 1,2 / 1,4 / 1,6 / 1,8).

    python tools/rd_sweep.py                      # grille complete de la spec (F 0,020..0,070, K 0,050..0,068, pas 0,002)
    python tools/rd_sweep.py --pas 0.004 --pas-sim 3000
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from core import reaction as rc  # noqa: E402
from engine.deps import moderngl, np  # noqa: E402
from engine.reaction import ReactionDiffusion  # noqa: E402
from engine.render import SHADER_DIR  # noqa: E402


def components(mask):
    """Aires des composantes connexes (4-voisinage, grille en boucle) d'un masque booleen."""
    h, w = mask.shape
    seen = np.zeros_like(mask, bool)
    sizes = []
    for y0, x0 in zip(*np.nonzero(mask)):
        if seen[y0, x0]:
            continue
        stack, n = [(y0, x0)], 0
        seen[y0, x0] = True
        while stack:
            y, x = stack.pop()
            n += 1
            for dy, dx in ((1, 0), (-1, 0), (0, 1), (0, -1)):
                ny, nx = (y + dy) % h, (x + dx) % w
                if mask[ny, nx] and not seen[ny, nx]:
                    seen[ny, nx] = True
                    stack.append((ny, nx))
        sizes.append(n)
    return sizes


def main() -> int:
    ap = argparse.ArgumentParser(description="Balayage de (F, K) pour le regime taches de la palette labyrinthe")
    ap.add_argument("--pas", type=float, default=0.002, help="pas de F (et de K sans --pas-k)")
    ap.add_argument("--pas-k", type=float, default=None)
    ap.add_argument("--f", type=float, nargs=2, default=(0.020, 0.070), metavar=("MIN", "MAX"))
    ap.add_argument("--k", type=float, nargs=2, default=(0.050, 0.068), metavar=("MIN", "MAX"))
    ap.add_argument("--pas-sim", type=int, default=6000, help="pas de simulation par candidat")
    ap.add_argument("--tout", action="store_true", help="affiche aussi les points rejetes au premier critere")
    args = ap.parse_args()
    ctx = moderngl.create_standalone_context()
    vbo = ctx.buffer(np.array([-1, -1, 1, -1, -1, 1, 1, 1], dtype="f4").tobytes())

    def sim(shape, steps):
        r = ReactionDiffusion(ctx, vbo, SHADER_DIR)
        r.ensure(1.0)
        r.reset(0)
        for _ in range(steps // 20):
            r.step(20, shape)
        return r

    maze_mean = float(np.mean(components(sim(1.0, args.pas_sim).read_v() > 0.25)))
    print(f"lignes (forme 1) : aire moyenne {maze_mean:.0f}")
    found = []
    f = args.f[0]
    while f <= args.f[1] + 1e-7:
        k = args.k[0]
        while k <= args.k[1] + 1e-7:
            rc.BLOB = (round(f, 4), round(k, 4))
            r = sim(2.0, args.pas_sim)
            m0 = r.read_v() > 0.25
            cover = float(m0.mean())
            ok = 0.45 <= cover <= 0.85
            mean = float(np.mean(components(m0))) if ok and m0.any() else 0.0
            if args.tout and not (ok and mean > 5 * maze_mean):
                print(f"F {f:.3f} K {k:.3f} : couverture {cover:.2f}, aire {mean:.0f} -> rejete", flush=True)
            ok = ok and mean > 5 * maze_mean
            if ok:
                for _ in range(100):
                    r.step(20, 2.0)
                moving = int(((r.read_v() > 0.25) != m0).sum())
                path = [float((sim(s, args.pas_sim // 2).read_v() > 0.25).mean()) for s in (1.2, 1.4, 1.6, 1.8)]
                ok = moving > 300 and all(0.05 < x < 0.9 for x in path)
                print(f"F {f:.3f} K {k:.3f} : couverture {cover:.2f}, aire {mean:.0f}, change {moving}, chemin {[round(x, 2) for x in path]}"
                      f" -> {'CANDIDAT' if ok else 'non'}", flush=True)
                if ok:
                    found.append((abs(cover - 0.65), rc.BLOB, cover, mean))
            k += args.pas_k or args.pas
        f += args.pas
    if not found:
        print("Aucun candidat.")
        return 1
    best = min(found)
    print(f"Meilleur (couverture la plus proche de 0,65) : BLOB = {best[1]} (couverture {best[2]:.2f}, aire {best[3]:.0f})")
    return 0


if __name__ == "__main__":
    sys.exit(main())
