"""Routage audio par effet (#198) : pastille de source a cote de chaque effet et onglet « Routage » (tableau de toutes les lignes + mini-spectre).

Une seule donnee : les reglages `rt_<id>` (source) et `rg_<id>` (dosage) de la table unique (core/routing.py). La pastille et le tableau
partagent les MEMES variables Tk (une par cle, enregistrees par `bind_param` dans `synced`) : modifier l'un met l'autre a jour, et
appliquer une scene ou un preset (qui ecrit `synced`) met les deux a jour. Largeurs fixes : rien ne bouge quand un texte change.
Fiche : docs/fonctions/gui.md (section Routage).
"""
from __future__ import annotations

import math
import tkinter as tk
import types

import theme as T
from core import routing as rt

DOSES = (0.0, 0.25, 0.5, 0.75, 1.0, 1.25, 1.5, 2.0)          # dosages proposes dans le menu de la pastille (le tableau a un curseur)
LEVEL_W, LEVEL_H = 54, 8                                      # temoin de niveau d'une ligne
SPEC_W, SPEC_H = 300, 40                                      # mini-spectre
REFRESH_MS = 250
INFO_TEXT = {"strobe": "decide seul (phases, grille) : onglet Live",
             "foudre": "decide seule (phases, grille) : onglet Live",
             "noise": "ses propres cases : onglet Noise"}


def _get(var: tk.Variable, default):
    try:
        return var.get()
    except (tk.TclError, ValueError):
        return default


