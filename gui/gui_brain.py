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

import re
import time
import tkinter as tk

from core import brain as core_brain
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


VIEWS: dict = {name: BlankView for name in VIEW_NAMES}


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
        width = BOX_W
        try:
            width = max(BOX_W, int(self.master_box().winfo_reqwidth()))
        except Exception:
            pass
        surf = self._docked = _Surface(self, self.parent, floating=False)
        surf.frame.configure(width=width)
        surf.frame.pack_propagate(False)
        return surf.frame

    def _brain_shown(self) -> bool:
        return self._flag("brain_on") and not self.detached

    def place(self, wrap) -> tk.Frame:
        """Pose dans `wrap` (corps du panneau etendu, a droite) l'espace VJ ou le cerveau selon l'etat ; rend le cadre pose."""
        self._wrap = wrap
        return self._show_in_wrap()

    def _show_in_wrap(self):
        wrap = self._wrap
        if wrap is None:
            return None
        try:
            if not wrap.winfo_exists():
                return None
            shown = self.box() if self._brain_shown() else self.master_box()
            other = self.master_box() if self._brain_shown() else (self._docked.frame if self._docked is not None else None)
            if other is not None and other.winfo_manager():
                other.pack_forget()
            slaves = [w for w in wrap.pack_slaves() if w is not shown]
            if shown.winfo_manager() != "pack" or str(shown.pack_info().get("in")) != str(wrap):
                if slaves:
                    shown.pack(in_=wrap, side="right", fill="y", before=slaves[0])
                else:
                    shown.pack(in_=wrap, side="right", fill="y")
            shown.lift()                                        # un esclave place « dans » un autre doit le surplomber
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
        self.tick()

    def show_vj(self) -> None:
        """Remet l'espace VJ a sa place (onglet Affichage demande ailleurs)."""
        if self._brain_shown():
            self.params["brain_on"] = 0.0
            self._show_in_wrap()

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
        self.apply_topmost()
        self._show_in_wrap()                                     # l'espace VJ reprend sa place
        self.tick()

    def _on_configure(self, event) -> None:
        top = self.window
        if top is None or event.widget is not top:
            return
        self._remember_geom()

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

    def dock(self) -> None:
        """Raccroche le cerveau (fermer la fenetre detachee) ; il reprend sa place dans le panneau etendu."""
        top = self.window
        if top is None:
            return
        self._remember_geom()
        self.window, self._float = None, None
        try:
            top.destroy()
        except tk.TclError:
            pass
        self.params["brain_detached"] = 0.0
        self.params["brain_on"] = 1.0
        self._show_in_wrap()
        self.tick()

    def set_view(self, name: str) -> None:
        if name not in VIEW_NAMES:
            return
        self.view_name = name
        self.params["brain_view"] = name
        for surf in (self._docked, self._float):
            if surf is not None:
                surf.view_key = None                             # la vue est recreee au prochain dessin
        self._restyle()
        self.tick()

    def toggle_causes(self) -> None:
        self.causes = not self.causes
        self.params["brain_causes"] = 1.0 if self.causes else 0.0
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
    if panel._flag("brain_detached"):
        try:
            ns.root.after(200, panel.detach)
        except tk.TclError:
            pass
    return panel
