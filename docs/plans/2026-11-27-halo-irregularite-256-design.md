# Halo holographique : irregularites vivantes sur les anneaux (#256)

## Intention
Les anneaux du halo sont les courbes de niveau d'un champ de distance au sujet (`v`, `gl_shaders/holo.frag`) lues par `post.frag` : la deformation du fond
(`sin(v * 24 - ...)`) et les franges de lumiere (`sin(v * 30 - ...)`). `v` est parfaitement lisse, donc tous les anneaux sont reguliers et se ressemblent :
rendu « dessin a la regle », trop propre, impression de « fake ». Demande : de petites deformations propres a chaque anneau, **vivantes** (elles ondulent
lentement, plus fort au son), avec **deux curseurs** : irregularite et vitesse. Defaut 0 = rendu actuel.

## Decisions (utilisateur)
- Irregularites **vivantes** (pas figees), deux curseurs : irregularite et vitesse.

## Decisions (Claude, a corriger si besoin)
- **Mecanisme** : on perturbe `v` UNE fois, dans `holo.frag` (basse resolution, 1/4 de l'ecran : cout negligeable), avant que le champ soit lu par
  `post.frag`. Deformation et franges restent alignees, la poussiere et l'enveloppe suivent le meme `v` : rien a changer dans `post.frag`.
  `v' = v + A * m(v) * n(pos * F, v * K, t)` : `n` = bruit de valeur 3D (position en hauteurs d'ecran, `v` en 3e axe, temps `t`). Le 3e axe est ce qui donne
  a **chaque anneau sa propre irregularite** (chaque courbe de niveau voit une tranche differente du bruit), sans coupure entre anneaux.
- **Garde-fous** : le masque `m(v)` s'eteint pres du sujet (v -> 1 : le contour du sujet reste fidele, #240) et a la portee (v -> 0 : pas de bord franc).
  `v'` reste borne a 0..1. Amplitude maximale (curseur a 1) : environ un tiers de la periode des anneaux (a regler a l'oeil). Frequence spatiale `F` basse
  (quelques ondulations par anneau, pas de grain).
- **Reglages** : `holo_wobble` (irregularite, 0..1, defaut **0** : les scenes existantes ne changent pas) et `holo_wobble_speed` (vitesse, 0..2, defaut 1 ;
  0 = irregularites figees). Temps propre `t` integre cote CPU (comme `holo_phase`), independant de `holo_speed`.
- **Automation** : les deux reglages entrent dans `AUTOMATION_SPECS`, coupes par defaut (le bouton de courbe est automatique).
- **Reaction au son** : `holo_wobble` rejoint `core.holo.REACT_K` (K = 1, plafond 1,0) avec reglage `holo_react_wobble` (0..2, defaut 0) et ligne de routage
  `lg_holo_wobble` (mecanisme de #247). La vitesse ne reagit pas.
- **GUI** : deux curseurs « Irregularite » et « Vitesse des irregularites » sous les curseurs du halo ; la reaction au son va dans le bloc replie
  « Reactions par reglage » (#247), pastille de source comprise. Curseurs grises quand le halo est coupe.
- Scenes, presets, sets : rien a migrer (reglages de la table). Randomizer : ne tire pas ces reglages.

## Hors perimetre
Deformation du contour du sujet lui-meme, grain fin, irregularite differente par couche (fond / logo).

## Verifications
Pures : table (cles, plages, defauts 0 / 1), automation (cles presentes, coupees), `react_value` avec `holo_wobble`, ligne de routage `lg_holo_wobble`.
Rendu (champ du halo, GL logiciel) : irregularite 0 = champ identique ; > 0 = champ different, `v'` dans 0..1 et inchange contre le sujet ; vitesse 0 =
deux instants differents donnent le meme champ, vitesse > 0 = champs differents ; chaque anneau a une deformation differente (ecart entre deux
anneaux > 0). GUI : les deux curseurs et leur bouton de courbe existent. Cout du champ mesure avant / apres (`PerfGuard` / budget GPU).
Non verifie (materiel) : le rendu au projecteur avec de la vraie musique (amplitude et frequence reglees a l'oeil).
