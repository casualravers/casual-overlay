"""Projection (fenetre de rendu a la demande, reprise automatique), chargement du logo et boucle de rendu.

Extrait tel quel de `audio2wave_gl` (qui re-exporte ces noms).
"""
from __future__ import annotations

import queue
import sys
import threading
import time
from core import params as core_params, params_schema, sessionlog
from core.automation import AutomationEngine, set_fx_link
from core.controls import SilenceGuard, panic, strobe_press, strobe_release
from core.params import FX_NAMES, load_params, save_params
from engine.render import PerfGuard, Renderer, ShaderError, load_logo, render_text
from engine.deps import PIL_Image, glfw, moderngl
from engine.session import Session
from engine.video import HEX_DIGITS, BgVideoManager, LogoVideo
from engine.window import Window, fullscreen_blocked, monitor_contains, pick_monitor, resolve_fullscreen
from pathlib import Path


class Projection:
    """Fenetre de rendu (fil GL) ouverte et fermee a la demande, pour la GUI : `start()` / `stop()` autant de fois qu'on veut,
    la GUI, l'audio et le producteur ne sont pas touches. Par defaut elle s'ouvre EN FENETRE (sur un autre ecran que
    les reglages s'il y en a un) ; le plein ecran se demande (--fullscreen, case, touche F).
    `target` s'injecte pour les tests."""

    def __init__(self, s: Session, target=None):
        self.s = s
        self._target = target
        self.thread: threading.Thread | None = None

    def running(self) -> bool:
        return self.thread is not None and self.thread.is_alive()

    def start(self) -> str:
        """Ouvre la projection. Rend le message a afficher."""
        s = self.s
        if self.running():
            return "La projection est deja ouverte"
        a = s.args
        try:
            s.gui_point = s.gui_point_fn() if s.gui_point_fn else None      # appele depuis le fil de la GUI
        except Exception:
            s.gui_point = None
        a.fullscreen = resolve_fullscreen(getattr(a, "fullscreen_request", None))
        s.proj_stop.clear()
        while True:                                     # commandes envoyees pendant que la projection etait fermee : perimees
            try:
                s.commands.get_nowait()
            except queue.Empty:
                break
        while True:                                     # doodles arrives projection fermee : perimes aussi (seuls les nouveaux s'affichent)
            try:
                s.doodle_queue.get_nowait()
            except queue.Empty:
                break
        self.thread = threading.Thread(target=self._run, name="gl", daemon=True)
        self.thread.start()
        if a.fullscreen:
            return "Projection ouverte en plein ecran"
        return "Projection ouverte en fenetre (F ou la case Plein ecran pour le plein ecran)"

    MAX_RESTARTS = 3                                      # reprises automatiques tolerees...
    RESTART_WINDOW_S = 120.0                              # ...sur cette fenetre (au-dela : on s'arrete, il y a un vrai probleme)
    RESTART_DELAY_S = 1.0

    def _run(self) -> None:
        """Fil de la projection. Si la fenetre meurt sur une erreur (pas une fermeture voulue : Echap, bouton, arret de la GUI), elle
        est relancee toute seule avec les reglages et la scene du moment (ils vivent dans `s.params`, pas dans la fenetre)."""
        starts: list[float] = []
        while True:
            try:
                sessionlog.event("Projection : fenetre ouverte")
                (self._target or gl_main)(self.s)
                sessionlog.event("Projection : fenetre fermee")
                return
            except SystemExit as exc:                    # gl_main quitte par sys.exit : la GUI continue
                self.s.status["msg"] = "Projection impossible (voir la console)" if exc.code else self.s.status.get("msg", "")
                sessionlog.event(f"Projection : arret (code {exc.code})")
                return
            except Exception as exc:
                sessionlog.error("Projection : la fenetre GL est morte", exc)
                print(f"Projection arretee: {exc}", file=sys.stderr)
                now = time.monotonic()
                starts = [t for t in starts if now - t < self.RESTART_WINDOW_S] + [now]
                if self.s.proj_stop.is_set() or self.s.stop_event.is_set() or len(starts) > self.MAX_RESTARTS:
                    self.s.status["msg"] = f"Projection arretee: {exc}" + (
                        " (trop de reprises : bouton Demarrer pour reessayer)" if len(starts) > self.MAX_RESTARTS else "")
                    return
                self.s.status["msg"] = f"Projection relancee apres une erreur ({exc})"
                sessionlog.event(f"Projection : reprise automatique {len(starts)}/{self.MAX_RESTARTS}")
                time.sleep(self.RESTART_DELAY_S)
                if self.s.proj_stop.is_set() or self.s.stop_event.is_set():
                    return

    def stop(self, wait: float = 0.0) -> None:
        """Ferme la projection (attend au plus `wait` secondes)."""
        self.s.proj_stop.set()
        if wait and self.thread is not None and self.thread is not threading.current_thread():
            self.thread.join(wait)


