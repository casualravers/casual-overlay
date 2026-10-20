"""Palette de commandes (Ctrl+K) : taper quelques lettres, choisir, Entree. Scenes, sets, presets overlay / fond et les grosses commandes du VJ.

Module de la fenetre de reglages (comme les `gui_*.py`) : `install(c)` recoit les fonctions de la fenetre par le contexte `c`. Le classement est dans
`core/palette.py` (pur). La liste est refaite a chaque ouverture : un preset ou une scene ajoutes a l'instant y sont.
"""
from __future__ import annotations

import time
import tkinter as tk
import types

from core import palette

MAX_SHOWN = 12


def install(c):
    """Installe la palette dans la fenetre construite par `gl_gui.build_window` ; rend `open`, `items`, `choose`."""
    import audio2wave_gl as gl
    from gl_gui import SCENE_COLOR, BANNER_FG
    a2w = c.a2w
    root = c.root
    scene_book = c.scene_book
    scene_msg = c.scene_msg
    state: dict = {"win": None}

    def strobe_flash() -> None:
        gl.strobe_press(c.params)
        c.root.after(90, lambda: gl.strobe_release(c.params))

    def items() -> list[tuple[str, str, object]]:
        """[(libelle, mots-cles, action)] : les actions fixes d'abord, puis scenes du set actif, sets, presets overlay et fond."""
        out: list[tuple[str, str, object]] = [
            ("Panique", "stop couper effets", c.do_panic),
            ("Verrou de scene : verrouiller / deverrouiller", "lock geler", c.toggle_scene_lock),
            ("VJ : lecture / pause", "play enchainer", c.vj_toggle),
            ("Scene suivante", "next vj", c.vj_advance),
            ("Scene precedente", "retour back", c.back_scene),
            ("Montee", "build", c.start_build),
            ("Drop", "lacher flash", c.do_drop),
            ("Strobe : un eclair", "flash blanc", strobe_flash),
            ("Tap tempo", "bpm rythme", c.do_tap),
            ("Temps 1 : le temps qui tombe est le premier", "grille mesure phase", c.do_one),
            ("Panneau etendu : ouvrir / fermer", "vj liste scenes etendre reduire", lambda: c.set_expanded(not getattr(c.s, "scenes_expanded", False))),
            ("Console VJ", "grosses commandes", c.console_window),
            ("Aide : raccourcis clavier", "help ?", c.help_window),
            ("Projection : demarrer / fermer", "ecran fenetre gl", c.toggle_projection),
        ]
        rows = scene_book.rows()
        for r in rows:
            out.append((f"Scene : {r['name']}", f"scene f{r['index'] + 1}", lambda i=r["index"]: c.apply_entry(i)))
        for name in scene_book.set_names():
            out.append((f"Set : {name}", "set", lambda n=name: c.switch_set(n)))
        for label, bar in (("overlay", c.overlay_presets), ("fond", c.bg_presets)):
            for name in bar["book"].names():
                out.append((f"Preset {label} : {name}", f"preset {label}", lambda n=name, b=bar: b["load"](n)))
        return out

    def matches(query: str) -> list[tuple[str, str, object]]:
        all_items = items()
        order = palette.rank(query, [(lab, kw) for lab, kw, _fn in all_items])
        return [all_items[i] for i in order]

    def choose(query: str, run: bool = True) -> str | None:
        """Premiere commande qui correspond a `query` (et l'execute) ; None si rien ne correspond. Sert aux tests et aux scripts."""
        found = matches(query)
        if not found:
            return None
        label, _kw, fn = found[0]
        if run:
            fn()
        return label

    def close(_evt=None) -> None:
        win = state.get("win")
        state["win"] = None
        if win is not None:
            try:
                win.destroy()
            except tk.TclError:
                pass

    def open_palette(_evt=None) -> str:
        old = state.get("win")
        if old is not None:
            try:
                if old.winfo_exists():
                    old.lift()
                    return "break"
            except tk.TclError:
                pass
        win = state["win"] = tk.Toplevel(root)
        win.title("Palette de commandes")
        win.configure(bg=a2w.GUI_PANEL_BG)
        win.transient(root)
        win.attributes("-topmost", True)
        var = tk.StringVar()
        entry = tk.Entry(win, textvariable=var, width=46, font=("Segoe UI", 13))
        entry.pack(fill="x", padx=10, pady=(10, 4))
        lb = tk.Listbox(win, height=MAX_SHOWN, activestyle="none", exportselection=False, bg=a2w.GUI_BG, fg=a2w.GUI_FG, bd=0, highlightthickness=0,
                        selectbackground=SCENE_COLOR, selectforeground=BANNER_FG, font=("Segoe UI", 11))
        lb.pack(fill="both", expand=True, padx=10, pady=(0, 4))
        hint = tk.Label(win, text="↑ ↓ choisir  ·  Entree lancer  ·  Echap fermer", bg=a2w.GUI_PANEL_BG, fg=a2w.GUI_MUTED_FG)
        hint.pack(anchor="w", padx=10, pady=(0, 8))
        shown: list = []

        def refill(*_a) -> None:
            shown[:] = matches(var.get())[:MAX_SHOWN]
            lb.delete(0, "end")
            for label, _kw, _fn in shown:
                lb.insert("end", label)
            if shown:
                lb.selection_set(0)

        def move(delta: int) -> str:
            if not shown:
                return "break"
            cur = lb.curselection()
            i = min(max((cur[0] if cur else 0) + delta, 0), len(shown) - 1)
            lb.selection_clear(0, "end")
            lb.selection_set(i)
            lb.see(i)
            return "break"

        def run(_evt=None) -> str:
            cur = lb.curselection()
            if shown and cur:
                _label, _kw, fn = shown[cur[0]]
                close()
                try:
                    fn()
                except Exception as exc:          # une commande qui echoue ne doit pas laisser la palette ouverte ni tuer la fenetre
                    scene_msg.set(f"Commande impossible: {exc}")
            return "break"

        var.trace_add("write", refill)
        entry.bind("<Down>", lambda e: move(1))
        entry.bind("<Up>", lambda e: move(-1))
        entry.bind("<Return>", run)
        entry.bind("<Escape>", close)
        lb.bind("<Double-Button-1>", run)
        win.bind("<Escape>", close)
        win.protocol("WM_DELETE_WINDOW", close)
        refill()
        win.update_idletasks()
        x = root.winfo_rootx() + max((root.winfo_width() - win.winfo_reqwidth()) // 2, 0)
        win.geometry(f"+{x}+{root.winfo_rooty() + 80}")
        entry.focus_force()
        return "break"

    root.bind_all("<Control-k>", open_palette)
    return types.SimpleNamespace(open=open_palette, items=items, matches=matches, choose=choose, close=close, state=state)
