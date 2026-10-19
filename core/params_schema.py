"""Table unique des reglages de casual-overlay (stdlib seulement, aucune dependance au moteur ni a la GUI).

Chaque reglage de `params` est decrit ICI, une seule fois : valeur par defaut, plage valide, valeurs permises (enum), PORTEE et
documentation. Tout le reste en est DERIVE :

- `defaults()`                    -> `DEFAULT_PARAMS` (dans l'ordre de la table : celui des fichiers sauves) ;
- `ROOM_KEYS`, `TRANSIENT_KEYS`,
  `MEMORY_KEYS`, `BG_KEYS`,
  `OVERLAY_PRESET_EXCLUDED`,
  `RELOAD_KEEP_KEYS`              -> les listes qui decidaient, chacune de son cote, ce qu'un preset, une scene, une memoire de
                                     masters, la touche R ou la sauvegarde ont le droit de toucher ;
- `validate(key, value)`          -> la validation de `coerce_params` (type, longueur d'une liste, enum, plage bornee).

PORTEES (`scope`) :
- `look`   : l'overlay (logo, effets, halo...) : capture par les presets overlay et les scenes ;
- `bg`     : le fond (source, motif) : capture par les presets de fond et les scenes ;
- `room`   : reglage de salle (masters, composition, strobe...) : jamais capture par un preset ni une scene, sauve avec P, enregistre
             par les memoires de masters ;
- `state`  : etat du moment (montee, idle, strobe arme) : comme `room` mais ni sauve ni recharge ni memorise ;
- `device` : depend du micro et de la salle (sensibilite du kick) : sauve, mais hors presets et scenes.

Ajouter un reglage = UNE ligne ici. `check_gl.py` (`check_params_schema`) verifie la coherence de la table avec le moteur, la GUI,
les presets integres, les automations et le hasard.
"""
from __future__ import annotations

import copy
from dataclasses import dataclass

SCOPES = ("look", "bg", "room", "state", "device")


@dataclass(frozen=True)
class Param:
    key: str
    default: object                 # float / int / str / liste de nombres ; `None` pour logo_path (rempli par defaults())
    lo: float | None = None         # borne basse (par element pour une liste) ; None = pas de borne
    hi: float | None = None
    choices: tuple | None = None    # valeurs permises d'une chaine (enum)
    scope: str = "look"
    keep: bool = False              # la touche R (recharge) ne remet pas cette cle a la valeur sauvee
    doc: str = ""


def _p(key, default, lo=None, hi=None, *, choices=None, scope="look", keep=False, doc="") -> Param:
    return Param(key, default, lo, hi, tuple(choices) if choices else None, scope, keep, doc)


