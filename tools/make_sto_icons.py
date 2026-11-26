#!/usr/bin/env python3
"""Fabrique les icones de la liste « Champ » du stochastique (issue #229) : `asset/sto_fields/<valeur>.png`, une vignette des VRAIS points
du stochastique sur chaque champ, rendue avec le shader de l'appli (Renderer, contexte OpenGL autonome).

    python tools/make_sto_icons.py [--planche sortie.png]

Deterministe (horloge fixe) : relancer rend les memes images. `--planche` ecrit en plus une planche agrandie de tous les champs, nommes, pour
juger a l'oeil. Couleurs : points a l'accent de la partie OVERLAY (theme.OVERLAY) sur le fond des panneaux (theme.PANEL)."""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "gui"))

import numpy as np                     # noqa: E402
from PIL import Image, ImageDraw       # noqa: E402

import audio2wave_gl as gl             # noqa: E402
import theme as T                      # noqa: E402
from core import sto_fields as sf      # noqa: E402

RENDER = (384, 216)                    # rendu 8 x la vignette, reduit en douceur (bords des points lisses)
QUIET = {"bass": 0.0, "mid": 0.0, "high": 0.0, "rms": 0.0, "beat": 0.0, "since_beat": 9.0, "beats": 0}


def render_field(ctx, fbo, value: str) -> Image.Image:
    p = dict(gl.DEFAULT_PARAMS, bg_mode="pattern", bg_palette="stochastique", bg_sto_field=value, bg_sto_size=9.0, bg_sto_density=0.5,
             bg_color1=T.OVERLAY, bg_color2=T.PANEL, bg_speed=0.0, bg_react=0.0, bg_hue=0.0, bg_angle=30.0, fx_on=[0] * 5, holo_on=0.0,
             auto_master=0.0)
    r = gl.Renderer(ctx, RENDER, None)
    r.draw(fbo, RENDER, QUIET, p, 0.0)
    r.draw(fbo, RENDER, QUIET, p, 1.0)
    w, h = RENDER
    raw = np.frombuffer(fbo.read(components=3), dtype=np.uint8).reshape(h, w, 3)[::-1]
    return Image.fromarray(np.ascontiguousarray(raw), "RGB")


def main(argv: list[str]) -> int:
    sheet_path = Path(argv[argv.index("--planche") + 1]) if "--planche" in argv else None
    ctx = gl.moderngl.create_standalone_context()
    fbo = ctx.framebuffer(color_attachments=[ctx.texture(RENDER, 4)])
    out = ROOT / sf.ICON_DIR
    out.mkdir(parents=True, exist_ok=True)
    big = []
    for value, label, _i, _d in sf.FIELDS:
        img = render_field(ctx, fbo, value)
        img.resize(sf.ICON_SIZE, Image.LANCZOS).save(out / f"{value}.png")
        big.append((label, img))
        print(f"{value}.png")
    if sheet_path is not None:
        cols, (w, h) = 4, RENDER
        sheet = Image.new("RGB", (cols * w, 2 * (h + 18)), T.BG)
        draw = ImageDraw.Draw(sheet)
        for k, (label, img) in enumerate(big):
            x, y = (k % cols) * w, (k // cols) * (h + 18)
            sheet.paste(img, (x, y + 18))
            draw.text((x + 6, y + 3), label, fill=T.FG)
        sheet.save(sheet_path)
        print(sheet_path)
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
