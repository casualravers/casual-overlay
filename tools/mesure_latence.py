"""Mesure de la latence audio -> image de casual-overlay.

Un kick est injecte, la vraie chaine l'analyse (`AudioAnalyzer`, meme code que l'application) et le vrai rendu (`Renderer`) le montre
par un eclair du strobe (mode « kick ») : l'image devient blanche. On releve trois instants par kick :

    debut du son  ->  kick detecte  ->  premiere image blanche lue dans le tampon de rendu
          (analyse)                       (rendu, image comprise)

Deux modes :
  * interne (defaut) : le kick est ecrit dans le tampon audio au rythme reel, blocs de 512 comme le callback d'une carte son.
    Mesure la partie LOGICIELLE (fenetre d'analyse, fil d'analyse, boucle de rendu a 60 Hz). Aucun materiel.
  * --materiel : le kick est JOUE par la sortie audio (haut-parleur ou casque pose contre le micro) et capte par l'entree reelle
    (sounddevice, comme `run_app`). Ajoute le tampon des cartes son et le trajet acoustique.

Ce qui reste hors mesure dans les deux cas : l'affichage (balayage de l'ecran, traitement du videoprojecteur, quelques dizaines de ms) ;
seule une camera rapide ou une photodiode devant l'ecran le donne. Le chiffre rendu est donc un MINIMUM pour ce que voit le public.

    python tools/mesure_latence.py                      # logiciel seul
    python tools/mesure_latence.py --materiel           # entree et sortie par defaut de Windows
    python tools/mesure_latence.py --materiel --entree "Microphone" --sortie "Speakers" --kicks 20
    python tools/mesure_latence.py --liste              # peripheriques audio
"""
from __future__ import annotations

import argparse
import json
import math
import sys
import threading
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

TARGET_MS = 50.0            # cible du plan d'origine (audio -> effet)
WARMUP_S = 2.5              # le detecteur de kick a besoin d'une ligne de base (voir engine.audio)
GAP_S = 0.8                 # un kick toutes les 0,8 s (75 BPM) : plus lent que la periode refractaire (0,15 s)


def kick_wave(ph, np):
    """Kick de test : balayage 160 -> 60 Hz, decroissance 90 ms. `ph` : secondes depuis le debut du son (tableau numpy)."""
    ph = np.clip(ph, 0.0, None)
    phase = 2 * np.pi * (60.0 * ph + 100.0 * 0.03 * (1.0 - np.exp(-ph / 0.03)))
    return 0.9 * np.sin(phase) * np.exp(-ph / 0.09)


