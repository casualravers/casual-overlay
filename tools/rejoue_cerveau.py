"""Stabilite du visualizer du cerveau (#192) sur de la VRAIE musique : rejoue un morceau dans la vraie analyse audio (AudioAnalyzer,
a vitesse simulee) ou un journal enregistre pendant une soiree (bouton « log » du panneau, fichier .jsonl), et compte ce qui fait sauter
l'affichage, tel que le panneau le verrait (un instantane tous les 0,1 s, comme le rafraichissement a 10 Hz).

    python tools/rejoue_cerveau.py morceau.mp3 [autre.wav ...]      # MP3 / FLAC... decodes par ffmpeg
    python tools/rejoue_cerveau.py ~/.audio2wave/cerveau/journal-*.jsonl
    python tools/rejoue_cerveau.py morceau.mp3 --secondes 120 --brut  # --brut : sans la stabilisation de l'affichage (comparaison)

Par minute : pertes de grille (bascules vers la frise libre), recalages de la grille (nouvelle epoque), changements de teinte (phase
affichee), sauts du curseur (ecart > 0,5 pas avec l'avance attendue), changements de page hors fin de page ; et sur les pas finis :
part de surprises et de manques. Plus la repartition des forces des frappes (pour regler le seuil des frappes faibles).
Fiche : docs/fonctions/cerveau.md (section Stabilite).
"""
from __future__ import annotations

import argparse
import json
import statistics
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
for _p in (ROOT, Path(__file__).resolve().parent):
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))

from core import brain as br              # noqa: E402

SNAP_S = 0.1                                # cadence des instantanes (vues a 10 Hz)
JUMP_STEPS = 0.5                            # saut du curseur : ecart a l'avance attendue au-dela d'un demi-pas


def _snapshot(log, now: float, steady):
    if steady is None:
        return br.snapshot(log, now)
    return br.snapshot(log, now, steady=steady)


class Counter:
    """Compte les sauts de l'affichage d'instantane en instantane (pur : prend les instantanes dans l'ordre)."""

    def __init__(self) -> None:
        self.n = 0
        self.lost = self.epochs = self.tints = self.jumps = self.pages = 0
        self.free = 0
        self.ok = self.miss = self.surprise = 0
        self._prev = None
        self._epoch = None
        self._score = None

    def add(self, snap: dict, epoch: int | None = None) -> None:
        self.n += 1
        prev = self._prev
        grid = bool(snap.get("grid"))
        if not grid:
            self.free += 1
        h = snap.get("header") or {}
        if prev is not None:
            pg = bool(prev.get("grid"))
            if pg and not grid:
                self.lost += 1
            if (prev.get("header") or {}).get("phase") != h.get("phase"):
                self.tints += 1
            if pg and grid:
                if snap["first"] == prev["first"]:
                    period = prev.get("period") or snap.get("period")         # periode de la grille affichee (pas le BPM affiche)
                    expect = SNAP_S / period * br.STEPS_PER_BEAT if period else 0.0
                    if abs((snap["cursor"] - prev["cursor"]) - expect) > JUMP_STEPS:
                        self.jumps += 1
                else:
                    if snap["first"] != prev["first"] + br.PAGE_STEPS or snap["cursor"] > 2.0:
                        self.pages += 1                      # page changee ailleurs qu'en fin de page (recalage)
                    if self._score is not None:
                        self._take_score()
        if epoch is not None:
            if self._epoch is not None and epoch != self._epoch:
                self.epochs += 1
            self._epoch = epoch
        if grid:
            self._score = snap.get("score")
        self._prev = snap

    def _take_score(self) -> None:
        ok, miss, sur = self._score
        self.ok += ok
        self.miss += miss
        self.surprise += sur
        self._score = None

    def report(self) -> dict:
        if self._score is not None:
            self._take_score()
        mins = max(1e-9, self.n * SNAP_S / 60.0)
        played = self.ok + self.miss + self.surprise
        return {
            "minutes": round(mins, 2),
            "pertes_grille_min": round(self.lost / mins, 1),
            "sans_grille_pct": round(100.0 * self.free / max(1, self.n), 1),
            "recalages_min": round(self.epochs / mins, 1),
            "teintes_min": round(self.tints / mins, 1),
            "sauts_curseur_min": round(self.jumps / mins, 1),
            "pages_hors_fin_min": round(self.pages / mins, 1),
            "surprises_pct": round(100.0 * self.surprise / max(1, played), 1),
            "manques_pct": round(100.0 * self.miss / max(1, self.ok + self.miss), 1),
        }


