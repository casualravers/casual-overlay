"""Reglages incompatibles : on les grise (ils ne servent a rien dans le mode ou l'etat courant).

Deux usages :
  - `install_mode_gates(host, mode)` : fenetre d'un mode d'audio2wave (Live, Snap, Ridge). Aucune modification
    d'audio2wave : on retrouve les widgets par leur texte, et on suit les variables Tk du mode (style, forme,
    cases) par une trace Tcl.
  - `Gates` : le meme moteur pour nos propres panneaux (halo, fonte, cellules, palette du fond).

Un widget peut etre vise par plusieurs regles (ex. "Rayon du halo" : style pencil ET halo coche) : il n'est actif
que si TOUTES sont satisfaites. Les etats d'origine (par ex. un champ deja en lecture seule) sont gardes et
restaures, jamais ecrases.
"""

from __future__ import annotations

import tkinter as tk
from typing import Callable

STATEFUL = ("Scale", "Radiobutton", "Checkbutton", "Button", "Entry", "Menubutton", "Spinbox")


def walk(widget):
    for child in widget.winfo_children():
        yield child
        yield from walk(child)


def leaves(widget) -> list:
    """`widget` et tous ses descendants."""
    return [widget] + list(walk(widget))


def top_child(host, widget):
    """Le descendant direct de `host` qui contient `widget` (celui qui porte la place dans la grille)."""
    while widget.master is not host and widget.master is not None:
        widget = widget.master
    return widget


def find(host, cls: str, text: str):
    for w in walk(host):
        if w.winfo_class() == cls and w.cget("text") == text:
            return w
    return None


def row_widgets(host, anchor, panels: bool = True) -> list:
    """Tous les widgets de la ligne de `anchor`. `panels` : les panneaux gauche et droit de audio2wave partagent la
    grille de la fenetre mais ont chacun leurs lignes (colonnes 0-1 a gauche, 3-4 a droite) ; sinon (nos propres
    conteneurs) toute la ligne."""
    top = top_child(host, anchor)
    info = top.grid_info()
    if not info:
        return [top]
    if not panels:
        return list(host.grid_slaves(row=int(info["row"])))
    col = int(info["column"])
    cols = (0, 1) if col <= 1 else (3, 4)
    return [w for w in host.grid_slaves(row=int(info["row"])) if int(w.grid_info()["column"]) in cols]


def row_of_label(host, text: str, panels: bool = True) -> list:
    label = find(host, "Label", text)
    return row_widgets(host, label, panels) if label is not None else []


def rows_after(container, widget) -> list:
    """Les widgets de `container` poses sur les lignes SOUS celle de `widget` (reglages qui dependent d'une case)."""
    row = int(top_child(container, widget).grid_info()["row"])
    return [w for w in container.grid_slaves() if int(w.grid_info()["row"]) > row]


def read_var(widget, name: str):
    return widget.tk.globalgetvar(name)


def trace_var(widget, name: str, fn: Callable[[], None]) -> None:
    """Appelle `fn` a chaque ecriture de la variable Tcl `name`, sans creer d'objet Variable (dont le
    ramasse-miettes detruirait la variable d'audio2wave)."""
    command = widget.register(lambda *_a: fn())
    widget.tk.call("trace", "add", "variable", name, "write", command)


class Gates:
    """Ensemble de regles "ces widgets ne sont actifs que si `cond()`", recalculees par `refresh()`."""

    def __init__(self, muted_fg: str) -> None:
        self.muted_fg = muted_fg
        self.rules: list[tuple[list, Callable[[], bool]]] = []
        self._orig: dict[str, dict] = {}

    def add(self, widgets, cond: Callable[[], bool]) -> None:
        targets = []
        for w in widgets:
            targets.extend(leaves(w))
        if targets:
            self.rules.append((targets, cond))

    def _apply(self, widget, enabled: bool) -> None:
        key = str(widget)
        cls = widget.winfo_class()
        try:
            if cls in STATEFUL:
                orig = self._orig.setdefault(key, {"state": widget.cget("state")})
                target = orig["state"] if enabled else "disabled"
                if str(widget.cget("state")) == str(target):
                    return
                # Piege Tk : reconfigurer un Scale rend a sa variable la valeur que le curseur croyait avoir (perimee si un
                # preset vient de la changer) : on la reprend telle qu'elle etait avant.
                name = str(widget.cget("variable")) if cls == "Scale" else ""
                before = widget.tk.globalgetvar(name) if name else None
                widget.config(state=target)
                if name and str(widget.tk.globalgetvar(name)) != str(before):
                    widget.tk.globalsetvar(name, before)
            elif cls == "Label":
                orig = self._orig.setdefault(key, {"fg": widget.cget("fg")})
                widget.config(fg=orig["fg"] if enabled else self.muted_fg)
        except tk.TclError:                       # widget detruit entre-temps (reconstruction de la fenetre)
            pass

    def refresh(self) -> None:
        state: dict = {}
        for targets, cond in self.rules:
            ok = bool(cond())
            for w in targets:
                state[w] = state.get(w, True) and ok
        for w, enabled in state.items():
            self._apply(w, enabled)


