"""Calibration du retard son -> lumiere de bout en bout, a la camera d'un telephone (lot 1 de la spec synchro son / image).

Ce que l'on mesure : le temps entre le MOMENT OU LE SON EST ENTENDU dans la salle et le moment ou la lumiere apparait sur l'ecran, en
passant par toute la chaine reelle (micro du portable, pilote, analyse, detection du kick, affichage, videoprojecteur). C'est le retard
que l'« avance » du lot 4 devra compenser. Le chiffre est lu sur une video : le telephone enregistre le son et l'image avec la meme horloge.

Deroulement :

  1. `emettre` : le programme joue un clic grave (un kick de test) par le haut-parleur, une fois par seconde ; il l'ecoute par le MICRO
     (la vraie chaine d'analyse) et, des qu'il detecte le kick, allume la fenetre en blanc. Mets la fenetre sur le videoprojecteur
     (--x / --y = coin haut gauche de l'ecran voulu, la fenetre s'y met en plein ecran).
  2. Filme l'ecran ET le haut-parleur dans le meme plan, au ralenti (120 ou 240 images/s : precision de 8 ou 4 ms ; a 30 images/s la
     precision n'est que de ~33 ms), pendant une vingtaine de secondes.
  3. `analyser video.mp4` : lit le son de la video (instants des clics) et la luminance de l'image (instants des flashs), apparie les
     deux et rend le retard median, la dispersion et la valeur recommandee pour l'avance.

    python tools/calibrer_avance.py emettre [--entree "Micro"] [--sortie "Haut-parleurs"] [--x 1920 --y 0] [--clics 20]
    python tools/calibrer_avance.py analyser ma_video.mp4 [--fps 240]
    python tools/calibrer_avance.py liste

Limites (a lire avant de se fier au chiffre) : la fenetre de calibration est une fenetre Tk, pas le rendu OpenGL de l'application (un
ecart d'au plus une image d'ecran, ~16 ms, entre les deux) ; la video d'un telephone peut avoir son propre decalage son / image de
quelques millisecondes ; le chiffre est valable pour CE materiel (portable, micro, projecteur, position du haut-parleur) : refaire la
mesure si l'un change.
"""
from __future__ import annotations

import argparse
import subprocess
import sys
import threading
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
for _p in (ROOT, Path(__file__).resolve().parent):
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))

GAP_S = 1.0                  # un clic par seconde
WARMUP_S = 3.0               # la ligne de base du detecteur de kick a besoin de quelques secondes (voir engine.audio)
FLASH_S = 0.12               # duree du flash blanc
MAX_DELAY_S = 0.6            # un flash plus de 0,6 s apres un clic n'est pas le sien
MIN_CONTRAST = 0.08          # ecart de luminance (0..1) sous lequel l'image n'a pas de flash franc (camera mal placee, ecran trop sombre)


# --- Analyse de la video : fonctions pures (numpy seulement) -------------------------------------------------------------------

def find_flashes(times, luma, min_gap: float = 0.4) -> list[float]:
    """Instants (secondes) ou la luminance moyenne de l'image monte franchement : front montant au milieu entre le bas (10e centile)
    et le haut (99e centile) de la serie. `times` : instant de chaque image ; `luma` : luminance moyenne de chaque image (0..1).
    Rend [] si la serie n'a pas de contraste (aucun flash visible)."""
    import numpy as np
    t = np.asarray(times, dtype=float)
    y = np.asarray(luma, dtype=float)
    if len(y) < 3 or len(t) != len(y):
        return []
    lo, hi = float(np.percentile(y, 10)), float(np.percentile(y, 99))
    if hi - lo < MIN_CONTRAST:
        return []
    thr = lo + 0.5 * (hi - lo)
    above = y > thr
    out: list[float] = []
    for i in np.flatnonzero(above[1:] & ~above[:-1]) + 1:
        if not out or t[i] - out[-1] >= min_gap:
            out.append(float(t[i]))
    return out


