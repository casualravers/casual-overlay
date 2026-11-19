# Effets cales sur la structure du morceau (issue #164)

Spec validee dans le chat le 2026-11-19 (une seule PR spec + code).

## But

Une meilleure osmose entre le son et le visuel : les effets ne partent plus tous en meme temps, chaque impact a son temps
(1er temps, caisse claire sur 2 et 4, charleys), et les fins de cycles de 8 / 16 / 32 temps sont renforcees.

## Decisions de cadrage (issue)

- Temps 1 des mesures et phrases : automatique + appui ; le bouton « Temps 1 » reste prioritaire.
- Temps de la snare : sur la vraie caisse claire detectee (classifieur kick / caisse claire) ; muet sans caisse claire.
- Effets de fin de cycle : d'abord des renforts des effets existants (plus forts sur les cycles longs, le 32 finit le plus fort),
  les effets dedies dans un second temps (hors de cette PR).
- Structure (couplet / refrain) : a part, liee a #105.

## Conception

1. **Temps 1 auto** (`core/rhythm.py`) : le « 1 » se pose seul sur le premier kick d'une reprise (apres un vide de 1,5 s, des que le BPM
   est connu) et sur le kick qui revient au drop (`PhaseTracker.drop_kick`). Une grille posee a la main n'est jamais deplacee par l'auto.
   Origine affichee a cote du BPM (manuel / auto (drop) / auto (reprise)). Reglage de salle `one_auto` (defaut 1).
2. **Caisse claire** (`engine/audio.py`, `core/audio_layers.py`) : les attaques ecartees du kick par le classifieur deviennent un evenement
   et une source `snare`. Muette sans deux familles nettes.
3. **Fins de cycles** : sources `fin8` (dernier temps de 8), `fin16` (2 derniers de 16), `fin32` (4 derniers de 32), seulement avec une
   grille. Position dans la phrase publiee (`phrase_pos`, 0..31).
4. **Tableau auto** : groove et pic : onde de choc sur le 1, aberration du logo sur la caisse claire (sinon frappes / aigus en groove,
   kick au pic), glitch sur l'air / les frappes.
5. **Renforts** : en groove et au pic (effets auto), glitch et aberration x1,3 (fin de 8), x1,5 (fin de 16), x1,6 a x2 (fin de 32),
   plafonnes a 1. Master `cycle_accent` (0..1, defaut 1).

## Correctif rencontre

`BeatGrid.nudge` deplacait l'ancre d'une mesure a chaque kick : le compte des phrases 8 / 16 / 32 glissait. L'ancre est desormais deplacee
de phrases entieres (32 temps).

## Verification

`check_structure` (pur) : pose du 1 a la reprise et au drop, priorite de l'appui, coupure, vide ; recalage qui garde les phrases ;
fins de cycles et renforts ; source et tableau de la caisse claire ; caisse claire sur un rock synthetique ; evenements de fin de cycle
dans l'analyseur avec et sans grille.

Non verifie (demande la vraie musique et le videoprojecteur) : justesse du 1 auto, caisse claire sur de vrais morceaux, rendu des renforts.
