"""Routage audio par effet (#198) : chaque chose qui reagit au son peut avoir SA source audio et son dosage.

Python pur (stdlib seulement). Spec : docs/plans/2026-11-25-routage-audio-198.md ; fiche : docs/fonctions/audio.md.

Une LIGNE = un effet ou une reaction (ondulation du fond, pulsation du logo, teinte spectrale...). Deux reglages par ligne routable
(table unique, portee `look`) : `rt_<id>` (source : `herite` = comme la couche, ou une valeur de `audio_layers.SOURCES`) et `rg_<id>`
(dosage 0..2). Par defaut (`herite`, 1.0) la ligne lit le vecteur de sa couche tel qu'avant : ZERO perte, image identique.
Les lignes `info` (strobe, foudre, noise) decident deja seules : lecture seule, aucun reglage.
"""
from __future__ import annotations

from collections import namedtuple

from core import audio_layers as al

INHERIT = "herite"
ROUTE_SOURCES = (INHERIT, *al.SOURCES)
ZONES = ("fond", "logo", "global")
DOSE_MIN, DOSE_MAX = 0.0, 2.0

Route = namedtuple("Route", "id zone layer label kind")       # kind : fx (vecteur shader), vec (vecteur de couche), info (lecture seule)

ROUTES: tuple[Route, ...] = (
    Route("bg_wobble", "fond", "bg", "Ondulation", "fx"),
    Route("bg_ripple", "fond", "bg", "Onde de choc", "fx"),
    Route("bg_glitch", "fond", "bg", "Glitch", "fx"),
    Route("bg_chroma", "fond", "bg", "Aberration", "fx"),
    Route("bg_flash", "fond", "bg", "Flash du motif", "vec"),
    Route("bg_scroll", "fond", "bg", "Defilement et damier", "vec"),
    Route("bg_sparkle", "fond", "bg", "Scintillement du motif", "vec"),
    Route("lg_wobble", "logo", "logo", "Ondulation", "fx"),
    Route("lg_ripple", "logo", "logo", "Onde de choc", "fx"),
    Route("lg_glitch", "logo", "logo", "Glitch", "fx"),
    Route("lg_chroma", "logo", "logo", "Aberration", "fx"),
    Route("lg_pulse", "logo", "logo", "Pulsation", "vec"),
    Route("lg_jitter", "logo", "logo", "Tremblement", "vec"),
    Route("lg_glow", "logo", "logo", "Contour lumineux", "vec"),
    Route("lg_holo", "logo", "logo", "Halo holographique", "vec"),
    Route("lg_melt", "logo", "logo", "Fonte acide", "vec"),
    Route("lg_cells", "logo", "logo", "Cellules", "vec"),
    Route("breath", "global", "bg", "Respiration (pulsation)", "vec"),
    Route("hue", "global", "bg", "Teinte spectrale", "vec"),
    Route("strobe", "global", None, "Strobe", "info"),
    Route("foudre", "global", None, "Foudre", "info"),
    Route("noise", "global", None, "Noise", "info"),
)
BY_ID = {r.id: r for r in ROUTES}

# Les 8 effets du shader : id -> (couche, indice dans audio_layers.EFFECTS = indice du vecteur u_au_*[i])
FX_LINES = {f"{pre}_{e}": (layer, i) for pre, layer in (("bg", "bg"), ("lg", "logo")) for i, e in enumerate(al.EFFECTS)}

# Familles du menu de source (une ligne de separation entre deux familles)
SOURCE_FAMILIES = (
    ("Reglage", (INHERIT, "auto", "rien")),
    ("Niveaux", ("tout", "basses", "mediums", "aigus")),
    ("Bandes", tuple(al.BAND_SOURCES)),
    ("Rythme", ("kick", "frappes", "snare", "elan")),
    ("Structure", (*al.PULSE_GRIDS, *al.END_GRIDS)),
)


def source_key(rid: str) -> str:
    return "rt_" + rid


def dose_key(rid: str) -> str:
    return "rg_" + rid


def routable() -> tuple[Route, ...]:
    """Les lignes qui ont des reglages (toutes sauf `info`)."""
    return tuple(r for r in ROUTES if r.kind != "info")


