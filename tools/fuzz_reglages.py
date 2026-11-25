"""Fuzz des reglages (#195), rendu : des jeux de reglages tires depuis la table (core/fuzz.py) sont rendus avec le vrai moteur ; un jeu echoue sur une
exception, une erreur OpenGL ou une image impossible (uniforme alors que rien ne cache le logo).

    python tools/fuzz_reglages.py                        # 20 jeux, graine 2026
    python tools/fuzz_reglages.py --cas 500 --graine 7   # balayage large
    python tools/fuzz_reglages.py --graine 7 --cas 1 --depuis 42   # rejoue le cas 42 (et tous les precedents, sans les rapporter : l'etat
                                                                   # du moteur passe d'un jeu au suivant)

Un echec est affiche sur une ligne : graine, cas, source, raison, reglages. Code de sortie 1 s'il y en a. Fiche : docs/fonctions/outillage.md.
"""
from __future__ import annotations

import argparse
import sys
import tempfile
from pathlib import Path
from typing import Callable, NamedTuple

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

SIZE = (640, 360)
SILENCE = {"bass": 0.0, "mid": 0.0, "high": 0.0, "rms": 0.0, "beat": 0.0, "since_beat": 9.0, "beats": 0}
SOURCES = ("texte", "image")
GRAINE_DEFAUT, CAS_DEFAUT = 2026, 20
PRESENCE = {"logo_opacity": 1.0, "text_scale": 0.5, "logo_scale": 0.5, "melt_on": 0.0}     # epingles sur les cas pairs : sinon le tirage cache presque toujours le logo (la fonte le dissout)
DEPART = 1000.0
SAUT = 2.0                       # secondes simulees entre les deux images dessinees (les effets de duree s'installent)


class Echec(NamedTuple):
    graine: int
    indice: int
    source: str
    raison: str
    reglages: str


def _cache_logo(case: dict, source: str) -> bool:
    """Vrai si le jeu peut legitimement cacher le logo (opacite, master, taille, fonte profonde, vague de lettres, remplissage transparent) : on n'exige alors pas d'image visible."""
    if float(case.get("logo_opacity", 1.0)) <= 0.2 or float(case.get("overlay_master", 1.0)) <= 0.2:
        return True
    if float(case.get("text_scale" if source == "texte" else "logo_scale", 1.0)) <= 0.2:
        return True
    if float(case.get("melt_on", 0.0)) >= 0.5 and float(case.get("melt_depth", 0.0)) >= 0.8:
        return True                                                              # la fonte acide profonde dissout le logo en quelques secondes : c'est son role
    if source == "texte" and (float(case.get("text_ola_on", 0.0)) >= 0.5 or (float(case.get("text_fill_on", 0.0)) >= 0.5 and float(case.get("text_fill_clear", 1.0)) >= 0.5)):
        return True
    return False


def fuzz(graine: int, n: int, depuis: int = 0, sources: tuple[str, ...] = SOURCES, *, injecter: Callable | None = None) -> list[Echec]:
    """Rend les jeux `depuis` a `depuis + n - 1` de la graine, pour chaque source ; rend la liste des echecs. `injecter(renderer, k)` est un crochet de test
    appele avant chaque jeu (il peut lever pour simuler un echec)."""
    import numpy as np
    import audio2wave_gl as gl
    from core import fuzz as core_fuzz
    from engine import textfill
    from engine.render import render_text

    jeux = core_fuzz.cases(graine, depuis + n)               # tout le prefixe est rendu : l'etat du moteur passe d'un jeu au suivant, un cas seul ne se rejoue pas a l'identique
    echecs: list[Echec] = []
    saved = textfill.tile_cache_path
    tmp = tempfile.TemporaryDirectory()
    try:
        textfill.tile_cache_path = lambda: Path(tmp.name) / "tuile.npz"          # jamais le cache de l'utilisateur
        ctx = gl.moderngl.create_standalone_context()
        fbo = ctx.framebuffer(color_attachments=[ctx.texture(SIZE, 4)])
        for source in sources:
            r = gl.Renderer(ctx, SIZE, None)
            r.video_tex.write(bytes([90]) * (SIZE[0] * SIZE[1] * 3))
            if source == "texte":
                r.set_logo(render_text("FUZZ", "auto", "#ffffff", "center", 128), "", "text")
            else:
                logo = np.zeros((120, 200, 4), np.uint8)
                logo[...] = (255, 40, 40, 255)
                r.set_logo(logo, "", "image")
            t = DEPART
            for k, case in enumerate(jeux):
                if k % 2 == 0:
                    case = dict(case, **PRESENCE)                                # un cas sur deux : le logo ne peut pas etre cache (on verifie alors l'image)
                params = dict(gl.DEFAULT_PARAMS, logo_x=0.5, logo_y=0.5)
                params.update(case)
                raison = None
                try:
                    if injecter is not None:
                        injecter(r, k)
                    r.draw(fbo, SIZE, SILENCE, params, t)
                    t += SAUT
                    r.draw(fbo, SIZE, SILENCE, params, t)
                    err = getattr(ctx, "error", "GL_NO_ERROR")
                    img = np.frombuffer(fbo.read(components=3), np.uint8)
                    if err != "GL_NO_ERROR":
                        raison = f"erreur OpenGL : {err}"
                    elif float(img.std()) == 0.0 and not _cache_logo(case, source):
                        raison = "image impossible : uniforme alors que rien ne cache le logo"
                except Exception as exc:                                         # un jeu qui plante ne doit pas arreter les suivants
                    raison = f"exception : {type(exc).__name__}: {exc}"
                if raison is not None and k >= depuis:
                    echecs.append(Echec(int(graine), k, source, raison, core_fuzz.describe(case)))
        ctx.release()
    finally:
        textfill.tile_cache_path = saved
        tmp.cleanup()
    return sorted(echecs, key=lambda e: (e.indice, e.source))


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Fuzz des reglages : rend des jeux de reglages tires depuis la table.")
    ap.add_argument("--graine", type=int, default=GRAINE_DEFAUT)
    ap.add_argument("--cas", type=int, default=CAS_DEFAUT, help="nombre de jeux")
    ap.add_argument("--depuis", type=int, default=0, help="indice du premier jeu (pour rejouer un cas)")
    args = ap.parse_args(argv)
    echecs = fuzz(args.graine, args.cas, args.depuis)
    for e in echecs:
        print(f"graine={e.graine} cas={e.indice} source={e.source} : {e.raison} ; reglages : {e.reglages}")
    print(f"{args.cas} jeu(x) x {len(SOURCES)} sources, {len(echecs)} echec(s)")
    return 1 if echecs else 0


if __name__ == "__main__":
    sys.exit(main())
