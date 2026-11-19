# Labyrinthe fluide : plus de saut a la naissance, la soudure et la rupture (issue #189)

Valide dans le chat le 2026-11-19 (chemin court : changement borne d'un flux existant).

## Constat

- Une image fait ~10 pas de simulation ; le seuil d'affichage est quasi dur (V dans 0,25 +/- 0,02). Un pont qui se forme ou se rompt
  franchit le seuil en une image : soudure et rupture « claquent ».
- Un germe de kick etait un disque plein de V = 1 (rayon 3 cellules) pose en un pas : la tache apparait d'un coup.

## Decision

1. **Affichage lisse** (`engine/reaction.py`, `ReactionDiffusion.smooth`) : une copie affichee de l'etat le rattrape a chaque image,
   `affiche = mix(affiche, etat, alpha)`, `alpha = core.reaction.display_alpha(dt, fluidite) = 1 - exp(-dt / (0,25 s x fluidite))`.
   `dt` = temps reel de l'image (pas multiplie par le tempo : un motif fige est rejoint), donc juste a toute cadence, vitesse et en
   enregistrement. La simulation n'est pas touchee.
2. **Germes doux** (`rd_step.frag`, `core.reaction.seed_value`) : profil gaussien de hauteur 0,4, moitie au rayon, coupe a deux rayons.
   La simulation fait grandir la tache ; l'etat initial garde ses disques pleins.
3. **Bord** : `w = max(ancien, 0,7 x fwidth(V))` (jamais plus fin qu'un pixel d'ecran) ; les liseres emboites restent.
4. **Reglage** : curseur « Fluidite » (`bg_rd_smooth`, 0..1, defaut 0,5 = tau 0,125 s ; 0 = affichage brut). Les presets sans la cle prennent
   0,5 (ils deviennent fluides).

## Mesures (check_rd_fluide)

Kick toutes les 20 images, 10 pas par image, aire qui bascule d'un coup dans une fenetre 9 x 9 cellules (81) : 80 avant, ~60 germes doux
seuls, ~17 avec la fluidite 0,5 (6 a 10 a la naissance). Sans kick, l'evolution ne fait basculer que ~7 cellules par image.

Fluidite 0,4 laissait ~33 cellules a la naissance (exponentielle : le coeur du germe franchit le seuil tres vite) ; 0,5 est le coude mesure.

## Verifications

- `check_rd_fluide_pure` (pur) : alpha (0 = sans retard, independant de la cadence, tau, bornes), profil du germe.
- `check_rd_fluide` (GL) : saut local borne, couverture inchangee (pas de flou), motif fige rejoint, redimensionnement, sans lissage = etat.
- Non verifie : le rendu sur le videoprojecteur avec de la vraie musique.