# L'ordre est celui de l'ancien DEFAULT_PARAMS : les fichiers sauves gardent la meme disposition.
TABLE: tuple[Param, ...] = (
    _p("fx_on", [1, 1, 1, 1, 1], 0, 1, doc="Interrupteurs des 5 effets du fond (wobble, onde de choc, aberration, glitch, logo)."),
    _p("fx_int", [1.0, 1.0, 1.0, 1.0, 1.0], 0.0, 2.0, doc="Intensites des 5 effets du fond."),
    _p("fx_link", 1.0, 0.0, 1.0, doc="1 = le logo subit les memes effets que le fond ; 0 = effets propres (fxl_*)."),
    _p("fxl_on", [1, 1, 1, 1], 0, 1, doc="Interrupteurs des 4 effets de la couche logo (fx_link = 0)."),
    _p("fxl_int", [1.0, 1.0, 1.0, 1.0], 0.0, 2.0, doc="Intensites des 4 effets de la couche logo."),
    _p("noise_amount", 0.6, 0.0, 1.0, doc="Noise : force du hasard (0 = aucun effet)."),
    _p("noise_fx", [0, 0, 0, 0, 0], 0, 1, doc="Noise : effets sur lesquels il agit (wobble, onde, aberration, glitch, reaction du logo)."),
    _p("master", 1.0, 0.0, 2.0, doc="Intensite globale des effets de deformation."),
    _p("sensitivity", 1.0, 0.25, 4.0, scope="device", keep=True, doc="Sensibilite du detecteur de kick (depend du micro et de la salle)."),
    _p("react_master", 1.0, 0.0, 2.0, scope="room", doc="Master de l'audioreactivite : x tout ce qui vient de l'audio, 0 = aucune reaction."),
    _p("overlay_master", 1.0, 0.0, 1.0, scope="room", doc="Master de la presence de l'overlay : 0 = invisible, 1 = normal."),
    _p("hue_cycle", 0.0, -360.0, 360.0, scope="room", doc="Cycle de teinte : la teinte tourne toute seule, en degres par seconde (0 = fixe)."),
    _p("breath_overlay", 0.0, 0.0, 1.0, scope="room", doc="Respiration de l'overlay : sa presence oscille entre 1 - valeur et 1."),
    _p("breath_bg", 0.0, 0.0, 1.0, scope="room", doc="Respiration du fond, en opposition de phase avec celle de l'overlay."),
    _p("breath_period", 8.0, 1.0, 60.0, scope="room", doc="Periode de la respiration, en secondes."),
    _p("silence_on", 0.0, 0.0, 1.0, scope="room", doc="Mode silence : quand le son tombe, le visuel passe en idle."),
    _p("silence_level", 0.03, 0.0, 1.0, scope="room", doc="Niveau (rms normalise) sous lequel on parle de silence."),
    _p("silence_delay", 4.0, 0.5, 120.0, scope="room", doc="Secondes de silence avant de passer en idle."),
    _p("silence_tempo", 0.3, 0.0, 1.0, scope="room", doc="Tempo relatif en idle (1 = inchange)."),
    _p("idle", 0.0, 0.0, 1.0, scope="state", doc="Etat courant du mode silence (0..1), ecrit par SilenceGuard."),
    _p("comp_sym", 0, 0, 4, scope="room", doc="Symetrie : 0 aucune, 1 miroir gauche, 2 miroir haut, 3 quatre quadrants, 4 kaleidoscope."),
    _p("comp_seg", 6, 2, 16, scope="room", doc="Segments du kaleidoscope."),
    _p("comp_rot", 0.0, -360.0, 360.0, scope="room", doc="Rotation du kaleidoscope, en degres par seconde."),
    _p("comp_pix", 0.0, 0.0, 1.0, scope="room", doc="Pixelisation de toute l'image."),
    _p("comp_trail", 0.0, 0.0, 0.97, scope="room", doc="Trainee / feedback : l'image precedente s'estompe lentement."),
    _p("comp_warp", 0.0, 0.0, 1.0, scope="room", doc="Deformation globale."),
    _p("build", 0.0, 0.0, 1.0, scope="state", doc="Montee en cours (0..1), ecrite par le bouton Montee."),
    _p("build_time", 45.0, 1.0, 180.0, scope="room", doc="Duree de la montee, en secondes (le strobe monte de 2 a 16 Hz sur cette duree, puis reste au maximum jusqu'au drop)."),
    _p("hue_master", 0.0, 0.0, 360.0, scope="room", doc="Teinte globale en degres : rotation de couleur de toute l'image."),
    _p("bg_master", 1.0, 0.0, 1.0, scope="room", doc="Master de la luminosite du fond : 0 = fond noir (logo seul), 1 = normal."),
    _p("strobe_on", 0.0, 0.0, 1.0, scope="state", doc="Strobe arme / maintenu."),
    _p("strobe_mode", "manual", choices=("manual", "kick", "rate"), scope="room", doc="Mode du strobe."),
    _p("strobe_rate", 8.0, 1.0, 20.0, scope="room", doc="Cadence du strobe en Hz (modes manuel maintenu et cadence)."),
    _p("doodle_on", 0.0, 0.0, 1.0, scope="state", doc="Recuperation des doodles du site (case de l'onglet Affichage) : jamais sauvee, jamais dans un preset ni une scene."),
    _p("strobe_hits", 0, 0, 10 ** 9, scope="state", doc="Compteur d'appuis : un appui plus court qu'une image declenche quand meme un eclair."),
    _p("tempo", 1.0, 0.0, 2.0, scope="room", doc="Master du tempo : vitesse des animations et des automations (1 = normal)."),
    _p("bpm_auto", 0.0, 0.0, 1.0, scope="room", doc="Tempo automatique : le master du tempo (et la cadence du strobe en mode Cadence) suit le BPM detecte."),
    _p("drop_auto", 0.0, 0.0, 1.0, scope="room", doc="Montee / drop automatiques : une cassure detectee lance la montee, le retour des basses la relache (flash)."),
    _p("gpu_cap", 1.0, 0.0, 1.0, scope="room", doc="Securite GPU du VJ : la scene suivante est allegee (effets couteux coupes) si le GPU ne tiendrait plus l'image."),
    _p("fx_master", 1.0, 0.0, 2.0, scope="room", doc="Master des effets : x l'intensite de TOUS les effets ; 0 = aucun."),
    _p("strobe_max_hz", 10.0, 1.0, 30.0, scope="room", doc="Limiteur de strobe : cadence maximale (eclairs par seconde)."),
    _p("strobe_max_s", 8.0, 1.0, 120.0, scope="room", doc="Limiteur de strobe : duree maximale d'une rafale continue (s)."),
    # Incrustation du logo (pilotee par la GUI ou --logo*, relue a chaque image)
    _p("logo_source", "image", choices=("image", "text", "video"), keep=True, doc="Source du logo : image (PNG), texte ou video anime."),
    _p("logo_video", "", keep=True, doc="Fichier video / GIF anime (source video)."),
    _p("logo_key", "", keep=True, doc="#rrggbb a detourer si le fichier n'a pas d'alpha ('' = aucun)."),
    _p("logo_path", None, keep=True, doc="Image du logo ('' = aucun) ; defaut : le logo du depot."),
    _p("text_content", "CASUAL RAVERS", doc="Source texte : le texte (plusieurs lignes possibles)."),
    _p("text_font", "auto", doc="Nom de police de TEXT_FONTS, chemin d'un .ttf/.otf, ou auto."),
    _p("text_color", "#ffffff", doc="Couleur du texte."),
    _p("text_align", "center", choices=("left", "center", "right"), doc="Alignement (texte sur plusieurs lignes)."),
    _p("text_scale", 0.16, 0.03, 0.6, doc="Hauteur du bloc de texte, fraction de la hauteur de l'image."),
    _p("logo_x", 0.5, 0.0, 1.0, doc="Centre, fraction de la largeur (origine a gauche)."),
    _p("logo_y", 0.5, 0.0, 1.0, doc="Centre, fraction de la hauteur (origine en haut)."),
    _p("logo_scale", 0.35, 0.05, 0.95, doc="Cote du carre englobant, fraction de la hauteur."),
    _p("logo_opacity", 1.0, 0.0, 1.0, doc="Opacite du logo."),
    _p("logo_pulse", 0.12, 0.0, 0.5, doc="Zoom au kick (0,12 = +12 %)."),
    _p("logo_jitter", 0.004, 0.0, 0.03, doc="Tremblement sur les aigus, fraction de l'ecran."),
    _p("logo_glow", 1.0, 0.0, 3.0, doc="Intensite du contour lumineux."),
    _p("logo_glow_radius", 1.0, 0.0, 3.0, doc="Multiplicateur du rayon du contour."),
    _p("logo_glow_color", "#5fd4c8", doc="Couleur du contour lumineux."),
    # Halo holographique
    _p("holo_on", 1.0, 0.0, 1.0, doc="Halo holographique : interrupteur."),
    _p("holo_intensity", 1.0, 0.0, 3.0, doc="Force de la lumiere irisee."),
    _p("holo_reach", 1.3, 0.3, 3.0, doc="Portee : multiplicateur de l'etendue du halo (1 = environ 1,5 x le logo)."),
    _p("holo_warp", 1.0, 0.0, 3.0, doc="Deformation du fond (refraction + ondes)."),
    _p("holo_dust", 1.0, 0.0, 3.0, doc="Quantite de poussiere d'etoiles."),
    _p("holo_speed", 0.15, 0.0, 1.0, doc="Vitesse de l'animation (ondes, derive des etoiles)."),
    _p("holo_react", 0.0, 0.0, 2.0, doc="0 = pas audioreactif ; > 0 : intensite au kick, vitesse aux basses."),
    # Fonte acide du logo
    _p("melt_on", 0.0, 0.0, 1.0, doc="Fonte acide : interrupteur (coupee par defaut : une marque ne doit pas disparaitre sans l'avoir voulu)."),
    _p("melt_depth", 0.9, 0.0, 1.0, doc="Degre maximal de dissolution (1 = disparait entierement)."),
    _p("melt_period", 14.0, 4.0, 60.0, doc="Secondes pour un cycle complet."),
    _p("melt_scale", 1.0, 0.3, 4.0, doc="Finesse des trous."),
    _p("melt_edge", 1.0, 0.0, 2.0, doc="Largeur et intensite de la lisiere acide."),
    _p("melt_color", "#a6ff00", doc="Couleur de l'acide."),
    _p("melt_react", 0.0, 0.0, 2.0, doc="0 = pas audioreactif ; > 0 : cycle accelere par les basses."),
    # Cellules organiques du logo
    _p("cell_on", 0.0, 0.0, 1.0, doc="Cellules : interrupteur."),
    _p("cell_amount", 0.65, 0.0, 1.0, doc="Decomposition : 0 = logo intact .. 1 = entierement en cellules separees."),
    _p("cell_scale", 1.0, 0.3, 3.0, doc="Finesse des cellules."),
    _p("cell_fusion", 1.0, 0.2, 1.2, doc="Rayon des cellules."),
    _p("cell_speed", 0.3, 0.0, 2.0, doc="Vitesse de derive des cellules."),
    _p("cell_flat", 0.85, 0.0, 1.0, doc="0 = couleurs d'origine, 1 = aplat de la couleur au centre de la cellule."),
    _p("cell_ink", "#14103a", doc="Contour des cellules."),
    _p("cell_react", 0.0, 0.0, 2.0, doc="0 = pas audioreactif ; > 0 : derive acceleree par les basses."),
    # Fond : live (spectre ffmpeg), pattern (motif genere), video (fichier lu en boucle)
    _p("bg_mode", "live", choices=("live", "pattern", "video"), scope="bg", keep=True, doc="Source du fond."),
    _p("bg_video", "", scope="bg", doc="Fichier video / animation du fond video ('' = aucun)."),
    _p("bg_video_fit", "cover", choices=("cover", "stretch"), scope="bg", doc="cover = remplit en recadrant, stretch = etire."),
    _p("bg_palette", "classic", choices=("classic", "duo", "test"), scope="bg", doc="Palette du motif genere."),
    _p("bg_color1", "#3a1cff", scope="bg", doc="Palette duo : couleur 1."),
    _p("bg_color2", "#14f0d8", scope="bg", doc="Palette duo : couleur 2."),
    _p("bg_angle", 0.0, 0.0, 360.0, scope="bg", doc="Direction du degrade duo, en degres."),
    _p("bg_hue", 0.0, 0.0, 1.0, scope="bg", doc="Rotation de teinte, 0..1 (un tour)."),
    _p("bg_speed", 1.0, 0.0, 4.0, scope="bg", doc="Vitesse de defilement des degrades."),
    _p("bg_tile", 80.0, 20.0, 300.0, scope="bg", doc="Cote d'un carreau, en pixels pour 720 px de haut."),
    _p("bg_checker", 0.24, 0.0, 1.0, scope="bg", doc="Contraste du damier."),
    _p("bg_flip", 2.0, 0.0, 8.0, scope="bg", doc="Bascules du damier par seconde."),
    _p("bg_react", 1.0, 0.0, 2.0, scope="bg", doc="Reaction a l'audio : flash + damier au kick, defilement aux basses."),
    _p("auto_master", 1.0, 0.0, 1.0, keep=True, doc="Interrupteur general des automations (1 = actives)."),
)

