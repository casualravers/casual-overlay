#!/usr/bin/env python3
"""Verifications hors materiel de audio2wave_gl.py (aucun micro, projecteur ni ffmpeg).

    python check_gl.py

  1. lecteur de frames : faux producteur livrant par blocs de 4 Ko, aucune frame melangee,
     frame partielle de fin de flux jetee, latest-wins;
  2. features : salves de sinus 60 Hz -> un beat par salve, aucun faux positif sur bruit
     constant, silence = niveaux nuls, latence de detection mesuree;
  3. rendu : une frame en contexte moderngl standalone -> PNG non vide, et image neutre
     (identique a la video) quand l'audio est coupe.
"""

from __future__ import annotations

import sys
import tempfile
import time
from pathlib import Path

import audio2wave_gl as gl

gl.require_deps()
np = gl.np

failures: list[str] = []


def check(ok: bool, label: str, detail: str = "") -> None:
    print(f"  [{'OK' if ok else 'ECHEC'}] {label}{(' - ' + detail) if detail else ''}")
    if not ok:
        failures.append(label)


# --- 1. lecteur de frames ------------------------------------------------------------

class FakeProducer:
    """Flux de frames dont chaque octet vaut le numero de frame; livre par petits blocs."""

    def __init__(self, frame_size: int, n_frames: int, extra: int = 0, block: int = 4096):
        self.data = b"".join(bytes([i % 251 + 1]) * frame_size for i in range(n_frames))
        self.data += bytes([250]) * extra          # frame partielle de fin de flux
        self.off = 0
        self.block = block

    def read(self, n: int) -> bytes:
        chunk = self.data[self.off:self.off + min(n, self.block)]
        self.off += len(chunk)
        return chunk


def check_reader() -> None:
    print("Lecteur de frames")
    frame_size = 30 * 20 * 3          # volontairement pas multiple de 4096
    seen: list[bytes] = []

    def on_frame(mv):
        seen.append(bytes(mv))

    reader = gl.FrameReader(FakeProducer(frame_size, 40, extra=1000), frame_size, on_frame)
    reader.start()
    reader.join(5)
    mixed = [i for i, f in enumerate(seen) if len(set(f)) != 1]
    check(reader.ended, "fin de flux detectee")
    check(len(seen) == 40, "40 frames entieres publiees (la partielle est jetee)", f"{len(seen)} recues")
    check(not mixed, "aucune frame ne melange des octets de deux frames")
    check([f[0] for f in seen] == [i % 251 + 1 for i in range(40)], "ordre des frames conserve")
    last = reader.take()
    check(last is not None and len(last) == frame_size and len(set(last)) == 1,
          "latest-wins: la derniere frame entiere est disponible")
    check(reader.take() is None, "pas de nouvelle frame apres consommation")


# --- 2. features audio ---------------------------------------------------------------

def run_signal(signal, rate: int, hop: int = 512, sensitivity: float = 1.0):
    """Fait defiler le signal comme le fil d'analyse: (temps des beats, niveaux finaux)."""
    ex = gl.FeatureExtractor(rate, sensitivity=sensitivity)
    ring = gl.RingBuffer()
    beats, levels = [], {}
    for i in range(0, len(signal) - hop, hop):
        ring.write(signal[i:i + hop])
        t = (i + hop) / rate
        levels, beat = ex.process(ring.latest(ex.win_size), hop / rate, t)
        if beat:
            beats.append(t)
    return beats, levels


def kick_bursts(rate: int, seconds: float, amp: float = 0.5, noise: float = 0.002):
    rng = np.random.default_rng(5)
    n = int(rate * seconds)
    t = np.arange(n) / rate
    ph = t % 0.5
    env = np.where(ph < 0.1, np.sin(np.pi * ph / 0.1), 0.0)        # salve de 100 ms toutes les 500 ms
    return (amp * env * np.sin(2 * np.pi * 60 * t) + noise * rng.standard_normal(n)).astype(np.float32)


