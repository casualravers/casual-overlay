"""Commandes de salle du Mode VJ : tempo effectif, strobe (appui, limiteur), panique, tap tempo.

Fonctions et classes pures (aucune fenetre, aucun GL, stdlib seulement) : le temps est injecte, tout se teste sans materiel.
"""
from __future__ import annotations

from pathlib import Path

def _unit(value, lo: float = 0.0, hi: float = 1.0) -> float:
    return min(max(float(value), lo), hi)


def effective_tempo(p: dict) -> float:
    """Tempo reel : le master, multiplie par celui de la phase du morceau (`phase_tempo`, tres lent en ambiance ; 1 sans « phases auto »)."""
    return _unit(p.get("tempo", 1.0), 0.0, 2.0) * _unit(p.get("phase_tempo", 1.0), 0.0, 2.0)


def strobe_press(params: dict) -> None:
    """Appui sur le strobe (bouton ou touche S) : manuel = eclairs tant que c'est maintenu ; kick / cadence = arme ou desarme."""
    params["strobe_hits"] = int(params.get("strobe_hits", 0)) + 1
    if params.get("strobe_mode", "manual") == "manual":
        params["strobe_on"] = 1.0
    else:
        params["strobe_on"] = 0.0 if float(params.get("strobe_on", 0.0)) >= 0.5 else 1.0


def strobe_release(params: dict) -> None:
    if params.get("strobe_mode", "manual") == "manual":
        params["strobe_on"] = 0.0


STROBE_COOLDOWN_S = 3.0               # pause forcee apres une rafale trop longue


class StrobeLimiter:
    """Limiteur de strobe (securite du public : photosensibilite). Deux regles, appliquees a TOUS les eclairs du strobe (appui, kick,
    cadence, montee) : (1) jamais plus de `max_hz` eclairs par seconde ; (2) une rafale continue (eclairs espaces de moins de 1 s)
    ne dure pas plus de `max_s` secondes : ensuite pause forcee de `STROBE_COOLDOWN_S`. Pure (le temps est injecte), testable."""

    GAP_S = 1.0

    def __init__(self) -> None:
        self.last = -1e9
        self.burst_start: float | None = None
        self.blocked_until = -1e9
        self.trips = 0                                   # nombre de fois ou la duree maximale a coupe le strobe

    def blocked(self, now: float) -> bool:
        return now < self.blocked_until

    def allow(self, now: float, max_hz: float, max_s: float, timed: bool = True) -> bool:
        """`timed` faux (eclairs de la montee) : seule la cadence maximale s'applique, la rafale n'est jamais coupee par sa duree."""
        max_hz = _unit(max_hz, 1.0, 30.0)
        max_s = _unit(max_s, 1.0, 120.0)
        if now < self.blocked_until:
            return False
        if now - self.last > self.GAP_S:
            self.burst_start = None                       # une pause d'une seconde remet la rafale a zero
        if now - self.last < 1.0 / max_hz - 1e-9:
            return False
        if not timed:
            pass
        elif self.burst_start is None:
            self.burst_start = now
        elif now - self.burst_start > max_s:
            self.blocked_until = now + STROBE_COOLDOWN_S
            self.burst_start = None
            self.trips += 1
            return False
        self.last = now
        return True


# Panique (bouton / touche X) : coupe d'un geste tout ce qui peut partir en vrille et rend une image propre (fond + logo tels que la
# scene les a poses). Les reglages du logo, du fond et des medias ne sont pas touches.
PANIC_RESET = {
    "build": 0.0, "strobe_on": 0.0, "auto_phase": 0.0,
    "comp_sym": 0, "comp_rot": 0.0, "comp_pix": 0.0, "comp_trail": 0.0, "comp_warp": 0.0,
    "hue_master": 0.0, "hue_cycle": 0.0, "hue_spectrum": 0.0, "breath_overlay": 0.0, "breath_bg": 0.0,
    "bg_master": 1.0, "overlay_master": 1.0, "react_master": 1.0, "tempo": 1.0, "fx_master": 1.0,
    "holo_on": 0.0, "melt_on": 0.0, "cell_on": 0.0, "bpm_auto": 0.0, "drop_auto": 0.0,
}


SPECTRUM_HUE_COLD = 200.0           # rotation de teinte quand tout est dans les aigus (0 = tout dans les graves)