def find_clicks(samples, rate: float, min_gap: float = 0.4) -> list[float]:
    """Instants (secondes depuis le premier echantillon) des clics d'une piste audio : front montant de l'enveloppe (moyenne de |x| sur
    2 ms) a 30 % de son plus haut niveau, affine en remontant jusqu'a 10 % du pic local (le debut reel du son, pas l'instant ou il
    devient fort). [] si la piste est silencieuse."""
    import numpy as np
    x = np.abs(np.asarray(samples, dtype=np.float32))
    if len(x) == 0:
        return []
    win = max(1, int(rate * 0.002))
    env = np.convolve(x, np.ones(win, dtype=np.float32) / win, mode="same")
    peak = float(np.percentile(env, 99.9))
    if peak < 1e-4:
        return []
    thr = 0.3 * peak
    above = env > thr
    out: list[float] = []
    last = -1e9
    reach = int(rate * 0.015)
    for i in np.flatnonzero(above[1:] & ~above[:-1]) + 1:
        if i / rate - last < min_gap:
            continue
        local = float(env[i:i + int(rate * 0.05)].max())
        lo = 0.1 * local
        j = i
        while j > 0 and i - j < reach and env[j - 1] > lo:
            j -= 1
        out.append(j / rate)
        last = i / rate
    return out


def match_events(clicks: list[float], flashes: list[float], max_delay: float = MAX_DELAY_S) -> list[dict]:
    """Associe chaque clic au premier flash qui le suit (jusqu'a `max_delay`) : [{"click", "flash", "delay_ms"}] ; flash et delay_ms
    valent None si aucun flash ne suit (clic non detecte par le programme, ou flash non vu par la camera)."""
    rows = []
    for c in clicks:
        f = next((f for f in flashes if c - 0.02 <= f < c + max_delay), None)
        rows.append({"click": c, "flash": f, "delay_ms": None if f is None else (f - c) * 1000.0})
    return rows


def recommend(rows: list[dict], frame_s: float | None = None) -> dict:
    """Resume des retards : n, mediane, min, max, p95 (mesure_latence.summarize) + `advance_ms` (la mediane arrondie a la ms, valeur a
    mettre dans l'avance) + `resolution_ms` (la duree d'une image de la camera : la precision de la lecture)."""
    from mesure_latence import summarize
    s = summarize([r["delay_ms"] for r in rows])
    out = dict(s)
    if s:
        out["advance_ms"] = int(round(s["median"]))
        out["spread_ms"] = s["p95"] - s["min"]
    if frame_s:
        out["resolution_ms"] = frame_s * 1000.0
    out["missed"] = sum(1 for r in rows if r["delay_ms"] is None)
    return out


# --- Lecture de la video (ffmpeg / ffprobe) -------------------------------------------------------------------------------------

def _run(cmd: list[str], text: bool = True):
    return subprocess.run(cmd, capture_output=True, text=text, check=False)


