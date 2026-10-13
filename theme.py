"""Jetons de design de la fenetre de reglages : une seule source pour les couleurs, rayons, espacements et polices.

Direction : sombre sobre, surfaces en niveaux de gris bleutes, UN accent par partie (LIVE turquoise, OVERLAY violet, SCENES ambre).
Le fond (`BG`) et le panneau (`PANEL`) sont ceux d'audio2wave (`common.GUI_*`) pour que ses panneaux, que l'on ne modifie pas, se
fondent avec les notres. Stdlib seulement.
"""
from __future__ import annotations

# Surfaces, du plus sombre au plus clair
BG = "#1b1e27"          # fenetre (= audio2wave GUI_BG)
PANEL = "#242835"       # panneau / champ (= audio2wave GUI_PANEL_BG)
CARD = "#2c3142"        # bouton neutre, carte
HOVER = "#363c50"       # survol d'un element neutre
LINE = "#343a4d"        # filets et bordures discrets
TROUGH = "#12151c"      # piste des curseurs

# Texte
FG = "#e7e9ee"
MUTED = "#8993a8"
FAINT = "#5d667b"       # desactive

# Accents, un par partie, et le texte lisible dessus
LIVE = "#3b82f6"
OVERLAY = "#b392ff"
SCENE = "#ffcf6b"
ON_ACCENT = "#0b1018"

# Formes
RADIUS = 7              # rayon des boutons et champs (px)
CHECK_RADIUS = 4
PAD_X, PAD_Y = 12, 4    # marges internes d'un bouton (px)

# Typographie (Segoe UI partout : installee sur tout Windows)
FONT = ("Segoe UI", 10)
FONT_BOLD = ("Segoe UI", 10, "bold")
FONT_SMALL = ("Segoe UI", 9)
FONT_KICKER = ("Segoe UI", 8, "bold")      # petits titres de section
FONT_TITLE = ("Segoe UI", 11, "bold")      # titres des deux grandes parties


def hex_to_rgb(color: str) -> tuple[int, int, int]:
    color = color.lstrip("#")
    if len(color) == 3:
        color = "".join(c * 2 for c in color)
    return int(color[0:2], 16), int(color[2:4], 16), int(color[4:6], 16)


def rgb_to_hex(rgb) -> str:
    return "#%02x%02x%02x" % tuple(max(0, min(255, int(round(v)))) for v in rgb)


def mix(a: str, b: str, t: float) -> str:
    """Melange de deux couleurs : t = 0 -> a, t = 1 -> b."""
    ra, rb = hex_to_rgb(a), hex_to_rgb(b)
    return rgb_to_hex([x + (y - x) * t for x, y in zip(ra, rb)])


def lighten(color: str, t: float = 0.14) -> str:
    return mix(color, "#ffffff", t)


def darken(color: str, t: float = 0.14) -> str:
    return mix(color, "#000000", t)
