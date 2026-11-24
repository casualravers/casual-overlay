"""Panneau du visualizer du cerveau (issue #192) : ce que l'overlay entend et decide, relu dans `Session.brain` (core/brain.py).

Place : le panneau etendu du Mode VJ, a l'emplacement de l'espace de controle global (`master_box`) ; un bouton « brain / VJ » de la
tete du panneau bascule l'un et l'autre. Bouton « detach » : fenetre a part (Toplevel), fermer la fenetre = la raccrocher (`dock`).

Contenu : barre (vues, causes, pause, detach), en-tete (BPM, phase, page, score, fins de cycles, prochaine scene), la vue (un Canvas),
legende et pastilles pour masquer des pistes, fil des 4 derniers evenements, jauges (sources, auto, sante). Les vues ne font que
projeter l'instantane (`core.brain.snapshot`) ; interface d'une vue : `View(canvas)`, `.layout(w, h)`, `.update(snap, causes, hidden,
now)`, `.hover(x, y) -> str | None` ; registre `VIEWS` (les vraies vues s'y inscrivent, `BlankView` en attendant). Sans grille
(`snap["grid"] is False`) : `FreeView`, frise en secondes, quelle que soit la vue choisie.

Cout : `tick` (ordonnanceur toutes les 50 ms ; les quatre vues a 10 Hz, `VIEW_TICK_MS`) ne fait rien quand le panneau n'est pas
visible ; pendant un REC il dessine une derniere fois « fige pendant le REC » puis plus rien. Budget mesure : docs/fonctions/cerveau.md. Libelles en anglais, raisons en francais (contraintes du plan). Reglages `brain_*` : portee « device » (ni presets, ni scenes,
ni memoires de masters), sauves tout seuls dans brain_ui.json.
"""
from __future__ import annotations

import math
import re
import time
import tkinter as tk

from core import brain as core_brain
from core import params as core_params
from core import sessionlog

PANEL_BG = "#07080c"
FG = "#e8ecff"
MUTED = "#8a93c4"
DIM = "#5a6390"
REASON_FG = "#c9d1ff"
BTN_BG = "#11131c"
BTN_ON = "#2ef2c8"
MISS_FG = "#ff5a5a"
TIP_BG, TIP_BORDER = "#1a1d2b", "#3a4170"
PHASE_COLORS = {"groove": "#2ef2c8", "build": "#ffb347", "peak": "#ff3d7f", "break": "#6b8bff", "ambient": "#5a6390", "silence": "#5a6390"}
PHASE_TINT = {"groove": (6, 18, 20), "build": (24, 16, 6), "peak": (28, 6, 17), "break": (8, 10, 26)}
DEFAULT_TINT = (7, 8, 12)

VIEW_NAMES = ("iso", "mandala", "tunnel", "sismo")
TICK_MS = 50                                   # ~20 Hz
VIEW_TICK_MS = {"iso": 100, "mandala": 100, "tunnel": 100, "sismo": 100}   # cadence propre a une vue (budget mesure, tache 9 : toutes a 10 Hz)
BOX_W = 640                                    # largeur du panneau raccroche quand l'espace VJ n'en donne pas (pixels)
TOGGLE_W = 6                                   # largeur fixe (caracteres) des boutons dont le texte change
DETACH_W = 7
ANGLE_BOX_W = 124                              # place reservee au curseur d'angle de la vue iso dans la barre (pixels)
FROZEN_TEXT = "fige pendant le REC"
NO_TEMPO = "tempo non detecte"
WINDOW_TITLE = "casual-overlay - cerveau"
FONT = ("Consolas", 9)
FONT_HEAD = ("Consolas", 10)
FONT_SMALL = ("Consolas", 8)
GEOM_SAVE_MS = 500                             # geometrie de la fenetre detachee sauvee 0,5 s apres le dernier deplacement
GEOM_RE = re.compile(r"^\d+x\d+([+-]-?\d+[+-]-?\d+)?$")


BAND_CELLS = 4                                 # jauge sources : 4 cases par bande (texte a chasse fixe : largeur constante)
BAND_ON, BAND_OFF = "█", "·"
SOURCE_W = 6                                   # nom de source le plus long, en anglais (« highs »)
# libelles anglais des sources audio par couche (core.audio_layers les nomme en francais)
SOURCE_EN = {"tout": "all", "basses": "bass", "mediums": "mids", "aigus": "highs", "kick": "kick", "frappes": "hits", "rien": "none"}


def band_bar(v) -> str:
    """Barre texte d'un niveau 0..1 sur BAND_CELLS cases."""
    try:
        n = max(0, min(BAND_CELLS, round(float(v) * BAND_CELLS)))
    except (TypeError, ValueError):
        n = 0
    return BAND_ON * n + BAND_OFF * (BAND_CELLS - n)


def sources_text(src: dict, bands: dict) -> str:
    """Jauge « sources » : par couche, la source puis ce que la couche voit (B basses, H aigus, K kick). Toujours 3 lignes de meme
    longueur (les boutons ne bougent pas)."""
    lines = ["sources"]
    for lay, name in (("bg", "bg  "), ("logo", "logo")):
        vec = tuple(bands.get(lay) or (0.0, 0.0, 0.0))[:3]
        vec = vec + (0.0,) * (3 - len(vec))
        raw = str(src.get(lay) or "-")
        s = SOURCE_EN.get(raw, raw)[:SOURCE_W].ljust(SOURCE_W)
        lines.append(f"{name} <- {s} " + " ".join(f"{k} {band_bar(v)}" for k, v in zip("BHK", vec)))
    width = max(len(x) for x in lines)
    return "\n".join(x.ljust(width) for x in lines)


BUILD_W = 32                                   # « build 125 s  strobe in 2.5 s » et ses variantes tiennent dans cette largeur


def build_text(h: dict) -> str:
    """Montee en cours de l'en-tete : duree, puis strobe a venir (delai ou cadence) quand le strobe auto l'annoncera. Largeur fixe
    (BUILD_W) : les elements a droite ne bougent pas quand le texte change ; vide sans montee."""
    b = h.get("build_s")
    if b is None:
        return ""
    txt = f"   build {b:.0f} s"
    if h.get("strobe_hz") is not None:
        txt += f"  strobe {h['strobe_hz']:.1f} Hz"
    elif h.get("strobe_in_s") is not None:
        txt += f"  strobe in {h['strobe_in_s']:.1f} s"
    return txt.ljust(BUILD_W)


def tint(phase: str | None) -> str:
    """Couleur de fond du canvas selon la phase (teintes des contraintes du plan)."""
    return "#%02x%02x%02x" % PHASE_TINT.get(phase or "", DEFAULT_TINT)


def _mmss(seconds: float) -> str:
    seconds = max(0, int(seconds))
    return f"{seconds // 60}:{seconds % 60:02d}"


# ---------------------------------------------------------------------------------------------------------------------------
# Vues communes : vide (en attendant les vraies) et frise libre (sans grille)
# ---------------------------------------------------------------------------------------------------------------------------

class BlankView:
    """Vue vide (les vraies vues s'inscrivent dans VIEWS) : une mention au centre."""

    def __init__(self, canvas: tk.Canvas) -> None:
        self.canvas = canvas
        self._text = canvas.create_text(0, 0, text="", fill=DIM, font=FONT)

    def layout(self, w: int, h: int) -> None:
        self.canvas.coords(self._text, w / 2, h / 2)

    def update(self, snap: dict, causes: bool, hidden: set, now: float) -> None:
        pass

    def hover(self, x: float, y: float) -> str | None:
        return None


class FreeView:
    """Sans grille : frise des FREE_S dernieres secondes, une ligne par instrument et par piste, points a leur age (le present a droite).
    Les points sont crees une fois (reserve) puis deplaces ou caches."""

    ROWS = core_brain.INSTRUMENTS + core_brain.TRACKS
    POOL = 240
    LABEL_W = 60

    def __init__(self, canvas: tk.Canvas) -> None:
        self.canvas = canvas
        self.w = self.h = 1
        self._labels = {r: canvas.create_text(4, 0, text=core_brain.label(r), anchor="w", fill=core_brain.COLORS.get(r, FG), font=FONT)
                        for r in self.ROWS}
        self._lines = {r: canvas.create_line(0, 0, 0, 0, fill="#161a28") for r in self.ROWS}
        self._dots = [canvas.create_oval(0, 0, 0, 0, state="hidden", outline="") for _ in range(self.POOL)]
        self._used = 0
        self._fill: list = [None] * self.POOL                     # famille dont chaque point porte la couleur
        self._hid_sig = None
        self._note = canvas.create_text(0, 0, text=NO_TEMPO, fill=MUTED, font=FONT_HEAD, anchor="ne")
        self._points: list = []                                    # (x, y, texte) des points montres, pour le survol

    def row_y(self, row: str) -> float:
        top, bottom = 22.0, max(30.0, self.h - 8.0)
        i = self.ROWS.index(row)
        return top + (bottom - top) * (i + 0.5) / len(self.ROWS)

    def x_of(self, age: float) -> float:
        left, right = self.LABEL_W, max(self.LABEL_W + 10.0, self.w - 10.0)
        return right - (right - left) * min(max(age, 0.0), core_brain.FREE_S) / core_brain.FREE_S

    def layout(self, w: int, h: int) -> None:
        self.w, self.h = max(1, w), max(1, h)
        tight = (self.row_y(self.ROWS[1]) - self.row_y(self.ROWS[0])) < 10      # panneau bas : les couleurs de la legende suffisent
        for r in self.ROWS:
            y = self.row_y(r)
            self.canvas.coords(self._labels[r], 4, y)
            self.canvas.itemconfigure(self._labels[r], state="hidden" if tight else "normal")
            self.canvas.coords(self._lines[r], self.LABEL_W, y, self.w - 10, y)
        self.canvas.coords(self._note, self.w - 8, 4)

    def update(self, snap: dict, causes: bool, hidden: set, now: float) -> None:
        cv = self.canvas
        pts = []
        for age, fam, strength in snap.get("free", []):
            if fam in hidden or fam not in self.ROWS or len(pts) >= self.POOL:
                continue
            pts.append((self.x_of(age), self.row_y(fam), fam, strength, age))
        for i, (x, y, fam, strength, age) in enumerate(pts):
            r = 2.0 + 3.0 * max(0.0, min(1.0, float(strength)))
            cv.coords(self._dots[i], x - r, y - r, x + r, y + r)
            if self._fill[i] != fam or i >= self._used:            # couleur et etat seulement quand ils changent (budget, tache 9)
                self._fill[i] = fam
                cv.itemconfigure(self._dots[i], fill=core_brain.COLORS.get(fam, FG), state="normal")
        for i in range(len(pts), self._used):
            cv.itemconfigure(self._dots[i], state="hidden")
        self._used = len(pts)
        hsig = tuple(sorted(hidden))
        if hsig != self._hid_sig:
            self._hid_sig = hsig
            for r in self.ROWS:
                cv.itemconfigure(self._labels[r], fill=DIM if r in hidden else core_brain.COLORS.get(r, FG))
        self._points = [(x, y, f"{core_brain.label(fam)} il y a {age:.1f} s") for x, y, fam, _s, age in pts]

    def hover(self, x: float, y: float) -> str | None:
        best, dist = None, 8.0
        for px, py, text in self._points:
            d = ((px - x) ** 2 + (py - y) ** 2) ** 0.5
            if d < dist:
                best, dist = text, d
        return best


# ---------------------------------------------------------------------------------------------------------------------------
# Vue isometrique : la page (64 pas) en relief, le temps part de devant
# ---------------------------------------------------------------------------------------------------------------------------

ISO_R = 3.0                                    # largeur d'une rangee (pas = 1 unite) ; 3 et non 4,2 (maquette) : piliers carres plus serres,
                                               # et les libelles des pistes (un par rangee, en escalier) ne se chevauchent pas
ISO_CELL = 0.8                                 # cote de la base carree d'un pilier d'instrument (unites)
ISO_FX_CELL = 0.6                              # cote d'un effet (strobe et foudre plus fins, scene : plaque carree de la largeur de la rangee)
ISO_A0 = 5.2                                   # echelle de reference de la maquette (z en pixels a cette echelle)
ISO_X_MIN, ISO_X_MAX = -67.0, 12 * ISO_R + 3.1  # etendue horizontale (x - y) en unites, textes compris
ISO_S_MAX = 12 * ISO_R + 64.1                  # etendue verticale maximale (x + y) en unites
ISO_FADE_S = 0.35                              # fondu des fantomes en debut de page
ISO_ANGLE = (18, 45)
ISO_MAX_CAUSES = 96
ISO_VEIL_LINES = 8                             # lignes de relief du voile, une par piste d'effet (comme la maquette)
ISO_PAGE = core_brain.PAGE_STEPS


def _rgb(color: str) -> tuple[int, int, int]:
    return int(color[1:3], 16), int(color[3:5], 16), int(color[5:7], 16)


def blend(fg: str, alpha: float, bg: str) -> str:
    """`fg` a l'opacite `alpha` sur le fond `bg` (le canvas Tk n'a pas d'alpha : on melange vers la couleur du fond)."""
    a = max(0.0, min(1.0, alpha))
    f, b = _rgb(fg), _rgb(bg)
    return "#%02x%02x%02x" % tuple(round(b[i] + (f[i] - b[i]) * a) for i in range(3))


def shade(color: str, k: float) -> str:
    r, g, b = _rgb(color)
    return "#%02x%02x%02x" % (round(r * k), round(g * k), round(b * k))


class _IsoCell:
    """Elements d'une case : 3 polygones et 3 lignes reutilises selon le mode (pilier, fil de fer, croix)."""
    __slots__ = ("polys", "lines", "mode", "sig", "pt")

    def __init__(self, polys, lines) -> None:
        self.polys, self.lines, self.mode, self.sig, self.pt = polys, lines, None, None, None