def read_video(path: str, fps: float | None = None, log=print) -> dict:
    """{"times", "luma", "audio", "rate", "frame_s"} d'une video : instant et luminance moyenne de chaque image (image reduite a 16 x 9),
    piste audio mono a 48 kHz decalee sur la meme origine que l'image."""
    import numpy as np
    probe = _run(["ffprobe", "-v", "error", "-select_streams", "v:0", "-show_entries", "frame=best_effort_timestamp_time",
                  "-of", "csv=p=0", path])
    times = []
    for line in probe.stdout.splitlines():
        try:
            times.append(float(line.strip().rstrip(",")))
        except ValueError:
            continue
    if len(times) < 10:
        raise RuntimeError(f"ffprobe ne lit pas d'images dans {path} : {probe.stderr.strip()[:200]}")
    raw = subprocess.run(["ffmpeg", "-v", "error", "-i", path, "-vsync", "passthrough", "-vf", "scale=16:9,format=gray",
                          "-f", "rawvideo", "-"], capture_output=True, check=False).stdout
    frames = np.frombuffer(raw, dtype=np.uint8)
    n = min(len(frames) // 144, len(times))
    luma = frames[:n * 144].reshape(n, 144).mean(axis=1) / 255.0
    times = times[:n]
    start = {}
    for kind in ("a", "v"):
        out = _run(["ffprobe", "-v", "error", "-select_streams", kind + ":0", "-show_entries", "stream=start_time", "-of", "csv=p=0", path])
        try:
            start[kind] = float(out.stdout.strip().split(",")[0])
        except (ValueError, IndexError):
            start[kind] = 0.0
    audio_raw = subprocess.run(["ffmpeg", "-v", "error", "-i", path, "-vn", "-ac", "1", "-ar", "48000", "-f", "f32le", "-"],
                               capture_output=True, check=False).stdout
    audio = np.frombuffer(audio_raw, dtype=np.float32)
    if len(audio) == 0:
        raise RuntimeError("la video n'a pas de piste audio : filme avec le son (c'est lui qui donne l'instant des clics)")
    frame_s = (times[-1] - times[0]) / max(len(times) - 1, 1)
    if fps:
        frame_s = 1.0 / fps
    log(f"  {n} images (~{1.0 / frame_s:.0f} images/s), {len(audio) / 48000.0:.1f} s de son")
    return {"times": times, "luma": luma, "audio": audio, "rate": 48000.0, "frame_s": frame_s, "audio_start": start["a"]}


def analyse_video(path: str, fps: float | None = None, log=print) -> dict:
    v = read_video(path, fps, log)
    clicks = [v["audio_start"] + c for c in find_clicks(v["audio"], v["rate"])]
    flashes = find_flashes(v["times"], v["luma"])
    log(f"  {len(clicks)} clic(s) entendu(s), {len(flashes)} flash(s) vu(s)")
    rows = match_events(clicks, flashes)
    return {"rows": rows, "summary": recommend(rows, v["frame_s"]), "clicks": len(clicks), "flashes": len(flashes)}


def report(result: dict) -> str:
    s = result["summary"]
    if not s.get("n"):
        why = ("aucun flash visible : cadre l'ecran en entier, baisse la lumiere de la piece, ou rapproche la camera" if not result["flashes"]
               else "aucun clic entendu : rapproche le telephone du haut-parleur" if not result["clicks"]
               else "les clics et les flashs ne se rejoignent pas (retard > 0,6 s ou programme qui ne detecte pas le kick)")
        return "Calibration impossible : " + why + "."
    lines = [f"Retard son -> lumiere : mediane {s['median']:.0f} ms (de {s['min']:.0f} a {s['max']:.0f} ms, p95 {s['p95']:.0f}), "
             f"{s['n']} mesure(s), {s['missed']} clic(s) sans flash.",
             f"Precision de lecture : une image de la camera = {s.get('resolution_ms', 0.0):.0f} ms.",
             f"=> AVANCE recommandee : {s['advance_ms']} ms (reglage disponible avec le lot 4 de la spec ; en attendant, c'est le retard que voit le public)."]
    if s["spread_ms"] > 60.0:
        lines.append("Attention : les mesures sont tres dispersees (plus de 60 ms) : le retard n'est pas assez stable pour etre compense "
                     "par une avance fixe (charge du portable ? pilote en mode partage ?). Refais la mesure sans autre programme ouvert.")
    if s.get("resolution_ms", 0.0) > 20.0:
        lines.append("La camera filme a moins de 50 images/s : le chiffre est approximatif. Reprends en ralenti (120 ou 240 images/s).")
    return "\n".join(lines)


# --- Emission : clic par le haut-parleur, flash a la detection ------------------------------------------------------------------

def emettre(args) -> int:
    import tkinter as tk
    import mesure_latence as ml
    from engine.audio import AudioAnalyzer, FeatureExtractor, RingBuffer, SoundDeviceInput
    from engine.deps import np, sd
    if np is None or sd is None:
        print("numpy et sounddevice sont necessaires (pip install -r requirements-gl.txt)", file=sys.stderr)
        return 1
    ring = RingBuffer()
    mic = SoundDeviceInput(args.entree, ring)
    rate = mic.rate
    an = AudioAnalyzer(ring, FeatureExtractor(rate))
    state = {"i": 0}
    count = args.clics

    def play(outdata, frames, time_info, status):
        i0 = state["i"]
        t = (i0 + np.arange(frames)) / rate
        sig = np.zeros(frames, dtype=np.float32)
        for k in range(max(int((t[0] - WARMUP_S) // GAP_S), 0), max(int((t[-1] - WARMUP_S) // GAP_S), 0) + 1):
            if k >= count:
                break
            ph = t - (WARMUP_S + k * GAP_S)
            mask = ph >= 0.0
            if mask.any():
                sig[mask] += ml.kick_wave(ph[mask], np).astype(np.float32)
        outdata[:] = sig.reshape(-1, 1) if outdata.shape[1] == 1 else np.repeat(sig.reshape(-1, 1), outdata.shape[1], axis=1)
        state["i"] = i0 + frames

    out_info = sd.query_devices(args.sortie, "output")
    speaker = sd.OutputStream(device=args.sortie, samplerate=rate, channels=max(1, min(2, int(out_info["max_output_channels"]))),
                              blocksize=512, dtype="float32", latency="low", callback=play)
    root = tk.Tk()
    root.title("Calibration de l'avance - Echap pour arreter")
    root.configure(bg="black")
    root.geometry(f"+{args.x}+{args.y}")
    root.update_idletasks()
    root.attributes("-fullscreen", True)
    root.bind("<Escape>", lambda _e: root.destroy())
    seen = {"beats": 0, "off": None, "end": time.monotonic() + WARMUP_S + count * GAP_S + 2.0}

    def tick():
        now = time.monotonic()
        if an.beats != seen["beats"]:
            seen["beats"] = an.beats
            root.configure(bg="white")
            root.update_idletasks()
            seen["off"] = now + FLASH_S
        elif seen["off"] is not None and now >= seen["off"]:
            root.configure(bg="black")
            seen["off"] = None
        if now >= seen["end"]:
            root.destroy()
            return
        root.after(1, tick)

    print(f"Entree : {mic.name} (latence annoncee {mic.latency_ms:.0f} ms) ; sortie : {out_info['name']}.")
    print(f"{count} clics, un par seconde, apres {WARMUP_S:.0f} s de chauffe. Filme l'ecran ET le haut-parleur, en ralenti (120 ou 240 images/s).")
    an.start()
    mic.start()
    speaker.start()
    root.after(1, tick)
    try:
        root.mainloop()
    finally:
        for s in (speaker, mic):
            try:
                s.stop()
            except Exception:
                pass
        an.stop()
    detected = an.beats
    print(f"{detected} kick(s) detecte(s) par le programme sur {count} clic(s) emis.")
    print("Maintenant : python tools/calibrer_avance.py analyser <ta_video.mp4>")
    return 0 if detected else 2


def lister() -> int:
    from engine.deps import sd
    if sd is None:
        print("sounddevice absent", file=sys.stderr)
        return 1
    print(sd.query_devices())
    return 0


def main(argv=None) -> int:
    p = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    sub = p.add_subparsers(dest="cmd", required=True)
    e = sub.add_parser("emettre", help="joue les clics et allume la fenetre a la detection")
    e.add_argument("--entree", default=None, help="micro (nom ou numero, voir `liste`) ; defaut = entree par defaut de Windows")
    e.add_argument("--sortie", default=None, help="haut-parleur (nom ou numero) ; defaut = sortie par defaut")
    e.add_argument("--x", type=int, default=0, help="coin haut gauche de l'ecran ou mettre la fenetre (le videoprojecteur)")
    e.add_argument("--y", type=int, default=0)
    e.add_argument("--clics", type=int, default=20)
    a = sub.add_parser("analyser", help="lit la video du telephone et rend le retard")
    a.add_argument("video")
    a.add_argument("--fps", type=float, default=None, help="images/s reelles de la camera si la video a des horodatages irreguliers")
    sub.add_parser("liste", help="peripheriques audio")
    args = p.parse_args(argv)
    if args.cmd == "emettre":
        return emettre(args)
    if args.cmd == "liste":
        return lister()
    try:
        result = analyse_video(args.video, args.fps)
    except (RuntimeError, FileNotFoundError) as exc:
        print(f"Erreur : {exc}", file=sys.stderr)
        return 1
    print(report(result))
    return 0 if result["summary"].get("n") else 2


if __name__ == "__main__":
    sys.exit(main())
