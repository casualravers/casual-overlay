"""Extraits automatiques des enregistrements (issue #148) : choix des moments forts a partir de la serie d'energie du son ENREGISTRE.

Python pur (stdlib seulement). L'analyse du fichier (decodage, RMS, basses) est dans `engine/highlights_audio.py` ; ici on ne manipule que des series :
  * `energy` : liste de `(niveau, basses)` toutes les `step_s` secondes, valeurs 0..1 deja normalisees (`normalize_series`) ;
  * `pick_clips` rend jusqu'a `count_max` extraits `Clip`, du meilleur au moins bon, sans chevauchement.
Un **drop** = un saut des basses apres un creux (moyenne des ~2 s qui suivent comparee a celle des ~4 s qui precedent) : extrait de 8 s avant a 12 s apres.
Un **pic** = la fenetre de 20 s la plus forte, prise en complement (seulement s'il reste de la place, et loin des drops deja choisis : meme passage du morceau).
Avec un `bpm`, debut et fin sont ramenes sur une grille de temps ancree sur le drop (un drop tombe sur un temps).
"""
from __future__ import annotations

import math
import os
from typing import NamedTuple

HISTORY_S = 4.0            # un drop : moyenne des basses sur ces secondes AVANT l'instant ...
AFTER_S = 2.0              # ... comparee a celle de ces secondes APRES
LULL_MAX = 0.4             # le « creux » : basses d'avant au plus a ce niveau normalise
JUMP_MIN = 0.35            # saut minimal des basses (0..1) pour parler de drop
PIC_S = 20.0               # fenetre d'un pic
PIC_MIN = 0.5              # energie moyenne minimale d'un pic (un morceau calme ne donne pas de « moment fort »)
PIC_APART_S = 15.0         # un pic est pris au moins a cette distance d'un extrait deja choisi (pas le prolongement d'un drop)
MARGIN_S = 1.0             # marge avec le debut et la fin de l'enregistrement
GAP_S = 2.0                # ecart minimal entre deux extraits
FLAT_STD = 0.03            # ecart-type en dessous duquel l'enregistrement est « plat » (rien a choisir)
HASHTAGS = "#vj #visuals #livemusic"


class Clip(NamedTuple):
    start: float           # secondes depuis le debut de l'enregistrement
    end: float
    score: float
    kind: str              # "drop" | "pic"
    at: float              # instant du drop, ou centre du pic (la vignette est prise la)


def _percentile(sorted_values: list[float], pct: float) -> float:
    pos = (len(sorted_values) - 1) * pct / 100.0
    lo = int(math.floor(pos))
    hi = min(lo + 1, len(sorted_values) - 1)
    return sorted_values[lo] + (sorted_values[hi] - sorted_values[lo]) * (pos - lo)


def normalize_series(values, lo_pct: float = 5.0, hi_pct: float = 95.0) -> list[float]:
    """Serie ramenee a 0..1 par ses percentiles (le 5e = 0, le 95e = 1, le reste borne). Constante : 0,5 partout ; vide : vide."""
    vals = [float(v) for v in values]
    if not vals:
        return []
    ordered = sorted(vals)
    lo, hi = _percentile(ordered, lo_pct), _percentile(ordered, hi_pct)
    if hi - lo < 1e-12:
        return [0.5] * len(vals)
    return [min(1.0, max(0.0, (v - lo) / (hi - lo))) for v in vals]


def _std(values: list[float]) -> float:
    if not values:
        return 0.0
    mean = sum(values) / len(values)
    return math.sqrt(sum((v - mean) ** 2 for v in values) / len(values))


def _fit(start: float, end: float, lo: float, hi: float) -> tuple[float, float]:
    """Fenetre decalee (pas raccourcie) pour tenir dans [lo, hi]."""
    length = end - start
    if start < lo:
        start, end = lo, lo + length
    if end > hi:
        start, end = hi - length, hi
    return max(start, lo), min(end, hi)


