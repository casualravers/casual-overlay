# Masque d'expansion de la reaction-diffusion (#178)

Spec a valider dans le chat. Une seule PR : cette spec, son plan et le code. Fiche a lire : [docs/fonctions/fond.md](../fonctions/fond.md) (labyrinthe, zones, `bg_rd_void`).

## Ce que veut l'utilisateur

Piloter un **calque (masque)** qui limite la zone ou la reaction-diffusion du labyrinthe a le droit de vivre : le motif se **revele dans des formes mouvantes**, comme des fenetres, au lieu de couvrir tout le cadre. Decisions de l'issue : role = masque d'expansion (rien ne s'etend dehors) ; bords **doux et reglables** (le motif s'amincit avant de disparaitre) ; source = **formes generees** (cercle, bandes, degrade, bruit) ; parametres : forme, rotation, nombre de zones, inversion, douceur ; **derive automatique** et parametres **automatisables** ; **vignette live** dans l'onglet fond ; enregistre avec scenes, presets de fond et Randomizer ; desactive (defaut) = rendu identique a aujourd'hui.

Reponses du 2026-11-27 aux questions ouvertes :
- **Vide** : le masque et `bg_rd_void` **cohabitent** (le masque limite la zone, le Vide creuse encore des trous dedans ; presets et scenes existants inchanges) ;
- **Position / taille** : curseurs **nombre de zones** et **taille** ; la **position derive toute seule** (disposition periodique qui boucle), pas de curseur X / Y ;
- **Son** : **aucune reaction au son** propre ; les curseurs sont **automatisables** (courbes) ;
- **Bruit** : **le meme bruit** que le Vide (`pnoise` periodique, grandes taches qui derivent), son nombre de zones regle sa finesse ;
- **Germes de depart** (delimiter ou naissent les germes) : **hors de cette version** (extension possible).

## Principe

La palette labyrinthe calcule **une fois par image** une carte (F, K) par cellule (`gl_shaders/rd_zone.frag`) que `rd_step.frag` lit a chaque pas. Le Vide y fait glisser (F, K) vers `VOID_FK` (hors de la limite d'existence du motif : la matiere s'eteint, progressivement sur le bord). Le masque ajoute **la meme chose** : la part `gone_mask = 1 - m` du masque `m` (1 = vit, 0 = eteint, bord progressif) est combinee a celle du Vide : `gone = 1 - (1 - gone_vide) x (1 - gone_masque)` puis `fk = mix(fk, VOID_FK, gone)`. **Aucune passe de plus, aucune lecture de plus dans la simulation** : seul le calcul de la carte, une fois par image, gagne quelques operations. La carte sert aussi aux germes : un germe ne prend que s'il est dans la zone qui vit (sinon il s'eteint tout de suite, comme dans une zone vide).

## Le masque `m(uv, t)` (formules pures dans `core/reaction.py`, repliquees en GLSL)

