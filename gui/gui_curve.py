"""Fenetre « courbe » des automations de casual-overlay : plus grande, graduee, plus simple a dessiner.

Une seule fenetre (`CurveWindow`) pour TOUTES les courbes du programme :
  * celles de l'overlay et du fond (gestionnaire `CurveManager`, derive de `AutomationManager` d'audio2wave : il ajoute la marque de
    version aux courbes capturees et lit la valeur actuelle dans `params`) ;
  * celles des panneaux Live / Ridge / Sphere (gestionnaires crees par audio2wave : crochet de classe
    `AutomationManager.default_open_editor`) et Snap (son editeur a lui : crochet `audio2wave_snap.CURVE_EDITOR_HOOK`), posees par `install`.
La fenetre ne travaille que sur l'etat d'une courbe, le meme dict partout : `label`, `lo`, `hi`, `enabled` (BooleanVar), `points`
(liste 0..1), `period` (DoubleVar, secondes), `start` (time.monotonic du debut du cycle), `editor`. Le bas de la courbe = 0 % du curseur
(`lo`), le haut = 100 % (`hi`). Elle ne pose jamais `state["canvas"]` : l'ancien trace d'audio2wave n'y dessine donc jamais ; elle se
redessine seule quand les points changent ailleurs (preset charge, tirage).
"""
from __future__ import annotations

import time
import tkinter as tk

import skin
import theme as T
from core.automation import CURVE_VERSION

CANVAS_W, CANVAS_H = 560, 260
PAD_L, PAD_R, PAD_T, PAD_B = 84, 16, 14, 14      # marges du trace dans le canevas (graduations a gauche)
HANDLE_R = 5                                     # rayon d'une poignee (6 au survol / pendant le glisser)
LIVE_MS = 100                                    # rafraichissement : valeur actuelle, points changes ailleurs
GRID_STEPS = (0, 25, 50, 75, 100)
SPEED_LEN = 320
IDLE_TEXT = "Clique ou glisse dans le cadre pour dessiner la courbe."


def _mix(a: str, b: str, t: float) -> str:
    """Couleur entre `a` (t = 0) et `b` (t = 1)."""
    ca = [int(a[i:i + 2], 16) for i in (1, 3, 5)]
    cb = [int(b[i:i + 2], 16) for i in (1, 3, 5)]
    return "#" + "".join(f"{round(x + (y - x) * t):02x}" for x, y in zip(ca, cb))


def value_text(value: float, lo: float, hi: float) -> str:
    """Valeur d'un reglage, avec le nombre de decimales qui convient a sa plage (virgule francaise)."""
    span = abs(hi - lo)
    digits = 3 if span <= 0.1 else 2 if span <= 10 else 0
    return f"{value:.{digits}f}".replace(".", ",")


def invert_points(points) -> list[float]:
    """La courbe a l'envers (haut <-> bas)."""
    return [1.0 - float(p) for p in points]


def stretch_points(points) -> list[float]:
    """La courbe etiree de 0 a 100 % (sa forme est gardee) ; une courbe plate ne bouge pas."""
    lo, hi = min(points), max(points)
    if hi - lo < 1e-6:
        return [float(p) for p in points]
    return [(float(p) - lo) / (hi - lo) for p in points]


def curve_fraction(state: dict, now: float, min_period: float) -> float | None:
    """Ou en est la courbe (0..1) a l'instant `now`, comme la fait avancer audio2wave ; None si elle est coupee."""
    if not state["enabled"].get() or not state["points"]:
        return None
    period = max(min_period, float(state["period"].get()))
    n = len(state["points"])
    pos = (((now - state["start"]) / period) % 1.0) * n
    i0, frac = int(pos) % n, pos - int(pos)
    a, b = state["points"][i0], state["points"][(i0 + 1) % n]
    return a + (b - a) * frac


