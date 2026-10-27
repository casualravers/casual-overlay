#!/usr/bin/env python3
"""Fabrique `asset/logo_chargement.png` : la forme d'onde d'UN kick (attaque brutale, decroissance), en blanc, avec une aberration
chromatique (copies decalees aux couleurs des visuels audio2wave) et un tout petit peu de glitch (deux fines tranches decalees).

    python tools/make_logo_chargement.py [sortie.png]

Deterministe (graine fixe) : relancer rend la meme image. Fond transparent, affiche par l'ecran de chargement (gui/gui_splash.py)."""
from __future__ import annotations

import math
import random
import sys
from pathlib import Path

import numpy as np
from PIL import Image

W, H = 840, 300                    # 2 x la taille affichee (reduit en douceur par l'ecran de chargement)
COL = 6                            # largeur d'une colonne : le rendu « barres » des visuels de reference
MID = H // 2 + 6
ATTACK_X = 120                     # le kick part ici

# Couleurs des franges (reprises des images de reference), de l'arriere vers l'avant.
FRINGES = (
    ((190, 0, 160), -16, 1.09),    # magenta : le plus decale a gauche, un peu plus haut
    ((110, 70, 255), -10, 1.06),   # violet
    ((0, 170, 200), 9, 1.05),      # cyan, de l'autre cote
    ((90, 255, 70), -5, 1.04),     # vert
    ((160, 190, 0), 13, 1.06),     # jaune-vert
)


def kick_envelope(rng: random.Random) -> np.ndarray:
    """Demi-hauteur (pixels) de chaque colonne : montee tres rapide, puis decroissance en cloche irreguliere comme un vrai kick."""
    n = W // COL
    out = np.zeros(n)
    for i in range(n):
        x = i * COL
        t = (x - ATTACK_X) / 330.0
        if t < -0.06:
            out[i] = 2 + 3 * math.exp(-(x - ATTACK_X) ** 2 / 4000.0) * 0.0 + rng.uniform(0, 2)
        elif t < 0:
            out[i] = 6 + (t + 0.06) / 0.06 * 62                       # attaque quasi verticale
        else:
            body = math.exp(-t * 2.2) * (0.78 + 0.22 * math.cos(t * 11.0))     # decroissance + battements du corps
            thump = 0.16 * math.exp(-((t - 0.95) ** 2) / 0.05)                  # petit retour de la caisse
            out[i] = 4 + 104 * max(body, 0.0) + 22 * thump
        out[i] *= rng.uniform(0.93, 1.07)                                        # grain des colonnes
    return out


def draw_columns(canvas: np.ndarray, heights: np.ndarray, up_down: float, color, dx: int, rng: random.Random, jitter: float) -> None:
    """Colonnes pleines, symetriques autour de MID (la silhouette du kick), decalees de `dx` pixels."""
    for i, h in enumerate(heights):
        x0 = i * COL + dx
        if x0 + COL <= 0 or x0 >= W:
            continue
        hh = h * (1.0 + rng.uniform(-jitter, jitter))
        top = int(MID - hh * up_down)
        bot = int(MID + hh * (2.0 - up_down))
        canvas[max(top, 0):min(bot, H), max(x0, 0):min(x0 + COL, W)] = color


def make() -> Image.Image:
    rng = random.Random(7)
    heights = kick_envelope(rng)
    layers = []
    for color, dx, boost in FRINGES:
        layer = np.zeros((H, W, 4), dtype=np.uint8)
        paint = np.zeros((H, W, 3), dtype=np.uint8)
        mask = np.zeros((H, W), dtype=bool)
        tmp = np.zeros((H, W), dtype=np.uint8)
        draw_columns(tmp, heights * boost, 1.0 + rng.uniform(-0.04, 0.04), 255, dx, rng, 0.10)
        mask = tmp > 0
        paint[mask] = color
        layer[..., :3] = paint
        layer[..., 3] = np.where(mask, 255, 0)
        layers.append(layer)
    white = np.zeros((H, W), dtype=np.uint8)
    draw_columns(white, heights, 1.0, 255, 0, rng, 0.0)
    main = np.zeros((H, W, 4), dtype=np.uint8)
    main[white > 0] = (255, 255, 255, 255)
    stack = layers + [main]                                    # les franges derriere, le blanc devant

    out = np.zeros((H, W, 4), dtype=np.uint8)
    for layer in stack:                                        # peinture « par-dessus » : la derniere gagne
        m = layer[..., 3] > 0
        out[m] = layer[m]

    # Glitch : deux fines tranches horizontales decalees (une avec ses franges seules, l'autre en blanc)
    for y0, y1, shift in ((MID - 58, MID - 49, 24), (MID + 28, MID + 35, -30), (MID - 22, MID - 18, 14)):
        out[y0:y1] = np.roll(out[y0:y1], shift, axis=1)
    return Image.fromarray(out, "RGBA")


def main() -> None:
    dest = Path(sys.argv[1]) if len(sys.argv) > 1 else Path(__file__).resolve().parent.parent / "asset" / "logo_chargement.png"
    img = make()
    bbox = img.getbbox()
    if bbox:
        img = img.crop((max(bbox[0] - 12, 0), max(bbox[1] - 12, 0), min(bbox[2] + 12, W), min(bbox[3] + 12, H)))
    dest.parent.mkdir(parents=True, exist_ok=True)
    img.save(dest, optimize=True)
    print(f"{dest} ({img.size[0]}x{img.size[1]})")


if __name__ == "__main__":
    main()
