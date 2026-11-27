# Mode IDLE : curseur de reactivite du calme (#255)

## Intention
Le « mode IDLE » = les phases **silence** et **ambiance** de `core/phase.py` (+ le calme proportionnel qui ralentit les animations). Constat de
l'utilisateur : il ne s'enclenche pas assez vite. Causes dans le code : delais fixes (le silence doit durer 2,5 s sous le seuil avant d'etre
candidat, puis la phase est adoptee apres 2,5 s de plus et ne dure jamais moins de 4 s) et seuils absolus (silence 0,002, ambiance 0,04) qui ne
conviennent ni au micro (bruit de salle : le seuil de silence n'est jamais atteint) ni a une carte son (silence numerique : un seuil haut classerait
une musique douce en ambiance). Les phases lisent le niveau BRUT, pas `sensitivity` : la sensibilite du kick n'y change rien (et la monter
ajoute de faux kicks qui gardent le groove plus longtemps).

## Decisions (utilisateur)
- Un curseur « Reactivite du calme » (1 = comportement actuel), utile selon la source (micro ou carte son), en plus de la sensibilite du kick.
- Le lien avec le kick reste tel quel : le kick decide du groove, le calme de l'ambiance, chacun avec son reglage. Pas d'apprentissage automatique
  du bruit de fond dans ce ticket.

## Decisions (Claude, a corriger si besoin)
- **Reglage** `calm_react` (0,25..4, defaut 1), portee `device` + `keep` comme `sensitivity` (depend de la source et de la salle : sauve, hors presets et scenes).
- **Effet** (`core/phase.py`, f = `calm_factor(calm_react)`, borne 0,25..4, NaN = 1) :
  - delais d'entree **en silence et en ambiance** divises par f : `enter_delay(target, f)` (ENTER_S 2,5 s) pour la candidature (`_adopt`) ET la duree sous
    le seuil de silence (`_classify`) ; duree minimale d'une phase (MIN_DWELL_S 4 s) divisee par f quand la phase visee est silence ou ambiance
    (`min_dwell(target, f)`). Les autres phases (groove, montee, cassure, pic) gardent leurs delais.
  - seuils : silence et ambiance (`thresholds(mean, f)`) multiplies par f ; les planchers « calme » et « montee » ne changent pas.
  - f = 1 : tout est identique a aujourd'hui (les verifications existantes de `check_phase` en font foi).
- **Branchement** : la boucle de rendu ecrit `analyzer.phase.calm_react` depuis les reglages a chaque image (comme `sensitivity`).
- **GUI** : un curseur « Reactivite du calme » sous « Sensibilite kick » (onglet des effets), meme largeur ; infobulle : micro = monter, carte son = laisser ou baisser.
- Scenes, presets, sets : rien a migrer (reglage d'appareil).

## Hors perimetre
Apprentissage automatique du bruit de fond, fenetre des kicks (4 s) qui decide aussi du passage groove -> cassure -> ambiance, profils des phases.

## Verifications
Pures (`check_phase`) : `calm_factor` (bornes, NaN), `thresholds` et delais a f = 1 identiques a l'actuel, a f = 2 ; scenario « la musique s'arrete » :
l'ambiance / le silence arrive plus tot a f = 3 qu'a f = 1 et plus tard a f = 0,5 ; bruit de fond de micro (niveau 0,004 : au-dessus du seuil de silence
a f = 1) : jamais de silence a f = 1, silence a f = 3 ; un groove (kicks, 0,10) n'entre jamais en ambiance meme a f = 4. Table : cle, plage, defaut.
GUI : le curseur existe. Non verifie (materiel) : le reglage sur un vrai micro et une vraie carte son.
