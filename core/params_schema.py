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
- `state`  : etat du moment (montee, phase en cours, strobe arme) : comme `room` mais ni sauve ni recharge ni memorise ;
- `device` : depend du micro et de la salle (sensibilite du kick) : sauve, mais hors presets et scenes.

Ajouter un reglage = UNE ligne ici. `check_gl.py` (`check_params_schema`) verifie la coherence de la table avec le moteur, la GUI,
les presets integres, les automations et le hasard.
"""
from __future__ import annotations

import copy
from dataclasses import dataclass

from core import audio_layers, recording, routing, sto_fields

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
    _p("src_bg", "auto", choices=audio_layers.SOURCES, doc="Partie du son qui pilote le fond (effets, animations) : une bande, les attaques, ou la grille de la pulsation."),
    _p("src_logo", "auto", choices=audio_layers.SOURCES, doc="Partie du son qui pilote le logo (effets, reactions, halo, fonte, cellules) ; ignore tant que fx_link = 1 (le logo suit alors la source du fond)."),
    _p("sens_bg", 1.0, 0.25, 4.0, doc="Sensibilite du kick propre au fond (x la sensibilite generale) ; 1 = le kick commun."),
    _p("sens_logo", 1.0, 0.25, 4.0, doc="Sensibilite du kick propre au logo (x la sensibilite generale) ; 1 = le kick commun."),
    _p("lag_bg", 0.0, 0.0, 0.5, doc="Retard (secondes) de la reaction du fond au son."),
    _p("lag_logo", 0.0, 0.0, 0.5, doc="Retard (secondes) de la reaction du logo au son."),
    _p("smooth_bg", 0.0, 0.0, 1.0, doc="Lissage de la reaction du fond (0 = sec, 1 = attaque 120 ms et relachement 800 ms)."),
    _p("smooth_logo", 0.0, 0.0, 1.0, doc="Lissage de la reaction du logo."),
    _p("noise_amount", 0.6, 0.0, 1.0, doc="Noise : force du hasard (0 = aucun effet)."),
    _p("noise_fx", [0, 0, 0, 0, 0], 0, 1, doc="Noise : effets sur lesquels il agit (wobble, onde, aberration, glitch, reaction du logo)."),
    _p("master", 1.0, 0.0, 2.0, doc="Intensite globale des effets de deformation."),
    _p("sensitivity", 1.0, 0.25, 4.0, scope="device", keep=True, doc="Sensibilite du detecteur de kick (depend du micro et de la salle)."),
    _p("calm_react", 1.0, 0.25, 4.0, scope="device", keep=True,
       doc="Reactivite du calme (mode IDLE) : plus haut = silence et ambiance adoptes plus vite, avec des seuils plus hauts (micro bruyant) ; 1 = comme avant."),
    _p("hit_sensitivity", 1.0, 0.25, 4.0, scope="device", doc="Sensibilite des frappes aigues (charleys, claps) ; sauvee, hors presets et scenes."),
    _p("react_master", 1.0, 0.0, 2.0, scope="room", doc="Master de l'audioreactivite : x tout ce qui vient de l'audio, 0 = aucune reaction."),
    _p("overlay_master", 1.0, 0.0, 1.0, scope="room", doc="Master de la presence de l'overlay : 0 = invisible, 1 = normal."),
    _p("hue_cycle", 0.0, -360.0, 360.0, scope="room", doc="Cycle de teinte : la teinte tourne toute seule, en degres par seconde (0 = fixe)."),
    _p("hue_spectrum", 0.0, 0.0, 1.0, scope="room", doc="Teinte tiree de la musique : les graves tirent les couleurs vers le chaud, les aigus vers le froid (0 = aucun effet)."),
    _p("breath_overlay", 0.0, 0.0, 1.0, scope="room", doc="Respiration de l'overlay : sa presence oscille entre 1 - valeur et 1."),
    _p("breath_bg", 0.0, 0.0, 1.0, scope="room", doc="Respiration du fond, en opposition de phase avec celle de l'overlay."),
    _p("breath_period", 8.0, 1.0, 60.0, scope="room", doc="Periode de la respiration, en secondes."),
    _p("breath_beats", 0.0, 0.0, 64.0, scope="room", doc="Respiration lente sur la pulsation : un cycle dure ce nombre de temps (avec une pulsation calee ou libre) ; 0 = breath_period en secondes."),
    _p("breath_pulse", 0.0, 0.0, 1.0, scope="room", doc="Respiration sur la pulsation : la presence de l'overlay, la luminosite et la derive du fond suivent la phase de la pulsation (courbe douce, creux entre les temps) et l'enveloppe des basses ; un seul reglage d'amplitude, 0 = coupe."),
    _p("scene_auto_folder", 1.0, 0.0, 1.0, scope="room", doc="Rangement auto (#241) : une NOUVELLE scene enregistree prend le dossier de son type de fond (snap, ridge, stochastique, video...) ; 0 = elle reste a la racine. N'agit que sur les nouvelles scenes."),
    _p("auto_phase", 1.0, 0.0, 1.0, scope="room", doc="Phases auto : le visuel suit les phases du morceau (ambiance, groove, montee, cassure, pic) tout seul."),
    _p("one_auto", 1.0, 0.0, 1.0, scope="room", doc="Temps 1 auto : sans appui, le « 1 » se pose seul a la reprise des kicks et au drop ; une grille posee a la main n'est jamais deplacee."),
    _p("cycle_accent", 1.0, 0.0, 1.0, scope="room", doc="Renfort des fins de cycles (groove, pic, effets auto) : glitch et aberration plus forts sur le dernier temps d'un 8, les 2 derniers d'un 16, les 4 derniers d'un 32 ; 0 = aucun."),
    _p("phase_tempo", 1.0, 0.0, 2.0, scope="state", doc="Multiplicateur de tempo de la phase en cours (ecrit par la boucle de rendu)."),
    _p("phase_react", 1.0, 0.0, 3.0, scope="state", doc="Multiplicateur de reactivite de la phase en cours."),
    _p("phase_fx", 1.0, 0.0, 3.0, scope="state", doc="Multiplicateur d'intensite des effets de la phase en cours."),
    _p("phase_overlay", 1.0, 0.0, 1.0, scope="state", doc="Presence de l'overlay dans la phase en cours."),
    _p("phase_bg", 1.0, 0.0, 1.0, scope="state", doc="Luminosite du fond dans la phase en cours."),
    _p("phase_hue", 0.0, 0.0, 60.0, scope="state", doc="Derive de teinte de la phase en cours (degres par seconde)."),
    _p("phase_breath", 0.0, 0.0, 1.0, scope="state", doc="Respiration de la phase en cours (0..1)."),
    _p("comp_sym", 0, 0, 4, scope="room", doc="Symetrie : 0 aucune, 1 miroir gauche, 2 miroir haut, 3 quatre quadrants, 4 kaleidoscope."),
    _p("comp_seg", 6, 2, 16, scope="room", doc="Segments du kaleidoscope."),
    _p("comp_rot", 0.0, -360.0, 360.0, scope="room", doc="Rotation du kaleidoscope, en degres par seconde."),
    _p("comp_pix", 0.0, 0.0, 1.0, scope="room", doc="Pixelisation de toute l'image."),
    _p("comp_trail", 0.0, 0.0, 0.97, scope="room", doc="Trainee / feedback : l'image precedente s'estompe lentement."),
    _p("comp_warp", 0.0, 0.0, 1.0, scope="room", doc="Deformation globale."),
    _p("build", 0.0, 0.0, 1.0, scope="state", doc="Montee en cours (0..1), ecrite par le bouton Montee."),
    _p("build_auto", 0.0, 0.0, 1.0, scope="state", doc="1 = la montee en cours a ete lancee par les detecteurs (phases, cassure) : ses eclairs sont ceux du strobe auto (rien s'il est coupe, rien avant 4 s, 4 Hz au plus) ; 0 = lancee a la main."),
    _p("build_time", 16.0, 2.0, 60.0, scope="room", doc="Duree de la montee, en secondes (le strobe monte de 0,5 a 16 Hz sur cette duree, puis reste au maximum jusqu'au drop)."),
    _p("hue_master", 0.0, 0.0, 360.0, scope="room", doc="Teinte globale en degres : rotation de couleur de toute l'image."),
    _p("bg_master", 1.0, 0.0, 1.0, scope="room", doc="Master de la luminosite du fond : 0 = fond noir (logo seul), 1 = normal."),
    _p("strobe_on", 0.0, 0.0, 1.0, scope="state", doc="Strobe arme / maintenu."),
    _p("strobe_why", "", scope="state", doc="Raison du dernier eclair demande par la GUI (drop...), affichee par le voyant du strobe ; vide = eclair manuel."),
    _p("strobe_auto", 1.0, 0.0, 1.0, scope="room", doc="Strobe auto : le strobe decide seul quand eclairer (phase du morceau, kicks, frappes aigues, grille du temps) ; coupe par la PANIQUE."),
    _p("strobe_mode", "manual", choices=("manual", "kick", "light"), scope="room", doc="Mode du strobe (light = leger : un kick sur N)."),
    _p("strobe_div", 4, 2, 16, scope="room", doc="Strobe leger : un eclair tous les N kicks (4 = un kick sur quatre, soit un par mesure)."),
    _p("trans_time", 0.8, 0.0, 3.0, scope="room", doc="Duree de la transition au changement de scene, en secondes (0 = pas de transition : l'ancien flash du VJ, rien a la main)."),
    _p("trans_kind", "fade", choices=("fade", "zoom"), scope="room", doc="Style de la transition de scene : fondu enchaine (fade) ou zoom avec aberration (zoom)."),
    _p("sync_lead_ms", 0.0, 0.0, 200.0, scope="room", doc="Avance manuelle (ms) : sur un morceau regulier, les effets « sur kick » partent ce temps AVANT le kick detecte pour que la lumiere tombe avec le son ; 0 = on reagit a la detection. Utilisee seulement si sync_lead_auto = 0."),
    _p("sync_lead_auto", 1.0, 0.0, 1.0, scope="room", doc="Avance automatique (1, defaut) : estimee en direct (core.latency : son mesure + detection + image + affichage suppose) ; 0 = le curseur sync_lead_ms."),
    _p("sync_lead_est", 0.0, 0.0, 200.0, scope="state", doc="Avance estimee (ms), ecrite par la boucle de rendu (core.latency.LeadEstimator) ; etat du moment, jamais sauve."),
    _p("strobe_dark", 1.0, 0.05, 1.0, scope="room", doc="Seuil de luminosite du strobe : il ne blanchit que les parties de l'image plus sombres que ce seuil (1 = toute l'image)."),
    _p("strobe_kind", "strobe", choices=("strobe", "foudre"), scope="room", doc="Ce que fait le strobe : eclairs blancs (strobe) ou arbre de lumiere fractal declenche par les kicks (foudre)."),
    _p("foudre_amount", 0.7, 0.0, 1.5, scope="room", doc="Foudre : intensite de la lumiere."),
    _p("foudre_reach", 1.0, 0.4, 1.5, scope="room", doc="Foudre : portee des branches (1 = jusqu'au bord de l'image)."),
    _p("foudre_color", "#3a7bff", scope="room", doc="Foudre : couleur du halo (le coeur est toujours blanc)."),
    _p("foudre_drop", 0, 0, 10 ** 9, scope="state", doc="Compteur : chaque increment declenche une grande decharge a pleine profondeur (drop)."),
    _p("drop_fx", 0, 0, 10 ** 9, scope="state", doc="Compteur : chaque increment lance l'effet du Drop (core/drop.py : un style tire au hasard + rafale d'effets ~1,5 s)."),
    _p("drop_clear", 0, 0, 10 ** 9, scope="state", doc="Compteur : chaque increment coupe net l'effet du Drop en cours (PANIQUE)."),
    _p("drop_amount", 1.0, 0.0, 1.5, scope="room", doc="Force de l'effet du Drop (0 = aucun, 1 = normal)."),
    _p("foudre_clear", 0, 0, 10 ** 9, scope="state", doc="Compteur : chaque increment vide la foudre en cours (panique)."),
    _p("remote_on", 0.0, 0.0, 1.0, scope="state", doc="Telecommande telephone (case de l'onglet Affichage) : desactivee a chaque lancement, jamais sauvee, jamais dans un preset ni une scene."),
    _p("doodle_on", 0.0, 0.0, 1.0, scope="state", doc="Recuperation des doodles du site (case de l'onglet Affichage) : jamais sauvee, jamais dans un preset ni une scene."),
    _p("strobe_hits", 0, 0, 10 ** 9, scope="state", doc="Compteur d'appuis : un appui plus court qu'une image declenche quand meme un eclair."),
    _p("tempo", 1.0, 0.0, 2.0, scope="room", doc="Master du tempo : vitesse des animations et des automations (1 = normal)."),
    _p("bpm_auto", 1.0, 0.0, 1.0, scope="room", doc="Tempo automatique : le master du tempo suit le BPM detecte."),
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
    _p("sched_on", 0.0, 0.0, 1.0, scope="device", doc="Planning de la soiree : le texte du logo change tout seul a l'heure de chaque passage."),
    _p("qr_on", 0.0, 0.0, 1.0, scope="device", doc="QR code permanent dans un coin de la projection (pour que le public rejoigne le site) ; sauve, hors presets et scenes."),
    _p("qr_url", "", scope="device", doc="Adresse encodee dans le QR code de la projection (213 octets au plus)."),
    _p("rec_format", recording.DEFAULT_FORMAT, choices=tuple(recording.FORMATS), scope="device", doc="Enregistrement : format de l'image (vertical 1080x1920, vertical_hd 720x1280 pour les looks lourds, horizontal, carre)."),
    _p("rec_fps", 30, 30, 60, scope="device", doc="Enregistrement : images par seconde (30 ou 60 ; 60 ne tient pas en 1080x1920 sur un GPU integre)."),
    _p("rec_dir", recording.DEFAULT_DIR, scope="device", doc="Enregistrement : dossier des fichiers MP4 (~ = dossier de l'utilisateur)."),
    _p("rec_delay_ms", 40.0, 0.0, 300.0, scope="device", doc="Enregistrement : retard (ms) de l'audio par rapport a l'image, pour que l'effet tombe sur le son (le visuel reagit apres le son) ; le reglage « Avance » (sync_lead_ms) est retranche automatiquement (le fichier n'a pas de latence d'affichage) ; tools/calibrer_avance.py mesure projecteur + trajet acoustique, donc donne une valeur trop grande pour un fichier : regle a l'oreille sur un clip."),
    _p("rec_clips", 0.0, 0.0, 1.0, scope="device", doc="Enregistrement : apres l'arret, 3 a 5 extraits de 15 a 30 s calés sur les moments forts du son (drops, pics), prets a poster ; "
                                                        "ajoute un calcul a chaque arret, donc coupe par defaut."),
    _p("rec_encoder", "libx264", choices=("libx264", "materiel"), scope="device", doc="Enregistrement : encodeur x264 (defaut, qualite previsible) ou materiel (nvenc, amf, qsv s'ils fonctionnent)."),
    _p("qr_corner", "bas droite", choices=("bas droite", "bas gauche", "haut droite", "haut gauche"), scope="device", doc="Coin de la projection qui porte le QR code."),
    _p("qr_size", 0.18, 0.08, 0.5, scope="device", doc="Cote du QR code, en fraction de la hauteur de l'ecran (marge blanche comprise)."),
    _p("sched_lines", "", scope="device", doc="Planning : une ligne par passage, `HH:MM texte` (voir core/schedule.py)."),
    _p("text_font", "auto", doc="Nom de police de TEXT_FONTS, chemin d'un .ttf/.otf, ou auto."),
    _p("text_color", "#ffffff", doc="Couleur du texte."),
    _p("logo_texts", "", doc="Textes suivants du logo texte, un par ligne (`\\n` = retour a la ligne) : ils se succedent apres le texte principal."),
    _p("text_bars", 4, 1, 16, doc="Rotation des textes : changement toutes les N mesures (4 kicks)."),
    _p("text_idx", 0, 0, 10 ** 6, scope="state", doc="Indice du texte affiche dans la rotation (0 = le principal)."),
    _p("text_fade", 1.0, 0.0, 1.0, scope="state", doc="Fondu de la rotation des textes : presence du logo pendant le changement."),
    _p("text_border", 2.0, 0.0, 8.0, doc="Texte : epaisseur de la fine bordure noire autour des lettres, en pixels de la texture du texte (0 = aucune)."),
    _p("text_ola_on", 0.0, 0.0, 1.0, doc="Texte : vague de lettres (ola) : les lettres s'eteignent puis se rallument de gauche a droite, en boucle."),
    _p("text_ola_dur", 6.0, 1.0, 10.0, doc="Vague de lettres : duree d'un balayage complet (extinction puis rallumage), en secondes."),
    _p("text_ola_every", 20.0, 5.0, 120.0, doc="Vague de lettres : periode de la boucle, en secondes (au moins la duree du balayage)."),
    _p("text_ola_fade", 1.0, 0.1, 1.5, doc="Vague de lettres : duree du fondu d'une lettre, en secondes."),
    _p("text_fill_on", 0.0, 0.0, 1.0, doc="Texte : remplissage des lettres par un motif de reaction-diffusion qui les colonise en vague (lettre par lettre, du bas vers le haut)."),
    _p("text_fill_dur", 2.7, 0.5, 3.0, doc="Remplissage : duree pour coloniser une lettre, en secondes (3 s au plus) : le front monte du bas au haut, puis le motif finit de pousser."),
    _p("text_fill_stagger", 3.0, 0.0, 8.0, doc="Remplissage : ecart de depart entre la premiere et la derniere lettre, en secondes (la vague de colonisation)."),
    _p("text_fill_scale", 1.0, 0.5, 2.0, doc="Remplissage : finesse du motif (1 = ~2 cellules de motif par pixel de la texture du texte ; plus grand = motif plus fin)."),
    _p("text_fill_contrast", 0.9, 0.0, 1.0, doc="Remplissage, lettres pleines : de combien la matiere assombrit les lettres (sans effet en mode transparent)."),
    _p("text_front_edge", 3.0, 0.0, 30.0, doc="Remplissage : epaisseur de la ligne du front de colonisation (couleur du texte), en pixels de la texture du texte (0 = aucune)."),
    _p("text_front_glint", 0.5, 0.0, 1.0, doc="Remplissage : scintillement de la ligne du front (frisson de luminosite qui court le long de la ligne ; 0 = ligne fixe)."),
    _p("text_fill_edge", 3.0, 1.0, 8.0, doc="Remplissage, lettres transparentes : epaisseur du bord conserve, en pixels de la texture du texte."),
    _p("text_fill_clear", 1.0, 0.0, 1.0, doc="Remplissage : 1 = lettres transparentes sauf les bords, la colonisation les remplit ; 0 = lettres pleines que la colonisation assombrit."),
    _p("text_fill_every", 20.0, 5.0, 120.0, doc="Remplissage : le motif repart de zero toutes les N secondes (sans vague ; avec la vague, au rallumage)."),
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
    # Irregularites des anneaux (#256) : bruit ajoute au champ du halo (core.holo.wobble_v) ; 0 = anneaux parfaits, comme avant
    _p("holo_wobble", 0.0, 0.0, 1.0, doc="Halo : irregularite des anneaux (0 = parfaits, comme avant ; chaque anneau a sa propre deformation)."),
    _p("holo_wobble_speed", 1.0, 0.0, 2.0, doc="Halo : vitesse des irregularites des anneaux (0 = figees)."),
    # La reaction au son de chaque reglage du halo (#247) est sa ligne de routage `lg_holo_<nom>` (pastille a droite du curseur, #259) : plus de reglage a part
    # Fonte acide du logo
    _p("melt_on", 0.0, 0.0, 1.0, doc="Fonte acide : interrupteur (coupee par defaut : une marque ne doit pas disparaitre sans l'avoir voulu)."),
    _p("melt_depth", 0.9, 0.0, 1.0, doc="Degre maximal de dissolution (1 = disparait entierement)."),
    _p("melt_period", 14.0, 4.0, 60.0, doc="Secondes pour un cycle complet."),
    _p("melt_beats", 0.0, 0.0, 64.0, doc="Fonte sur la pulsation : un cycle dure ce nombre de temps (avec une pulsation calee ou libre) ; 0 = melt_period en secondes."),
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
    _p("bg_palette", "classic", choices=("classic", "duo", "test", "stochastique", "labyrinthe"), scope="bg",
       doc="Palette du motif genere (stochastique = points dont la densite dessine un champ qui bouge ; labyrinthe = reaction-diffusion)."),
    _p("bg_color1", "#3a1cff", scope="bg", doc="Palette duo : couleur 1."),
    _p("bg_color2", "#14f0d8", scope="bg", doc="Palette duo : couleur 2."),
    _p("bg_angle", 0.0, 0.0, 360.0, scope="bg", doc="Direction du degrade duo, en degres."),
    _p("bg_hue", 0.0, 0.0, 1.0, scope="bg", doc="Rotation de teinte, 0..1 (un tour)."),
    _p("bg_speed", 1.0, 0.0, 4.0, scope="bg", doc="Vitesse de defilement des degrades."),
    _p("bg_tile", 80.0, 20.0, 300.0, scope="bg", doc="Cote d'un carreau, en pixels pour 720 px de haut."),
    _p("bg_checker", 0.24, 0.0, 1.0, scope="bg", doc="Contraste du damier."),
    _p("bg_flip", 2.0, 0.0, 8.0, scope="bg", doc="Bascules du damier par seconde."),
    _p("bg_react", 1.0, 0.0, 2.0, scope="bg", doc="Reaction a l'audio : flash + damier au kick, defilement aux basses."),
    _p("bg_sto_field", "noise", choices=sto_fields.VALUES, scope="bg",
       doc="Palette stochastique : forme du champ que les points dessinent (taches, organique, cellules, reseau, veines, marbre, vagues, degrade ; #229)."),
    _p("bg_sto_size", 10.0, 3.0, 60.0, scope="bg", doc="Palette stochastique : taille des points, en pixels pour 720 px de haut."),
    _p("bg_sto_regular", 1.0, 0.0, 1.0, scope="bg", doc="Palette stochastique : 1 = bruit bleu (sans amas), 0 = aleatoire brut (grain)."),
    _p("bg_sto_vary", 0.0, 0.0, 1.0, scope="bg", doc="Palette stochastique : la taille des points suit le champ (0 = taille constante)."),
    _p("bg_sto_density", 0.5, 0.0, 1.0, scope="bg", doc="Palette stochastique : niveau moyen du champ (part de points allumes)."),
    _p("bg_sto_soft", 0.0, 0.0, 1.0, scope="bg",
       doc="Palette stochastique : douceur, les points naissent et meurent en grossissant / retrecissant (0 = allumage sec, comme avant)."),
    _p("bg_sto_invert", 0.0, 0.0, 1.0, scope="bg", doc="Palette stochastique : 1 = echange la couleur des points et celle du fond."),
    _p("bg_sto_rainbow", 0.0, 0.0, 1.0, scope="bg", doc="Palette stochastique : la teinte des points suit le champ (0 = couleur 1 seule)."),
    _p("bg_sto_border", 0.0, 0.0, 12.0, scope="bg",
       doc="Palette stochastique : epaisseur de la bordure des points, en pixels pour 720 px de haut, tracee dans le point (0 = aucune)."),
    _p("bg_sto_border_color", "#ffffff", scope="bg", doc="Palette stochastique : couleur de la bordure des points."),
    _p("bg_sto_levels", "continuous", choices=("continuous", "2", "3"), scope="bg",
       doc="Palette stochastique : paliers de taille des points (continuous = rayon continu ; 2 ou 3 = tailles fixes selon le champ, facon trame)."),
    _p("bg_sto_tex", "none", choices=("none", "wave", "tiling"), scope="bg",
       doc="Palette stochastique : texture de la zone delimitee par la tache des points (none = aplat ; wave = lignes d'oscilloscope ; "
           "tiling = pavage de reaction-diffusion balaye par une vague)."),
    _p("bg_sto_tex_scale", 1.0, 0.3, 3.0, scope="bg", doc="Palette stochastique : finesse de la texture (espacement des lignes, taille du pavage)."),
    _p("bg_sto_tex_contrast", 0.6, 0.0, 1.0, scope="bg", doc="Palette stochastique : eclaircissement de la couleur 1 sur les lignes / le pavage (0 = aplat)."),
    _p("bg_sto_tex_every", 8.0, 2.0, 30.0, scope="bg", doc="Palette stochastique : secondes entre deux vagues du pavage."),
    _p("bg_sto_tex_sync", 0.0, 0.0, 1.0, scope="bg", doc="Palette stochastique : 1 = la vague du pavage repart toutes les N mesures, sur le temps 1."),
    _p("bg_sto_tex_bars", "4", choices=("1", "2", "4", "8"), scope="bg", doc="Palette stochastique : mesures entre deux vagues du pavage (calage sur la mesure)."),
    # front de la vague du pavage (#249, core/stotex.py : front_pos) : forme et direction propres (le front ne suit plus l'angle du degrade)
    _p("bg_sto_front", "straight", choices=("straight", "circle", "arc", "organic"), scope="bg",
       doc="Palette stochastique : forme du front de la vague du pavage (droit, cercle qui grandit du centre, arc venu d'un cote, organique)."),
    _p("bg_sto_front_angle", 0.0, 0.0, 360.0, scope="bg", doc="Palette stochastique : direction d'ou vient la vague (droit, arc, organique d'origine cote), en degres."),
    _p("bg_sto_front_curve", 0.5, 0.0, 1.0, scope="bg", doc="Palette stochastique : courbure de l'arc du front (0 = presque droit, 1 = tres arrondi)."),
    _p("bg_sto_front_rough", 0.5, 0.0, 1.0, scope="bg", doc="Palette stochastique : irregularite du front organique (0 = cercle ou arc net)."),
    _p("bg_sto_front_origin", "center", choices=("center", "side"), scope="bg", doc="Palette stochastique : origine du front organique (centre ou cote)."),
    _p("bg_rd_shape", 0.6, 0.0, 1.0, scope="bg", doc="Palette labyrinthe : 0 = taches qui se repoussent, 1 = lignes sinueuses (labyrinthe)."),
    _p("bg_rd_scale", 1.0, 0.3, 2.0, scope="bg", doc="Palette labyrinthe : taille du motif (plus grand = lignes et taches plus epaisses)."),
    _p("bg_rd_offset", 0.012, 0.0, 0.05, scope="bg", doc="Palette labyrinthe : ecart entre les trois couches colorees (fraction de la hauteur)."),
    _p("bg_rd_color1", "#ff4a2a", scope="bg", doc="Palette labyrinthe : couleur de la couche 1."),
    _p("bg_rd_color2", "#2f6bff", scope="bg", doc="Palette labyrinthe : couleur de la couche 2."),
    _p("bg_rd_color3", "#19d6a5", scope="bg", doc="Palette labyrinthe : couleur de la couche 3 (posee par-dessus)."),
    _p("bg_rd_ground", "#120d14", scope="bg", doc="Palette labyrinthe : couleur du fond."),
    _p("bg_rd_mix", 0.0, 0.0, 1.0, scope="bg", doc="Palette labyrinthe : variete des zones (points, lignes, taches dans la meme image) ; 0 = une seule forme partout."),
    _p("bg_rd_drift", 0.3, 0.0, 1.0, scope="bg", doc="Palette labyrinthe : vitesse de derive des zones de formes."),
    _p("bg_rd_smooth", 0.5, 0.0, 1.0, scope="bg", doc="Palette labyrinthe : fluidite de l'affichage (naissance, soudure, rupture sans saut) ; 0 = rendu d'avant, 1 = tau de 0,25 s."),
    _p("bg_rd_void", 0.0, 0.0, 1.0, scope="bg",
       doc="Palette labyrinthe : zones vides (sans matiere) et zones denses qui derivent ; 0 = tout est rempli, 1 = environ la moitie de l'image vide."),
    _p("bg_rd_spawn", 1.0, 0.0, 1.0, scope="bg",
       doc="Palette labyrinthe : part des kicks qui sement de nouvelles taches (1 = chaque kick, comme avant ; 0 = aucune, le motif grandit seul)."),
    _p("bg_rd_bud", 0.0, 0.0, 1.0, scope="bg",
       doc="Palette labyrinthe : 1 = un nouveau germe ne prend que s'il touche le motif existant (bourgeons, rien ne nait dans le vide)."),
    # densite variable (#160, core/reaction.py : dens_value) : lignes plus serrees pres d'un « aimant » ou dans des zones qui derivent
    _p("bg_rd_dens", "none", choices=("none", "magnet", "zones"), scope="bg",
       doc="Palette labyrinthe : densite variable (lignes serrees pres d'un ou plusieurs foyers qui derivent, ou dans des zones) ; none = espacement regle partout."),
    _p("bg_rd_dens_amount", 0.7, 0.0, 1.0, scope="bg", doc="Palette labyrinthe : intensite de la densite (1 = lignes ~2 fois plus serrees au plus dense)."),
    _p("bg_rd_dens_reach", 0.4, 0.1, 1.0, scope="bg", doc="Palette labyrinthe : portee d'un foyer, ou taille des zones, de la densite."),
    _p("bg_rd_dens_count", 1.0, 1.0, 4.0, scope="bg", doc="Palette labyrinthe : nombre de foyers de la densite (aimant)."),
    _p("bg_rd_dens_drift", 0.5, 0.0, 2.0, scope="bg", doc="Palette labyrinthe : vitesse de derive des foyers ou des zones de la densite."),
    _p("bg_rd_layers", 0.0, 0.0, 1.0, scope="bg", doc="Palette labyrinthe : epaisseur differente par couche (1 fine, 3 epaisse) ; 0 = meme seuil partout."),
    _p("bg_rd_sym", "none", choices=("none", "mirror_lr", "mirror_tb", "quad", "rot"), scope="bg",
       doc="Palette labyrinthe : symetrie de colonisation (germes et zones) ; le motif lui-meme n'est jamais strictement symetrique."),
    _p("bg_rd_branches", 4, 2, 8, scope="bg", doc="Palette labyrinthe : nombre de branches de la symetrie en rotation."),
    _p("bg_rd_free", 0.5, 0.0, 1.0, scope="bg", doc="Palette labyrinthe : liberte de la symetrie (0 = la plus stricte possible, 1 = presque aucune)."),
    # Masque d'expansion du labyrinthe (#178, core/reaction.py : mask_value) : limite ou la matiere a le droit de vivre
    _p("bg_rd_mask", "none", choices=("none", "circles", "bands", "gradient", "noise"), scope="bg",
       doc="Palette labyrinthe : masque d'expansion (cercles, bandes, degrade, bruit) qui limite ou le motif vit ; none = pas de masque."),
    _p("bg_rd_mask_count", 3.0, 1.0, 8.0, scope="bg", doc="Palette labyrinthe : masque, nombre de zones (cellules, bandes, periodes, finesse du bruit)."),
    _p("bg_rd_mask_size", 0.5, 0.1, 0.9, scope="bg", doc="Palette labyrinthe : masque, part de l'image ou la matiere vit."),
    _p("bg_rd_mask_angle", 0.0, 0.0, 360.0, scope="bg", doc="Palette labyrinthe : masque, rotation de la disposition (degres)."),
    _p("bg_rd_mask_soft", 0.4, 0.0, 1.0, scope="bg", doc="Palette labyrinthe : masque, douceur des bords (0 = net, 1 = tres progressif)."),
    _p("bg_rd_mask_invert", 0.0, 0.0, 1.0, scope="bg", doc="Palette labyrinthe : masque, 1 = la matiere vit hors de la forme."),
    _p("bg_rd_mask_drift", 0.4, 0.0, 2.0, scope="bg", doc="Palette labyrinthe : masque, vitesse de derive des zones."),
    _p("auto_master", 1.0, 0.0, 1.0, keep=True, doc="Interrupteur general des automations (1 = actives)."),
    # visualizer du cerveau (#192, gui/gui_brain.py) : etat du panneau, portee device (ni presets, ni scenes, ni memoires de masters),
    # sauve tout seul dans brain_ui.json (core/params.py : save_brain_ui) et relu au lancement
    _p("brain_on", 0, 0, 1, scope="device", keep=True, doc="Cerveau : 1 = le panneau du cerveau occupe la place de l'espace de controle global du Mode VJ."),
    _p("brain_view", "iso", choices=("iso", "mandala", "tunnel", "sismo"), scope="device", keep=True, doc="Cerveau : vue choisie."),
    _p("brain_detached", 0, 0, 1, scope="device", keep=True, doc="Cerveau : 1 = panneau dans une fenetre a part (fermer la fenetre le raccroche)."),
    _p("brain_geom", "", scope="device", keep=True, doc="Cerveau : position et taille de la fenetre detachee (geometrie Tk, `LxH+X+Y`)."),
    _p("brain_topmost", 0, 0, 1, scope="device", keep=True, doc="Cerveau : 1 = fenetre detachee toujours au-dessus."),
    _p("brain_causes", 1, 0, 1, scope="device", keep=True, doc="Cerveau : 1 = lignes de cause (frappe -> effet) et « <- kick » dans le fil."),
    _p("brain_hidden", "", scope="device", keep=True, doc="Cerveau : pistes d'effets masquees, separees par des virgules (strobe,glitch...)."),
    # Routage audio par effet (#198, core/routing.py) : source et dosage de chaque ligne ; par defaut (herite, 1) = comme avant
    *(_p(k, d, lo, hi, choices=c, doc=doc) for k, d, lo, hi, c, doc in routing.route_specs()),
)

assert len({p.key for p in TABLE}) == len(TABLE), "cle en double dans la table des reglages"
assert all(p.scope in SCOPES for p in TABLE)

BY_KEY: dict[str, Param] = {p.key: p for p in TABLE}
# Reglages qui repartent de leur defaut a chaque lancement (ni gl_params.json ni la reprise apres arret brutal ne les ramenent) :
# l'enregistrement est toujours en vertical 1080x1920 (TikTok, Reels) ; un autre format se choisit pour la session.
LAUNCH_DEFAULT_KEYS = ("rec_format",)


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
