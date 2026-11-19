"""Temps GPU par scene, pour le journal (19/11/2026 : une alerte « GPU trop charge : 49 ms » apparaissait en direct alors qu'aucune des 95 scenes ne depasse 10 ms
en 1920x1080 dans un test isole, et rien dans le journal ne disait quel GPU tournait ni quelle scene etait active). Pur (stdlib), l'horloge est fournie.
"""
from __future__ import annotations

PERIOD_S = 30.0                # une ligne de journal toutes les 30 s (s'il y a eu des mesures)
MAX_SCENES = 3                 # scenes citees par ligne (les plus lourdes)


def gpu_line(renderer: str, vendor: str = "") -> str:
    """Ligne de journal au demarrage de la projection : quel GPU dessine (sur un portable a deux GPU, le journal ne le disait pas)."""
    return f"Projection : GPU {renderer}" + (f" ({vendor})" if vendor else "")


class GpuLog:
    """Moyenne du temps GPU (une image sur 8, mesuree par requete GL) par scene active. `note` rend une ligne a ecrire quand la periode est ecoulee, sinon None."""

    def __init__(self, budget_ms: float, period_s: float = PERIOD_S) -> None:
        self.budget_ms = float(budget_ms)
        self.period_s = float(period_s)
        self._t0: float | None = None
        self._by_scene: dict[str, list[float]] = {}          # nom -> [somme, nombre, max]
        self._rec = 0                                       # mesures faites pendant un enregistrement (rendu a la taille du format, pas celle de la fenetre)

    def note(self, gpu_ms: float, scene: str | None, now: float, recording: bool = False) -> str | None:
        if self._t0 is None:
            self._t0 = now
        entry = self._by_scene.setdefault(scene or "(aucune scene)", [0.0, 0, 0.0])
        entry[0] += gpu_ms
        entry[1] += 1
        entry[2] = max(entry[2], gpu_ms)
        if recording:
            self._rec += 1
        if now - self._t0 < self.period_s:
            return None
        return self.flush(now)

    def flush(self, now: float) -> str | None:
        rows = sorted(((s / n, mx, n, name) for name, (s, n, mx) in self._by_scene.items() if n), reverse=True)
        total = sum(r[2] for r in rows)
        rec = self._rec
        self._t0, self._by_scene, self._rec = now, {}, 0
        if not rows:
            return None
        mean = sum(r[0] * r[2] for r in rows) / total
        flag = "HORS BUDGET " if mean > self.budget_ms else ""
        shown = ", ".join(f"\"{name}\" moy {avg:.1f} ms (max {mx:.0f}, {n} mesures)" for avg, mx, n, name in rows[:MAX_SCENES])
        more = f" et {len(rows) - MAX_SCENES} autre(s)" if len(rows) > MAX_SCENES else ""
        where = " pendant un enregistrement" if rec * 2 > total else ""
        return f"GPU {flag}: moy {mean:.1f} ms pour {self.budget_ms:.1f} ms disponibles{where} ; {shown}{more}"