assert len({p.key for p in TABLE}) == len(TABLE), "cle en double dans la table des reglages"
assert all(p.scope in SCOPES for p in TABLE)

BY_KEY: dict[str, Param] = {p.key: p for p in TABLE}


def keys(*scopes: str) -> tuple[str, ...]:
    """Les cles d'une ou plusieurs portees, dans l'ordre de la table."""
    return tuple(p.key for p in TABLE if p.scope in scopes)


ROOM_KEYS = keys("room", "state")                              # reglages de salle (y compris les etats du moment)
TRANSIENT_KEYS = keys("state")                                 # etats du moment : ni sauves ni recharges ni memorises
MEMORY_KEYS = keys("room")                                     # ce qu'une memoire de masters enregistre
BG_KEYS = keys("bg")                                           # ce que capturent les presets de fond et les scenes pour le fond
OVERLAY_PRESET_EXCLUDED = keys("device") + ROOM_KEYS           # ni presets ni scenes n'y touchent
RELOAD_KEEP_KEYS = tuple(p.key for p in TABLE if p.keep or p.scope in ("room", "state"))   # la touche R n'y touche pas


def is_bg(key: str) -> bool:
    """Une cle de reglage (ou d'automation) appartient-elle au fond ? (les automations `bg_*` suivent leur reglage)."""
    p = BY_KEY.get(key)
    return p.scope == "bg" if p is not None else False