def spectral_hue(bass: float, high: float) -> float:
    """Rotation de teinte (degres) pour un contenu spectral : 0 quand les graves dominent (couleurs d'origine, chaudes), jusqu'a
    `SPECTRUM_HUE_COLD` quand les aigus dominent (froides). Un silence (les deux ~0) donne 0 : la teinte ne saute pas sur le bruit."""
    bass, high = max(float(bass), 0.0), max(float(high), 0.0)
    return SPECTRUM_HUE_COLD * high / (high + bass + 0.08)


def panic(params: dict) -> list[str]:
    """Remet a zero ce qui deforme, masque ou fait clignoter l'image. Rend la liste des reglages qui ont change (pour le journal)."""
    changed: list[str] = []
    for key, value in PANIC_RESET.items():
        if params.get(key) != value:
            changed.append(key)
        params[key] = value
    for key, size in (("fx_on", 5), ("fxl_on", 4), ("noise_fx", 5)):
        if any(params.get(key, [])):
            changed.append(key)
        params[key] = [0] * size
    bg = str(params.get("bg_video") or "")
    if params.get("bg_mode") == "video" and not (bg and Path(bg).is_file()):         # fond video sans fichier = ecran noir
        params["bg_mode"] = "live"
        changed.append("bg_mode")
    params["strobe_hits"] = int(params.get("strobe_hits", 0))                       # pas d'eclair en attente
    return changed


BUILD_STROBE_HZ = (0.5, 16.0)         # cadence des eclairs de la montee : au depart un eclair toutes les 2 s, a 100 % seize par seconde


def build_strobe_hz(build: float) -> float:
    """Cadence (Hz) des eclairs automatiques de la montee. Courbe en carre : lente au debut (pas d'effet epileptique des les premieres
    secondes), qui ne s'emballe que vers la fin. build = 0..1."""
    b = _unit(build)
    lo, hi = BUILD_STROBE_HZ
    return lo + (hi - lo) * b * b


def tap_bpm(taps: list[float], max_gap: float = 2.0, keep: int = 6) -> float | None:
    """BPM d'une suite d'appuis (instants en secondes) : moyenne des derniers intervalles ; un trou de plus de `max_gap`
    secondes recommence la serie. None tant qu'il y a moins de deux appuis valides. Pure."""
    series: list[float] = []
    for t in taps:
        if series and t - series[-1] > max_gap:
            series = []
        series.append(t)
    series = series[-(keep + 1):]
    if len(series) < 2:
        return None
    mean = (series[-1] - series[0]) / (len(series) - 1)
    return 60.0 / mean if mean > 0 else None


BEATS_PER_BAR = 4                      # une mesure = 4 kicks detectes (4/4 ; ce n'est pas un BPM detecte, on compte les kicks)
BAR_CHOICES = (1, 2, 4, 8, 16, 32)     # durees proposees au VJ « en rythme »


class BarTimer:
    """Minuteur du VJ en rythme : « dans N mesures », en comptant les kicks detectes (`beats`, compteur croissant de l'analyse).

    Garde-fou : si les kicks ne viennent pas (silence, kick manque), le changement a lieu quand meme apres le DOUBLE de la duree attendue
    au tempo du moment (120 BPM x master du tempo), au moins 8 s : le VJ ne reste jamais fige. Pure : kicks et horloge sont passes en arguments."""

    def __init__(self, bars: int = 8, beats_per_bar: int = BEATS_PER_BAR) -> None:
        self.per_bar = max(int(beats_per_bar), 1)
        self.total = max(int(bars), 1) * self.per_bar
        self.start = 0
        self.deadline = 0.0

    def arm(self, beats: int, now: float, tempo: float = 1.0, bpm: float | None = None) -> "BarTimer":
        """Demarre le compte a l'instant `now` ; `tempo` = master du tempo (1 = 120 BPM). `bpm` : tempo detecte (il l'emporte sur le master)."""
        bpm = min(max(float(bpm) if bpm else 120.0 * float(tempo), 30.0), 300.0)
        self.start = int(beats)
        self.deadline = now + max(2.0 * self.total * 60.0 / bpm, 8.0)
        return self

    def done(self, beats: int) -> int:
        return min(max(int(beats) - self.start, 0), self.total)

    def due(self, beats: int, now: float) -> bool:
        return int(beats) - self.start >= self.total or now >= self.deadline

    def label(self, beats: int) -> str:
        """« 3/8 » : la mesure en cours sur le total."""
        bars = max(self.total // self.per_bar, 1)
        return f"{min(self.done(beats) // self.per_bar + 1, bars)}/{bars}"
