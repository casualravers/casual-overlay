"""Reprise des medias la ou ils se sont arretes (Mode VJ) : pur, horloge injectable.

Un media (video du logo, video de fond, videos de Snap) joue en boucle tant qu'il est a l'ecran ; quand la scene change, son decodeur
s'arrete. `Playback` retient ou il en etait (par chemin) pour le relancer a cet endroit au retour de la scene, au lieu du debut.
La position est ramenee dans la duree du fichier (il boucle) ; un media oublie depuis `EXPIRE_S` repart du debut.
"""
import time

EXPIRE_S = 3600.0        # au-dela, le media repart du debut (la scene n'a plus rien de "en cours")
MIN_RESUME_S = 0.5       # sous ce seuil, repartir du debut (inutile de chercher)
MAX_ENTRIES = 64


def fold(position: float, duration: float | None) -> float:
    """Position ramenee dans le fichier qui boucle. Duree inconnue : position brute (un fichier trop court la depasse = fin)."""
    if position < 0:
        return 0.0
    if duration and duration > 0:
        return position % duration
    return position


class Playback:
    def __init__(self, clock=time.monotonic):
        self.clock = clock
        self.saved: dict[str, tuple[float, float]] = {}      # chemin -> (position, instant de l'arret)
        self.running: dict[str, tuple[float, float]] = {}    # chemin -> (position au lancement, instant du lancement)
        self.durations: dict[str, float] = {}

    def duration(self, path: str, duration: float | None = None) -> float | None:
        """Memorise (si donnee) puis rend la duree connue du fichier."""
        if duration and duration > 0:
            self.durations[path] = float(duration)
        return self.durations.get(path)

    def start(self, path: str, duration: float | None = None) -> float:
        """Un decodeur de `path` demarre : rend la position (s) ou le lancer (0 = debut)."""
        now = self.clock()
        dur = self.duration(path, duration)
        pos = 0.0
        got = self.saved.pop(path, None)
        if got is not None and now - got[1] <= EXPIRE_S:
            pos = fold(got[0], dur)
            if pos < MIN_RESUME_S:
                pos = 0.0
            elif dur and dur - pos < MIN_RESUME_S:
                pos = 0.0
        self.running[path] = (pos, now)
        return pos

    def stop(self, path: str) -> float | None:
        """Le decodeur de `path` s'arrete : memorise la position atteinte (None si on ne l'avait pas lance)."""
        got = self.running.pop(path, None)
        if got is None:
            return None
        now = self.clock()
        pos = fold(got[0] + (now - got[1]), self.durations.get(path))
        self.saved[path] = (pos, now)
        while len(self.saved) > MAX_ENTRIES:
            self.saved.pop(next(iter(self.saved)))
        return pos

    def position(self, path: str) -> float | None:
        """Position actuelle si le media joue, sinon celle memorisee, sinon None."""
        got = self.running.get(path)
        if got is not None:
            return fold(got[0] + (self.clock() - got[1]), self.durations.get(path))
        got = self.saved.get(path)
        return got[0] if got else None


PLAYBACK = Playback()