class IsoView:
    """Page en 3D isometrique. Les elements du canvas sont tous crees a la construction, dans l'ordre de profondeur (du fond vers l'avant),
    puis reutilises : `update` ne touche que les cases dont le statut a change, la bande des phases, le voile, les pointilles des causes et
    le plan du present. Pas d'alpha sous Tk : les couleurs sont melangees vers le fond de la phase (`blend`) ; le plan du present utilise un
    stipple."""

    angle = 30                                 # retenu d'une creation de la vue a l'autre

    def __init__(self, canvas: tk.Canvas) -> None:
        self.canvas = canvas
        self.w = self.h = 1
        self.geo = 0
        self._compute(1, 1)
        self._last = None
        self._hov: dict = {}
        self._fx_sig: dict = {}
        self._cause_sig = None
        self._veil_sig = None
        self._veil_on = False
        self._hid_sig = None
        self._plane_bg = None
        cv = canvas
        R = ISO_R
        # fond : grilles (statiques), bande des phases
        self._grid = []
        for lo, hi in ((0, 3), (4, 12)):
            for j in range(lo, hi + 1):
                self._grid.append((cv.create_line(0, 0, 0, 0, fill="#1c2236"), (j * R, 0, j * R, 64)))
            for i in range(65):
                col = "#3a4170" if i % 16 == 0 else "#222943" if i % 4 == 0 else "#161b2c"
                self._grid.append((cv.create_line(0, 0, 0, 0, fill=col), (lo * R, 64 - i, hi * R, 64 - i)))
        self._bands = [cv.create_polygon(0, 0, 0, 0, 0, 0, 0, 0, fill="", outline="") for _ in range(ISO_PAGE)]
        self._band_sig = [None] * ISO_PAGE
        self._labels = []
        for j in range(4):
            self._labels.append((cv.create_text(0, 0, text=f"bar {j + 1}", fill=DIM, font=FONT_SMALL, anchor="e"), -2.2, 64 - (j * 16 + 8)))
        for j, r in enumerate(core_brain.INSTRUMENTS):
            self._labels.append((cv.create_text(0, 0, text=core_brain.label(r), fill=core_brain.COLORS[r], font=FONT_SMALL), j * R + R / 2, 66.2))
        self._track_labels = {}
        for k, r in enumerate(core_brain.TRACKS):
            it = cv.create_text(0, 0, text=core_brain.label(r), fill=core_brain.COLORS[r], font=FONT_SMALL)
            self._labels.append((it, (4 + k) * R + R / 2, 66.2))
            self._track_labels[r] = it
        # cases : instruments (3 x 64) et effets (8 x 64), creees triees par profondeur (x + y croissant : le fond d'abord)
        order = []
        for i in range(ISO_PAGE):
            for j, r in enumerate(core_brain.INSTRUMENTS):
                order.append((j * R + 63 - i, 0, "ins", i, r))
            for k, r in enumerate(core_brain.TRACKS):
                order.append(((4 + k) * R + 63 - i, 1, "fx", i, r))
        order.sort(key=lambda o: (o[0], o[1]))
        self._ins: dict = {}
        self._fxc: dict = {}
        for _k, _o, kind, i, r in order:
            polys = [cv.create_polygon(0, 0, 0, 0, 0, 0, 0, 0, state="hidden", fill="", outline="") for _ in range(3)]
            lines = [] if kind == "fx" else [cv.create_line(0, 0, 0, 0, state="hidden") for _ in range(3)]
            (self._ins if kind == "ins" else self._fxc)[(i, r)] = _IsoCell(polys, lines)
        # voile, causes, present
        self._veil = [cv.create_line(0, 0, 0, 0, 0, 0, state="hidden") for _ in range(ISO_VEIL_LINES)]
        self._cause_lines = [cv.create_line(0, 0, 0, 0, state="hidden", dash=(3, 3)) for _ in range(ISO_MAX_CAUSES)]
        self._cause_dots = [cv.create_oval(0, 0, 0, 0, state="hidden", outline="") for _ in range(ISO_MAX_CAUSES)]
        self._plane = cv.create_polygon(0, 0, 0, 0, 0, 0, 0, 0, fill="#ffffff", stipple="gray12", outline="")
        self._edge = cv.create_line(0, 0, 0, 0, fill="#e0e0e0", width=1.5)
        # libelles au-dessus des piliers (crees en premier, ils etaient caches par les cases)
        for it, _x, _y in self._labels:
            cv.tag_raise(it)
        # le curseur d'angle est dans la barre du haut du panneau (comme la maquette), montre seulement pour cette vue

    # ---- geometrie
    def _compute(self, w: int, h: int) -> None:
        t = math.tan(math.radians(self.angle))
        a = min((w - 40.0) / (ISO_X_MAX - ISO_X_MIN), (h - 18.0) / ((ISO_S_MAX + 1.4) * t + 60.0 / ISO_A0))
        self.a = a = max(0.5, a)
        self.b = a * t
        self.zs = a / ISO_A0
        self.ox = 20.0 - ISO_X_MIN * a
        self.oy = 8.0 + 1.4 * self.b + 60.0 * self.zs

    def P(self, x: float, y: float, z: float = 0.0) -> tuple[float, float]:
        return self.ox + (x - y) * self.a, self.oy + (x + y) * self.b - z * self.zs

    def set_angle(self, angle) -> None:
        angle = max(ISO_ANGLE[0], min(ISO_ANGLE[1], int(angle)))
        type(self).angle = angle
        self._regeo()
        if self._last is not None:
            self.update(*self._last)

    def _regeo(self) -> None:
        self._compute(self.w, self.h)
        self.geo += 1
        cv, P = self.canvas, self.P
        for it, (x1, y1, x2, y2) in self._grid:
            cv.coords(it, *P(x1, y1), *P(x2, y2))
        for it, x, y in self._labels:
            cv.coords(it, *P(x, y))

    def layout(self, w: int, h: int) -> None:
        self.w, self.h = max(1, w), max(1, h)
        self._regeo()

    # ---- formes
    def _box(self, polys, x, y, w, d, h, col, alpha, bg, outline=False) -> tuple[float, float]:
        cv, P = self.canvas, self.P
        right = [*P(x + w, y), *P(x + w, y + d), *P(x + w, y + d, h), *P(x + w, y, h)]
        left = [*P(x, y + d), *P(x + w, y + d), *P(x + w, y + d, h), *P(x, y + d, h)]
        top = [*P(x, y, h), *P(x + w, y, h), *P(x + w, y + d, h), *P(x, y + d, h)]
        for it, pts, fill in ((polys[0], right, blend(shade(col, 0.55), alpha, bg)), (polys[1], left, blend(shade(col, 0.35), alpha, bg)),
                              (polys[2], top, blend(col, alpha, bg))):
            cv.coords(it, *pts)
            cv.itemconfigure(it, fill=fill, outline="#ffffff" if (outline and it == polys[2]) else "", state="normal")
        return self.P(x + w / 2, y + d / 2, h)

    def _wire(self, cell: _IsoCell, x, y, w, d, h, col) -> tuple[float, float]:
        cv, P = self.canvas, self.P
        cv.coords(cell.polys[2], *P(x, y, h), *P(x + w, y, h), *P(x + w, y + d, h), *P(x, y + d, h))
        cv.itemconfigure(cell.polys[2], fill="", outline=col, state="normal")
        for it, (cx, cy) in zip(cell.lines, ((x + w, y), (x + w, y + d), (x, y + d))):
            cv.coords(it, *P(cx, cy), *P(cx, cy, h))
            cv.itemconfigure(it, fill=col, state="normal")
        return P(x + w / 2, y + d / 2, h)

    def _cross(self, cell: _IsoCell, x0, y0, col) -> tuple[float, float]:
        cv, P = self.canvas, self.P
        cx, hw = x0 + ISO_R / 2, 0.3                               # croix carree au milieu de la rangee
        cv.coords(cell.lines[0], *P(cx - hw, y0 + 0.2), *P(cx + hw, y0 + 0.8))
        cv.coords(cell.lines[1], *P(cx + hw, y0 + 0.2), *P(cx - hw, y0 + 0.8))
        for it in cell.lines[:2]:
            cv.itemconfigure(it, fill=col, width=1.2, state="normal")
        return P(cx, y0 + 0.5)

    def _mode(self, cell: _IsoCell, mode) -> None:
        """Change le mode d'une case : cache d'abord tous ses elements (seulement quand le mode change)."""
        if cell.mode == mode:
            return
        cv = self.canvas
        for it in cell.polys + cell.lines:
            cv.itemconfigure(it, state="hidden")
        cell.mode = mode

    def _clear(self, key, cell: _IsoCell) -> None:
        self._mode(cell, None)
        cell.pt = None
        self._hov.pop(key, None)

    # ---- mise a jour
    def update(self, snap: dict, causes: bool, hidden: set, now: float) -> None:
        self._last = (snap, causes, hidden, now)
        cv = self.canvas
        header = snap.get("header") or {}
        bg = tint(header.get("phase"))
        cursor = float(snap.get("cursor", 0.0))
        cur = int(cursor)
        bpm = header.get("bpm")
        step_s = 60.0 / bpm / core_brain.STEPS_PER_BEAT if bpm else 0.125
        fade = min(1.0, cursor * step_s / ISO_FADE_S)
        fadeq = 1.0 if fade >= 1.0 else round(fade, 1)
        gsig = (bg, self.geo)
        R, C = ISO_R, core_brain.COLORS
        rows = core_brain.INSTRUMENTS
        # instruments : seules les cases dont le statut a change
        for i, row, v, p, status in snap.get("cells", ()):
            cell = self._ins.get((i, row))
            if cell is None:
                continue
            if status is None or row in hidden:
                sig = None
            elif status == "ghost":
                sig = (status, round(p, 2), fadeq, gsig)
            elif status == "miss":
                sig = (status, round(p, 2), gsig)
            else:
                sig = (status, round(v, 2), round(p, 2), gsig)
            if sig == cell.sig:
                continue
            cell.sig = sig
            key = ("ins", i, row)
            if sig is None:
                self._clear(key, cell)
                continue
            x0, y0, name = rows.index(row) * R, 63 - i, core_brain.label(row)
            cx, cy, c = x0 + (R - ISO_CELL) / 2, y0 + (1 - ISO_CELL) / 2, ISO_CELL     # base carree au milieu de la rangee
            if status in ("real", "surprise"):
                self._mode(cell, "box")
                pt = self._box(cell.polys, cx, cy, c, c, 5 + 22 * v, C[row], 0.45 + 0.55 * v, bg, status == "surprise")
                tip = f"{name} {round(v * 100)} % · predit {round(p * 100)} % · " + ("surprise" if status == "surprise" else "realise")
            elif status == "miss":
                self._mode(cell, "cross")
                pt = self._cross(cell, x0, y0, blend(MISS_FG, 0.8, bg))
                tip = f"{name} predit {round(p * 100)} % · manque"
            else:
                self._mode(cell, "wire")
                pt = self._wire(cell, cx, cy, c, c, 5 + 22 * p * 0.85, blend(C[row], (0.2 + 0.6 * p) * fade, bg))
                tip = f"{name} attendu a {round(p * 100)} %"
            cell.pt = pt if status in ("real", "surprise") else None
            self._hov[key] = (pt[0], pt[1], tip)
        # effets
        active: dict = {}
        for i, track, strength, reason, cause in snap.get("fx", ()):
            if track in hidden or (i, track) not in self._fxc:
                continue
            active[(i, track)] = (strength, reason, cause)
        for key in [k for k in self._fx_sig if k not in active or self._fx_sig[k][2] != gsig]:
            if key not in active:
                self._clear(("fx",) + key, self._fxc[key])
                del self._fx_sig[key]
        fx_pts = {}
        for (i, track), (strength, reason, cause) in active.items():
            cell = self._fxc[(i, track)]
            sig = (round(strength, 2), reason, gsig)
            if self._fx_sig.get((i, track)) != sig:
                self._fx_sig[(i, track)] = sig
                self._mode(cell, "box")
                x0, y0, col = (4 + core_brain.TRACKS.index(track)) * R, 63 - i, C[track]
                s = max(0.0, min(1.0, float(strength)))
                # bases carrees, centrees dans la rangee (cote c) : strobe et foudre fins et hauts, scene en plaque de la largeur de la rangee
                if track == "scene":
                    c = R - 0.2
                    cell.pt = self._box(cell.polys, x0 + 0.1, y0 + 1 - c, c, c, 3, col, 0.85, bg)
                else:
                    c = 0.5 if track in ("strobe", "foudre") else ISO_FX_CELL
                    bx, by = x0 + (R - c) / 2, y0 + (1 - c) / 2
                    if track == "strobe":
                        cell.pt = self._box(cell.polys, bx, by, c, c, 56 if reason.startswith("drop") else 34 * s, col, 0.9, bg)
                    elif track == "foudre":
                        cell.pt = self._box(cell.polys, bx, by, c, c, 38, col, 0.85, bg)
                    else:
                        cell.pt = self._box(cell.polys, bx, by, c, c, 3 + 14 * s, col, 0.5 + 0.5 * s, bg)
                self._hov[("fx", i, track)] = (cell.pt[0], cell.pt[1], reason)
            fx_pts[(i, track)] = cell.pt
        # pastilles de pistes masquees
        hsig = (tuple(sorted(hidden)),)
        if hsig != self._hid_sig:
            self._hid_sig = hsig
            for r, it in self._track_labels.items():
                cv.itemconfigure(it, fill=DIM if r in hidden else C[r])
        # bande des phases (une teinte par pas ; plus vive pour le passe)
        steps = snap.get("phase_steps") or ()
        for i in range(min(ISO_PAGE, len(steps))):
            sig = (steps[i], i <= cur, gsig)
            if sig == self._band_sig[i]:
                continue
            self._band_sig[i] = sig
            cv.coords(self._bands[i], *self.P(-1.4, 63 - i), *self.P(-0.4, 63 - i), *self.P(-0.4, 64 - i), *self.P(-1.4, 64 - i))
            cv.itemconfigure(self._bands[i], fill=blend(PHASE_COLORS.get(steps[i], DIM), 0.85 if i <= cur else 0.18, bg))
        # pointilles des causes : de la frappe a l'effet
        links = []
        if causes:
            for (i, track), (_s, _r, cause) in active.items():
                src = self._ins.get((i, cause)) if cause else None
                if src is not None and src.pt is not None and fx_pts.get((i, track)) is not None:
                    links.append((src.pt, fx_pts[(i, track)], C[track]))
        csig = (tuple(links[:ISO_MAX_CAUSES]), bg)
        if csig != self._cause_sig:
            n_old = 0 if self._cause_sig is None else len(self._cause_sig[0])
            self._cause_sig = csig
            for n, (p, q, col) in enumerate(csig[0]):
                cv.coords(self._cause_lines[n], p[0], p[1], q[0], q[1])
                cv.itemconfigure(self._cause_lines[n], fill=blend(col, 0.55, bg), state="normal")
                cv.coords(self._cause_dots[n], q[0] - 1.8, q[1] - 1.8, q[0] + 1.8, q[1] + 1.8)
                cv.itemconfigure(self._cause_dots[n], fill=col, state="normal")
            for n in range(len(csig[0]), n_old):
                cv.itemconfigure(self._cause_lines[n], state="hidden")
                cv.itemconfigure(self._cause_dots[n], state="hidden")
        # voile : ISO_VEIL_LINES lignes de relief en sinusoide, une au-dessus de chaque piste d'effet (maquette)
        veil = snap.get("veil") or ()
        top = min(cur + 1, ISO_PAGE, len(veil) - 1) if veil else -1
        on = top >= 1 and any(veil[i][1] > 0 for i in range(top + 1))
        if on:
            kind = veil[min(cur, len(veil) - 1)][0]
            col = "#ffffff" if kind == "drop" else PHASE_COLORS["build"]
            if self._veil_sig != (col, bg):
                self._veil_sig = (col, bg)
                for k, it in enumerate(self._veil):
                    cv.itemconfigure(it, fill=blend(col, 0.55 - k * 0.04, bg))
            for k, it in enumerate(self._veil):
                kp = k * 7.0 / max(1, ISO_VEIL_LINES - 1)                                 # piste (0..7) sous la ligne
                x0 = (4 + kp) * R + R * 0.5
                pts = []
                for i in range(top + 1):
                    z = veil[i][1] * 40 * (0.55 + 0.45 * math.sin(i * 0.32 + kp * 0.8 + now * 2.2))
                    pts.extend(self.P(x0, 64 - i, z))
                cv.coords(it, *pts)
                if not self._veil_on:
                    cv.itemconfigure(it, state="normal")
        elif self._veil_on:
            for it in self._veil:
                cv.itemconfigure(it, state="hidden")
        self._veil_on = on
        # plan du present
        yy = 64 - cursor
        P = self.P
        cv.coords(self._plane, *P(-1.4, yy), *P(12 * R, yy), *P(12 * R, yy, 60), *P(-1.4, yy, 60))
        cv.coords(self._edge, *P(-1.4, yy), *P(12 * R, yy))

    # ---- survol
    def hover(self, x: float, y: float) -> str | None:
        best, dist = None, 12.0
        for px, py, text in self._hov.values():
            d = ((px - x) ** 2 + (py - y) ** 2) ** 0.5
            if d < dist:
                best, dist = text, d
        return best


# ---------------------------------------------------------------------------------------------------------------------------
# Vue sismographe : une onde par instrument (passe en trait plein, futur predit en pointilles), portee de 8 lignes pour les effets
# ---------------------------------------------------------------------------------------------------------------------------