Repere isotrope centre (ecran), tourne de l'angle, decale par la derive (`t x bg_rd_mask_drift x MASK_SPEED`, dans la meme direction lente que les zones). Une **valeur signee** `f` (positive dedans) par forme, puis `m = smoothstep(-e, e, f)` avec `e` = demi-largeur de bord qui croit avec la douceur ; l'inversion fait `m = 1 - m`.
- **Cercles** : reseau de `nombre` cellules en hauteur (autant en largeur, a l'aspect), un disque par cellule dont la **part de surface = taille** ; `f` = rayon - distance au centre de la cellule, normalise.
- **Bandes** : `nombre` bandes par hauteur, la part de chaque periode dans la zone = **taille** ; `f` = demi-largeur - distance au milieu de la bande.
- **Degrade** : rampe triangulaire de periode `1 / nombre`, `taille` decale le seuil ; avec une douceur de 1 c'est un vrai degrade.
- **Bruit** : le bruit du Vide (`void_noise`) a `nombre` x les cellules de base, seuil selon **taille** (la part de l'image vivante est a peu pres `taille`, comme `VOID_THR`).
- Les coordonnees sont **repliees par la symetrie** de colonisation (`fold`, comme les formes et le Vide) : avec une symetrie le masque la respecte.
- `none` : `m = 1` partout, le chemin du shader n'est pas parcouru (`u_mask_on` = 0).

## Reglages (table `core/params_schema.py`, tous `scope="bg"`, comme `bg_rd_*`)

| Cle | Defaut | Plage | Role |
|---|---|---|---|
| `bg_rd_mask` | `none` | `none` / `circles` / `bands` / `gradient` / `noise` | forme du masque ; `none` = comme avant |
| `bg_rd_mask_count` | 3 | 1 .. 8 | nombre de zones (cellules / bandes / periodes / finesse du bruit) |
| `bg_rd_mask_size` | 0,5 | 0,1 .. 0,9 | part de l'image ou la matiere vit |
| `bg_rd_mask_angle` | 0 | 0 .. 360 | rotation de la disposition |
| `bg_rd_mask_soft` | 0,4 | 0 .. 1 | douceur des bords (0 = net, 1 = tres progressif) |
| `bg_rd_mask_invert` | 0 | 0 / 1 | la matiere vit **hors** de la forme |
| `bg_rd_mask_drift` | 0,4 | 0 .. 2 | vitesse de derive des zones (x la derive des formes) |

**Automatisables** (courbes, comme les autres curseurs) : taille, angle, douceur, derive. Le nombre et la forme ne le sont pas (ils font sauter la disposition). Enregistres avec **scenes, presets de fond** (`BG_KEYS`) et le **Full Randomizer** (labyrinthe : masque tire une fois sur trois, reglages dans leurs plages).

## GUI (`gui/gui_fond.py`, bloc labyrinthe `rd_box`)

Un groupe « Masque d'expansion » : radio de forme, curseurs, case Inverser, et a droite **une vignette 96 x 54 px** qui montre le masque en direct (blanc = la matiere vit, noir = eteinte). La vignette est calculee **cote Python par la meme fonction pure** que le shader (echantillon 48 x 27, agrandi), sur le temps de derive publie par le moteur (`Session.status`) : elle suit les curseurs au fil de l'eau et la derive du projecteur. Curseurs grises quand la forme est `none` (`gui_gates`).

## Cas limites et risques

- **Derive rapide** : une zone qui se deplace laisse derriere elle de la matiere eteinte ; dans la nouvelle zone la matiere doit s'etendre depuis le bord ou etre ensemencee (les kicks et les germes du labyrinthe y pourvoient). La derive est donc lente (borne `MASK_SPEED`), a regler au rendu ; **un masque qui bouge plus vite que le motif ne peut le suivre** (a verifier : la zone peut rester vide tant qu'aucun germe n'y tombe).
- **Masque tres petit** (`taille` 0,1, un seul cercle) : peu de matiere ; pas de plancher force (comportement voulu), mais la derive et les germes la ramenent.
- **Symetrie** : masque replie ; **liberte** : meme bruit non replie que les zones (`u_free x u_free_zone`) pour que la symetrie ne soit pas stricte.
- **Reinitialisation** : changer un reglage du masque ne reensemence pas (comme le Vide).
- **Cout GPU** : a mesurer a 1080 p sur le labyrinthe charge (attendu : < 0,1 ms, la carte n'est calculee qu'une fois par image) ; `core/safety.py` inchange sauf mesure contraire.
- **Fond non labyrinthe** : les reglages sont sans effet (grises).

## Fichiers

`core/reaction.py` (formules pures du masque, constantes), `core/params_schema.py`, `gl_shaders/rd_zone.frag` (+ uniforms), `engine/reaction.py` / `engine/render.py` (uniforms, temps de derive publie), `gui/gui_fond.py` (+ `gl_gui.py` : grisages, rafraichissement de la vignette), `core/automation.py` (specs), `core/randomizer.py`, `docs/fonctions/fond.md`, `tools/check_gl.py`.

## Verifications

- **Pur** : `mask_value` (forme, nombre, taille ~ part vivante, rotation, inversion, douceur, derive periodique, symetrie) ; part vivante proche de `taille` pour chaque forme ; `none` = 1 partout ; automatisables et plages dans la table.
- **Rendu** : masque `none` = image d'origine ; avec un masque, la matiere est **absente hors zone et presente dedans** (V moyen), bords progressifs (pas de marche), inversion bascule ; **la carte GPU concorde avec la fonction pure** (echantillons) ; derive sans saccade ; cohabite avec le Vide ; une scene / un preset de fond enregistre et recharge retrouve le masque ; Randomizer.
- **GUI** : vignette creee, suit un curseur et la derive, grisages.
- **Non verifie (materiel)** : lecture au videoprojecteur, derive en soiree, cout sur la machine du VJ.

## Hors de cette PR

Delimiter les points de depart des germes ; image PNG ou dessin libre comme masque ; position X / Y reglable ; reaction au son ; fusion du Vide dans le masque.
