"""Full Randomizer cote GUI : banques d'assets, tirage d'une scene temporaire, ajout d'une scene aleatoire, duplication, Garder ce tirage (dialogue et enregistrement).

Extrait de `gl_gui.build_window` : le code a ete coupe tel quel (memes noms, meme indentation). Les noms dont il a besoin
arrivent par le contexte `c` (`install(c)`), ceux que le reste de la fenetre utilise sont rendus par `install`.
"""
from __future__ import annotations

from core import randomizer
from core import scenes
import audio2wave_gl as gl
import json
import random
import tkinter as tk


def install(c):
    """Installe ce bloc dans la fenetre construite par `gl_gui.build_window` ; `c` donne les noms de la fenetre dont il depend."""
    from gl_gui import mode_store          # import tardif : gl_gui importe ce module
    after = c.after
    apply_scene_dict = c.apply_scene_dict
    bg_presets = c.bg_presets
    capture_live_overrides = c.capture_live_overrides
    current_index = c.current_index
    live = c.live
    overlay_presets = c.overlay_presets
    panel_state = c.panel_state
    params = c.params
    refresh_scene_bar = c.refresh_scene_bar
    rename_scene_dialog = c.rename_scene_dialog
    s = c.s
    save_live_preset = c.save_live_preset
    scene_book = c.scene_book
    scene_locked = c.scene_locked
    scene_msg = c.scene_msg
    scene_ui = c.scene_ui
    sync_automation_now = c.sync_automation_now
    vj = c.vj
    vj_arm = c.vj_arm

    rnd = getattr(s, "rnd", None)
    if rnd is None:
        rnd = s.rnd = {"last": {}, "scene": None, "info": "", "bank": {"fond": [], "overlay": []}, "bank_key": None,
                       "rng": random.Random()}

    def random_bank(force: bool = False) -> dict:
        """Les deux banques d'assets du set, `{"fond": [...], "overlay": [...]}` (rescannees seulement si les dossiers changent
        ou si `force`)."""
        rs = scene_book.vj()["random"]
        key = (tuple(rs["folders_fond"]), tuple(rs["folders_overlay"]))
        if force or rnd["bank_key"] != key:
            rnd["bank"] = {"fond": randomizer.scan_bank(list(key[0]), "fond"), "overlay": randomizer.scan_bank(list(key[1]), "overlay")}
            rnd["bank_key"] = key
        return rnd["bank"]

    def random_scene() -> tuple[dict, str, dict]:
        """Tire une scene (sans l'appliquer) avec les reglages du hasard du set actif."""
        sync_automation_now()
        settings = scene_book.vj()["random"]
        try:
            a2w_presets = mode_store(live, s.mode).all()
        except Exception:
            a2w_presets = {}
        a2w_modes: dict = {}
        if "params" in settings["bg_sources"]:               # config aleatoire : tous les modes autorises (charges seulement ici)
            for mode in settings["modes"]:
                try:
                    a2w_modes[mode] = a2w_presets if mode == s.mode else mode_store(live, mode).all()
                except Exception:
                    a2w_modes[mode] = {}
        banks = random_bank()
        ctx = randomizer.Context(bank_fond=banks["fond"], bank_overlay=banks["overlay"], bg_presets=gl.BACKGROUND_BOOK.all(),
                                 overlay_presets=gl.OVERLAY_BOOK.all(), a2w_presets=a2w_presets, a2w_modes=a2w_modes, mode=s.mode,
                                 current_scene=scenes.capture_scene("Full Randomizer", params, s.mode, None), last=rnd["last"])
        return randomizer.draw_scene(rnd["rng"], settings, ctx)

    def random_draw() -> bool:
        """Tire une scene et l'applique (le VJ ajoute le flash et relance le minuteur)."""
        if scene_locked():
            return False
        scene, info, record = random_scene()
        rnd["last"], rnd["scene"], rnd["info"] = record, scene, info
        apply_scene_dict(scene)
        s.active_scene, s.active_entry = "Full Randomizer", None
        scene_msg.set("Full Randomizer : " + info)
        return True

    def add_random_scene() -> str:
        """Bouton + : une scene tiree au hasard, sous un nom aleatoire, ajoutee a la fin du set actif. Aucun preset n'est enregistre
        et rien n'est applique (le spectacle en cours n'est pas perturbe). Rend le nom de la scene."""
        return add_random_scenes(1)[0]

    def add_random_scenes(count: int = 5) -> list[str]:
        """Bouton +5 : `count` scenes tirees au hasard (comme +), ajoutees a la suite ; la barre n'est reconstruite qu'une fois."""
        names: list[str] = []
        info = ""
        for _ in range(max(1, int(count))):
            scene, info, record = random_scene()
            rnd["last"] = record
            name = randomizer.random_name(rnd["rng"], scene_book.names())
            scene_book.put(dict(scene, name=name, temp=True))       # provisoire : a garder ou a retirer (panneau etendu)
            names.append(name)
        if len(names) == 1:
            scene_msg.set(f"Scene aleatoire '{names[0]}' ajoutee au set (aucun preset enregistre) : {info}")
        else:
            scene_msg.set(f"{len(names)} scenes aleatoires ajoutees au set (aucun preset enregistre) : {', '.join(names)}")
        refresh_scene_bar()
        return names

    def duplicate_last_scene(ask: bool = True) -> str | None:
        """Bouton « Dupliquer la derniere » : copie de la derniere scene du set (scene + media, videos, duree, VJ de l'entree) sous un nom
        inedit, ajoutee a la suite. Rend le nouveau nom (None : set vide). `ask` : propose ensuite de la renommer."""
        rows = scene_book.rows()
        if not rows:
            scene_msg.set("Le set est vide : rien a dupliquer (utilise + ou Nouvelle scene)")
            return None
        last = rows[-1]
        name = scenes.unique_name(f"{last['name']} (2)", scene_book.names())
        scene_book.put(dict(json.loads(json.dumps(last["scene"])), name=name))
        index = len(scene_book.rows()) - 1
        if last["media"]:
            scene_book.set_entry_media(index, dict(last["media"]))
        for key, path in (last["a2w"] or {}).items():
            scene_book.set_entry_a2w(index, key, path)
        if last.get("bg_video"):
            scene_book.set_entry_bg_video(index, last["bg_video"])
        scene_book.set_entry_vj(index, seconds=last["seconds"], skip=last["skip"])
        scene_msg.set(f"Scene '{last['name']}' dupliquee en '{name}' : change ses medias, sa duree ou son nom")
        refresh_scene_bar()
        def show_last() -> None:                                       # la nouvelle ligne est visible
            cv = panel_state.get("cv")                                 # (le panneau a pu etre reconstruit entre-temps)
            try:
                if cv is not None:
                    cv.yview_moveto(1.0)
            except tk.TclError:
                pass
        after(80, show_last)
        if ask:
            rename_scene_dialog(name)
        return name

    def random_draw_now() -> None:
        """Bouton « Tirer maintenant » : un tirage tout de suite (le VJ, s'il tourne, repart pour une duree pleine)."""
        if scene_locked():
            return
        s.commands.put("flash")
        random_draw()
        if vj["running"]:
            vj_arm()
        refresh_scene_bar()

    def keep_a2w_mode(scene: dict | None) -> str | None:
        """Mode d'audio2wave dont le panneau est le fond de ce tirage (None : le fond n'est pas un visuel audio2wave)."""
        if scene is not None and scene.get("bg") == "live" and (scene.get("mode") in (None, s.mode)):
            return s.mode
        return None

    def random_keep_check(names: dict) -> str | None:
        """Message d'erreur pour les noms demandes (None = tout va bien)."""
        scene = rnd["scene"]
        if scene is None:
            return "Aucun tirage a garder : 'Tirer maintenant' d'abord"
        names = {k: (names.get(k) or "").strip()[:scenes.MAX_NAME] for k in ("fond", "overlay", "a2w", "scene")}
        if not any(names.values()):
            return "Donne au moins un nom (preset fond, preset overlay, preset audio2wave ou scene)"
        if names["fond"] and not randomizer.fond_saveable(scene):
            return "Ce tirage n'a pas de fond genere ni de video choisie : pas de preset de fond"
        if names["a2w"] and keep_a2w_mode(scene) is None:
            return "Ce tirage n'a pas de fond audio2wave : pas de preset audio2wave"
        try:
            a2w_taken = list(mode_store(live, s.mode).all())
        except Exception:
            a2w_taken = []
        for key, taken, label in (("fond", gl.BACKGROUND_BOOK.names(), "preset de fond"),
                                  ("overlay", gl.OVERLAY_BOOK.names(), "preset overlay"),
                                  ("a2w", a2w_taken, f"preset {s.mode} d'audio2wave"), ("scene", scene_book.names(), "scene")):
            name = names[key]
            if name and (name.lower() == "default" or name.lower() in (x.lower() for x in taken)):
                return f"{label} '{name}' existe deja : choisis un autre nom"
        return None

    def random_save(names: dict) -> str | None:
        """Enregistre le dernier tirage : preset de fond, preset overlay et / ou scene (un nom vide = pas enregistre). Rend un message
        d'erreur (rien n'est ecrit) ou None."""
        error = random_keep_check(names)
        if error:
            return error
        scene = rnd["scene"]
        names = {k: (names.get(k) or "").strip()[:scenes.MAX_NAME] for k in ("fond", "overlay", "a2w", "scene")}
        import preset_registry
        names["a2w"] = preset_registry.normalize_name(names["a2w"])          # la regle des trois fenetres d'audio2wave
        saved = []
        if keep_a2w_mode(scene) is not None and (names["a2w"] or names["scene"]):
            # Fond audio2wave : ce qui est a l'ecran est l'etat reel de leur panneau (le tirage, mais aussi ce qui a pu etre retouche
            # depuis, et un tirage « fond verrouille » ou preset de fond live n'a pas d'etat propre) : c'est lui qu'on retient.
            if names["a2w"]:
                stored = save_live_preset(names["a2w"])
                if stored is None:
                    return f"Preset {s.mode} d'audio2wave non enregistre : leur panneau l'a refuse (rien n'a ete ecrit)"
                saved.append(f"preset {s.mode} '{stored}'")
            state = capture_live_overrides()
            if state is not None:
                scene = dict(scene, mode=s.mode, live_overrides=scenes.strip_live_overrides(state))
        if names["fond"]:
            gl.BACKGROUND_BOOK.save(names["fond"], json.loads(json.dumps(scene["fond"])))
            bg_presets["refresh"]()
            saved.append(f"preset fond '{names['fond']}'")
        if names["overlay"]:
            gl.OVERLAY_BOOK.save(names["overlay"], json.loads(json.dumps(scene["overlay"])))
            overlay_presets["refresh"]()
            saved.append(f"preset overlay '{names['overlay']}'")
        if names["scene"]:
            scene_book.put(dict(scene, name=names["scene"]))
            s.active_scene = names["scene"]
            s.active_entry = current_index(scene_book.rows())
            saved.append(f"scene '{names['scene']}' (ajoutee au set)")
        scene_msg.set("Enregistre : " + ", ".join(saved))
        refresh_scene_bar()
        return None

    def random_keep() -> None:
        """Bouton « Garder ce tirage... » : une modale propose les noms du preset de fond, du preset overlay et de la scene."""
        scene = rnd["scene"]
        if scene is None:
            scene_msg.set("Aucun tirage a garder : 'Tirer maintenant' d'abord")
            return
        fond_ok = randomizer.fond_saveable(scene)
        a2w_mode = keep_a2w_mode(scene)
        defaults = {"fond": scenes.unique_name("Tirage fond", gl.BACKGROUND_BOOK.names()),
                    "overlay": scenes.unique_name("Tirage overlay", gl.OVERLAY_BOOK.names()),
                    "scene": scenes.unique_name("Tirage", scene_book.names())}
        if a2w_mode:
            try:
                defaults["a2w"] = scenes.unique_name(f"Tirage {a2w_mode}", list(mode_store(live, a2w_mode).all()))
            except Exception:
                defaults["a2w"] = f"Tirage {a2w_mode}"
        names = scene_ui["ask_keep"](defaults, fond_ok, random_keep_check, a2w_mode)
        if names is None:
            return
        error = random_save(names)
        if error:
            scene_msg.set(error)

    def set_random_option(refresh: bool = False, **kw) -> None:
        current = scene_book.vj()["random"]
        current.update(kw)
        scene_book.set_vj(random=current)
        if refresh:
            refresh_scene_bar()

    import types
    return types.SimpleNamespace(add_random_scene=add_random_scene, add_random_scenes=add_random_scenes, duplicate_last_scene=duplicate_last_scene, random_bank=random_bank, random_draw=random_draw, random_draw_now=random_draw_now, random_keep=random_keep, random_keep_check=random_keep_check, random_save=random_save, rnd=rnd, set_random_option=set_random_option)