def summarize(values: list[float]) -> dict:
    """n, mini, mediane, p95 et maxi (en ms) d'une liste de mesures ; {} si vide."""
    vals = sorted(v for v in values if v is not None and math.isfinite(v))
    if not vals:
        return {}
    pos = 0.95 * (len(vals) - 1)
    lo, hi = int(math.floor(pos)), int(math.ceil(pos))
    p95 = vals[lo] + (vals[hi] - vals[lo]) * (pos - lo)
    mid = vals[len(vals) // 2] if len(vals) % 2 else (vals[len(vals) // 2 - 1] + vals[len(vals) // 2]) / 2
    return {"n": len(vals), "min": vals[0], "median": mid, "p95": p95, "max": vals[-1]}


def match(onsets: list[float], detections: list[float], flashes: list[float], gap: float = GAP_S) -> list[dict]:
    """Associe a chaque debut de son (horloge monotone) le premier kick detecte puis la premiere image blanche qui le suivent."""
    rows = []
    for onset in onsets:
        det = next((d for d in detections if onset - 0.02 <= d < onset + gap), None)
        flash = next((f for f in flashes if det is not None and f >= det and f < onset + gap + 0.2), None)
        rows.append({"onset": onset,
                     "analyse_ms": None if det is None else (det - onset) * 1000.0,
                     "rendu_ms": None if det is None or flash is None else (flash - det) * 1000.0,
                     "total_ms": None if flash is None else (flash - onset) * 1000.0})
    return rows


def _render_loop(an, size, fps, t_end, np, moderngl):
    """Boucle de rendu a cadence fixe (comme l'ecran) : rend l'etat de l'analyse, lit l'image, releve les eclairs. Rend les instants d'eclair."""
    import audio2wave_gl as gl
    ctx = moderngl.create_standalone_context()
    target = ctx.texture(size, 4)
    fbo = ctx.framebuffer(color_attachments=[target])
    renderer = gl.Renderer(ctx, size, None)
    params = dict(gl.DEFAULT_PARAMS)
    params.update(holo_on=0.0, bg_mode="pattern", bg_master=0.0, fx_on=[0, 0, 0, 0, 0], strobe_on=1.0, strobe_mode="kick",
                  strobe_max_hz=20.0, strobe_max_s=600.0, strobe_dark=1.0, auto_master=0.0)
    flashes: list[float] = []
    was_white = False
    period = 1.0 / fps
    t0 = time.monotonic()
    next_frame = t0
    while True:
        now = time.monotonic()
        if now > t_end:
            break
        state = an.latest()
        renderer.draw(fbo, size, state, params, now - t0)
        data = fbo.read(components=3)                       # attend la fin du rendu : l'image est vraiment prete
        t_read = time.monotonic()
        white = float(np.frombuffer(data, dtype=np.uint8).mean()) > 100.0
        if white and not was_white:
            flashes.append(t_read)
        was_white = white
        next_frame += period
        delay = next_frame - time.monotonic()
        if delay > 0:
            time.sleep(delay)
        else:
            next_frame = time.monotonic()                   # en retard : on ne rattrape pas, comme l'ecran
    return flashes


def _watch_detections(an, stop, out):
    """Releve l'instant de chaque kick detecte (`an.t_beat` est pose par le fil d'analyse au moment de la detection)."""
    seen = an.beats
    while not stop.is_set():
        if an.beats != seen:
            seen = an.beats
            if an.t_beat is not None:
                out.append(an.t_beat)
        time.sleep(0.0005)


def measure(kicks: int = 12, hardware: bool = False, input_device=None, output_device=None, fps: int = 60,
            size: tuple[int, int] = (320, 180), rate: int = 48000, block: int = 512, log=print) -> dict:
    """Lance la mesure et rend {"rows": [...], "analyse": {...}, "rendu": {...}, "total": {...}, "missed": n, "info": {...}}."""
    from engine.audio import AudioAnalyzer, FeatureExtractor, RingBuffer, SoundDeviceInput
    from engine.deps import MISSING, moderngl, np, sd
    if np is None or moderngl is None:
        raise RuntimeError("numpy et moderngl sont necessaires (pip install -r requirements-gl.txt)")
    ring = RingBuffer()
    info: dict = {"mode": "materiel" if hardware else "interne", "fps": fps, "bloc": block}
    onsets: list[float] = []
    stop = threading.Event()
    streams = []
    t_start = time.monotonic() + 0.3

    if hardware:
        if sd is None:
            raise RuntimeError("sounddevice est necessaire pour --materiel")
        mic = SoundDeviceInput(input_device, ring, blocksize=block)
        rate = mic.rate
        info.update(entree=mic.name, latence_entree_ms=round(mic.latency_ms, 1), rate=rate)
        out_idx = {"i": 0, "base": None}

        def play(outdata, frames, time_info, status):
            cb = time.monotonic()
            dac = cb + (time_info.outputBufferDacTime - time_info.currentTime)       # quand le 1er echantillon sort vraiment
            i0 = out_idx["i"]
            t = (i0 + np.arange(frames)) / rate
            sig = np.zeros(frames, dtype=np.float32)
            k0 = max(int((t[0] - WARMUP_S) // GAP_S), 0)
            for k in range(k0, k0 + 2):
                onset_t = WARMUP_S + k * GAP_S
                if k >= kicks:
                    break
                ph = t - onset_t
                mask = ph >= 0.0
                if mask.any():
                    sig[mask] += kick_wave(ph[mask], np).astype(np.float32)
                if t[0] <= onset_t < t[-1] + 1.0 / rate and len(onsets) <= k:
                    onsets.append(dac + (onset_t - t[0]))
            outdata[:] = sig.reshape(-1, 1) if outdata.shape[1] == 1 else np.repeat(sig.reshape(-1, 1), outdata.shape[1], axis=1)
            out_idx["i"] = i0 + frames

        out_info = sd.query_devices(output_device, "output")
        speaker = sd.OutputStream(device=output_device, samplerate=rate, channels=max(1, min(2, int(out_info["max_output_channels"]))),
                                  blocksize=block, dtype="float32", latency="low", callback=play)
        info.update(sortie=out_info["name"], latence_sortie_ms=round(float(speaker.latency) * 1000.0, 1))
        streams = [mic, speaker]
        t_start = None
    extractor = FeatureExtractor(rate)
    an = AudioAnalyzer(ring, extractor)
    detections: list[float] = []
    an.start()
    watcher = threading.Thread(target=_watch_detections, args=(an, stop, detections), daemon=True)
    watcher.start()

    feeder = None
    if not hardware:
        def feed():
            rng = np.random.default_rng(3)
            idx = 0
            t0 = t_start
            for k in range(kicks):
                onsets.append(t0 + WARMUP_S + k * GAP_S)
            while not stop.is_set():
                t = (idx + np.arange(block)) / rate
                sig = rng.standard_normal(block) * 0.003
                ph = (t - WARMUP_S) % GAP_S
                live = t >= WARMUP_S
                sig = sig + np.where(live, kick_wave(ph, np), 0.0)
                idx += block
                wait = t0 + idx / rate - time.monotonic()            # le bloc n'est « livre » qu'une fois complet, comme un callback
                if wait > 0:
                    time.sleep(wait)
                ring.write(sig.astype(np.float32))
        feeder = threading.Thread(target=feed, daemon=True)
        feeder.start()
    else:
        for s_ in streams:
            s_.start()
    t_end = time.monotonic() + 0.3 + WARMUP_S + kicks * GAP_S + 0.6
    log(f"Mesure en cours ({kicks} kicks, ~{t_end - time.monotonic():.0f} s)...")
    try:
        flashes = _render_loop(an, size, fps, t_end, np, moderngl)
    finally:
        stop.set()
        an.stop()
        for s_ in streams:
            try:
                s_.stop()
            except Exception:
                pass
    rows = match(sorted(onsets), detections, flashes)
    result = {"rows": rows, "info": info, "missed": sum(1 for r in rows if r["total_ms"] is None),
              "analyse": summarize([r["analyse_ms"] for r in rows]), "rendu": summarize([r["rendu_ms"] for r in rows]),
              "total": summarize([r["total_ms"] for r in rows])}
    return result


def report(result: dict) -> str:
    lines = [f"Latence audio -> image ({result['info']['mode']}, rendu a {result['info']['fps']} images/s)"]
    for k, v in result["info"].items():
        if k not in ("mode", "fps"):
            lines.append(f"  {k}: {v}")
    lines.append("")
    lines.append(f"{'':>8}{'min':>9}{'mediane':>10}{'p95':>9}{'max':>9}   (ms)")
    for label, key in (("analyse", "analyse"), ("rendu", "rendu"), ("total", "total")):
        s = result[key]
        if s:
            lines.append(f"{label:>8}{s['min']:9.1f}{s['median']:10.1f}{s['p95']:9.1f}{s['max']:9.1f}")
    lines.append(f"kicks mesures: {result['total'].get('n', 0)} / {len(result['rows'])} (manques: {result['missed']})")
    total = result["total"]
    if total:
        verdict = "sous" if total["median"] <= TARGET_MS else "AU-DESSUS de"
        lines.append(f"Mediane {total['median']:.0f} ms : {verdict} la cible de {TARGET_MS:.0f} ms (hors affichage de l'ecran / du videoprojecteur).")
    lines.append("analyse = debut du son -> kick detecte ; rendu = kick -> premiere image blanche lue ; l'affichage n'est pas inclus.")
    return "\n".join(lines)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--materiel", action="store_true", help="joue le kick par la sortie audio et l'ecoute par l'entree (sinon : chaine logicielle seule)")
    ap.add_argument("--entree", help="entree audio (nom ou numero ; defaut : celle de Windows)")
    ap.add_argument("--sortie", help="sortie audio (nom ou numero ; defaut : celle de Windows)")
    ap.add_argument("--kicks", type=int, default=12, help="nombre de kicks (defaut 12)")
    ap.add_argument("--fps", type=int, default=60, help="cadence du rendu simule (defaut 60)")
    ap.add_argument("--json", action="store_true", help="resultat brut en JSON")
    ap.add_argument("--liste", action="store_true", help="liste les peripheriques audio et quitte")
    args = ap.parse_args(argv)
    if args.liste:
        from engine.audio import print_audio_devices
        print_audio_devices()
        return 0

    def dev(v):
        return None if v is None else (int(v) if str(v).isdigit() else v)
    try:
        result = measure(args.kicks, args.materiel, dev(args.entree), dev(args.sortie), args.fps)
    except (RuntimeError, ValueError, OSError) as exc:
        print(f"Mesure impossible: {exc}", file=sys.stderr)
        return 1
    if args.json:
        print(json.dumps(result, indent=2))
    else:
        print(report(result))
    return 0 if result["total"] else 2


if __name__ == "__main__":
    sys.exit(main())