def logo_signature(params: dict) -> tuple:
    """Ce qui determine la texture du logo: si ca change, il faut la regenerer. Les autres
    reglages (position, taille, opacite, effets...) sont relus a chaque image, sans regeneration."""
    if params.get("logo_source") == "text":
        return ("text", params.get("text_content", ""), params.get("text_font", "auto"),
                params.get("text_color", "#ffffff"), params.get("text_align", "center"))
    if params.get("logo_source") == "video":
        key = str(params.get("logo_key", "")).strip().lstrip("#").lower()
        key = key if len(key) == 6 and all(c in HEX_DIGITS for c in key) else ""
        return ("video", params.get("logo_video", ""), key)
    return ("image", params.get("logo_path", ""))


def apply_logo_request(s: Session, renderer: Renderer) -> None:
    """(Re)genere la texture du logo si ce qui la determine a change (fil GL): image PNG, texte
    tape a la main rendu par Pillow, ou video animee (ffmpeg en boucle)."""
    if renderer.logo_video is not None:
        err = renderer.logo_video.error()
        if err:                                   # ffmpeg mort sans image (codec absent, fichier corrompu)
            s.status["logo"] = f"Video illisible: {err}"
            print(s.status["logo"], file=sys.stderr)
            renderer.set_logo(None, "")
    signature = logo_signature(s.params)
    if signature == renderer.logo_requested:
        return
    renderer.logo_requested = signature
    if signature[0] == "video":
        want = signature[1]
        if not want:
            renderer.set_logo(None, "")
            s.status["logo"] = "Aucune video"
            return
        if not Path(want).is_file():
            s.status["logo"] = f"Video introuvable: {want}"
            print(s.status["logo"], file=sys.stderr)
            return
        try:
            video = LogoVideo(want, signature[2])
        except Exception as exc:
            s.status["logo"] = f"Video illisible ({Path(want).name}): {exc}"
            print(s.status["logo"], file=sys.stderr)
            return
        renderer.set_logo_video(video, want)
        s.status["logo"] = f"Video: {Path(want).name}"
        return
    if signature[0] == "text":
        try:
            rgba = render_text(signature[1], signature[2], signature[3], signature[4])
        except Exception as exc:
            s.status["logo"] = f"Texte illisible: {exc}"
            print(s.status["logo"], file=sys.stderr)
            return
        renderer.set_logo(rgba, "", "text")
        first = signature[1].strip().splitlines()[0] if signature[1].strip() else ""
        s.status["logo"] = f"Texte: {first[:24]}" if rgba is not None else "Texte vide"
        return
    want = signature[1]
    if not want or want.lower() == "none":
        renderer.set_logo(None, "")
        s.status["logo"] = "Aucun logo"
        return
    path = Path(want)
    if not path.is_file():
        s.status["logo"] = f"Logo introuvable: {want}"
        print(s.status["logo"], file=sys.stderr)
        return
    try:
        renderer.set_logo(load_logo(path), want, "image")
        s.status["logo"] = f"Logo: {path.name}"
    except Exception as exc:
        s.status["logo"] = f"Logo illisible ({path.name}): {exc}"
        print(s.status["logo"], file=sys.stderr)


