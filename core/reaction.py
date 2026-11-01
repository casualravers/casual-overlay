"""Palette « labyrinthe » du fond genere : logique pure de la reaction-diffusion (modele Gray-Scott, motif de Turing).

Python pur (stdlib seulement). La simulation elle-meme tourne sur le GPU (`engine/reaction.py`, `gl_shaders/rd_step.frag`) ;
ici : parametres du modele selon la forme voulue, taille de la grille, germes et nombre de pas par image.
"""
from __future__ import annotations

import math
import random

# (F, K) : taches qui se repoussent et se divisent jusqu'a remplir l'ecran (valeur classique ; mesure en RG32F : 238 taches, aucun contact,
# motif toujours actif apres 12000 pas). En RG16F elle semblait ne jamais se diviser : c'etait un artefact d'arrondi (voir engine/reaction.py).
SPOTS = (0.0367, 0.0649)
MAZE = (0.0290, 0.0570)         # (F, K) : taches soudees en lignes sinueuses (labyrinthe)
BASE_GRID = (384, 216)          # grille de simulation a l'echelle 1 (independante de la fenetre)
STEPS_PER_S = 600.0             # pas de simulation par seconde a vitesse 1
MAX_STEPS = 24                  # plafond par image (au-dela, l'exces est jete : pas de retard qui s'accumule)
SEED_COUNT = 40                 # germes de l'etat initial
SEED_RADIUS = 3.0               # rayon d'un germe, en cellules
KICK_SEEDS = 3                  # germes semes par kick
DEAD_LEVEL = 0.002              # moyenne de V sous laquelle le motif est considere eteint
WATCH_S = 2.0                   # periode du garde-fou anti-extinction

# Zones de formes et symetries de colonisation (docs/plans/2026-11-01-labyrinthe-evolution-design.md).
BLOB = (0.0250, 0.0550)         # (F, K) : grosses taches pleines (provisoire : fixe par balayage, tools/rd_sweep.py)
SYM_MODES = ("none", "mirror_lr", "mirror_tb", "quad", "rot")
ZONE_CELLS = (5, 3)             # reseau periodique du bruit des zones, en cellules sur la grille (~un tiers de la hauteur)
ZONE_SPEED = 0.08               # cycles de derive des zones par seconde a bg_rd_drift = 1
FREE_NOISE = 1e-3               # bruit asymetrique ajoute a V a chaque pas, x Liberte (seulement avec une symetrie)
FREE_ZONE = 0.35                # part du bruit non replie dans la carte des zones, x Liberte
FREE_JITTER = 0.04              # decalage max d'une copie de germe (fraction de la hauteur), x Liberte
LAYER_SPREAD = 0.12             # ecart des seuils des couches a bg_rd_layers = 1
MAX_PENDING_SEEDS = 32          # file des germes en attente (orbites), les plus anciens jetes au-dela


def feed_kill(shape: float) -> tuple[float, float]:
    """(F, K) du modele : 0 = taches, 1 = labyrinthe, interpolation lineaire (forme bornee a 0..1)."""
    s = min(max(float(shape), 0.0), 1.0)
    return (SPOTS[0] + (MAZE[0] - SPOTS[0]) * s, SPOTS[1] + (MAZE[1] - SPOTS[1]) * s) if 0.0 < s < 1.0 else (SPOTS if s == 0.0 else MAZE)


def shape_feed_kill(s: float) -> tuple[float, float]:
    """(F, K) sur l'axe interne 0..2 : 0 = points, 1 = lignes, 2 = taches (lineaire par morceaux, borne). Sur 0..1 : exactement feed_kill."""
    s = min(max(float(s), 0.0), 2.0)
    if s <= 1.0:
        return feed_kill(s)
    if s == 2.0:
        return BLOB
    t = s - 1.0
    return (MAZE[0] + (BLOB[0] - MAZE[0]) * t, MAZE[1] + (BLOB[1] - MAZE[1]) * t)