SIS_H0 = 350.0                                 # hauteur de reference de la maquette
SIS_BASES = (56.0, 104.0, 152.0)               # ligne de base de chaque onde (kick, snare, hi-hat) a la hauteur de reference
SIS_AMP = 21.0
SIS_DECAY = {"kick": 0.9, "snare": 1.2, "hihat": 1.9}
SIS_STAFF0, SIS_STAFF_GAP = 206.0, 15.0        # premiere ligne de la portee et ecart (reference)
SIS_LEFT = 66.0
SIS_RIGHT = 10.0
SIS_MAX_FX = 160
SIS_MAX_CAUSES = 96
SIS_MAX_SCENES = 8
SIS_VEIL_LINES = 4                             # lignes du voile en sinusoide au-dessus de la portee (comme la maquette)
SIS_Q = 4                                      # points par pas pour l'onde


class SismoView:
    """Sismographe de la page : un element de canvas par onde (le passe, plein) et un par onde pour le futur predit (pointilles),
    mis a jour par `coords` ; marqueurs (surprise, manque), notes d'effets et pointilles des causes tires de reserves de taille fixe.
    Pas d'alpha sous Tk : les couleurs sont melangees vers le fond de la phase (`blend`)."""

    def __init__(self, canvas: tk.Canvas) -> None:
        self.canvas = canvas
        self.w = self.h = 1
        self.geo = 0
        self.sy = 1.0
        self.left, self.dx = SIS_LEFT, 8.8
        self._hov: dict = {}
        self._wave_sig = None
        self._wave_pts: dict = {}                      # instrument -> ((valeurs, geometrie), points de l'onde)
        self._wave_bg: dict = {}                       # instrument -> fond des couleurs posees
        self._mark_sig = None
        self._band_sig = None
        self._hid_sig = None
        self._veil_on = False
        self._veil_sig = None
        cv = canvas
        ins, trk = core_brain.INSTRUMENTS, core_brain.TRACKS
        self._bands = [cv.create_rectangle(0, 0, 0, 0, fill="", outline="") for _ in range(core_brain.PAGE_STEPS)]
        self._base_lines = [cv.create_line(0, 0, 0, 0, fill="#141826") for _ in ins]
        self._base_labels = [cv.create_text(6, 0, text=core_brain.label(r), anchor="w", fill=core_brain.COLORS[r], font=FONT_SMALL) for r in ins]
        self._bar_lines = [cv.create_line(0, 0, 0, 0, fill="#2a3050") for _ in range(4)]
        self._bar_labels = [cv.create_text(0, 0, text=f"bar {j + 1}", anchor="w", fill=DIM, font=FONT_SMALL) for j in range(4)]
        self._staff = [cv.create_line(0, 0, 0, 0, fill="#1c2236") for _ in trk]
        self._staff_labels = {r: cv.create_text(6, 0, text=core_brain.label(r), anchor="w", fill=core_brain.COLORS[r], font=FONT_SMALL) for r in trk}
        self._veil = [cv.create_line(0, 0, 0, 0, 0, 0, state="hidden") for _ in range(SIS_VEIL_LINES)]
        # ondes : [passe, futur] par instrument
        self._past = {r: cv.create_line(0, 0, 0, 0, fill=core_brain.COLORS[r], width=1.4) for r in ins}
        self._fut = {r: cv.create_line(0, 0, 0, 0, fill=core_brain.COLORS[r], width=1, dash=(2, 3)) for r in ins}
        n_cells = core_brain.PAGE_STEPS * len(ins)
        self._rings = [cv.create_oval(0, 0, 0, 0, state="hidden", outline="#ffffff", width=1.2) for _ in range(n_cells)]
        self._miss = [(cv.create_line(0, 0, 0, 0, state="hidden", width=1.2), cv.create_line(0, 0, 0, 0, state="hidden", width=1.2))
                      for _ in range(n_cells)]
        self._fxo = [cv.create_oval(0, 0, 0, 0, state="hidden", outline="") for _ in range(SIS_MAX_FX)]
        self._fxt = [cv.create_line(0, 0, 0, 0, state="hidden", width=1) for _ in range(SIS_MAX_FX)]
        self._fxr = [cv.create_rectangle(0, 0, 0, 0, state="hidden", outline="") for _ in range(SIS_MAX_FX)]
        self._scn = [cv.create_text(0, 0, text="", state="hidden", fill=core_brain.COLORS["scene"], font=FONT_SMALL) for _ in range(SIS_MAX_SCENES)]
        self._cause_lines = [cv.create_line(0, 0, 0, 0, state="hidden", dash=(3, 3), width=1) for _ in range(SIS_MAX_CAUSES)]
        self._edge = cv.create_line(0, 0, 0, 0, fill="#e0e0e0", width=1.5)
        self._used = {"rings": 0, "miss": 0, "fx": 0, "scn": 0, "causes": 0}

    def wave_items(self) -> list:
        """Les six elements d'onde (passe puis futur de chaque instrument)."""
        return [self._past[r] for r in core_brain.INSTRUMENTS] + [self._fut[r] for r in core_brain.INSTRUMENTS]

    # ---- geometrie
    def X(self, i: float) -> float:
        return self.left + i * self.dx

    def Y(self, v: float) -> float:
        return v * self.sy

    def layout(self, w: int, h: int) -> None:
        self.w, self.h = max(1, w), max(1, h)
        self.sy = max(0.3, self.h / SIS_H0)
        self.left = SIS_LEFT
        self.dx = max(2.0, (self.w - SIS_LEFT - SIS_RIGHT) / core_brain.PAGE_STEPS)
        self.geo += 1
        cv, X, Y = self.canvas, self.X, self.Y
        for j, r in enumerate(core_brain.INSTRUMENTS):
            by = Y(SIS_BASES[j])
            cv.coords(self._base_lines[j], X(0), by, X(64), by)
            cv.coords(self._base_labels[j], 6, by)
        for j in range(4):
            cv.coords(self._bar_lines[j], X(j * 16), Y(24), X(j * 16), Y(330))
            cv.coords(self._bar_labels[j], X(j * 16) + 3, Y(20))
        for k, r in enumerate(core_brain.TRACKS):
            sy = Y(SIS_STAFF0 + k * SIS_STAFF_GAP)
            cv.coords(self._staff[k], X(0), sy, X(64), sy)
            cv.coords(self._staff_labels[r], 6, sy)
        self._band_sig = self._wave_sig = self._mark_sig = None

    # ---- mise a jour
    def _vals(self, snap: dict, cur: int, hidden: set):
        val = {r: [0.0] * 64 for r in core_brain.INSTRUMENTS}
        sts: dict = {}
        for i, row, v, p, status in snap.get("cells", ()):
            if row not in val or not 0 <= i < 64 or status is None or row in hidden:
                continue
            if status in ("real", "surprise") and i <= cur:
                val[row][i] = float(v)
            elif status == "ghost" and i > cur:
                val[row][i] = float(p)
            sts[(i, row)] = (status, float(v), float(p))
        return val, sts

    def _wave_points(self, row: str, vals: tuple) -> list:
        """Points de l'onde (x, y a plat) aux 64 x SIS_Q + 1 positions de la page : calcules une fois par (valeurs, geometrie), puis
        seulement coupes au curseur (budget, tache 9 : l'onde etait recalculee a chaque rafraichissement)."""
        key = (vals, self.geo)
        hit = self._wave_pts.get(row)
        if hit is not None and hit[0] == key:
            return hit[1]
        base = self.Y(SIS_BASES[core_brain.INSTRUMENTS.index(row)])
        amp, dec = SIS_AMP * self.sy, SIS_DECAY[row]
        flat: list = []
        for q in range(64 * SIS_Q + 1):
            x = q / SIS_Q
            si = min(63, int(x))
            e = 0.0
            for k in range(6):
                i = si - k
                if i < 0:
                    break
                if vals[i] > 0:
                    e = max(e, vals[i] * math.exp(-(x - i) * dec))
            flat += [self.X(x), base - amp * e * math.sin(q * 2.3)]
        self._wave_pts[row] = (key, flat)
        return flat

    def _wave(self, row: str, vals: tuple, c: float, bg: str) -> None:
        """Passe (plein) jusqu'au curseur `c` (multiple de 1 / SIS_Q), futur predit (pointilles) ensuite."""
        cv = self.canvas
        flat = self._wave_points(row, vals)
        iq = max(0, min(64 * SIS_Q, int(round(c * SIS_Q))))
        past = flat[:2 * iq + 2]
        fut = flat[2 * iq:]
        for it, pts in ((self._past[row], past), (self._fut[row], fut)):
            cv.coords(it, *(pts if len(pts) >= 4 else pts * 2))
        if self._wave_bg.get(row) != bg:
            self._wave_bg[row] = bg
            cv.itemconfigure(self._past[row], fill=blend(core_brain.COLORS[row], 0.95, bg))
            cv.itemconfigure(self._fut[row], fill=blend(core_brain.COLORS[row], 0.3, bg))

    def _hide_from(self, key: str, items: list, n: int) -> None:
        for it in items[n:self._used[key]]:
            if isinstance(it, tuple):
                for sub in it:
                    self.canvas.itemconfigure(sub, state="hidden")
            else:
                self.canvas.itemconfigure(it, state="hidden")
        self._used[key] = n

    def update(self, snap: dict, causes: bool, hidden: set, now: float) -> None:
        cv = self.canvas
        header = snap.get("header") or {}
        bg = tint(header.get("phase"))
        cursor = max(0.0, min(64.0, float(snap.get("cursor", 0.0))))
        cur = int(cursor)
        cq = int(cursor * SIS_Q) / SIS_Q
        C = core_brain.COLORS
        ins = core_brain.INSTRUMENTS
        val, sts = self._vals(snap, cur, hidden)
        gsig = (bg, self.geo)
        # pistes masquees : etiquettes et ondes
        hsig = tuple(sorted(hidden))
        if hsig != self._hid_sig:
            self._hid_sig = hsig
            for r in core_brain.TRACKS:
                cv.itemconfigure(self._staff_labels[r], fill=DIM if r in hidden else C[r])
            for r in ins:
                cv.itemconfigure(self._base_labels[ins.index(r)], fill=DIM if r in hidden else C[r])
                st = "hidden" if r in hidden else "normal"
                cv.itemconfigure(self._past[r], state=st)
                cv.itemconfigure(self._fut[r], state=st)
        # ondes
        rvals = {r: tuple(round(v, 2) for v in val[r]) for r in ins}
        wsig = (tuple(rvals[r] for r in ins), cq, gsig, hsig)
        if wsig != self._wave_sig:
            self._wave_sig = wsig
            for r in ins:
                if r not in hidden:
                    self._wave(r, rvals[r], cq, bg)
        # bande des phases
        steps = snap.get("phase_steps") or ()
        bsig = (tuple(steps), cur, gsig)
        if bsig != self._band_sig:
            self._band_sig = bsig
            y0, y1 = self.Y(180), self.Y(185)
            for i in range(min(core_brain.PAGE_STEPS, len(steps))):
                cv.coords(self._bands[i], self.X(i), y0, self.X(i + 1), y1)
                cv.itemconfigure(self._bands[i], fill=blend(PHASE_COLORS.get(steps[i], DIM), 0.85 if i <= cur else 0.18, bg))
        # effets : un seul par (pas, piste), le plus fort
        active: dict = {}
        for i, track, strength, reason, cause in snap.get("fx", ()):
            if track in hidden or track not in core_brain.TRACKS or not 0 <= i < 64:
                continue
            old = active.get((i, track))
            if old is None or float(strength) > old[0]:
                active[(i, track)] = (float(strength), reason, cause)
        fx_items = sorted(active.items())[:SIS_MAX_FX]
        msig = (tuple(sorted((k, s) for k, s in sts.items())), tuple((k, round(v[0], 2), v[1], v[2]) for k, v in fx_items), causes, gsig, self.w)
        if msig != self._mark_sig:
            self._mark_sig = msig
            self._marks(sts, fx_items, causes, bg)
        # voile au-dessus de la portee
        veil = snap.get("veil") or ()
        lim = min(core_brain.PAGE_STEPS, cur + 1)
        on = bool(veil) and any(veil[min(i, len(veil) - 1)][1] > 0 for i in range(lim + 1))
        if on:
            kind = veil[min(cur, len(veil) - 1)][0]
            col = "#ffffff" if kind == "drop" else PHASE_COLORS["build"]
            if self._veil_sig != (col, bg):
                self._veil_sig = (col, bg)
                for L, it in enumerate(self._veil):
                    cv.itemconfigure(it, fill=blend(col, 0.5 - L * 0.08, bg))
            for L, it in enumerate(self._veil):
                pts = []
                for i in range(lim + 1):
                    v = veil[min(i, len(veil) - 1)][1]
                    y = SIS_STAFF0 - 6 - L * 4 - v * 44 * (0.55 + 0.45 * math.sin(i * 0.32 + L + now * 2.2))
                    pts += [self.X(i), self.Y(y)]
                cv.coords(it, *pts)
                if not self._veil_on:
                    cv.itemconfigure(it, state="normal")
        elif self._veil_on:
            for it in self._veil:
                cv.itemconfigure(it, state="hidden")
        self._veil_on = on
        # ligne du present
        cv.coords(self._edge, self.X(cursor), self.Y(24), self.X(cursor), self.Y(330))

    def _marks(self, sts: dict, fx_items: list, causes: bool, bg: str) -> None:
        """Surprises, manques, notes des effets et causes : reserves reutilisees (largeurs et formes remises a chaque emploi)."""
        cv, X, Y, C = self.canvas, self.X, self.Y, core_brain.COLORS
        hov: dict = {}
        peaks: dict = {}
        nr = nm = 0
        for (i, row), (status, v, p) in sorted(sts.items()):
            j = core_brain.INSTRUMENTS.index(row)
            by = Y(SIS_BASES[j])
            name = core_brain.label(row)
            if status in ("real", "surprise"):
                px, py = X(i) + 1, by - Y(SIS_AMP) * v
                peaks[(i, row)] = (px, py)
                tip = f"{name} {round(v * 100)} % · " + ("surprise" if status == "surprise" else "realise")
                hov[("ins", i, row)] = (px, py, tip)
                if status == "surprise" and nr < len(self._rings):
                    cv.coords(self._rings[nr], px - 3.5, py - 3.5, px + 3.5, py + 3.5)
                    cv.itemconfigure(self._rings[nr], state="normal")
                    nr += 1
            elif status == "miss" and nm < len(self._miss):
                a, b = self._miss[nm]
                x0, d = X(i) + 1, 3 * self.sy
                cv.coords(a, x0, by - d, x0 + 6, by + d)
                cv.coords(b, x0 + 6, by - d, x0, by + d)
                col = blend(MISS_FG, 0.8, bg)
                cv.itemconfigure(a, fill=col, state="normal")
                cv.itemconfigure(b, fill=col, state="normal")
                hov[("ins", i, row)] = (x0 + 3, by, f"{name} predit {round(p * 100)} % · manque")
                nm += 1
        self._hide_from("rings", self._rings, nr)
        self._hide_from("miss", self._miss, nm)
        nf = ns = 0
        pts: dict = {}
        for (i, track), (s, reason, cause) in fx_items:
            k = core_brain.TRACKS.index(track)
            x, y = X(i) + 4, Y(SIS_STAFF0 + k * SIS_STAFF_GAP)
            s = max(0.0, min(1.0, s))
            col = blend(C[track], 0.45 + 0.55 * s, bg)
            o, t, r_ = self._fxo[nf], self._fxt[nf], self._fxr[nf]
            nf += 1
            cv.itemconfigure(t, state="hidden")
            cv.itemconfigure(o, state="hidden")
            cv.itemconfigure(r_, state="hidden")
            if track == "strobe":
                half = 12 if reason.startswith("drop") else 6
                cv.coords(r_, x - 1, y - Y(half), x + 1.5, y + Y(half))
                cv.itemconfigure(r_, fill=col, state="normal")
            elif track == "scene":
                rx = min(x - 3, self.w - 2 - 26)
                cv.coords(r_, rx, y - 4, rx + 26, y + 4)
                cv.itemconfigure(r_, fill=blend(C[track], 0.85, bg), state="normal")
                if ns < SIS_MAX_SCENES:
                    txt = (reason.split(" : ")[-1].split(" (")[0])[:14]
                    if rx + 29 + 6 * len(txt) > self.w:
                        cv.coords(self._scn[ns], rx - 3, y)
                        cv.itemconfigure(self._scn[ns], text=txt, anchor="e", state="normal")
                    else:
                        cv.coords(self._scn[ns], rx + 29, y)
                        cv.itemconfigure(self._scn[ns], text=txt, anchor="w", state="normal")
                    ns += 1
                x = rx + 13
            else:
                rx_, ry_ = 2 + 2 * s, 1.6 + 1.6 * s
                cv.coords(o, x - rx_, y - ry_, x + rx_, y + ry_)
                cv.itemconfigure(o, fill=col, state="normal")
                cv.coords(t, x + rx_, y, x + rx_, y - 9)
                cv.itemconfigure(t, fill=col, state="normal")
            pts[(i, track)] = (x, y, col)
            hov[("fx", i, track)] = (x, y, reason)
        self._hide_from("fx", list(zip(self._fxo, self._fxt, self._fxr)), nf)
        for it in self._scn[ns:self._used["scn"]]:
            cv.itemconfigure(it, state="hidden")
        self._used["scn"] = ns
        nc = 0
        if causes:
            for ((i, track), (_s, _r, cause)) in fx_items:
                src = peaks.get((i, cause)) if cause else None
                if src is None or nc >= SIS_MAX_CAUSES:
                    continue
                x, y, _c = pts[(i, track)]
                cv.coords(self._cause_lines[nc], src[0], src[1], x, y)
                cv.itemconfigure(self._cause_lines[nc], fill=blend(C[track], 0.55, bg), state="normal")
                nc += 1
        self._hide_from("causes", self._cause_lines, nc)
        self._hov = hov

    # ---- survol
    def hover(self, x: float, y: float) -> str | None:
        best, dist = None, 10.0
        for px, py, text in self._hov.values():
            d = ((px - x) ** 2 + (py - y) ** 2) ** 0.5
            if d < dist:
                best, dist = text, d
        return best


