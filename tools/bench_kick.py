"""Banc du detecteur de kick (lot 2 de la spec synchro son / image, issue #96).

Rejoue de l'audio dans le VRAI `FeatureExtractor`, par blocs de 512 echantillons comme le fil d'analyse, **a vitesse simulee** (horloge injectee :
un morceau de 3 minutes se rejoue en quelques secondes) et compare les kicks detectes a une verite terrain. Il repond a une question chiffree :
le kick est-il manque ? en trop ? mal date ? (le lot 3 en depend : on ne reduit pas le retard au jugé.)

Verite terrain : un fichier `.kicks.json` a cote de l'audio (`{"kicks": [secondes...]}` ou simplement la liste). Pour les signaux synthetiques
elle est connue par construction. `annoter` pre-remplit ce fichier avec le detecteur : c'est un POINT DE DEPART a corriger a la main (sinon le
banc mesurerait le detecteur contre lui-meme, le fichier porte `"source": "detecteur"` et le rapport le signale).

    python tools/bench_kick.py                              # corpus synthetique, sensibilite 1
    python tools/bench_kick.py --sensibilites 0.7,1,1.5,2.5 --json sortie.json
    python tools/bench_kick.py --dossier D:\\mes_sets        # WAV (ou ce que ffmpeg decode) + .kicks.json a cote
    python tools/bench_kick.py annoter morceau.wav          # ecrit morceau.kicks.json (a corriger)

Datation : un kick est date a la FIN du bloc ou il est detecte (comme le fil d'analyse). L'erreur (detecte - vrai) contient donc le retard
logiciel d'analyse (~25 ms attendus) : c'est elle que le lot 3 doit reduire.
"""
from __future__ import annotations

import argparse
import json
import statistics
import subprocess
import sys
import wave
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
for _p in (ROOT, Path(__file__).resolve().parent):
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))

import numpy as np                      # noqa: E402

from engine.audio import FeatureExtractor      # noqa: E402

HOP = 512                      # taille de bloc du fil d'analyse
EARLY_S = 0.03                 # un kick detecte jusqu'a 30 ms AVANT le vrai compte (gigue d'annotation)
LATE_S = 0.09                  # ... et jusqu'a 90 ms APRES (retard d'analyse + montee)
DOUBLE_S = 0.12                # deux detections du meme kick a moins de 120 ms : « double »
DEFAULT_SENS = (1.0,)


# --- rejeu et mesure -----------------------------------------------------------------------------------------------

FAST = True                    # detecteur d'attaque rapide (lot 3) actif ; `--sans-rapide` pour comparer avec la FFT seule


def replay(signal, rate: int, sensitivity: float = 1.0, hop: int = HOP) -> list[float]:
    """Instants (s) des kicks detectes, a vitesse simulee. Meme boucle que le fil d'analyse : un tampon glissant de 2048 echantillons."""
    ex = FeatureExtractor(rate, sensitivity=sensitivity)
    ex.fast_attack = FAST
    x = np.asarray(signal, dtype=np.float32)
    win = ex.win_size
    got: list[float] = []
    buf = np.zeros(win, dtype=np.float32)
    for i in range(0, len(x) - hop + 1, hop):
        buf = np.concatenate((buf[hop:], x[i:i + hop]))
        t = (i + hop) / rate
        _levels, beat = ex.process(buf, hop / rate, t)
        if beat:
            got.append(t)
    return got


def match(got, truth, early: float = EARLY_S, late: float = LATE_S) -> dict:
    """Apparie chaque detection au kick vrai non encore pris le plus proche dans la fenetre [vrai - early, vrai + late].
    Rend vrais positifs, faux positifs, faux negatifs, erreurs de position (detecte - vrai) et nombre de doubles."""
    truth = sorted(float(t) for t in truth)
    used: set[int] = set()
    errors: list[float] = []
    tp = 0
    unmatched: list[float] = []
    for tb in sorted(got):
        best, best_d = None, None
        for j, tr in enumerate(truth):
            if j in used or not (tr - early <= tb <= tr + late):
                continue
            d = abs(tb - tr)
            if best is None or d < best_d:
                best, best_d = j, d
        if best is None:
            unmatched.append(tb)
        else:
            used.add(best)
            tp += 1
            errors.append(tb - truth[best])
    doubles = 0                                    # detection en trop collee a un kick deja pris : un « double », pas un faux kick
    fp = 0
    for tb in unmatched:
        if any(abs(tb - truth[j]) <= DOUBLE_S + late for j in used) and any(0.0 <= tb - truth[j] <= DOUBLE_S + late for j in used):
            doubles += 1
        else:
            fp += 1
    return {"tp": tp, "fp": fp + doubles, "fn": len(truth) - tp, "doubles": doubles, "errors": errors}


