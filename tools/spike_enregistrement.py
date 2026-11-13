#!/usr/bin/env python3
"""SPIKE JETABLE (issue #140, tache 1) : ne se fusionne pas.

Question : le rendu hors ecran en 1080x1920 + la relecture + l'encodage MP4 tiennent-ils a 30 puis 60 images/s sur ce GPU ?
Mesure, pour deux profils d'effets (leger / lourd) : temps de rendu, de relecture, d'ecriture, CPU de ffmpeg, debit libre,
et, en cadence imposee, le nombre d'images en retard (= images dupliquees dans la vraie fonction).

    python tools/spike_enregistrement.py --out <dossier> [--seconds 10] [--encoder libx264]
"""
from __future__ import annotations

import argparse
import math
import queue
import shutil
import subprocess
import sys
import threading
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path[:0] = [str(ROOT)]
import audio2wave_gl as gl  # noqa: E402

gl.require_deps()
np = gl.np
SIZE = (1080, 1920)


def cpu_seconds(proc) -> float:
    """Temps CPU (utilisateur + noyau) d'un processus Windows deja termine ou non."""
    import ctypes
    from ctypes import wintypes
    c, e, k, u = (wintypes.FILETIME() for _ in range(4))
    ctypes.windll.kernel32.GetProcessTimes(wintypes.HANDLE(int(proc._handle)), ctypes.byref(c), ctypes.byref(e), ctypes.byref(k), ctypes.byref(u))
    val = lambda f: ((f.dwHighDateTime << 32) | f.dwLowDateTime) * 1e-7
    return val(k) + val(u)


def audio_state(t: float) -> dict:
    """Etat audio synthetique : un kick toutes les 0,5 s, basses et aigus qui respirent."""
    since = t % 0.5
    return {"bass": 0.55 + 0.4 * math.sin(t * 1.3), "mid": 0.4 + 0.2 * math.sin(t * 2.1), "high": 0.35 + 0.3 * math.sin(t * 3.7),
            "rms": 0.5, "beat": math.exp(-since / 0.2), "since_beat": since, "beats": int(t / 0.5)}


def profile_params(name: str) -> dict:
    p = dict(gl.DEFAULT_PARAMS)
    p.update({"bg_mode": "pattern", "bg_palette": "duo", "auto_phase": 0})
    p.update({"holo_on": 0.0, "cell_on": 0.0, "melt_on": 0.0})
    if name == "halo":
        p.update({"holo_on": 1.0})
    elif name == "cellules":
        p.update({"cell_on": 1.0, "cell_amount": 0.65})
    elif name == "fonte":
        p.update({"melt_on": 1.0})
    elif name == "compo":
        p.update({"comp_trail": 0.5, "comp_pix": 0.2})
    elif name == "lourd":                       # effets couteux allumes, composition globale en plus
        p.update({"holo_on": 1.0, "cell_on": 1.0, "cell_amount": 0.65, "melt_on": 1.0, "comp_trail": 0.5, "comp_pix": 0.2})
    return p


def make_renderer(ctx, size):
    logo = gl.load_logo(gl.DEFAULT_LOGO) if gl.DEFAULT_LOGO.is_file() else None
    return gl.Renderer(ctx, size, logo, str(gl.DEFAULT_LOGO)), logo


def encoder_args(encoder: str) -> list[str]:
    if encoder == "libx264":
        return ["-c:v", "libx264", "-preset", "veryfast", "-crf", "17"]
    return ["-c:v", encoder]


