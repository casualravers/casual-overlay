"""Mesure du cout du panneau du cerveau (issue #192, tache 9 du plan) dans la VRAIE fenetre de reglages, avec la vraie projection.

Lance l'application comme `audio2wave_gl.py --gui --synthetic --projeter` (son et video synthetiques, fenetre de projection GL ouverte
sur l'ecran du PC), puis enchaine des segments de `--duree` secondes : d'abord SANS le cerveau (espace VJ affiche : reference), puis le
cerveau raccroche dans chaque vue (iso, mandala, tunnel, sismo), puis la frise libre (sans grille). Pour chaque segment :

- `brain.tick` (chronometre `STATS` de la GUI) : nombre d'appels, moyenne, pire ; le pire « en regime » ecarte la premiere seconde
  (changement de vue : la vue cree ses elements une fois) ; le cout de ce changement est donne a part (`bascule`) ;
- gels du fil Tk (`StallWatch` de la GUI, > 150 ms) avec l'operation en cours ;
- images/s du rendu GL (sante publiee par le rendu dans le journal de la session, une valeur par seconde, moyenne du segment).

Le panneau lit un journal SYNTHETIQUE (et non celui de la session) nourri comme une vraie soiree : grille a 126 BPM, kick a chaque temps,
snare sur 2 et 4, hi-hat en croches et quelques doubles croches, quelques frappes manquees ou en trop (predictions realisees, manquees,
surprises), phases groove -> build -> peak (drop) -> break en boucle, effets avec leur raison et leur cause (ripple <- kick, chroma <-
snare, logo <- hi-hat, strobe en peak, foudre, glitch, wobble, scene toutes les 16 mesures). Le journal de la session continue de recevoir
les publications du moteur (le rendu publie toujours : son cout est dans les deux segments). Les fichiers de l'utilisateur ne sont pas
touches (reglages, scenes, auto-sauvegarde, brain_ui.json dans un dossier temporaire).

    python tools/mesure_cerveau.py                     # 60 s par segment (~6 min)
    python tools/mesure_cerveau.py --duree 20          # plus court
    python tools/mesure_cerveau.py --sans-rendu        # sans la fenetre de projection (cout Tk seul)
    python tools/mesure_cerveau.py --json sortie.json  # resultats en JSON

Verdict : budget retenu par l'utilisateur (2026-11-21) : au plus 80 ms/s de brain.tick, aucun gel > 150 ms imputable au panneau,
images/s du rendu a 5 % pres de la reference (les seuils par tick du plan, 4 ms / 20 ms, restent affiches pour l'historique). Fiche : docs/fonctions/cerveau.md (section Budget).
"""
from __future__ import annotations

import argparse
import cProfile
import json
import pstats
import random
import sys
import tempfile
import threading
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import audio2wave_gl as gl                 # noqa: E402  (ajoute gui/ a sys.path)
import gl_gui                              # noqa: E402
import gui_brain                           # noqa: E402
from core import autosave as core_autosave  # noqa: E402
from core import brain as br               # noqa: E402
from core import params as core_params     # noqa: E402
from core import scenes as core_scenes     # noqa: E402

BPM = 126.0
PERIOD = 60.0 / BPM
# plan des phases, en mesures (boucle) : groove 16, build 8, peak 8 (drop au debut), break 4
PLAN = (("groove", 16), ("build", 8), ("peak", 8), ("break", 4))
SEGMENTS = ("sans", "iso", "mandala", "tunnel", "sismo", "free")
TICK_MEAN_MS, TICK_WORST_MS, STALL_MS, FPS_TOL = 4.0, 20.0, 150.0, 0.05     # seuils par tick du plan (historique, plus le verdict)
BUDGET_MS_S = 80.0          # budget retenu par l'utilisateur (2026-11-21) : temps mur de brain.tick par seconde (4 ms x 20 Hz)


def phase_of_bar(bar: int) -> tuple[str, int]:
    """Phase de la mesure `bar` (depuis le debut) et rang de la mesure dans la phase."""
    total = sum(n for _p, n in PLAN)
    b = bar % total
    for name, n in PLAN:
        if b < n:
            return name, b
        b -= n
    return PLAN[0][0], 0