_GL_SLOT = threading.Lock()          # une seule fenetre de projection a la fois, quel que soit le chemin qui l'ouvre


def gl_main(s: Session) -> None:
    """Un seul `_gl_main` en marche : un second appel (demarrage pendant la fermeture du premier, double declenchement)
    attend la fin de l'ancien au plus 3 s, puis renonce plutot que d'ouvrir une deuxieme fenetre par-dessus la GUI."""
    if not _GL_SLOT.acquire(timeout=3.0):
        s.status["msg"] = "La projection est deja ouverte"
        return
    try:
        _gl_main(s)
    finally:
        _GL_SLOT.release()


def _gl_main(s: Session) -> None:
    """Fenetre GL + boucle de rendu. Tous les appels glfw partent de CE fil (init, creation,
    evenements, destruction): avec --gui il tourne a cote de tkinter, qui garde le fil principal."""
    args = s.args
    window = None
    try:
        if not glfw.init():
            print("Initialisation de glfw impossible.", file=sys.stderr)
            sys.exit(1)
        avoid = getattr(s, "gui_point", None)
        monitor = pick_monitor(args.monitor, avoid)
        window = Window(monitor, s.win_size, args.fullscreen, shared=monitor_contains(monitor, avoid))
        ctx = moderngl.create_context()
        mode = glfw.get_video_mode(monitor)
        wx, wy = glfw.get_window_pos(window.handle)
        ww, wh = glfw.get_window_size(window.handle)
        print(f"OpenGL: {ctx.info['GL_RENDERER']} | moniteur en ({window.mx},{window.my}) "
              f"{window.mw}x{window.mh} @ {mode.refresh_rate} Hz | fenetre en ({wx},{wy}) {ww}x{wh} | "
              f"rendu ffmpeg {s.render_size[0]}x{s.render_size[1]}")
        renderer = Renderer(ctx, s.render_size, None, "")
        renderer.logo_requested = None          # force le chargement du logo demande a la 1re image
        renderer.hud = args.hud
        s.renderer, s.window = renderer, window
        loop(s, window, ctx, renderer)
    finally:
        if getattr(s, "bgvideo", None) is not None:
            s.bgvideo.stop()
        if getattr(s.pysrc, "native", False):
            s.pysrc.release_gl()                      # les ressources GL d'un mode se liberent dans le fil GL
        if s.renderer is not None:
            s.renderer.release_logo_video()
        s.renderer = None
        if window is not None:
            glfw.destroy_window(window.handle)
        glfw.terminate()
        if getattr(args, "gui", False):
            s.status["msg"] = "Projection fermee"          # la GUI reste ouverte (bouton pour la rouvrir)
            s.status["fps"] = 0.0
        else:
            s.finished_event.set()


