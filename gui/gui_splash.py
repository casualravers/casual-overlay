"""Ecran de chargement du demarrage (`--gui`) : une petite fenetre sombre, des les premieres secondes, pendant que le programme se
prepare (diagnostic, audio2wave) puis que la fenetre de reglages se construit sans etre affichee (transparente).

Une seule racine Tk : le chargement la cree, `run_gui(boot=...)` la reutilise. La racine reste cachee puis transparente (`alpha` 0)
pendant la construction : elle est affichee et mise en page normalement (un `withdraw()` fausserait les mesures de la mise en page
adaptative), mais invisible. Quand tout est pret, `reveal` la rend visible et ferme l'ecran de chargement."""
from __future__ import annotations

import time
import tkinter as tk
from pathlib import Path

import theme

WIDTH, HEIGHT = 460, 282
LOGO_PATH = Path(__file__).resolve().parent.parent / "asset" / "logo_chargement.png"     # fabrique par tools/make_logo_chargement.py
LOGO_W = 360
BAR_W, BAR_H = 380, 8                     # barre en gelule (capsule) : piste sombre, remplissage a l'accent avec un reflet
TAGLINE = "projection audio-reactive"
CREEP = 0.07                  # chaque jalon avance la barre de cette part du chemin restant (jamais a 100 % avant la fin)
MIN_REPAINT_S = 0.03

# Jalons de construction (sections chronometrees de la GUI) -> texte affiche.
SECTION_LABELS = {
    "build_window": "Construction de l'interface",
    "build.destruction": "Preparation de la fenetre",
    "refresh_scene_bar": "Barre des scenes",
    "bar.mesure": "Barre des scenes",
    "bar.habillage": "Habillage de l'interface",
    "panel.master_box": "Espace de controle",
    "panel.lignes": "Liste des scenes",
    "panel.lot": "Liste des scenes",
    "panel.habillage": "Habillage de l'interface",
}


class Splash:
    def __init__(self) -> None:
        self.root = tk.Tk()
        self.root.withdraw()                                 # rien d'affiche tant que l'ecran de chargement n'est pas pret
        self.root.attributes("-alpha", 0.0)
        self.frac = 0.0
        self._last = 0.0
        top = tk.Toplevel(self.root)
        self.top = top
        self.root._boot_top = top                            # build_window ne detruit pas cette fenetre avec le reste
        top.overrideredirect(True)
        top.attributes("-topmost", True)
        top.configure(bg=theme.BG, highlightthickness=1, highlightbackground=theme.LINE)
        tk.Frame(top, bg=theme.LIVE, height=3, bd=0).pack(fill="x", side="top")          # filet d'accent en haut de la carte
        x = (top.winfo_screenwidth() - WIDTH) // 2
        y = (top.winfo_screenheight() - HEIGHT) // 3
        top.geometry(f"{WIDTH}x{HEIGHT}+{x}+{y}")
        self.logo = self._load_logo()
        if self.logo is not None:                            # la forme d'onde d'un kick, aberration chromatique et glitch
            tk.Label(top, image=self.logo, bg=theme.BG, bd=0).pack(pady=(20, 2))
        tk.Label(top, text="casual-overlay", bg=theme.BG, fg=theme.FG, font=("Segoe UI", 15 if not self.logo else 14, "bold")).pack(pady=(0 if self.logo else 22, 0))
        tk.Label(top, text=TAGLINE.upper(), bg=theme.BG, fg=theme.MUTED, font=theme.FONT_KICKER).pack(pady=(0, 14))
        row = tk.Frame(top, bg=theme.BG)                     # etape en cours a gauche, avancement a droite
        row.pack(fill="x", padx=(WIDTH - BAR_W) // 2)
        self.label = tk.Label(row, text="Demarrage", bg=theme.BG, fg=theme.FG, font=theme.FONT, anchor="w")
        self.label.pack(side="left")
        self.percent = tk.Label(row, text="0 %", bg=theme.BG, fg=theme.MUTED, font=theme.FONT_SMALL, anchor="e")
        self.percent.pack(side="right")
        self.bar = tk.Canvas(top, width=BAR_W, height=BAR_H, bg=theme.BG, bd=0, highlightthickness=0)
        self.bar.pack(pady=(8, 0))
        self._capsule(0, BAR_W, theme.TROUGH)                # piste
        self.fill = []                                       # morceaux du remplissage (gelule + reflet), redessines a chaque avance
        self._draw_fill(0.0)
        top.update()                                         # premiere peinture : evenements traites une fois, ensuite seulement le dessin

    def _capsule(self, x0: float, x1: float, color: str, y0: float = 0, y1: float = BAR_H) -> list:
        """Une gelule horizontale (rectangle + deux disques) sur la barre ; rend les identifiants des formes."""
        r = (y1 - y0) / 2
        x1 = max(x1, x0 + 2 * r)
        c = self.bar
        return [c.create_oval(x0, y0, x0 + 2 * r, y1, fill=color, width=0), c.create_oval(x1 - 2 * r, y0, x1, y1, fill=color, width=0),
                c.create_rectangle(x0 + r, y0, x1 - r, y1, fill=color, width=0)]

    def _draw_fill(self, frac: float) -> None:
        for item in self.fill:
            self.bar.delete(item)
        self.fill = []
        if frac <= 0:
            return
        x1 = BAR_W * min(frac, 1.0)
        self.fill = self._capsule(0, x1, theme.LIVE)
        self.fill += self._capsule(2, max(x1 - 2, BAR_H), theme.lighten(theme.LIVE, 0.22), 1, 3)          # reflet fin sur le haut

    @staticmethod
    def _load_logo():
        """Le logo du chargement, reduit a LOGO_W (None si le fichier ou Pillow manque : le titre seul est affiche)."""
        try:
            from PIL import Image, ImageTk
            img = Image.open(LOGO_PATH).convert("RGBA")
            img = img.resize((LOGO_W, max(round(img.height * LOGO_W / img.width), 1)), Image.LANCZOS)
            bg = Image.new("RGBA", img.size, theme.BG)           # compose sur le fond : coins nets, pas de frange sombre
            bg.alpha_composite(img)
            return ImageTk.PhotoImage(bg.convert("RGB"))
        except Exception:
            return None

    def bump(self, label: str | None = None, to: float | None = None) -> None:
        """Avance la barre (jamais en arriere) et change le texte ; le dessin est limite a ~30 par seconde."""
        self.frac = max(self.frac, to) if to is not None else self.frac + (0.97 - self.frac) * CREEP
        now = time.perf_counter()
        if label is None and now - self._last < MIN_REPAINT_S:
            return
        self._last = now
        try:
            if label:
                self.label.configure(text=label.rstrip(". "))
            self.percent.configure(text=f"{int(min(self.frac, 1.0) * 100)} %")
            self._draw_fill(self.frac)
            self.top.update_idletasks()                      # dessin seul : pas d'evenements (les minuteries de la fenetre en construction attendent)
        except tk.TclError:
            pass

    def on_section(self, name: str) -> None:
        """Appelee a l'entree de chaque section chronometree de la GUI (`uistats.Stats.on_enter`)."""
        self.bump(SECTION_LABELS.get(name))

    def close(self) -> None:
        try:
            self.root._boot_top = None
            self.top.destroy()
        except tk.TclError:
            pass


def start() -> Splash | None:
    """L'ecran de chargement, ou None si Tk n'est pas disponible (le demarrage se fait alors comme avant)."""
    try:
        return Splash()
    except Exception:
        return None