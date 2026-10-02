"""Aides a la saisie des couleurs dans les panneaux d'audio2wave (Live, Snap, Ridge).

Leurs champs "Couleurs" / "Couleur de fond" / "Couleur du trait" sont du texte libre (nom ffmpeg, 0xRRGGBB, plusieurs
couleurs separees par |). On garde le champ tel quel (aucune modification d'audio2wave) et on lui ajoute, dans la
meme cellule de la grille :
  - une pastille par couleur du champ (fond = la couleur ; un clic ouvre le selecteur de couleur et remplace CETTE
    couleur) ;
  - un menu de couleurs nommees (remplace la premiere couleur) ;
  - un "+" pour ajouter une couleur aux champs qui en acceptent plusieurs ("Couleurs" de Live et de Snap).
Les pastilles suivent le champ (trace Tcl de sa variable) : taper, charger un preset ou choisir une couleur les met a jour.
"""

from __future__ import annotations

import tkinter as tk
from tkinter import colorchooser

from gui_gates import find, leaves, row_of_label, trace_var

NAMED = ("white", "black", "gray", "red", "orange", "yellow", "lime", "green", "teal", "cyan", "blue", "purple",
         "magenta", "pink")
FIELDS = {"Couleurs": True, "Couleur de fond": False, "Couleur du trait": False}    # libelle -> accepte plusieurs couleurs
HEX = "0123456789abcdef"


def to_rgb(text: str):
    """(r, v, b) d'une couleur ffmpeg (nom CSS, 0xRRGGBB, #RRGGBB, alpha ignore), ou None si on ne sait pas."""
    base = text.strip().split("@")[0].lower()
    if not base:
        return None
    body = base[2:] if base.startswith("0x") else base[1:] if base.startswith("#") else None
    if body is not None:
        if len(body) in (6, 8) and all(c in HEX for c in body):
            return tuple(int(body[i:i + 2], 16) for i in (0, 2, 4))
        return None
    try:
        from PIL import ImageColor
        return tuple(ImageColor.getrgb(base)[:3])
    except (ValueError, ImportError):
        return None


def hex_of(rgb) -> str:
    return "#%02x%02x%02x" % tuple(rgb)


def ffmpeg_hex(rgb) -> str:
    return "0x%02x%02x%02x" % tuple(rgb)


def _contrast(rgb) -> str:
    return "#000000" if (0.299 * rgb[0] + 0.587 * rgb[1] + 0.114 * rgb[2]) > 140 else "#ffffff"


class ColorField:
    """Un champ de couleur d'audio2wave + ses aides."""

    def __init__(self, host, entry, label: str, multi: bool, accent: str, accent_fg: str) -> None:
        self.host, self.entry, self.label, self.multi = host, entry, label, multi
        self.var_name = str(entry.cget("textvariable"))
        info = entry.grid_info()
        self.box = tk.Frame(host)
        self.box.grid(row=int(info["row"]), column=int(info["column"]), sticky=info.get("sticky", "w"),
                      padx=info.get("padx", 0), pady=info.get("pady", 0))
        entry.grid(in_=self.box, row=0, column=0, padx=0, pady=0)       # le champ reste le meme widget, dans la cellule
        entry.tkraise(self.box)       # un widget grille dans un autre conteneur doit etre AU-DESSUS de lui, sinon il est cache
        self.swatches = tk.Frame(self.box)
        self.swatches.grid(row=0, column=1, padx=(6, 0))
        self.names = tk.Menubutton(self.box, text="▾", relief="flat", bg=accent, fg=accent_fg, padx=6, pady=0,
                                   cursor="hand2", activebackground=accent, activeforeground=accent_fg)
        menu = tk.Menu(self.names, tearoff=0)
        for name in NAMED:
            rgb = to_rgb(name)
            menu.add_command(label=name, background=hex_of(rgb), foreground=_contrast(rgb),
                             command=lambda n=name: self.set_segment(0, n))
        self.names.config(menu=menu)
        self.names.grid(row=0, column=2, padx=(6, 0))
        self.accent, self.accent_fg = accent, accent_fg
        trace_var(host, self.var_name, self.refresh)
        self.refresh()

    # -- texte du champ <-> segments ---------------------------------------------------------------
    def segments(self) -> list[str]:
        text = str(self.host.tk.globalgetvar(self.var_name))
        return text.split("|") if text.strip() else [""]

    def write(self, segments: list[str]) -> None:
        self.host.tk.globalsetvar(self.var_name, "|".join(segments))
        # Snap et Ridge appliquent le champ sur Entree (Live, sur la variable) ; Tk n'envoie un evenement clavier
        # qu'a la fenetre qui a le focus, d'ou le focus donne au champ juste avant.
        self.entry.focus_force()
        self.entry.event_generate("<Return>")

    def set_segment(self, index: int, color: str) -> None:
        segs = self.segments()
        while len(segs) <= index:
            segs.append("")
        segs[index] = color
        self.write(segs)

    def add_segment(self) -> None:
        segs = [s for s in self.segments() if s.strip()]
        self.write(segs + [segs[-1] if segs else "white"])

    def pick(self, index: int) -> None:
        segs = self.segments()
        current = to_rgb(segs[index]) if index < len(segs) else None
        rgb, _ = colorchooser.askcolor(color=hex_of(current) if current else "#ffffff", parent=self.host.winfo_toplevel(),
                                       title=self.label)
        if rgb:
            self.set_segment(index, ffmpeg_hex([int(round(v)) for v in rgb]))

    # -- pastilles ----------------------------------------------------------------------------------
    def refresh(self) -> None:
        try:
            for child in self.swatches.winfo_children():
                child.destroy()
            for i, seg in enumerate(self.segments()):
                rgb = to_rgb(seg)
                btn = tk.Button(self.swatches, width=2, relief="flat", cursor="hand2", padx=0, pady=0,
                                bg=hex_of(rgb) if rgb else "#555555", fg=_contrast(rgb) if rgb else "#ffffff",
                                activebackground=hex_of(rgb) if rgb else "#555555",
                                text="" if rgb else "?", command=lambda i=i: self.pick(i))
                btn.pack(side="left", padx=(0, 3))
            if self.multi:
                tk.Button(self.swatches, text="+", width=2, relief="flat", cursor="hand2", padx=0, pady=0,
                          bg=self.accent, fg=self.accent_fg, command=self.add_segment).pack(side="left")
        except tk.TclError:                          # fenetre reconstruite entre-temps
            pass

    def swatch_colors(self) -> list[str]:
        return [str(b.cget("bg")) for b in self.swatches.winfo_children() if str(b.cget("text")) != "+"]


def add_color_helpers(host, accent: str, accent_fg: str) -> dict[str, ColorField]:
    """Ajoute les aides a chaque champ de couleur du panneau `host` ; renvoie les champs par libelle."""
    fields: dict[str, ColorField] = {}
    for label, multi in FIELDS.items():
        for w in row_of_label(host, label):
            entry = next((x for x in leaves(w) if x.winfo_class() == "Entry"), None)
            if entry is not None:
                fields[label] = ColorField(host, entry, label, multi, accent, accent_fg)
                break
    return fields
