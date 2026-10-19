"""Regles de securite du Mode VJ : ne pas enchainer une scene que le GPU ne tiendrait pas.

Python pur (stdlib seulement). Le garde-fou `PerfGuard` (engine/render.py) previent quand le GPU ne tient plus l'image mais ne fait rien ;
ici, quand le VJ s'apprete a poser la scene suivante, on estime son cout a partir des effets qu'elle allume et, si l'image risque de
ne plus tenir dans le budget, on coupe les effets les plus chers de cette scene (le reste de la scene est intact) avant de la poser.
"""
from __future__ import annotations

# Cout approximatif de chaque effet couteux, en ms par image a 1080p sur le poste de reference (voir CLAUDE.md, « Mesures » : cellules
# +6,7 ms, halo +3,3 ms, fonte ~1,5 ms), du plus cher au moins cher : c'est l'ordre dans lequel on les coupe.
EFFECT_COST_MS = (("cell_on", "cellules", 6.7), ("holo_on", "halo", 3.3), ("melt_on", "fonte", 1.5))
REF_PIXELS = 1920 * 1080
HIGH = 0.90                                    # part du budget d'une image qu'on accepte de remplir (comme PerfGuard.HIGH)


def effects_cost(values: dict, render_size: tuple[int, int] | None = None) -> float:
    """Cout estime (ms par image) des effets couteux allumes dans `values` (reglages 0 / 1), a la taille de rendu donnee."""
    scale = 1.0
    if render_size and render_size[0] > 0 and render_size[1] > 0:
        scale = (render_size[0] * render_size[1]) / REF_PIXELS
    return sum(ms for key, _label, ms in EFFECT_COST_MS if float(values.get(key, 0.0) or 0.0) >= 0.5) * scale


def cap_values(values: dict, current: dict, gpu_ms: float, budget_ms: float, render_size: tuple[int, int] | None = None) -> tuple[dict, list[str]]:
    """Allege `values` (reglages que la scene suivante va poser) si, ajoutes a la charge du moment, ils depasseraient le budget.

    `current` = reglages du moment, `gpu_ms` = temps GPU lisse mesure avec ces reglages (0 = pas de mesure : rien n'est change),
    `budget_ms` = temps disponible par image. Rend (valeurs eventuellement allegees, libelles des effets coupes). Pure : `values` n'est
    pas modifie. Un effet deja allume dans le look du moment n'est pas compte deux fois (la mesure l'inclut)."""
    if gpu_ms <= 0 or budget_ms <= 0:
        return values, []
    predicted = gpu_ms - effects_cost(current, render_size) + effects_cost({k: values.get(k, current.get(k, 0.0)) for k, _l, _m in EFFECT_COST_MS}, render_size)
    out = dict(values)
    dropped: list[str] = []
    for key, label, _ms in EFFECT_COST_MS:
        if predicted <= HIGH * budget_ms:
            break
        if float(out.get(key, current.get(key, 0.0)) or 0.0) >= 0.5:
            predicted -= effects_cost({key: 1.0}, render_size)
            out[key] = 0.0
            dropped.append(label)
    return (out, dropped) if dropped else (values, [])
