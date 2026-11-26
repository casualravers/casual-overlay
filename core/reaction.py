"""Palette « labyrinthe » du fond genere : logique pure de la reaction-diffusion (modele Gray-Scott, motif de Turing).

Python pur (stdlib seulement). La simulation elle-meme tourne sur le GPU (`engine/reaction.py`, `gl_shaders/rd_step.frag`) ;
ici : parametres du modele selon la forme voulue, taille de la grille, germes et nombre de pas par image.
"""
from __future__ import annotations

import math
import random
from typing import NamedTuple

# (F, K) : taches qui se repoussent et se divisent jusqu'a remplir l'ecran (valeur classique ; mesure en RG32F : 238 taches, aucun contact,
# motif toujours actif apres 12000 pas). En RG16F elle semblait ne jamais se diviser : c'etait un artefact d'arrondi (voir engine/reaction.py).
SPOTS = (0.0367, 0.0649)
MAZE = (0.0290, 0.0570)         # (F, K) : taches soudees en lignes sinueuses (labyrinthe)
BASE_GRID = (384, 216)          # grille de simulation a l'echelle 1 (independante de la fenetre)
STEPS_PER_S = 600.0             # pas de simulation par seconde a vitesse 1
MAX_STEPS = 24                  # plafond par image (au-dela, l'exces est jete : pas de retard qui s'accumule)
SCALE_MIN, SCALE_MAX = 0.3, 2.0     # taille du motif (grille de 1280 x 720 a 0,3 : lignes tres fines)
# Cellules x pas au plus par image : une grille fine (1280 x 720 = 11 fois plus de cellules qu'a l'echelle 1) ne peut pas faire autant de
# pas par image. Mesure (Radeon integree) : 0,85 ns par cellule et par pas, soit ~4,2 ms a 5 millions ; a l'echelle 0,3 la simulation tourne
# alors a ~5 pas par image (300 / s au lieu de 600 : le motif evolue deux fois moins vite), a l'echelle 0,5 a 10 pas (inchange).
MAX_CELL_STEPS = 5_000_000
# Demi-precision (#225) : l'etat en RG16F (W = 1 - U, voir engine/reaction.py) rend un pas ~2 fois moins cher aux grilles fines (mesure, Radeon
# integree : 0,67 -> 0,36 ms a 1280 x 720, 0,30 -> 0,15 ms a 768 x 432). Le plafond monte d'autant : 10 pas par image a 1280 x 720 (le motif fin
# retrouve sa vitesse, a cout a peu pres egal) ; HALF_STEP_COST = cout relatif d'un pas pour la securite GPU.
MAX_CELL_STEPS_HALF = 9_500_000
HALF_STEP_COST = 0.55
RESIZE_MIN_S = 0.4              # echelle automatisee : au plus un changement de taille de la grille par ce delai
SEED_COUNT = 40                 # germes de l'etat initial
SEED_RADIUS = 3.0               # rayon d'un germe, en cellules
KICK_SEEDS = 3                  # germes semes par kick
# Apparitions (#187) : bg_rd_spawn = part des kicks qui sement ; bg_rd_bud = un germe ne prend que s'il touche de la matiere (rd_step.frag)
BUD_SEEDS = 4                   # candidats par kick contre le motif (une partie tombe dans le vide et est jetee)
BUD_LEVEL = 0.1                 # V au-dessus duquel une lecture autour du germe compte comme matiere (la matiere visible est au-dessus de 0,2)
DEAD_LEVEL = 0.002              # moyenne de V sous laquelle le motif est considere eteint
WATCH_S = 2.0                   # periode du garde-fou anti-extinction
RESEED_DELAY_S = 0.4            # symetrie ou branches changees : le motif repart des germes apres ce delai sans nouveau changement