class CurveWindow:
    """La fenetre d'edition d'UNE courbe. `value_reader(attr)` (facultatif) : valeur actuelle du reglage ; sans lui, elle est calculee
    depuis la courbe (horloge d'audio2wave)."""

    def __init__(self, root, state: dict, attr: str, live, accent: str, tooltip_cls, value_reader=None) -> None:
        self.root, self.state, self.attr, self.live = root, state, attr, live
        self.accent, self.Tooltip, self.value_reader = accent, tooltip_cls, value_reader
        self.hot: int | None = None            # point sous la souris
        self.last: tuple[int, float] | None = None   # dernier point peint pendant un glisser
        self.live_y: float | None = None
        self.drawn: tuple | None = None        # points du dernier dessin (detecte un changement fait ailleurs)
        self.after_id = None
        self._build()

    # ---- geometrie du trace
    @staticmethod
    def x_of(i: int, n: int) -> float:
        return PAD_L + i * (CANVAS_W - PAD_L - PAD_R) / max(n - 1, 1)

    @staticmethod
    def y_of(v: float) -> float:
        return PAD_T + (1.0 - v) * (CANVAS_H - PAD_T - PAD_B)

    @staticmethod
    def v_at(y: float) -> float:
        return min(max(1.0 - (y - PAD_T) / (CANVAS_H - PAD_T - PAD_B), 0.0), 1.0)

    @staticmethod
    def index_at(x: float, n: int) -> int:
        return max(0, min(n - 1, round((x - PAD_L) * (n - 1) / (CANVAS_W - PAD_L - PAD_R))))

    # ---- construction
    def _build(self) -> None:
        state, live, bg = self.state, self.live, T.PANEL
        self.win = win = tk.Toplevel(self.root)
        win.title(f"Courbe : {state['label']}")
        win.resizable(False, False)
        win.configure(bg=bg)
        body = tk.Frame(win, bg=bg)
        body.pack(fill="both", expand=True, padx=18, pady=16)

        head = tk.Frame(body, bg=bg)
        head.pack(fill="x")
        tk.Label(head, text=state["label"], bg=bg, fg=T.FG, font=T.FONT_TITLE).pack(side="left")
        lo, hi = state["lo"], state["hi"]
        tk.Label(head, text=f"   de {value_text(lo, lo, hi)} (0 %) a {value_text(hi, lo, hi)} (100 %)", bg=bg, fg=T.MUTED,
                 font=T.FONT_SMALL).pack(side="left", pady=(3, 0))
        self.active = tk.Checkbutton(head, text="Actif", variable=state["enabled"], command=self._on_toggle, bg=bg, fg=T.FG,
                                     activebackground=bg, selectcolor=T.TROUGH)
        self.active.pack(side="right")
        self.Tooltip(self.active, "Allume / coupe cette automation (meme case que « ~ » a cote du curseur).")

        self.canvas = cv = tk.Canvas(body, width=CANVAS_W, height=CANVAS_H, bg=T.TROUGH, highlightthickness=1,
                                     highlightbackground=T.LINE, cursor="crosshair")
        cv.pack(pady=(12, 6))
        cv.bind("<Button-1>", self.on_drag)
        cv.bind("<B1-Motion>", self.on_drag)
        cv.bind("<ButtonRelease-1>", self.on_release)
        cv.bind("<Motion>", self.on_motion)
        cv.bind("<Leave>", self.on_leave)
        self.readout = tk.Label(body, text=IDLE_TEXT, bg=bg, fg=T.MUTED, font=T.FONT_SMALL, anchor="w")
        self.readout.pack(fill="x")

        self.buttons: dict[str, tk.Button] = {}
        shapes = self._row(body, "FORME", (12, 0))
        n = len(state["points"])
        for name, fn in (("Sinus", live.automate_curve_sinus), ("Triangle", live.automate_curve_triangle), ("Carre", live.automate_curve_carre),
                         ("Dents de scie", live.automate_curve_dents_de_scie), ("Aleatoire", live.automate_curve_aleatoire)):
            self._button(shapes, name, lambda fn=fn: self.set_points(fn(n)))
        tools = self._row(body, "OUTILS", (8, 0))
        self.Tooltip(self._button(tools, "Inverser", lambda: self.set_points(invert_points(state["points"]))),
                     "Retourne la courbe : le haut devient le bas.")
        self.Tooltip(self._button(tools, "Plein 0-100 %", lambda: self.set_points(stretch_points(state["points"]))),
                     "Etire la courbe pour qu'elle aille du minimum (0 %) au maximum (100 %) du curseur, en gardant sa forme.")

        speed = self._row(body, "VITESSE", (12, 0))
        self.Tooltip(speed.winfo_children()[0], "Vers la droite = plus rapide (cycle court), vers la gauche = plus lent (cycle long). "
                                                "Le chiffre est la duree d'un cycle complet.")
        self.speed_pos = tk.DoubleVar(value=live.period_to_pos(state["period"].get()))
        self._syncing = False
        self.speed_pos.trace_add("write", self._pos_changed)
        tk.Scale(speed, from_=0, to=1000, resolution=0.1, orient="horizontal", variable=self.speed_pos, length=SPEED_LEN,
                 showvalue=False).pack(side="left")
        self.speed_value = tk.Label(speed, text=live.period_text(state["period"].get()), bg=bg, fg=T.FG, width=12, anchor="w")
        self.speed_value.pack(side="left", padx=(10, 0))
        self.trace_id = state["period"].trace_add("write", self._period_changed)

        foot = tk.Frame(body, bg=bg)
        foot.pack(fill="x", pady=(16, 0))
        self._button(foot, "Fermer", self.close, side="right", padx=14)
        win.protocol("WM_DELETE_WINDOW", self.close)
        win.bind("<Escape>", lambda _e: self.close())
        state["editor"] = win
        state["_window"] = self
        skin.zone(win, self.accent)
        skin.apply(win)
        cv.configure(bg=T.TROUGH)
        self.redraw()
        self._tick()
        win.lift()
        win.attributes("-topmost", True)
        win.after(300, lambda: win.attributes("-topmost", False) if win.winfo_exists() else None)
        win.focus_force()

    def _row(self, parent, kicker: str, pady) -> tk.Frame:
        row = tk.Frame(parent, bg=T.PANEL)
        row.pack(fill="x", pady=pady)
        tk.Label(row, text=kicker, bg=T.PANEL, fg=T.MUTED, font=T.FONT_KICKER, width=8, anchor="w").pack(side="left")
        return row

    def _button(self, parent, text: str, command, side: str = "left", padx: int = 8) -> tk.Button:
        b = tk.Button(parent, text=text, command=command, padx=padx)
        b.pack(side=side, padx=(0, 4) if side == "left" else 0)
        self.buttons[text] = b
        return b

    # ---- vitesse (curseur logarithmique d'audio2wave, garde en phase avec state["period"] dans les deux sens)
    def _pos_changed(self, *_a) -> None:
        if not self._syncing:
            self._syncing = True
            try:
                self.state["period"].set(self.live.pos_to_period(self.speed_pos.get()))
            finally:
                self._syncing = False

    def _period_changed(self, *_a) -> None:
        if not self._syncing:
            self._syncing = True
            try:
                self.speed_pos.set(self.live.period_to_pos(self.state["period"].get()))
            finally:
                self._syncing = False
        try:
            self.speed_value.configure(text=self.live.period_text(self.state["period"].get()))
        except tk.TclError:
            pass

    def _on_toggle(self) -> None:
        if self.state["enabled"].get():
            self.state["start"] = time.monotonic()      # comme la case « ~ » : la courbe repart de son debut

    # ---- dessin
    def redraw(self) -> None:
        state, cv = self.state, self.canvas
        try:
            cv.delete("all")
        except tk.TclError:
            return
        lo, hi = state["lo"], state["hi"]
        left, right, bottom = PAD_L, CANVAS_W - PAD_R, self.y_of(0.0)
        for pct in GRID_STEPS:                                  # graduations : pourcentage du curseur et vraie valeur
            y = self.y_of(pct / 100.0)
            edge = pct in (0, 100)
            cv.create_line(left, y, right, y, fill=_mix(T.LINE, T.MUTED, 0.35) if edge else T.LINE, dash=() if edge else (2, 4))
            cv.create_text(left - 10, y, anchor="e", fill=T.MUTED, font=T.FONT_SMALL,
                           text=f"{pct:>3d} %  {value_text(lo + (hi - lo) * pct / 100.0, lo, hi)}")
        points = list(state["points"])
        n = len(points)
        for i in range(n):                                       # un filet discret par point de la courbe
            x = self.x_of(i, n)
            cv.create_line(x, self.y_of(1.0), x, bottom, fill=_mix(T.TROUGH, T.LINE, 0.5))
        coords = []
        for i, v in enumerate(points):
            coords += [self.x_of(i, n), self.y_of(v)]
        if n >= 2:
            cv.create_polygon(left, bottom, *coords, right, bottom, fill=_mix(T.TROUGH, self.accent, 0.18), outline="")
            cv.create_line(*coords, fill=self.accent, width=2)
        if self.live_y is not None:                              # ou en est le reglage maintenant
            cv.create_line(left, self.live_y, right, self.live_y, fill=T.FG, dash=(6, 4))
            cv.create_text(right - 4, self.live_y - 8, anchor="e", fill=T.FG, font=T.FONT_SMALL, text="valeur actuelle")
        for i in range(n):
            x, y = coords[2 * i], coords[2 * i + 1]
            r = HANDLE_R + (1 if i == self.hot else 0)
            cv.create_oval(x - r, y - r, x + r, y + r, fill=T.FG if i == self.hot else self.accent, outline=T.TROUGH, width=2)
        self.drawn = tuple(points)

    def _set_readout(self, i: int | None) -> None:
        state = self.state
        if i is None:
            text = IDLE_TEXT
        else:
            v = state["points"][i]
            value = value_text(state["lo"] + (state["hi"] - state["lo"]) * v, state["lo"], state["hi"])
            text = f"Point {i + 1} / {len(state['points'])} :  {round(v * 100):d} %   ({value})"
        try:
            self.readout.configure(text=text)
        except tk.TclError:
            pass

    # ---- souris
    def on_drag(self, event) -> None:
        points = self.state["points"]
        n = len(points)
        i, v = self.index_at(event.x, n), self.v_at(event.y)
        if self.last is not None and self.last[0] != i:           # glisser rapide : les points sautes sont peints en ligne droite
            (i0, v0), step = self.last, (1 if i > self.last[0] else -1)
            for j in range(i0 + step, i, step):
                points[j] = v0 + (v - v0) * (j - i0) / (i - i0)
        points[i] = v
        self.last, self.hot = (i, v), i
        self.redraw()
        self._set_readout(i)

    def on_release(self, _event=None) -> None:
        self.last = None

    def on_motion(self, event) -> None:
        points = self.state["points"]
        i = self.index_at(event.x, len(points))
        if abs(event.y - self.y_of(points[i])) > 18 and self.last is None:
            i = None
        if i != self.hot:
            self.hot = i
            self.redraw()
            self._set_readout(i)

    def on_leave(self, _event=None) -> None:
        if self.hot is not None and self.last is None:
            self.hot = None
            self.redraw()
            self._set_readout(None)

    def set_points(self, points) -> None:
        self.state["points"] = [min(max(float(p), 0.0), 1.0) for p in points]
        self.redraw()

    # ---- rafraichissement : valeur actuelle, points changes ailleurs
    def _tick(self) -> None:
        if not self.win.winfo_exists():
            return
        state = self.state
        frac = None
        if state["enabled"].get():
            try:
                if self.value_reader is not None:
                    lo, hi = state["lo"], state["hi"]
                    frac = min(max((float(self.value_reader(self.attr)) - lo) / ((hi - lo) or 1.0), 0.0), 1.0)
                else:
                    frac = curve_fraction(state, time.monotonic(), getattr(self.live, "AUTOMATE_PERIOD_MIN_S", 0.3))
            except Exception:
                frac = None
        y = None if frac is None else self.y_of(frac)
        if y != self.live_y or tuple(state["points"]) != self.drawn:
            self.live_y = y
            self.redraw()
        self.after_id = self.win.after(LIVE_MS, self._tick)

    def close(self) -> None:
        state = self.state
        try:
            state["period"].trace_remove("write", self.trace_id)
        except tk.TclError:
            pass
        if self.after_id is not None:
            try:
                self.win.after_cancel(self.after_id)
            except tk.TclError:
                pass
        state["editor"] = None
        state["_window"] = None
        try:
            self.win.destroy()
        except tk.TclError:
            pass


