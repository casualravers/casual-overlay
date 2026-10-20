"""Strobe automatique : decide TOUT SEUL quand eclairer, a partir de la phase du morceau et des detections (kicks, frappes aigues, grille du temps).

Python pur (stdlib seulement, temps injecte). Aucun reglage : l'operateur arme « Strobe auto » et le cerveau applique des regles de musicien.

  * silence, ambiance, cassure : jamais d'eclair (le noir fait partie du morceau) ;
  * groove : un petit accent de temps en temps (un eclair discret qui ne blanchit que les parties sombres de l'image) : le « 1 » de la mesure (au plus
    toutes les ~4 mesures) si la grille du temps est connue, sinon une frappe des aigus (clap, caisse claire) espacee de 3 s, sinon un kick sur 16 ;
  * montee : cadence qui accelere avec la montee (0,5 Hz au depart, 4 Hz apres 16 s, courbe en carre) ;
  * drop : le passage en « pic » apres une montee ou une cassure donne UN gros eclair blanc ;
  * pic : un eclair sur chaque kick (3 par seconde au plus), toute l'image.
Le limiteur de securite du strobe (cadence maximale, duree d'une rafale) reste applique par le rendu a tous les eclairs.
"""
from __future__ import annotations

SILENT_PHASES = ("silence", "ambient", "break")

PEAK_GAP_S = 0.30            # au plus ~3 eclairs par seconde en pic
ACCENT_GAP_S = 7.0           # groove : accent sur le « 1 » (grille connue), au plus toutes les ~4 mesures
HIT_ACCENT_GAP_S = 3.0       # groove : accent sur une frappe des aigus (sans grille)
KICKS_FALLBACK = 16          # groove sans grille ni frappes : un kick sur 16
BAR_BEATS = 4
BUILD_HZ = (0.5, 4.0)        # cadence de la montee detectee (la « Montee » manuelle va jusqu'a 16 Hz, plafonnee par le limiteur)
BUILD_RAMP_S = 16.0
DARK_GROOVE = 0.5            # luminosite sous laquelle l'eclair blanchit (1 = toute l'image) : discret en groove
DARK_BUILD = 0.8


class StrobeBrain:
    def __init__(self) -> None:
        self.reset()

    def reset(self) -> None:
        self.prev: str | None = None
        self.last = -1e9
        self.kicks = 0
        self.reason = ""                       # pourquoi le dernier eclair est parti (affiche dans l'interface)

    def update(self, now: float, phase: str | None, since: float | None, kicks: int, hits: int, beat_index: int | None = None) -> tuple[bool, float]:
        """Un pas (une image). `kicks` / `hits` : nouveaux kicks / frappes aigues depuis le pas precedent ; `since` : instant ou la phase a commence.
        Rend (eclair a partir maintenant, seuil de luminosite de cet eclair)."""
        prev, self.prev = self.prev, phase
        if phase is None or phase in SILENT_PHASES:
            self.kicks = 0
            return False, 1.0
        gap = now - self.last
        fire, dark = False, 1.0
        if phase == "peak":
            if prev in ("build", "break"):                    # le drop : un gros eclair
                fire, self.reason = True, "drop (passage en pic)"
            elif kicks and gap >= PEAK_GAP_S:
                fire, self.reason = True, "kick au pic"
        elif phase == "groove":
            dark = DARK_GROOVE
            self.kicks += kicks
            if beat_index is not None:
                fire = bool(kicks) and int(beat_index) % BAR_BEATS == 0 and gap >= ACCENT_GAP_S
                self.reason = "accent groove (le 1 de la mesure)"
            else:
                by_hit = bool(hits) and gap >= HIT_ACCENT_GAP_S
                fire = by_hit or self.kicks >= KICKS_FALLBACK
                self.reason = "accent groove (frappe des aigus)" if by_hit else "accent groove (1 kick sur 16)"
            if fire:
                self.kicks = 0
        elif phase == "build":
            dark = DARK_BUILD
            progress = 0.0 if since is None else min(max((now - since) / BUILD_RAMP_S, 0.0), 1.0)
            hz = BUILD_HZ[0] + (BUILD_HZ[1] - BUILD_HZ[0]) * progress * progress
            fire = gap >= 1.0 / hz
            self.reason = f"montee ({hz:.1f} eclairs/s)".replace(".", ",")
        if fire:
            self.last = now
        return fire, dark