# Zones de formes et symetries de colonisation (docs/plans/2026-11-01-labyrinthe-evolution-design.md).
# (F, K) : grosses plages pleines percees de trous (une seule masse connexe, ~50 % de l'image). Trouve par balayage (tools/rd_sweep.py,
# RG32F, 6000 pas) : couverture 0,49, motif toujours actif, chemin lignes -> taches vivant ; voisins sains de tous cotes (0,048 ; 0,060,
# plus proche des 65 % vises, n'est qu'a 0,001 en K de l'envahissement total : ecarte). La bande de transition est etroite : au-dessus de
# F 0,04, K 0,058 ou moins remplit tout l'ecran.
BLOB = (0.0440, 0.0600)
SYM_MODES = ("none", "mirror_lr", "mirror_tb", "quad", "rot")
ZONE_CELLS = (5, 3)             # reseau periodique du bruit des zones, en cellules sur la grille (~un tiers de la hauteur)
ZONE_SPEED = 0.08               # cycles de derive des zones par seconde a bg_rd_drift = 1
FREE_NOISE = 1e-3               # bruit asymetrique ajoute a V a chaque pas, x Liberte (seulement avec une symetrie)
FREE_ZONE = 0.35                # part du bruit non replie dans la carte des zones, x Liberte
FREE_JITTER = 0.04              # decalage max d'une copie de germe (fraction de la hauteur), x Liberte
LAYER_SPREAD = 0.05             # ecart des seuils des couches a bg_rd_layers = 1 (V plafonne vers 0,35 : 0,30 garde la couche du dessus visible)
MAX_PENDING_SEEDS = 32          # file des germes en attente (orbites), les plus anciens jetes au-dela
# Zones vides et zones denses (bg_rd_void) : un bruit a grande echelle, independant de celui des formes, qui derive. Ou il depasse un
# seuil, (F, K) glisse vers VOID_FK (au-dela de la limite d'existence du motif : la matiere s'eteint, progressivement sur le bord) ;
# ou il est bas, la forme est tiree vers les grosses taches (DENSE_PULL).
VOID_FK = (0.037, 0.071)
VOID_CELLS = (3, 2)             # reseau periodique du bruit des vides, en cellules sur la grille (zones plus grandes que celles des formes)
VOID_THR_OFF = 0.92             # seuil du bruit a Vide = 0 : au-dessus de presque tout le bruit (mesure : 0 % de vide)
VOID_THR_FULL = 0.6             # seuil a Vide = 1 : un peu plus de la moitie de l'image vide (mesure sur 12 cycles de derive : 0,53)
VOID_EDGE = 0.1                 # demi-largeur de la transition (bords progressifs : le motif s'amincit avant de disparaitre)
DENSE_THR = 0.5                 # sous ce niveau de bruit, zone dense, x Vide (mesure : ~13 % de l'image strictement dense)
DENSE_PULL = 0.9                # part de la forme tiree vers les taches dans une zone dense, a Vide = 1
VOID_SPEED = 0.7                # vitesse de derive des vides par rapport a celle des formes
# Masque d'expansion (#178) : une carte 0..1 (1 = la matiere vit, 0 = eteinte, bord progressif) qui limite ou le motif a le droit d'exister.
# Meme mecanisme que le Vide (rd_zone.frag : (F, K) glisse vers VOID_FK la ou la carte est basse), formules repliquees en GLSL.
MASK_SHAPES = ("none", "circles", "bands", "gradient", "noise")
MASK_SPEED = 0.05               # cycles de derive des zones par seconde a bg_rd_mask_drift = 1 (une zone qui bouge plus vite que le motif ne se remplit pas)
MASK_EDGE_MIN, MASK_EDGE_SOFT = 0.01, 0.4    # demi-largeur du bord (unites de maille ; unites de valeur pour le degrade) : mini, x douceur
MASK_NOISE_EDGE_MIN, MASK_NOISE_EDGE_SOFT = 0.01, 0.15
MASK_SALT = 11.0                # decalage des indices de cellule du bruit du masque (hache(0, 0) = 0 : un reseau de 1 cellule serait plat)
MASK_NOISE_CENTER = 0.6         # seuil du bruit a taille 0,5 (calibre sur 40 positions de derive a 3 zones : environ la moitie de l'image)
MASK_NOISE_SPREAD = 0.5       # seuil du bruit = 0,5 + (taille - 0,5) x ceci (la part vivante suit a peu pres `taille`)


