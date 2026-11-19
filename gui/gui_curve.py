"""Fenetre « courbe » des automations de casual-overlay : plus grande, graduee, plus simple a dessiner.

Le gestionnaire d'automation reste celui d'audio2wave (`AutomationManager` : case « ~ », bouton de courbe, etat, presets) ; ce module
n'en derive qu'une classe propre a casual-overlay (`curve_manager_class`) qui remplace la FENETRE d'edition et ajoute la marque de
version aux courbes capturees. La copie interne d'audio2wave n'est pas modifiee ; les fenetres de courbe des panneaux Live / Snap /
Ridge (gestionnaires crees par audio2wave lui-meme) gardent la leur.

Le bas de la courbe = 0 % du curseur, le haut = 100 % (core/automation.py, `AUTOMATION_SPECS`).
"""
from __future__ import annotations

import tkinter as tk

import skin
import theme as T
from core.automation import CURVE_VERSION

CANVAS_W, CANVAS_H = 560, 260
PAD_L, PAD_R, PAD_T, PAD_B = 84, 16, 14, 14      # marges du trace dans le canevas (graduations a gauche)
HANDLE_R = 5                                     # rayon d'une poignee (6 au survol / pendant le glisser)
LIVE_MS = 100                                    # rafraichissement de la ligne « valeur actuelle »
GRID_STEPS = (0, 25, 50, 75, 100)
SPEED_LEN = 320


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


