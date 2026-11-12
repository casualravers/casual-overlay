# Labyrinthe : zones de formes, symetries de colonisation, automations (issue #102) : spec

Date : 2026-11-01. Statut : spec a relire, pas encore planifiee ni developpee. Suite de `2026-10-27-fond-labyrinthe-design.md` (palette existante, inchangee par defaut).

## But

Rendre la palette **Labyrinthe** (reaction-diffusion Gray-Scott) plus riche, sans casser ce qui existe :

1. **Points + lignes + taches dans la meme image**, et des **lignes qui s'agglomerent en taches** au fil du temps.
2. **Symetries de colonisation** : l'espace est colonise de facon symetrique (d'ou le motif part, comment il se repartit) **sans que les motifs eux-memes soient strictement symetriques**.
3. **Automations** sur les reglages de la palette.

Vocabulaire : **points** = petites taches separees (la forme « taches » actuelle, `bg_rd_shape` = 0) ; **lignes** = le labyrinthe actuel (`bg_rd_shape` = 1) ; **taches** = grosses plages pleines (nouveau regime, « blob »).

## Decisions prises avec l'utilisateur

- Approche **A + C** : carte de forme spatiale qui derive (A) + seuil different par couche (C). Pas de simulation par couche (B), gardee pour plus tard si A + C ne suffisent pas.
- Symetrie sur les **germes ET les zones**.
- Automations **actives par defaut**.
- Aucun changement tant que les nouveaux reglages sont a leur valeur neutre.

## 1. Carte de forme spatiale (`gl_shaders/rd_step.frag`, `core/reaction.py`)

- Le shader recoit aujourd'hui un seul `u_fk` pour toute la grille. Il calcule a la place (F, K) **par texel** : `s_zone = clamp(base + mix x bruit, 0, 2)` ou `base` = la forme actuelle (`bg_rd_shape` + 0,4 x basses x reaction, bornee a 0..1, **inchangee**) et `bruit` un bruit de valeur lent dans [-1, 1].
- L'axe de forme passe de 0..1 a **0..2** par trois points : 0 = points (`SPOTS`), 1 = lignes (`MAZE`), 2 = taches (`BLOB`, nouveau). (F, K) est interpole lineairement entre deux points consecutifs. `BLOB` n'est **pas devine** : il est choisi par un balayage mesure (voir Tests), sur le critere « motif vivant, grosses plages, aucun envahissement total ».
- **Bruit** : bruit de valeur a 2 octaves sur un reseau **periodique** (la grille boucle), a l'echelle d'environ un tiers de l'ecran ; le temps de derive `_rd_zone_t` est **integre image par image** (`dt` deja multiplie par le master du tempo x `bg_rd_drift`), comme les autres phases du projet : tempo 0 = zones figees, changer la vitesse ne fait jamais sauter le motif.
- Avec `bg_rd_mix` = 0, `s_zone` = `base` : l'etat de la simulation est **identique a aujourd'hui** (garde-fou de non-regression).
- Une ligne qui entre dans une zone dont `s_zone` > 1 s'epaissit et s'agglomere : c'est l'effet « lignes -> taches », sans effet special.

## 2. Seuil par couche (`gl_shaders/scene.frag`, `rd_layer`)

- `bg_rd_layers` (0..1) ecarte les seuils des trois couches autour du seuil actuel 0,25 : couche 1 plus haute (traits fins, points), couche 3 plus basse (traits epais, taches) ; couche 2 inchangee. A 0 : identique a aujourd'hui. Decalages et couleurs inchanges.

## 3. Symetries de colonisation (`core/reaction.py`, `engine/reaction.py`, `rd_step.frag`)

Distincte de la symetrie de l'onglet *Compo*, qui reflete l'**image finale** de facon stricte : ici on agit sur la **croissance**.

