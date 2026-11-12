"""Regles de securite du Mode VJ : ne pas enchainer une scene que le GPU ne tiendrait pas.

Python pur (stdlib seulement). Le garde-fou `PerfGuard` (engine/render.py) previent quand le GPU ne tient plus l'image mais ne fait rien ;
ici, quand le VJ s'apprete a poser la scene suivante, on estime son cout a partir des effets qu'elle allume et, si l'image risque de
ne plus tenir dans le budget, on coupe les effets les plus chers de cette scene (le reste de la scene est intact) avant de la poser.
"""
from __future__ import annotations

# Cout approximatif de chaque effet couteux, en ms par image a 1080p sur le poste de reference (voir CLAUDE.md, « Mesures » : cellules
# +6,7 ms, halo +3,3 ms, fonte ~1,5 ms), du plus cher au moins cher : c'est l'ordre dans lequel on les coupe.
EFFECT_COST_MS = (("cell_on", "cellules", 6.7), ("holo_on", "halo", 3.3), ("melt_on", "fonte", 1.5))
# Surcout du fond genere en palette stochastique, en ms a 1080p (mesure a 1280 x 720 : bruit +2,3 ms, vagues +1,3 ms, x 2,25 pour 1080p ;
# le degrade, non mesure, est compte comme les vagues). Le fond n'est jamais coupe : il compte seulement dans l'estimation.
BG_STO_COST_MS = {"noise": 5.2, "waves": 2.9, "gradient": 2.9}
# Palette labyrinthe (reaction-diffusion) : mesure +0,6 ms a 1280 x 720 (simulation a grille fixe, quasi independante de la taille,
# + trois lectures par pixel) ; 1,0 ms a 1080p par prudence.
BG_RD_COST_MS = 1.0
# Zones de formes et zones vides du labyrinthe (bg_rd_mix ou bg_rd_void > 0) : une passe de carte (F, K) par image a la taille de la grille, independante du rendu.
# Mesure sur Intel UHD 605 (GPU faible), 10 pas par image : +0,3 ms (zones), +1,6 ms (rotation a 8 branches) ; 1,5 ms par prudence.
BG_RD_ZONE_COST_MS = 1.5
REF_PIXELS = 1920 * 1080
HIGH = 0.90                                    # part du budget d'une image qu'on accepte de remplir (comme PerfGuard.HIGH)
BG_KEYS = ("bg_mode", "bg_palette", "bg_sto_field", "bg_rd_mix", "bg_rd_void")


def _scale(render_size: tuple[int, int] | None) -> float:
    if render_size and render_size[0] > 0 and render_size[1] > 0:
        return (render_size[0] * render_size[1]) / REF_PIXELS
    return 1.0


def effects_cost(values: dict, render_size: tuple[int, int] | None = None) -> float:
    """Cout estime (ms par image) des effets couteux allumes dans `values` (reglages 0 / 1), a la taille de rendu donnee."""
    return sum(ms for key, _label, ms in EFFECT_COST_MS if float(values.get(key, 0.0) or 0.0) >= 0.5) * _scale(render_size)


def background_cost(values: dict, render_size: tuple[int, int] | None = None) -> float:
    """Surcout estime (ms par image) du fond de `values` : seuls les fonds generes stochastique et labyrinthe en ont un notable."""
    if values.get("bg_mode") != "pattern":
        return 0.0
    if values.get("bg_palette") == "labyrinthe":
        zoned = float(values.get("bg_rd_mix", 0.0) or 0.0) > 0.0 or float(values.get("bg_rd_void", 0.0) or 0.0) > 0.0
        zones = BG_RD_ZONE_COST_MS if zoned else 0.0
        return BG_RD_COST_MS * _scale(render_size) + zones
    if values.get("bg_palette") != "stochastique":
        return 0.0
    return BG_STO_COST_MS.get(values.get("bg_sto_field", "noise"), BG_STO_COST_MS["noise"]) * _scale(render_size)


def cap_values(values: dict, current: dict, gpu_ms: float, budget_ms: float, render_size: tuple[int, int] | None = None) -> tuple[dict, list[str]]:
    """Allege `values` (reglages que la scene suivante va poser) si, ajoutes a la charge du moment, ils depasseraient le budget.

    `current` = reglages du moment, `gpu_ms` = temps GPU lisse mesure avec ces reglages (0 = pas de mesure : rien n'est change),
    `budget_ms` = temps disponible par image. Rend (valeurs eventuellement allegees, libelles des effets coupes). Pure : `values` n'est
    pas modifie. Un effet (ou un fond stochastique) deja present dans le look du moment n'est pas compte deux fois (la mesure l'inclut)."""
    if gpu_ms <= 0 or budget_ms <= 0:
        return values, []
    nxt = {k: values.get(k, current.get(k, 0.0)) for k, _l, _m in EFFECT_COST_MS}
    nxt.update({k: values.get(k, current.get(k)) for k in BG_KEYS})
    predicted = (gpu_ms - effects_cost(current, render_size) - background_cost(current, render_size)
                 + effects_cost(nxt, render_size) + background_cost(nxt, render_size))
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