def feed_kill(shape: float) -> tuple[float, float]:
    """(F, K) du modele : 0 = taches, 1 = labyrinthe, interpolation lineaire (forme bornee a 0..1)."""
    s = min(max(float(shape), 0.0), 1.0)
    return (SPOTS[0] + (MAZE[0] - SPOTS[0]) * s, SPOTS[1] + (MAZE[1] - SPOTS[1]) * s) if 0.0 < s < 1.0 else (SPOTS if s == 0.0 else MAZE)


class Mask(NamedTuple):
    """Masque d'expansion (forme none = aucune limite) pour un pas de simulation ; `t` = temps de derive integre (cycles)."""
    shape: str = "none"         # MASK_SHAPES
    count: float = 3.0          # nombre de zones (cellules / bandes / periodes / finesse du bruit)
    size: float = 0.5           # part de l'image ou la matiere vit
    angle: float = 0.0          # rotation de la disposition (degres)
    soft: float = 0.4           # douceur des bords 0..1
    invert: bool = False        # la matiere vit hors de la forme
    t: float = 0.0              # derive integree


class Zone(NamedTuple):
    """Reglages des zones et de la symetrie pour un pas de simulation (mix 0 et sym none = comportement d'origine)."""
    mix: float = 0.0            # variete des zones (ecart de forme autour de la forme de base)
    t: float = 0.0              # temps de derive integre (cycles)
    sym: str = "none"           # symetrie de colonisation (SYM_MODES)
    branches: int = 4           # branches de la rotation
    free: float = 0.0           # liberte de la symetrie
    aspect: float = 16 / 9      # largeur / hauteur de la grille (rotations rondes a l'ecran)
    void: float = 0.0           # zones vides (et denses) : 0 = tout est rempli comme avant
    mask: Mask = Mask()         # masque d'expansion (#178) : aucun par defaut


def shape_feed_kill(s: float) -> tuple[float, float]:
    """(F, K) sur l'axe interne 0..2 : 0 = points, 1 = lignes, 2 = taches (lineaire par morceaux, borne). Sur 0..1 : exactement feed_kill."""
    s = min(max(float(s), 0.0), 2.0)
    if s <= 1.0:
        return feed_kill(s)
    if s == 2.0:
        return BLOB
    t = s - 1.0
    return (MAZE[0] + (BLOB[0] - MAZE[0]) * t, MAZE[1] + (BLOB[1] - MAZE[1]) * t)


def void_threshold(void: float) -> float:
    """Seuil du bruit des vides : decroit de VOID_THR_OFF (aucun vide) a VOID_THR_FULL ; `void` borne a 0..1."""
    v = min(max(float(void), 0.0), 1.0)
    return VOID_THR_OFF + (VOID_THR_FULL - VOID_THR_OFF) * v


def _zhash(cx: float, cy: float) -> float:
    """Replique de zhash (rd_zone.frag) : hachage sans sin d'une cellule."""
    p = [(v * 0.1031) % 1.0 for v in (cx, cy, cx)]
    d = p[0] * (p[1] + 33.33) + p[1] * (p[2] + 33.33) + p[2] * (p[0] + 33.33)
    p = [v + d for v in p]
    return ((p[0] + p[1]) * p[2]) % 1.0


def _pnoise(qx: float, qy: float, cx: float, cy: float, salt: float = 0.0) -> float:
    """Replique de pnoise : bruit de valeur periodique (indices modulo la maille)."""
    ix, iy = math.floor(qx), math.floor(qy)
    fx, fy = qx - ix, qy - iy
    fx, fy = fx * fx * (3.0 - 2.0 * fx), fy * fy * (3.0 - 2.0 * fy)
    x0, x1, y0, y1 = ix % cx + salt, (ix + 1) % cx + salt, iy % cy + salt, (iy + 1) % cy + salt   # salt : la cellule (0, 0) ne hache plus a 0
    a = _zhash(x0, y0) + (_zhash(x1, y0) - _zhash(x0, y0)) * fx
    b = _zhash(x0, y1) + (_zhash(x1, y1) - _zhash(x0, y1)) * fx
    return a + (b - a) * fy


