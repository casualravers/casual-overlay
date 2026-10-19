"""Fenetre glfw : choix du moniteur, plein ecran, curseur.

Extrait tel quel de `audio2wave_gl` (qui re-exporte ces noms). Les dependances facultatives viennent de `engine.deps`.
"""
from __future__ import annotations

import ctypes
import sys

from engine.deps import glfw


# --- Fenetre glfw ------------------------------------------------------------------

def _addr(monitor) -> int:
    return ctypes.cast(monitor, ctypes.c_void_p).value or 0


def monitor_contains(monitor, point) -> bool:
    """Le point (x, y) du bureau est-il sur ce moniteur ?"""
    if point is None:
        return False
    mx, my = glfw.get_monitor_pos(monitor)
    mode = glfw.get_video_mode(monitor)
    return mx <= point[0] < mx + mode.size.width and my <= point[1] < my + mode.size.height


def pick_monitor(index: int | None, avoid=None):
    """Moniteur cible : --monitor N (index glfw), sinon un moniteur qui n'est PAS celui de la fenetre de reglages (`avoid` =
    un point de cette fenetre), sinon le premier NON principal (l'ordre des moniteurs n'est pas fiable), sinon le principal."""
    monitors = glfw.get_monitors()
    if index is not None:
        if not 0 <= index < len(monitors):
            print(f"--monitor {index} invalide ({len(monitors)} moniteur(s) detecte(s)).", file=sys.stderr)
            sys.exit(2)
        return monitors[index]
    if avoid is not None:
        for m in monitors:
            if not monitor_contains(m, avoid):
                return m
    primary = _addr(glfw.get_primary_monitor())
    for m in monitors:
        if _addr(m) != primary:
            return m
    return monitors[0]


def resolve_fullscreen(requested: bool | None) -> bool:
    """Fenetre par defaut, TOUJOURS : un plein ecran borderless pouvait recouvrir le poste de travail et la GUI (souris
    bloquee). Il faut le demander (--fullscreen, case « Plein ecran », touche F)."""
    return bool(requested)

def windowed_rect(mx: int, my: int, mw: int, mh: int, size: tuple[int, int], shared: bool) -> tuple[int, int, int, int]:
    """(x, y, largeur, hauteur) de la fenetre de projection sur un moniteur : centree, ou dans le coin bas droit si le moniteur
    est aussi celui des reglages (ils restent atteignables). Toujours contenue dans le moniteur."""
    w, h = min(size[0], max(mw - 40, 320)), min(size[1], max(mh - 80, 240))
    if shared:
        x, y = mx + mw - w - 20, my + mh - h - 60
    else:
        x, y = mx + (mw - w) // 2, my + (mh - h) // 2
    return max(x, mx), max(y, my), w, h


def fullscreen_blocked(on_gui_monitor: bool | None, is_primary: bool, monitor_index, requested) -> bool:
    """Un plein ecran demande par la case / un preset est refuse sur l'ecran des reglages (il les recouvrirait) ; position de la
    GUI inconnue (`on_gui_monitor` None) : sur l'ecran principal. `--monitor` ou `--fullscreen` explicites l'emportent."""
    if monitor_index is not None or requested is True:
        return False
    return is_primary if on_gui_monitor is None else on_gui_monitor


def hide_cursor(fullscreen: bool, on_primary: bool) -> bool:
    """Le curseur n'est masque qu'en plein ecran sur un moniteur NON principal (projecteur).
    Sur le moniteur principal, le masquer ferait perdre la souris a l'utilisateur."""
    return fullscreen and not on_primary


class Window:
    def __init__(self, monitor, windowed_size: tuple[int, int], fullscreen: bool, shared: bool = False):
        self.monitor = monitor
        self.shared = shared                  # le moniteur est aussi celui des reglages : la fenetre va dans le coin bas droit
        self.is_primary = _addr(monitor) == _addr(glfw.get_primary_monitor())
        self.mx, self.my = glfw.get_monitor_pos(monitor)
        mode = glfw.get_video_mode(monitor)
        self.mw, self.mh = mode.size.width, mode.size.height
        self.windowed_size = windowed_size
        self.fullscreen = fullscreen
        glfw.window_hint(glfw.CONTEXT_VERSION_MAJOR, 3)
        glfw.window_hint(glfw.CONTEXT_VERSION_MINOR, 3)
        glfw.window_hint(glfw.OPENGL_PROFILE, glfw.OPENGL_CORE_PROFILE)
        glfw.window_hint(glfw.AUTO_ICONIFY, False)
        glfw.window_hint(glfw.VISIBLE, False)
        glfw.window_hint(glfw.DECORATED, not fullscreen)
        w, h = (self.mw, self.mh) if fullscreen else windowed_size
        self.handle = glfw.create_window(w, h, "casual-overlay GL", None, None)
        if not self.handle:
            print("Impossible de creer la fenetre OpenGL 3.3 (pilote graphique ?).", file=sys.stderr)
            sys.exit(1)
        self._place()
        glfw.make_context_current(self.handle)
        glfw.swap_interval(1)
        glfw.show_window(self.handle)

    def retarget(self, monitor, avoid=None) -> None:
        """Vise un autre moniteur (le projecteur branche apres le lancement, par exemple)."""
        self.monitor = monitor
        self.shared = monitor_contains(monitor, avoid)
        self.is_primary = _addr(monitor) == _addr(glfw.get_primary_monitor())
        self.mx, self.my = glfw.get_monitor_pos(monitor)
        mode = glfw.get_video_mode(monitor)
        self.mw, self.mh = mode.size.width, mode.size.height

    def _place(self) -> None:
        if self.fullscreen:
            glfw.set_window_attrib(self.handle, glfw.DECORATED, False)
            glfw.set_window_pos(self.handle, self.mx, self.my)
            glfw.set_window_size(self.handle, self.mw, self.mh)
            hidden = hide_cursor(True, self.is_primary)
            glfw.set_input_mode(self.handle, glfw.CURSOR, glfw.CURSOR_HIDDEN if hidden else glfw.CURSOR_NORMAL)
        else:
            w, h = self.windowed_size
            glfw.set_window_attrib(self.handle, glfw.DECORATED, True)
            x, y, w, h = windowed_rect(self.mx, self.my, self.mw, self.mh, (w, h), self.shared)
            glfw.set_window_size(self.handle, w, h)
            glfw.set_window_pos(self.handle, x, y)
            glfw.set_input_mode(self.handle, glfw.CURSOR, glfw.CURSOR_NORMAL)

    def toggle_fullscreen(self) -> None:
        self.fullscreen = not self.fullscreen
        self._place()

    def framebuffer_size(self) -> tuple[int, int]:
        w, h = glfw.get_framebuffer_size(self.handle)
        return max(w, 1), max(h, 1)