- Reglages : `bg_rd_sym` (`none`, `mirror_lr`, `mirror_tb`, `quad`, `rot`), `bg_rd_branches` (2..8, pour `rot`), `bg_rd_free` (« Liberte », 0..1).
- **Germes** : `initial_seeds` et `kick_seeds` repetent chaque germe de base en **orbite** autour du centre de l'image, en coordonnees isotropes (format 16:9 pris en compte). Le nombre de germes de base est reduit pour garder ~40 germes au total ; un kick en seme 3 x la taille de l'orbite. La Liberte decale position et rayon de chaque copie (graine deterministe, donc tests reproductibles).
- **Zones** : le bruit de la section 1 est lu aux coordonnees **repliees** par la symetrie choisie (valeur absolue pour un miroir, repli angulaire pour la rotation) ; la Liberte ajoute un second bruit non replie de faible amplitude.
- **Simulation** : une evolution symetrique reste symetrique. Un tres faible bruit asymetrique, proportionnel a la Liberte, est donc ajoute a V a chaque pas (amplitude de l'ordre de 1e-3 x Liberte, a regler par test). Liberte 0 = la symetrie la plus stricte possible, qui finit tout de meme par se defaire (flottants, instabilite du modele).
- **File des germes** : le shader n'applique que 4 germes par pas (`u_seeds[4]`). Les orbites sont mises en **file** (`Renderer`), 4 par pas, sans changer le principe actuel ; au plus un tour de file par image.
- Limites : la grille est rectangulaire et son laplacien n'est pas isotrope : les rotations autres que 4 quadrants sont approximatives (voulu : pas de symetrie stricte).
- Aucune symetrie (`none`) = comportement actuel exact.

## 4. Reglages (`core/params_schema.py`, portee `bg`)

| Cle | Defaut | Plage | Role |
|---|---|---|---|
| `bg_rd_mix` | 0,0 | 0..1 | variete : ecart des zones autour de la forme de base (0 = comme aujourd'hui) |
| `bg_rd_drift` | 0,3 | 0..1 | vitesse de derive des zones |
| `bg_rd_layers` | 0,0 | 0..1 | epaisseur differente par couche (0 = comme aujourd'hui) |
| `bg_rd_sym` | `none` | enum | type de symetrie de colonisation |
| `bg_rd_branches` | 4 | 2..8 | branches de la rotation |
| `bg_rd_free` | 0,5 | 0..1 | liberte : 0 = symetrie la plus stricte, 1 = quasi aucune |

`bg_rd_shape` garde sa plage 0..1 (anciens fichiers intacts). Tous dans `BG_KEYS` (presets de fond et scenes).

## 5. Automations (`core/automation.py`, `AUTOMATION_SPECS`)

Cinq entrees ajoutees (23 -> 28), **actives par defaut**, periodes toutes differentes de celles existantes (79, 53, 37, 29, 23, 47, 43, 31, 13, 19, 21, 11, 17, 19, 41, 7, 61, 59, 67, 71, 83) :

| Cle | Libelle | Plage proposee | Courbe | Periode |
|---|---|---|---|---|
| `bg_rd_shape` | Forme du labyrinthe | 0,3 .. 0,9 | sinus | 73 |
| `bg_rd_offset` | Ecart des couches | 0,004 .. 0,030 | sinus | 89 |
| `bg_rd_mix` | Variete des zones | 0,2 .. 0,9 | triangle | 97 |
| `bg_rd_layers` | Epaisseur des couches | 0,1 .. 0,8 | sinus | 101 |
| `bg_rd_free` | Liberte de la symetrie | 0,2 .. 0,8 | sinus | 103 |

- Les plages sont **resserrees a l'interieur de la zone ou le motif reste vivant** (verifie par test). `bg_rd_drift` n'est pas automatise (la derive est deja un mouvement).
- **Pas d'automation** pour `bg_rd_scale` (le changement d'echelle recopie l'etat de la simulation et ferait sauter l'image), ni pour les couleurs (la teinte globale `bg_hue` l'est deja), ni pour `bg_rd_sym` / `bg_rd_branches` (changement brutal).
- Consequence connue : `merge_automation` complete les anciens fichiers avec les valeurs par defaut, donc **une ancienne scene a palette labyrinthe fait bouger ses nouveaux reglages** des que l'automation tourne : son look ne reste pas identique a l'ancien. Decision de l'utilisateur : acceptee (l'identite exacte n'est garantie que reglages neutres et automations coupees).
- Les automations `bg_*` sont deja rangees avec le fond (presets de fond, scenes) : rien a changer.

## 6. Son, GUI, Randomizer, securite GPU

- **Son** : inchange (basses, kick, energie, ecart au kick), applique a `base` ; les zones s'y ajoutent.
- **GUI** : le bloc `rd_box` gagne les curseurs Variete, Derive, Epaisseur des couches, Liberte, un menu Symetrie et le curseur Branches (grise hors rotation). Memes grisages que la palette. Les cases `~` d'automation viennent par le mecanisme existant.
- **Randomizer** : `random_background_params` tire les nouveaux reglages dans leurs plages (symetrie tiree une fois sur trois, jamais `rot` avec Liberte < 0,2 ; reste dans `MAX_COSTLY`).
- **Securite GPU** : le bruit par texel s'ajoute aux pas de simulation (83 000 texels x jusqu'a 24 pas). Cout a **mesurer** ; `core.safety.BG_RD_COST_MS` (1,0 aujourd'hui) est mis a jour si besoin.

## 7. Tests (`tools/check_gl.py`)

- **Pur** (`PURES`) : schema (defauts, plages, `BG_KEYS`, enum), `AUTOMATION_SPECS` (28 entrees, plages dans celles des curseurs, periodes distinctes), (F, K) par zone (`mix` = 0 : identique ; chemin points -> lignes -> taches monotone et borne), orbites de germes (taille, symetrie exacte a Liberte 0, deterministe, Liberte > 0 decale sans sortir du cadre), file des germes (4 par pas, rien perdu), Randomizer (300 tirages dans les plages).
- **Balayage de `BLOB`** (script de recherche, resultat fige dans la constante) : motif vivant, plages couvrantes, aucun envahissement total, sur un maillage de (F, K).
- **Rendu / simulation** (`check_rd_zones`, `check_rd_symmetry`, extension de `check_bg_labyrinthe`) :
  - `mix` = 0, `layers` = 0, `sym` = `none` : etat et image **identiques** a l'ancienne version apres N pas ;
  - avec la variete, des zones aux caracteres differents coexistent (statistiques de composantes connexes par zone : petites rondes / allongees / grandes) ;
  - le motif reste vivant partout pendant la derive ;
  - une zone qui passe de `s_zone` ~ 1 a ~ 2 voit sa surface moyenne par composante augmenter (agglomeration) ;
  - l'epaisseur des couches est ordonnee (surface couche 3 > couche 1) quand `layers` > 0 ;
  - symetrie : correlation image / son miroir elevee a Liberte faible, **decroissante** avec la Liberte, **jamais 1** ; `none` : pas de correlation particuliere ;
  - tempo 0 = zones figees ; reaction 0 : image identique au silence ; meme dessin a 640 x 360 et 1280 x 720 ;
  - aucune autre palette modifiee.
- **GUI** : curseurs, menu et grisages dans `rd_box`, cases d'automation.

## 8. Livraison

Une PR de code (apres validation de cette spec et un plan dans `docs/plans/`), documentation (`CLAUDE.md`, `README.md`). L'utilisateur juge ensuite le rendu **a l'oeil sur le projecteur** ; les valeurs de depart (echelle des zones, amplitude du bruit de Liberte, plages des automations, `BLOB`) se reglent apres ce retour.

## Risques et inconnues

- Le passage d'un regime a l'autre peut tuer le motif par endroits : balayage, plages resserrees, garde-fou anti-extinction existant.
- Le format RG16F : la simulation passe deja en RG32F si besoin (piege du demi-flottant, voir la spec d'origine) ; un bruit d'amplitude 1e-3 serait perdu en demi-flottant, donc la Liberte exige RG32F (a verifier au demarrage, avec repli : Liberte sans effet et message dans le statut).
- Rotations a N branches sur une grille rectangulaire : approximatives.
- Liens : `#104` (physique derriere le fond) et `#101` (nouveaux fonds generes) touchent le meme domaine ; rien n'est repris ici.