def _fold(x: float, y: float, sym: str, branches: int, aspect: float) -> tuple[float, float]:
    """Replique de fold (rd_zone.frag) : coordonnees repliees par la symetrie de colonisation."""
    if sym in ("mirror_lr", "quad"):
        x = 0.5 - abs(x - 0.5)
    if sym in ("mirror_tb", "quad"):
        y = 0.5 - abs(y - 0.5)
    if sym == "rot":
        dx, dy = (x - 0.5) * aspect, y - 0.5
        sector = 2.0 * math.pi / max(int(branches), 2)
        a = math.atan2(dy, dx) % sector
        a = min(a, sector - a)
        r = math.hypot(dx, dy)
        x, y = 0.5 + r * math.cos(a) / aspect, 0.5 + r * math.sin(a)
    return x, y


def mask_cells(count: float) -> tuple[int, int]:
    """Reseau periodique du bruit du masque : 3 zones = VOID_CELLS (3 x 2), 1 -> (2, 2) (un reseau d'une cellule serait plat), 8 -> (8, 5)."""
    c = min(max(float(count), 1.0), 8.0)
    return max(2, round(c)), max(2, round(c * 2.0 / 3.0))


def mask_uniforms(mask: Mask) -> list[tuple[str, object]]:
    """Uniforms de rd_zone.frag pour le masque (la meme preparation que mask_value : le shader ne refait que la geometrie)."""
    shape = MASK_SHAPES.index(mask.shape) if mask.shape in MASK_SHAPES else 0
    count = min(max(float(mask.count), 1.0), 8.0)
    size = min(max(float(mask.size), 0.0), 1.0)
    soft = min(max(float(mask.soft), 0.0), 1.0)
    a = math.radians(mask.angle)
    noise = mask.shape == "noise"
    edge = (MASK_NOISE_EDGE_MIN + MASK_NOISE_EDGE_SOFT * soft) if noise else (MASK_EDGE_MIN + MASK_EDGE_SOFT * soft)
    return [("u_mask", shape), ("u_mask_count", count), ("u_mask_size", size), ("u_mask_rot", (math.cos(a), math.sin(a))), ("u_mask_edge", edge),
            ("u_mask_r", min(math.sqrt(size / math.pi), 0.5)), ("u_mask_thr", MASK_NOISE_CENTER + (size - 0.5) * MASK_NOISE_SPREAD),
            ("u_mask_cells", tuple(float(c) for c in mask_cells(count))), ("u_mask_invert", int(bool(mask.invert))), ("u_mask_t", float(mask.t)),
            ("u_mask_salt", MASK_SALT)]


