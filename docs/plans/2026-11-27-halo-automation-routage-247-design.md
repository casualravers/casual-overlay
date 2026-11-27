# Halo holographique : automation et reaction audio par reglage (#247)

## Intention
Le halo n'a aujourd'hui que cinq curseurs fixes (`holo_intensity`, `holo_reach`, `holo_warp`, `holo_dust`, `holo_speed`) et une seule reaction audio
(`holo_react`, ligne de routage `lg_holo`) qui acceleree l'animation aux basses et gonfle l'ensemble au kick. Demande : **automatiser** chaque reglage
(courbes dans le temps, comme `logo_glow`) et **choisir pour chacun sa source audio** (routage par effet, #198).

## Decisions (utilisateur)
- Une ligne de routage **par reglage** (source et dosage propres), plutot qu'une seule pour tout le halo.

## Decisions (Claude, a corriger si besoin)
- **Automation** : les cinq reglages entrent dans `AUTOMATION_SPECS` (plage = celle du curseur), **coupes par defaut** (rien ne bouge tout seul : un
  look existant reste identique). Le bouton de courbe apparait a cote du curseur (deja automatique pour toute cle de la table).
- **Reaction par reglage** : cinq reglages `holo_react_intensity|reach|warp|dust|speed` (0..2, defaut **0** = pas de reaction, image identique) et
  cinq lignes de routage `lg_holo_intensity|reach|warp|dust|speed` (source `rt_`, dosage `rg_`, comme les autres lignes ; defaut `herite` = les basses
  de la couche logo). Valeur effective = `base x (1 + reaction x niveau x K)`, `niveau` = composante basses de la ligne (`READS` = `(0,)`, une source
  qui ne la remplit pas est completee par `_fit`), `K` = 1 (intensite, deformation, poussiere), 0,5 (portee), 3 (vitesse, comme `holo_react`).
  Bornee a la plage du curseur (sauf la vitesse). Formule pure `core.holo.react_value`.
- **L'existant ne change pas** : `holo_react` / `lg_holo` restent la reaction generale (acceleration et gonflement d'ensemble) ; les nouvelles
  reactions s'ajoutent. Ligne grisee tant que `holo_on` ou sa reaction vaut 0 (`VEC_GATES`).
- **GUI** : sous les curseurs du halo, case « Reactions par reglage » (repliee par defaut) : cinq lignes curseur de reaction + pastille de source.
  Les lignes apparaissent aussi dans l'onglet Routage (zone logo, automatique).
- Scenes, presets, sets : rien a migrer (reglages de la table, portee look ; courbes dans `_automation`).

## Hors perimetre
Reactions du halo a des evenements (grille, phrase) autrement qu'a travers les sources de routage existantes ; randomizer (ne tire pas de reactions).

## Verifications
Pures : `react_value` (0 = identique, monotone, borne, vitesse non bornee), table (cles, plages, defauts a 0), lignes de routage (ids, `READS`, portes),
automation (cles presentes, coupees, plage = curseur). GUI : les cinq curseurs ont leur bouton de courbe, la case replie / deplie les reactions,
les pastilles existent. Rendu : reaction a 0 = image identique ; une reaction > 0 avec un son fort change l'image, avec silence non.
Non verifie (materiel) : le rendu au projecteur avec de la vraie musique.
