"""Palette « labyrinthe » du fond genere : logique pure de la reaction-diffusion (modele Gray-Scott, motif de Turing).

Python pur (stdlib seulement). La simulation elle-meme tourne sur le GPU (`engine/reaction.py`, `gl_shaders/rd_step.frag`) ;
ici : parametres du modele selon la forme voulue, taille de la grille, germes et nombre de pas par image.
"""
from __future__ import annotations

import math
import random

# (F, K) : taches qui se repoussent et se divisent jusqu'a remplir l'ecran. La valeur classique (0,0367 ; 0,0649) donnait des taches
# stables qui ne se divisaient pas (29 taches, 2 % de l'ecran apres 12000 pas) : on part du quart du chemin vers MAZE (mesure).
SPOTS = (0.0348, 0.0629)
MAZE = (0.0290, 0.0570)         # (F, K) : taches soudees en lignes sinueuses (labyrinthe)
BASE_GRID = (384, 216)          # grille de simulation a l'echelle 1 (independante de la fenetre)
STEPS_PER_S = 600.0             # pas de simulation par seconde a vitesse 1
MAX_STEPS = 24                  # plafond par image (au-dela, l'exces est jete : pas de retard qui s'accumule)
SEED_COUNT = 40                 # germes de l'etat initial
SEED_RADIUS = 3.0               # rayon d'un germe, en cellules
KICK_SEEDS = 3                  # germes semes par kick
DEAD_LEVEL = 0.002              # moyenne de V sous laquelle le motif est considere eteint
WATCH_S = 2.0                   # periode du garde-fou anti-extinction


def feed_kill(shape: float) -> tuple[float, float]:
    """(F, K) du modele : 0 = taches, 1 = labyrinthe, interpolation lineaire (forme bornee a 0..1)."""
    s = min(max(float(shape), 0.0), 1.0)
    return (SPOTS[0] + (MAZE[0] - SPOTS[0]) * s, SPOTS[1] + (MAZE[1] - SPOTS[1]) * s) if 0.0 < s < 1.0 else (SPOTS if s == 0.0 else MAZE)


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