class Routing:
    """Variables partagees, pastilles et onglet. `c` : params, synced, bind_param, Tooltip, s (session : `s.status`), ui_every."""

    def __init__(self, c) -> None:
        self.c = c
        self.pills: dict[str, list[tk.Menubutton]] = {}
        self.rows: dict[str, dict] = {}
        self.hover: str | None = None
        self.spectrum: tk.Canvas | None = None
        self._traced: set[str] = set()

    # --- donnee -------------------------------------------------------------------------------------------------------------------
    def vars(self, rid: str) -> tuple[tk.StringVar, tk.DoubleVar]:
        """(source, dosage) de la ligne : crees et lies une seule fois, puis partages par tous les widgets."""
        c, out = self.c, []
        for key, make, conv, default in ((rt.source_key(rid), tk.StringVar, str, rt.INHERIT), (rt.dose_key(rid), tk.DoubleVar, float, 1.0)):
            var = c.synced.get(key)
            if var is None:
                var = make(value=c.params.get(key, default))
                c.bind_param(key, var, conv)
            out.append(var)
        return out[0], out[1]

    def route(self, rid: str) -> tuple[str, float]:
        src, dose = self.vars(rid)
        return rt.route_of({rt.source_key(rid): _get(src, rt.INHERIT), rt.dose_key(rid): _get(dose, 1.0)}, rid)

    def reset(self, rids) -> None:
        for rid in rids:
            src, dose = self.vars(rid)
            src.set(rt.INHERIT)
            dose.set(1.0)

    # --- pastille -----------------------------------------------------------------------------------------------------------------
    def fill_menu(self, menu: tk.Menu, rid: str, with_dose: bool = True) -> None:
        src, dose = self.vars(rid)
        for n, (_family, sources) in enumerate(rt.SOURCE_FAMILIES):
            if n:
                menu.add_separator()
            for value in sources:
                menu.add_radiobutton(label=rt.menu_label(value), value=value, variable=src)
        if with_dose:
            menu.add_separator()
            sub = tk.Menu(menu, tearoff=0)
            for d in DOSES:
                sub.add_radiobutton(label=f"{int(round(d * 100))} %", value=d, variable=dose)
            menu.add_cascade(label="Dosage", menu=sub)

    def pill(self, parent: tk.Misc, rid: str, with_dose: bool = True) -> tk.Menubutton:
        """Pastille de source : grise « Comme le fond » par defaut, a l'accent « Basses 80 % » quand la ligne est reglee ; clic = menu."""
        mb = tk.Menubutton(parent, width=rt.PILL_CHARS, anchor="w", relief="flat", borderwidth=0, highlightthickness=0,
                           font=T.FONT_SMALL, cursor="hand2", padx=6, pady=1, indicatoron=0)
        mb._noskin = True                               # dessinee ici (le skin en ferait un champ de liste)
        menu = tk.Menu(mb, tearoff=0)
        self.fill_menu(menu, rid, with_dose)
        mb.configure(menu=menu)
        self.pills.setdefault(rid, []).append(mb)
        if rid not in self._traced:                     # une trace par ligne (toutes ses pastilles se repeignent ensemble)
            self._traced.add(rid)
            for var in self.vars(rid):
                var.trace_add("write", lambda *_a, r=rid: self.paint(r))
        tip = getattr(self.c, "Tooltip", None)
        if tip is not None:
            tip(mb, f"Source audio de « {rt.BY_ID[rid].label} » ({'fond' if rt.BY_ID[rid].layer == 'bg' else 'logo'}). « Comme la couche » = la source "
                    "de la couche (menu « reagit a »). Meme reglage que l'onglet Routage.")
        mb.bind("<Enter>", lambda _e, r=rid: self.set_hover(r), add="+")
        self.paint(rid)
        return mb

    def paint(self, rid: str) -> None:
        src, dose = self.route(rid)
        default = (src, dose) == (rt.INHERIT, 1.0)
        enabled = rt.line_enabled(self.c.params, rid)
        text = rt.pill_text(src, dose, rt.BY_ID[rid].layer)
        if rt.fx_route_id(rid, float(self.c.params.get("fx_link", 1.0)) >= 0.5) != rid:
            text = "Suit le fond"                       # logo lie : ses effets suivent la ligne du fond (sa propre ligne est ignoree)
        if default:
            bg, fg = T.CARD, (T.MUTED if enabled else T.FAINT)
        else:
            bg, fg = (T.OVERLAY, T.ON_ACCENT) if enabled else (T.LINE, T.MUTED)
        for mb in list(self.pills.get(rid, [])):
            try:
                if mb.cget("text") != text or mb.cget("bg") != bg or mb.cget("fg") != fg:
                    mb.configure(text=text, bg=bg, fg=fg, activebackground=bg, activeforeground=fg)
            except tk.TclError:
                self.pills[rid].remove(mb)
        row = self.rows.get(rid)
        if row is not None:
            try:
                row["label"].configure(fg=T.FG if enabled else T.FAINT)
            except tk.TclError:
                pass
        if self.hover == rid:
            self.draw_spectrum()

    # --- onglet ---------------------------------------------------------------------------------------------------------------------
    def build_tab(self, tab: tk.Frame, zone, add_separator, compact_scale, row_padx: int = 6, row_pady: int = 2) -> None:
        intro = tk.Label(tab, text="Chaque ligne choisit la partie du son qui la pilote. « Comme la couche » = comme avant.",
                         fg=T.MUTED, font=T.FONT_SMALL, anchor="w", justify="left", wraplength=SPEC_W + 120)
        intro.grid(row=zone.next_row(), column=0, columnspan=5, sticky="w", padx=row_padx)
        self.spectrum = tk.Canvas(tab, width=SPEC_W, height=SPEC_H, bg=T.PANEL, highlightthickness=0)
        self.spectrum.grid(row=zone.next_row(), column=0, columnspan=5, sticky="w", padx=row_padx, pady=(4, 6))
        self.draw_spectrum()
        for zname, title in (("fond", "Fond"), ("logo", "Logo"), ("global", "Global")):
            add_separator(zone, title)
            for r in rt.ROUTES:
                if r.zone == zname:
                    self._row(tab, zone, r, compact_scale, row_padx, row_pady)
            ids = [r.id for r in rt.ROUTES if r.zone == zname and r.kind != "info"]
            btn = tk.Button(tab, text="Tout remettre", command=lambda ids=ids: self.reset(ids), font=T.FONT_SMALL)
            btn.grid(row=zone.next_row(), column=1, sticky="w", padx=row_padx, pady=(row_pady, 6))
        self.c.ui_every("routage", REFRESH_MS, self.refresh)

    def _row(self, tab, zone, r, compact_scale, padx, pady) -> None:
        row = zone.next_row()
        label = tk.Label(tab, text=r.label, anchor="w")
        label.grid(row=row, column=0, sticky="w", padx=padx, pady=pady)
        cells = {"label": label}
        if r.kind == "info":
            tk.Label(tab, text=INFO_TEXT.get(r.id, ""), fg=T.MUTED, font=T.FONT_SMALL, anchor="w").grid(
                row=row, column=1, columnspan=4, sticky="w", padx=padx, pady=pady)
            self.rows[r.id] = cells
            return
        cells["pill"] = self.pill(tab, r.id, with_dose=False)
        cells["pill"].grid(row=row, column=1, sticky="w", padx=padx, pady=pady)
        level = tk.Canvas(tab, width=LEVEL_W, height=LEVEL_H, bg=T.TROUGH, highlightthickness=0)
        level.grid(row=row, column=2, sticky="w", padx=padx, pady=pady)
        cells["bar"] = level.create_rectangle(0, 0, 0, LEVEL_H, fill=T.OVERLAY, width=0)
        cells["level"] = level
        _src, dose = self.vars(r.id)
        scale = compact_scale(tab, rt.DOSE_MIN, rt.DOSE_MAX, 0.05, dose, 90)
        scale.grid(row=row, column=3, sticky="w", padx=padx, pady=pady)
        reset = tk.Button(tab, text="↺", width=2, command=lambda rid=r.id: self.reset([rid]), font=T.FONT_SMALL)
        reset.grid(row=row, column=4, sticky="w", padx=padx, pady=pady)
        for w in (label, level, scale, reset):
            w.bind("<Enter>", lambda _e, rid=r.id: self.set_hover(rid), add="+")
        self.rows[r.id] = cells
        self.paint(r.id)

    def set_hover(self, rid: str) -> None:
        if self.hover != rid:
            self.hover = rid
            self.draw_spectrum()

    @staticmethod
    def _x(hz: float) -> float:
        lo, hi = rt.SPECTRUM_HZ
        return SPEC_W * (math.log10(hz) - math.log10(lo)) / (math.log10(hi) - math.log10(lo))

    def draw_spectrum(self) -> None:
        """Le spectre 20 Hz - 20 kHz ; la ligne survolee y surligne sa plage, ou dit que sa source est un evenement (kick, grille...)."""
        cv = self.spectrum
        if cv is None:
            return
        try:
            cv.delete("all")
        except tk.TclError:
            self.spectrum = None
            return
        base = SPEC_H - 12
        for hz, text in ((20, "20"), (200, "200"), (2000, "2k"), (20000, "20k")):
            x = min(max(self._x(hz), 8), SPEC_W - 10)
            cv.create_line(x, base, x, base + 3, fill=T.LINE)
            cv.create_text(x, SPEC_H - 4, text=text, fill=T.FAINT, font=T.FONT_SMALL)
        cv.create_line(0, base, SPEC_W, base, fill=T.LINE)
        rid = self.hover
        if rid is None or rt.BY_ID[rid].kind == "info":
            cv.create_text(SPEC_W / 2, base / 2, text="survole une ligne pour voir ce qu'elle ecoute", fill=T.FAINT, font=T.FONT_SMALL)
            return
        src, _dose = self.route(rid)
        shown = src
        on_bg = rt.BY_ID[rid].layer == "bg" or float(self.c.params.get("fx_link", 1.0)) >= 0.5 and rid in rt.FX_LINES
        if src == rt.INHERIT:
            shown = self.c.params.get("src_bg" if on_bg else "src_logo", "tout")
        if shown == "auto":                             # la source qu'Auto a choisie, publiee par le rendu (s.status["sources"])
            status = getattr(getattr(self.c, "s", None), "status", None) or {}
            sources = status.get("sources")
            shown = sources[0 if on_bg else 1] if sources else "tout"
        rng = rt.source_range_hz(shown)
        name = f"{rt.BY_ID[rid].label} <- {rt.source_label(src)}" + (f" ({rt.source_label(shown)})" if src == rt.INHERIT else "")
        if rng is not None:
            x0, x1 = self._x(rng[0]), self._x(rng[1])
            cv.create_rectangle(x0, 4, x1, base, fill=T.OVERLAY, outline="", stipple="gray50")
            cv.create_text(SPEC_W / 2, 9, text=name, fill=T.FG, font=T.FONT_SMALL)
        else:
            cv.create_text(SPEC_W / 2, base / 2, text=name + " : evenement, pas une bande", fill=T.FG, font=T.FONT_SMALL)

    def refresh(self) -> None:
        """Temoins de niveau (source resolue publiee par le rendu) et grisage des lignes (effet coupe, reaction a 0)."""
        status = getattr(getattr(self.c, "s", None), "status", None) or {}
        now = status.get("routes_now") or {}
        for rid, cells in self.rows.items():
            bar = cells.get("bar")
            if bar is not None:
                level = float(now.get(rid, ("", 0.0))[1]) if rid in now else 0.0
                try:
                    cells["level"].coords(bar, 0, 0, LEVEL_W * min(max(level, 0.0), 1.0), LEVEL_H)
                except tk.TclError:
                    continue
        for rid in list(self.pills):
            self.paint(rid)


def install(c) -> Routing:
    return Routing(c)


def stub_context(params: dict, status: dict | None = None):
    """Contexte minimal (tests) : variables liees a `params` comme `gl_gui.bind_param`."""
    synced: dict = {}

    def bind_param(key, var, conv=float):
        synced[key] = var

        def on_write(*_a):
            try:
                params[key] = conv(var.get())
            except (ValueError, tk.TclError):
                pass
        var.trace_add("write", on_write)

    return types.SimpleNamespace(params=params, synced=synced, bind_param=bind_param, Tooltip=None,
                                 s=types.SimpleNamespace(status=status if status is not None else {}), ui_every=lambda *a, **k: None)
