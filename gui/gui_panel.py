"""Panneau etendu du Mode VJ : le set en detail (une ligne par entree : media avec vignette, duree, participation au VJ, ordre), corps du Full Randomizer, reconstruction minimale et mise a jour sur place, poignee de hauteur.

Extrait de `gl_gui.build_window` : le code a ete coupe tel quel (memes noms, meme indentation). Les noms dont il a besoin
arrivent par le contexte `c` (`install(c)`), ceux que le reste de la fenetre utilise sont rendus par `install`.
"""
from __future__ import annotations

from core import randomizer
from core import scenes
from pathlib import Path
import json
import skin
import time
import tkinter as tk


def install(c):
    """Installe ce bloc dans la fenetre construite par `gl_gui.build_window` ; `c` donne les noms de la fenetre dont il depend."""
    from gl_gui import BANNER_FG, MIN_WIN_H, SCENE_COLOR, THUMB_H          # import tardif : gl_gui importe ce module
    Tooltip = c.Tooltip
    a2w = c.a2w
    a2w_clear = c.a2w_clear
    add_random_scene = c.add_random_scene
    apply_entry = c.apply_entry
    bg_clear = c.bg_clear
    current_index = c.current_index
    dnd = c.dnd
    duplicate_last_scene = c.duplicate_last_scene
    master_box = c.master_box
    media_clear = c.media_clear
    move_entry_now = c.move_entry_now
    panel_state = c.panel_state
    random_bank = c.random_bank
    random_draw_now = c.random_draw_now
    random_keep = c.random_keep
    refresh_scene_bar = c.refresh_scene_bar
    register_drop = c.register_drop
    register_tree = c.register_tree
    remove_entry_now = c.remove_entry_now
    rename_scene_dialog = c.rename_scene_dialog
    rnd = c.rnd
    root = c.root
    s = c.s
    scene_book = c.scene_book
    scene_panel = c.scene_panel
    scene_ui = c.scene_ui
    set_entry_vj = c.set_entry_vj
    set_random_option = c.set_random_option
    set_vj_option = c.set_vj_option
    thumb_photo = c.thumb_photo

    PANEL_BODY_H = 200
    PANEL_BODY_MIN = 80
    NAME_MIN, NAME_MAX = 14, 70                     # largeur (caracteres) du bouton du nom : la plus longue entree, bornee
    DUR_W = 13
    BTN_W = {"▲": 3, "▼": 3, "Renommer": 9, "Retirer": 8}
    BOLD = ("Segoe UI", 9, "bold")

    def row_bg(active: bool) -> str:
        return "#3b3320" if active else a2w.GUI_BG

    def card_bg(active: bool) -> str:
        return "#4a3f26" if active else a2w.GUI_PANEL_BG

    def dur_label(row: dict) -> str:
        return "duree " + ("du set" if row["seconds"] is None else f"{row['seconds']:.0f} s") + " \u25be"

    def build_panel_row(parent, row: dict, active: bool) -> dict:
        """Une ligne du panneau ; rend ses references (`frame`, `num`, `name`, `dur`, `cards`, `active`) pour la repeindre sur place."""
        bg = row_bg(active)
        i = row["index"]
        refs: dict = {"index": i, "cards": [], "active": active}
        fr = tk.Frame(parent, bg=bg)
        fr.pack(fill="x", pady=1, padx=2)
        refs["frame"] = fr
        refs["num"] = tk.Label(fr, text=str(i + 1), width=3, bg=bg, fg=SCENE_COLOR, font=BOLD)
        refs["num"].pack(side="left")
        shown = row["name"]                             # en entier (largeur commune a toutes les lignes : panel_state["name_w"])
        name_btn = tk.Button(fr, text=shown, command=lambda k=i: apply_entry(k), relief="flat", bd=0, cursor="hand2", font=BOLD,
                             bg=SCENE_COLOR if active else a2w.GUI_PANEL_BG, fg=BANNER_FG if active else a2w.GUI_FG,
                             activebackground=SCENE_COLOR, activeforeground=BANNER_FG)
        name_btn._skin_stretch = True                  # remplit la hauteur de la ligne ; meme largeur sur toutes les lignes
        name_btn.configure(width=panel_state["name_w"])
        name_btn.pack(side="left", fill="y", padx=4, pady=2)
        refs["name"] = name_btn
        scene = row["scene"]
        snap = scene.get("bg") == "live" and scene.get("mode") == "snap"
        own_a2w = row["a2w"] or {}
        scene_a2w = scenes.scene_a2w_media(scene)

        def zone(title: str, width: int, own: dict | None, inherited: dict | None, clear, target: str, enabled: bool = True) -> None:
            """Une zone de depot : media propre a l'entree (vif, avec sa croix), sinon celui que la scene a capture (en sourdine)."""
            cbg = card_bg(active)                                      # carte de la zone
            z = tk.Frame(fr, bg=cbg, width=width, height=THUMB_H + 16, highlightthickness=0)
            z.pack(side="left", padx=3)
            z.pack_propagate(False)
            refs["cards"].append(z)
            tk.Label(z, text=title, bg=cbg, fg=SCENE_COLOR if enabled else a2w.GUI_MUTED_FG, font=("Segoe UI", 7, "bold"),
                     anchor="w", padx=4).pack(fill="x")
            inner = tk.Frame(z, bg=cbg)
            inner.pack(fill="both", expand=True)
            shown = own or inherited
            if not enabled:
                tk.Label(inner, text="scene Snap seulement", bg=cbg, fg=a2w.GUI_MUTED_FG, anchor="w").pack(side="left")
            elif shown:
                missing = bool(scenes.a2w_missing({"v": shown["logo_video"]})) if target != "overlay" else scenes.media_missing(shown)
                if own:
                    tk.Button(inner, text="\u2715", command=clear, relief="flat", bd=0, cursor="hand2", bg=cbg,
                              fg=a2w.GUI_MUTED_FG).pack(side="right")
                photo = thumb_photo(shown)
                thumb = tk.Label(inner, image=photo, bg=cbg) if photo else tk.Label(
                    inner, text="T" if shown.get("logo_source") == "text" else "\u25a0", width=5, bg=a2w.GUI_BG, fg=a2w.GUI_MUTED_FG)
                thumb.image = photo
                thumb.pack(side="left")
                label = scenes.media_label(shown) + ("  (introuvable)" if missing else "")
                tk.Label(inner, text=label if len(label) <= 22 else label[:21] + ".", anchor="w", bg=cbg,
                         fg="#ff8a8a" if missing else (a2w.GUI_FG if own else a2w.GUI_MUTED_FG)).pack(side="left", padx=4)
            else:
                tk.Label(inner, text="glisse un fichier ici", bg=cbg, fg=a2w.GUI_MUTED_FG, anchor="w").pack(side="left")
            if enabled:
                register_tree(z, i, target)

        def as_video(path: str | None) -> dict | None:
            return {"logo_source": "video", "logo_video": path} if path else None
        media = row["media"]
        own_media = scenes.capture_media(scene.get("overlay") or {})
        if own_media.get("logo_source", "image") == "image" and not own_media.get("logo_path"):
            own_media = None                               # la scene n'a pas de logo
        zone("OVERLAY", 215, media, own_media, lambda k=i: media_clear(k), "overlay")
        zone("FOND  video", 165, as_video(row.get("bg_video")), as_video(scenes.scene_bg_video(scene)),
             lambda k=i: bg_clear(k), "bgvideo")
        zone("AUDIO2WAVE  video int.", 165, as_video(own_a2w.get("video")), as_video(scene_a2w.get("video")),
             lambda k=i: a2w_clear(k, "video"), "video", snap)
        zone("AUDIO2WAVE  video ext.", 165, as_video(own_a2w.get("video2")), as_video(scene_a2w.get("video2")),
             lambda k=i: a2w_clear(k, "video2"), "video2", snap)
        dur = tk.Menubutton(fr, text=dur_label(row), relief="flat", bd=0, padx=6, cursor="hand2", bg=a2w.GUI_PANEL_BG,
                            fg=a2w.GUI_MUTED_FG, activebackground=a2w.GUI_BG, activeforeground=a2w.GUI_FG)
        dur.configure(width=DUR_W)                       # colonnes alignees : meme largeur sur toutes les lignes
        dur.pack(side="left", fill="y", padx=4, pady=2)
        refs["dur"] = dur
        dmenu = tk.Menu(dur, tearoff=0)
        dmenu.add_command(label="Duree du set", command=lambda k=i: set_entry_vj(k, seconds=None))
        for value in scenes.VJ_SECONDS:
            dmenu.add_command(label=f"{value} s" if value < 60 else f"{value // 60} min",
                              command=lambda k=i, v=value: set_entry_vj(k, seconds=float(v)))
        dur["menu"] = dmenu
        var = tk.IntVar(value=0 if row["skip"] else 1)
        cb = tk.Checkbutton(fr, text="VJ", variable=var, bg=bg, fg=a2w.GUI_FG, selectcolor=a2w.GUI_PANEL_BG, bd=0,
                            activebackground=bg, activeforeground=a2w.GUI_FG,
                            command=lambda k=i, v=var: set_entry_vj(k, skip=not v.get()))
        cb.var = var
        cb.pack(side="left", padx=4)
        Tooltip(cb, "Coche : le VJ joue cette entree. Decoche : elle reste chargeable a la main (clic, F-touche) mais le VJ la saute.")
        n = len(scene_book.rows())
        for text, cmd, ok in (("\u25b2", lambda k=i: move_entry_now(k, -1), i > 0),
                              ("\u25bc", lambda k=i: move_entry_now(k, 1), i < n - 1),
                              ("Renommer", lambda nm=row["name"]: rename_scene_dialog(nm), True),
                              ("Retirer", lambda k=i: remove_entry_now(k), True)):
            b = tk.Button(fr, text=text, command=cmd, state="normal" if ok else "disabled", relief="flat", bd=0, padx=6, width=BTN_W[text],
                          cursor="hand2", bg=a2w.GUI_PANEL_BG, fg=a2w.GUI_FG, disabledforeground=a2w.GUI_MUTED_FG,
                          activebackground=SCENE_COLOR, activeforeground=BANNER_FG)
            b._skin_stretch = True                         # meme hauteur que la ligne (et que le bouton du nom)
            b.pack(side="left", fill="y", padx=1, pady=2)
        register_drop(fr, i)
        register_drop(name_btn, i)
        return refs

    def restyle_row(refs: dict, active: bool) -> None:
        """Repeint une ligne quand seule la scene active change : fond de ligne, cartes, bouton du nom (aucun widget reconstruit)."""
        if refs["active"] == active:
            return
        old_card, new_card = card_bg(refs["active"]).lower(), card_bg(active)
        bg = row_bg(active)
        refs["active"] = active
        try:
            refs["frame"].configure(bg=bg)
            refs["num"].configure(bg=bg)
            for w in refs["frame"].winfo_children():
                if w is refs["name"]:
                    w.configure(bg=SCENE_COLOR if active else a2w.GUI_PANEL_BG, fg=BANNER_FG if active else a2w.GUI_FG)
                    skin.retint(w, bg)
                elif w in refs["cards"]:
                    stack = [w]
                    while stack:
                        node = stack.pop()
                        cls = node.winfo_class()
                        if cls in ("Frame", "Label") and str(node.cget("bg")).lower() == old_card:
                            node.configure(bg=new_card)
                        elif cls == "Button":
                            node.configure(bg=new_card)
                            skin.retint(node, new_card)
                        stack.extend(node.winfo_children())
                elif w.winfo_class() in ("Button", "Checkbutton", "Menubutton"):
                    skin.retint(w, bg)
        except tk.TclError:
            pass                                           # widget detruit entre-temps (reconstruction du panneau)

    def build_random_body(inner, settings: dict) -> None:
        """Corps du panneau etendu en ordre « Full Randomizer » : banques d'assets, sources du fond et de l'overlay, verrous."""
        rs = settings["random"]
        bg = a2w.GUI_BG
        small = ("Segoe UI", 8, "bold")

        def line(title: str) -> tk.Frame:
            fr = tk.Frame(inner, bg=bg)
            fr.pack(fill="x", padx=8, pady=3)
            tk.Label(fr, text=title, width=15, anchor="w", bg=bg, fg=SCENE_COLOR, font=small).pack(side="left")
            return fr

        def check(parent, text: str, checked: bool, command, tip: str = "") -> tk.Checkbutton:
            var = tk.IntVar(value=int(checked))
            cb = tk.Checkbutton(parent, text=text, variable=var, command=lambda: command(bool(var.get())), bg=bg, fg=a2w.GUI_FG,
                                selectcolor=a2w.GUI_PANEL_BG, bd=0, activebackground=bg, activeforeground=a2w.GUI_FG)
            cb.var = var
            cb.pack(side="left", padx=(0, 10))
            if tip:
                Tooltip(cb, tip)
            return cb

        def member(key: str, value: str):
            def toggle(on: bool) -> None:
                items = [x for x in scene_book.vj()["random"][key] if x != value]
                set_random_option(**{key: items + [value] if on else items})
            return toggle

        # ---- banques : FOND (videos plein cadre) et OVERLAY (images, animations, videos), autant de dossiers que l'on veut.
        # C'est le dossier qui classe, jamais l'extension (un .mov peut etre une animation comme une video).
        banks = random_bank()

        def bank_block(which: str, title: str, tip: str) -> None:
            bank = banks[which]
            fkey, ckey = f"folders_{which}", f"categories_{which}"
            fr = line(title)
            for folder in rs[fkey]:
                box = tk.Frame(fr, bg=a2w.GUI_PANEL_BG)
                box.pack(side="left", padx=(0, 6))
                ok = Path(folder).is_dir()
                tk.Label(box, text=("" if ok else "\u26a0 ") + (folder if len(folder) <= 30 else "..." + folder[-27:]),
                         bg=a2w.GUI_PANEL_BG, fg=a2w.GUI_FG if ok else "#ff8a8a", padx=6).pack(side="left")
                tk.Button(box, text="\u2715", command=lambda f=folder: set_random_option(
                    refresh=True, **{fkey: [x for x in scene_book.vj()["random"][fkey] if x != f]}),
                    relief="flat", bd=0, cursor="hand2", bg=a2w.GUI_PANEL_BG, fg=a2w.GUI_MUTED_FG).pack(side="left")

            def add_folder() -> None:
                folder = scene_ui["ask_dir"]()
                if folder and folder not in scene_book.vj()["random"][fkey]:
                    set_random_option(refresh=True, **{fkey: scene_book.vj()["random"][fkey] + [folder]})

            add_btn = tk.Button(fr, text="+ Dossier", command=add_folder, relief="flat", bd=0, padx=8, cursor="hand2",
                                bg=a2w.GUI_PANEL_BG, fg=SCENE_COLOR)
            add_btn.pack(side="left", padx=2)
            Tooltip(add_btn, tip)
            n_img = sum(1 for a in bank if a.kind == "image")
            if which == "fond":
                count = f"{len(bank)} video(s)"
            else:
                count = f"{n_img} image(s), {len(bank) - n_img} video(s) / animation(s)"
            tk.Label(fr, text=count if bank else ("vide : ajoute un dossier" if not rs[fkey] else "aucun fichier trouve"),
                     bg=bg, fg=a2w.GUI_MUTED_FG).pack(side="left", padx=10)
            if which == "overlay":
                scan_btn = tk.Button(fr, text="Rescanner", command=lambda: (random_bank(True), refresh_scene_panel(force=True)), relief="flat",
                                     bd=0, padx=8, cursor="hand2", bg=a2w.GUI_PANEL_BG, fg=a2w.GUI_FG)
                scan_btn.pack(side="left", padx=2)
                Tooltip(scan_btn, "Relit les dossiers des deux banques (nouveaux fichiers ajoutes depuis).")
            cats = randomizer.categories(bank)
            if len(cats) > 1:
                cfr = line("  categories")
                chosen = set(rs[ckey] or cats)
                for cat in cats:
                    def toggle_cat(on: bool, cat=cat, ckey=ckey, cats=cats) -> None:
                        now = set(scene_book.vj()["random"][ckey] or cats)
                        now = (now | {cat}) if on else (now - {cat})
                        set_random_option(**{ckey: [] if now >= set(cats) else sorted(now)})
                    check(cfr, f"{cat} ({sum(1 for a in bank if a.category == cat)})", cat in chosen, toggle_cat,
                          "Les categories (sous-dossiers) de cette banque dans lesquelles piocher.")

        bank_block("fond", "BANQUE FOND", "Dossiers de VIDEOS plein cadre : elles servent de fond. Pour qu'une video serve aussi de logo, "
                                          "declare son dossier dans la banque OVERLAY.")
        bank_block("overlay", "BANQUE OVERLAY", "Dossiers d'images, d'animations et de videos : ils servent de logo (le dossier decide, "
                                                "pas l'extension : un .mov peut etre une animation).")
        # ---- fond
        fr = line("FOND")
        for key, label, tip in (("preset", "preset de fond", "Un preset de fond genere (section PRESETS FOND)."),
                                ("a2w", "preset audio2wave", "Un preset du mode audio2wave courant (Live / Snap / Ridge) : le mode ne change pas."),
                                ("video", "video de la banque FOND", "Une video de la banque FOND, en fond video."),
                                ("params", "config aleatoire", "Un fond entierement nouveau : soit un fond genere aux reglages tires au hasard (palette, couleurs, "
                                                               "teinte, angle, vitesse...), soit un visuel audio2wave dans un mode et un preset de ce mode "
                                                               "tires au hasard (modes ci-dessous).")):
            check(fr, label, key in rs["bg_sources"], member("bg_sources", key), tip)
        fr = line("  modes a2w")
        for key, label in (("live", "Live"), ("snap", "Snap"), ("ridge", "Ridge")):
            check(fr, label, key in rs["modes"], member("modes", key),
                  "Modes audio2wave que la config aleatoire du fond peut tirer. Changer de mode reconstruit la fenetre (environ 1 s, "
                  "masque en partie par le flash) ; Ridge demande une entree audio.")
        # ---- overlay
        fr = line("OVERLAY")
        for key, label, tip in (("overlay_preset", "preset overlay", "Un des presets overlay enregistres."),
                                ("params", "config aleatoire", "Une configuration entierement nouvelle : logo (taille, position, contour), effets, halo / "
                                                               "fonte / cellules et noise tires au hasard, sans partir d'un preset."),
                                ("fx", "effets du logo", "Halo, fonte, cellules, noise et effets allumes au hasard (au plus deux effets couteux)."),
                                ("media", "media de la banque OVERLAY", "Une image, animation ou video de la banque OVERLAY comme logo."),
                                ("text", "texte", "Le texte ci-contre comme logo (couleur tiree au hasard), au meme titre qu'un fichier de la banque.")):
            check(fr, label, key in rs["overlay"], member("overlay", key), tip)
        text_var = tk.StringVar(value=rs["text"])
        text_entry = tk.Entry(fr, textvariable=text_var, width=24)
        text_entry.pack(side="left", padx=(0, 6))
        Tooltip(text_entry, "Texte tire comme logo (defaut CASUAL RAVERS). Entree ou changer de champ pour valider ; vide = jamais de texte.")
        for ev in ("<Return>", "<FocusOut>"):
            text_entry.bind(ev, lambda e: set_random_option(text=text_var.get()) if text_var.get().strip()[:randomizer.MAX_TEXT] != scene_book.vj()["random"]["text"] else None)
        # ---- verrous
        fr = line("VERROUS")
        for key, label, tip in (("lock_bg", "fond", "Le fond reste celui du moment pendant que l'overlay varie."),
                                ("lock_media", "media", "Le logo / texte / video reste celui du moment."),
                                ("lock_fx", "effets", "Les effets et le preset overlay restent ceux du moment.")):
            check(fr, label, bool(rs[key]), lambda on, key=key: set_random_option(**{key: on}), tip)
        # ---- actions
        fr = line("TIRAGE")
        draw_btn = tk.Button(fr, text="Tirer maintenant", command=random_draw_now, relief="flat", bd=0, padx=10, cursor="hand2",
                             bg=SCENE_COLOR, fg=BANNER_FG, font=BOLD)
        draw_btn.pack(side="left", padx=(0, 6))
        Tooltip(draw_btn, "Tire et applique une scene tout de suite (avec le flash).")
        keep_btn = tk.Button(fr, text="Garder ce tirage...", command=random_keep, relief="flat", bd=0, padx=10, cursor="hand2",
                             bg=a2w.GUI_PANEL_BG, fg=a2w.GUI_FG)
        keep_btn.pack(side="left", padx=2)
        Tooltip(keep_btn, "Enregistre le dernier tirage : une fenetre propose les noms du preset de fond, du preset overlay et de la "
                          "scene (champ vide = pas enregistre).")
        tk.Label(fr, text=rnd["info"], bg=bg, fg=a2w.GUI_MUTED_FG, anchor="w", justify="left", wraplength=760).pack(
            side="left", padx=10, fill="x")


    def panel_summary(rows: list[dict], settings: dict) -> str:
        played = [r for r in rows if not r["skip"]]
        secs = settings["seconds"]
        loop = sum(r["seconds"] or secs for r in played)
        if settings["order"] == "alea":
            return f"Full Randomizer : un tirage toutes les {secs:.0f} s"
        return (f"{len(played)}/{len(rows)} scenes dans le VJ, boucle ~ "
                + (f"{loop / 60:.1f} min" if loop >= 90 else f"{loop:.0f} s"))

    def panel_signature(rows: list[dict], settings: dict) -> str:
        """Tout ce qui, hors la scene active, change ce que le panneau affiche : s'il est identique, on ne reconstruit rien."""
        return json.dumps([settings, rows, rnd["info"] if settings["order"] == "alea" else "",
                           [len(b) for b in rnd["bank"].values()], sum(1 for v in getattr(s, "thumb_cache", {}).values() if v != "pending"), dnd["ok"],
                           scene_book.active_set_name()], sort_keys=True, default=str)

    def refresh_scene_panel(force: bool = False) -> None:
        """Construit / met a jour le panneau etendu. Rien n'est reconstruit si seul l'ordre d'affichage actif a change (la ligne
        active est alors repeinte sur place) ; une reconstruction garde la position de l'ascenseur et rehabille les widgets."""
        if not getattr(s, "scenes_expanded", False):
            for child in scene_panel.winfo_children():
                child.destroy()
            scene_panel.pack_forget()
            panel_state.update(sig=None, rows={}, cv=None, summary=None, dur={})
            return
        rows = scene_book.rows()
        settings = scene_book.vj()
        current = current_index(rows)
        sig = panel_signature(rows, settings)
        if not force and sig == panel_state["sig"] and scene_panel.winfo_children():
            for refs in panel_state["rows"].values():
                restyle_row(refs, refs["index"] == current)
            return
        from tkinter import font as tkfont
        face = tkfont.Font(root=root, font=BOLD)
        longest = max([face.measure(r["name"]) for r in rows] + [0])
        panel_state["name_w"] = max(NAME_MIN, min(NAME_MAX, -(-longest // max(face.measure("0"), 1)) + 2))
        frac = 0.0
        cv_old = panel_state["cv"]
        if cv_old is not None:
            try:
                frac = cv_old.yview()[0]
            except tk.TclError:
                frac = 0.0
        for child in scene_panel.winfo_children():
            child.destroy()
        panel_state.update(sig=sig, rows={}, cv=None, summary=None, dur={})
        scene_panel.pack(fill="x")
        head = tk.Frame(scene_panel, bg=a2w.GUI_PANEL_BG)
        head.pack(fill="x")
        tk.Label(head, text=f" SET  {scene_book.active_set_name()}", bg=a2w.GUI_PANEL_BG, fg=SCENE_COLOR, font=BOLD).pack(
            side="left", padx=(6, 12), pady=4)
        order = tk.Menubutton(head, text="ordre : " + {"seq": "dans l'ordre", "random": "au hasard", "alea": "Full Randomizer"}[settings["order"]] + " \u25be",
                              relief="flat", bd=0, padx=8, cursor="hand2", bg=a2w.GUI_BG, fg=a2w.GUI_FG,
                              activebackground=a2w.GUI_PANEL_BG, activeforeground=a2w.GUI_FG)
        order.pack(side="left", padx=2)
        om = tk.Menu(order, tearoff=0)
        om.add_command(label="Dans l'ordre", command=lambda: set_vj_option(order="seq"))
        om.add_command(label="Au hasard (jamais la meme deux fois)", command=lambda: set_vj_option(order="random"))
        om.add_command(label="Full Randomizer (fond, overlay et media tires a chaque fois)", command=lambda: set_vj_option(order="alea"))
        order["menu"] = om
        secs = settings["seconds"]
        default = tk.Menubutton(head, text=f"duree par defaut : {secs:.0f} s \u25be", relief="flat", bd=0, padx=8, cursor="hand2",
                                bg=a2w.GUI_BG, fg=a2w.GUI_FG, activebackground=a2w.GUI_PANEL_BG, activeforeground=a2w.GUI_FG)
        default.pack(side="left", padx=2)
        dm = tk.Menu(default, tearoff=0)
        for value in scenes.VJ_SECONDS:
            dm.add_command(label=f"{value} s" if value < 60 else f"{value // 60} min",
                           command=lambda v=value: set_vj_option(seconds=float(v)))
        default["menu"] = dm
        summary = tk.StringVar(value=panel_summary(rows, settings))
        panel_state["summary"] = summary
        tk.Label(head, textvariable=summary, bg=a2w.GUI_PANEL_BG, fg=a2w.GUI_MUTED_FG).pack(side="left", padx=10)
        tk.Label(head, text="glisser-deposer d'un fichier sur une zone (overlay / audio2wave) = lier ce media"
                 if dnd["ok"] else "glisser-deposer indisponible (pip install tkinterdnd2)", bg=a2w.GUI_PANEL_BG,
                 fg=a2w.GUI_MUTED_FG).pack(side="right", padx=10)
        wrap = tk.Frame(scene_panel, bg=a2w.GUI_BG)                 # la liste a gauche, l'espace de controle global a droite
        wrap.pack(fill="x")
        mbox = master_box()
        mbox.pack(in_=wrap, side="right", fill="y")
        mbox.lift()                                                 # un esclave place « dans » un autre doit le surplomber
        body = tk.Frame(wrap, bg=a2w.GUI_BG, height=getattr(s, "panel_body_h", PANEL_BODY_H))
        body.pack(side="left", fill="both", expand=True)
        body.pack_propagate(False)
        foot = tk.Frame(scene_panel, bg=a2w.GUI_PANEL_BG)           # sous la liste : ajouter a la suite
        foot.pack(fill="x")
        plus = tk.Button(foot, text="+", command=add_random_scene, relief="flat", bd=0, padx=14, cursor="hand2", bg=SCENE_COLOR,
                         fg=BANNER_FG, font=BOLD, activebackground=SCENE_COLOR, activeforeground=BANNER_FG)
        plus.pack(side="left", padx=(8, 4), pady=3)
        Tooltip(plus, "Ajoute a la suite du set une scene tiree au hasard (reglages du Full Randomizer), sous un nom aleatoire. "
                      "Aucun preset n'est enregistre et la scene n'est pas appliquee.")
        dup = tk.Button(foot, text="Dupliquer la derniere", command=duplicate_last_scene, relief="flat", bd=0, padx=10, cursor="hand2",
                        bg=a2w.GUI_BG, fg=a2w.GUI_FG)
        dup.pack(side="left", padx=4, pady=3)
        Tooltip(dup, "Ajoute a la suite une copie de la derniere scene (meme fond, overlay, medias, duree) puis propose de la renommer. "
                     "Change ensuite ses medias en glissant un fichier sur sa ligne, sa duree ou sa case VJ.")
        grip = tk.Label(scene_panel, text="⋯", bg=a2w.GUI_PANEL_BG, fg=a2w.GUI_MUTED_FG, cursor="sb_v_double_arrow", pady=0)
        grip.pack(fill="x")
        Tooltip(grip, "Glisse vers le haut ou le bas pour agrandir ou reduire la liste (la fenetre suit).")
        drag: dict = {}

        def grip_press(e) -> None:
            drag.update(y=e.y_root, h=int(body.cget("height")))

        def grip_move(e) -> None:
            if not drag:
                return
            want = max(PANEL_BODY_MIN, drag["h"] + e.y_root - drag["y"])
            old = int(body.cget("height"))
            delta = want - old
            want = min(want, max(root.winfo_screenheight() - 200, PANEL_BODY_MIN))
            delta = want - old
            if delta == 0:
                return
            # la fenetre suit tant qu'elle tient dans l'ecran ; au-dela la liste grandit quand meme (la page du dessous se reduit,
            # elle defile) et ce « trop-plein » est rendu en premier quand on reduit
            overflow = getattr(s, "panel_overflow", 0)
            if delta > 0:
                win = min(delta, max(root.winfo_screenheight() - 80 - root.winfo_height(), 0))
                overflow += delta - win
            else:
                take = min(overflow, -delta)
                overflow -= take
                win = delta + take
            s.panel_overflow = overflow
            body.configure(height=old + delta)
            s.panel_body_h = old + delta
            if win:
                root.geometry(f"{root.winfo_width()}x{max(MIN_WIN_H, root.winfo_height() + win)}")
                s.scene_panel_h = getattr(s, "scene_panel_h", 0) + win       # a la fermeture, la fenetre rend exactement ce qu'elle a pris

        grip.bind("<ButtonPress-1>", grip_press)
        grip.bind("<B1-Motion>", grip_move)
        grip.bind("<ButtonRelease-1>", lambda e: drag.clear())
        bar = skin.scrollbar(body)
        hbar = skin.scrollbar(body, orient="horizontal")             # seulement si un nom tres long depasse la largeur
        cv = tk.Canvas(body, highlightthickness=0, bd=0, bg=a2w.GUI_BG, yscrollcommand=bar.set, xscrollcommand=hbar.set)
        bar.config(command=cv.yview)
        hbar.config(command=cv.xview)
        bar.pack(side="right", fill="y")
        cv.pack(side="left", fill="both", expand=True)
        inner = tk.Frame(cv, bg=a2w.GUI_BG)
        item = cv.create_window((0, 0), window=inner, anchor="nw")

        def fit_width(_e=None) -> None:
            need = inner.winfo_reqwidth()
            cv.itemconfigure(item, width=max(cv.winfo_width(), need))
            if need > cv.winfo_width() + 1:
                if not hbar.winfo_ismapped():
                    hbar.pack(side="bottom", fill="x", before=cv)
            else:
                if hbar.winfo_ismapped():
                    hbar.pack_forget()
                cv.xview_moveto(0)
        cv.bind("<Configure>", fit_width)
        restore = {"until": time.monotonic() + 0.5 if frac else 0.0}

        def on_inner(_e=None) -> None:
            fit_width()
            cv.configure(scrollregion=cv.bbox("all"))
            if time.monotonic() < restore["until"]:        # la mise en page se fait en plusieurs passes : on remet la position a chacune
                cv.yview_moveto(frac)
        inner.bind("<Configure>", on_inner)
        panel_state["cv"] = cv
        if settings["order"] == "alea":
            build_random_body(inner, settings)
        else:
            if not rows:
                tk.Label(inner, text="Ce set est vide : '+ Scene' pour en ajouter.", bg=a2w.GUI_BG, fg=a2w.GUI_MUTED_FG).pack(pady=20)
            for row in rows:
                refs = build_panel_row(inner, row, row["index"] == current)
                panel_state["rows"][row["index"]] = refs
                panel_state["dur"][row["index"]] = refs["dur"]
        skin.apply(scene_panel)                    # TOUJOURS : un panneau reconstruit sans habillage (vignettes pretes, Rescanner...) laissait les cases brutes

    def panel_update_in_place(index: int, kw: dict) -> bool:
        """Duree propre ou case VJ d'une entree : on met a jour le resume et le bouton de duree, sans reconstruire le panneau."""
        summary, btn = panel_state["summary"], panel_state["dur"].get(index)
        if summary is None or not getattr(s, "scenes_expanded", False) or btn is None:
            return False
        rows = scene_book.rows()
        if not 0 <= index < len(rows):
            return False
        summary.set(panel_summary(rows, scene_book.vj()))
        if "seconds" in kw:
            btn.configure(text=dur_label(rows[index]))
        panel_state["sig"] = panel_signature(rows, scene_book.vj())
        return True

    def set_expanded(flag: bool) -> None:
        """Ouvre / ferme le panneau etendu : la fenetre grandit (ou reprend sa taille) de la hauteur du panneau."""
        if bool(getattr(s, "scenes_expanded", False)) == bool(flag):
            return
        s.scenes_expanded = bool(flag)
        s.panel_overflow = 0
        refresh_scene_bar()
        if root.winfo_ismapped():
            root.update_idletasks()
            if flag:
                s.scene_panel_h = scene_panel.winfo_reqheight()
            delta = getattr(s, "scene_panel_h", 0) * (1 if flag else -1)
            height = max(MIN_WIN_H, min(root.winfo_height() + delta, root.winfo_screenheight() - 80))
            root.geometry(f"{root.winfo_width()}x{height}")

    import types
    return types.SimpleNamespace(panel_update_in_place=panel_update_in_place, refresh_scene_panel=refresh_scene_panel, set_expanded=set_expanded)