def _snap(at: float, start: float, end: float, period: float, lo: float, hi: float, clip_min: float, clip_max: float) -> tuple[float, float]:
    """Debut et fin ramenes sur la grille `at + k x period` ; inchange si le calage sortirait des bornes de duree ou du drop."""
    s = at - round((at - start) / period) * period
    e = at + round((end - at) / period) * period
    for _ in range(200):                                   # borne de securite : la boucle converge en quelques pas
        if s < lo - 1e-9:
            s += period
        elif e > hi + 1e-9:
            e -= period
        elif e - s > clip_max + 1e-9:
            e -= period
        elif e - s < clip_min - 1e-9:
            e += period
        else:
            break
    if not (lo - 1e-9 <= s < at < e <= hi + 1e-9 and clip_min - 1e-9 <= e - s <= clip_max + 1e-9):
        return start, end
    return s, e


def _free(start: float, end: float, chosen: list[Clip], gap: float) -> bool:
    return all(end + gap <= c.start or start >= c.end + gap for c in chosen)


def pick_clips(energy, step_s: float, *, bpm: float | None = None, clip_min: float = 15.0, clip_max: float = 30.0, count_max: int = 5,
               pre: float = 8.0, post: float = 12.0) -> list[Clip]:
    """Extraits proposes, du meilleur au moins bon. Liste vide : serie vide, trop courte (moins de `clip_min` utiles), ou plate."""
    n = len(energy)
    if n == 0 or step_s <= 0 or count_max <= 0:
        return []
    dur = n * step_s
    lo, hi = MARGIN_S, dur - MARGIN_S
    if hi - lo < clip_min:
        return []
    levels = [float(e[0]) for e in energy]
    bass = [float(e[1]) for e in energy]
    if _std(levels) < FLAT_STD and _std(bass) < FLAT_STD:
        return []
    period = 60.0 / bpm if bpm and bpm > 0 else None

    def window(at: float, before: float, after: float, kind: str, score: float) -> Clip | None:
        s, e = _fit(at - before, at + after, lo, hi)
        if e - s < clip_min - 1e-9:
            return None
        if period is not None:
            s, e = _snap(at, s, e, period, lo, hi, clip_min, clip_max)
        return Clip(round(s, 3), round(e, 3), round(score, 4), kind, round(at, 3))

    chosen: list[Clip] = []
    # --- drops : creux de basses puis saut
    h, a = max(1, round(HISTORY_S / step_s)), max(1, round(AFTER_S / step_s))
    cum = [0.0]
    for b in bass:
        cum.append(cum[-1] + b)
    drops = []
    for i in range(h, n - a + 1):
        before = (cum[i] - cum[i - h]) / h
        after = (cum[i + a] - cum[i]) / a
        jump = after - before
        if before <= LULL_MAX and jump >= JUMP_MIN:
            drops.append((jump * after, i * step_s))
    for score, at in sorted(drops, key=lambda d: (-d[0], d[1])):
        if len(chosen) >= count_max:
            break
        c = window(at, pre, post, "drop", score)
        if c is not None and _free(c.start, c.end, chosen, GAP_S):
            chosen.append(c)
    # --- pics : fenetres de PIC_S les plus fortes, en complement
    w = max(1, round(PIC_S / step_s))
    if len(chosen) < count_max and n >= w:
        mix = [(levels[i] + bass[i]) / 2.0 for i in range(n)]
        cm = [0.0]
        for v in mix:
            cm.append(cm[-1] + v)
        pics = [((cm[j + w] - cm[j]) / w, (j + w / 2.0) * step_s) for j in range(0, n - w + 1)]
        for score, at in sorted(pics, key=lambda d: (-d[0], d[1])):
            if len(chosen) >= count_max or score < PIC_MIN:
                break
            half = PIC_S / 2.0
            c = window(at, half, half, "pic", score)
            if c is not None and _free(c.start, c.end, chosen, PIC_APART_S):
                chosen.append(c)
    return sorted(chosen, key=lambda c: (-c.score, c.start))


