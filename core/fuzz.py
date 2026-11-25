"""Fuzz des reglages (#195) : tirage reproductible de jeux de reglages valides depuis la table `core.params_schema` (stdlib seulement).

Le rendu de ces jeux est fait par `tools/fuzz_reglages.py` (moteur) ; ici, seulement le tirage, pour qu'il se teste partout.

- `draw_params(rng, scopes, only)` : un jeu complet des cles de ces portees ; chaque valeur est, au hasard, le defaut, la borne basse, la
  borne haute ou une valeur uniforme dans la plage (les bornes sont tirees plus souvent que le hasard pur : c'est la que ca casse) ;
- `cases(seed, n, ...)` : `n` jeux reproductibles ; le jeu `k` ne depend que de `seed` et de `k` (un cas se rejoue seul) ;
- `describe(case)` : les reglages qui different du defaut, pour les messages d'echec.

Jamais de valeur que `params_schema.validate` refuse ou corrige. Les cles qu'on ne peut pas tirer (chaine libre, plage ouverte) gardent leur
defaut ; celles dont le defaut est `None` (chemin du logo) sont absentes du jeu : on le pose sur `defaults()`. Aucun `hash()`, aucun `set` itere : meme graine = memes jeux sur toute machine.
"""
from __future__ import annotations

import random

from core import params_schema as ps

SEED_STRIDE = 1_000_003          # le jeu k d'une graine utilise random.Random(graine x SEED_STRIDE + k)
DESCRIBE_MAX = 200               # longueur maximale du texte de `describe` (puis « ... »)

# Poids d'un tirage : defaut 10 %, borne basse 20 %, borne haute 20 %, valeur uniforme 50 %.
_P_DEFAULT, _P_LOW, _P_HIGH = 0.10, 0.30, 0.50


def _is_int(value) -> bool:
    return isinstance(value, int) and not isinstance(value, bool)


def _draw_number(rng: random.Random, default, lo, hi):
    """Un nombre du type de `default` (int ou float) dans `lo..hi`, ou `default` si la plage est ouverte."""
    if lo is None or hi is None or isinstance(default, bool):
        return default
    if _is_int(default):
        lo_i, hi_i = int(lo), int(hi)
        pick = rng.random()
        if pick < _P_DEFAULT:
            return default
        if pick < _P_LOW:
            return lo_i
        if pick < _P_HIGH:
            return hi_i
        return rng.randint(lo_i, hi_i)
    pick = rng.random()
    if pick < _P_DEFAULT:
        return default
    if pick < _P_LOW:
        return float(lo)
    if pick < _P_HIGH:
        return float(hi)
    return round(rng.uniform(float(lo), float(hi)), 4)


def draw_params(rng: random.Random, scopes: tuple[str, ...] = ("look",), only: set[str] | None = None) -> dict:
    """Un jeu de reglages valide : toutes les cles de ces portees (ou seulement celles de `only`), dans l'ordre de la table."""
    out: dict = {}
    for p in ps.TABLE:
        if p.scope not in scopes or (only is not None and p.key not in only):
            continue
        default = p.default
        if isinstance(default, list):
            out[p.key] = [_draw_number(rng, d, p.lo, p.hi) for d in default]
        elif isinstance(default, str):
            out[p.key] = rng.choice(p.choices) if p.choices else default
        elif default is None:
            continue                                   # chemin (logo_path) : rempli par defaults(), `validate` refuse None
        else:
            out[p.key] = _draw_number(rng, default, p.lo, p.hi)
    return out


def cases(seed: int, n: int, scopes: tuple[str, ...] = ("look",), only: set[str] | None = None) -> list[dict]:
    """`n` jeux reproductibles : le jeu k = `draw_params(random.Random(seed * SEED_STRIDE + k), ...)`."""
    return [draw_params(random.Random(int(seed) * SEED_STRIDE + k), scopes, only) for k in range(int(n))]


def describe(case: dict) -> str:
    """Les reglages de `case` qui different du defaut (`cle=valeur`, tries), plafonnes a DESCRIBE_MAX caracteres."""
    base = ps.defaults()
    parts = [f"{k}={case[k]}" for k in sorted(case) if k in base and case[k] != base[k]]
    text = ", ".join(parts)
    return text if len(text) <= DESCRIBE_MAX else text[:DESCRIBE_MAX] + "..."
