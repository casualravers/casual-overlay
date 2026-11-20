"""Panneau du visualizer du cerveau (issue #192) : ce que l'overlay entend et decide, relu dans `Session.brain` (core/brain.py).

Place : le panneau etendu du Mode VJ, a l'emplacement de l'espace de controle global (`master_box`) ; un bouton « brain / VJ » de la
tete du panneau bascule l'un et l'autre. Bouton « detach » : fenetre a part (Toplevel), fermer la fenetre = la raccrocher (`dock`).

Contenu : barre (vues, causes, pause, detach), en-tete (BPM, phase, page, score, fins de cycles, prochaine scene), la vue (un Canvas),
legende et pastilles pour masquer des pistes, fil des 4 derniers evenements, jauges (sources, auto, sante). Les vues ne font que
projeter l'instantane (`core.brain.snapshot`) ; interface d'une vue : `View(canvas)`, `.layout(w, h)`, `.update(snap, causes, hidden,
now)`, `.hover(x, y) -> str | None` ; registre `VIEWS` (les vraies vues s'y inscrivent, `BlankView` en attendant). Sans grille
(`snap["grid"] is False`) : `FreeView`, frise en secondes, quelle que soit la vue choisie.

Cout : `tick` (ordonnanceur, ~20 Hz) ne fait rien quand le panneau n'est pas visible ; pendant un REC il dessine une derniere fois
« fige pendant le REC » puis plus rien. Libelles en anglais, raisons en francais (contraintes du plan). Reglages de salle `brain_*`.
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
BOX_W = 640                                    # largeur du panneau raccroche quand l'espace VJ n'en donne pas (pixels)
TOGGLE_W = 6                                   # largeur fixe (caracteres) des boutons dont le texte change
DETACH_W = 7
FROZEN_TEXT = "fige pendant le REC"
NO_TEMPO = "tempo non detecte"
WINDOW_TITLE = "casual-overlay - cerveau"
FONT = ("Consolas", 9)
FONT_HEAD = ("Consolas", 10)
FONT_SMALL = ("Consolas", 8)
GEOM_SAVE_MS = 500                             # geometrie de la fenetre detachee sauvee 0,5 s apres le dernier deplacement
GEOM_RE = re.compile(r"^\d+x\d+([+-]-?\d+[+-]-?\d+)?$")


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
            cv.itemconfigure(self._dots[i], fill=core_brain.COLORS.get(fam, FG), state="normal")
        for i in range(len(pts), self._used):
            cv.itemconfigure(self._dots[i], state="hidden")
        self._used = len(pts)
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

ISO_R = 4.2                                    # largeur d'une rangee d'instrument (pas = 1 unite)
ISO_A0 = 5.2                                   # echelle de reference de la maquette (z en pixels a cette echelle)
ISO_X_MIN, ISO_X_MAX = -67.0, 53.5             # etendue horizontale (x - y) en unites, textes compris
ISO_S_MAX = 114.5                              # etendue verticale maximale (x + y) en unites
ISO_FADE_S = 0.35                              # fondu des fantomes en debut de page
ISO_ANGLE = (18, 45)
ISO_MAX_CAUSES = 96
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
        self._veil = [cv.create_line(0, 0, 0, 0, 0, 0, state="hidden") for _ in range(8)]
        self._cause_lines = [cv.create_line(0, 0, 0, 0, state="hidden", dash=(3, 3)) for _ in range(ISO_MAX_CAUSES)]
        self._cause_dots = [cv.create_oval(0, 0, 0, 0, state="hidden", outline="") for _ in range(ISO_MAX_CAUSES)]
        self._plane = cv.create_polygon(0, 0, 0, 0, 0, 0, 0, 0, fill="#ffffff", stipple="gray12", outline="")
        self._edge = cv.create_line(0, 0, 0, 0, fill="#e0e0e0", width=1.5)
        # curseur d'angle (fenetre du canvas, en bas a gauche : zone vide de l'isometrique)
        self._scale = None
        old = getattr(canvas, "_iso_scale", None)
        if old is not None:
            try:
                old.destroy()
            except tk.TclError:
                pass
        try:
            sc = tk.Scale(canvas, from_=ISO_ANGLE[0], to=ISO_ANGLE[1], orient="horizontal", length=96, width=8, sliderlength=12,
                          showvalue=True, label="angle", font=FONT_SMALL, bg=PANEL_BG, fg=MUTED, troughcolor=BTN_BG, bd=0,
                          highlightthickness=0, activebackground=BTN_BG)
            sc.set(type(self).angle)
            sc.configure(command=lambda v: self.set_angle(int(float(v))))
            self._scale_win = canvas.create_window(4, 4, window=sc, anchor="sw")
            canvas._iso_scale = self._scale = sc
        except tk.TclError:
            self._scale = None

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
        if self._scale is not None and int(self._scale.get()) != angle:
            self._scale.set(angle)
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
        if self._scale is not None:
            cv.coords(self._scale_win, 4, self.h - 4)

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
        R = ISO_R
        cv.coords(cell.lines[0], *P(x0 + R * 0.2, y0 + 0.2), *P(x0 + R * 0.8, y0 + 0.8))
        cv.coords(cell.lines[1], *P(x0 + R * 0.8, y0 + 0.2), *P(x0 + R * 0.2, y0 + 0.8))
        for it in cell.lines[:2]:
            cv.itemconfigure(it, fill=col, width=1.2, state="normal")
        return P(x0 + R * 0.5, y0 + 0.5)

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
            if status in ("real", "surprise"):
                self._mode(cell, "box")
                pt = self._box(cell.polys, x0 + R * 0.12, y0 + 0.12, R * 0.76, 0.76, 5 + 22 * v, C[row], 0.45 + 0.55 * v, bg, status == "surprise")
                tip = f"{name} {round(v * 100)} % · predit {round(p * 100)} % · " + ("surprise" if status == "surprise" else "realise")
            elif status == "miss":
                self._mode(cell, "cross")
                pt = self._cross(cell, x0, y0, blend(MISS_FG, 0.8, bg))
                tip = f"{name} predit {round(p * 100)} % · manque"
            else:
                self._mode(cell, "wire")
                pt = self._wire(cell, x0 + R * 0.12, y0 + 0.12, R * 0.76, 0.76, 5 + 22 * p * 0.85, blend(C[row], (0.2 + 0.6 * p) * fade, bg))
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
                if track == "strobe":
                    cell.pt = self._box(cell.polys, x0 + R * 0.3, y0 + 0.2, R * 0.4, 0.6, 56 if reason.startswith("drop") else 34 * s, col, 0.9, bg)
                elif track == "scene":
                    cell.pt = self._box(cell.polys, x0 + R * 0.1, y0 - 2 + 0.1, R * 0.8, 2.8, 3, col, 0.85, bg)
                elif track == "foudre":
                    cell.pt = self._box(cell.polys, x0 + R * 0.3, y0 + 0.25, R * 0.4, 0.5, 38, col, 0.85, bg)
                else:
                    cell.pt = self._box(cell.polys, x0 + R * 0.2, y0 + 0.2, R * 0.6, 0.6, 3 + 14 * s, col, 0.5 + 0.5 * s, bg)
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
        # voile : 8 lignes de relief au-dessus des pistes d'effets
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
                x0 = (4 + k) * R + R * 0.5
                pts = []
                for i in range(top + 1):
                    z = veil[i][1] * 40 * (0.55 + 0.45 * math.sin(i * 0.32 + k * 0.8 + now * 2.2))
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


VIEWS: dict = {name: BlankView for name in VIEW_NAMES}
VIEWS["iso"] = IsoView


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
        self.size = (0, 0)
        self.frozen = False
        self.bg = None
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
        self.topmost_var = None
        if floating:
            self.topmost_var = tk.IntVar(value=int(float(panel.params.get("brain_topmost", 0)) >= 0.5))
            tk.Checkbutton(bar, text="toujours au-dessus", variable=self.topmost_var, command=panel.apply_topmost, bg=PANEL_BG, fg=MUTED,
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
            lab = tk.Label(gauges, text=key + "\n-\n-", bg=PANEL_BG, fg=MUTED, font=FONT, justify="left", anchor="nw")
            lab.pack(side="left", fill="both", expand=True)
            self.gauges[key] = lab
        self.feed = _rich(frame, core_brain.FEED_LINES)
        self.feed.pack(side="bottom", fill="x", padx=4)
        legend = tk.Frame(frame, bg=PANEL_BG)
        legend.pack(side="bottom", fill="x", padx=4, pady=(2, 2))
        self.pills = {}
        tk.Label(legend, text="\u25a0 realise \u25a1 predit \u25a3 surprise x manque", bg=PANEL_BG, fg=MUTED,
                 font=FONT_SMALL).pack(side="right")
        for name in core_brain.TRACKS:                             # pastilles : un clic masque / montre la piste
            pill = tk.Label(legend, text=core_brain.label(name), bg=PANEL_BG, font=FONT_SMALL, cursor="hand2", padx=1)
            pill.pack(side="left", padx=(0, 3))
            pill.bind("<Button-1>", lambda _e, n=name: panel.toggle_track(n))
            self.pills[name] = pill
        self.canvas = tk.Canvas(frame, bg=tint(None), highlightthickness=0, bd=0, width=200, height=80)
        self.canvas.pack(side="top", fill="both", expand=True, padx=4)
        self.canvas.bind("<Motion>", self.on_motion)
        self.canvas.bind("<Leave>", lambda _e: self.hide_tip())
        self.restyle()

    # ---- etat des boutons et des pastilles
    def restyle(self) -> None:
        p = self.panel
        for name, b in self.view_buttons.items():
            on = name == p.view_name
            b.configure(fg=BTN_ON if on else REASON_FG, highlightbackground=BTN_ON if on else "#2a3050")
        self.causes_btn.configure(fg=BTN_ON if p.causes else REASON_FG, highlightbackground=BTN_ON if p.causes else "#2a3050")
        self.pause_btn.configure(fg=BTN_ON if p.paused else REASON_FG, highlightbackground=BTN_ON if p.paused else "#2a3050")
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
                surf.view_key = None                             # la vue est recreee au prochain dessin
        self._restyle()
        self.tick()

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
        snap = core_brain.snapshot(log, now, rolling=(self.view_name == "tunnel"))
        self._draw(surf, snap, now)

    def _draw(self, surf: _Surface, snap: dict, now: float) -> None:
        self._draw_text(surf, snap)
        cv = surf.canvas
        key = self.view_name if snap.get("grid") else "free"
        cls = FreeView if key == "free" else VIEWS.get(key, BlankView)
        size = (cv.winfo_width(), cv.winfo_height())
        if surf.view_key != key or type(surf.view) is not cls:
            cv.delete("all")
            surf.view, surf.view_key, surf.size = None, key, (0, 0)
            try:
                surf.view = cls(cv)
            except Exception as exc:
                self._fail(key, exc)
        view = surf.view
        header = snap.get("header", {})
        bg = tint(header.get("phase"))
        if surf.bg != bg:
            surf.bg = bg
            cv.configure(bg=bg)
        if view is not None:
            try:
                if surf.size != size:
                    surf.size = size
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
            line1.append(("   4 next bars predicted", FG))
        elif h.get("page") is not None:
            line1.append((f"   page {h['page']} \u00b7 bar {h['bar']}/4", FG))
        ok, miss, sur = snap.get("score", (0, 0, 0))
        line2 = [("predit ", MUTED), (f"{ok}/{ok + miss}", BTN_ON), (" \u00b7 ", MUTED), (str(sur), "#ffffff"),
                 (" surprises \u00b7 ", MUTED), (str(miss), MISS_FG), (" manquees", MUTED)]
        cycles = h.get("cycles")
        if cycles:                                                 # temps restants avant la fin des cycles de 8, 16 et 32 temps
            line2.append(("   end " + " ".join(f"{n}\u00b7{cycles[n]}" for n in sorted(cycles)), MUTED))
        if h.get("build_s") is not None:
            line2.append((f"   build {h['build_s']:.0f} s", PHASE_COLORS["build"]))
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
        src = g.get("sources") or {}
        surf.set_label("sources", "sources\n" + f"fond <- {src.get('bg', '-')}\n" + f"logo <- {src.get('logo', '-')}")
        auto = g.get("auto") or {}

        def dot(flag) -> str:
            return "\u25cf" if flag else "\u25cb"
        fxm = auto.get("fx_master")
        surf.set_label("auto", "auto\n" + (f"fx master {fxm * 100:.0f} %" if fxm is not None else "fx master -") + "\n"
                       + f"{dot(auto.get('phases'))} phases {dot(auto.get('strobe'))} strobe {dot(auto.get('tempo'))} tempo "
                       + f"{dot(auto.get('vj'))} VJ")
        gpu = g.get("gpu")
        gpu_txt = "GPU -" if not gpu else f"GPU {gpu.get('ms', 0.0):.1f}/{gpu.get('budget_ms', 0.0):.0f} ms" + (" !" if gpu.get("over") else "")
        surf.set_label("sante", "sante\n" + f"{g.get('fps', 0.0) or 0.0:.0f} img/s  {gpu_txt}\n"
                       + f"latence {g.get('latency_ms', 0.0) or 0.0:.0f} ms  REC {'on' if g.get('rec') else 'off'}")


def install(ns) -> BrainPanel:
    """Panneau du cerveau d'une construction de la fenetre ; `ns` : root, s, scene_top, master_box, Tooltip, STATS, ui_every.
    Rouvre la fenetre detachee si elle l'etait (reglage de salle `brain_detached`)."""
    panel = BrainPanel(ns)
    every = getattr(ns, "ui_every", None)
    if every is not None:
        every("brain", TICK_MS, panel.tick)
        every("brain_slot", 300, panel.sync_slot)
    if panel._flag("brain_detached"):
        try:
            ns.root.after(200, panel.detach)
        except tk.TclError:
            pass
    return panel