def install_mode_gates(host, mode: str, muted_fg: str) -> Gates:
    """Grise les reglages sans objet dans le mode `mode` ("live", "snap" ou "ridge") du panneau `host`."""
    gates = Gates(muted_fg)

    def rb(text):                                   # variable Tcl d'un groupe de boutons radio, via l'un d'eux
        w = find(host, "Radiobutton", text)
        return str(w.cget("variable")) if w is not None else None

    def cb_var(text):
        w = find(host, "Checkbutton", text)
        return str(w.cget("variable")) if w is not None else None

    def value(name, default=""):
        return read_var(host, name) if name else default

    def checked(name) -> bool:
        return bool(name) and host.tk.getboolean(read_var(host, name))

    watched: list[str] = []

    if mode == "live":
        style_v, shape_v = rb("analyzer"), rb("bar")
        watched += [style_v, shape_v]
        analyzer = lambda: value(style_v) == "analyzer"
        # Forme, lissage et echelle des frequences n'existent que pour l'analyseur (la radio est une onde, sans FFT)
        for label in ("Forme", "Lissage", "Echelle frequences"):
            gates.add(row_of_label(host, label), analyzer)
        # L'espace entre barres n'a de sens qu'en barres
        gates.add(row_of_label(host, "Espace entre barres"), lambda: analyzer() and value(shape_v) == "bar")

    elif mode == "snap":
        style_v = rb("pencil")
        wave_v, glow_v, auto_v = cb_var("Sinusoide"), cb_var("Halo sur les kicks"), cb_var("Gain automatique")
        watched += [style_v, wave_v, glow_v, auto_v]
        pencil = lambda: value(style_v) == "pencil"
        not_pencil = lambda: value(style_v) != "pencil"
        # Trait, sinusoide, halo des kicks et videos: crayon seulement
        for label in ("Epaisseur du trait", "Rayon du halo", "Video interieure", "Video exterieure", "VIDEO"):
            gates.add(row_of_label(host, label), pencil)
        for text in ("Sinusoide", "Halo sur les kicks"):
            w = find(host, "Checkbutton", text)
            if w is not None:
                gates.add(row_widgets(host, w), pencil)
        # Echelle, filtre de colonne: rekordbox / simple. Crossover: rekordbox seul.
        for label in ("Echelle", "Filtre colonne", "REKORDBOX / SIMPLE"):
            gates.add(row_of_label(host, label), not_pencil)
        gates.add(row_of_label(host, "Crossover Hz"), lambda: value(style_v) == "rekordbox")
        # Dependances internes
        w = find(host, "Checkbutton", "Sinusoide")
        if w is not None:
            gates.add([x for x in row_widgets(host, w) if x.winfo_class() == "Scale"], lambda: checked(wave_v))
        w = find(host, "Checkbutton", "Temps reel")
        if w is not None:
            gates.add([w], lambda: checked(glow_v))
        gates.add(row_of_label(host, "Rayon du halo"), lambda: checked(glow_v))
        gates.add(row_of_label(host, "Gain manuel"), lambda: not checked(auto_v))

    elif mode == "ridge":
        auto_v = cb_var("Gain automatique")
        watched.append(auto_v)
        gates.add(row_of_label(host, "Gain manuel (dB)"), lambda: not checked(auto_v))

    for name in watched:
        if name:
            trace_var(host, name, gates.refresh)
    gates.refresh()
    return gates
