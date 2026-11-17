"""Extraits automatiques (issue #148) : analyse du son d'un enregistrement (ffmpeg + numpy) -> serie d'energie pour `core.highlights`.

Fonction pure d'un fichier : aucune dependance a la session, a la GUI ni au rendu (un fichier absent ou illisible rend une serie vide, jamais une exception).
"""
from __future__ import annotations

import subprocess

from core import highlights as hl
from engine.deps import np

RATE = 22050                    # le son est decode en mono a cette frequence (assez pour les basses et le niveau, leger pour un long enregistrement)
BASS_WINDOW = int(round(RATE / 300))        # moyenne glissante de ~73 echantillons : passe-bas vers 150 Hz (premier zero a 300 Hz)
FLUX_STEP_S = 0.02              # pas du flux d'attaques des basses (pour le BPM : le pas de 250 ms est trop grossier)
BPM_MAX_STEPS = 9000            # le BPM est estime sur les 3 minutes les plus fortes au plus (autocorrelation en Python pur)
_NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)


def _decode(path: str, ffmpeg: str):
    try:
        done = subprocess.run([ffmpeg, "-v", "error", "-i", path, "-map", "0:a:0", "-ac", "1", "-ar", str(RATE), "-f", "f32le", "-"],
                              capture_output=True, timeout=900, creationflags=_NO_WINDOW)
    except (OSError, subprocess.SubprocessError):
        return None
    if done.returncode != 0 or len(done.stdout) < 8:
        return None
    return np.frombuffer(done.stdout[:len(done.stdout) // 4 * 4], dtype="<f4")


def energy_series(path: str, ffmpeg: str, step_s: float = 0.25):
    """`(energie, bpm, duree_s)` : `energie` = liste de `(niveau, basses)` normalises 0..1 toutes les `step_s` s ; `bpm` = tempo estime ou None ;
    `duree_s` = duree du son. Fichier absent, sans audio ou illisible : `([], None, 0.0)`."""
    if np is None:
        return [], None, 0.0
    x = _decode(path, ffmpeg)
    if x is None:
        return [], None, 0.0
    duration = len(x) / RATE
    win = max(1, int(round(step_s * RATE)))
    n = len(x) // win
    if n < 1:
        return [], None, duration
    c = np.concatenate(([0.0], np.cumsum(x.astype(np.float64))))
    low = (c[BASS_WINDOW:] - c[:-BASS_WINDOW]) / BASS_WINDOW
    low = np.concatenate((low, np.zeros(len(x) - len(low))))
    frames, lows = x[:n * win].astype(np.float64).reshape(n, win), low[:n * win].reshape(n, win)
    level = np.sqrt((frames * frames).mean(axis=1))
    bass = np.sqrt((lows * lows).mean(axis=1))
    energy = list(zip(hl.normalize_series(level.tolist()), hl.normalize_series(bass.tolist())))
    # --- BPM : flux d'attaques des basses a 20 ms
    hop = max(1, int(round(FLUX_STEP_S * RATE)))
    m = len(low) // hop
    bpm = None
    if m >= 16:
        env = np.sqrt((low[:m * hop].reshape(m, hop) ** 2).mean(axis=1))
        flux = np.maximum(0.0, np.diff(env))
        if len(flux) > BPM_MAX_STEPS:
            cs = np.concatenate(([0.0], np.cumsum(flux)))
            sums = cs[BPM_MAX_STEPS:] - cs[:-BPM_MAX_STEPS]
            j = int(np.argmax(sums))
            flux = flux[j:j + BPM_MAX_STEPS]
        bpm = hl.estimate_bpm(flux.tolist(), FLUX_STEP_S)
    return energy, bpm, duration