def metrics(got, truth, duration_s: float) -> dict:
    m = match(got, truth)
    tp, fp, fn = m["tp"], m["fp"], m["fn"]
    prec = tp / (tp + fp) if tp + fp else 1.0
    rec = tp / (tp + fn) if tp + fn else 1.0
    f1 = 2 * prec * rec / (prec + rec) if prec + rec else 0.0
    errs = sorted(m["errors"])
    p95 = errs[min(len(errs) - 1, int(0.95 * len(errs)))] if errs else None
    return {"tp": tp, "fp": fp, "fn": fn, "doubles": m["doubles"], "kicks": len(list(truth)),
            "precision": round(prec, 4), "rappel": round(rec, 4), "f1": round(f1, 4),
            "erreur_mediane_ms": round(statistics.median(errs) * 1000, 1) if errs else None,
            "erreur_p95_ms": round(p95 * 1000, 1) if p95 is not None else None,
            "faux_par_minute": round(fp * 60.0 / duration_s, 2) if duration_s > 0 else 0.0}


# --- corpus synthetique (verite connue) ---------------------------------------------------------------------------

def _kick(rate: int, saturated: bool = False):
    kk = np.arange(int(0.35 * rate)) / rate
    base = np.exp(-kk / 0.16) * (1 - np.exp(-kk / 0.002)) * np.sin(2 * np.pi * np.cumsum(45 + 110 * np.exp(-kk / 0.03)) / rate)
    kick = 0.7 * (np.tanh(2.5 * base) / np.tanh(2.5) if saturated else base)
    return kick


def _pattern(rate: int, bpm: float, seconds: float, seed: int, kick_steps, snare_steps=(), hats=True, offbeat_bass=True,
             saturated: bool = False, rumble: float = 0.0, noise: float = 0.002, dry_snare: bool = False):
    """Morceau synthetique sur une grille de 16 doubles-croches. kick_steps = rangs de 0..15 qui portent un kick."""
    rg = np.random.default_rng(seed)
    out = np.zeros(int(rate * seconds))
    step = 60.0 / bpm / 4.0
    truth: list[float] = []

    def put(sig, at):
        i = int(at * rate)
        m = min(len(sig), len(out) - i)
        if m > 0:
            out[i:i + m] += sig[:m]
    kick = _kick(rate, saturated)
    hk = np.arange(int(0.05 * rate)) / rate
    bk = np.arange(int(step * 4 * 0.4 * rate)) / rate
    sk = np.arange(int(0.12 * rate)) / rate
    for bar in range(int(seconds / (step * 16)) + 1):
        for st in range(16):
            at = bar * step * 16 + st * step
            if st in kick_steps and at < seconds - 0.4:
                put(kick, at)
                truth.append(at)
            if st in snare_steps:
                nz = rg.standard_normal(len(sk) + 2)
                if dry_snare:                                   # clap sec : plus rien sous ~400 Hz (derivee seconde du bruit)
                    nz = np.diff(nz, 2) * 0.5
                else:
                    nz = nz[:len(sk)]
                put(0.3 * nz * np.exp(-sk / 0.04), at)
            if hats and st % 2 == 0:
                put(0.12 * np.diff(rg.standard_normal(len(hk)), prepend=0) * np.exp(-hk / 0.012), at)
            if offbeat_bass and st % 4 == 2:
                put(0.35 * np.minimum(bk / 0.01, 1.0) * np.exp(-bk / 0.1) * np.sin(2 * np.pi * 55 * bk), at)
    if rumble:
        t = np.arange(len(out)) / rate
        out += rumble * np.sin(2 * np.pi * 50 * t)
    out += noise * rg.standard_normal(len(out))
    return out.astype(np.float32), truth