def open_window(root, state: dict, attr: str, live, accent: str, tooltip_cls, value_reader=None) -> CurveWindow | None:
    """Ouvre la fenetre de la courbe `state`, ou ramene au premier plan celle qui est deja ouverte."""
    existing = state.get("editor")
    if existing is not None:
        try:
            if existing.winfo_exists():
                existing.deiconify()
                existing.lift()
                existing.focus_force()
                return state.get("_window")
        except tk.TclError:
            pass
    return CurveWindow(root, state, attr, live, accent, tooltip_cls, value_reader)


def install(live, snap=None) -> None:
    """La fenetre de casual-overlay pour les courbes des panneaux d'audio2wave : Live / Ridge / Sphere (crochet de classe du gestionnaire,
    accent du gestionnaire) et Snap (son propre editeur, accent bleu des parties LIVE). Idempotent."""
    live.AutomationManager.default_open_editor = staticmethod(
        lambda manager, attr: open_window(manager.root, manager.state[attr], attr, live, manager.accent, manager.Tooltip))
    if snap is not None and hasattr(snap, "CURVE_EDITOR_HOOK"):
        snap.CURVE_EDITOR_HOOK = lambda root, state, attr: open_window(root, state, attr, live, T.LIVE, live.Tooltip)


def curve_manager_class(live):
    """Classe de gestionnaire d'automation de l'overlay et du fond de casual-overlay, derivee de `live.AutomationManager`."""
    base = live.AutomationManager

    class CurveManager(base):
        value_reader = None          # callable(cle) -> valeur actuelle du reglage (params, ecrite par le moteur), pose par gl_gui

        def capture(self) -> dict:
            out = super().capture()
            for entry in out.values():
                entry["v"] = CURVE_VERSION          # points sur toute la plage du curseur (jamais reconvertis a la lecture)
            return out

        def redraw(self, attr: str) -> None:
            window = self.state[attr].get("_window")
            if window is not None:
                window.redraw()

        def open_editor(self, attr: str) -> CurveWindow | None:
            return open_window(self.root, self.state[attr], attr, live, self.accent, self.Tooltip, self.value_reader)

    return CurveManager