def run_case(ctx, profile: str, fps: int, seconds: float, encoder: str, paced: bool, out: Path, ffmpeg: str) -> dict:
    rend, _ = make_renderer(ctx, SIZE)
    params = profile_params(profile)
    tex = ctx.texture(SIZE, 4)
    fbo = ctx.framebuffer(color_attachments=[tex])
    frame_bytes = SIZE[0] * SIZE[1] * 3
    bufs = [ctx.buffer(reserve=frame_bytes) for _ in range(2)]
    name = f"{profile}_{fps}_{'cadence' if paced else 'libre'}.mp4"
    dest = out / name
    cmd = [ffmpeg, "-y", "-loglevel", "error", "-f", "rawvideo", "-pix_fmt", "rgb24", "-s", f"{SIZE[0]}x{SIZE[1]}", "-r", str(fps), "-i", "-",
           "-vf", "vflip", *encoder_args(encoder), "-pix_fmt", "yuv420p", "-g", str(fps), str(dest)]
    proc = subprocess.Popen(cmd, stdin=subprocess.PIPE, stderr=subprocess.PIPE)
    q: queue.Queue = queue.Queue(maxsize=6)
    stats = {"write_ms": 0.0, "full": 0}

    def writer():
        while True:
            item = q.get()
            if item is None:
                return
            t0 = time.perf_counter()
            try:
                proc.stdin.write(item)
            except OSError:
                return
            stats["write_ms"] += (time.perf_counter() - t0) * 1000

    th = threading.Thread(target=writer, daemon=True)
    th.start()
    n = int(seconds * fps)
    draw_ms, read_ms, put_ms, late = [], [], [], 0
    cpu0 = time.process_time()
    t_start = time.perf_counter()
    prev = None
    for i in range(n):
        if paced:
            deadline = t_start + i / fps
            delay = deadline - time.perf_counter()
            if delay > 0:
                time.sleep(delay)
            elif -delay > 1.0 / fps:
                late += 1
        t0 = time.perf_counter()
        rend.draw(fbo, SIZE, audio_state(i / fps), params, i / fps)
        ctx.finish()
        t1 = time.perf_counter()
        fbo.read_into(bufs[i % 2], components=3, alignment=1)         # lecture asynchrone vers un tampon GL
        t2 = time.perf_counter()
        if prev is not None:
            data = bufs[prev].read()                                   # l'image PRECEDENTE : sa copie a eu une image de rendu pour finir
            try:
                q.put(data, timeout=2.0)
            except queue.Full:
                stats["full"] += 1
        t3 = time.perf_counter()
        draw_ms.append((t1 - t0) * 1000)
        read_ms.append((t2 - t1) * 1000)
        put_ms.append((t3 - t2) * 1000)
        prev = i % 2
    if prev is not None:
        q.put(bufs[prev].read())
    q.put(None)
    th.join(timeout=30)
    wall = time.perf_counter() - t_start
    own_cpu = time.process_time() - cpu0
    try:
        proc.stdin.close()
    except OSError:
        pass
    proc.wait(timeout=60)
    err = proc.stderr.read().decode("utf-8", "replace").strip()
    res = {"profil": profile, "fps": fps, "cadence": paced, "images": n, "duree_s": wall, "fps_reel": n / wall,
           "rendu_ms": float(np.mean(draw_ms)), "rendu_p95": float(np.percentile(draw_ms, 95)),
           "lecture_ms": float(np.mean(read_ms)), "recup_ms": float(np.mean(put_ms)), "ecriture_ms": stats["write_ms"] / n,
           "file_pleine": stats["full"], "en_retard": late, "cpu_python_pct": 100 * own_cpu / wall,
           "cpu_ffmpeg_pct": 100 * cpu_seconds(proc) / wall, "fichier": str(dest), "taille_mo": dest.stat().st_size / 1e6 if dest.exists() else 0,
           "erreur": err[-300:]}
    rend.release() if hasattr(rend, "release") else None
    return res


def resolution_check(ctx, profile: str) -> float:
    """Meme image rendue en 1080x1920 puis reduite de moitie, comparee a un rendu direct en 540x960 : l'ecart moyen dit si les effets
    dependants de la taille en pixels (pixelisation, halo, grain) se comportent pareil."""
    params = profile_params(profile)
    outs = []
    for size in (SIZE, (SIZE[0] // 2, SIZE[1] // 2)):
        rend, _ = make_renderer(ctx, size)
        tex = ctx.texture(size, 4)
        fbo = ctx.framebuffer(color_attachments=[tex])
        rend.draw(fbo, size, audio_state(1.0), params, 1.0)
        rend.draw(fbo, size, audio_state(1.2), params, 1.2)
        img = np.frombuffer(fbo.read(components=3, alignment=1), np.uint8).reshape(size[1], size[0], 3).astype(float)
        outs.append(img)
    big, small = outs
    down = big.reshape(small.shape[0], 2, small.shape[1], 2, 3).mean(axis=(1, 3))
    return float(np.abs(down - small).mean())


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", required=True)
    ap.add_argument("--seconds", type=float, default=10.0)
    ap.add_argument("--encoder", default="libx264")
    ap.add_argument("--profils", default="leger,lourd")
    ap.add_argument("--fps", default="30,60")
    ap.add_argument("--size", default="1080x1920")
    ap.add_argument("--modes", default="libre,cadence")
    args = ap.parse_args()
    global SIZE
    SIZE = tuple(int(v) for v in args.size.split("x"))
    ffmpeg = shutil.which("ffmpeg")
    if not ffmpeg:
        sys.exit("ffmpeg introuvable")
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    ctx = gl.moderngl.create_standalone_context()
    print("GPU :", ctx.info.get("GL_RENDERER"), "| encodeur :", args.encoder)
    for profile in args.profils.split(","):
        print(f"ecart de resolution (profil {profile}, 540x960 direct vs 1080x1920 reduit) : {resolution_check(ctx, profile):.2f} /255")
    print(f"{'profil':7} {'fps':>3} {'mode':8} {'reel':>6} {'rendu':>7} {'p95':>6} {'lect':>5} {'recup':>6} {'ecrit':>6} {'pleine':>6} {'retard':>6} {'cpuPy':>6} {'cpuFF':>6} {'Mo':>5}")
    for profile in args.profils.split(","):
        for fps in [int(x) for x in args.fps.split(",")]:
            for paced in [m == "cadence" for m in args.modes.split(",")]:
                r = run_case(ctx, profile, fps, args.seconds, args.encoder, paced, out, ffmpeg)
                print(f"{r['profil']:7} {r['fps']:>3} {'cadence' if paced else 'libre':8} {r['fps_reel']:6.1f} {r['rendu_ms']:7.1f} {r['rendu_p95']:6.1f} "
                      f"{r['lecture_ms']:5.1f} {r['recup_ms']:6.1f} {r['ecriture_ms']:6.1f} {r['file_pleine']:6d} {r['en_retard']:6d} "
                      f"{r['cpu_python_pct']:5.0f}% {r['cpu_ffmpeg_pct']:5.0f}% {r['taille_mo']:5.1f}" + (f"  ERREUR: {r['erreur']}" if r["erreur"] else ""))


if __name__ == "__main__":
    main()
