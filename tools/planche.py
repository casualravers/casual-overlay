"""Planche de preuves (#195) : rend des cas avec le vrai moteur et les assemble en une planche PNG, pour valider un rendu sur image avant de lancer l'appli.

    python tools/planche.py --liste                      # les recettes
    python tools/planche.py texte-remplissage            # output/planches/texte-remplissage.png
    python tools/planche.py texte-remplissage --sortie C:\\temp\\x.png

Une recette (tools/planches/<nom>.py, voir tools/planches/__init__.py) liste des cas : des reglages et un instant. Fond synthetique (degrade raye),
audio silencieux, aucune comparaison de pixels (les GPU different) : c'est pour l'oeil. Fiche : docs/fonctions/outillage.md.
"""
from __future__ import annotations

import argparse
import importlib
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tools.planches import Cas, Recette  # noqa: E402  (apres sys.path)

BANDE = 22                       # hauteur de la bande d'etiquette au-dessus de chaque image (pixels)
LARGEUR_MAX = 1600               # largeur de la planche rendue (reduite au-dela)
ZOOM = 3                         # agrandissement des zooms
SILENCE = {"bass": 0.0, "mid": 0.0, "high": 0.0, "rms": 0.0, "beat": 0.0, "since_beat": 9.0, "beats": 0}
DEPART = 1000.0                  # instant d'origine par defaut (les effets sont mesures depuis leur propre depart)
PAS = 1 / 60                     # une image simulee


def lister() -> list[str]:
    """Noms des recettes de tools/planches/ (un module par recette ; `_` du fichier devient `-` du nom)."""
    return sorted(p.stem.replace("_", "-") for p in (ROOT / "tools" / "planches").glob("*.py") if not p.stem.startswith("_"))


def charger(nom: str) -> Recette:
    """La recette `nom`, ou ValueError(« recette inconnue : nom »)."""
    if nom not in lister():
        raise ValueError(f"recette inconnue : {nom}")
    return importlib.import_module("tools.planches." + nom.replace("-", "_")).RECETTE


def _fond(size: tuple[int, int]):
    """Fond neutre : degrade raye synthetique (RGB uint8, haut en premier)."""
    import numpy as np
    gx, gy = np.meshgrid(np.linspace(0, 1, size[0]), np.linspace(0, 1, size[1]))
    bg = np.stack([18 + 40 * gx, 22 + 30 * gy, 70 + 90 * (1 - gx) * (0.4 + 0.6 * gy)], axis=2)
    bg *= (0.7 + 0.3 * np.sin((gx * 38 + gy * 14) * np.pi) ** 2)[..., None]
    return np.clip(bg, 0, 255).astype(np.uint8)


def _legende(cas: Cas) -> str:
    regl = ", ".join(f"{k}={v}" for k, v in cas.reglages.items())
    return f"{cas.etiquette}  (t = {cas.instant:g} s{' ; ' + regl if regl else ''})"


def rendre(recette: Recette, sortie: Path, cache_dir: Path | None = None) -> Path:
    """Rend tous les cas de `recette` (un seul Renderer) et ecrit la planche PNG `sortie` (et `<nom>_zoom.png` a cote s'il y a des zooms).
    Le cache disque de la tuile du texte est redirige vers `cache_dir` (dossier temporaire par defaut) le temps du rendu."""
    import numpy as np
    from PIL import Image, ImageDraw
    import audio2wave_gl as gl
    from engine import textfill
    from engine.render import render_text

    sortie = Path(sortie)
    sortie.parent.mkdir(parents=True, exist_ok=True)
    saved = textfill.tile_cache_path
    tmp = None
    try:
        if cache_dir is None:
            tmp = tempfile.TemporaryDirectory()
            cache_dir = Path(tmp.name)
        textfill.tile_cache_path = lambda: Path(cache_dir) / "tuile.npz"
        size = tuple(recette.taille)
        ctx = gl.moderngl.create_standalone_context()
        fbo = ctx.framebuffer(color_attachments=[ctx.texture(size, 4)])
        r = gl.Renderer(ctx, size, None)
        r.video_tex.write(np.ascontiguousarray(_fond(size)[::-1]).tobytes())
        r.set_logo(render_text(recette.texte, "auto", "#ffffff", "center", 128), "", "text")
        base = dict(gl.DEFAULT_PARAMS, logo_x=0.5, logo_y=0.5, bg_mode="live", fx_on=[0, 0, 0, 0, 0], holo_on=0.0,
                    logo_opacity=1.0, logo_glow=0.0, logo_pulse=0.0, logo_jitter=0.0)
        base.update(recette.base)

        def dessiner(reglages: dict, t: float):
            r.draw(fbo, size, SILENCE, dict(base, **reglages), t)
            return np.frombuffer(fbo.read(components=3), np.uint8).reshape(size[1], size[0], 3)[::-1]
        origine = recette.attente(r, dessiner) if recette.attente else DEPART
        images = [dessiner(c.reglages, origine + c.instant) for c in recette.cas]
        ctx.release()
    finally:
        textfill.tile_cache_path = saved
        if tmp is not None:
            tmp.cleanup()

    haut = size[1] + BANDE
    feuille = Image.new("RGB", (size[0], haut * len(images)), (14, 14, 14))
    crayon = ImageDraw.Draw(feuille)
    for i, (cas, img) in enumerate(zip(recette.cas, images)):
        feuille.paste(Image.fromarray(np.ascontiguousarray(img)), (0, i * haut + BANDE))
        crayon.text((8, i * haut + 5), _legende(cas), fill=(255, 230, 120))
    if feuille.width > LARGEUR_MAX:
        feuille = feuille.resize((LARGEUR_MAX, round(feuille.height * LARGEUR_MAX / feuille.width)), Image.LANCZOS)
    feuille.save(sortie)
    zooms = [Image.fromarray(np.ascontiguousarray(img[c.zoom[1]:c.zoom[3], c.zoom[0]:c.zoom[2]])).resize(
        ((c.zoom[2] - c.zoom[0]) * ZOOM, (c.zoom[3] - c.zoom[1]) * ZOOM), Image.NEAREST) for c, img in zip(recette.cas, images) if c.zoom]
    if zooms:
        largeur = max(z.width for z in zooms)
        pile = Image.new("RGB", (largeur, sum(z.height for z in zooms) + 6 * (len(zooms) - 1)), (14, 14, 14))
        y = 0
        for z in zooms:
            pile.paste(z, (0, y))
            y += z.height + 6
        pile.save(sortie.with_name(sortie.stem + "_zoom.png"))
    return sortie


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Planche de preuves : rend les cas d'une recette avec le vrai moteur.")
    ap.add_argument("recette", nargs="?", help="nom de la recette (voir --liste)")
    ap.add_argument("--liste", action="store_true", help="liste les recettes")
    ap.add_argument("--sortie", help="fichier PNG de sortie (defaut : output/planches/<recette>.png)")
    args = ap.parse_args(argv)
    if args.liste or not args.recette:
        print("\n".join(lister()))
        return 0
    try:
        recette = charger(args.recette)
    except ValueError as exc:
        print(str(exc), file=sys.stderr)
        return 2
    sortie = Path(args.sortie) if args.sortie else ROOT / "output" / "planches" / f"{args.recette}.png"
    print(f"rendu de {len(recette.cas)} cas ...")
    print(rendre(recette, sortie))
    return 0


if __name__ == "__main__":
    sys.exit(main())