def route_specs() -> list[tuple]:
    """(cle, defaut, lo, hi, choices, doc) des reglages de routage, pour la table unique (core/params_schema.py)."""
    out = []
    for r in routable():
        where = {"fond": "du fond", "logo": "du logo", "global": ""}[r.zone]
        out.append((source_key(r.id), INHERIT, None, None, ROUTE_SOURCES,
                    f"Routage : source audio de « {r.label} » {where} (herite = comme la couche)."))
        out.append((dose_key(r.id), 1.0, DOSE_MIN, DOSE_MAX, None, f"Routage : dosage de « {r.label} » {where} (1 = tel quel)."))
    return out


def route_of(params: dict, rid: str) -> tuple[str, float]:
    """(source, dosage) d'une ligne : source inconnue -> herite, dosage borne 0..2 (invalide -> 1)."""
    src = params.get(source_key(rid), INHERIT)
    if src not in ROUTE_SOURCES:
        src = INHERIT
    dose = params.get(dose_key(rid), 1.0)
    if isinstance(dose, bool) or not isinstance(dose, (int, float)):
        dose = 1.0
    return src, min(max(float(dose), DOSE_MIN), DOSE_MAX)


def is_default(params: dict, rid: str | None = None) -> bool:
    """Vrai si la ligne `rid` (ou toutes les lignes si None) est par defaut : herite, dosage 1."""
    if rid is not None:
        return route_of(params, rid) == (INHERIT, 1.0)
    return all(is_default(params, r.id) for r in routable())


def routed_ids(params: dict) -> list[str]:
    """Les lignes modifiees, dans l'ordre de la table."""
    return [r.id for r in routable() if not is_default(params, r.id)]


def fx_route_id(rid: str, linked: bool) -> str:
    """La ligne dont un effet shader prend les reglages : lies (fx_link = 1), les effets du logo suivent ceux du fond (comme la source,
    la sensibilite, le retard et le lissage de la couche)."""
    return "bg_" + rid[3:] if linked and rid.startswith("lg_") and rid in FX_LINES else rid


def _scaled(vec: tuple, dose: float) -> tuple:
    if dose <= 0.0:
        return al.SILENT
    return (vec[0] * dose, vec[1] * dose, vec[2] * dose, vec[3])


class RouteBank:
    """Vecteur audio (bass, high, beat, since) de chaque ligne, image par image.

    Ligne par defaut : le vecteur de sa couche tel quel (meme objet : aucun calcul, image identique). `herite` + dosage : le vecteur de la couche
    (deja retarde / lisse) x dosage. Source explicite : un `SourceMixer` et un `LayerFilter` propres a la ligne (memes retard et lissage que la
    couche), puis le dosage ; `auto` prend `auto_source` (la source du tableau des phases pour la couche) avec un fondu quand elle change."""

    def __init__(self) -> None:
        self._mix: dict[str, al.SourceMixer] = {}
        self._filt: dict[str, al.LayerFilter] = {}
        self._info: dict[str, tuple[str, float]] = {}

    def vector(self, now: float, state: dict, params: dict, rid: str, base: tuple, auto_source: str,
               lag: float, smooth: float, kick_layer: str | None = None, like: str | None = None) -> tuple:
        """`like` : lire la source et le dosage d'une autre ligne (logo lie : ses effets suivent la ligne du fond) ; l'etat reste celui de `rid`."""
        src, dose = route_of(params, like or rid)
        if src == INHERIT:
            self._mix.pop(rid, None)
            self._filt.pop(rid, None)
            if dose == 1.0:
                self._info.pop(rid, None)
                return base
            out = _scaled(base, dose)
            self._info[rid] = (INHERIT, max(out[0], out[1], out[2]))
            return out
        resolved = auto_source if src == "auto" else src
        layer = kick_layer or BY_ID[rid].layer or "bg"
        mixer = self._mix.setdefault(rid, al.SourceMixer())
        filt = self._filt.setdefault(rid, al.LayerFilter())
        raw = mixer.levels(now, state, resolved, layer, src == "auto")
        out = _scaled(filt.step(now, raw, lag, smooth), dose)
        self._info[rid] = (resolved, max(out[0], out[1], out[2]))
        return out

    def info(self, rid: str) -> tuple[str, float] | None:
        """(source resolue, niveau courant) d'une ligne modifiee, None pour une ligne par defaut (voyant de l'onglet Routage)."""
        return self._info.get(rid)