def defaults(logo_path: str = "") -> dict:
    """Valeurs par defaut (copie profonde), dans l'ordre de la table. `logo_path` : le logo du depot (donne par le moteur)."""
    out = {p.key: copy.deepcopy(p.default) for p in TABLE}
    out["logo_path"] = logo_path
    return out


def _number(value) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool)


def _clamp(p: Param, value: float) -> float:
    if p.lo is not None and value < p.lo:
        return float(p.lo)
    if p.hi is not None and value > p.hi:
        return float(p.hi)
    return value


def validate(key: str, value):
    """(valide, valeur) pour un reglage lu d'un fichier ou d'un preset : mauvais type, liste de mauvaise longueur, valeur hors enum =
    refuse ; un nombre hors plage est RAMENE dans la plage. Une cle inconnue est refusee."""
    p = BY_KEY.get(key)
    if p is None:
        return False, None
    default = p.default
    if isinstance(default, list):
        if isinstance(value, list) and len(value) == len(default) and all(_number(v) for v in value):
            return True, [_clamp(p, float(v)) if isinstance(d, float) else int(_clamp(p, v)) for v, d in zip(value, default)]
        return False, None
    if isinstance(default, str) or default is None:
        if isinstance(value, str) and (p.choices is None or value in p.choices):
            return True, value
        return False, None
    if _number(value):
        return True, _clamp(p, float(value))
    return False, None
