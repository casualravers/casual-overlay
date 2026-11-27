# Halo : curseurs moins sensibles (#260)

## Intention
Les curseurs du halo sont trop sensibles : l'utilisateur n'en utilise que les 20 % les plus faibles. Mesure sur ses 116 valeurs (presets overlay et
scenes) : vitesse (0..1) = 15 % de course en mediane, 30 % au maximum ; intensite (0..3) 33 % / 70 % ; portee (0,3..3) 37 % / 78 % ; deformation (0..3)
33 % / 92 % ; poussiere (0..3) 28 % / 53 %. Les defauts (1 sur 0..3) sont deja au tiers de la course.

## Decisions (utilisateur)
- Tous les curseurs du halo, avec la recommandation de Claude : plage reduite pour la vitesse, reponse non lineaire pour les autres (aucune valeur
  sauvee n'est touchee).

## Decisions (Claude, a corriger si besoin)
- **Vitesse** (`holo_speed`) : plage **0..0,5** (table, curseur, automation `AUTOMATION_SPECS`). Aucune valeur sauvee n'est coupee (max 0,30 dans les presets
  et scenes) ; la courbe d'automation par defaut (0,05..0,4) y tient ; le defaut 0,15 passe a 30 % de la course.
- **Intensite, portee, deformation, poussiere et irregularite** : plage inchangee (les valeurs sauvees, jusqu'a 2,75, restent valides), mais le curseur a
  une **reponse en puissance 2** : valeur = `bas + (haut - bas) x position^2` (position 0..1 du curseur). Les valeurs d'usage (jusqu'a 1,5 sur 0..3) occupent
  alors les deux tiers de la course au lieu de la moitie, le debut est 2 a 3 fois plus fin. Arrondi a 0,01.
- **Laisses lineaires** : la vitesse des irregularites (0..2, defaut 1 : au milieu, #256), la « Reaction audio » generale (0..2, defaut 0) et les dosages des
  pastilles ; faute d'usage mesure qui le justifie. A corriger si on les veut aussi.
- **Mecanisme** (GUI seulement, `gui/gl_gui.py` : `compact_scale` / `add_slider` / `param_slider` recoivent `curve`, 1 = lineaire comme avant) : le `Scale` Tk
  manipule une position 0..1 ; la variable de valeur reste celle qui est liee au reglage (`bind_param`), donc automation, scenes, presets, Randomizer et
  telecommande n'y voient rien. Synchronisation dans les deux sens (curseur -> valeur arrondie ; valeur changee ailleurs -> position), avec un drapeau
  contre la reentrance (pas de derive d'arrondi). La valeur affichee a cote du curseur reste la valeur du reglage.
- **Pur** : les conversions position <-> valeur sont dans `core/response.py` (stdlib), testables sans fenetre : `to_value(pos, lo, hi, curve)`,
  `to_pos(value, lo, hi, curve)`, bornees, NaN = bas de plage, `curve` 1 = lineaire a l'identique.
- **Automation** : l'editeur de courbes garde un axe lineaire en valeur (`AUTOMATION_SPECS`) ; seule la plage de la vitesse change (0..0,5).
- Rien a migrer : les valeurs sauvees sont des valeurs de reglage.

## Hors perimetre
Reponse non lineaire des autres curseurs de l'appli ; echelle logarithmique ; l'axe de l'editeur de courbes.

## Verifications
Pures (`core/response.py`) : `curve` 1 = identite, extremites exactes, aller-retour `to_pos(to_value(p)) = p`, monotone, valeurs hors plage et NaN bornees,
puissance 2 : le milieu du curseur donne le quart de la plage ; table : `holo_speed` 0..0,5 (defaut 0,15), les autres plages inchangees ; automation :
`holo_speed` 0..0,5 et ses plages par defaut dans la plage ; Randomizer : ses tirages restent dans les plages. GUI (fenetre reelle) : le curseur de
l'intensite suit la valeur du reglage dans les deux sens (valeur posee ailleurs -> position ; position bougee -> valeur arrondie), sans boucle ; un
curseur lineaire (vitesse des irregularites) reste identique. Non verifie (materiel) : le ressenti sur le projecteur (la puissance 2 est un choix a regler a
la main si le debut de course reste trop vif ou trop mou).