# ---------------------------------------------------------------------------------------------------------------------------
# Vue mandala : la page en cercles, une mesure par anneau, le 1 pile en haut
# ---------------------------------------------------------------------------------------------------------------------------

MAN_R0, MAN_BWR, MAN_IW = 48.0, 28.0, 9.0      # rayon interieur, epaisseur d'une mesure, d'un instrument (unites de la maquette)
MAN_RC = MAN_R0 + 4 * MAN_BWR + 6              # rayon de la couronne des 32 temps
MAN_RE = MAN_RC + 18                           # rayon du premier anneau d'effets
MAN_GAP = 5.0                                  # ecart des anneaux d'effets
MAN_RMAX = 240.0                               # rayon le plus grand dessine (strobe de drop compris), pour la mise a l'echelle
MAN_SP = 2 * math.pi / 16                      # largeur angulaire d'une case (16 pas par mesure)
MAN_MAX_FX = 160
MAN_MAX_CAUSES = 96
MAN_VEIL_RINGS = 4                             # ondes du voile au centre (comme la maquette)


def _man_angle(s: float) -> float:
    """Angle (radians, sens horaire a l'ecran depuis la droite) du centre de la case `s` : le pas 0 est pile en haut."""
    return -math.pi / 2 + s * MAN_SP