def check_features() -> None:
    print("Features audio")
    for rate in (48000, 44100):
        secs = 20
        beats, _ = run_signal(kick_bursts(rate, secs), rate)
        bursts = int(secs / 0.5)
        # les 0.5 premieres secondes servent de chauffe: la premiere salve peut etre manquee
        check(bursts - 1 <= len(beats) <= bursts, f"{rate} Hz: un beat par salve",
              f"{len(beats)} beats pour {bursts} salves")
        gaps = np.diff(beats)
        check(len(gaps) > 0 and abs(float(np.median(gaps)) - 0.5) < 0.02, f"{rate} Hz: periode ~500 ms",
              f"mediane {np.median(gaps) * 1000:.0f} ms")

    # Robustesse au niveau du micro: meme salve, 30 dB plus faible.
    beats_quiet, _ = run_signal(kick_bursts(48000, 20, amp=0.5 * 10 ** (-30 / 20), noise=0.00002), 48000)
    check(len(beats_quiet) >= 18, "niveau micro -30 dB: kicks toujours detectes (auto-gain)",
          f"{len(beats_quiet)} beats")

    # Kick pose sur une basse continue (55 Hz) : limite connue a sensibilite 1.0, la
    # sensibilite (touches haut/bas) doit permettre de le rattraper.
    rng2 = np.random.default_rng(4)
    tt = np.arange(48000 * 20) / 48000
    env = np.where((tt % 0.5) < 0.1, np.sin(np.pi * (tt % 0.5) / 0.1), 0.0)
    onbass = (0.15 * np.sin(2 * np.pi * 55 * tt) + 0.3 * env * np.sin(2 * np.pi * 60 * tt)
              + 0.002 * rng2.standard_normal(len(tt))).astype(np.float32)
    beats_hi, _ = run_signal(onbass, 48000, sensitivity=2.5)
    check(len(beats_hi) >= 30, "kick sur basse continue: detecte avec sensibilite 2.5", f"{len(beats_hi)}/40")

    # Faux positifs.
    rng = np.random.default_rng(9)
    for amp in (0.05, 0.3):
        noise = (amp * rng.standard_normal(48000 * 60)).astype(np.float32)
        beats, _ = run_signal(noise, 48000)
        # 0.3 = bruit quasi ecrete, hors cas realiste: on tolere 1 declenchement par minute.
        check(len(beats) <= (0 if amp < 0.1 else 1), f"bruit blanc constant (amplitude {amp}) sur 60 s: "
              "pas de faux positif", f"{len(beats)} beats")
    tone = (0.3 * np.sin(2 * np.pi * 60 * np.arange(48000 * 20) / 48000)).astype(np.float32)
    beats, _ = run_signal(tone, 48000)
    check(len(beats) == 0, "sinus 60 Hz continu: aucun faux positif", f"{len(beats)} beats")

    # Silence: niveaux nuls (sinon l'auto-gain amplifierait le bruit du micro).
    quiet = (0.0001 * rng.standard_normal(48000 * 10)).astype(np.float32)
    beats, levels = run_signal(quiet, 48000)
    check(not beats and all(levels[k] < 0.05 for k in ("bass", "mid", "high", "rms")),
          "silence: aucun beat, niveaux ~0", f"{ {k: round(levels[k], 3) for k in ('bass', 'mid', 'high', 'rms')} }")

    # Bandes: un sinus 60 Hz alimente les basses, un sinus 5 kHz les aigus.
    for freq, band in ((60, "bass"), (500, "mid"), (5000, "high")):
        s = (0.3 * np.sin(2 * np.pi * freq * np.arange(48000 * 3) / 48000)).astype(np.float32)
        _, lv = run_signal(s, 48000)
        others = [lv[b] for b in ("bass", "mid", "high") if b != band]
        check(lv[band] > 0.8 and max(others) < 0.2, f"sinus {freq} Hz -> bande {band} seule",
              f"{band}={lv[band]:.2f} autres<={max(others):.2f}")

    # Latence de detection (logicielle): debut de salve -> beat.
    rate = 48000
    sig = gl.synth_audio_block(0, rate * 8, rate, 120.0)          # vrai kick: attaque instantanee
    beats, _ = run_signal(sig, rate)
    lat = [(b % 0.5) * 1000 for b in beats[1:]]
    print(f"  latence logicielle kick -> beat: mediane {np.median(lat):.0f} ms "
          f"(max {max(lat):.0f} ms; hop 10,7 ms + fenetre 2048, hors capture et rendu)")
    check(len(beats) >= 14, "synthese audio (kick 120 BPM): kicks detectes", f"{len(beats)}/16")
    check(np.median(lat) < 50, "latence de detection < 50 ms")

    # Ring: latest() apres bouclage.
    ring = gl.RingBuffer(size=1024)
    ring.write(np.arange(700, dtype=np.float32))
    ring.write(np.arange(700, 1500, dtype=np.float32))
    out = ring.latest(300)
    check(np.array_equal(out, np.arange(1200, 1500, dtype=np.float32)), "RingBuffer: ordre correct apres bouclage")