class Feeder(threading.Thread):
    """Nourrit un BrainLog synthetique en temps reel, pas par pas (comme les fils audio et GL : jamais d'evenement dans le futur)."""

    def __init__(self, log: br.BrainLog, session_log, stop: threading.Event) -> None:
        super().__init__(name="mesure-cerveau", daemon=True)
        self.log, self.session_log, self.stop = log, session_log, stop
        self.rng = random.Random(192)
        self.anchor = time.monotonic() + 0.5
        self.grid = True
        self.step = 0
        self.phase = None

    def _status(self) -> None:
        gauges = {}
        try:
            gauges = dict(self.session_log.status().get("gauges") or {})     # sante reelle publiee par le rendu
        except Exception:
            pass
        gauges.setdefault("sources", {"bg": "basses", "logo": "aigus"})
        gauges.setdefault("auto", {"fx_master": 0.8, "phases": True, "strobe": True, "tempo": False, "vj": False})
        self.log.set_status(bpm=BPM, confidence=0.92, origin="kick", grid=(self.anchor, PERIOD) if self.grid else None,
                            phase=self.phase, gauges=gauges, next_scene="scene 3",
                            next_scene_at=time.monotonic() + 30.0 - (time.monotonic() % 30.0))

    def _publish_step(self, k: int) -> None:
        log, rng = self.log, self.rng
        t = br.step_time(k, self.anchor, PERIOD)
        bar, s = divmod(k, br.STEPS_PER_BAR)
        phase, rank = phase_of_bar(bar)
        if s == 0 and rank == 0:
            self.phase = phase
            log.publish("phase", reason=phase, t=t)
            if phase == "build":
                log.publish("build", t=t)
            elif phase == "peak":
                log.publish("drop", t=t)
                log.publish("strobe", 1.0, reason="drop : eclair du pic", t=t)
        if s == 0 and bar % 16 == 0:
            log.publish("scene", 1.0, reason="VJ : scene suivante (16 mesures)", t=t)
        kick = s % 4 == 0 and phase != "break" and rng.random() > 0.04
        snare = s in (4, 12) and rng.random() > 0.06 or (phase == "build" and rank >= 4 and s % 2 == 0)
        hihat = s % 2 == 0 and rng.random() > 0.1 or rng.random() < 0.08
        if kick:
            v = 0.7 + 0.3 * rng.random()
            log.publish("kick", v, t=t)
            if rng.random() < 0.5:
                log.publish("ripple", v, reason="ripple : kick fort sur les basses", cause="kick", t=t)
            if phase == "peak" and s == 0:
                log.publish("strobe", 1.0, reason="strobe auto : pic, eclair sur le kick", cause="kick", t=t)
        if snare:
            v = 0.5 + 0.4 * rng.random()
            log.publish("snare", v, t=t)
            if rng.random() < 0.4:
                log.publish("chroma", v, reason="chroma : caisse claire", cause="snare", t=t)
        if hihat:
            v = 0.3 + 0.5 * rng.random()
            log.publish("hihat", v, t=t)
            if rng.random() < 0.15:
                log.publish("logo", v, reason="logo : reaction <- aigus", cause="hihat", t=t)
        if rng.random() < 0.01:
            log.publish("foudre", 1.0, reason="foudre : frappe aigue isolee", cause="hihat", t=t)
        if rng.random() < 0.02:
            log.publish("glitch", 0.8, reason="glitch : automation du fond", t=t)
        if rng.random() < 0.02:
            log.publish("wobble", 0.6, reason="wobble : noise", t=t)

    def run(self) -> None:
        last_status = 0.0
        while not self.stop.is_set():
            now = time.monotonic()
            self.log.set_clock(now, now)                   # horloge du son = horloge murale
            while br.step_time(self.step, self.anchor, PERIOD) <= now:
                self._publish_step(self.step)
                self.step += 1
            if now - last_status >= 0.25:
                last_status = now
                self._status()
            time.sleep(0.005)


class _Proxy:
    """La session vue par le panneau : tout comme la vraie, sauf `brain` (le journal synthetique)."""

    def __init__(self, s, log) -> None:
        self.__dict__["_s"] = s
        self.__dict__["brain"] = log

    def __getattr__(self, name):
        return getattr(self._s, name)

    def __setattr__(self, name, value):
        setattr(self._s, name, value)


