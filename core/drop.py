"""Drop plus impactant (issue #89) : a chaque drop, un STYLE tire au hasard (jamais deux fois le meme de suite, les cinq defilent) et ses courbes.

Python pur (stdlib), horloge injectee : `DropFx.trigger(now)` choisit le style, `DropFx.values(now)` rend ce que le rendu applique a cet instant
(`DropValues`). Socle commun a tous les styles : une RAFALE d'effets (~1,5 s : les quatre effets par couche forces et pousses, une onde de choc
depuis le centre, l'aberration et le glitch au kick du drop) ; puis une signature propre au style :

  onde       onde de choc geante + aberration forte
  coup       coup de zoom vers l'avant qui revient en rebondissant + secousse
  fracture   l'image se casse en tranches decalees, couleurs separees
  negatif    passage en negatif, puis retour en tournant de teinte
  implosion  l'image est aspiree vers le centre puis relachee d'un coup (flash a la relache)

Le flash blanc est dose par style (jamais plein ecran en mode Foudre). Tout revient EXACTEMENT au neutre apres `DURATION`.
"""

from __future__ import annotations

import math
import random
from dataclasses import dataclass

STYLES = ("onde", "coup", "fracture", "negatif", "implosion")
DURATION = 2.0               # secondes : au-dela, plus rien (valeurs neutres)
BURST_S = 1.5                # duree de la rafale d'effets
FADE_FROM = 0.7              # part de DURATION a partir de laquelle tout s'eteint en douceur (garantit le neutre exact a DURATION)
BASE_FORCE = (0.6, 0.8, 0.8, 0.5)      # rafale commune : ondulation, onde de choc, aberration, glitch (ordre de u_fx_bg / u_fx_logo)


@dataclass(frozen=True)
class DropValues:
    boost: float = 0.0                                  # 0..1 : les effets actifs sont pousses (multiplicateur 1 + 1,5 x boost)
    kick: float = 0.0                                   # enveloppe du « kick » du drop (onde de choc, aberration, glitch)
    since: float = 99.0                                 # secondes depuis le drop (rayon de l'onde de choc)
    force: tuple = (0.0, 0.0, 0.0, 0.0)                 # intensite minimale de chaque effet (meme coupe)
    flash: float = 0.0                                  # flash blanc 0..1
    zoom: float = 0.0                                   # > 0 : l'image grossit (coup de zoom)
    shake: tuple = (0.0, 0.0)                           # decalage de l'image (uv)
    slices: float = 0.0                                 # 0..1 : tranches decalees (fracture)
    invert: float = 0.0                                 # 0..1 : negatif
    hue: float = 0.0                                    # rotation de teinte ajoutee (radians)
    implode: float = 0.0                                # > 0 : aspiration vers le centre ; < 0 : expulsion
    seed: float = 0.0                                   # tirage des tranches (change a chaque drop)


NEUTRAL = DropValues()


def _smooth(e0: float, e1: float, x: float) -> float:
    t = min(max((x - e0) / (e1 - e0), 0.0), 1.0)
    return t * t * (3.0 - 2.0 * t)


class DropFx:
    def __init__(self, rng: random.Random | None = None) -> None:
        self.rng = rng or random.Random()
        self.style: str | None = None
        self.t0: float | None = None
        self.seed = 0.0
        self._bag: list[str] = []
        self.count = 0

    def _next_style(self) -> str:
        if not self._bag:
            bag = list(STYLES)
            self.rng.shuffle(bag)
            if self.style is not None and bag[0] == self.style:          # jamais deux fois le meme de suite, meme d'un sac a l'autre
                bag[0], bag[-1] = bag[-1], bag[0]
            self._bag = bag
        return self._bag.pop(0)

    def trigger(self, now: float, style: str | None = None) -> str:
        """Lance un drop a l'instant `now` (un drop pendant un autre repart de zero). `style` force un style (tests) ; rend le style joue."""
        self.style = style if style in STYLES else self._next_style()
        self.t0 = now
        self.seed = self.rng.random() * 100.0
        self.count += 1
        return self.style

    def values(self, now: float, amount: float = 1.0, foudre: bool = False) -> DropValues:
        """Ce que le rendu applique a l'instant `now`. `amount` dose tout (0 = rien) ; `foudre` : pas de flash blanc."""
        if self.t0 is None or amount <= 0.0:
            return NEUTRAL
        t = now - self.t0
        if t < 0.0 or t >= DURATION:
            return NEUTRAL
        a = float(amount)
        fade = 1.0 - _smooth(FADE_FROM * DURATION, DURATION, t)
        burst = (1.0 - t / BURST_S) ** 2 if t < BURST_S else 0.0
        kick = math.exp(-t / 0.35)
        force = [f * burst for f in BASE_FORCE]
        flash = zoom = slices = invert = hue = implode = 0.0
        shake = (0.0, 0.0)
        st = self.style
        if st == "onde":
            force[1] += 2.5 * burst
            force[2] += 1.2 * burst
            flash = 0.55 * math.exp(-t / 0.15)
        elif st == "coup":
            zoom = 0.16 * math.exp(-t / 0.22) * math.cos(t * 2.0 * math.pi * 3.0)
            amp = 0.012 * math.exp(-t / 0.25)
            shake = (amp * math.sin(t * 73.0 + self.seed), amp * math.cos(t * 61.0 + self.seed))
            flash = 0.4 * math.exp(-t / 0.1)
        elif st == "fracture":
            slices = math.exp(-t / 0.5)
            force[3] += 1.5 * burst
            force[2] += 1.5 * burst
            flash = 0.2 * math.exp(-t / 0.08)
        elif st == "negatif":
            invert = 1.0 if t < 0.1 else math.exp(-(t - 0.1) / 0.15)
            hue = math.pi * (1.0 - _smooth(0.0, 1.3, t))
        elif st == "implosion":
            if t < 0.3:
                implode = 0.5 * (t / 0.3) ** 2
            else:
                implode = 0.5 * math.exp(-(t - 0.3) / 0.12) * math.cos((t - 0.3) * 25.0)
                flash = 0.6 * math.exp(-(t - 0.3) / 0.12)
        if foudre:
            flash = 0.0
        k = a * fade
        return DropValues(boost=min(burst * k, 1.5), kick=kick * k, since=t, force=tuple(f * k for f in force), flash=min(flash * k, 1.0),
                          zoom=zoom * k, shake=(shake[0] * k, shake[1] * k), slices=min(slices * k, 1.0), invert=min(invert * k, 1.0),
                          hue=hue * k, implode=implode * k, seed=self.seed)
