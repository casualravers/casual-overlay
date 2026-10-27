# Fond genere « stochastique » (halftone stochastique et points aleatoires) : spec

Date : 2026-10-27. Statut : spec a relire, pas encore planifiee ni developpee.

## But

Ajouter au fond genere (touche B, bloc « Fond genere » de la GUI) une palette **Stochastique** : des points de meme taille dont la densite dessine un champ qui bouge, repartis sans grille visible (esprit bruit bleu), avec en option une repartition aleatoire brute (grain). Le son module le fond. Le logo et tous les effets s'appliquent par-dessus, comme pour les autres palettes.

## Decisions prises avec l'utilisateur

1. **Densite** : un champ genere qui bouge, module par le son (pas de retramage de la video ni de l'image audio2wave).
2. **Caractere des points** : curseur « regularite » (bruit bleu -> aleatoire brut) et curseur « variation de taille » ; defaut = tramage pur (taille constante, regularite maximale).
3. **Champs**, en deux temps : (1) bruit fluide, vagues, degrades geometriques ; (2) metaballes, si le cout GPU mesure le permet.
4. **Son** : les basses, le kick, les aigus et le tempo agissent tous.
5. **Allure** : couleurs libres (dont un mode arc-en-ciel) et inversion points clairs / sombres. Point toujours **rond** ; pas de choix de forme.
6. **Integration** : une **nouvelle valeur de `bg_palette`** (`stochastique`), pas un nouveau `bg_mode`. Le fond herite des presets de fond, scenes, Randomizer et du grisage des curseurs.
7. **Placement des points** : grille jitteree, rang par cellule, tout dans le shader (aucune texture, aucun numpy). Une vraie texture de bruit bleu (void-and-cluster) reste une amelioration possible si le rendu paraissait trop regulier.

## Reglages (`core/params_schema.py`, portee `bg`)

`bg_palette` : choix `stochastique` ajoute (`classic`, `duo`, `test`, `stochastique`).

| Cle | Defaut | Plage / valeurs | Role |
|---|---|---|---|
| `bg_sto_field` | `noise` | `noise`, `waves`, `gradient` (puis `blobs`) | forme du champ |
| `bg_sto_size` | 10 | 3..60 (px pour 720 px de haut) | taille de base des points |
| `bg_sto_regular` | 1 | 0..1 | 1 = bruit bleu, 0 = aleatoire brut |
| `bg_sto_vary` | 0 | 0..1 | la taille suit le champ |
| `bg_sto_density` | 0.5 | 0..1 | niveau moyen du champ (part de points allumes au repos) |
| `bg_sto_invert` | 0 | 0 / 1 | echange points et fond |
| `bg_sto_rainbow` | 0 | 0..1 | la teinte des points suit le champ |

Reutilises sans changement : `bg_color1` (points), `bg_color2` (fond), `bg_hue`, `bg_speed` (derive du champ, avec le master du tempo), `bg_angle` (direction des vagues et du degrade), `bg_react` (quantite d'audio). Tous les `bg_*` sont dans `BG_KEYS` : presets de fond, scenes et `capture_background` les prennent sans autre code ; un ancien fichier se lit avec les valeurs par defaut. Pas d'automation en premiere version.

## Shader (`gl_shaders/scene.frag`, `stochastic_pattern(p)`)

- `u_bg_palette` = 3. Nouveaux uniforms : reglages ci-dessus et niveaux audio du fond (basses, kick, aigus) deja calcules par `Renderer.draw` (`bg_au` : source par couche, retard, lissage). `Renderer._set` ignore les absents.
- Pour un pixel : 3 x 3 cellules voisines (cote = `bg_sto_size`). Par cellule : rang `r` = melange de la suite R2 (`fract(c.x*0.7548776662 + c.y*0.5698402910)`) et d'un hachage sans `sin` (`chash`), pondere par `1 - regular` ; position du point decalee dans la cellule (amplitude ~0,3 a regularite 1, jusqu'a 0,5 a 0 : a regler a l'oeil) ; valeur du champ `f` a la position du point.
- Point allume si `f >= r` ; rayon = taille x (1 + vary x (f - 0,5) x 2), bord anti-crenele (`smoothstep`). Couleur : points `c1` (teinte decalee de `f x rainbow`), fond `c2` ; `invert` les echange. Le tout passe par `hue_rotate` puis le master `bg_master` comme les autres palettes.
- Champs : `noise` = bruit de valeur a 2 octaves qui derive (`u_bg_phase.x`), `waves` = anneaux ou bandes selon `bg_angle`, qui se propagent, `gradient` = lineaire / aller-retour. Le champ ne depend jamais de la resolution (coordonnees normalisees, taille des points en px mis a l'echelle de la hauteur).

## Son

Mis a l'echelle par `bg_react` :

- **basses** : densite et taille augmentent (le fond respire) ;
- **kick** (`u_beat`) : coup de contraste du champ et saut de phase des vagues ;
- **aigus** : une fraction des rangs est renouvelee (scintillement), proportionnelle au niveau ;
- **tempo** : la derive du champ suit le master du tempo (phase integree, comme `_bg_scroll`, donc aucun saut quand on change de vitesse).

## GUI, scenes, Randomizer

- Bloc « Fond genere » (`gui/gui_fond.py`) : choix « Stochastique » parmi les palettes ; rangee de curseurs (taille, regularite, variation, densite, arc-en-ciel), menu du champ et case Inverser, visibles seulement pour cette palette. Plages de curseurs dans celles de la table.
- `gui_gates` : tile, damier et bascule grises pour cette palette ; angle actif seulement pour vagues et degrade.
- Scenes et presets : rien a ajouter. PANIQUE : rien (ne change pas le fond choisi).
- Randomizer : `random_background_params` tire aussi `stochastique` (jamais le banc de test), avec des valeurs dans les plages (le test des 300 tirages le garantit).

## Tests (`tools/check_gl.py`)

- Pur (liste `PURES` si possible) : schema (defauts, plages, enums), curseurs de la GUI dans la plage, tirages du Randomizer.
- Rendu hors ecran : determinisme ; densite croissante avec `bg_sto_density` ; espacement des points plus regulier a regularite 1 qu'a 0 (ecart-type de la distance au plus proche voisin) ; taille variable ; inversion ; chaque entree audio (basses = plus de points, kick = image differente, aigus = rangs renouveles, tempo = derive plus rapide) ; reaction a 0 = image identique sans audio ; aller-retour d'un preset ; aucune des autres palettes modifiee pixel pour pixel.
- GUI : palette choisie, curseurs visibles / grises, scene capturee et rejouee.
- Cout GPU mesure en 720 p et 1080 p (`--synthetic --stats`), reporte dans `CLAUDE.md` ; ajoute a `core/safety.py` seulement s'il est eleve.

## Livraison

1. **PR 1** : palette + champs `noise`, `waves`, `gradient` + tests + `CLAUDE.md` / `README.md`.
2. **PR 2** : champ `blobs` (metaballes), seulement apres mesure du cout et validation du rendu par l'utilisateur.

## Non verifie automatiquement / risques

- L'esthetique reelle (le decalage des points qui cache la grille sans perdre l'allure bruit bleu) et la lisibilite sur le videoprojecteur : a juger a l'oeil.
- Le jitter fixe ne donne qu'une approximation du bruit bleu ; plan B : texture void-and-cluster generee par numpy dans `engine/`.
- Cout du bruit fluide evalue 9 fois par pixel : a mesurer, plan B = evaluer le champ par cellule en basse resolution.