def summarize(seg: dict) -> dict:
    ticks = seg["ticks"]
    regime = [ms for t, ms in ticks if t >= seg["t0"] + 1.0]
    first = [ms for t, ms in ticks if t < seg["t0"] + 1.0]
    fps = [v for v in seg["fps"] if v > 0]
    span = max(1e-6, seg.get("t1", seg["t0"]) - seg["t0"])
    cpu = seg["cpu"]
    return {
        "cpu_tick_ms": sum(cpu) / len(cpu) if cpu else 0.0,
        "ms_par_s": sum(ms for _t, ms in ticks) / span,
        "charge_tk": (seg.get("tk1", seg["tk0"]) - seg["tk0"]) / span,
        "appels": len(ticks),
        "moy_ms": sum(ms for _t, ms in ticks) / len(ticks) if ticks else 0.0,
        "pire_ms": max((ms for _t, ms in ticks), default=0.0),
        "pire_regime_ms": max(regime, default=0.0),
        "moy_regime_ms": sum(regime) / len(regime) if regime else 0.0,
        "bascule_ms": max(first, default=0.0),
        "gels": [(round(ms), where) for ms, where in seg["stalls"]],
        "fps": sum(fps) / len(fps) if fps else None,
    }


def main() -> None:
    ap = argparse.ArgumentParser(description="Cout du panneau du cerveau dans la vraie fenetre de reglages (issue #192).")
    ap.add_argument("--duree", type=float, default=60.0, help="secondes par segment (defaut 60)")
    ap.add_argument("--sans-rendu", action="store_true", help="sans la fenetre de projection GL")
    ap.add_argument("--json", type=Path, default=None, help="ecrit les resultats dans ce fichier")
    ap.add_argument("--profil", action="store_true", help="profil (cProfile) de brain.tick par segment, en fin de mesure")
    opts = ap.parse_args()

    tmp = Path(tempfile.mkdtemp(prefix="mesure_cerveau_"))
    core_params.OVERLAY_PRESETS_PATH = tmp / "overlay_presets.json"
    core_params.BACKGROUND_PRESETS_PATH = tmp / "background_presets.json"
    core_params.BRAIN_UI_PATH = tmp / "brain_ui.json"
    core_scenes.SCENES_PATH, core_scenes.MASTERS_PATH = tmp / "scenes.json", tmp / "masters.json"
    core_autosave.AUTOSAVE_PATH = tmp / "autosave.json"

    results: dict = {}
    segs: dict = {}
    state = {"seg": None}
    stop = threading.Event()
    real_run_gui = gl_gui.run_gui
    STATS = gl_gui.STATS
    real_stall, real_record = STATS.stall, STATS.record

    def stall(ms):
        real_stall(ms)
        seg = segs.get(state["seg"])
        if seg is not None:
            seg["stalls"].append(STATS.stalls[-1])

    def record(name, ms):
        real_record(name, ms)
        seg = segs.get(state["seg"])
        if seg is not None and name == "brain.tick":
            seg["ticks"].append((time.perf_counter(), ms))
    STATS.stall, STATS.record = stall, record
    real_tick = gui_brain.BrainPanel.tick

    profiles: dict = {}

    def tick(self, now=None):
        """Temps CPU du fil Tk pendant le tick (sans l'attente du verrou Python tenu par les autres fils), en plus du temps mur."""
        seg = segs.get(state["seg"])
        prof = None
        if opts.profil and seg is not None:
            prof = profiles.setdefault(state["seg"], cProfile.Profile())
            prof.enable()
        c0 = time.thread_time()
        try:
            return real_tick(self, now)
        finally:
            if prof is not None:
                prof.disable()
            if seg is not None:
                seg["cpu"].append((time.thread_time() - c0) * 1000.0)
    gui_brain.BrainPanel.tick = tick

    def on_ready(c):
        root, bp = c["root"], c["brain"]
        s = bp.s
        fake = br.BrainLog()
        feeder = Feeder(fake, s.brain, stop)
        feeder.start()
        bp.s = _Proxy(s, fake)
        root.geometry("1500x900")
        c["scenes"]["expand"](True)
        live_args_s = getattr(s, "projection", None)
        print(f"Projection : {'ouverte' if live_args_s is not None and live_args_s.running() else 'fermee'} ; "
              f"{opts.duree:.0f} s par segment.", flush=True)
        order = list(SEGMENTS)

        def sample_fps():
            seg = segs.get(state["seg"])
            if seg is not None and time.perf_counter() >= seg["t0"] + 2.0:
                g = (s.brain.status().get("gauges") or {}) if getattr(s, "brain", None) is not None else {}
                seg["fps"].append(float(g.get("fps") or 0.0))
            if not stop.is_set():
                root.after(1000, sample_fps)

        def start(i):
            if state["seg"] is not None:
                seg = segs[state["seg"]]
                seg["t1"], seg["tk1"] = time.perf_counter(), time.thread_time()
                results[state["seg"]] = summarize(seg)
                r = results[state["seg"]]
                print(f"  {state['seg']:<8} CPU tick {r['cpu_tick_ms']:.2f} ms, charge du fil Tk {r['charge_tk'] * 100:.1f} %, "
                      f"tick moy {r['moy_ms']:.2f} ms (regime {r['moy_regime_ms']:.2f}), pire regime "
                      f"{r['pire_regime_ms']:.1f} ms, bascule {r['bascule_ms']:.1f} ms, gels {r['gels']}, "
                      f"img/s {r['fps'] if r['fps'] is None else round(r['fps'], 1)}", flush=True)
            if i >= len(order):
                state["seg"] = None
                stop.set()
                c["close"]()
                return
            name = order[i]
            if name == "sans":
                if bp._flag("brain_on"):
                    bp.toggle()
            else:
                feeder.grid = name != "free"
                if not bp._flag("brain_on"):
                    bp.toggle()
                if name != "free":
                    bp.set_view(name)
            state["seg"] = name
            segs[name] = {"t0": time.perf_counter(), "tk0": time.thread_time(), "ticks": [], "cpu": [], "stalls": [], "fps": []}
            root.after(int(opts.duree * 1000), lambda: start(i + 1))

        def warm():
            bp.toggle()                                    # construit le panneau une fois (hors mesure)
            bp.set_view("iso")
            root.after(1500, lambda: (bp.toggle(), root.after(2000, lambda: start(0))))
        root.after(2000, warm)
        root.after(1000, sample_fps)

    def run_gui(s, live, on_ready_=None, boot=None):
        return real_run_gui(s, live, on_ready, boot=boot)
    gl_gui.run_gui = run_gui
    argv = ["audio2wave_gl.py", "--gui", "--synthetic", "--no-fullscreen"]
    if not opts.sans_rendu:
        argv.append("--projeter")
    sys.argv = argv
    try:
        gl.main()
    finally:
        stop.set()
        gl_gui.run_gui = real_run_gui
        gui_brain.BrainPanel.tick = real_tick

    base = (results.get("sans") or {}).get("fps")
    print("\nmoy / pire / bascule : temps mur de brain.tick (STATS) ; CPU : temps CPU du fil Tk par tick ; Tk % : charge totale du fil Tk")
    print("(redessins du canvas compris) sur le segment ; ms/s : temps mur de brain.tick par seconde (budget : 4 ms x 20 Hz = 80 ms/s).")
    print(f"Verdict : budget retenu par l'utilisateur ({BUDGET_MS_S:.0f} ms/s de brain.tick), aucun gel, images/s a 5 % pres.")
    print("Segment   appels  moy ms  pire ms  pire regime  bascule  ms/s  CPU ms   Tk %  gels>150ms  img/s   verdict")
    for name in SEGMENTS:
        r = results.get(name)
        if r is None:
            print(f"{name:<9} (non mesure)")
            continue
        fps = r["fps"]
        ok = True
        if name != "sans":
            ok = r["ms_par_s"] <= BUDGET_MS_S and not r["gels"]
            if base and fps:
                ok = ok and fps >= base * (1.0 - FPS_TOL)
        print(f"{name:<9} {r['appels']:>6}  {r['moy_ms']:6.2f}  {r['pire_ms']:7.1f}  {r['pire_regime_ms']:11.1f}  "
              f"{r['bascule_ms']:7.1f}  {r['ms_par_s']:4.0f}  {r['cpu_tick_ms']:6.2f}  {r['charge_tk'] * 100:5.1f}  {len(r['gels']):>10}  {'-' if fps is None else f'{fps:5.1f}'}   {'ok' if ok else 'DEPASSE'}")
    for name, prof in profiles.items():
        print(f"\n--- profil de brain.tick : {name} (temps propre, 12 premieres fonctions)")
        pstats.Stats(prof).sort_stats("tottime").print_stats(12)
    if opts.json is not None:
        opts.json.write_text(json.dumps({"duree_s": opts.duree, "rendu": not opts.sans_rendu, "segments": results}, indent=2),
                             encoding="utf-8")


if __name__ == "__main__":
    main()