def curve_manager_class(live):
    """Classe de gestionnaire d'automation de casual-overlay, derivee de `live.AutomationManager` (module audio2wave_live)."""
    base = live.AutomationManager

    class CurveManager(base):
        value_reader = None          # callable(cle) -> valeur actuelle du reglage (params, ecrite par le moteur), pose par gl_gui

        # ---- etat
        def capture(self) -> dict:
            out = super().capture()
            for entry in out.values():
                entry["v"] = CURVE_VERSION          # points sur toute la plage du curseur (jamais reconvertis a la lecture)
            return out

        # ---- geometrie du trace
        @staticmethod
        def _x(i: int, n: int) -> float:
            return PAD_L + i * (CANVAS_W - PAD_L - PAD_R) / max(n - 1, 1)

        @staticmethod
        def _y(v: float) -> float:
            return PAD_T + (1.0 - v) * (CANVAS_H - PAD_T - PAD_B)

        @staticmethod
        def _v_at(y: float) -> float:
            return min(max(1.0 - (y - PAD_T) / (CANVAS_H - PAD_T - PAD_B), 0.0), 1.0)

        def _index_at(self, x: float, n: int) -> int:
            return max(0, min(n - 1, round((x - PAD_L) * (n - 1) / (CANVAS_W - PAD_L - PAD_R))))

        # ---- dessin
        def redraw(self, attr: str) -> None:
            state = self.state[attr]
            canvas = state.get("canvas")
            if canvas is None:
                return
            canvas.delete("all")
            lo, hi = state["lo"], state["hi"]
            left, right, bottom = PAD_L, CANVAS_W - PAD_R, self._y(0.0)
            for pct in GRID_STEPS:                                  # graduations : pourcentage du curseur et vraie valeur
                y = self._y(pct / 100.0)
                canvas.create_line(left, y, right, y, fill=T.LINE if pct not in (0, 100) else _mix(T.LINE, T.MUTED, 0.35),
                                   dash=(2, 4) if pct not in (0, 100) else ())
                canvas.create_text(left - 10, y, anchor="e", fill=T.MUTED, font=T.FONT_SMALL,
                                   text=f"{pct:>3d} %  {value_text(lo + (hi - lo) * pct / 100.0, lo, hi)}")
            points = state["points"]
            n = len(points)
            for i in range(n):                                       # un filet discret par point de la courbe
                x = self._x(i, n)
                canvas.create_line(x, self._y(1.0), x, bottom, fill=_mix(T.TROUGH, T.LINE, 0.5))
            coords = []
            for i, v in enumerate(points):
                coords += [self._x(i, n), self._y(v)]
            if n >= 2:
                canvas.create_polygon(left, bottom, *coords, right, bottom, fill=_mix(T.TROUGH, self.accent, 0.18), outline="")
                canvas.create_line(*coords, fill=self.accent, width=2, smooth=False)
            live_y = state.get("_live_y")
            if live_y is not None:                                   # valeur actuelle du reglage (moteur)
                canvas.create_line(left, live_y, right, live_y, fill=T.FG, dash=(6, 4))
                canvas.create_text(right - 4, live_y - 8, anchor="e", fill=T.FG, font=T.FONT_SMALL, text="valeur actuelle")
            hot = state.get("_hot")
            for i in range(n):
                x, y = coords[2 * i], coords[2 * i + 1]
                r = HANDLE_R + (1 if i == hot else 0)
                canvas.create_oval(x - r, y - r, x + r, y + r, fill=T.FG if i == hot else self.accent,
                                   outline=T.TROUGH, width=2, tags=("handle",))

        # ---- souris
        def _set_readout(self, attr: str, i: int | None) -> None:
            state = self.state[attr]
            label = state.get("_readout")
            if label is None:
                return
            if i is None:
                text = "Clique ou glisse dans le cadre pour dessiner la courbe."
            else:
                v = state["points"][i]
                text = f"Point {i + 1} / {len(state['points'])} :  {round(v * 100):d} %   ({value_text(state['lo'] + (state['hi'] - state['lo']) * v, state['lo'], state['hi'])})"
            try:
                label.configure(text=text)
            except tk.TclError:
                pass

        def _on_drag(self, event, attr: str) -> None:
            state = self.state[attr]
            n = len(state["points"])
            i = self._index_at(event.x, n)
            v = self._v_at(event.y)
            last = state.get("_last")
            if last is not None and last[0] != i:                    # glisser rapide : les points sautes sont peints en ligne droite
                (i0, v0), step = last, (1 if i > last[0] else -1)
                for j in range(i0 + step, i, step):
                    state["points"][j] = v0 + (v - v0) * (j - i0) / (i - i0)
            state["points"][i] = v
            state["_last"] = (i, v)
            state["_hot"] = i
            self.redraw(attr)
            self._set_readout(attr, i)

        def _on_release(self, _event, attr: str) -> None:
            self.state[attr]["_last"] = None

        def _on_motion(self, event, attr: str) -> None:
            state = self.state[attr]
            n = len(state["points"])
            i = self._index_at(event.x, n)
            if abs(event.y - self._y(state["points"][i])) > 18 and state.get("_last") is None:
                i = None
            if i != state.get("_hot"):
                state["_hot"] = i
                self.redraw(attr)
                self._set_readout(attr, i)

        def _on_leave(self, _event, attr: str) -> None:
            state = self.state[attr]
            if state.get("_hot") is not None and state.get("_last") is None:
                state["_hot"] = None
                self.redraw(attr)
                self._set_readout(attr, None)

        def _set_points(self, attr: str, points) -> None:
            self.state[attr]["points"] = [min(max(float(p), 0.0), 1.0) for p in points]
            self.redraw(attr)

        # ---- valeur actuelle (moteur)
        def _live_tick(self, attr: str) -> None:
            state = self.state[attr]
            win = state.get("editor")
            if win is None or not win.winfo_exists():
                return
            y = None
            if state["enabled"].get() and self.value_reader is not None:
                try:
                    value = float(self.value_reader(attr))
                    lo, hi = state["lo"], state["hi"]
                    y = self._y(min(max((value - lo) / ((hi - lo) or 1.0), 0.0), 1.0))
                except Exception:
                    y = None
            if y != state.get("_live_y"):
                state["_live_y"] = y
                self.redraw(attr)
            state["_live_after"] = win.after(LIVE_MS, lambda: self._live_tick(attr))

        # ---- fenetre
        def open_editor(self, attr: str) -> None:
            state = self.state[attr]
            existing = state.get("editor")
            if existing is not None and existing.winfo_exists():
                existing.deiconify()
                existing.lift()
                existing.focus_force()
                return
            bg = T.PANEL
            win = tk.Toplevel(self.root)
            win.title(f"Courbe : {state['label']}")
            win.resizable(False, False)
            win.configure(bg=bg)
            body = tk.Frame(win, bg=bg)
            body.pack(fill="both", expand=True, padx=18, pady=16)

            head = tk.Frame(body, bg=bg)
            head.pack(fill="x")
            title = tk.Label(head, text=state["label"], bg=bg, fg=T.FG, font=T.FONT_TITLE)
            title.pack(side="left")
            lo, hi = state["lo"], state["hi"]
            tk.Label(head, text=f"   de {value_text(lo, lo, hi)} (0 %) a {value_text(hi, lo, hi)} (100 %)", bg=bg, fg=T.MUTED,
                     font=T.FONT_SMALL).pack(side="left", pady=(3, 0))

            def on_toggle() -> None:
                if state["enabled"].get():
                    import time
                    state["start"] = time.monotonic()
            active = tk.Checkbutton(head, text="Actif", variable=state["enabled"], command=on_toggle, bg=bg, fg=T.FG,
                                    activebackground=bg, selectcolor=T.TROUGH)
            active.pack(side="right")
            self.Tooltip(active, "Allume / coupe cette automation (meme case que « ~ » a cote du curseur).")
            state["_active"] = active

            canvas = tk.Canvas(body, width=CANVAS_W, height=CANVAS_H, bg=T.TROUGH, highlightthickness=1,
                               highlightbackground=T.LINE, cursor="crosshair")
            canvas.pack(pady=(12, 6))
            state["canvas"] = canvas
            state["_hot"] = state["_last"] = state["_live_y"] = None
            canvas.bind("<Button-1>", lambda e: self._on_drag(e, attr))
            canvas.bind("<B1-Motion>", lambda e: self._on_drag(e, attr))
            canvas.bind("<ButtonRelease-1>", lambda e: self._on_release(e, attr))
            canvas.bind("<Motion>", lambda e: self._on_motion(e, attr))
            canvas.bind("<Leave>", lambda e: self._on_leave(e, attr))

            readout = tk.Label(body, text="", bg=bg, fg=T.MUTED, font=T.FONT_SMALL, anchor="w")
            readout.pack(fill="x")
            state["_readout"] = readout
            self._set_readout(attr, None)

            shapes = tk.Frame(body, bg=bg)
            shapes.pack(fill="x", pady=(12, 0))
            tk.Label(shapes, text="FORME", bg=bg, fg=T.MUTED, font=T.FONT_KICKER, width=8, anchor="w").pack(side="left")
            n = len(state["points"])
            curves = (("Sinus", live.automate_curve_sinus), ("Triangle", live.automate_curve_triangle), ("Carre", live.automate_curve_carre),
                      ("Dents de scie", live.automate_curve_dents_de_scie), ("Aleatoire", live.automate_curve_aleatoire))
            for name, fn in curves:
                tk.Button(shapes, text=name, command=lambda fn=fn: self._set_points(attr, fn(n)), padx=8).pack(side="left", padx=(0, 4))
            tools = tk.Frame(body, bg=bg)
            tools.pack(fill="x", pady=(8, 0))
            tk.Label(tools, text="OUTILS", bg=bg, fg=T.MUTED, font=T.FONT_KICKER, width=8, anchor="w").pack(side="left")
            inv = tk.Button(tools, text="Inverser", command=lambda: self._set_points(attr, invert_points(state["points"])), padx=8)
            inv.pack(side="left", padx=(0, 4))
            self.Tooltip(inv, "Retourne la courbe : le haut devient le bas.")
            full = tk.Button(tools, text="Plein 0-100 %", command=lambda: self._set_points(attr, stretch_points(state["points"])), padx=8)
            full.pack(side="left", padx=(0, 4))
            self.Tooltip(full, "Etire la courbe pour qu'elle aille du minimum (0 %) au maximum (100 %) du curseur, en gardant sa forme.")

            speed = tk.Frame(body, bg=bg)
            speed.pack(fill="x", pady=(12, 0))
            sl = tk.Label(speed, text="VITESSE", bg=bg, fg=T.MUTED, font=T.FONT_KICKER, width=8, anchor="w")
            sl.pack(side="left")
            self.Tooltip(sl, "Vers la droite = plus rapide (cycle court), vers la gauche = plus lent (cycle long). Le chiffre est la "
                             "duree d'un cycle complet.")
            speed_pos = tk.DoubleVar(value=live.period_to_pos(state["period"].get()))
            syncing = {"on": False}

            def pos_changed(*_a) -> None:
                if not syncing["on"]:
                    syncing["on"] = True
                    try:
                        state["period"].set(live.pos_to_period(speed_pos.get()))
                    finally:
                        syncing["on"] = False

            def period_changed(*_a) -> None:
                if not syncing["on"]:
                    syncing["on"] = True
                    try:
                        speed_pos.set(live.period_to_pos(state["period"].get()))
                    finally:
                        syncing["on"] = False
                try:
                    speed_value.configure(text=live.period_text(state["period"].get()))
                except tk.TclError:
                    pass

            speed_pos.trace_add("write", pos_changed)
            tk.Scale(speed, from_=0, to=1000, resolution=0.1, orient="horizontal", variable=speed_pos, length=SPEED_LEN,
                     showvalue=False).pack(side="left")
            speed_value = tk.Label(speed, text=live.period_text(state["period"].get()), bg=bg, fg=T.FG, width=12, anchor="w")
            speed_value.pack(side="left", padx=(10, 0))
            trace_id = state["period"].trace_add("write", period_changed)

            foot = tk.Frame(body, bg=bg)
            foot.pack(fill="x", pady=(16, 0))

            def on_close() -> None:
                try:
                    state["period"].trace_remove("write", trace_id)
                except tk.TclError:
                    pass
                after_id = state.pop("_live_after", None)
                if after_id is not None:
                    try:
                        win.after_cancel(after_id)
                    except tk.TclError:
                        pass
                for key in ("editor", "canvas", "_readout", "_active"):
                    state[key] = None
                win.destroy()

            tk.Button(foot, text="Fermer", command=on_close, padx=14).pack(side="right")
            win.protocol("WM_DELETE_WINDOW", on_close)
            win.bind("<Escape>", lambda _e: on_close())
            state["editor"] = win
            skin.zone(win, self.accent)
            skin.apply(win)
            canvas.configure(bg=T.TROUGH)
            self.redraw(attr)
            self._live_tick(attr)
            win.lift()
            win.attributes("-topmost", True)
            win.after(300, lambda: win.attributes("-topmost", False) if win.winfo_exists() else None)
            win.focus_force()

    return CurveManager
