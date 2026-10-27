# Fond genere « labyrinthe » (reaction-diffusion, motif de Turing) : spec

Date : 2026-10-27. Statut : spec a relire, pas encore planifiee ni developpee.

## But

Ajouter au fond genere une cinquieme palette, **Labyrinthe** : un motif de reaction-diffusion (motif de Turing, comme une empreinte digitale ou un corail) en trois couches colorees decalees, a la maniere du pull montre par l'utilisateur (lignes sinueuses rouges, bleues et vertes sur fond sombre). Proprietes attendues :

- deux taches ne se touchent jamais : elles se repoussent et gardent un ecart regulier ;
- certaines s'allongent et se soudent en lignes sinueuses ;
- le motif vit et se reorganise en continu.

## Contexte

La palette « stochastique » (tramage de points, `docs/plans/2026-10-27-fond-stochastique-*.md`) ne donne pas ce look : ses points sont independants et se touchent quand la densite monte. Elle est gardee comme palette a part ; Labyrinthe est un ajout, sans rien retirer.

## Decisions prises avec l'utilisateur

1. **Vraie reaction-diffusion simulee sur le GPU** (modele Gray-Scott), pas une imitation par lignes de niveau d'un bruit.
2. **Trois couches colorees decalees** (couleurs reglables), sans hachures dans les creux (option possible plus tard).
3. **Son** : les basses changent la forme (taches -> labyrinthe), le kick seme des germes, la vitesse suit le tempo et l'energie, l'ecart des couches s'ouvre au kick. Tout est multiplie par `bg_react`.
4. **Integration** : une nouvelle valeur de `bg_palette` (`labyrinthe`), comme la palette stochastique.

## Simulation (`engine/render.py`, nouveau shader `gl_shaders/rd_step.frag`)

