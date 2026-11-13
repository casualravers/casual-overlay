"""Avance estimee automatiquement (remplace la calibration au telephone).

L'« Avance » fait partir les effets « sur kick » AVANT le kick detecte, pour que la lumiere tombe avec le son. Sa bonne valeur est le retard de toute
la chaine entre le son et la lumiere. On l'estime en direct, sans camera, comme la somme de :

* **son** : retard de capture mesure (horodatage du pilote, `AudioAnalyzer.audio_age_floor_ms`) ; sans horodatage, la latence annoncee par la carte son ;
* **detection** : retard median de datation du kick mesure sur le banc (`tools/bench_kick.py`, attaque rapide du lot 3), fixe ;
* **image** : une periode d'image MESUREE (attente de la prochaine image) + le temps GPU mesure, plafonne a une periode (le premier temps mesure,
  compilation des shaders comprise, valait plus d'une demi-seconde) ;
* **affichage** : traitement de l'ecran / du videoprojecteur, impossible a mesurer sans camera : une HYPOTHESE fixe (dite « supposee » a l'ecran).

Juste a quelques dizaines de ms pres (l'affichage depend du projecteur) : le mode manuel reste disponible. Python pur, temps injecte.
"""
from __future__ import annotations

DETECT_MS = 15.0           # retard median de datation du kick (banc, attaque rapide) : mesure, pas une hypothese
DISPLAY_MS = 25.0          # traitement typique d'un videoprojecteur (un moniteur : moins) : HYPOTHESE
MAX_MS = 200.0             # meme borne que le curseur manuel
DEFAULT_REFRESH_HZ = 60.0
ROUND_MS = 5.0             # valeur affichee et appliquee arrondie : elle ne tremble pas
SMOOTH_TAU_S = 2.0         # lissage des mesures
UPDATE_S = 1.0             # la valeur publiee change au plus une fois par seconde
HYSTERESIS_MS = 4.0        # ... et seulement si la mesure lissee s'en ecarte de plus de 4 ms


def estimate(capture_ms: float, refresh_hz: float, gpu_ms: float, fallback_capture_ms: float = 0.0) -> dict:
    """Detail de l'estimation (ms) : `son`, `detection`, `image`, `affichage`, `total` (borne 0..MAX_MS)."""
    son = float(capture_ms) if capture_ms and capture_ms > 0 else max(0.0, float(fallback_capture_ms or 0.0))
    hz = float(refresh_hz) if refresh_hz and refresh_hz > 0 else DEFAULT_REFRESH_HZ
    period = 1000.0 / hz
    image = period + min(period, max(0.0, float(gpu_ms or 0.0)))
    total = min(MAX_MS, max(0.0, son + DETECT_MS + image + DISPLAY_MS))
    return {"son": son, "detection": DETECT_MS, "image": image, "affichage": DISPLAY_MS, "total": total}


class LeadEstimator:
    """Estimation lissee : `value` (ms, multiple de ROUND_MS, None avant la premiere mesure) et `parts` (detail lisse, pour le texte)."""

    def __init__(self) -> None:
        self.value: float | None = None
        self.parts: dict | None = None
        self._t_last: float | None = None
        self._t_pub: float | None = None

    def update(self, now: float, capture_ms: float, refresh_hz: float, gpu_ms: float, fallback_capture_ms: float = 0.0) -> float | None:
        raw = estimate(capture_ms, refresh_hz, gpu_ms, fallback_capture_ms)
        if self.parts is None:
            self.parts = dict(raw)
        else:
            dt = max(0.0, now - (self._t_last if self._t_last is not None else now))
            k = 1.0 if SMOOTH_TAU_S <= 0 else min(1.0, dt / SMOOTH_TAU_S)
            for key, val in raw.items():
                self.parts[key] += (val - self.parts[key]) * k
        self._t_last = now
        smooth = self.parts["total"]
        if self.value is None:
            self.value, self._t_pub = _round(smooth), now
        elif now - self._t_pub >= UPDATE_S:
            self._t_pub = now
            if abs(smooth - self.value) > HYSTERESIS_MS:
                self.value = _round(smooth)
        return self.value


def _round(ms: float) -> float:
    return min(MAX_MS, max(0.0, round(ms / ROUND_MS) * ROUND_MS))


def effective_lead_ms(params: dict) -> float:
    """Avance appliquee : l'estimation en mode auto (`sync_lead_auto`, 0 tant qu'aucune estimation n'existe), le curseur en mode manuel."""
    if float(params.get("sync_lead_auto", 1.0)) >= 0.5:
        return float(params.get("sync_lead_est", 0.0) or 0.0)
    return float(params.get("sync_lead_ms", 0.0))