def _phone(x, rate: int, fc: float, gain_db: float, noise: float, seed: int = 1):
    spec = np.fft.rfft(x)
    f = np.fft.rfftfreq(len(x), 1 / rate)
    f[0] = 1e-9
    y = np.fft.irfft(spec * (1.0 / np.sqrt(1.0 + (fc / f) ** 4)), len(x)) * 10 ** (gain_db / 20)
    return (y + noise * np.random.default_rng(seed).standard_normal(len(y))).astype(np.float32)


def synthetic_corpus(rate: int = 48000, seconds: float = 30.0) -> dict:
    """nom -> (signal, rate, instants vrais des kicks). Les noms commencant par « sans kick » ont une verite vide : on y compte les faux kicks."""
    four = (0, 4, 8, 12)
    corpus: dict = {}
    corpus["house 124 BPM"] = (*_pattern(rate, 124, seconds, 1, four), )
    corpus["techno 132 BPM (grondement)"] = (*_pattern(rate, 132, seconds, 2, four, rumble=0.08), )
    corpus["breakbeat 140 BPM"] = (*_pattern(rate, 140, seconds, 3, (0, 6, 10), snare_steps=(4, 12), offbeat_bass=False), )
    corpus["lent 100 BPM"] = (*_pattern(rate, 100, seconds, 4, four), )
    sat, truth = _pattern(rate, 126, seconds, 5, four, saturated=True)
    corpus["kick sature, plein niveau"] = (sat, truth)
    ph, truth = _pattern(rate, 126, seconds, 6, four, saturated=True)
    corpus["telephone (coupe sous 250 Hz, -30 dB)"] = (_phone(ph, rate, 250.0, -30.0, 0.0008), truth)
    rg = np.random.default_rng(7)
    t = np.arange(int(rate * seconds)) / rate
    env = np.where((t % 0.5) < 0.1, np.sin(np.pi * (t % 0.5) / 0.1), 0.0)
    corpus["kick sur basse continue"] = ((0.15 * np.sin(2 * np.pi * 55 * t) + 0.3 * env * np.sin(2 * np.pi * 60 * t)
                                          + 0.002 * rg.standard_normal(len(t))).astype(np.float32), [i * 0.5 for i in range(int(seconds / 0.5) - 1)])
    nokick, _ = _pattern(rate, 124, seconds, 8, (), snare_steps=(4, 12), offbeat_bass=False)
    corpus["sans kick : charleys et caisses claires larges"] = (nokick, [])
    dry, _ = _pattern(rate, 124, seconds, 9, (), snare_steps=(4, 12), offbeat_bass=False, dry_snare=True)
    corpus["sans kick : charleys et claps secs"] = (dry, [])
    pad = (0.05 * np.sin(2 * np.pi * 110 * t) * (0.6 + 0.4 * np.sin(2 * np.pi * 0.2 * t)) + 0.003 * rg.standard_normal(len(t))).astype(np.float32)
    corpus["sans kick : nappe ambiante"] = (pad, [])
    return {name: (sig, rate, list(truth)) for name, (sig, truth) in corpus.items()}


# --- fichiers reels ------------------------------------------------------------------------------------------------

def read_audio(path: Path) -> tuple[np.ndarray, int]:
    """Mono float32 et frequence. WAV lu en direct (8 / 16 / 24 / 32 bits) ; le reste par ffmpeg (MP3, FLAC...)."""
    path = Path(path)
    if path.suffix.lower() == ".wav":
        try:
            with wave.open(str(path), "rb") as w:
                rate, ch, width, n = w.getframerate(), w.getnchannels(), w.getsampwidth(), w.getnframes()
                raw = w.readframes(n)
            if width == 2:
                x = np.frombuffer(raw, dtype="<i2").astype(np.float32) / 32768.0
            elif width == 1:
                x = (np.frombuffer(raw, dtype=np.uint8).astype(np.float32) - 128.0) / 128.0
            elif width == 3:
                b = np.frombuffer(raw, dtype=np.uint8).reshape(-1, 3).astype(np.int32)
                v = b[:, 0] | (b[:, 1] << 8) | (b[:, 2] << 16)
                x = np.where(v >= 1 << 23, v - (1 << 24), v).astype(np.float32) / float(1 << 23)
            elif width == 4:
                x = np.frombuffer(raw, dtype="<i4").astype(np.float32) / 2147483648.0
            else:
                raise ValueError("largeur d'echantillon inconnue")
            return x.reshape(-1, ch).mean(axis=1).astype(np.float32), rate
        except (wave.Error, ValueError, EOFError):
            pass                                              # WAV flottant ou exotique : on laisse ffmpeg s'en charger
    rate = 48000
    proc = subprocess.run(["ffmpeg", "-v", "error", "-i", str(path), "-ac", "1", "-ar", str(rate), "-f", "f32le", "-"],
                          capture_output=True)
    if proc.returncode != 0 or not proc.stdout:
        raise RuntimeError(f"lecture impossible ({path.name}) : {proc.stderr.decode(errors='replace')[-200:]}")
    return np.frombuffer(proc.stdout, dtype="<f4").copy(), rate


