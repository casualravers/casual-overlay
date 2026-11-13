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


FX_NAMES = {"wobble": "ondulation", "ripple": "choc", "glitch": "glitch", "chroma": "aberration"}


def fx_text(layer: str, active: list | None) -> str:
    """Les effets d'une couche en mode auto par effet : "fond : ondulation <- basses 60 %, choc <- kick 50 %". `active` = (effet, source, dosage) de
    `EffectBank.active` ; None = la couche n'est pas en auto par effet (vide) ; liste vide = tout au repos."""
    if active is None:
        return ""
    if not active:
        return f"{layer} : tout au repos"
    parts = []
    for effect, source, dose in active:
        name = FX_NAMES.get(effect, effect)
        parts.append(f"{name} (derive lente)" if source == "derive" else f"{name} <- {source} {_fr(float(dose) * 100, 0)} %")
    return f"{layer} : " + ", ".join(parts)


def fx_line(fx_now: dict | None) -> str:
    """La ligne des voyants d'effets : fond puis logo, ou "source de la couche" quand le mode auto par effet ne s'applique pas."""
    fx_now = fx_now or {}
    bg, logo = fx_text("fond", fx_now.get("bg")), fx_text("logo", fx_now.get("logo"))
    if not bg and not logo:
        return "effets : source de la couche (reglage a la main, ou phase inconnue)"
    return "effets : " + "  |  ".join(x for x in (bg, logo) if x)


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


def foudre_info_text(info: dict | None) -> str:
    """Mode Foudre : « foudre : 312 segments, 14 pointes, pouls 2 » ; `info` = Lightning.stats() du moteur, None = pas de projection."""
    if not info:
        return "foudre : en attente de la projection"
    n = int(info.get("segments", 0))
    if n <= 0:
        return "foudre : au repos, attend un kick"
    return f"foudre : {n} segments, {int(info.get('tips', 0))} pointes, pouls {int(info.get('pulses', 0))}"


def phase_measures_text(info: dict | None) -> str:
    """Ce que le detecteur de phases mesure (kicks par seconde, energie / moyenne, gain des mediums + aigus sur 8 et 20 s)."""
    if not info or "kicks" not in info:
        return "mesures : -"
    return (f"mesures : kicks {_fr(info['kicks'], 1)}/s | energie x{_fr(info.get('energy', 1.0))} | "
            f"mediums + aigus x{_fr(info.get('top8', 1.0))} (8 s) x{_fr(info.get('top20', 1.0))} (20 s) | calme {round(100 * float(info.get('calm', 0.0)))} %")


def rec_status_text(seconds: float, size_bytes: int, peak_db: float, dropped: int, encoder: str) -> str:
    """Voyant de l'enregistrement : « REC 01:23 | 412 Mo | crete -3 dBFS | images dupliquees 0 | libx264 »."""
    total = int(seconds)
    clock = f"{total // 3600}:{total % 3600 // 60:02d}:{total % 60:02d}" if total >= 3600 else f"{total // 60:02d}:{total % 60:02d}"
    if peak_db <= -119.0:
        level = "pas de signal"
    else:
        level = f"crete {round(peak_db)} dBFS" + (" SATURE" if peak_db >= -0.1 else "")
    return f"REC {clock} | {round(size_bytes / 1e6)} Mo | {level} | images dupliquees {dropped} | {encoder}"


def rec_panel(recording: bool, status: dict | None, projection_open: bool, ffmpeg_ok: bool, done: dict | None, fmt_label: str, fps: int,
              audio_name: str | None) -> tuple[str, bool]:
    """(texte du voyant, bouton actif) de l'onglet « Rec » : pendant l'enregistrement le voyant et l'avertissement en cours ; sinon pourquoi on ne peut pas
    enregistrer (projection fermee, ffmpeg absent), ou ce qui va etre enregistre et le dernier fichier ecrit."""
    if recording:
        st = status or {}
        text = rec_status_text(float(st.get("seconds", 0.0)), int(st.get("bytes", 0)), float(st.get("peak_db", -120.0)), int(st.get("dropped", 0)),
                               str(st.get("encoder", "libx264")))
        if st.get("warning"):
            text += "\n" + str(st["warning"])
        return text, True
    if not projection_open:
        return "Ouvre la projection pour enregistrer.", False
    if not ffmpeg_ok:
        return "ffmpeg introuvable : enregistrement impossible.", False
    text = f"Pret : {fmt_label}, {fps} images/s, " + (f"son : {audio_name}" if audio_name else "sans son (aucune entree audio)")
    if done:
        path = done.get("path")
        text += "\n" + (f"Dernier fichier : {str(path).replace(chr(92), '/').rsplit('/', 1)[-1]}" if path else f"Dernier essai sans fichier : {done.get('warning') or '-'}")
    return text, True


def lead_text(auto: bool, manual_ms: float, est_ms: float | None, parts: dict | None) -> str:
    """Ligne de l'Avance (onglet Affichage) : « Avance auto : 85 ms = son 22 + detection 15 + image 23 + affichage ~25 (suppose) », ou en manuel
    « Avance manuelle : 30 ms (estimation auto : 85 ms) »."""
    if est_ms is None or not parts:
        est = "estimee quand la projection est ouverte"
        return f"Avance auto : {est}" if auto else f"Avance manuelle : {round(manual_ms)} ms (estimation auto : quand la projection est ouverte)"
    if not auto:
        return f"Avance manuelle : {round(manual_ms)} ms (estimation auto : {round(est_ms)} ms)"
    return (f"Avance auto : {round(est_ms)} ms = son {round(parts['son'])} + detection {round(parts['detection'])} + image {round(parts['image'])}"
            f" + affichage ~{round(parts['affichage'])} (suppose)")


def pulse_text(info: dict | None, lead_ms: float = 0.0) -> str:
    """Voyant de la pulsation (lot 4) : « pulsation calee, avance 80 ms : les effets partent en avance (ecart prediction - detection +3 ms) »."""
    if not info:
        return "pulsation : -"
    state = str(info.get("state", "absente"))
    names = {"cale": "calee", "libre": "libre (en cours d'accrochage)", "perdue": "perdue", "absente": "pas encore de BPM fiable"}
    text = "pulsation " + names.get(state, state)
    if lead_ms <= 0.0:
        return text + " | avance 0 : les effets suivent la detection"
    if info.get("predicting"):
        delta = float(info.get("delta_ms", 0.0))
        return text + f" | avance {round(lead_ms)} ms : effets en avance (ecart prediction - detection {delta:+.0f} ms)"
    return text + f" | avance {round(lead_ms)} ms en attente : reaction a la detection tant que la pulsation n'est pas calee et reguliere"
