"""Textes des voyants des fonctions automatiques : l'operateur doit voir ce que le systeme decide (et pourquoi) pour le comprendre et le deboguer.

Python pur (stdlib seulement). Chaque fonction intelligente a sa ligne dans l'interface, comme « phase : groove » pour les phases :
  * sources audio par couche (menus « reagit a » reglables sur Auto) : `sources_text` ;
  * ce que la phase du morceau change aux reglages (tempo, reactivite, effets, teinte) : `phase_effects_text` ;
  * le strobe auto : la raison du dernier eclair, `strobe_info_text`.
"""
from __future__ import annotations


def _fr(value: float, digits: int = 2) -> str:
    return f"{value:.{digits}f}".replace(".", ",")


def sources_text(bg: str, logo: str, bg_auto: bool, logo_auto: bool, phase: str | None = None) -> str:
    """« sources : fond <- basses (auto)  |  logo <- aigus (auto) »."""
    def part(name: str, src: str, auto: bool) -> str:
        return f"{name} <- {src}" + (" (auto)" if auto else "")
    lead = "sources : " + part("fond", bg, bg_auto) + "  |  " + part("logo", logo, logo_auto)
    if (bg_auto or logo_auto) and phase is None:
        lead += "  [phase inconnue : comme tout]"
    return lead


def phase_effects_text(mult: dict | None, enabled: bool = True) -> str:
    """Ce que les phases auto font aux masters : « tempo x0,30 | reactivite x0,60 | effets x0,35 | derive de teinte 3 deg/s »."""
    if not enabled or not mult:
        return "phases auto coupees : reglages tels quels"
    return (f"tempo x{_fr(mult['tempo'])} | reactivite x{_fr(mult['react'])} | effets x{_fr(mult['fx'])} | fond {_fr(mult['bg'] * 100, 0)} % | "
            f"derive de teinte {_fr(mult['hue'], 0)} deg/s")


def strobe_info_text(reason: str | None, age: float | None, armed: bool) -> str:
    """« strobe auto : dernier eclair = accent groove (le 1 de la mesure), il y a 3 s »."""
    if not armed:
        return "strobe auto : coupe"
    if not reason:
        return "strobe auto : arme, aucun eclair pour l'instant"
    return f"strobe auto : dernier eclair = {reason}, il y a {_fr(age or 0.0, 0)} s"


def phase_measures_text(info: dict | None) -> str:
    """Ce que le detecteur de phases mesure (kicks par seconde, energie / moyenne, gain des mediums + aigus sur 8 et 20 s)."""
    if not info or "kicks" not in info:
        return "mesures : -"
    return (f"mesures : kicks {_fr(info['kicks'], 1)}/s | energie x{_fr(info.get('energy', 1.0))} | "
            f"mediums + aigus x{_fr(info.get('top8', 1.0))} (8 s) x{_fr(info.get('top20', 1.0))} (20 s)")
