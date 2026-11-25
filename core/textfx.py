"""Overlay Texte (#199) : calendrier de la vague de lettres (« ola ») et des remises a zero du remplissage par reaction-diffusion.

Python pur (stdlib seulement) : le temps est injecte, aucune fenetre, aucun GL. Spec : docs/plans/2026-11-25-texte-ola-remplissage-199.md.

Vague : un cycle dure `every` s ; au debut, un balayage de `dur` s : de 0 a dur/2 les lettres s'eteignent en fondu de gauche a droite
(la lettre de rang 0 d'abord, celle de rang 1 en dernier), de dur/2 a dur elles se rallument dans le meme sens ; ensuite le texte entier
reste visible jusqu'au cycle suivant. Le rang d'une lettre (0 a 1) est sa position de gauche a droite sur toute la largeur du texte.
"""
from __future__ import annotations

import math

OLA_DUR_MIN, OLA_DUR_MAX = 1.0, 10.0
FADE_MIN = 0.02                      # un fondu nul ferait une marche : borne basse (secondes)


def _smoothstep(a: float, b: float, x: float) -> float:
    if b <= a:
        return 1.0 if x >= b else 0.0
    t = min(max((x - a) / (b - a), 0.0), 1.0)
    return t * t * (3.0 - 2.0 * t)


def clean_ola(dur: float, every: float, fade: float | None = None) -> tuple[float, float, float]:
    """(duree, periode, fondu) bornes : la duree d'un balayage dans [1, 10] s, la periode au moins egale a la duree, le fondu d'une lettre
    au plus la demi-duree (sinon la derniere lettre ne finirait pas de s'eteindre avant que la premiere revienne)."""
    dur = min(max(float(dur), OLA_DUR_MIN), OLA_DUR_MAX)
    every = max(float(every), dur)
    fade = dur / 2.0 if fade is None else min(max(float(fade), FADE_MIN), dur / 2.0)
    return dur, every, fade


def ola_alpha(rank: float, tau: float, dur: float, fade: float) -> float:
    """Opacite (0 a 1) de la lettre de rang `rank` a `tau` secondes du debut du cycle. Hors du balayage (tau >= dur, ou tau < 0) : 1."""
    dur, _every, fade = clean_ola(dur, dur, fade)
    r = min(max(float(rank), 0.0), 1.0)
    half = dur / 2.0
    span = half - fade                                  # les debuts des fondus se repartissent sur [0, span] selon le rang
    if tau < 0.0 or tau >= dur:
        return 1.0
    if tau < half:                                      # extinction
        start = r * span
        return 1.0 - _smoothstep(start, start + fade, tau)
    start = half + r * span                             # rallumage
    return _smoothstep(start, start + fade, tau)


class OlaClock:
    """Horloge de la vague. `update(now, on, dur, every)` rend `(tau, relight)` : `tau` = secondes depuis le debut du cycle (None case
    decochee), `relight` = vrai une seule fois par cycle, quand les lettres commencent a se rallumer (mi-balayage). Le premier cycle
    demarre a l'activation de la case ; la decocher puis la recocher redemarre un balayage."""

    def __init__(self) -> None:
        self.t0: float | None = None
        self._prev: float | None = None

    def update(self, now: float, on: bool, dur: float, every: float) -> tuple[float | None, bool]:
        if not on:
            self.t0 = self._prev = None
            return None, False
        dur, every, _fade = clean_ola(dur, every)
        now = float(now)
        if self.t0 is None:
            self.t0, self._prev = now, None
        rel = now - self.t0
        tau = rel % every
        relight = False
        if self._prev is not None:
            half = dur / 2.0
            n_prev = math.floor((self._prev - self.t0 - half) / every)
            n_now = math.floor((rel - half) / every)
            relight = n_now > n_prev and n_now >= 0
        self._prev = now
        return tau, relight


FILL_GRID_BASE = 2048                # cote long de la grille de simulation a l'echelle 1 (cellules) : motif fin, choisi sur maquette
FILL_GRID_MIN, FILL_GRID_MAX = 256, 3072
FILL_STEPS_PER_S = 1440.0            # pas de Gray-Scott par seconde a vitesse 1 (24 par image a 60 Hz, le plafond)
FILL_MAX_STEPS = 24                  # plafond par image (l'exces est jete : pas de retard qui s'accumule)
FILL_SETTLE_STEPS = 9000             # pas apres lesquels le motif est fige jusqu'a la remise a zero suivante (~6 s a vitesse 1) : le cout retombe a zero
FILL_SHAPE = 1.5                     # forme du motif (axe 0..2 de core.reaction.shape_feed_kill) : entre lignes et taches, corail
FILL_DIFF = 0.20                     # facteur sur les diffusions de Gray-Scott : plus petit = motif plus petit
FILL_SEED_BLOCK = 24                 # un germe par bloc de N x N cellules du masque (colonisation reguliere, aucune zone oubliee)


def fill_grid_size(aspect: float, scale: float) -> tuple[int, int]:
    """Taille (largeur, hauteur) de la grille du remplissage pour un texte de rapport `aspect` (largeur / hauteur). Le cote long vaut
    FILL_GRID_BASE x `scale` (borne), l'autre suit le rapport : plus d'echelle = grille plus fine = motif plus fin dans les lettres."""
    aspect = max(float(aspect), 0.05)
    long_side = min(max(round(FILL_GRID_BASE * float(scale)), FILL_GRID_MIN), FILL_GRID_MAX)
    if aspect >= 1.0:
        return long_side, max(round(long_side / aspect), 2)
    return max(round(long_side * aspect), 2), long_side


def fill_steps(acc: float, dt: float, speed: float, slow: bool) -> tuple[int, float]:
    """Pas de simulation a faire a cette image et reste de l'accumulateur. `slow` (GPU charge, PerfGuard) : moitie moins de pas, un au moins."""
    acc += max(float(dt), 0.0) * FILL_STEPS_PER_S * max(float(speed), 0.0)
    n = int(acc)
    acc -= n
    if slow:
        n = max(n // 2, 1) if n > 0 else 0
    if n > FILL_MAX_STEPS:
        n, acc = FILL_MAX_STEPS, 0.0
    return n, acc


class FillClock:
    """Quand le motif de reaction-diffusion du texte repart de zero (germes neufs) : une premiere fois, a chaque changement de texte, au
    rallumage de la vague quand elle est cochee, sinon toutes les `every` s. `update` rend vrai au plus une fois par appel : un texte qui
    change au meme instant que le rallumage ne remet a zero qu'une fois."""

    def __init__(self) -> None:
        self.text = None
        self.t_reset: float | None = None

    def update(self, now: float, text_sig, ola_on: bool, relight: bool, every: float) -> bool:
        now = float(now)
        if self.t_reset is None or text_sig != self.text:
            self.text, self.t_reset = text_sig, now
            return True
        if ola_on:
            due = bool(relight)                          # avec la vague, seul le rallumage compte (pas la periode N s)
        else:
            due = now - self.t_reset >= max(float(every), 1.0)
        if due:
            self.t_reset = now
        return due