def mask_value(mask: Mask, x: float, y: float, aspect: float, sym: str = "none", branches: int = 4) -> float:
    """Masque d'expansion au point (x, y) de l'image (0..1, y vers le haut comme `v_uv` du shader) : 1 = la matiere vit, 0 = eteinte,
    bord progressif. `none` = 1. Replique de rd_zone.frag (qui en tire la part eteinte, 1 - ceci)."""
    if mask.shape not in MASK_SHAPES[1:]:
        return 1.0
    x, y = _fold(x, y, sym, branches, aspect)
    px, py = (x - 0.5) * aspect, y - 0.5                     # repere isotrope centre (hauteur = 1)
    a = math.radians(mask.angle)
    ca, sa = math.cos(a), math.sin(a)
    rx, ry = px * ca + py * sa, -px * sa + py * ca
    count = min(max(float(mask.count), 1.0), 8.0)
    size = min(max(float(mask.size), 0.0), 1.0)
    soft = min(max(float(mask.soft), 0.0), 1.0)
    if mask.shape == "noise":
        cx, cy = mask_cells(count)
        ux, uy = rx / aspect + 0.5, ry + 0.5                # retour en coordonnees de l'image (cellules du Vide a 3 zones)
        qx, qy = ux * cx + mask.t + 3.1, uy * cy - 0.6 * mask.t + 1.3
        n = 0.7 * _pnoise(qx, qy, cx, cy, MASK_SALT) + 0.3 * _pnoise(qx * 2.0 + 1.7, qy * 2.0 + 1.7, cx * 2.0, cy * 2.0, MASK_SALT)
        f = (MASK_NOISE_CENTER + (size - 0.5) * MASK_NOISE_SPREAD) - n
        e = MASK_NOISE_EDGE_MIN + MASK_NOISE_EDGE_SOFT * soft
    else:
        qx, qy = rx * count + mask.t, ry * count + 0.6 * mask.t   # unites de maille ; bandes de periode 1 (t = 1 boucle), cercles a t = 5
        fx, fy = qx - math.floor(qx), qy - math.floor(qy)
        e = MASK_EDGE_MIN + MASK_EDGE_SOFT * soft
        if mask.shape == "circles":
            r = min(math.sqrt(size / math.pi), 0.5)
            f = r - math.hypot(fx - 0.5, fy - 0.5)
        elif mask.shape == "bands":
            f = size * 0.5 - abs(fx - 0.5)
        else:                                                # gradient : rampe triangulaire
            f = size - abs(fx * 2.0 - 1.0)
    t = min(max((f + e) / (2.0 * e), 0.0), 1.0)
    m = t * t * (3.0 - 2.0 * t)
    return 1.0 - m if mask.invert else m


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


def queue_seeds(pending: list, new: list) -> list:
    """File des germes en attente (orbites : plus de 4 par kick) : ajout a la fin, au plus MAX_PENDING_SEEDS, les plus recents gardes."""
    return (list(pending) + list(new))[-MAX_PENDING_SEEDS:]


def grid_size(scale: float) -> tuple[int, int]:
    """Taille de la grille pour une echelle (0,3..2) : plus grande echelle = grille plus petite = motif plus gros a l'ecran."""
    s = min(max(float(scale), SCALE_MIN), SCALE_MAX)
    return round(BASE_GRID[0] / s), round(BASE_GRID[1] / s)


def step_load(scale: float, half: bool = False) -> float:
    """Charge GPU de la simulation a 60 images/s rapportee a l'echelle 1 en RG32F (10 pas sur 384 x 216) : 1 a l'echelle 1, ~4 a 0,5, ~5 a 0,3
    (les pas par image sont plafonnes par MAX_CELL_STEPS). `half` : etat en demi-precision, un pas compte HALF_STEP_COST et le plafond est
    MAX_CELL_STEPS_HALF (10 pas a 0,3 : un peu plus de charge qu'en RG32F, mais le motif fin va a pleine vitesse)."""
    w, h = grid_size(scale)
    return min(10, max_steps(w * h, half)) * w * h * (HALF_STEP_COST if half else 1.0) / (10.0 * BASE_GRID[0] * BASE_GRID[1])


