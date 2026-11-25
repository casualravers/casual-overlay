"""Overlay Texte (#199) : calendrier de la vague de lettres (« ola »), du remplissage par reaction-diffusion (tuile rejouee par une vague) et de ses remises a zero.

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


# ---- Remplissage (vague 2 de #199) : un motif de reaction-diffusion est calcule UNE fois sur une petite tuile periodique (avec, pour chaque
# cellule, l'instant ou elle a ete colonisee), puis rejoue par une vague : lettre par lettre (rang), du bas vers le haut de chaque lettre
# (hauteur), la croissance locale suivant la tuile. Aucune simulation par image : le cout est quelques lectures de texture par pixel du texte.
TILE_SIZE = 512                      # cote de la tuile (cellules), periodique
TILE_STEPS = 2000                    # pas de Gray-Scott pour la murir (la plupart des cellules sont colonisees avant 1200)
TILE_STEPS_PER_FRAME = 8             # pas par image pendant la preparation (la moitie si le GPU est charge)
TILE_SEED_BLOCK = 24                 # un germe par bloc de N x N cellules (colonisation reguliere)
TILE_THRESHOLD = 0.12                # V au-dela duquel une cellule est dite colonisee
TILE_VERSION = 1                     # a incrementer quand le calcul de la tuile change (invalide le cache)
FILL_SHAPE = 1.5                     # forme du motif (axe 0..2 de core.reaction.shape_feed_kill) : entre lignes et taches, corail
FILL_DIFF = 0.20                     # facteur sur les diffusions de Gray-Scott : plus petit = motif plus petit
CELLS_PER_TEXEL = 2.0                # cellules de la tuile par pixel de la texture du texte a la finesse 1 (texte rendu en police de 128 px)
FILL_DUR_MIN, FILL_DUR_MAX = 0.5, 3.0    # duree d'une lettre (front + croissance) : 3 s au plus
FILL_STAGGER_MAX = 8.0               # ecart de depart entre la premiere et la derniere lettre
FRONT_SHARE = 0.45                   # part de la duree d'une lettre prise par le front bas -> haut ; le reste = croissance locale de la tuile
FILL_SOFT_S = 0.18                   # fondu d'apparition d'une cellule (secondes)


def tile_key() -> str:
    """Cle du cache de la tuile : decrit tout ce qui change son contenu (une autre cle = le cache est ignore et la tuile recalculee)."""
    return f"v{TILE_VERSION}-{TILE_SIZE}-{TILE_STEPS}-{FILL_SHAPE}-{FILL_DIFF}-{TILE_SEED_BLOCK}-{TILE_THRESHOLD}"


def tile_batch(done: int, slow: bool) -> int:
    """Pas de preparation de la tuile a faire a cette image (0 une fois les TILE_STEPS faits). `slow` (GPU charge) : moitie moins, un au moins."""
    left = TILE_STEPS - int(done)
    if left <= 0:
        return 0
    return min(left, max(TILE_STEPS_PER_FRAME // 2, 1) if slow else TILE_STEPS_PER_FRAME)


def clean_fill(dur: float, stagger: float) -> tuple[float, float]:
    """(duree d'une lettre, ecart de depart) bornes : la duree dans [0,5 ; 3] s, l'ecart dans [0 ; 8] s."""
    return min(max(float(dur), FILL_DUR_MIN), FILL_DUR_MAX), min(max(float(stagger), 0.0), FILL_STAGGER_MAX)


def fill_times(dur: float) -> tuple[float, float]:
    """(front, croissance) en secondes pour une lettre de duree `dur` : le front monte du bas au haut de la lettre pendant `front`, puis la
    tuile finit de pousser pendant `growth` (front + croissance = dur)."""
    front = float(dur) * FRONT_SHARE
    return front, float(dur) - front


def fill_tau(ft: float, rank: float, rise: float, stagger: float, front: float) -> float:
    """Temps local d'un pixel : `ft` secondes depuis la remise a zero, moins le depart de sa lettre (`rank` 0 = la plus a gauche, 1 = la plus a
    droite, x `stagger`) et le retard du front (`rise` 0 = bas de la lettre, 1 = haut, x `front`). Meme formule que scene.frag."""
    return float(ft) - min(max(float(rank), 0.0), 1.0) * float(stagger) - min(max(float(rise), 0.0), 1.0) * float(front)


def fill_reveal(tau: float, arrival: float, growth: float) -> float:
    """Part (0 a 1) de la matiere visible d'une cellule de la tuile : elle apparait a `arrival` (0 a 1 de la tuile) x `growth` secondes du
    temps local, avec un fondu de FILL_SOFT_S, puis reste. Meme formule que scene.frag."""
    start = min(max(float(arrival), 0.0), 1.0) * float(growth)
    return _smoothstep(start, start + FILL_SOFT_S, float(tau))


def fill_total(dur: float, stagger: float) -> float:
    """Duree de la vague entiere : depart de la derniere lettre + duree d'une lettre (+ fondu) ; ensuite le motif ne bouge plus."""
    return float(stagger) + float(dur) + FILL_SOFT_S

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
