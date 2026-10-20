"""Mesure de la fluidite de l'interface (stdlib seulement, horloge injectable) : duree des operations de la GUI, gels du fil Tk
et ordonnanceur unique des rafraichissements periodiques.

- `Stats` : chronometre des operations nommees (`timed`, `watched`), garde le nombre d'appels, le total et le pire temps de chacune ;
  une operation plus lente que `SLOW_MS` est signalee (journal de session, au plus une fois par seconde et par nom).
- `StallWatch` : mesure le retard d'une minuterie reguliere ; un retard anormal = le fil Tk a ete bloque (gel), avec le nom de
  l'operation en cours si elle est connue.
- `Scheduler` : une seule boucle pour tous les rafraichissements periodiques (voyants, mesures, statut...) au lieu d'une minuterie Tk
  par rafraichissement ; chaque tache a sa periode, une erreur dans une tache ne tue jamais les autres.
"""
from __future__ import annotations

import functools
import time
from contextlib import contextmanager

SLOW_MS = 80.0            # une operation de l'interface plus lente que cela est signalee
STALL_MS = 150.0          # un retard de minuterie plus grand que cela = gel du fil Tk
LOG_EVERY_S = 1.0         # au plus un signalement par nom et par seconde


class Stats:
    def __init__(self, clock=time.perf_counter, log=None) -> None:
        self.clock = clock
        self.log = log                                  # fonction(message) ou None
        self.ops: dict[str, list] = {}                  # nom -> [appels, total ms, pire ms]
        self.stack: list[str] = []                      # operations en cours (la derniere = la plus interne)
        self.last_logged: dict[str, float] = {}
        self.stalls: list[tuple[float, str]] = []       # (duree ms, operation la plus longue de la fenetre du gel)
        self.recent: list[tuple[float, str, float]] = []  # (fin, nom, ms) des dernieres operations de plus de 20 ms

    @contextmanager
    def timed(self, name: str):
        t0 = self.clock()
        self.stack.append(name)
        try:
            yield
        finally:
            self.stack.pop()
            self.record(name, (self.clock() - t0) * 1000.0)

    def record(self, name: str, ms: float) -> None:
        row = self.ops.setdefault(name, [0, 0.0, 0.0])
        row[0] += 1
        row[1] += ms
        row[2] = max(row[2], ms)
        if ms >= 20.0:
            self.recent.append((self.clock(), name, ms))
            del self.recent[:-30]
        if ms >= SLOW_MS:
            self._say(name, f"IHM lente : {name} {ms:.0f} ms")

    def watched(self, name: str):
        """Decorateur : chronometre la fonction sous `name`."""
        def deco(fn):
            @functools.wraps(fn)
            def inner(*a, **k):
                with self.timed(name):
                    return fn(*a, **k)
            return inner
        return deco

    def stall(self, ms: float) -> None:
        now = self.clock()
        window = [r for r in self.recent if r[0] >= now - ms / 1000.0 - 0.1]
        where = max(window, key=lambda r: r[2])[1] if window else (self.stack[-1] if self.stack else "")
        self.stalls.append((ms, where))
        del self.stalls[:-50]
        self._say("gel", f"IHM gelee {ms:.0f} ms" + (f" pendant {where}" if where else ""))

    def _say(self, key: str, message: str) -> None:
        now = self.clock()
        if self.log is None or now - self.last_logged.get(key, -1e9) < LOG_EVERY_S:
            return
        self.last_logged[key] = now
        try:
            self.log(message)
        except Exception:
            pass

    def report(self, top: int = 10) -> list[str]:
        """Lignes lisibles : les operations les plus couteuses (pire temps), puis les derniers gels."""
        rows = sorted(self.ops.items(), key=lambda kv: -kv[1][2])[:top]
        lines = [f"{name:<28} {c:>5} appels   moy {t / c:6.1f} ms   pire {w:7.1f} ms" for name, (c, t, w) in rows]
        if self.stalls:
            lines.append("")
            lines.append("Derniers gels : " + ", ".join(f"{ms:.0f} ms" + (f" ({w})" if w else "") for ms, w in self.stalls[-5:]))
        return lines or ["Aucune mesure pour l'instant."]


class StallWatch:
    """Appeler `beat(now)` a intervalle regulier (`period_ms`) : rend le retard en ms quand il depasse `STALL_MS`, sinon 0."""

    def __init__(self, period_ms: float = 50.0) -> None:
        self.period_ms = period_ms
        self.last: float | None = None

    def beat(self, now: float) -> float:
        late = 0.0
        if self.last is not None:
            late = (now - self.last) * 1000.0 - self.period_ms
        self.last = now
        return late if late >= STALL_MS else 0.0


class Scheduler:
    """Taches periodiques : `add(nom, periode_ms, fn)`, puis `run_due(now)` appelee par UNE minuterie. Une tache n'est jamais
    rattrapee en rafale apres un gel (la prochaine echeance repart de `now`)."""

    def __init__(self, stats: Stats | None = None, on_error=None) -> None:
        self.tasks: dict[str, list] = {}               # nom -> [periode s, echeance, fn]
        self.stats = stats
        self.on_error = on_error

    def add(self, name: str, period_ms: float, fn, first_ms: float = 0.0, now: float = 0.0) -> None:
        self.tasks[name] = [period_ms / 1000.0, now + first_ms / 1000.0, fn]

    def remove(self, name: str) -> None:
        self.tasks.pop(name, None)

    def run_due(self, now: float) -> list[str]:
        ran = []
        for name, task in list(self.tasks.items()):
            period, due, fn = task
            if now < due:
                continue
            task[1] = now + period
            ran.append(name)
            try:
                if self.stats is not None:
                    with self.stats.timed(name):
                        fn()
                else:
                    fn()
            except Exception as exc:
                if self.on_error is not None:
                    self.on_error(name, exc)
        return ran

    def next_delay_ms(self, now: float, floor: float = 10.0, ceil: float = 100.0) -> float:
        """Delai avant la prochaine echeance (borne) : la boucle ne tourne pas plus souvent que necessaire."""
        if not self.tasks:
            return ceil
        soonest = min(t[1] for t in self.tasks.values())
        return min(max((soonest - now) * 1000.0, floor), ceil)
