"""Palette stochastique : les champs qui decident ou les points s'allument (issue #229).

Python pur (stdlib seulement). Ordre = ordre de la liste deroulante ; `index` = numero du champ dans gl_shaders/sto_field.frag (les trois
champs d'origine gardent leur numero : noise 0, waves 1, gradient 2). Icones : asset/sto_fields/<valeur>.png (tools/make_sto_icons.py).
"""
from __future__ import annotations

FIELDS: tuple[tuple[str, str, int, str], ...] = (
    ("noise", "Taches", 0, "grandes taches molles qui derivent (le champ d'origine)"),
    ("organic", "Organique", 3, "formes douces et naturelles (bruit de Perlin)"),
    ("cells", "Cellules", 4, "amas ronds, bulles (bruit cellulaire de Worley)"),
    ("network", "Reseau", 5, "bords des cellules : ecailles, nid d'abeille"),
    ("veins", "Veines", 6, "lignes fines qui serpentent (cretes du bruit)"),
    ("marble", "Marbre", 7, "volutes qui coulent (domaine deforme)"),
    ("waves", "Vagues", 1, "bandes qui se propagent dans la direction de l'angle"),
    ("gradient", "Degrade", 2, "degrade aller-retour"),
)
VALUES = tuple(v for v, _l, _i, _d in FIELDS)
DEFAULT = "noise"
ICON_SIZE = (48, 27)                # vignette de la liste deroulante (16:9)
ICON_DIR = "asset/sto_fields"       # relatif a la racine du depot / du dossier portable


def index(value) -> int:
    """Numero du champ dans le shader ; valeur inconnue = Taches."""
    for v, _l, i, _d in FIELDS:
        if v == value:
            return i
    return 0


def label(value) -> str:
    for v, lab, _i, _d in FIELDS:
        if v == value:
            return lab
    return FIELDS[0][1]


def describe(value) -> str:
    for v, _l, _i, desc in FIELDS:
        if v == value:
            return desc
    return FIELDS[0][3]