def truth_path(audio: Path) -> Path:
    return audio.with_suffix(".kicks.json")


def read_truth(path: Path) -> tuple[list[float], str]:
    """(instants, source). Format : `{"kicks": [...], "source": "..."}` ou une liste seule."""
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    if isinstance(data, dict):
        return sorted(float(t) for t in data.get("kicks", [])), str(data.get("source", "main"))
    return sorted(float(t) for t in data), "main"


def annotate(audio: Path, sensitivity: float = 1.0) -> Path:
    """Pre-annote : ecrit les kicks du detecteur (corriges de son retard d'analyse median attendu, 25 ms) dans `<audio>.kicks.json`."""
    x, rate = read_audio(audio)
    got = replay(x, rate, sensitivity)
    out = truth_path(audio)
    out.write_text(json.dumps({"source": "detecteur", "a_corriger": True, "kicks": [round(max(t - 0.025, 0.0), 3) for t in got]}, indent=1),
                   encoding="utf-8")
    return out


# --- banc et rapport -----------------------------------------------------------------------------------------------

def bench_item(name: str, signal, rate: int, truth, sensitivities=DEFAULT_SENS, source: str = "synthese") -> dict:
    duration = len(signal) / rate
    rows = {}
    for s in sensitivities:
        rows[f"{s:g}"] = metrics(replay(signal, rate, s), truth, duration)
    return {"nom": name, "duree_s": round(duration, 1), "verite": source, "sensibilites": rows}


def bench_synthetic(sensitivities=DEFAULT_SENS, seconds: float = 30.0) -> list[dict]:
    return [bench_item(n, sig, rate, truth, sensitivities) for n, (sig, rate, truth) in synthetic_corpus(seconds=seconds).items()]


def bench_folder(folder: Path, sensitivities=DEFAULT_SENS) -> list[dict]:
    items = []
    for audio in sorted(Path(folder).iterdir()):
        tp = truth_path(audio)
        if audio.suffix.lower() in (".json", ".txt") or not tp.exists():
            continue
        truth, source = read_truth(tp)
        x, rate = read_audio(audio)
        items.append(bench_item(audio.name, x, rate, truth, sensitivities, source))
    return items


def summary(items: list[dict], sens: str = "1") -> dict:
    """Totaux sur les morceaux AVEC kicks (les passages sans kick ne comptent que pour les faux par minute)."""
    tp = fp = fn = 0
    errs: list[float] = []
    false_min: list[float] = []
    for it in items:
        m = it["sensibilites"].get(sens)
        if not m:
            continue
        if m["kicks"] == 0:
            false_min.append(m["faux_par_minute"])
            continue
        tp, fp, fn = tp + m["tp"], fp + m["fp"], fn + m["fn"]
        if m["erreur_mediane_ms"] is not None:
            errs.append(m["erreur_mediane_ms"])
    prec = tp / (tp + fp) if tp + fp else 1.0
    rec = tp / (tp + fn) if tp + fn else 1.0
    return {"sensibilite": sens, "precision": round(prec, 4), "rappel": round(rec, 4),
            "f1": round(2 * prec * rec / (prec + rec), 4) if prec + rec else 0.0,
            "erreur_mediane_ms": round(statistics.median(errs), 1) if errs else None,
            "faux_par_minute_sans_kick": round(max(false_min), 2) if false_min else None}