def loop(s: Session, window: Window, ctx, renderer: Renderer) -> None:
    args, params, reader = s.args, s.params, s.reader
    analyzer = s.audio.analyzer
    handle = window.handle
    quit_flag = {"v": False}
    automation = AutomationEngine()
    silence = SilenceGuard()

    def status_line() -> str:
        on = "".join(str(int(v)) for v in params["fx_on"])
        return (f"intensite {params['master']:.1f} | sensibilite kick {params['sensitivity']:.2f} | "
                f"effets {on} ({'/'.join(FX_NAMES)})"
                + ("" if params["fx_link"] >= 0.5 else
                   f" | logo {''.join(str(int(v)) for v in params['fxl_on'])} (separes du fond)"))

    def gui_pt():
        return getattr(s, "gui_point", None)

    def act(name: str) -> None:
        """Actions communes aux touches et aux commandes de la GUI (executees dans ce fil)."""
        if name == "quit":
            quit_flag["v"] = True
        elif name == "fullscreen":
            if not window.fullscreen:
                window.retarget(pick_monitor(args.monitor, gui_pt()), gui_pt())     # un projecteur branche depuis le lancement est pris en compte
            window.toggle_fullscreen()
        elif name.startswith("fullscreen="):      # etat explicite (case « Plein ecran » de la GUI, preset qui la coche)
            want = name.endswith("1")
            if want and not window.fullscreen:
                window.retarget(pick_monitor(args.monitor, gui_pt()), gui_pt())
                # seul l'ecran des reglages est interdit (un plein ecran le recouvrirait) ; point inconnu : l'ecran principal
                on_gui = monitor_contains(window.monitor, gui_pt()) if gui_pt() is not None else None
                if fullscreen_blocked(on_gui, window.is_primary, args.monitor, getattr(args, "fullscreen_request", None)):
                    s.status["msg"] = "Plein ecran refuse sur l'ecran des reglages : projette sur un autre ecran (ou --fullscreen)"
                    print(s.status["msg"], file=sys.stderr)
                    return
            if window.fullscreen != want:
                window.toggle_fullscreen()
        elif name == "hud":
            renderer.hud = not renderer.hud
        elif name == "bg":
            modes = ["live", "pattern"] + (["video"] if params.get("bg_video") else [])     # video seulement si un fichier est choisi
            params["bg_mode"] = modes[(modes.index(params["bg_mode"]) + 1) % len(modes)] if params["bg_mode"] in modes else "live"
            print(f"fond: {params['bg_mode']}")
        elif name == "auto":
            params["auto_master"] = 0.0 if params["auto_master"] >= 0.5 else 1.0
            print(f"automations: {'actives' if params['auto_master'] >= 0.5 else 'figees'}")
        elif name == "reload":
            try:
                renderer.reload_shaders()
                # ne pas ecraser ce que la GUI vient de regler (ni l'etat des automations qu'elle edite)
                keep = {*params_schema.RELOAD_KEEP_KEYS, "_automation"}
                params.update({k: v for k, v in load_params().items() if k not in keep})
                print("Shaders et reglages recharges.")
                s.status["msg"] = "Shaders recharges"
            except ShaderError as exc:
                print(f"Erreur de shader (ancien programme conserve):\n{exc}", file=sys.stderr)
                s.status["msg"] = "Erreur de shader (voir la console), ancien programme conserve"
        elif name == "save":
            save_params(params)
            print(f"Reglages sauves dans {core_params.PARAMS_PATH}")
            s.status["msg"] = f"Reglages sauves ({core_params.PARAMS_PATH.name})"
        elif name == "holo":
            params["holo_on"] = 0.0 if float(params["holo_on"]) >= 0.5 else 1.0
            print(f"halo holographique: {'on' if params['holo_on'] >= 0.5 else 'off'}")
        elif name == "melt":
            params["melt_on"] = 0.0 if float(params["melt_on"]) >= 0.5 else 1.0
            print(f"fonte acide du logo: {'on' if params['melt_on'] >= 0.5 else 'off'}")
        elif name == "cell":
            params["cell_on"] = 0.0 if float(params["cell_on"]) >= 0.5 else 1.0
            print(f"cellules du logo: {'on' if params['cell_on'] >= 0.5 else 'off'}")
        elif name == "flash":
            renderer.flash_t = time.monotonic()           # transition du VJ (demandee par la GUI)
        elif name == "panic":
            changed = panic(params)                        # tout de suite, dans ce fil : pas d'attente de la GUI
            sessionlog.event("PANIQUE : " + (", ".join(changed) if changed else "rien a couper"))
            s.status["msg"] = "PANIQUE : effets, strobe et montee coupes"
            renderer.flash_t = time.monotonic()
            if args.gui:
                s.scene_requests.put("panic")              # la GUI met le VJ en pause et remet les curseurs a jour
        elif name in ("vj", "vjnext", "lock", "back", "tap", "help"):
            if args.gui:
                s.scene_requests.put(name)                # Espace / fleche droite : la GUI pilote le VJ
            else:
                print("VJ: disponible avec --gui seulement", file=sys.stderr)
        elif name.startswith("scene") and name[5:].isdigit():
            if args.gui:
                s.scene_requests.put(int(name[5:]))       # la GUI (fil principal) applique la scene
            else:
                print("Scenes: disponibles avec --gui seulement", file=sys.stderr)
        elif name == "link":
            set_fx_link(params, float(params["fx_link"]) < 0.5)
            print(f"effets fond/logo: {'lies' if params['fx_link'] >= 0.5 else 'separes'}")
        elif name.startswith("fxl") and name[3:].isdigit():
            if float(params["fx_link"]) >= 0.5:      # toucher a la couche du logo la delie (reglages copies)
                set_fx_link(params, False)
            i = int(name[3:])
            params["fxl_on"][i] = 0 if params["fxl_on"][i] else 1
            print(f"effet {FX_NAMES[i]} (logo): {'on' if params['fxl_on'][i] else 'off'}")
        elif name.startswith("fx") and name[2:].isdigit():
            i = int(name[2:])
            params["fx_on"][i] = 0 if params["fx_on"][i] else 1
            print(f"effet {FX_NAMES[i]}: {'on' if params['fx_on'][i] else 'off'}")
        elif name == "master+":
            params["master"] = min(params["master"] + 0.1, 2.0)
            print(status_line())
        elif name == "master-":
            params["master"] = max(params["master"] - 0.1, 0.0)
            print(status_line())
        elif name == "sens+":
            params["sensitivity"] = min(params["sensitivity"] * 1.15, 4.0)
            print(status_line())
        elif name == "sens-":
            params["sensitivity"] = max(params["sensitivity"] / 1.15, 0.25)
            print(status_line())

    key_actions = {glfw.KEY_ESCAPE: "quit", glfw.KEY_F: "fullscreen", glfw.KEY_H: "hud", glfw.KEY_B: "bg", glfw.KEY_T: "auto",
                   glfw.KEY_R: "reload", glfw.KEY_P: "save",
                   glfw.KEY_EQUAL: "master+", glfw.KEY_KP_ADD: "master+", glfw.KEY_PAGE_UP: "master+",
                   glfw.KEY_MINUS: "master-", glfw.KEY_KP_SUBTRACT: "master-", glfw.KEY_PAGE_DOWN: "master-",
                   glfw.KEY_UP: "sens+", glfw.KEY_DOWN: "sens-"}
    for i, k in enumerate((glfw.KEY_1, glfw.KEY_2, glfw.KEY_3, glfw.KEY_4, glfw.KEY_5)):
        key_actions[k] = f"fx{i}"
    for i, k in enumerate((glfw.KEY_KP_1, glfw.KEY_KP_2, glfw.KEY_KP_3, glfw.KEY_KP_4, glfw.KEY_KP_5)):
        key_actions[k] = f"fx{i}"
    repeatable = {"master+", "master-", "sens+", "sens-"}

    key_actions[glfw.KEY_L] = "link"
    key_actions[glfw.KEY_C] = "holo"
    key_actions[glfw.KEY_M] = "melt"
    key_actions[glfw.KEY_V] = "cell"
    key_actions[glfw.KEY_BACKSPACE] = "back"     # scene precedente (la GUI la gere : voir poll_scene_requests)
    key_actions[glfw.KEY_K] = "lock"             # verrou de scene (la GUI le gere : voir poll_scene_requests)
    key_actions[glfw.KEY_S] = "strobe"          # strobe : maintenu (manuel) ou arme / desarme (kick, cadence)
    key_actions[glfw.KEY_SPACE] = "vj"           # VJ : lecture / pause
    key_actions[glfw.KEY_X] = "panic"            # PANIQUE : coupe effets, strobe, montee... et rend une image propre
    key_actions[glfw.KEY_PERIOD] = "tap"         # tap tempo (la GUI le gere)
    key_actions[glfw.KEY_SLASH] = "help"         # aide des raccourcis (la GUI l'affiche)
    key_actions[glfw.KEY_RIGHT] = "vjnext"       # VJ : scene suivante
    for n in range(9):
        key_actions[getattr(glfw, f"KEY_F{n + 1}")] = f"scene{n}"       # F1..F9 : scenes

    def on_key(win, key, scancode, action, mods):
        name = key_actions.get(key)
        if name and mods & glfw.MOD_SHIFT and name in ("fx0", "fx1", "fx2", "fx3"):
            name = "fxl" + name[2:]              # Maj + 1..4: meme effet sur la couche du logo
        if name == "strobe":
            if action == glfw.PRESS:
                strobe_press(params)
            elif action == glfw.RELEASE:
                strobe_release(params)
        elif name and (action == glfw.PRESS or (action == glfw.REPEAT and name in repeatable)):
            act(name)

    glfw.set_key_callback(handle, on_key)
    print("Echap quitte | F plein ecran | H barres debug | B fond live/motif | T automations | 1-5 effets (Maj+1-4: "
          "logo) | L lier fond/logo | C halo holographique | +/- intensite | haut/bas sensibilite kick | R recharge shaders | P sauve reglages | F1-F9 scenes | Espace VJ | -> scene suivante | X PANIQUE | . tap tempo | / aide (avec --gui)")
    print(status_line())

    last_native = None
    native_warned = False
    bgvideo = s.bgvideo = BgVideoManager(s.render_size, s.status)     # fond "video" : decodage lance / arrete selon les reglages
    query = ctx.query(time=True)      # une image sur 8 (toutes avec --stats) : le garde-fou de charge et les mesures
    guard = PerfGuard(glfw.get_video_mode(window.monitor).refresh_rate or 60)
    frame_i = 0
    perf_warned = False
    t0 = time.monotonic()
    last_title = t0
    frames = 0
    win_frames = 0
    up_ms = gpu_ms = 0.0
    up_n = gpu_n = 0
    last_stats = t0
    warned = False
    last_restarts = 0
    last_beats = 0
    refresh = glfw.get_video_mode(window.monitor).refresh_rate or 60
    cap = refresh * 1.02 if args.fps_cap is None else args.fps_cap
    period = 1.0 / cap if cap > 0 else 0.0
    deadline = time.monotonic()
    last_read = 0
    max_gap = 0.0
    prev_frame_t = t0
    shot_done = False
    if args.screenshot and args.max_seconds is None:
        args.max_seconds = 3.0

    while (not glfw.window_should_close(handle) and not quit_flag["v"] and not s.stop_event.is_set()
           and not s.proj_stop.is_set()):
        glfw.poll_events()
        while True:
            try:
                act(s.commands.get_nowait())
            except queue.Empty:
                break
        now = time.monotonic()
        max_gap = max(max_gap, now - prev_frame_t)
        prev_frame_t = now
        if args.max_seconds is not None and now - t0 >= args.max_seconds:
            break

        apply_logo_request(s, renderer)
        while True:                             # doodles du site decodes par le fil de recuperation : poses ici (fil GL)
            try:
                item = s.doodle_queue.get_nowait()
            except queue.Empty:
                break
            renderer.add_doodle(item["rgba"], now)
        automation.step(params, now)
        analyzer.extractor.sensitivity = params["sensitivity"]

        u0 = time.perf_counter()
        reader = s.reader                       # change quand on bascule Live / Snap / Ridge
        bg_reader = bgvideo.reader_for(params)
        if bg_reader is not None:               # fond "video" pret : son flux remplace celui d'audio2wave
            reader = bg_reader
        native = s.pysrc if getattr(s.pysrc, "native", False) else None      # Snap / Ridge dessines en GL : leur texture remplace le flux
        if native is not last_native:
            if last_native is not None:
                last_native.release_gl()
            last_native = native
        mode_tex = None
        if native is not None and bg_reader is None:
            try:
                mode_tex = native.render(ctx, now)
            except Exception as exc:            # un mode ne doit jamais faire tomber la fenetre : on garde le fond d'avant
                if not native_warned:
                    print(f"Mode {native.name} (GL) : {exc}", file=sys.stderr)
                    s.status["msg"] = f"Mode {native.name} : {exc}"
                    native_warned = True
        renderer.mode_tex = mode_tex
        if reader.upload_to(renderer.video_tex):
            up_ms += (time.perf_counter() - u0) * 1000.0
            up_n += 1
        producer = s.manager.producer if s.manager and s.mode == "live" else None
        if s.manager and s.manager.restarts != last_restarts:
            last_restarts, warned = s.manager.restarts, False
        if producer is not None and not warned and producer.poll() is not None:
            print(f"Le producteur ffmpeg s'est arrete (code {producer.returncode}): "
                  "derniere image conservee.", file=sys.stderr)
            s.status["live"] = f"ffmpeg arrete (code {producer.returncode}): derniere image conservee"
            warned = True

        state = analyzer.latest()
        silence.update(params, state, now)
        size = window.framebuffer_size()
        sample = args.stats or frame_i % 8 == 0
        frame_i += 1
        if sample:
            with query:
                renderer.draw(ctx.screen, size, state, params, now - t0)
        else:
            renderer.draw(ctx.screen, size, state, params, now - t0)
        if args.screenshot and not shot_done and now - t0 >= args.max_seconds - 0.2:
            # Lecture AVANT swap_buffers: apres, le tampon arriere est indefini.
            data = ctx.screen.read(viewport=(0, 0, size[0], size[1]), components=3)
            img = PIL_Image.frombytes("RGB", size, data).transpose(PIL_Image.FLIP_TOP_BOTTOM)
            Path(args.screenshot).parent.mkdir(parents=True, exist_ok=True)
            img.save(args.screenshot)
            print(f"Capture: {args.screenshot}")
            shot_done = True
        s.status["noise"] = renderer.noise_out
        glfw.swap_buffers(handle)
        if period:
            # Le vsync n'est pas garanti en borderless plein ecran (mesure: 108 fps sur un
            # moniteur 59 Hz): limiteur logiciel, inoperant quand le vsync tient deja le rythme.
            deadline += period
            delay = deadline - time.monotonic()
            if delay > 0:
                time.sleep(delay)
            elif delay < -period:
                deadline = time.monotonic()
        frames += 1
        win_frames += 1
        if sample:
            elapsed = query.elapsed / 1e6
            gpu_ms += elapsed
            gpu_n += 1
            warning = guard.update(elapsed, time.monotonic(), params)
            if warning:
                s.status["perf"] = warning
                if not perf_warned:
                    print(warning, file=sys.stderr)
                    perf_warned = True
            else:
                s.status.pop("perf", None)
                perf_warned = False

        now = time.monotonic()
        if now - last_title >= 0.5:
            fps = win_frames / (now - last_title)
            s.status["fps"] = fps
            glfw.set_window_title(handle, f"casual-overlay GL - {fps:.0f} fps - int {params['master']:.1f}"
                                          f" - sens {params['sensitivity']:.2f}")
            last_title = now
            win_frames = 0
        if args.stats and now - last_stats >= 5.0:
            span = now - last_stats
            print(f"[stats] {frames / span:.1f} fps | video {(reader.frames_read - last_read) / span:.1f} frames/s "
                  f"| upload {up_ms / max(up_n, 1):.2f} ms ({up_n / span:.1f}/s) "
                  f"| gpu {gpu_ms / max(gpu_n, 1):.2f} ms | pire intervalle {max_gap * 1000:.0f} ms "
                  f"| kicks {analyzer.beats - last_beats}", flush=True)
            max_gap = 0.0
            last_stats, frames, up_ms, up_n, gpu_ms, gpu_n = now, 0, 0.0, 0, 0.0, 0
            last_beats = analyzer.beats
            last_read = reader.frames_read