def max_steps(cells: int, half: bool = False) -> int:
    """Pas par image au plus pour une grille de `cells` cellules (au moins 1, au plus MAX_STEPS) ; `half` : etat en demi-precision."""
    return max(1, min(MAX_STEPS, (MAX_CELL_STEPS_HALF if half else MAX_CELL_STEPS) // max(int(cells), 1)))


def initial_seeds(seed: int, count: int = SEED_COUNT) -> list[tuple[float, float]]:
    """Positions normalisees (0..1) des germes de l'etat initial, deterministes pour une graine."""
    rng = random.Random(seed)
    return [(rng.random(), rng.random()) for _ in range(count)]


def kick_seeds(kick_index: int, count: int = KICK_SEEDS) -> list[tuple[float, float]]:
    """Positions normalisees (0..1) des germes semes par le kick numero `kick_index` (deterministes)."""
    rng = random.Random(kick_index * 7919 + 17)
    return [(rng.random(), rng.random()) for _ in range(count)]


def kick_spawns(kick_index: int, rate: float) -> bool:
    """Le kick numero `kick_index` seme-t-il ? `rate` = part des kicks (bornee 0..1, invalide = 1) ; tirage deterministe par kick : a 100 %
    tous les kicks sement (comme avant), a 0 % aucun."""
    try:
        rate = float(rate)
    except (TypeError, ValueError):
        rate = 1.0
    if not math.isfinite(rate) or rate >= 1.0:
        return True
    if rate <= 0.0:
        return False
    return random.Random(kick_index * 104729 + 3).random() < rate


def kick_candidates(bud: bool) -> int:
    """Germes tires par kick : plus de candidats quand ils ne prennent que contre le motif."""
    return BUD_SEEDS if bud else KICK_SEEDS


def steps_for(acc: float, dt: float, speed: float, energy: float, cells: int | None = None, half: bool = False) -> tuple[int, float]:
    """Pas a faire pour cette image et reste de l'accumulateur. `dt` deja multiplie par le master du tempo ; `cells` = taille de la
    grille (plafonne les pas d'une grille fine, voir MAX_CELL_STEPS ; None = plafond MAX_STEPS seul) ; `half` : etat en demi-precision
    (plafond MAX_CELL_STEPS_HALF)."""
    acc += max(dt, 0.0) * STEPS_PER_S * max(speed, 0.0) * (1.0 + max(energy, 0.0))
    whole = math.floor(acc)
    return min(whole, MAX_STEPS if cells is None else max_steps(cells, half)), acc - whole


# Fluidite (issue #189) : l'image affichee n'est pas l'etat brut de la simulation mais une copie qui le rattrape en douceur (lissage
# exponentiel, une passe a la taille de la grille). Une soudure ou une rupture, qui fait franchir le seuil a un pont en une image, devient un
# gonflement / amincissement continu. La simulation elle-meme n'est pas touchee (formes, vitesse, couches identiques).
SMOOTH_TAU_MAX = 0.25           # constante de temps de l'affichage a bg_rd_smooth = 1, en secondes
SEED_REACH = 2.0                # germe doux : profil gaussien coupe a deux rayons
# Hauteur du germe doux : la simulation fait grandir la tache elle-meme (naissance continue) au lieu d'un disque plein pose d'un coup.
# Mesure (check_rd_fluide, kicks toutes les 20 images) : plus gros saut local 80 cellules avec l'ancien disque de V = 1, 52 a 0,4 sans
# lissage, 6 a 10 a 0,4 avec la fluidite 0,5 ; a 0,3 la tache nait encore mais plus lentement (V 0,29 apres 300 pas).
SEED_PEAK = 0.4


def display_alpha(dt: float, smooth: float) -> float:
    """Part de l'ecart a l'etat que l'affichage rattrape pendant `dt` secondes (temps REEL de l'image, pas multiplie par le tempo : un motif
    fige doit quand meme etre rejoint). `smooth` 0..1 : 0 = l'affichage suit l'etat sans retard (rendu d'avant), 1 = tau de 0,25 s."""
    try:
        s = min(max(float(smooth), 0.0), 1.0)
    except (TypeError, ValueError):
        s = 0.0
    if not s == s or s <= 0.0:                              # nan = invalide
        return 1.0
    if dt <= 0.0:
        return 0.0
    return 1.0 - math.exp(-float(dt) / (SMOOTH_TAU_MAX * s))


def seed_value(r: float, radius: float) -> float:
    """V pose par un germe a `r` cellules de son centre (profil gaussien : SEED_PEAK au centre, moitie au rayon, 0 au-dela de SEED_REACH
    rayons). Avec le lissage de l'affichage, la tache nait au centre et grandit au lieu d'apparaitre d'un coup. Meme formule que `rd_step.frag`."""
    if radius <= 0.0 or r > SEED_REACH * radius:
        return 0.0
    return SEED_PEAK * math.exp(-math.log(2.0) * (r / radius) ** 2)
