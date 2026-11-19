"""Strobe automatique : decide TOUT SEUL quand eclairer, a partir de la phase du morceau et des detections (kicks, grille du temps).

Python pur (stdlib seulement, temps injecte). Aucun reglage : l'operateur arme « Strobe auto » et le cerveau applique des regles de musicien.
Un strobe qui clignote tout le temps ne veut plus rien dire (issue #185 : en soiree, le limiteur coupait une rafale toutes les 11 s) : les
eclairs restent RARES, ils soulignent la structure du morceau.

  * silence, ambiance, cassure : jamais d'eclair (le noir fait partie du morceau) ;
  * groove : un petit accent (un eclair discret qui ne blanchit que les parties sombres de l'image) au debut d'une phrase de 32 temps si la grille
    du temps est connue (~15 s), sinon un kick sur 32 ; jamais moins de 15 s entre deux accents. Les frappes aigues ne declenchent plus rien ;
  * montee : rien pendant les 4 premieres secondes (une fausse montee qui retombe ne clignote pas), puis une cadence qui accelere de 0,5 a
    4 eclairs par seconde (`auto_build_hz`, la meme pour la montee automatique de l'interface) ;
  * drop : le passage en « pic » apres une montee ou une cassure donne UN gros eclair blanc ;
  * pic : un eclair sur le 1 d'une mesure sur deux (grille connue), sinon un kick sur 8 ; jamais moins de 3 s entre deux eclairs.
Le limiteur de securite du strobe (cadence maximale, duree d'une rafale) reste applique par le rendu a tous les eclairs.
"""
from __future__ import annotations

SILENT_PHASES = ("silence", "ambient", "break")

PEAK_GAP_S = 3.0             # pic : au plus un eclair toutes les 3 s (avant : un par kick, 3 par seconde)
PEAK_BEATS = 8               # pic : le 1 d'une mesure sur deux (grille connue), sinon un kick sur 8
ACCENT_GAP_S = 15.0          # groove : au plus un accent toutes les 15 s
PHRASE_BEATS = 32            # groove : debut d'une phrase de 32 temps (grille connue), sinon un kick sur 32
BUILD_CONFIRM_S = 4.0        # montee : aucun eclair avant 4 s de montee (les fausses montees ne clignotent pas)
BUILD_HZ = (0.5, 4.0)        # cadence de la montee detectee (la « Montee » lancee a la main va jusqu'a 16 Hz, plafonnee par le limiteur)
BUILD_RAMP_S = 16.0
DARK_GROOVE = 0.5            # luminosite sous laquelle l'eclair blanchit (1 = toute l'image) : discret en groove
DARK_BUILD = 0.8
KINDS = ("pic", "groove", "montee", "drop")


def auto_build_hz(elapsed_s: float) -> float | None:
    """Cadence des eclairs d'une montee AUTOMATIQUE apres `elapsed_s` secondes : None pendant les `BUILD_CONFIRM_S` premieres secondes, puis de
    0,5 a 4 eclairs par seconde sur `BUILD_RAMP_S` (courbe en carre : lente au debut)."""
    t = float(elapsed_s) - BUILD_CONFIRM_S
    if t < 0.0:
        return None
    progress = min(t / BUILD_RAMP_S, 1.0)
    return BUILD_HZ[0] + (BUILD_HZ[1] - BUILD_HZ[0]) * progress * progress


def minute_text(counts: dict, builds: int) -> str:
    """Ligne de journal d'une minute de strobe auto ; vide si aucun eclair n'est parti et aucune montee n'a commence."""
    total = sum(int(counts.get(k, 0)) for k in KINDS)
    if total == 0 and not builds:
        return ""
    detail = ", ".join(f"{k} {int(counts.get(k, 0))}" for k in KINDS)
    return f"Strobe auto : {total} eclairs en 1 min ({detail}) ; {int(builds)} montee(s) commencee(s)"


class StrobeBrain:
    def __init__(self) -> None:
        self.reset()

    def reset(self) -> None:
        self.prev: str | None = None
        self.last = -1e9
        self.kicks = 0
        self.reason = ""                       # pourquoi le dernier eclair est parti (affiche dans l'interface)
        self.kind = ""                         # famille du dernier eclair (KINDS)
        self.counts: dict[str, int] = {}       # eclairs decides par famille (journal par minute ; le rendu le vide)

    def update(self, now: float, phase: str | None, since: float | None, kicks: int, hits: int, beat_index: int | None = None,
               busy: bool = False) -> tuple[bool, float]:
        """Un pas (une image). `kicks` / `hits` : nouveaux kicks / frappes aigues depuis le pas precedent (les frappes ne servent plus, gardees
        pour la signature) ; `since` : instant ou la phase a commence. Rend (eclair a partir maintenant, seuil de luminosite de cet eclair).
        `busy` : une montee est en cours (automatique ou manuelle), c'est elle qui eclaire : le cerveau se tait pour ne pas doubler les eclairs."""
        prev, self.prev = self.prev, phase
        if busy:
            self.kicks = 0
            return False, 1.0
        if phase is None or phase in SILENT_PHASES:
            self.kicks = 0
            return False, 1.0
        if phase != prev:
            self.kicks = 0
        gap = now - self.last
        fire, dark, kind = False, 1.0, ""
        if phase == "peak":
            self.kicks += kicks
            if prev in ("build", "break"):                    # le drop : un gros eclair
                fire, kind, self.reason = True, "drop", "drop (passage en pic)"
            elif kicks and gap >= PEAK_GAP_S:
                if beat_index is not None:
                    fire = int(beat_index) % PEAK_BEATS == 0
                    self.reason = "pic (le 1, une mesure sur deux)"
                else:
                    fire = self.kicks >= PEAK_BEATS
                    self.reason = "pic (un kick sur 8)"
                kind = "pic"
        elif phase == "groove":
            dark = DARK_GROOVE
            self.kicks += kicks
            if kicks and gap >= ACCENT_GAP_S:
                if beat_index is not None:
                    fire = int(beat_index) % PHRASE_BEATS == 0
                    self.reason = "accent groove (debut de phrase de 32 temps)"
                else:
                    fire = self.kicks >= PHRASE_BEATS
                    self.reason = "accent groove (un kick sur 32)"
                kind = "groove"
        elif phase == "build":
            dark = DARK_BUILD
            hz = auto_build_hz(0.0 if since is None else now - since)
            if hz is not None:
                fire = gap >= 1.0 / hz
                kind = "montee"
                self.reason = f"montee ({hz:.1f} eclairs/s)".replace(".", ",")
            else:
                self.reason = "montee (attente de confirmation)"
        if fire:
            self.last = now
            self.kicks = 0
            self.kind = kind
            self.counts[kind] = self.counts.get(kind, 0) + 1
        return fire, dark