def orbit_size(sym: str, branches: int) -> int:
    """Nombre de copies d'un germe pour une symetrie (branches de la rotation bornees a 2..8)."""
    if sym == "rot":
        return min(max(int(branches), 2), 8)
    return {"mirror_lr": 2, "mirror_tb": 2, "quad": 4}.get(sym, 1)


def base_seed_count(sym: str, branches: int) -> int:
    """Germes de base de l'etat initial : ~SEED_COUNT au total une fois repetes en orbite."""
    return max(1, round(SEED_COUNT / orbit_size(sym, branches)))


def _orbit(x: float, y: float, sym: str, branches: int, aspect: float) -> list[tuple[float, float]]:
    if sym == "mirror_lr":
        return [(x, y), (1.0 - x, y)]
    if sym == "mirror_tb":
        return [(x, y), (x, 1.0 - y)]
    if sym == "quad":
        return [(x, y), (1.0 - x, y), (x, 1.0 - y), (1.0 - x, 1.0 - y)]
    if sym == "rot":
        n = orbit_size(sym, branches)
        dx, dy = (x - 0.5) * aspect, y - 0.5                 # coordonnees isotropes : rotation bien ronde a l'ecran
        out = []
        for i in range(n):
            a = 2.0 * math.pi * i / n
            ca, sa = math.cos(a), math.sin(a)
            out.append((0.5 + (dx * ca - dy * sa) / aspect, 0.5 + dx * sa + dy * ca))
        return out
    return [(x, y)]


def colonize(points, sym: str, branches: int, free: float, aspect: float, salt: int) -> list[tuple[float, float, float]]:
    """Germes (x, y, rayon) : chaque point repete en orbite selon la symetrie ; la Liberte decale position et rayon de chaque copie
    (deterministe pour `salt`). Sans symetrie, les points sont rendus tels quels avec le rayon par defaut (Liberte sans effet)."""
    if sym not in SYM_MODES[1:]:
        return [(x, y, SEED_RADIUS) for x, y in points]
    free = min(max(float(free), 0.0), 1.0)
    rng = random.Random(salt)
    out = []
    for x, y in points:
        for ox, oy in _orbit(x, y, sym, branches, aspect):
            jx, jy, jr = (rng.uniform(-1.0, 1.0) for _ in range(3))
            if free > 0.0:
                ox += FREE_JITTER * free * jx / aspect
                oy += FREE_JITTER * free * jy
            out.append((ox % 1.0, oy % 1.0, SEED_RADIUS * (1.0 + 0.3 * free * jr)))
    return out


def grid_size(scale: float) -> tuple[int, int]:
    """Taille de la grille pour une echelle (0,5..2) : plus grande echelle = grille plus petite = motif plus gros a l'ecran."""
    s = min(max(float(scale), 0.5), 2.0)
    return round(BASE_GRID[0] / s), round(BASE_GRID[1] / s)


def initial_seeds(seed: int, count: int = SEED_COUNT) -> list[tuple[float, float]]:
    """Positions normalisees (0..1) des germes de l'etat initial, deterministes pour une graine."""
    rng = random.Random(seed)
    return [(rng.random(), rng.random()) for _ in range(count)]


def kick_seeds(kick_index: int, count: int = KICK_SEEDS) -> list[tuple[float, float]]:
    """Positions normalisees (0..1) des germes semes par le kick numero `kick_index` (deterministes)."""
    rng = random.Random(kick_index * 7919 + 17)
    return [(rng.random(), rng.random()) for _ in range(count)]


def steps_for(acc: float, dt: float, speed: float, energy: float) -> tuple[int, float]:
    """Pas a faire pour cette image et reste de l'accumulateur. `dt` deja multiplie par le master du tempo."""
    acc += max(dt, 0.0) * STEPS_PER_S * max(speed, 0.0) * (1.0 + max(energy, 0.0))
    whole = math.floor(acc)
    return min(whole, MAX_STEPS), acc - whole