def estimate_bpm(flux, step_s: float, lo: float = 60.0, hi: float = 200.0) -> float | None:
    """BPM d'un flux d'attaques (une valeur toutes les `step_s` s) par autocorrelation ; a egalite de force, le rythme le plus rapide (pas l'octave en dessous).
    None : flux vide, plat, ou sans periodicite nette."""
    n = len(flux)
    if n < 8 or step_s <= 0:
        return None
    mean = sum(flux) / n
    x = [float(v) - mean for v in flux]
    e0 = sum(v * v for v in x) / n
    if e0 <= 1e-12:
        return None
    lag_min = max(2, int(round(60.0 / hi / step_s)))
    lag_max = min(int(round(60.0 / lo / step_s)), n // 2 - 2)
    if lag_max < lag_min:
        return None
    acf: dict[int, float] = {}
    for lag in range(lag_min - 1, lag_max + 2):
        acc = 0.0
        for i in range(n - lag):
            acc += x[i] * x[i + lag]
        acf[lag] = acc / (n - lag) / e0
    best = max(acf[l] for l in range(lag_min, lag_max + 1))
    if best < 0.15:
        return None
    lag = next(l for l in range(lag_min, lag_max + 1) if acf[l] >= 0.95 * best)
    num = acf[lag - 1] - acf[lag + 1]
    den = 2.0 * (acf[lag - 1] - 2.0 * acf[lag] + acf[lag + 1])
    shift = max(-0.5, min(0.5, num / den)) if abs(den) > 1e-12 else 0.0
    return 60.0 / ((lag + shift) * step_s)


# --- fichiers et commandes ffmpeg ------------------------------------------------------------------------------------------

def clip_names(base_path: str, n: int) -> str:
    """Video du n-ieme extrait, a cote de l'enregistrement : `<nom>-extrait-N.mp4`."""
    root = os.path.splitext(base_path)[0]
    return f"{root}-extrait-{n}.mp4"


def clip_command(ffmpeg: str, src: str, out: str, start: float, duration: float) -> list[str]:
    """Decoupe exacte (re-encodage court, memes reglages et etiquettes bt709 que l'enregistrement) : `-ss` AVANT `-i` cherche vite, le re-encodage rend la coupe precise."""
    return [ffmpeg, "-y", "-loglevel", "error", "-ss", f"{start:.3f}", "-i", src, "-t", f"{duration:.3f}",
            "-c:v", "libx264", "-preset", "veryfast", "-crf", "17", "-pix_fmt", "yuv420p", "-g", "30",
            "-colorspace", "bt709", "-color_primaries", "bt709", "-color_trc", "bt709",
            "-bsf:v", "h264_metadata=colour_primaries=1:transfer_characteristics=1:matrix_coefficients=1",
            "-c:a", "aac", "-b:a", "320k", "-ar", "48000", "-movflags", "+faststart", out]


def mmss(seconds: float) -> str:
    """Minutage « mm:ss » (liste d'extraits, fichier texte)."""
    s = int(max(0.0, seconds))
    return f"{s // 60:02d}:{s % 60:02d}"


def caption_text(base_name: str, clips, numbers=None) -> str:
    """Texte a cote des extraits : minutages (mm:ss dans l'enregistrement), type, hashtags generiques (le morceau n'est pas connu).
    `numbers` : numeros de fichier des extraits (si un extrait a echoue, les suivants gardent leur numero) ; sinon 1, 2, 3..."""
    lines = [f"Extraits de {base_name}", ""]
    for n, c in zip(numbers or range(1, len(clips) + 1), clips):
        lines.append(f"{n}. {mmss(c.start)} - {mmss(c.end)} ({c.kind})")
    lines += ["", f"Hashtags : {HASHTAGS}", ""]
    return "\n".join(lines)


def discard_clip(entry: dict) -> bool:
    """« Jeter » : supprime la video d'un extrait (`entry` = un dict de `engine.clips.make_clips`). Deja absente = sans erreur.
    True si la video n'existe plus ensuite."""
    target = entry.get("path")
    if target:
        try:
            os.remove(target)
        except OSError:
            pass
    return not (entry.get("path") and os.path.exists(entry["path"]))
