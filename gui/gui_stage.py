"""Mode « scene » : une fenetre epuree en plein ecran pour jouer au portable dans la penombre — les scenes du set actif en grosses touches,
la PANIQUE, le Verrou, la lecture du VJ et les deux masters (Overlay / Fond). Rien d'autre.

Module de la fenetre de reglages (comme les `gui_*.py`) : `install(c)` recoit les fonctions de la fenetre par le contexte `c`. La fenetre se
met a jour toute seule (scene active, set, verrou, VJ) par le planificateur de la fenetre principale.
"""
from __future__ import annotations

import tkinter as tk
import types

COLUMNS_MAX = 4
POLL_MS = 250


def grid_columns(count: int, width: int) -> int:
    """Nombre de colonnes de touches : assez pour ne pas faire defiler, des touches d'au moins ~220 px de large."""
    if count <= 0:
        return 1
    return max(1, min(COLUMNS_MAX, count, width // 220 if width > 0 else COLUMNS_MAX))


def install(c):
    from gl_gui import BANNER_FG, SCENE_COLOR
    a2w = c.a2w
    root = c.root
    params = c.params
    scene_book = c.scene_book
    s = c.s
    state: dict = {"win": None, "sig": None, "widgets": {}}
    BIG = ("Segoe UI", 18, "bold")
    MID = ("Segoe UI", 13, "bold")
    RED = "#d92b2b"

    def window_alive() -> bool:
        win = state.get("win")
        try:
            return win is not None and bool(win.winfo_exists())
        except tk.TclError:
            return False

    def signature() -> tuple:
        rows = scene_book.rows()
        return (tuple((r["index"], r["name"]) for r in rows), scene_book.active_set_name(), bool(getattr(s, "scene_lock", False)),
                bool(c.vj["running"]), getattr(s, "active_entry", None), getattr(s, "active_scene", None))

    def rebuild() -> None:
        win, w = state["win"], state["widgets"]
        for child in w["scenes"].winfo_children():
            child.destroy()
        rows = scene_book.rows()
        cols = grid_columns(len(rows), win.winfo_width())
        for i in range(COLUMNS_MAX):
            w["scenes"].columnconfigure(i, weight=1 if i < cols else 0, uniform="stage")
        active = c.current_index(rows)
        for r in rows:
            on = r["index"] == active
            b = tk.Button(w["scenes"], text=r["name"], command=lambda i=r["index"]: c.apply_entry(i), font=BIG, relief="flat", bd=0, wraplength=360,
                          bg=SCENE_COLOR if on else a2w.GUI_BG, fg=BANNER_FG if on else a2w.GUI_FG, activebackground=SCENE_COLOR,
                          activeforeground=BANNER_FG, cursor="hand2", pady=22)
            b.grid(row=r["index"] // cols, column=r["index"] % cols, sticky="nsew", padx=5, pady=5)
        for rr in range((len(rows) + cols - 1) // cols):
            w["scenes"].rowconfigure(rr, weight=1)
        if not rows:
            tk.Label(w["scenes"], text="Aucune scene dans ce set", font=BIG, bg=a2w.GUI_PANEL_BG, fg=a2w.GUI_MUTED_FG).grid(row=0, column=0, pady=40)
        w["title"].configure(text=f"SET  {scene_book.active_set_name()}")
        lock = bool(getattr(s, "scene_lock", False))
        w["lock"].configure(text="Verrou ●" if lock else "Verrou", bg=SCENE_COLOR if lock else a2w.GUI_BG, fg=BANNER_FG if lock else a2w.GUI_FG)
        playing = bool(c.vj["running"])
        w["vj"].configure(text="■ VJ" if playing else "▶ VJ", bg=SCENE_COLOR if playing else a2w.GUI_BG, fg=BANNER_FG if playing else a2w.GUI_FG)

    def tick() -> None:
        if not window_alive():
            return
        w = state["widgets"]
        sig = signature()
        if sig != state["sig"]:
            state["sig"] = sig
            rebuild()
        w["status"].configure(text=c.status_text())
        for key in ("overlay_master", "bg_master"):                 # les curseurs suivent le reglage (panique, memoire, clavier...) sauf pendant qu'on les tient
            sc = w[key]
            if not state.get("holding") and abs(sc.get() - float(params[key]) * 100.0) > 0.5:
                sc.set(float(params[key]) * 100.0)

    def close(_evt=None) -> None:
        win = state.get("win")
        state["win"] = None
        if win is not None:
            try:
                c.ui_remove("stage")
                win.destroy()
            except tk.TclError:
                pass

    def toggle_fullscreen(_evt=None) -> None:
        win = state.get("win")
        if win is not None:
            win.attributes("-fullscreen", not bool(win.attributes("-fullscreen")))

    def open_stage(_evt=None) -> None:
        if window_alive():
            state["win"].lift()
            return
        win = state["win"] = tk.Toplevel(root)
        win.title("Mode scene")
        win.configure(bg=a2w.GUI_PANEL_BG)
        w = state["widgets"] = {}
        top = tk.Frame(win, bg=a2w.GUI_PANEL_BG)
        top.pack(fill="x", padx=10, pady=(8, 0))
        w["title"] = tk.Label(top, text="", font=MID, bg=a2w.GUI_PANEL_BG, fg=SCENE_COLOR)
        w["title"].pack(side="left")
        w["status"] = tk.Label(top, text="", font=("Segoe UI", 11), bg=a2w.GUI_PANEL_BG, fg=a2w.GUI_MUTED_FG, anchor="e")
        w["status"].pack(side="right", fill="x", expand=True)
        w["scenes"] = tk.Frame(win, bg=a2w.GUI_PANEL_BG)
        w["scenes"].pack(fill="both", expand=True, padx=8, pady=6)

        masters = tk.Frame(win, bg=a2w.GUI_PANEL_BG)
        masters.pack(fill="x", padx=10)
        for col, (key, label) in enumerate((("overlay_master", "Overlay"), ("bg_master", "Fond"))):
            box = tk.Frame(masters, bg=a2w.GUI_PANEL_BG)
            box.grid(row=0, column=col, sticky="ew", padx=6)
            masters.columnconfigure(col, weight=1, uniform="m")
            tk.Label(box, text=label, font=MID, bg=a2w.GUI_PANEL_BG, fg=a2w.GUI_FG).pack(side="left", padx=(0, 8))
            sc = tk.Scale(box, from_=0, to=100, orient="horizontal", showvalue=True, length=300, width=34, sliderlength=48, bg=a2w.GUI_PANEL_BG,
                          fg=a2w.GUI_FG, troughcolor=a2w.GUI_BG, highlightthickness=0, bd=0,
                          command=lambda v, k=key: params.__setitem__(k, float(v) / 100.0))
            sc.set(float(params[key]) * 100.0)
            sc.pack(side="left", fill="x", expand=True)
            sc.bind("<ButtonPress-1>", lambda e: state.__setitem__("holding", True))
            sc.bind("<ButtonRelease-1>", lambda e: state.__setitem__("holding", False))
            w[key] = sc

        bar = tk.Frame(win, bg=a2w.GUI_PANEL_BG)
        bar.pack(fill="x", padx=8, pady=8)

        def big(text, command, col, bg=None, fg=None, span=1):
            b = tk.Button(bar, text=text, command=command, font=BIG, relief="flat", bd=0, cursor="hand2", pady=18, bg=bg or a2w.GUI_BG,
                          fg=fg or a2w.GUI_FG, activebackground=SCENE_COLOR, activeforeground=BANNER_FG)
            b.grid(row=0, column=col, columnspan=span, sticky="nsew", padx=4)
            bar.columnconfigure(col, weight=2 if span > 1 else 1, uniform="b")
            return b
        big("◀ Retour", c.back_scene, 0)
        w["vj"] = big("▶ VJ", c.vj_toggle, 1)
        big("Suivante ▶▶", c.vj_advance, 2)
        w["lock"] = big("Verrou", c.toggle_scene_lock, 3)
        big("PANIQUE", c.do_panic, 4, bg=RED, fg="#ffffff")
        for evt, fn in (("<Escape>", close), ("<F11>", toggle_fullscreen), ("<space>", lambda e: c.vj_toggle()), ("x", lambda e: c.do_panic()),
                        ("X", lambda e: c.do_panic())):
            win.bind(evt, fn)
        win.protocol("WM_DELETE_WINDOW", close)
        win.geometry(f"{max(root.winfo_screenwidth() * 3 // 4, 900)}x{max(root.winfo_screenheight() * 3 // 4, 600)}+40+40")
        win.update_idletasks()
        state["sig"] = None
        tick()
        c.ui_every("stage", POLL_MS, tick)
        win.focus_force()

    return types.SimpleNamespace(open=open_stage, close=close, tick=tick, state=state, toggle_fullscreen=toggle_fullscreen)