def _strengths(log) -> dict:
    out = {}
    for row in br.INSTRUMENTS:
        v = sorted(e.strength for e in log.events() if e.family == row)
        if v:
            out[row] = {"n": len(v), "q10": round(v[len(v) // 10], 2), "med": round(statistics.median(v), 2), "q90": round(v[len(v) * 9 // 10], 2)}
    return out


ANALYSIS_KEYS = ("sensitivity", "hit_sensitivity", "one_auto")


def analysis_params(sensitivity: float | None = None) -> dict:
    """Reglages de l'analyse comme dans l'application : defauts, puis ceux de ~/.audio2wave/gl_params.json (lus, jamais ecrits),
    puis `--sensibilite`."""
    from core import params as core_params
    p = {k: core_params.DEFAULT_PARAMS[k] for k in ANALYSIS_KEYS}
    try:
        saved = json.loads(Path(core_params.PARAMS_PATH).read_text(encoding="utf-8"))
        p.update({k: float(saved[k]) for k in ANALYSIS_KEYS if k in saved})
    except (OSError, ValueError, TypeError):
        pass
    if sensitivity is not None:
        p["sensitivity"] = float(sensitivity)
    return p


def run_audio(path: Path, seconds: float | None, steady_on: bool, params: dict) -> tuple[dict, dict]:
    import numpy as np
    from bench_kick import read_audio
    from engine.audio import CAPTURE_BLOCK, AudioAnalyzer, FeatureExtractor, RingBuffer
    x, rate = read_audio(path)
    if seconds:
        x = x[: int(seconds * rate)]
    ring = RingBuffer()
    an = AudioAnalyzer(ring, FeatureExtractor(rate))
    an.extractor.sensitivity = float(params["sensitivity"])           # comme la boucle de rendu (engine/projection.py)
    an.extractor.hit_sens = float(params["hit_sensitivity"])
    an.rhythm.auto_one = float(params["one_auto"]) >= 0.5
    log = br.BrainLog()
    an.brain = log
    steady = br.Steady() if steady_on and hasattr(br, "Steady") else None
    count = Counter()
    hop = CAPTURE_BLOCK
    next_snap = 0.0
    x = np.asarray(x, dtype=np.float32)
    for i in range(0, len(x) - hop + 1, hop):
        t = (i + hop) / rate
        ring.write(x[i:i + hop], t_end=t)
        an._analyze(ring.total, rate, t)
        if t >= next_snap:
            next_snap = t + SNAP_S
            count.add(_snapshot(log, t, steady), log.status().get("epoch"))
    return count.report(), _strengths(log)


def run_journal(path: Path, seconds: float | None, steady_on: bool, _params: dict | None = None) -> tuple[dict, dict]:
    log = br.BrainLog()
    steady = br.Steady() if steady_on and hasattr(br, "Steady") else None
    count = Counter()
    t0 = None
    next_snap = None
    with open(path, encoding="utf-8") as f:
        for line in f:
            try:
                wall, kind, args = json.loads(line)
            except (ValueError, TypeError):
                continue
            if t0 is None:
                t0, next_snap = wall, wall
            if seconds and wall - t0 > seconds:
                break
            while wall >= next_snap:
                count.add(_snapshot(log, next_snap, steady), log.status().get("epoch"))
                next_snap += SNAP_S
            br.replay_line(log, kind, args)
    return count.report(), _strengths(log)


def main() -> None:
    ap = argparse.ArgumentParser(description="Stabilite du visualizer du cerveau sur de la vraie musique (issue #192).")
    ap.add_argument("fichiers", nargs="+", type=Path, help="morceaux (WAV, MP3...) ou journaux .jsonl du panneau")
    ap.add_argument("--secondes", type=float, default=None, help="ne rejoue que le debut de chaque fichier")
    ap.add_argument("--brut", action="store_true", help="sans la stabilisation de l'affichage (comparaison)")
    ap.add_argument("--json", type=Path, default=None, help="ecrit les resultats dans ce fichier")
    ap.add_argument("--sensibilite", type=float, default=None, help="sensibilite du kick (sinon celle des reglages enregistres)")
    opts = ap.parse_args()
    params = analysis_params(opts.sensibilite)
    print("analyse : " + "  ".join(f"{k} {v}" for k, v in params.items()))
    results = {}
    for path in opts.fichiers:
        run = run_journal if path.suffix.lower() == ".jsonl" else run_audio
        rep, strengths = run(path, opts.secondes, not opts.brut, params)
        results[path.name] = {"mesures": rep, "forces": strengths}
        print(f"{path.name[:60]}")
        print("  " + "  ".join(f"{k} {v}" for k, v in rep.items()))
        print("  forces : " + "  ".join(f"{k} {v}" for k, v in strengths.items()), flush=True)
    if len(results) > 1:
        keys = next(iter(results.values()))["mesures"].keys()
        mean = {k: round(statistics.mean(r["mesures"][k] for r in results.values()), 1) for k in keys}
        print("moyenne : " + "  ".join(f"{k} {v}" for k, v in mean.items()))
        results["_moyenne"] = mean
    if opts.json is not None:
        opts.json.write_text(json.dumps(results, indent=2, ensure_ascii=False), encoding="utf-8")


if __name__ == "__main__":
    main()