class MandalaView:
    """Page en cercles : une case = un arc du canvas (`style="arc"`, largeur = epaisseur de l'anneau), cases centrees sur les axes (pas 0 en
    haut, 4 a droite, 8 en bas, 12 a gauche). Tout est cree a la construction et place par `layout` (la geometrie ne bouge plus) ;
    `update` ne change que la couleur et l'etat des cases dont la signature change, puis l'aiguille et les ondes du voile a chaque tick.
    Pas d'alpha sous Tk : les couleurs sont melangees vers le fond de la phase (`blend`)."""

    def __init__(self, canvas: tk.Canvas) -> None:
        self.canvas = canvas
        self.w = self.h = 1
        self.geo = 0
        self.k, self.cx, self.cy = 1.0, 0.5, 0.5
        self._hov: dict = {}
        self._hov_c: dict = {}
        self._hov_f: dict = {}
        self._csig: dict = {}
        self._msig = self._crown_sig = self._bar_sig = self._disc_sig = self._hid_sig = None
        self._veil_on = False
        self._used = {"fx": 0, "causes": 0}
        self._mid: dict = {}
        cv = canvas
        ins, trk = core_brain.INSTRUMENTS, core_brain.TRACKS
        self._rings = [cv.create_oval(0, 0, 0, 0, outline="#151a2a") for _ in range(12)]
        self._spokes = [cv.create_line(0, 0, 0, 0, fill="#2a3050" if s % 4 == 0 else "#121624") for s in range(16)]
        self._cells: dict = {}
        for i in range(core_brain.PAGE_STEPS):
            for kk in range(len(ins)):
                poly = cv.create_polygon(*([0, 0] * 12), fill="", outline="", state="hidden")
                arc = cv.create_arc(0, 0, 1, 1, style="arc", outline="", state="hidden")
                m1 = cv.create_line(0, 0, 0, 0, state="hidden", width=1.2)
                m2 = cv.create_line(0, 0, 0, 0, state="hidden", width=1.2)
                self._cells[(i, kk)] = (arc, poly, m1, m2)
        self._crown = [cv.create_line(0, 0, 0, 0, fill="#2a3050", width=2 if (q + 1) % 8 == 0 else 1) for q in range(32)]
        self._crown_label = cv.create_text(0, 0, text="32", fill=DIM, font=FONT_SMALL)
        self._fx_rings = [cv.create_oval(0, 0, 0, 0, outline="#111522") for _ in trk]
        self._fxo = [cv.create_oval(0, 0, 0, 0, state="hidden", outline="") for _ in range(MAN_MAX_FX)]
        self._fxl = [cv.create_line(0, 0, 0, 0, state="hidden", width=2) for _ in range(MAN_MAX_FX)]
        self._fxa = [cv.create_arc(0, 0, 1, 1, style="arc", state="hidden", width=2) for _ in range(MAN_MAX_FX)]
        self._disc = cv.create_oval(0, 0, 0, 0, outline="")
        self._veil = [cv.create_oval(0, 0, 0, 0, state="hidden", width=1.5) for _ in range(MAN_VEIL_RINGS)]
        self._bpm = cv.create_text(0, 0, text="", fill=FG, font=FONT_HEAD)
        self._phase = cv.create_text(0, 0, text="", fill=DIM, font=FONT_SMALL)
        self._cur_rings = [cv.create_oval(0, 0, 0, 0, outline="#ffffff") for _ in range(2)]
        self._ins_labels = [cv.create_text(0, 0, text=core_brain.label(r), anchor="w", fill=core_brain.COLORS[r], font=FONT_SMALL) for r in ins]
        self._bar_labels = [cv.create_text(0, 0, text=f"bar {j + 1}", anchor="e", fill=DIM, font=FONT_SMALL) for j in range(4)]
        self._cause_lines = [cv.create_line(0, 0, 0, 0, state="hidden", dash=(3, 3), width=1) for _ in range(MAN_MAX_CAUSES)]
        self._cause_dots = [cv.create_oval(0, 0, 0, 0, state="hidden", outline="") for _ in range(MAN_MAX_CAUSES)]
        self._needle = cv.create_line(0, 0, 0, 0, fill="#e0e0e0", width=1.5)

    def cell_item(self, i: int, row: str):
        """L'arc de la case (pas `i` de la page, instrument `row`)."""
        return self._cells[(i, core_brain.INSTRUMENTS.index(row))][0]

    # ---- geometrie
    def pol(self, r: float, an: float) -> tuple[float, float]:
        return self.cx + r * self.k * math.cos(an), self.cy + r * self.k * math.sin(an)

    def _circle(self, item, r: float) -> None:
        r *= self.k
        self.canvas.coords(item, self.cx - r, self.cy - r, self.cx + r, self.cy + r)

    def layout(self, w: int, h: int) -> None:
        self.w, self.h = max(1, w), max(1, h)
        self.geo += 1
        self.cx, self.cy = self.w / 2, self.h / 2
        self.k = max(0.2, min(1.6, (min(self.w, self.h) / 2 - 3) / MAN_RMAX))
        cv, k = self.canvas, self.k
        for j in range(4):
            for kk in range(3):
                self._circle(self._rings[j * 3 + kk], MAN_R0 + j * MAN_BWR + kk * MAN_IW + MAN_IW - 1)
        for s, it in enumerate(self._spokes):
            an = _man_angle(s) - MAN_SP / 2
            (x0, y0), (x1, y1) = self.pol(MAN_R0, an), self.pol(MAN_R0 + 4 * MAN_BWR, an)
            cv.coords(it, x0, y0, x1, y1)
        for (i, kk), (arc, poly, m1, m2) in self._cells.items():
            j, s = divmod(i, 16)
            ri = MAN_R0 + j * MAN_BWR + kk * MAN_IW
            ro = ri + MAN_IW - 2
            a0, a1 = _man_angle(s) - MAN_SP / 2 + 0.02, _man_angle(s) + MAN_SP / 2 - 0.02
            self._circle(arc, (ri + ro) / 2)
            cv.itemconfigure(arc, start=-math.degrees(a1), extent=math.degrees(a1 - a0), width=(ro - ri) * k)
            pts = []
            for t in range(5):
                pts += self.pol(ro, a0 + (a1 - a0) * t / 4)
            for t in range(5):
                pts += self.pol(ri, a1 - (a1 - a0) * t / 4)
            cv.coords(poly, *pts)
            p, q = self.pol(ri + 1, a0 + 0.05), self.pol(ro - 1, a1 - 0.05)
            p2, q2 = self.pol(ri + 1, a1 - 0.05), self.pol(ro - 1, a0 + 0.05)
            cv.coords(m1, *p, *q)
            cv.coords(m2, *p2, *q2)
            self._mid[(i, kk)] = self.pol((ri + ro) / 2, _man_angle(s))
        for q, it in enumerate(self._crown):
            an = -math.pi / 2 + q / 32 * 2 * math.pi
            big = (q + 1) % 8 == 0
            ln = (12 if (q + 1) % 32 == 0 else 9 if (q + 1) % 16 == 0 else 7) if big else 3
            (x0, y0), (x1, y1) = self.pol(MAN_RC, an), self.pol(MAN_RC + ln, an)
            cv.coords(it, x0, y0, x1, y1)
        cv.coords(self._crown_label, *self.pol(MAN_RC + 20, -math.pi / 2 + 31.5 / 32 * 2 * math.pi))
        for kk, it in enumerate(self._fx_rings):
            self._circle(it, MAN_RE + kk * MAN_GAP)
        self._circle(self._disc, MAN_R0 - 4)
        cv.coords(self._bpm, self.cx, self.cy - 7 * k)
        cv.coords(self._phase, self.cx, self.cy + 9 * k)
        for kk, it in enumerate(self._ins_labels):
            cv.coords(it, self.cx + 4, self.cy - (MAN_R0 + kk * MAN_IW + 4) * k)
        for j, it in enumerate(self._bar_labels):
            cv.coords(it, self.cx - 6, self.cy - (MAN_R0 + j * MAN_BWR + MAN_IW * 1.5) * k)
        self._msig = self._crown_sig = self._bar_sig = None

    # ---- cases
    def _apply_cell(self, key, sig, bg: str) -> None:
        cv = self.canvas
        arc, poly, m1, m2 = self._cells[key]
        status, v, p, fade = sig[0], sig[1], sig[2], sig[3]
        col = core_brain.COLORS[core_brain.INSTRUMENTS[key[1]]]
        if status in ("real", "surprise"):
            cv.itemconfigure(arc, outline=blend(col, 0.35 + 0.65 * v, bg), state="normal")
            if status == "surprise":
                cv.itemconfigure(poly, outline="#ffffff", width=1.2, state="normal")
            else:
                cv.itemconfigure(poly, state="hidden")
            cv.itemconfigure(m1, state="hidden")
            cv.itemconfigure(m2, state="hidden")
        elif status == "miss":
            red = blend(MISS_FG, 0.8, bg)
            cv.itemconfigure(arc, state="hidden")
            cv.itemconfigure(poly, state="hidden")
            cv.itemconfigure(m1, fill=red, state="normal")
            cv.itemconfigure(m2, fill=red, state="normal")
        else:                                                     # fantome : contour fin, d'autant plus net que la prediction est forte
            cv.itemconfigure(arc, state="hidden")
            cv.itemconfigure(poly, outline=blend(col, (0.2 + 0.6 * p) * fade, bg), width=1, state="normal")
            cv.itemconfigure(m1, state="hidden")
            cv.itemconfigure(m2, state="hidden")

    def _hide_cell(self, key) -> None:
        for it in self._cells[key]:
            self.canvas.itemconfigure(it, state="hidden")

    def _cell_text(self, row: str, status: str, v: float, p: float) -> str:
        name = core_brain.label(row)
        if status in ("real", "surprise"):
            return f"{name} {round(v * 100)} % · " + ("surprise" if status == "surprise" else "realise")
        if status == "miss":
            return f"{name} predit {round(p * 100)} % · manque"
        return f"{name} attendu a {round(p * 100)} %"

    # ---- mise a jour
    def update(self, snap: dict, causes: bool, hidden: set, now: float) -> None:
        cv = self.canvas
        header = snap.get("header") or {}
        phase = header.get("phase")
        bg = tint(phase)
        bpm = header.get("bpm")
        cursor = max(0.0, min(64.0, float(snap.get("cursor", 0.0))))
        cur = int(cursor)
        first = int(snap.get("first", 0) or 0)
        C = core_brain.COLORS
        ins = core_brain.INSTRUMENTS
        gsig = (bg, self.geo)
        fade = round(min(1.0, cursor * 15.0 / float(bpm) / 0.35), 1) if bpm else 1.0
        # etiquettes des instruments masques
        hsig = tuple(sorted(hidden))
        if hsig != self._hid_sig:
            self._hid_sig = hsig
            for kk, r in enumerate(ins):
                cv.itemconfigure(self._ins_labels[kk], fill=DIM if r in hidden else C[r])
        # cases : une signature par case, seules celles qui changent sont retouchees
        new: dict = {}
        for i, row, v, p, status in snap.get("cells", ()):
            if row not in ins or not 0 <= i < core_brain.PAGE_STEPS or status is None or row in hidden:
                continue
            new[(i, ins.index(row))] = (status, round(float(v), 2), round(float(p), 2), fade if status == "ghost" else 1.0, gsig)
        changed = False
        for key in [k for k in self._csig if k not in new]:
            self._hide_cell(key)
            del self._csig[key]
            changed = True
        for key, sig in new.items():
            if self._csig.get(key) != sig:
                self._csig[key] = sig
                self._apply_cell(key, sig, bg)
                changed = True
        if changed:
            self._hov_c = {("ins", i, ins[kk]): (*self._mid[(i, kk)], self._cell_text(ins[kk], s[0], s[1], s[2])) for (i, kk), s in new.items()}
        # effets : un seul par (pas, piste), le plus fort
        active: dict = {}
        for i, track, strength, reason, cause in snap.get("fx", ()):
            if track in hidden or track not in core_brain.TRACKS or not 0 <= i < core_brain.PAGE_STEPS:
                continue
            old = active.get((i, track))
            if old is None or float(strength) > old[0]:
                active[(i, track)] = (float(strength), reason, cause)
        fx_items = sorted(active.items())[:MAN_MAX_FX]
        peaks = tuple(sorted(key for key, s in new.items() if s[0] in ("real", "surprise")))
        msig = (tuple((k, round(v[0], 2), v[1], v[2]) for k, v in fx_items), causes, gsig, peaks)
        if msig != self._msig:
            self._msig = msig
            self._marks(fx_items, set(peaks), causes, bg)
            changed = True
        if changed:
            self._hov = {**self._hov_c, **self._hov_f}
        # couronne des 32 temps
        ab = first + cur
        beat = (ab // 4) % 32
        pcol = PHASE_COLORS.get(phase, DIM)
        crsig = (beat, pcol)
        if crsig != self._crown_sig:
            self._crown_sig = crsig
            for q, it in enumerate(self._crown):
                cv.itemconfigure(it, fill=pcol if q <= beat else "#2a3050")
        # anneaux de la mesure presente
        bar = min(3, cur // 16)
        if (bar, bg) != self._bar_sig:
            self._bar_sig = (bar, bg)
            for j in range(4):
                for kk in range(3):
                    cv.itemconfigure(self._rings[j * 3 + kk], outline="#2a3050" if j == bar else "#151a2a")
            self._circle(self._cur_rings[0], MAN_R0 + bar * MAN_BWR - 1)
            self._circle(self._cur_rings[1], MAN_R0 + (bar + 1) * MAN_BWR - 2)
            for it in self._cur_rings:
                cv.itemconfigure(it, outline=blend("#ffffff", 0.35, bg))
        # disque central : BPM et phase
        dsig = (phase, None if bpm is None else round(float(bpm), 1), bg)
        disc_bg = shade(pcol, 0.14)
        if dsig != self._disc_sig:
            self._disc_sig = dsig
            cv.itemconfigure(self._disc, fill=disc_bg)
            cv.itemconfigure(self._bpm, text="--" if bpm is None else f"{float(bpm):.1f}")
            cv.itemconfigure(self._phase, text=phase or "", fill=pcol)
        # ondes du voile (changent a chaque tick : ces MAN_VEIL_RINGS anneaux seulement)
        veil = snap.get("veil") or ()
        v = veil[min(cur, len(veil) - 1)] if veil else None
        on = bool(v) and v[1] > 0
        if on:
            col = "#ffffff" if v[0] == "drop" else PHASE_COLORS["build"]
            rmax = MAN_R0 - 4
            for q, it in enumerate(self._veil):
                rr = ((now * 1.4 + q / MAN_VEIL_RINGS) % 1.0) * rmax
                self._circle(it, max(0.5, rr))
                cv.itemconfigure(it, outline=blend(col, v[1] * (1 - rr / rmax) * 0.9, disc_bg), state="normal")
        elif self._veil_on:
            for it in self._veil:
                cv.itemconfigure(it, state="hidden")
        self._veil_on = on
        # aiguille du present (bord de la case en cours)
        an = _man_angle(cursor % 16) - MAN_SP / 2
        (x0, y0), (x1, y1) = self.pol(MAN_R0, an), self.pol(MAN_RE + 8 * MAN_GAP, an)
        cv.coords(self._needle, x0, y0, x1, y1)

    def _marks(self, fx_items: list, peaks: set, causes: bool, bg: str) -> None:
        """Notes des effets sur les anneaux, causes en pointilles : reserves reutilisees (une reserve par forme)."""
        cv, C = self.canvas, core_brain.COLORS
        hov: dict = {}
        pts: dict = {}
        n = 0
        for (i, track), (s, reason, cause) in fx_items:
            kk = core_brain.TRACKS.index(track)
            an = _man_angle(i % 16)
            rr = MAN_RE + kk * MAN_GAP
            s = max(0.0, min(1.0, s))
            col = blend(C[track], 0.4 + 0.6 * s, bg)
            o, ln, ar = self._fxo[n], self._fxl[n], self._fxa[n]
            for it in (o, ln, ar):
                cv.itemconfigure(it, state="hidden")
            x, y = self.pol(rr, an)
            if track == "strobe":
                (x0, y0), (x1, y1) = self.pol(rr - 3, an), self.pol(rr + (14 if reason.startswith("drop") else 4), an)
                cv.coords(ln, x0, y0, x1, y1)
                cv.itemconfigure(ln, fill=col, state="normal")
            elif track == "scene":
                self._circle(ar, rr)
                cv.itemconfigure(ar, start=-math.degrees(an + MAN_SP / 2), extent=math.degrees(MAN_SP), outline=blend(C[track], 0.85, bg),
                                 state="normal")
            else:
                d = (1.2 + 2 * s) * self.k
                cv.coords(o, x - d, y - d, x + d, y + d)
                cv.itemconfigure(o, fill=col, state="normal")
            pts[(i, track)] = (x, y, col)
            hov[("fx", i, track)] = (x, y, reason)
            n += 1
        for idx in range(n, self._used["fx"]):
            for it in (self._fxo[idx], self._fxl[idx], self._fxa[idx]):
                cv.itemconfigure(it, state="hidden")
        self._used["fx"] = n
        nc = 0
        if causes:
            for ((i, track), (_s, _r, cause)) in fx_items:
                if not cause or cause not in core_brain.INSTRUMENTS or nc >= MAN_MAX_CAUSES:
                    continue
                kk = core_brain.INSTRUMENTS.index(cause)
                if (i, kk) not in peaks:
                    continue
                sx, sy = self._mid[(i, kk)]
                x, y, col = pts[(i, track)]
                cv.coords(self._cause_lines[nc], sx, sy, x, y)
                cv.itemconfigure(self._cause_lines[nc], fill=col, state="normal")
                d = 2.0
                cv.coords(self._cause_dots[nc], x - d, y - d, x + d, y + d)
                cv.itemconfigure(self._cause_dots[nc], fill=col, state="normal")
                nc += 1
        for idx in range(nc, self._used["causes"]):
            cv.itemconfigure(self._cause_lines[idx], state="hidden")
            cv.itemconfigure(self._cause_dots[idx], state="hidden")
        self._used["causes"] = nc
        self._hov_f = hov

    # ---- survol
    def hover(self, x: float, y: float) -> str | None:
        best, dist = None, 10.0
        for px, py, text in self._hov.values():
            d = ((px - x) ** 2 + (py - y) ** 2) ** 0.5
            if d < dist:
                best, dist = text, d
        return best


# ---------------------------------------------------------------------------------------------------------------------------
# Vue tunnel : le temps vient vers soi, les pas a venir s'enfoncent dans la profondeur (instantane glissant : 4 pas de passe, 64 a venir)
# ---------------------------------------------------------------------------------------------------------------------------

TUN_STEPS = core_brain.PAGE_STEPS + 4          # pas de l'instantane glissant (les 4 premiers sont le passe)
TUN_DEPTH = 32                                 # pas montres devant le present (budget mesure, tache 9 : 64 -> 32, soit 2 mesures)
TUN_SHOWN = 4 + TUN_DEPTH                      # pas dessines : les 4 du passe + la profondeur
TUN_VEIL_LINES = 5                             # lignes du voile en sinusoide au plafond (comme la maquette)
TUN_CELL0, TUN_CELL1 = 0.15, 0.45              # cote de la face avant carree d'une case : 0,15 + 0,45 x force (au plus 0,6 < voie de 0,8)
TUN_F = 300.0                                  # focale de la maquette (a l'echelle 1 : canvas 640 x 340)
TUN_W0, TUN_H0 = 640.0, 340.0
TUN_LANES = (-1.2, -0.4, 0.4, 1.2)             # bords des 3 voies (une par instrument) sur le sol
TUN_XW, TUN_YF, TUN_YC = 1.7, -1.0, 1.1        # murs en x = +-1,7 ; sol ; plafond (le voile)
TUN_ZMIN = 0.3                                 # un pas plus proche que cela de la camera n'est pas dessine
TUN_MAX_CAUSES = 48
TUN_CURSOR_MAX = 20.0


def _tun_z(d: float) -> float:
    """Profondeur du pas a la distance `d` (en pas) du present."""
    return 2.0 + 0.12 * d


class TunnelView:
    """Couloir en perspective : sol a 3 voies (kick, snare, hi-hat), 4 pistes d'effets sur chaque mur, voile au plafond, cadre blanc du
    present. Tout bouge a chaque image : le pool est cree a la construction (TUN_SHOWN = 36 pas x 3 voies, 36 x 8 panneaux, nervures,
    voile, causes ; l'instantane glissant en a 68, seuls les 4 du passe et les TUN_DEPTH = 32 suivants sont dessines, budget tache 9),
    `update` ne fait que des `coords` sur les elements visibles et des `itemconfigure` quand le statut ou la couleur d'une case change.
    Ordre de creation = ordre de dessin (du fond vers l'avant, comme la maquette). Pas d'alpha sous Tk : melange vers le fond (`blend`) ;
    l'eclair du strobe eclaircit le fond et pose un voile blanc trame (`stipple`) au-dessus de la scene, sous le cadre et les libelles.
    Les points sont ramenes dans le canvas."""

    def __init__(self, canvas: tk.Canvas) -> None:
        self.canvas = canvas
        self.w = self.h = 1
        self.k, self.cx, self.cy = 1.0, 0.5, 0.5
        self.min_z = 99.0                          # plus petite profondeur projetee a la derniere mise a jour (test : toujours > 0)
        self._hov: list = []
        self._used = {"causes": 0}
        self._csig: dict = {}
        self._psig: dict = {}
        self._rib_sig: list = [None] * TUN_SHOWN
        self._strip_sig: list = [None] * TUN_SHOWN
        self._veil_sig: list = [None] * TUN_VEIL_LINES
        self._cause_sig: list = [None] * TUN_MAX_CAUSES
        self._bg_fill = None
        self._hid_sig = None
        cv = canvas
        ins, trk = core_brain.INSTRUMENTS, core_brain.TRACKS
        self._bgrect = cv.create_rectangle(0, 0, 1, 1, fill=tint(None), outline="")
        self._ribs: list = [None] * TUN_SHOWN
        self._strips: list = [None] * TUN_SHOWN
        for i in range(TUN_SHOWN - 1, -1, -1):
            self._ribs[i] = tuple(cv.create_line(0, 0, 0, 0, fill="#161b2c", state="hidden") for _ in range(4))
            self._strips[i] = cv.create_polygon(*([0, 0] * 4), fill="", outline="", state="hidden")
        self._long = [cv.create_line(0, 0, 0, 0, fill="#1c2236") for _ in range(4)] + \
                     [cv.create_line(0, 0, 0, 0, fill="#222943") for _ in range(4)]
        self._cells: dict = {}
        self._panels: dict = {}
        for i in range(TUN_SHOWN - 1, -1, -1):
            for kk in range(len(ins)):
                top = cv.create_polygon(*([0, 0] * 4), fill="", outline="", state="hidden")
                front = cv.create_polygon(*([0, 0] * 4), fill="", outline="", state="hidden")
                m1 = cv.create_line(0, 0, 0, 0, state="hidden", width=1.2)
                m2 = cv.create_line(0, 0, 0, 0, state="hidden", width=1.2)
                self._cells[(i, kk)] = (top, front, m1, m2)
            for k in range(len(trk)):
                self._panels[(i, k)] = cv.create_polygon(*([0, 0] * 4), fill="", outline="", state="hidden")
        self._veil = [cv.create_line(0, 0, 0, 0, 0, 0, state="hidden", width=1) for _ in range(TUN_VEIL_LINES)]
        self._cause_lines = [cv.create_line(0, 0, 0, 0, state="hidden", dash=(3, 3), width=1) for _ in range(TUN_MAX_CAUSES)]
        self._cause_dots = [cv.create_oval(0, 0, 0, 0, state="hidden", outline="") for _ in range(TUN_MAX_CAUSES)]
        # eclair du strobe : voile blanc trame au-dessus de la scene, sous le cadre du present et les libelles (pas d'alpha sous Tk)
        self._flash = cv.create_rectangle(0, 0, 1, 1, fill="#ffffff", outline="", stipple="gray25", state="hidden")
        self._flash_sig = None
        self._frame = cv.create_line(*([0, 0] * 5), fill="#e6e6e6", width=1.5)
        self._ins_labels = [cv.create_text(0, 0, text=core_brain.label(r), fill=core_brain.COLORS[r], font=FONT_SMALL) for r in ins]
        self._trk_labels = [cv.create_text(0, 0, text=core_brain.label(r), fill=core_brain.COLORS[r], font=FONT_SMALL,
                                           anchor="w" if k < 4 else "e") for k, r in enumerate(trk)]

    # ---- geometrie
    def _pj(self, x: float, y: float, z: float) -> tuple[float, float]:
        """Projection en perspective, ramenee dans le canvas. L'appelant garantit z >= TUN_ZMIN."""
        s = TUN_F * self.k / z
        if z < self.min_z:
            self.min_z = z
        return (min(max(self.cx + x * s, 0.0), float(self.w)), min(max(self.cy - y * s, 0.0), float(self.h)))

    def _quad(self, item, a, b, c, d) -> None:
        self.canvas.coords(item, *a, *b, *c, *d)

    def layout(self, w: int, h: int) -> None:
        self.w, self.h = max(1, w), max(1, h)
        self.k = max(0.2, min(self.w / TUN_W0, self.h / TUN_H0))
        self.cx, self.cy = self.w / 2, self.h / 2
        self.min_z = 99.0
        cv, pj = self.canvas, self._pj
        cv.coords(self._bgrect, 0, 0, self.w, self.h)
        cv.coords(self._flash, 0, 0, self.w, self.h)
        zn, zf = _tun_z(-4), _tun_z(TUN_DEPTH)
        for q, x in enumerate(TUN_LANES):
            cv.coords(self._long[q], *pj(x, TUN_YF, zn), *pj(x, TUN_YF, zf))
        for q, (x, y) in enumerate(((-TUN_XW, TUN_YF), (TUN_XW, TUN_YF), (-TUN_XW, TUN_YC), (TUN_XW, TUN_YC))):
            cv.coords(self._long[4 + q], *pj(x, y, zn), *pj(x, y, zf))
        zp = _tun_z(0)
        cv.coords(self._frame, *pj(-TUN_XW, TUN_YF, zp), *pj(TUN_XW, TUN_YF, zp), *pj(TUN_XW, TUN_YC, zp), *pj(-TUN_XW, TUN_YC, zp),
                  *pj(-TUN_XW, TUN_YF, zp))
        for kk, it in enumerate(self._ins_labels):
            x, y = pj((TUN_LANES[kk] + TUN_LANES[kk + 1]) / 2, TUN_YF, zp)
            cv.coords(it, x, min(y + 12, self.h - 8))
        for k, it in enumerate(self._trk_labels):
            side, row = (-TUN_XW if k < 4 else TUN_XW), k % 4
            x, y = pj(side, 0.9 - row * 0.48 - 0.23, zp)
            cv.coords(it, x + (6 if k < 4 else -6), y)
        self._rib_sig = [None] * TUN_SHOWN
        self._strip_sig = [None] * TUN_SHOWN

    @staticmethod
    def _cell_text(row: str, status: str, v: float, p: float) -> str:
        name = core_brain.label(row)
        if status in ("real", "surprise"):
            return f"{name} {round(v * 100)} % · " + ("surprise" if status == "surprise" else "realise")
        if status == "miss":
            return f"{name} predit {round(p * 100)} % · manque"
        return f"{name} attendu a {round(p * 100)} %"

    def _apply_cell(self, key, sig, bg: str) -> None:
        """Statut ou valeur d'une case change : on reecrit tout (remplissage, contour, epaisseur) pour ne rien garder d'un autre aspect."""
        cv = self.canvas
        top, front, m1, m2 = self._cells[key]
        status, v, p = sig[0], sig[1], sig[2]
        col = core_brain.COLORS[core_brain.INSTRUMENTS[key[1]]]
        if status in ("real", "surprise"):
            a = 0.45 + 0.55 * v
            cv.itemconfigure(top, fill=blend(col, a, bg), outline="#ffffff" if status == "surprise" else "",
                             width=1.2 if status == "surprise" else 1, state="normal")
            cv.itemconfigure(front, fill=blend(shade(col, 0.5), a, bg), outline="", width=1, state="normal")
            cv.itemconfigure(m1, state="hidden")
            cv.itemconfigure(m2, state="hidden")
        elif status == "miss":
            red = blend(MISS_FG, 0.8, bg)
            cv.itemconfigure(top, state="hidden")
            cv.itemconfigure(front, state="hidden")
            cv.itemconfigure(m1, fill=red, width=1.2, state="normal")
            cv.itemconfigure(m2, fill=red, width=1.2, state="normal")
        else:                                                    # fantome : contour fin, d'autant plus net que la prediction est forte
            line = blend(col, 0.15 + 0.6 * p, bg)
            cv.itemconfigure(top, fill="", outline=line, width=1, state="normal")
            cv.itemconfigure(front, fill="", outline=line, width=1, state="normal")
            cv.itemconfigure(m1, state="hidden")
            cv.itemconfigure(m2, state="hidden")

    def _hide_cell(self, key) -> None:
        for it in self._cells[key]:
            self.canvas.itemconfigure(it, state="hidden")

    # ---- mise a jour
    def update(self, snap: dict, causes: bool, hidden: set, now: float) -> None:
        cv, pj, C = self.canvas, self._pj, core_brain.COLORS
        self.min_z = 99.0
        header = snap.get("header") or {}
        phase = header.get("phase")
        bg = tint(phase)
        first = int(snap.get("first", 0) or 0)
        cursor = max(0.0, min(TUN_CURSOR_MAX, float(snap.get("cursor", 0.0))))
        cur = int(cursor)
        ins, trk = core_brain.INSTRUMENTS, core_brain.TRACKS
        ph_steps = snap.get("phase_steps") or ()
        veil = snap.get("veil") or ()
        hsig = tuple(sorted(hidden))
        if hsig != self._hid_sig:
            self._hid_sig = hsig
            for kk, r in enumerate(ins):
                cv.itemconfigure(self._ins_labels[kk], fill=DIM if r in hidden else C[r])
            for k, r in enumerate(trk):
                cv.itemconfigure(self._trk_labels[k], fill=DIM if r in hidden else C[r])
        # profondeurs par pas : (z du debut, z de la fin, z du milieu de la case) ; None = trop pres de la camera, pas dessine
        alive = [_tun_z(i - cursor + 0.1) >= TUN_ZMIN and _tun_z(i - cursor) >= TUN_ZMIN for i in range(TUN_SHOWN)]
        hov: list = []
        # nervures et bande des phases (le long du mur gauche)
        for i in range(TUN_SHOWN):
            ribs, strip = self._ribs[i], self._strips[i]
            if not alive[i]:
                if self._rib_sig[i] is not None:
                    for it in ribs:
                        cv.itemconfigure(it, state="hidden")
                    self._rib_sig[i] = None
                if self._strip_sig[i] is not None:
                    cv.itemconfigure(strip, state="hidden")
                    self._strip_sig[i] = None
                continue
            d = i - cursor
            z, z1 = _tun_z(d), _tun_z(d + 1)
            bar = (first + i) % 16 == 0
            if self._rib_sig[i] != bar:
                self._rib_sig[i] = bar
                cv.itemconfigure(ribs[0], fill="#3a4170" if bar else "#161b2c", state="normal")
                for it in ribs[1:]:
                    cv.itemconfigure(it, fill="#3a4170", state="normal" if bar else "hidden")
            cv.coords(ribs[0], *pj(-TUN_XW, TUN_YF, z), *pj(TUN_XW, TUN_YF, z))
            if bar:
                cv.coords(ribs[1], *pj(-TUN_XW, TUN_YF, z), *pj(-TUN_XW, TUN_YC, z))
                cv.coords(ribs[2], *pj(TUN_XW, TUN_YF, z), *pj(TUN_XW, TUN_YC, z))
                cv.coords(ribs[3], *pj(-TUN_XW, TUN_YC, z), *pj(TUN_XW, TUN_YC, z))
            ssig = (ph_steps[i] if i < len(ph_steps) else None, i <= cur, bg)
            if self._strip_sig[i] != ssig:
                self._strip_sig[i] = ssig
                cv.itemconfigure(strip, fill=blend(PHASE_COLORS.get(ssig[0], DIM), 0.6 if ssig[1] else 0.15, bg), state="normal")
            self._quad(strip, pj(-TUN_XW - 0.01, TUN_YF, z), pj(-1.25, TUN_YF, z), pj(-1.25, TUN_YF, z1), pj(-TUN_XW - 0.01, TUN_YF, z1))
        # cases (3 voies)
        new: dict = {}
        for i, row, v, p, status in snap.get("cells", ()):
            if row not in ins or not 0 <= i < TUN_SHOWN or status is None or row in hidden or not alive[i]:
                continue
            new[(i, ins.index(row))] = (status, round(float(v), 2), round(float(p), 2), bg)
        for key in [k for k in self._csig if k not in new]:
            self._hide_cell(key)
            del self._csig[key]
        cellpt: dict = {}
        for key, sig in new.items():
            if self._csig.get(key) != sig:
                self._csig[key] = sig
                self._apply_cell(key, sig, bg)
            i, kk = key
            d = i - cursor
            z0, z1, zm = _tun_z(d + 0.1), _tun_z(d + 0.9), _tun_z(d + 0.5)
            top, front, m1, m2 = self._cells[key]
            status = sig[0]
            v, p = sig[1], sig[2]
            # face avant carree, centree dans la voie : son cote (= sa hauteur) dit la force (ou la prediction pour un fantome)
            mid = (TUN_LANES[kk] + TUN_LANES[kk + 1]) / 2
            side = TUN_CELL0 + TUN_CELL1 * (v if status in ("real", "surprise") else p * 0.85)
            x0, x1 = mid - side / 2, mid + side / 2
            if status == "miss":
                x0, x1 = TUN_LANES[kk] + 0.08, TUN_LANES[kk + 1] - 0.08
            if status in ("real", "surprise"):
                y1 = TUN_YF + side
                self._quad(top, pj(x0, y1, z0), pj(x1, y1, z0), pj(x1, y1, z1), pj(x0, y1, z1))
                self._quad(front, pj(x0, TUN_YF, z0), pj(x1, TUN_YF, z0), pj(x1, y1, z0), pj(x0, y1, z0))
                px, py = pj((x0 + x1) / 2, y1, zm)
                cellpt[key] = (px, py)
                hov.append((px, py, (ins[kk], status, v, p)))
            elif status == "miss":
                cv.coords(m1, *pj(x0 + 0.1, TUN_YF, z0), *pj(x1 - 0.1, TUN_YF, z1))
                cv.coords(m2, *pj(x1 - 0.1, TUN_YF, z0), *pj(x0 + 0.1, TUN_YF, z1))
                px, py = pj((x0 + x1) / 2, TUN_YF, zm)
                hov.append((px, py, (ins[kk], status, v, p)))
            else:
                y1 = TUN_YF + side
                self._quad(top, pj(x0, y1, z0), pj(x1, y1, z0), pj(x1, y1, z1), pj(x0, y1, z1))
                self._quad(front, pj(x0, TUN_YF, z0), pj(x1, TUN_YF, z0), pj(x1, y1, z0), pj(x0, y1, z0))
                px, py = pj((x0 + x1) / 2, y1, z0)
                hov.append((px, py, (ins[kk], status, v, p)))
        # panneaux d'effets sur les murs : un seul par (pas, piste), le plus fort ; seuls le passe et le present sont poses
        active: dict = {}
        for i, track, strength, reason, cause in snap.get("fx", ()):
            if track in hidden or track not in trk or not 0 <= i <= cur or i >= TUN_SHOWN or not alive[i]:
                continue
            key = (i, trk.index(track))
            old = active.get(key)
            if old is None or float(strength) > old[0]:
                active[key] = (float(strength), reason, cause)
        for key in [k for k in self._psig if k not in active]:
            cv.itemconfigure(self._panels[key], state="hidden")
            del self._psig[key]
        flash = 0.0
        ppt: dict = {}
        for key, (s, reason, cause) in sorted(active.items()):
            i, k = key
            s = max(0.0, min(1.0, s))
            sig = (round(s, 2), bg)
            if self._psig.get(key) != sig:
                self._psig[key] = sig
                cv.itemconfigure(self._panels[key], fill=blend(C[trk[k]], 0.4 + 0.6 * s, bg), state="normal")
            d = i - cursor
            z0, z1, zm = _tun_z(d + 0.1), _tun_z(d + 0.9), _tun_z(d + 0.5)
            side, row = (-TUN_XW if k < 4 else TUN_XW), k % 4
            ya, yb = 0.9 - row * 0.48 - 0.42, 0.9 - row * 0.48 - 0.04
            self._quad(self._panels[key], pj(side, ya, z0), pj(side, yb, z0), pj(side, yb, z1), pj(side, ya, z1))
            px, py = pj(side, (ya + yb) / 2, zm)
            ppt[key] = (px, py)
            hov.append((px, py, reason))
            if trk[k] == "strobe" and cursor - i < 2.5:
                flash = max(flash, (1 - (cursor - i) / 2.5) * (0.5 if reason.startswith("drop") else 0.22))
        # eclair : le rectangle de fond s'eclaircit, et un voile blanc trame couvre toute la scene (trame plus dense au debut de l'eclair)
        fill = blend("#ffffff", flash, bg) if flash > 0.004 else bg
        if fill != self._bg_fill:
            self._bg_fill = fill
            cv.itemconfigure(self._bgrect, fill=fill)
        fsig = None if flash <= 0.004 else ("gray75" if flash > 0.3 else "gray50" if flash > 0.12 else "gray25")
        if fsig != self._flash_sig:
            self._flash_sig = fsig
            if fsig is None:
                cv.itemconfigure(self._flash, state="hidden")
            else:
                cv.itemconfigure(self._flash, stipple=fsig, state="normal")
        # voile : TUN_VEIL_LINES lignes en sinusoide au plafond, du present vers le passe
        vtop = min(cur + 1, len(veil) - 1)
        vcol = None
        if veil and vtop >= 0:
            vcol = "#ffffff" if veil[min(cur, len(veil) - 1)][0] == "drop" else PHASE_COLORS["build"]
        for q, it in enumerate(self._veil):
            pts: list = []
            any_on = False
            if vcol is not None:
                x = 0.0 if TUN_VEIL_LINES == 1 else -1.2 + q * 2.4 / (TUN_VEIL_LINES - 1)
                for i in range(vtop, -1, -1):
                    z = _tun_z(i - cursor)
                    if z < 0.35:
                        break
                    vv = veil[i]
                    if vv[1] > 0:
                        any_on = True
                    pts += pj(x, TUN_YC - vv[1] * 0.9 * (0.55 + 0.45 * math.sin((first + i) * 0.32 + q + now * 2.2)), z)
            if any_on and len(pts) >= 4:
                cv.coords(it, *pts)
                vs = (vcol, bg)
                if self._veil_sig[q] != vs:
                    self._veil_sig[q] = vs
                    cv.itemconfigure(it, fill=blend(vcol, 0.55, bg), state="normal")
            elif self._veil_sig[q] is not None:                  # None = cachee
                self._veil_sig[q] = None
                cv.itemconfigure(it, state="hidden")
        # causes : de la case qui a frappe (meme pas) au panneau de l'effet, un petit point a l'arrivee
        nc = 0
        if causes:
            for key, (s, _r, cause) in sorted(active.items()):
                if not cause or cause not in ins or nc >= TUN_MAX_CAUSES:
                    continue
                src = cellpt.get((key[0], ins.index(cause)))
                if src is None:
                    continue
                x, y = ppt[key]
                x, y = min(max(x, 2.5), self.w - 2.5), min(max(y, 2.5), self.h - 2.5)       # le point reste entier dans le canvas
                col = blend(C[trk[key[1]]], 0.4 + 0.6 * max(0.0, min(1.0, s)), bg)
                cv.coords(self._cause_lines[nc], src[0], src[1], x, y)
                cv.coords(self._cause_dots[nc], x - 2, y - 2, x + 2, y + 2)
                if self._cause_sig[nc] != col:
                    self._cause_sig[nc] = col
                    cv.itemconfigure(self._cause_lines[nc], fill=col)
                    cv.itemconfigure(self._cause_dots[nc], fill=col)
                cv.itemconfigure(self._cause_lines[nc], state="normal")
                cv.itemconfigure(self._cause_dots[nc], state="normal")
                nc += 1
        for idx in range(nc, self._used["causes"]):
            cv.itemconfigure(self._cause_lines[idx], state="hidden")
            cv.itemconfigure(self._cause_dots[idx], state="hidden")
        self._used["causes"] = nc
        self._hov = hov

    # ---- survol
    def hover(self, x: float, y: float) -> str | None:
        best, dist = None, 10.0
        for px, py, text in self._hov:
            d = ((px - x) ** 2 + (py - y) ** 2) ** 0.5
            if d < dist:
                best, dist = text, d
        if isinstance(best, tuple):                              # texte d'une case forme au survol seulement (budget, tache 9)
            best = self._cell_text(*best)
        return best


VIEWS: dict = {name: BlankView for name in VIEW_NAMES}
VIEWS["iso"] = IsoView
VIEWS["sismo"] = SismoView
VIEWS["mandala"] = MandalaView
VIEWS["tunnel"] = TunnelView


# ---------------------------------------------------------------------------------------------------------------------------
# Une surface : les widgets du panneau dans un parent (raccroche dans le panneau etendu, ou dans la fenetre detachee)
# ---------------------------------------------------------------------------------------------------------------------------

def _button(parent, text, command, width=None) -> tk.Button:
    b = tk.Button(parent, text=text, command=command, relief="flat", bd=0, padx=7, pady=1, cursor="hand2", bg=BTN_BG, fg=REASON_FG,
                  activebackground=BTN_BG, activeforeground=BTN_ON, font=FONT, highlightthickness=1, highlightbackground="#2a3050")
    if width:
        b.configure(width=width)
    return b


def _rich(parent, height: int, font=FONT) -> tk.Text:
    t = tk.Text(parent, height=height, bg=PANEL_BG, fg=FG, bd=0, highlightthickness=0, font=font, wrap="none", cursor="arrow",
                padx=2, pady=0, takefocus=0)
    t.configure(state="disabled")
    return t


class _Surface:
    def __init__(self, panel: "BrainPanel", parent, floating: bool) -> None:
        self.panel, self.floating = panel, floating
        self.view = None
        self.view_key = None
        self.last_size = (0, 0)
        self.frozen = False
        self.last = {}                                             # dernier contenu ecrit de chaque zone de texte
        frame = self.frame = tk.Frame(parent, bg=PANEL_BG)
        bar = tk.Frame(frame, bg=PANEL_BG)
        bar.pack(side="top", fill="x", padx=4, pady=(4, 2))
        self.view_buttons = {}
        for name in VIEW_NAMES:
            b = _button(bar, name, lambda n=name: panel.set_view(n))
            b.pack(side="left", padx=(0, 3))
            self.view_buttons[name] = b
        self.causes_btn = _button(bar, "causes", panel.toggle_causes)
        self.causes_btn.pack(side="left", padx=(8, 3))
        self.pause_btn = _button(bar, "pause", panel.toggle_pause)
        self.pause_btn.pack(side="left", padx=(0, 3))
        self.detach_btn = _button(bar, "dock" if floating else "detach", panel.dock if floating else panel.detach, width=DETACH_W)
        self.detach_btn.pack(side="right")
        tip = panel.Tooltip
        if tip is not None:
            try:
                tip(self.detach_btn, "Raccroche le cerveau dans le panneau etendu (fermer la fenetre fait pareil)." if floating
                    else "Ouvre le cerveau dans une fenetre a part (position et taille retenues) ; la fermer le raccroche ici.")
                tip(self.causes_btn, "Relie chaque effet a la frappe qui l'a declenche (trait pointille ; « <- kick » dans le fil).")
                tip(self.pause_btn, "Fige la vue (l'appli continue de tourner).")
            except Exception:
                pass
        # curseur d'angle de la vue iso (comme la maquette : dans la barre, a droite) ; place reservee (largeur fixe), montre en iso seulement
        self.angle_box = tk.Frame(bar, bg=PANEL_BG, width=ANGLE_BOX_W, height=22)
        self.angle_box.pack_propagate(False)
        self.angle_box.pack(side="left", padx=(8, 0))
        self.angle_scale = None
        try:
            inner = tk.Frame(self.angle_box, bg=PANEL_BG)
            tk.Label(inner, text="angle", bg=PANEL_BG, fg=MUTED, font=FONT_SMALL).pack(side="left", padx=(0, 3))
            value = tk.Label(inner, text=str(IsoView.angle), bg=PANEL_BG, fg=REASON_FG, font=FONT_SMALL, width=2)
            sc = tk.Scale(inner, from_=ISO_ANGLE[0], to=ISO_ANGLE[1], orient="horizontal", length=64, width=8, sliderlength=10,
                          showvalue=False, bg=MUTED, troughcolor="#2a3050", bd=0, highlightthickness=0, activebackground=BTN_ON,
                          sliderrelief="flat")
            sc.set(IsoView.angle)
            sc.configure(command=lambda v: (value.configure(text=str(int(float(v)))), panel.set_angle(int(float(v)))))
            sc.pack(side="left")
            value.pack(side="left", padx=(3, 0))
            self.angle_inner, self.angle_scale = inner, sc
        except tk.TclError:
            self.angle_scale = None
        self.topmost_var = None
        if floating:
            self.topmost_var = tk.IntVar(value=int(float(panel.params.get("brain_topmost", 0)) >= 0.5))
            tk.Checkbutton(bar, text="on top", variable=self.topmost_var, command=panel.apply_topmost, bg=PANEL_BG, fg=MUTED,
                           selectcolor=BTN_BG, activebackground=PANEL_BG, activeforeground=FG, font=FONT, bd=0,
                           highlightthickness=0).pack(side="right", padx=6)
        self.h1 = _rich(frame, 1, FONT_HEAD)
        self.h1.pack(side="top", fill="x", padx=4)
        self.h2 = _rich(frame, 1)
        self.h2.pack(side="top", fill="x", padx=4, pady=(0, 2))
        # le bas (jauges, fil, legende) d'abord : quand la place manque, c'est la vue qui retrecit
        gauges = tk.Frame(frame, bg=PANEL_BG)
        gauges.pack(side="bottom", fill="x", padx=4, pady=(2, 4))
        self.gauges = {}
        for key in ("sources", "auto", "sante"):
            # 3 lignes des la creation : la vue ne retrecit pas au premier dessin
            lab = tk.Label(gauges, text=sources_text({}, {}) if key == "sources" else ("health" if key == "sante" else key) + "\n-\n-", bg=PANEL_BG, fg=MUTED, font=FONT, justify="left", anchor="nw")
            lab.pack(side="left", fill="both", expand=True)
            self.gauges[key] = lab
        self.feed = _rich(frame, core_brain.FEED_LINES)
        self.feed.pack(side="bottom", fill="x", padx=4)
        legend = tk.Frame(frame, bg=PANEL_BG)
        legend.pack(side="bottom", fill="x", padx=4, pady=(2, 2))
        self.pills = {}
        # legende de la maquette : petits carres de couleur (plein = realise, contour = predit, contour blanc = surprise, x rouge = manque)
        for sym, col, word in reversed((("\u25a0", BTN_ON, "realise"), ("\u25a1", BTN_ON, "predit"), ("\u25a3", "#ffffff", "surprise"),
                                        ("x", MISS_FG, "manque"))):
            tk.Label(legend, text=word, bg=PANEL_BG, fg=MUTED, font=FONT_SMALL, padx=0).pack(side="right", padx=(0, 6))
            tk.Label(legend, text=sym, bg=PANEL_BG, fg=col, font=FONT_SMALL, padx=0).pack(side="right", padx=(0, 2))
        for name in core_brain.TRACKS:                             # pastilles : un clic masque / montre la piste
            pill = tk.Label(legend, text=core_brain.label(name), bg=PANEL_BG, font=FONT_SMALL, cursor="hand2", padx=1)
            pill.pack(side="left", padx=(0, 3))
            pill.bind("<Button-1>", lambda _e, n=name: panel.toggle_track(n))
            self.pills[name] = pill
        # un canvas par vue (cle : nom de la vue ou « free »), construit une fois puis montre / cache : revenir a une vue deja vue
        # (grille perdue puis retrouvee au drop, bascule de vue) ne recree aucun element (iso : ~3100 elements, 70-215 ms)
        self._views: dict = {}                                     # cle -> [canvas, vue, taille, fond]
        self.canvas = self._new_canvas()
        self.canvas.pack(side="top", fill="both", expand=True, padx=4)
        self._spare = self.canvas                                  # canvas du depart, pris par la premiere vue
        self.restyle()

    def _new_canvas(self) -> tk.Canvas:
        cv = tk.Canvas(self.frame, bg=tint(None), highlightthickness=0, bd=0, width=200, height=80)
        cv.bind("<Motion>", self.on_motion)
        cv.bind("<Leave>", lambda _e: self.hide_tip())
        return cv

    def show_view(self, key: str, cls, on_fail) -> list:
        """Rend l'entree [canvas, vue, taille, fond] de la vue `key` et la montre (cache la precedente). La vue n'est construite qu'a
        sa premiere apparition (ou si sa classe a change, ou si sa construction avait leve)."""
        entry = self._views.get(key)
        if entry is None:
            cv = self._spare if self._spare is not None else self._new_canvas()
            self._spare = None
            entry = self._views[key] = [cv, None, (0, 0), None]
        cv = entry[0]
        if entry[1] is None or type(entry[1]) is not cls:
            cv.delete("all")
            entry[1], entry[2] = None, (0, 0)
            try:
                entry[1] = cls(cv)
            except Exception as exc:
                on_fail(key, exc)
        if cv is not self.canvas:
            old = self.canvas
            self.hide_tip()
            self.last_size = (old.winfo_width(), old.winfo_height())   # taille du canvas qui arrive tant que Tk ne l'a pas place
            old.pack_forget()
            cv.pack(side="top", fill="both", expand=True, padx=4)  # meme place : le canvas est toujours le dernier empaquete
            self.canvas = cv
        self.view, self.view_key = entry[1], key
        return entry

    # ---- etat des boutons et des pastilles
    def restyle(self) -> None:
        p = self.panel
        for name, b in self.view_buttons.items():
            on = name == p.view_name
            b.configure(fg=BTN_ON if on else REASON_FG, highlightbackground=BTN_ON if on else "#2a3050")
        self.causes_btn.configure(fg=BTN_ON if p.causes else REASON_FG, highlightbackground=BTN_ON if p.causes else "#2a3050")
        self.pause_btn.configure(fg=BTN_ON if p.paused else REASON_FG, highlightbackground=BTN_ON if p.paused else "#2a3050")
        if self.angle_scale is not None:
            if p.view_name == "iso":
                self.angle_inner.place(x=0, rely=0.5, anchor="w")
                if int(self.angle_scale.get()) != IsoView.angle:
                    self.angle_scale.set(IsoView.angle)
            else:
                self.angle_inner.place_forget()
        for name, pill in self.pills.items():
            off = name in p.hidden
            pill.configure(fg=DIM if off else core_brain.COLORS.get(name, FG), font=FONT_SMALL + (("overstrike",) if off else ()))

    # ---- texte riche (une zone = liste de (texte, couleur)), reecrit seulement s'il change
    def write(self, widget: tk.Text, key: str, lines: list) -> None:
        sig = tuple(tuple(line) for line in lines)
        if self.last.get(key) == sig:
            return
        self.last[key] = sig
        widget.configure(state="normal")
        widget.delete("1.0", "end")
        for i, line in enumerate(lines):
            if i:
                widget.insert("end", "\n")
            for text, color in line:
                tag = "c" + color.lstrip("#")
                widget.tag_configure(tag, foreground=color)
                widget.insert("end", text, tag)
        widget.configure(state="disabled")

    def set_label(self, key: str, text: str) -> None:
        lab = self.gauges[key]
        if lab.cget("text") != text:
            lab.configure(text=text)

    # ---- bulle de survol (elements du canvas, crees a la demande)
    def on_motion(self, event) -> None:
        text = None
        if self.view is not None:
            try:
                text = self.view.hover(event.x, event.y)
            except Exception:
                text = None
        if not text:
            self.hide_tip()
            return
        cv = self.canvas
        if not cv.find_withtag("tip_text"):
            cv.create_rectangle(0, 0, 0, 0, fill=TIP_BG, outline=TIP_BORDER, tags=("tip", "tip_box"))
            cv.create_text(0, 0, text="", fill=FG, font=FONT, anchor="nw", width=300, tags=("tip", "tip_text"))
        cv.itemconfigure("tip_text", text=text)
        x = min(event.x + 12, max(4, cv.winfo_width() - 310))
        y = event.y + 12
        cv.coords("tip_text", x + 6, y + 4)
        bx = cv.bbox("tip_text")
        if bx:
            cv.coords("tip_box", bx[0] - 6, bx[1] - 4, bx[2] + 6, bx[3] + 4)
            if bx[3] + 4 > cv.winfo_height():                       # trop bas : au-dessus du pointeur
                dy = (bx[3] - bx[1]) + 30
                cv.move("tip", 0, -dy)
        cv.itemconfigure("tip", state="normal")
        cv.tag_raise("tip")

    def hide_tip(self) -> None:
        try:
            self.canvas.itemconfigure("tip", state="hidden")
        except tk.TclError:
            pass

    # ---- REC
    def show_frozen(self) -> None:
        cv = self.canvas
        w, h = max(cv.winfo_width(), 1), max(cv.winfo_height(), 1)
        cv.delete("frozen")
        cv.create_rectangle(w / 2 - 110, h / 2 - 16, w / 2 + 110, h / 2 + 16, fill=PANEL_BG, outline=MISS_FG, tags=("frozen",))
        cv.create_text(w / 2, h / 2, text=FROZEN_TEXT, fill=MISS_FG, font=FONT_HEAD, tags=("frozen",))
        self.frozen = True

    def hide_frozen(self) -> None:
        for e in self._views.values():
            e[0].delete("frozen")
        self.canvas.delete("frozen")
        self.frozen = False

    def visible(self) -> bool:
        try:
            if not self.frame.winfo_ismapped():
                return False
            top = self.frame.winfo_toplevel()
            return top.state() not in ("iconic", "withdrawn")
        except tk.TclError:
            return False


# ---------------------------------------------------------------------------------------------------------------------------
# Le panneau
# ---------------------------------------------------------------------------------------------------------------------------

class BrainPanel:
    """Panneau du cerveau d'une fenetre de reglages (une instance par construction de la fenetre)."""

    def __init__(self, ns) -> None:
        self.root = ns.root
        self.s = ns.s
        self.parent = ns.scene_top                              # parent persistant (comme l'espace de controle global)
        self.master_box = ns.master_box
        self.Tooltip = getattr(ns, "Tooltip", None)
        self.STATS = getattr(ns, "STATS", None)
        p = self.params
        view = str(p.get("brain_view", "iso"))
        self.view_name = view if view in VIEW_NAMES else "iso"
        self.causes = float(p.get("brain_causes", 1.0)) >= 0.5
        self.hidden = {n for n in str(p.get("brain_hidden", "")).split(",") if n in core_brain.TRACKS}
        self.paused = False
        self.window = None                                      # Toplevel quand le panneau est detache
        self._docked = None
        self._float = None
        self._wrap = None
        self._slot = None                                       # emplacement de droite du panneau etendu (largeur reservee)
        self.slot_w = 0
        self._geom_job = None                                   # sauvegarde de la geometrie, 0,5 s apres le dernier deplacement
        self.toggle_button = None
        self._errors: set = set()
        self._sched_at = -1e9                                   # dernier rafraichissement demande par l'ordonnanceur

    @property
    def params(self) -> dict:
        return self.s.params

    def _flag(self, key: str) -> bool:
        try:
            return float(self.params.get(key, 0.0)) >= 0.5
        except (TypeError, ValueError):
            return False

    @property
    def detached(self) -> bool:
        return self.window is not None

    @property
    def current(self):
        """Vue dessinee par la surface active (tests)."""
        surf = self._active()
        return None if surf is None else surf.view

    # ---- construction et place
    def box(self) -> tk.Frame:
        """Panneau raccroche : construit une seule fois (parent persistant), pose ensuite dans chaque nouvelle liste du panneau etendu."""
        surf = self._docked
        try:
            if surf is not None and surf.frame.winfo_exists():
                return surf.frame
        except tk.TclError:
            pass
        surf = self._docked = _Surface(self, self.parent, floating=False)
        surf.frame.pack_propagate(False)                         # sa largeur est celle de l'emplacement reserve (slot)
        return surf.frame

    def _brain_shown(self) -> bool:
        return self._flag("brain_on") and not self.detached

    def place(self, wrap) -> tk.Frame:
        """Pose dans `wrap` (corps du panneau etendu, a droite) l'espace VJ ou le cerveau selon l'etat ; rend le cadre pose."""
        self._wrap = wrap
        return self._show_in_wrap()

    def _slot_frame(self) -> tk.Frame:
        """Emplacement de droite (parent persistant), de largeur RESERVEE : la plus grande vue pour l'espace VJ et le cerveau, qui ne fait
        que grandir (comme `layout["live_w"]`) ; la liste a gauche garde sa largeur quand on bascule (regle « rien ne bouge »)."""
        slot = self._slot
        try:
            if slot is not None and slot.winfo_exists():
                return slot
        except tk.TclError:
            pass
        slot = self._slot = tk.Frame(self.parent, bg=PANEL_BG, width=self.slot_w)
        slot.pack_propagate(False)
        return slot

    def sync_slot(self) -> None:
        """Agrandit l'emplacement si l'espace VJ demande plus (ses onglets se remplissent a la premiere selection). Jamais plus petit."""
        slot = self._slot
        try:
            if slot is None or not slot.winfo_exists() or not slot.winfo_ismapped():
                return
            want = max(self.slot_w, int(self.master_box().winfo_reqwidth()), BOX_W)
            if want != self.slot_w:
                self.slot_w = want
                slot.configure(width=want)
        except tk.TclError:
            pass

    def _show_in_wrap(self):
        wrap = self._wrap
        if wrap is None:
            return None
        try:
            if not wrap.winfo_exists():
                return None
            mbox = self.master_box()
            self.slot_w = max(self.slot_w, int(mbox.winfo_reqwidth()), BOX_W)
            slot = self._slot_frame()
            if int(slot.cget("width")) != self.slot_w:
                slot.configure(width=self.slot_w)
            if slot.winfo_manager() != "pack" or str(slot.pack_info().get("in")) != str(wrap):
                slaves = [w for w in wrap.pack_slaves() if w is not slot]
                if slaves:
                    slot.pack(in_=wrap, side="right", fill="y", before=slaves[0])
                else:
                    slot.pack(in_=wrap, side="right", fill="y")
            shown = self.box() if self._brain_shown() else mbox
            other = mbox if self._brain_shown() else (self._docked.frame if self._docked is not None else None)
            if other is not None and other.winfo_manager():
                other.pack_forget()
            if shown.winfo_manager() != "pack" or str(shown.pack_info().get("in")) != str(slot):
                shown.pack(in_=slot, fill="both", expand=True)
            # un esclave place « dans » un autre doit le surplomber : l'emplacement, la boite, puis ce qui est pose dans la boite
            # (l'onglet Affichage, enfant de la barre pose dans un onglet de l'espace VJ, restait cache sinon)
            slot.lift()
            shown.lift()
            inside = str(shown) + "."
            for w in self.parent.winfo_children():
                if w not in (slot, shown) and w.winfo_manager() == "pack" and str(w.pack_info().get("in")).startswith(inside):
                    w.lift()
        except tk.TclError:
            return None
        self._refresh_toggle()
        return shown

    def head_button(self, head) -> tk.Button:
        """Bouton « brain / VJ » (largeur fixe) dans la tete du panneau etendu, reconstruit avec elle."""
        b = tk.Button(head, text="", command=self.toggle, relief="flat", bd=0, padx=8, cursor="hand2", bg=BTN_BG, fg=BTN_ON,
                      activebackground=BTN_BG, activeforeground=FG, font=FONT, width=TOGGLE_W)
        b.pack(side="right", padx=(2, 6), pady=2)
        if self.Tooltip is not None:
            try:
                self.Tooltip(b, "Bascule l'espace de controle global du Mode VJ et le cerveau (ce que l'overlay entend et decide).")
            except Exception:
                pass
        self.toggle_button = b
        self._refresh_toggle()
        return b

    def _refresh_toggle(self) -> None:
        b = self.toggle_button
        if b is None:
            return
        want = "VJ" if self._brain_shown() else "brain"
        try:
            if b.cget("text") != want:
                b.configure(text=want)
        except tk.TclError:
            self.toggle_button = None

    def _restyle(self) -> None:
        for surf in (self._docked, self._float):
            if surf is not None:
                try:
                    surf.restyle()
                except tk.TclError:
                    pass

    # ---- actions
    def toggle(self) -> None:
        """Espace VJ <-> cerveau. Detache : le cerveau revient dans le panneau."""
        if self.detached:
            self.dock()
            return
        self.params["brain_on"] = 0.0 if self._flag("brain_on") else 1.0
        self._show_in_wrap()
        self._persist()
        self.tick()

    def show_vj(self) -> None:
        """Remet l'espace VJ a sa place (onglet Affichage demande ailleurs)."""
        if self._brain_shown():
            self.params["brain_on"] = 0.0
            self._show_in_wrap()
            self._persist()

    def detach(self) -> None:
        if self.detached:
            return
        try:
            top = tk.Toplevel(self.root)
        except tk.TclError:
            return
        top.title(WINDOW_TITLE)
        top.configure(bg=PANEL_BG)
        geom = str(self.params.get("brain_geom", ""))
        try:
            top.geometry(geom if GEOM_RE.match(geom) else "760x620")
        except tk.TclError:
            top.geometry("760x620")
        self.window = top
        self._float = _Surface(self, top, floating=True)
        self._float.frame.pack(fill="both", expand=True)
        top.protocol("WM_DELETE_WINDOW", self.dock)
        top.bind("<Configure>", self._on_configure, add="+")
        self.params["brain_detached"] = 1.0
        self.apply_topmost()                                     # (sauve l'etat)
        self._show_in_wrap()                                     # l'espace VJ reprend sa place
        self.tick()

    def _on_configure(self, event) -> None:
        top = self.window
        if top is None or event.widget is not top:
            return
        if self._geom_job is not None:                           # un deplacement emet des dizaines d'evenements : on attend la fin
            try:
                self.root.after_cancel(self._geom_job)
            except tk.TclError:
                pass
        try:
            self._geom_job = self.root.after(GEOM_SAVE_MS, self._geom_settled)
        except tk.TclError:
            self._geom_job = None

    def _geom_settled(self) -> None:
        self._geom_job = None
        self._remember_geom()
        self._persist()

    def _remember_geom(self) -> None:
        try:
            if self.window is not None and self.window.state() == "normal":
                self.params["brain_geom"] = self.window.geometry()
        except tk.TclError:
            pass

    def apply_topmost(self) -> None:
        surf = self._float
        if surf is None or surf.topmost_var is None:
            return
        try:
            on = int(surf.topmost_var.get())
            self.params["brain_topmost"] = float(on)
            if self.window is not None:
                self.window.attributes("-topmost", bool(on))
        except tk.TclError:
            pass
        self._persist()

    def _persist(self) -> None:
        """Etat du panneau sauve tout seul (brain_ui.json), sans attendre P : retrouve au prochain lancement."""
        core_params.save_brain_ui(self.params)

    def dock(self) -> None:
        """Raccroche le cerveau (fermer la fenetre detachee) ; il reprend sa place dans le panneau etendu."""
        top = self.window
        if top is None:
            return
        if self._geom_job is not None:
            try:
                self.root.after_cancel(self._geom_job)
            except tk.TclError:
                pass
            self._geom_job = None
        self._remember_geom()
        self.window, self._float = None, None
        try:
            top.destroy()
        except tk.TclError:
            pass
        self.params["brain_detached"] = 0.0
        self.params["brain_on"] = 1.0
        self._show_in_wrap()
        self._persist()
        self.tick()

    def set_view(self, name: str) -> None:
        if name not in VIEW_NAMES:
            return
        self.view_name = name
        self.params["brain_view"] = name
        self._persist()
        for surf in (self._docked, self._float):
            if surf is not None:
                surf.view_key = None                             # la vue (construite une fois, gardee) est montree au prochain dessin
        self._restyle()
        self.tick()

    def set_angle(self, angle: int) -> None:
        """Curseur d'angle de la barre : toutes les vues iso construites (raccrochee et detachee) suivent."""
        if int(angle) == IsoView.angle:
            return
        IsoView.angle = max(ISO_ANGLE[0], min(ISO_ANGLE[1], int(angle)))
        for surf in (self._docked, self._float):
            entry = surf._views.get("iso") if surf is not None else None
            if entry is not None and entry[1] is not None:
                entry[1].set_angle(IsoView.angle)
        self._restyle()

    def toggle_causes(self) -> None:
        self.causes = not self.causes
        self.params["brain_causes"] = 1.0 if self.causes else 0.0
        self._persist()
        self._restyle()
        self.tick()

    def toggle_pause(self) -> None:
        self.paused = not self.paused
        self._restyle()

    def toggle_track(self, name: str) -> None:
        if name in self.hidden:
            self.hidden.discard(name)
        else:
            self.hidden.add(name)
        self.params["brain_hidden"] = ",".join(n for n in core_brain.TRACKS if n in self.hidden)
        self._persist()
        self._restyle()
        self.tick()

    # ---- rafraichissement
    def _active(self):
        if self.window is not None:
            return self._float
        if self._brain_shown():
            return self._docked
        return None

    def scheduled_tick(self) -> None:
        """Appel de l'ordonnanceur (toutes les TICK_MS) : la vue choisie peut demander une cadence plus lente (VIEW_TICK_MS). Les
        actions (bascule, vue, causes...) appellent `tick` directement et redessinent tout de suite."""
        period = VIEW_TICK_MS.get(self.view_name, TICK_MS) / 1000.0
        t = time.perf_counter()
        if t - self._sched_at < period - 0.01:                   # marge : l'ordonnanceur peut arriver un peu en avance
            return
        self._sched_at = t
        self.tick()

    def tick(self, now: float | None = None) -> None:
        """Rafraichissement (ordonnanceur de la fenetre, ~20 Hz) : rien si le panneau n'est pas visible, fige pendant un REC."""
        if self.STATS is not None:
            with self.STATS.timed("brain.tick"):
                self._tick(now)
        else:
            self._tick(now)

    def _recording(self) -> bool:
        rec = getattr(self.s, "recording", None)
        return bool(rec is not None and getattr(rec, "active", False))

    def _tick(self, now: float | None) -> None:
        surf = self._active()
        if surf is None or not surf.visible():
            return
        if self._recording():
            if not surf.frozen:
                surf.show_frozen()                               # une derniere fois, puis plus rien
            return
        if surf.frozen:
            surf.hide_frozen()
        if self.paused:
            return
        log = getattr(self.s, "brain", None)
        if log is None:
            return
        now = time.monotonic() if now is None else float(now)
        tunnel = self.view_name == "tunnel"
        snap = core_brain.snapshot(log, now, rolling=tunnel, depth=TUN_DEPTH if tunnel else None)     # le tunnel ne calcule que ce qu'il dessine
        self._draw(surf, snap, now)

    def _draw(self, surf: _Surface, snap: dict, now: float) -> None:
        self._draw_text(surf, snap)
        key = self.view_name if snap.get("grid") else "free"
        cls = FreeView if key == "free" else VIEWS.get(key, BlankView)
        entry = surf.show_view(key, cls, self._fail)             # vue deja construite : seulement montree (aucun element recree)
        cv, view = entry[0], entry[1]
        size = (cv.winfo_width(), cv.winfo_height())
        if (size[0] <= 1 or size[1] <= 1) and surf.last_size[0] > 1:
            size = surf.last_size                                # canvas tout juste montre, pas encore place par Tk
        header = snap.get("header", {})
        bg = tint(header.get("phase"))
        if entry[3] != bg:
            entry[3] = bg
            cv.configure(bg=bg)
        if view is not None:
            try:
                if entry[2] != size:
                    entry[2] = size
                    view.layout(*size)
                view.update(snap, self.causes, set(self.hidden), now)
            except Exception as exc:
                self._fail(key, exc)

    def _fail(self, key: str, exc: BaseException) -> None:
        """Une vue qui leve ne fait pas tomber le panneau ; l'erreur est journalisee une fois par vue et par type d'erreur."""
        sig = (key, type(exc).__name__)
        if sig in self._errors:
            return
        self._errors.add(sig)
        sessionlog.error(f"Cerveau : la vue {key} a echoue", exc)

    def _draw_text(self, surf: _Surface, snap: dict) -> None:
        h = snap.get("header", {})
        bpm, conf = h.get("bpm"), h.get("confidence")
        line1 = [((f"{bpm:.1f}" if bpm else "--") + " BPM", FG)]
        if conf is not None:
            line1.append((" stable" if h.get("stable") else f" conf {conf * 100:.0f} %", BTN_ON if h.get("stable") else MUTED))
        if h.get("origin"):
            line1.append((f"  grid: {h['origin']}", DIM))
        phase = h.get("phase")
        if phase:
            line1.append(("   " + phase, PHASE_COLORS.get(phase, FG)))
            if h.get("phase_s") is not None:
                line1.append((" " + _mmss(h["phase_s"]), DIM))
        if not snap.get("grid"):
            line1.append(("   " + NO_TEMPO, MUTED))
        elif self.view_name == "tunnel":
            line1.append((f"   {TUN_DEPTH // core_brain.STEPS_PER_BAR} next bars predicted", FG))
        elif h.get("page") is not None:
            line1.append((f"   page {h['page']} \u00b7 bar {h['bar']}/4", FG))
        ok, miss, sur = snap.get("score", (0, 0, 0))
        line2 = [("predictions ", MUTED), (f"{ok}/{ok + miss}", BTN_ON), (" realisees \u00b7 ", MUTED), (str(sur), "#ffffff"),
                 (" surprises \u00b7 ", MUTED), (str(miss), MISS_FG), (" manquees", MUTED)]
        cycles = h.get("cycles")
        if cycles:                                                 # temps restants avant la fin des cycles de 8, 16 et 32 temps
            line2.append(("   end " + " ".join(f"{n}\u00b7{cycles[n]}" for n in sorted(cycles)), MUTED))
        if h.get("build_s") is not None:
            line2.append((build_text(h), PHASE_COLORS["build"]))
        if h.get("next_scene"):
            left = h.get("next_scene_s")
            line2.append((f"   next \u00b7 {h['next_scene']}" + (f" ({max(0.0, left):.0f} s)" if left is not None else ""), MUTED))
        surf.write(surf.h1, "h1", [line1])
        surf.write(surf.h2, "h2", [line2])
        feed = []
        for clock, fam, reason, cause in reversed(snap.get("feed", [])):          # le plus recent en haut
            if fam in self.hidden:
                continue
            line = [(clock + " ", DIM), (core_brain.label(fam), core_brain.COLORS.get(fam, FG)), (" " + reason, REASON_FG)]
            if cause and self.causes:
                line.append((" <- " + core_brain.label(cause), DIM))
            feed.append(line)
        surf.write(surf.feed, "feed", feed)
        g = snap.get("gauges") or {}
        surf.set_label("sources", sources_text(g.get("sources") or {}, snap.get("bands") or {}))
        auto = g.get("auto") or {}

        def dot(flag) -> str:
            return "\u25cf" if flag else "\u25cb"
        fxm = auto.get("fx_master")
        surf.set_label("auto", "auto\n" + (f"fx master {fxm * 100:.0f} %" if fxm is not None else "fx master -") + "\n"
                       + f"{dot(auto.get('phases'))} phases {dot(auto.get('strobe'))} strobe {dot(auto.get('tempo'))} tempo "
                       + f"{dot(auto.get('vj'))} VJ")
        gpu = g.get("gpu")
        gpu_txt = "GPU -" if not gpu else f"GPU {gpu.get('ms', 0.0):.1f}/{gpu.get('budget_ms', 0.0):.0f} ms" + (" !" if gpu.get("over") else "")
        surf.set_label("sante", "health\n" + f"{g.get('fps', 0.0) or 0.0:.0f} fps  {gpu_txt}\n"
                       + f"latency {g.get('latency_ms', 0.0) or 0.0:.0f} ms  REC {'on' if g.get('rec') else 'off'}")


def install(ns) -> BrainPanel:
    """Panneau du cerveau d'une construction de la fenetre ; `ns` : root, s, scene_top, master_box, Tooltip, STATS, ui_every, after.
    Rouvre la fenetre detachee si elle l'etait (`brain_detached`, portee « device », relu de brain_ui.json). `after` (celui de la
    construction, coupe quand la fenetre est reconstruite) : une reconstruction dans les 200 ms n'ouvre pas deux fenetres."""
    panel = BrainPanel(ns)
    every = getattr(ns, "ui_every", None)
    if every is not None:
        every("brain", TICK_MS, panel.scheduled_tick)
        every("brain_slot", 300, panel.sync_slot)
    if panel._flag("brain_detached"):
        later = getattr(ns, "after", None)
        try:
            if later is not None:
                later(200, panel.detach)
            else:
                ns.root.after(200, panel.detach)
        except tk.TclError:
            pass
    return panel