- **Etat** : deux textures RG16F (ou RG32F si le format 16 bits n'est pas rendable) en ping-pong, `r` = U, `g` = V. **Resolution fixe**, independante de la fenetre et de `--render-size` : `RD_BASE` = 384 x 216 a `bg_rd_scale` = 1, soit `(round(384 / scale), round(216 / scale))` pour une autre echelle (plus petit = motif plus gros a l'ecran). Bords en boucle (`REPEAT`) : pas de bord mort.
- **Pas Gray-Scott** (Karl Sims) : laplacien 3 x 3 (centre -1, voisins directs 0,2, diagonales 0,05), `Du = 1,0`, `Dv = 0,5`, `dt = 1` ; `U += Du*lapU - U*V*V + F*(1 - U)`, `V += Dv*lapV + U*V*V - (F + K)*V`, bornes 0..1.
- **Forme** : `(F, K)` interpole lineairement par `bg_rd_shape` entre **taches** `(0,0367 ; 0,0649)` (0) et **labyrinthe** `(0,0290 ; 0,0570)` (1). Ces deux points sont des valeurs connues du modele. La plage effectivement sure (motif vivant partout entre les deux) est **a valider par les tests** et peut etre resserree.
- **Initialisation** : U = 1, V = 0, plus 40 germes (disques de V = 1 de 3 cellules de rayon) a des positions tirees avec une **graine fixe**. Refaite a chaque creation du Renderer (ouverture de la projection) et quand on passe a cette palette depuis une autre.
- **Pas par image** : accumulateur `Renderer._rd_steps += dt * RD_STEPS_PER_S * bg_speed * (1 + energie)`, avec `RD_STEPS_PER_S` = 600 (a regler a l'oeil). On fait `floor` pas, au plus `RD_MAX_STEPS` = 24 par image, et on garde le reste. Le master du tempo agit deja sur `dt`, donc tempo 0 = motif fige.
- **Echelle changee** : l'etat est recopie dans les nouvelles textures par une passe d'echantillonnage bilineaire, sans repartir de zero.
- **Garde-fou anti-extinction** : toutes les 2 s, mipmaps de V lues au niveau 1 x 1 (4 octets). Si la moyenne de V est inferieure a 0,002, la simulation est ressemee (meme initialisation, graine suivante).
- **Germes du kick** : uniform `u_rd_seeds` (4 x vec3 : position, rayon), applique pendant UN pas apres un kick (V = 1 dans les disques). 3 germes par kick a positions tirees par hachage du numero du kick (deterministe). Uniquement si `bg_react` > 0 et que la source audio du fond a des kicks.
- Rien n'est simule quand la palette n'est pas `labyrinthe` ou que le fond n'est pas le fond genere : aucun cout.

## Rendu (`gl_shaders/scene.frag`, palette n° 4)

- `labyrinth_pattern(p)` lit V trois fois (texture `u_rd_tex`, lineaire) aux positions `p + o_i`. Les decalages `o_i` sont trois directions a 120 degres de longueur `bg_rd_offset` (fraction de la hauteur), plus l'ouverture au kick.
- Chaque lecture donne un masque `smoothstep(0,25 - w, 0,25 + w, V)`, avec `w` = un demi-texel de la simulation (bord net sans crenelage).
- Composition : fond `bg_rd_ground`, puis couche 1, 2 et 3 posees dans cet ordre (la derniere par-dessus) dans leurs couleurs. Puis `hue_rotate(bg_hue)` et `clamp`, comme les autres palettes.
- La texture est echantillonnee en coordonnees normalisees : le dessin ne depend pas de la taille de la fenetre (la simulation a sa propre resolution).

## Reglages (`core/params_schema.py`, portee `bg`)

`bg_palette` : choix `labyrinthe` ajoute.

| Cle | Defaut | Plage | Role |
|---|---|---|---|
| `bg_rd_shape` | 0,6 | 0..1 | 0 = taches, 1 = labyrinthe |
| `bg_rd_scale` | 1,0 | 0,5..2 | taille du motif (grille de simulation) |
| `bg_rd_offset` | 0,012 | 0..0,05 | ecart entre les trois couches (fraction de la hauteur) |
| `bg_rd_color1` | `#ff4a2a` | couleur | couche 1 (rouge) |
| `bg_rd_color2` | `#2f6bff` | couleur | couche 2 (bleu) |
| `bg_rd_color3` | `#19d6a5` | couleur | couche 3 (vert) |
| `bg_rd_ground` | `#120d14` | couleur | fond |

Reutilises : `bg_speed` (vitesse d'evolution), `bg_hue`, `bg_react`. Pas d'automation en premiere version.

## Son (x `bg_react`, niveaux du fond `bg_au` : source par couche, retard et lissage)

- **A, forme** : forme effective = `clamp(bg_rd_shape + 0,4 x basses x reaction, 0, 1)`. Elle reste dans la plage sure, donc le motif ne meurt pas.
- **B, kick** : 3 germes par nouveau kick (voir Simulation).
- **C, vitesse** : `energie` = basses x reaction dans l'accumulateur de pas.
- **D, ecart** : ecart effectif = `bg_rd_offset x (1 + 1,5 x kick x reaction)`, ou kick est l'enveloppe 1 -> 0 (`u_beat`).

## GUI, Randomizer, securite GPU

- **Bloc « Fond genere »** : 5e choix de palette, « Labyrinthe » (info-bulle). Un bloc `rd_box` n'apparait que pour cette palette (meme mecanisme que `sto_box`) : curseurs Forme, Taille du motif, Ecart des couches, et les quatre couleurs (`color_row`).
- **Grisages** : comme pour la palette stochastique, on grise Couleurs 1 / 2, Angle et les trois reglages du damier ; Teinte, Vitesse et Reaction restent actifs.
- **Randomizer** : `random_background_params` tire aussi `labyrinthe` (forme 0,2..0,9, taille 0,7..1,5, ecart 0..0,03, trois couleurs de teintes espacees et fond sombre). Les reglages `bg_rd_*` ne sont tires que pour cette palette.
- **Securite GPU** : le cout mesure (720 p, ramene a 1080 p) devient la constante `core.safety.BG_RD_COST_MS`. `background_cost` le rend pour `bg_mode` = `pattern` et `bg_palette` = `labyrinthe`, comme il le fait deja pour la palette stochastique.

## Tests (`tools/check_gl.py`)

- **Pur** : schema (defauts, plages, `BG_KEYS`), scene capturee puis rejouee, Randomizer (tiree, dans les plages), securite GPU (cout compte).
- **Rendu hors ecran** (`check_bg_labyrinthe`), sur l'etat de la simulation lu en numpy et sur l'image :
  - deterministe a graine fixe ;
  - vivant apres 2000 pas, a la forme 0, a 0,5 et a 1 ;
  - forme 0 : les composantes connexes de V > 0,25 sont nombreuses, de taille reguliere (plus grande < 4 x mediane) et separees (aucune paire de composantes a moins de 2 cellules) ;
  - forme 1 : composantes peu nombreuses et allongees (rapport perimetre / aire nettement plus grand qu'a la forme 0) ;
  - le kick ajoute des taches dans une zone vide ;
  - les basses rendent la forme effective plus « labyrinthe » ;
  - reaction 0 avec un son fort = image identique au silence ;
  - tempo 0 = etat fige ;
  - les trois couches sont decalees (l'ecart change l'image, 0 = les trois couches confondues) ;
  - meme dessin a 640 x 360 et 1280 x 720 ;
  - extinction forcee (V = 0 partout) = ressemee au garde-fou suivant ;
  - aucune autre palette modifiee.
- **GUI** : `rd_box` visible seulement pour la palette, grisages, curseurs qui ecrivent les reglages.

## Livraison

Une PR : palette, simulation, rendu, son, GUI, Randomizer, securite GPU, tests, `CLAUDE.md` / `README.md`. Puis l'utilisateur juge le rendu a l'oeil (ressemblance avec le pull, vitesse d'evolution) ; les valeurs de depart (`RD_STEPS_PER_S`, plage de forme, ecart) se reglent apres ce retour.

## Non verifie automatiquement / risques

- La ressemblance avec le pull et la vitesse qui plait : jugement a l'oeil.
- La plage `(F, K)` sure peut etre plus etroite que prevu (zones ou le motif meurt ou envahit tout) : le test « vivant » a trois formes le detecte, le garde-fou ressemant protege en soiree.
- Format RG16F rendable : verifie a la creation, repli RG32F. Le serveur de CI (Mesa llvmpipe) doit le supporter ; sinon le test le dira.
- Etat de simulation par Renderer : il repart de zero a chaque ouverture de la projection (comme la mosaique de doodles).
