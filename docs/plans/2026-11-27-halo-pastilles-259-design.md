# Halo : la pastille de routage a droite de chaque reglage (#259)

## Intention
Partout dans l'appli, la source audio d'un reglage est une pastille a DROITE de son curseur (effets, contour lumineux, pulsation...). Le halo s'en
ecartait depuis #247 : un bloc replie « Reactions par reglage » (case a cocher) repetait chaque reglage en curseur « X au son » + pastille. Demande :
la meme chose que d'habitude, une pastille a droite de chaque curseur du halo, sans case ni bloc.

## Constat (code)
- Le menu d'une pastille a deja un « Dosage » (0 a 200 %) qui multiplie le meme niveau que le curseur `holo_react_<nom>` (0..2) : les deux sont
  redondants. La quantite de reaction = le dosage de la pastille.
- La pastille d'une ligne est grisee tant que son reglage de reaction vaut 0 (`VEC_GATES`) : posee telle quelle, elle serait grisee par defaut.
- Les autres pastilles sont par defaut « Comme la couche » (la ligne reagit d'office, comme avant) ; le halo ne doit rien changer sans demande :
  defaut = **Rien**.

## Decisions (utilisateur)
- Une pastille a droite de chaque reglage du halo, sans case a cocher ni bloc replie.

## Decisions (Claude, a corriger si besoin)
- **Defaut « Rien »** pour les lignes du halo (`Route.default`, nouveau champ : `herite` pour toutes les autres lignes, `rien` pour `lg_holo_*`) :
  aucune reaction tant qu'une source n'est pas choisie, rendu actuel inchange. La quantite = le dosage de la pastille (0..200 %, defaut 100 %).
  Ou la valeur par defaut d'une ligne compte : `route_specs` (defaut de `rt_<id>`), `route_of` (cle absente ou source inconnue), `is_default`,
  `routed_ids`, `gui_routing` (variables, `reset`, `paint`, texte de la pastille « Rien »).
- **Formule** : `core.holo.react_value(key, base, level)` = `base x (1 + level x K)` (K inchange, borne a la plage du curseur sauf la vitesse) ;
  `level` = composante basses de la ligne, dosage compris. Un curseur `holo_react_<nom>` a 1 d'avant == un dosage de 100 % : meme image.
- **Supprime** : les 6 reglages `holo_react_intensity|reach|warp|dust|speed|wobble`, le bloc replie et ses curseurs. Reste : `holo_react` et sa pastille
  `lg_holo` (la « Reaction audio » generale), inchanges.
- **Pastilles** : a droite de Intensite, Portee, Deformation, Poussiere, Vitesse et Irregularite (`routing.pill_after`). La vitesse des irregularites
  n'en a pas (elle ne reagit pas au son, #256) : a corriger si on la veut.
- **Grisage** : une ligne du halo est grisee quand le halo est coupe ou quand SON reglage vaut 0 (`VEC_GATES` = `("holo_on", "<reglage>")`, comme
  le contour lumineux grise sur `logo_glow`).
- **Migration** : une scene, un preset ou un fichier de reglages qui contient `holo_react_<nom>` > 0 (reglage ajoute le 27/11, #247) est converti :
  source = celle de la ligne si elle etait reglee, sinon « Comme la couche » ; dosage = ancien dosage x ancienne reaction (borne 2). Fait a la lecture,
  dans l'entonnoir existant (`coerce_params` et la lecture des scenes / presets, a localiser a l'implementation) ; le reglage disparu ne reste nulle part.
- Rendu : une ligne par defaut est sautee (aucun calcul, aucune memoire de routage) ; sinon le niveau de la ligne alimente `react_value`.

## Hors perimetre
Pastille pour `holo_wobble_speed` ; fusion de `holo_react` / `lg_holo` ; pastilles de la fonte et des cellules (elles ont leur curseur « Reaction audio »).

## Verifications
Pures : `Route.default` (halo = `rien`, autres = `herite`), `route_of` / `is_default` / `routed_ids` avec defaut par ligne, texte « Rien » et
« Basses 150 % », `react_value(key, base, level)` (identique, formule, borne, NaN), table (plus de `holo_react_<nom>`), grisage, migration (cas :
reaction 0, reaction 1 sans source, reaction 1,5 + source + dosage, valeurs invalides). Rendu : defaut = image identique a avant ; source Basses a 100 %
avec son fort = l'image qu'avait `holo_react_<nom>` = 1 ; dosage 0 = identique ; silence identique. GUI : une pastille a droite de chacun des 6
curseurs, plus de case « Reactions par reglage » ni de curseur « ... au son ». Non verifie (materiel) : le ressenti sur le projecteur avec de la vraie musique.
