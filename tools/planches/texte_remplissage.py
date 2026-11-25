"""Planche du remplissage du texte (#199) : la vague de colonisation lettre par lettre, en lettres transparentes et en lettres pleines, et la bordure noire."""
from __future__ import annotations

from tools.planches import Cas, Recette

INSTANTS = (1.3, 2.1, 3.1)         # secondes apres le depart de la vague


def _attente(renderer, dessiner) -> float:
    """Laisse le moteur preparer la tuile du motif (quelques secondes simulees), puis rend le depart de la vague."""
    t = 1000.0
    for _ in range(3000):
        if renderer.text_tile is not None and renderer.text_tile.ready:
            break
        t += 1 / 60
        dessiner({}, t)
    else:
        raise RuntimeError("la tuile du motif n'est pas prete apres 3000 images")
    dessiner({}, t + 1 / 60)
    return renderer._fill_t0


_CAS = [Cas(f"{nom}", {"text_fill_clear": clair}, t, None)
        for nom, clair in (("lettres transparentes", 1.0), ("lettres pleines", 0.0)) for t in INSTANTS]
_CAS += [Cas("bordure noire 0 px", {"text_border": 0.0}, 3.1, None), Cas("bordure noire 6 px", {"text_border": 6.0}, 3.1, None)]

RECETTE = Recette("texte-remplissage", "CASUAL RAVERS", (1920, 330),
                  {"text_scale": 0.95, "text_fill_on": 1.0, "text_fill_every": 120.0, "text_ola_on": 0.0}, _CAS, _attente)
