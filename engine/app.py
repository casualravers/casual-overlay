"""Demarrage du moteur : micro, producteur, session, projection et arret propre.

Extrait tel quel de `audio2wave_gl` (qui re-exporte ces noms).
"""
from __future__ import annotations

import sys
from core import sessionlog
from core.automation import merge_automation
from core.autosave import clear_autosave, recover_autosave
from core.params import load_params
from engine.audio import RingBuffer, find_input_device
from engine.doodle_feed import DoodleFeed
from engine.projection import Projection, gl_main
from engine.session import AudioController, NoDeviceManager, Session
from engine.video import FrameReader, ProducerManager, SyntheticVideoStream
from pathlib import Path


def run_app(args, live, live_args, render_size: tuple[int, int], gui_loader=None) -> None:
    """gui_loader rend le module de la GUI (l'application l'injecte : le moteur n'importe jamais la GUI)."""
    sessionlog.setup()
    sessionlog.install_hooks()
    sessionlog.install_crash_dump()
    sessionlog.event(f"Lancement (gui={bool(args.gui)}, synthetic={bool(args.synthetic)})")
    params = load_params()
    params, recovery_msg = recover_autosave(params)
    crash = sessionlog.previous_crash()
    if crash:
        sessionlog.event("Plantage natif au lancement precedent (piles des fils) :\n" + crash[:4000])
    if args.sensitivity is not None:
        params["sensitivity"] = args.sensitivity
    params["_automation"] = merge_automation(params.get("_automation"))
    if args.automation is not None:
        params["auto_master"] = 1.0 if args.automation else 0.0
    if args.bg_video is not None:
        if not Path(args.bg_video).is_file():
            print(f"Video introuvable: {args.bg_video}", file=sys.stderr)
            sys.exit(2)
        params["bg_video"] = str(Path(args.bg_video).resolve())
        params["bg_mode"] = "video"
    if args.background is not None:
        params["bg_mode"] = args.background
    if args.text is not None:
        params["logo_source"] = "text"
        params["text_content"] = args.text.replace("\\n", "\n")
    if args.logo_video is not None:
        if not Path(args.logo_video).is_file():
            print(f"Video introuvable: {args.logo_video}", file=sys.stderr)
            sys.exit(2)
        params["logo_source"] = "video"
        params["logo_video"] = str(Path(args.logo_video).resolve())
    if args.logo_key is not None:
        params["logo_key"] = "" if args.logo_key.lower() == "none" else args.logo_key
    if args.logo is not None:
        params["logo_source"] = "image"
        if args.logo.lower() == "none":
            params["logo_path"] = ""
        elif not Path(args.logo).is_file():
            print(f"Logo introuvable: {args.logo} (--logo none pour s'en passer)", file=sys.stderr)
            sys.exit(2)
        else:
            params["logo_path"] = str(Path(args.logo).resolve())
    if args.logo_scale is not None:
        params["logo_scale"] = args.logo_scale
    if args.logo_pos is not None:
        params["logo_x"], params["logo_y"] = parse_pair(args.logo_pos, "--logo-pos", ",")
    win_size = tuple(int(v) for v in parse_pair(args.size or "1280x720", "--size", "x"))

    ring = RingBuffer(1 << 21)             # ~43 s a 48 kHz : les modes Snap / Ridge (GL) lisent des photos de plusieurs secondes
    device = None if args.synthetic else find_input_device(args.audio_device, args.device)
    audio = AudioController(ring, params["sensitivity"], args.synthetic, device)
    print(f"Analyse audio: {audio.name} ({audio.rate} Hz)"
          + ("" if args.synthetic else f", latence capture ~{audio.src.latency_ms:.0f} ms") + ".")

    frame_size = render_size[0] * render_size[1] * 3
    manager = None
    if args.synthetic:
        reader = FrameReader(SyntheticVideoStream(render_size[0], render_size[1], args.fps), frame_size)
    elif live_args.device:
        manager = ProducerManager(live, live_args, frame_size, {})
        reader = manager.reader
    else:
        # --gui sans -d: pas de producteur tant qu'aucune entree n'est choisie dans la fenetre.
        manager = NoDeviceManager(live, live_args, frame_size)
        reader = manager.reader

    s = Session(args, params, live, live_args, render_size, win_size, audio, manager, reader)
    if recovery_msg:
        s.status["msg"] = recovery_msg
        print(recovery_msg, file=sys.stderr)
    feed = DoodleFeed(s)                                  # doodles du site : le fil dort tant que la case est decochee
    clean = False
    try:
        feed.start()
        audio.start()
        if manager is not None:
            manager.start()
        else:
            reader.start()
        if args.gui:
            gl_gui = gui_loader()
            gl_gui.require_audio2wave_api(live)          # depot audio2wave trop ancien : message clair, avant de lancer le rendu
            s.projection = Projection(s)                 # la GUI seule s'ouvre ; la projection part du bouton (ou de --projeter)
            if args.projeter:
                print(s.projection.start())
            gl_gui.run_gui(s, live)
        else:
            gl_main(s)
        clean = True
    finally:
        sessionlog.event("Arret propre" if clean else "Arret anormal (l'auto-sauvegarde est gardee)")
        if clean:
            clear_autosave()
        s.stop_event.set()
        feed.stop()
        if s.projection is not None:
            s.projection.stop(wait=5.0)
        if s.pysrc is not None:
            s.pysrc.stop()
        audio.stop()
        if manager is not None:
            manager.stop()
        else:
            reader.stop()


def parse_pair(text: str, what: str, sep: str) -> tuple[float, float]:
    try:
        a, b = (float(x) for x in text.lower().split(sep, 1))
        return a, b
    except ValueError:
        print(f"{what} invalide: {text}", file=sys.stderr)
        sys.exit(2)