def report(items: list[dict], sensitivities=DEFAULT_SENS) -> str:
    lines = []
    for s in sensitivities:
        key = f"{s:g}"
        lines.append(f"Sensibilite {key}")
        lines.append(f"  {'morceau':44s} {'kicks':>5s} {'rappel':>7s} {'prec.':>7s} {'F1':>6s} {'err med':>8s} {'err p95':>8s} {'doubles':>7s} {'faux/min':>8s}")
        for it in items:
            m = it["sensibilites"][key]
            note = "  (verite = detecteur : a corriger)" if it["verite"] == "detecteur" else ""
            em = f"{m['erreur_mediane_ms']:.0f} ms" if m["erreur_mediane_ms"] is not None else "-"
            e95 = f"{m['erreur_p95_ms']:.0f} ms" if m["erreur_p95_ms"] is not None else "-"
            lines.append(f"  {it['nom'][:44]:44s} {m['kicks']:5d} {m['rappel']:7.2f} {m['precision']:7.2f} {m['f1']:6.2f} {em:>8s} {e95:>8s} "
                         f"{m['doubles']:7d} {m['faux_par_minute']:8.1f}{note}")
        t = summary(items, key)
        lines.append(f"  TOTAL (morceaux avec kicks) : rappel {t['rappel']:.2f}, precision {t['precision']:.2f}, F1 {t['f1']:.2f}, "
                     f"erreur mediane {t['erreur_mediane_ms']} ms, faux kicks sur passage sans kick : {t['faux_par_minute_sans_kick']} /min")
        lines.append("")
    lines.append(diagnosis(items, f"{sensitivities[0]:g}"))
    return "\n".join(lines)


def diagnosis(items: list[dict], sens: str = "1") -> str:
    """Une phrase par probleme chiffre : de quoi decider du lot 3 sans intuition."""
    t = summary(items, sens)
    out = ["Diagnostic (sensibilite " + sens + ") :"]
    out.append(f"  - kicks manques : {(1 - t['rappel']) * 100:.0f} %" + ("  <- le rappel est le probleme" if t["rappel"] < 0.9 else ""))
    out.append(f"  - kicks en trop : {(1 - t['precision']) * 100:.0f} %" + ("  <- trop de faux kicks" if t["precision"] < 0.9 else ""))
    if t["erreur_mediane_ms"] is not None:
        out.append(f"  - datation : {t['erreur_mediane_ms']:.0f} ms de retard median du detecteur" + ("  <- au-dessus de 20 ms : cible du lot 3" if t["erreur_mediane_ms"] > 20 else ""))
    worst = sorted((it for it in items if it["sensibilites"].get(sens, {}).get("kicks")), key=lambda it: it["sensibilites"][sens]["f1"])[:1]
    if worst:
        w = worst[0]
        out.append(f"  - morceau le plus difficile : {w['nom']} (F1 {w['sensibilites'][sens]['f1']:.2f})")
    return "\n".join(out)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Banc du detecteur de kick")
    ap.add_argument("commande_ou_fichier", nargs="*", help="`annoter <audio>` pour pre-annoter un fichier")
    ap.add_argument("--dossier", help="dossier de morceaux (WAV...) avec leur .kicks.json a cote")
    ap.add_argument("--sensibilites", default="1", help="liste separee par des virgules (defaut 1)")
    ap.add_argument("--json", help="ecrit le resultat dans ce fichier (comparable d'une version a l'autre)")
    ap.add_argument("--duree", type=float, default=30.0, help="duree du corpus synthetique, en secondes")
    ap.add_argument("--sans-rapide", action="store_true", help="coupe le detecteur d'attaque rapide (FFT seule : comparer avant / apres le lot 3)")
    a = ap.parse_args(argv)
    global FAST
    FAST = not a.sans_rapide
    if a.commande_ou_fichier and a.commande_ou_fichier[0] == "annoter":
        for f in a.commande_ou_fichier[1:]:
            print("ecrit", annotate(Path(f)), "(a corriger a la main avant de s'en servir)")
        return 0
    sens = tuple(float(s) for s in a.sensibilites.split(",") if s.strip())
    items = bench_folder(Path(a.dossier), sens) if a.dossier else bench_synthetic(sens, a.duree)
    if not items:
        print("aucun morceau avec un .kicks.json a cote")
        return 1
    print(report(items, sens))
    if a.json:
        Path(a.json).write_text(json.dumps({"sensibilites": {f"{s:g}": summary(items, f"{s:g}") for s in sens}, "morceaux": items},
                                           indent=1, ensure_ascii=False), encoding="utf-8")
    return 0


if __name__ == "__main__":
    sys.exit(main())