# --- 3. rendu standalone ---------------------------------------------------------------

def read_target(ctx, fbo, size):
    data = fbo.read(components=3)
    return np.frombuffer(data, dtype=np.uint8).reshape(size[1], size[0], 3)[::-1]   # haut en premier


def check_render() -> None:
    print("Rendu OpenGL (contexte standalone)")
    try:
        ctx = gl.moderngl.create_standalone_context()
    except Exception as exc:
        check(False, "contexte OpenGL standalone", str(exc))
        return
    size = (640, 360)
    stream = gl.SyntheticVideoStream(size[0], size[1], fps=1000)
    frame = b"".join(stream.read(1 << 20) for _ in range(1))
    while len(frame) < size[0] * size[1] * 3:
        frame += stream.read(size[0] * size[1] * 3 - len(frame))
    video = np.frombuffer(frame, np.uint8).reshape(size[1], size[0], 3)

    logo_path = gl.DEFAULT_LOGO
    logo = gl.load_logo(logo_path) if logo_path.is_file() else None
    target_tex = ctx.texture(size, 4)
    fbo = ctx.framebuffer(color_attachments=[target_tex])

    # Image neutre au repos, sans logo.
    r = gl.Renderer(ctx, size, None)
    r.video_tex.write(frame, alignment=1)
    idle = {"bass": 0.0, "mid": 0.0, "high": 0.0, "rms": 0.0, "beat": 0.0, "since_beat": 10.0}
    r.draw(fbo, size, idle, gl.load_params() | {"fx_on": [1] * 5}, 1.0)
    out = read_target(ctx, fbo, size)
    diff = np.abs(out.astype(int) - video.astype(int))
    check(diff.mean() < 1.0 and diff.max() <= 3, "audio coupe: image identique a la video (aucun effet)",
          f"ecart moyen {diff.mean():.2f}, max {diff.max()}")

    # Effets en plein kick: l'image doit changer sans etre noire ni saturee.
    loud = {"bass": 1.0, "mid": 0.6, "high": 0.9, "rms": 0.8, "beat": 1.0, "since_beat": 0.05}
    params = {"fx_on": [1] * 5, "fx_int": [1.0] * 5, "master": 1.0, "sensitivity": 1.0}
    r.draw(fbo, size, loud, params, 1.0)
    out2 = read_target(ctx, fbo, size)
    change = np.abs(out2.astype(int) - video.astype(int)).mean()
    check(change > 1.0, "kick: l'image est deformee", f"ecart moyen {change:.1f}")
    check(20 < out2.mean() < 235, "kick: image ni noire ni saturee", f"moyenne {out2.mean():.0f}")

    # Chaque effet est visible isolement.
    for i, name in enumerate(gl.FX_NAMES[:4]):
        p = dict(params, fx_on=[1 if j == i else 0 for j in range(5)])
        state = dict(loud, since_beat=0.3, beat=0.22) if name == "ripple" else loud
        r.draw(fbo, size, state, p, 1.0)
        d = np.abs(read_target(ctx, fbo, size).astype(int) - video.astype(int)).mean()
        check(d > 0.3, f"effet {name} visible isolement", f"ecart moyen {d:.2f}")

    # Logo.
    if logo is not None:
        r2 = gl.Renderer(ctx, size, logo, 0.4, (0.5, 0.5))
        r2.video_tex.write(frame, alignment=1)
        r2.draw(fbo, size, idle, params, 1.0)
        out3 = read_target(ctx, fbo, size)
        cx, cy, hw, hh = gl.logo_layout(size[0], size[1], r2.logo_aspect, 0.4, (0.5, 0.5))
        x0, x1 = int((cx - hw) * size[0]), int((cx + hw) * size[0])
        y0, y1 = int((cy - hh) * size[1]), int((cy + hh) * size[1])
        inside = np.abs(out3[y0:y1, x0:x1].astype(int) - video[y0:y1, x0:x1].astype(int)).mean()
        outside = np.abs(out3[:y0].astype(int) - video[:y0].astype(int)).mean() if y0 > 0 else 0.0
        check(inside > 2.0, "logo incruste dans son rectangle", f"ecart moyen {inside:.1f}")
        check(outside < 1.0, "video intacte hors du logo", f"ecart moyen {outside:.2f}")
        out_png = Path(tempfile.gettempdir()) / "casual_overlay_check.png"
        r2.draw(fbo, size, loud, params, 1.0)
        gl.PIL_Image.fromarray(np.ascontiguousarray(read_target(ctx, fbo, size))).save(out_png)
        print(f"  rendu de controle (kick, logo, effets): {out_png}")
        check(out_png.stat().st_size > 5000, "PNG de rendu non vide")

        # Le logo ne sort jamais du cadre, meme en pleine pulsation et en bord de cadre.
        ok = True
        for pos in ((0.5, 0.5), (0.05, 0.05), (0.98, 0.5), (0.5, 0.99)):
            for scale in (0.2, 0.6, 0.95):
                for pulse in (0.0, 0.12, 0.5):
                    cx, cy, hw, hh = gl.logo_layout(1920, 1080, r2.logo_aspect, scale, pos, pulse, (0.02, -0.02))
                    ok &= cx - hw >= -1e-6 and cx + hw <= 1 + 1e-6 and cy - hh >= -1e-6 and cy + hh <= 1 + 1e-6
        check(ok, "logo_layout: jamais hors cadre (pulse, jitter, bords)")

    # Rechargement de shader: une erreur garde l'ancien programme.
    before = r.post_prog
    bad = Path(tempfile.mkdtemp())
    for f in gl.SHADER_DIR.iterdir():
        (bad / f.name).write_text(f.read_text(encoding="utf-8"), encoding="utf-8")
    (bad / "post.frag").write_text("#version 330\nvoid main() { erreur }", encoding="utf-8")
    r.shader_dir = bad
    try:
        r.reload_shaders()
        check(False, "shader invalide refuse")
    except gl.ShaderError:
        check(r.post_prog is before, "shader invalide: ShaderError, ancien programme conserve")


def check_params() -> None:
    print("Reglages (touche P)")
    original = gl.PARAMS_PATH
    gl.PARAMS_PATH = Path(tempfile.mkdtemp()) / "sous" / "gl_params.json"
    try:
        check(gl.load_params() == gl.DEFAULT_PARAMS, "sans fichier: valeurs par defaut")
        params = gl.load_params()
        params["fx_on"][3] = 0
        params["master"] = 1.4
        params["sensitivity"] = 2.0
        gl.save_params(params)
        check(gl.load_params() == params, "sauvegarde puis rechargement identiques")
        gl.PARAMS_PATH.write_text("{ pas du json", encoding="utf-8")
        check(gl.load_params() == gl.DEFAULT_PARAMS, "fichier corrompu: retour aux valeurs par defaut")
    finally:
        gl.PARAMS_PATH = original


def main() -> None:
    check_reader()
    check_features()
    check_params()
    check_render()
    print()
    if failures:
        print(f"{len(failures)} verification(s) en echec: {', '.join(failures)}")
        sys.exit(1)
    print("Toutes les verifications passent.")


if __name__ == "__main__":
    main()
