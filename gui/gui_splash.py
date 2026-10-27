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

WIDTH, HEIGHT = 420, 232
LOGO_PATH = Path(__file__).resolve().parent.parent / "asset" / "logo_chargement.png"     # fabrique par tools/make_logo_chargement.py
LOGO_W = 340
BAR_W, BAR_H = 340, 6
CREEP = 0.07                  # chaque jalon avance la barre de cette part du chemin restant (jamais a 100 % avant la fin)
MIN_REPAINT_S = 0.03

# Jalons de construction (sections chronometrees de la GUI) -> texte affiche.
SECTION_LABELS = {
    "build_window": "Construction de l'interface...",
    "build.destruction": "Preparation de la fenetre...",
    "refresh_scene_bar": "Barre des scenes...",
    "bar.mesure": "Barre des scenes...",
    "bar.habillage": "Habillage...",
    "panel.master_box": "Espace de controle...",
    "panel.lignes": "Liste des scenes...",
    "panel.lot": "Liste des scenes...",
    "panel.habillage": "Habillage...",
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
        x = (top.winfo_screenwidth() - WIDTH) // 2
        y = (top.winfo_screenheight() - HEIGHT) // 3
        top.geometry(f"{WIDTH}x{HEIGHT}+{x}+{y}")
        self.logo = self._load_logo()
        if self.logo is not None:                            # la forme d'onde d'un kick, aberration chromatique et glitch
            tk.Label(top, image=self.logo, bg=theme.BG, bd=0).pack(pady=(24, 4))
        tk.Label(top, text="casual-overlay", bg=theme.BG, fg=theme.FG, font=("Segoe UI", 13 if self.logo else 15, "bold")).pack(pady=(0 if self.logo else 20, 2))
        self.label = tk.Label(top, text="Demarrage...", bg=theme.BG, fg=theme.MUTED, font=theme.FONT_SMALL)
        self.label.pack()
        self.bar = tk.Canvas(top, width=BAR_W, height=BAR_H, bg=theme.PANEL, bd=0, highlightthickness=0)
        self.bar.pack(pady=(10, 0))
        self.fill = self.bar.create_rectangle(0, 0, 0, BAR_H, fill=theme.LIVE, width=0)
        top.update()                                         # premiere peinture : evenements traites une fois, ensuite seulement le dessin

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
                self.label.configure(text=label)
            self.bar.coords(self.fill, 0, 0, BAR_W * min(self.frac, 1.0), BAR_H)
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