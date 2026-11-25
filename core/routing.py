"""Routage audio par effet (#198) : chaque chose qui reagit au son peut avoir SA source audio et son dosage.

Python pur (stdlib seulement). Spec : docs/plans/2026-11-25-routage-audio-198.md ; fiche : docs/fonctions/audio.md.

Une LIGNE = un effet ou une reaction (ondulation du fond, pulsation du logo, teinte spectrale...). Deux reglages par ligne routable
(table unique, portee `look`) : `rt_<id>` (source : `herite` = comme la couche, ou une valeur de `audio_layers.SOURCES`) et `rg_<id>`
(dosage 0..2). Par defaut (`herite`, 1.0) la ligne lit le vecteur de sa couche tel qu'avant : ZERO perte, image identique.
Les lignes `info` (strobe, foudre, noise) decident deja seules : lecture seule, aucun reglage.
"""
from __future__ import annotations

import math
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
    if isinstance(dose, bool) or not isinstance(dose, (int, float)) or not math.isfinite(dose):
        dose = 1.0                      # NaN (fichier edite a la main) : jamais d'uniform NaN
    return src, min(max(float(dose), DOSE_MIN), DOSE_MAX)


def is_default(params: dict, rid: str | None = None) -> bool:
    """Vrai si la ligne `rid` (ou toutes les lignes si None) est par defaut : herite, dosage 1."""
    if rid is not None:
        return route_of(params, rid) == (INHERIT, 1.0)
    return all(is_default(params, r.id) for r in routable())


def routed_ids(params: dict) -> list[str]:
    """Les lignes modifiees, dans l'ordre de la table."""
    return [r.id for r in routable() if not is_default(params, r.id)]


# --- Aides de l'interface (onglet Routage, pastilles) ------------------------------------------------------------------------------------------
SHORT_LABELS = {INHERIT: "Comme la couche", "tout": "Tout", "basses": "Basses", "mediums": "Mediums", "aigus": "Aigus", "kick": "Kick",
                "frappes": "Frappes", "snare": "Caisse claire", "rien": "Rien", "auto": "Auto", "sub": "Sub", "corps": "Corps",
                "presence": "Presence", "air": "Air", "elan": "Elan", "pouls": "Pouls", "croches": "Croches", "doubles": "Doubles",
                "mesure": "Mesure", "phrase8": "Phrase 8", "phrase16": "Phrase 16", "phrase32": "Phrase 32", "fin8": "Fin 8", "fin16": "Fin 16",
                "fin32": "Fin 32"}
LAYER_NAMES = {"bg": "le fond", "logo": "le logo"}
# Plages (Hz) que le mini-spectre surligne ; un evenement (kick, frappes, grilles...) n'a pas de plage. Bandes de engine/audio.py (BANDS, BANDS8).
RANGES_HZ = {"tout": (20.0, 12000.0), "basses": (20.0, 150.0), "mediums": (150.0, 2000.0), "aigus": (2000.0, 10000.0),
             "sub": (20.0, 60.0), "corps": (150.0, 1000.0), "presence": (1000.0, 5000.0), "air": (5000.0, 12000.0)}
SPECTRUM_HZ = (20.0, 20000.0)


def source_label(src: str) -> str:
    """Libelle court (pastille, tableau) ; source inconnue = comme la couche."""
    return SHORT_LABELS.get(src, SHORT_LABELS[INHERIT])


def menu_label(src: str) -> str:
    """Libelle long (entree du menu) : celui des menus de couche, sinon le court."""
    return al.LABELS.get(src, source_label(src))


def pill_text(src: str, dose: float, layer: str | None) -> str:
    """« Basses 80 % », « Comme le fond » ; le dosage n'est ecrit que s'il n'est pas de 100 %."""
    name = f"Comme {LAYER_NAMES[layer]}" if src == INHERIT and layer in LAYER_NAMES else source_label(src)
    pct = int(round(float(dose) * 100))
    return name if pct == 100 else f"{name} {pct} %"


PILL_CHARS = max(len(pill_text(s, 1.55, layer)) for s in ROUTE_SOURCES for layer in (*LAYER_NAMES, None))     # largeur fixe (en caracteres)


def auto_hint(phase, layer: str, since_hit=None) -> str:
    """Ce qu'Auto donne pour une couche : « Auto : groove -> Basses »."""
    bg, logo = al.auto_sources(phase, since_hit)
    return f"Auto : {phase if phase in al.AUTO_TABLE else 'phase inconnue'} -> {source_label(bg if layer == 'bg' else logo)}"


def source_range_hz(src: str) -> tuple[float, float] | None:
    return RANGES_HZ.get(src)


def _on(params: dict, key: str, i: int) -> bool:
    try:
        return float(params[key][i]) > 0.0
    except (KeyError, IndexError, TypeError, ValueError):
        return False


def _pos(params: dict, *keys: str) -> bool:
    try:
        return all(float(params.get(k, 0.0)) > 0.0 for k in keys)
    except (TypeError, ValueError):
        return False


FX_ON_INDEX = {"wobble": 0, "ripple": 1, "chroma": 2, "glitch": 3}      # ordre des interrupteurs fx_on / fxl_on (different de EFFECTS)
VEC_GATES = {"bg_flash": ("bg_react",), "bg_scroll": ("bg_react",), "bg_sparkle": ("bg_react",), "lg_jitter": ("logo_jitter",),
             "lg_glow": ("logo_glow",), "lg_holo": ("holo_on", "holo_react"), "lg_melt": ("melt_on", "melt_react"),
             "lg_cells": ("cell_on", "cell_react"), "breath": ("breath_pulse",), "hue": ("hue_spectrum",)}


def line_enabled(params: dict, rid: str) -> bool:
    """La ligne agit-elle en ce moment ? (grisee sinon) : effet coupe, reaction audio a 0, effet du logo qui suit le fond (lies)."""
    r = BY_ID[rid]
    if r.kind == "info":
        return True
    linked = float(params.get("fx_link", 1.0)) >= 0.5
    if rid in FX_LINES:
        effect = al.EFFECTS[FX_LINES[rid][1]]
        if r.layer == "bg":
            return _on(params, "fx_on", FX_ON_INDEX[effect])
        return not linked and _on(params, "fxl_on", FX_ON_INDEX[effect])
    if rid == "lg_pulse":
        return _on(params, "fx_on", 4) and _pos(params, "logo_pulse")
    if rid in ("lg_jitter", "lg_glow"):
        return _on(params, "fx_on", 4) and _pos(params, *VEC_GATES[rid])
    return _pos(params, *VEC_GATES[rid])


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

    def prune(self, active) -> None:
        """Oublie la memoire (fondu, retard, lissage, voyant) des lignes qui ne sont plus calculees : appele a chaque image avec les lignes
        calculees. Sans cela, une ligne remise par defaut puis reroutee rejouerait son vieil historique (onde de choc fantome)."""
        for store in (self._mix, self._filt, self._info):
            for rid in [k for k in store if k not in active]:
                del store[rid]

    def info(self, rid: str) -> tuple[str, float] | None:
        """(source resolue, niveau courant) d'une ligne modifiee, None pour une ligne par defaut (voyant de l'onglet Routage)."""
        return self._info.get(rid)
