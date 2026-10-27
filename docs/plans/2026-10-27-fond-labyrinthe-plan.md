# Fond genere labyrinthe (reaction-diffusion) : plan d'implementation

> **Pour les agents :** sous-skill requis : superpowers:subagent-driven-development (recommande) ou superpowers:executing-plans, tache par tache. Les etapes sont des cases a cocher (`- [ ]`).

**But :** ajouter la palette `labyrinthe` au fond genere : une simulation Gray-Scott sur le GPU, rendue en trois couches colorees decalees, modulee par le son.

**Architecture :** la logique pure (parametres du modele, taille de grille, germes, pas par image) va dans `core/reaction.py`. La simulation (textures ping-pong, shader `rd_step.frag`, initialisation, garde-fou) va dans une classe `engine/reaction.py:ReactionDiffusion` possedee par le `Renderer`, qui ne fait que la piloter et lier sa texture a `scene.frag`. La GUI, le Randomizer et la securite GPU suivent le modele de la palette stochastique.

**Technologies :** Python 3.12, moderngl / GLSL 330, numpy (initialisation, tests), Tk.

**Spec :** `docs/plans/2026-10-27-fond-labyrinthe-design.md`.

**Prerequis :** la PR de la palette stochastique (branche `feat/fond-stochastique`) est fusionnee. Ce plan s'appuie sur `sto_box`, les grisages par listes de palettes, `core.safety.background_cost` et le choix `stochastique` de `bg_palette`.

## Contraintes globales

- Documentation, commentaires et messages en francais **sans accents** ; fichiers CRLF (editer en gardant la fin de ligne).
- `core/` stdlib seulement (`check_core_purity`) ; numpy et moderngl seulement dans `engine/`.
- Un reglage = une ligne de `core/params_schema.py` ; un curseur de la GUI doit tenir dans la plage de la table.
- Les autres palettes restent identiques pixel pour pixel (`check_render`, `check_bg_stochastic`).
- Hachage sans `sin` dans le nouveau GLSL.
- Worktree depuis `origin/main` : `git worktree add ..\casual-overlay-rd -b feat/fond-labyrinthe origin/main`. Python : `..\casual-overlay\.venv\Scripts\python.exe` (note `PY`).
- Lancer une verification seule : `PY -c "import sys; sys.argv=['x']; sys.path[:0]=['tools','.']; import check_gl as t; t.NOM(); print('ECHECS', t.failures)"` (attendu `ECHECS []`).
- `Renderer.draw` plafonne `dt` a 0,1 s : un test qui fait avancer le temps enchaine des images de 0,05 s.

## Points a surveiller en revue

1. **Passer de stochastique a labyrinthe puis revenir** : la simulation repart d'une graine propre a chaque activation, et l'autre palette est intacte. Teste en tache 3.
2. **Fenetre redimensionnee pendant la simulation** : l'etat ne depend pas de la taille de la fenetre (grille fixe), donc rien n'est perdu. Teste en tache 3 (meme etat apres un rendu a une autre taille).
3. **Taille du motif changee a chaud** : l'etat est recopie, pas reinitialise ; le motif reste vivant. Teste en tache 2.
4. **Musique tres chargee en basses pendant longtemps** (forme effective bloquee a 1) : le motif ne meurt pas et n'envahit pas tout (part de V > 0,25 entre 5 % et 70 %). Teste en tache 2 (forme 1, 2000 pas).
5. **Projection fermee puis rouverte** : nouveau `Renderer`, nouvelle simulation semee, aucune ressource GL perimee. Teste en tache 3 (deux `Renderer` dans le meme contexte, images identiques a graine egale).

---

### Tache 1 : logique pure, reglages, cout GPU

**Fichiers :**
- Creer : `core/reaction.py`
- Modifier : `core/params_schema.py` (choix `labyrinthe` de `bg_palette` ; sept cles apres les `bg_sto_*`) ; `core/safety.py` (`BG_RD_COST_MS`, branche `labyrinthe` de `background_cost`, `bg_rd_*` inutiles a `BG_KEYS`) ; `tools/check_gl.py` (nouvelle `check_reaction_pure()`, ajoutee a `PURES` et a `main`, et ajout dans `check_safeguards`)

**Interfaces :**
- Produit (`core/reaction.py`) :
  - `SPOTS = (0.0367, 0.0649)`, `MAZE = (0.0290, 0.0570)`, `BASE_GRID = (384, 216)`, `STEPS_PER_S = 600.0`, `MAX_STEPS = 24`, `SEED_COUNT = 40`, `SEED_RADIUS = 3.0`, `KICK_SEEDS = 3`, `DEAD_LEVEL = 0.002`, `WATCH_S = 2.0`.
  - `feed_kill(shape: float) -> tuple[float, float]` : interpolation lineaire SPOTS -> MAZE, `shape` borne a 0..1.
  - `grid_size(scale: float) -> tuple[int, int]` : `(round(384 / scale), round(216 / scale))`, `scale` borne a 0,5..2.
  - `initial_seeds(seed: int, count: int = SEED_COUNT) -> list[tuple[float, float]]` : positions normalisees 0..1, `random.Random(seed)`.
  - `kick_seeds(kick_index: int, count: int = KICK_SEEDS) -> list[tuple[float, float]]` : positions 0..1 deterministes (`random.Random(kick_index * 7919 + 17)`).
  - `steps_for(acc: float, dt: float, speed: float, energy: float) -> tuple[int, float]` : `acc += dt * STEPS_PER_S * max(speed, 0) * (1 + max(energy, 0))` ; `n = min(floor(acc), MAX_STEPS)` ; rend `(n, acc - floor(acc))` (l'exces au-dela du plafond est jete : pas de retard qui s'accumule).
- Produit (schema) : `bg_rd_shape` 0,6 (0..1), `bg_rd_scale` 1,0 (0,5..2), `bg_rd_offset` 0,012 (0..0,05), `bg_rd_color1` `#ff4a2a`, `bg_rd_color2` `#2f6bff`, `bg_rd_color3` `#19d6a5`, `bg_rd_ground` `#120d14`, tous `scope="bg"`.
- Produit (safety) : `BG_RD_COST_MS = 1.0` (provisoire, remesure en tache 6) ; `background_cost` rend `BG_RD_COST_MS * scale` pour `bg_mode == "pattern"` et `bg_palette == "labyrinthe"`.

- [ ] **Etape 1 : tests qui echouent** dans `check_reaction_pure()` :
  - `feed_kill(0) == SPOTS`, `feed_kill(1) == MAZE`, `feed_kill(-3) == SPOTS`, `feed_kill(0.5)` = le milieu (a 1e-12 pres) ;
  - `grid_size(1) == (384, 216)`, `grid_size(2) == (192, 108)`, `grid_size(9) == (192, 108)`, `grid_size(0.5) == (768, 432)` ;
  - `initial_seeds(1) == initial_seeds(1)`, `!= initial_seeds(2)`, 40 points, tous dans [0, 1) ;
  - `kick_seeds(5)` : 3 points dans [0, 1), deterministes, differents de `kick_seeds(6)` ;
  - `steps_for(0, 1/60, 1, 0) == (10, ~0)` ; `steps_for(0.5, 0, 1, 0) == (0, 0.5)` ; `steps_for(0, 0.1, 4, 1)[0] == 24` et le reste < 1 ; vitesse 0 = 0 pas.
  - Dans `check_params_schema` : defauts et plages des sept cles, toutes dans `gl.BG_KEYS`, `coerce_params({"bg_palette": "labyrinthe"})` accepte, scene capturee puis rejouee (meme modele que le test stochastique).
  - Dans `check_safeguards` : `background_cost` d'un fond labyrinthe > 0, nul en `bg_mode="live"`.
- [ ] **Etape 2 : verifier l'echec** : `t.check_reaction_pure()` -> `ModuleNotFoundError` / echecs.
- [ ] **Etape 3 : implementer** `core/reaction.py`, les lignes du schema, la constante et la branche de `background_cost`.
- [ ] **Etape 4 : verifier** : les trois verifications -> `ECHECS []` ; `PY tools/check_gl.py --pur` -> « Toutes les verifications pures passent. » (`check_core_purity` inclut le nouveau module).
- [ ] **Etape 5 : commit** `Fond labyrinthe : logique pure, reglages, cout GPU`.

### Tache 2 : simulation sur le GPU

**Fichiers :**
- Creer : `engine/reaction.py`, `gl_shaders/rd_step.frag`
- Test : `tools/check_gl.py`, nouvelle `check_reaction_sim()` (apres `check_bg_stochastic`, ajoutee a `main`)

**Interfaces :**
- Consomme : `core.reaction` (tache 1).
- Produit : `class ReactionDiffusion` :
  - `__init__(self, ctx, vbo, shader_dir: Path)` : compile `quad.vert` + `rd_step.frag`, rien d'alloue.
  - `ensure(self, scale: float) -> None` : alloue (ou realloue) deux textures RG `dtype="f2"` (repli `"f4"` si la creation du FBO echoue) a `grid_size(scale)`, `repeat` actif, filtre lineaire ; si une grille existait, recopie l'etat par une passe bilineaire, sinon `reset`.
  - `reset(self, seed: int) -> None` : etat initial ecrit en numpy (U = 1, V = 0, disques de V = 1 de `SEED_RADIUS` cellules aux `initial_seeds(seed)`).
  - `step(self, n: int, shape: float, seeds: list[tuple[float, float]] = ()) -> None` : `n` pas Gray-Scott ; les germes (au plus 4) ne sont appliques qu'au premier pas ; restaure le viewport et le framebuffer actifs.
  - `texture` (propriete) : la texture de l'etat courant (a lier a `scene.frag`).
  - `read_v(self) -> np.ndarray` : V en float32, forme (h, w), haut en premier (tests et garde-fou).
  - `watch(self, now: float) -> bool` : toutes les `WATCH_S` s, lit la moyenne de V (mipmaps, niveau 1 x 1) ; si < `DEAD_LEVEL`, `reset(seed + 1)` et rend True.
  - `seed` (int, 0 au depart, incremente a chaque reensemencement).
- `rd_step.frag` : `uniform sampler2D u_state; uniform vec2 u_texel; uniform vec2 u_fk; uniform vec3 u_seeds[4]; uniform int u_nseeds;` ; laplacien 3 x 3 (centre -1, voisins 0,2, diagonales 0,05), `Du = 1.0`, `Dv = 0.5`, `dt = 1` ; germes : V = 1 dans un disque de rayon `u_seeds[i].z` cellules ; sortie `vec4(clamp(U), clamp(V), 0, 1)`.

- [ ] **Etape 1 : tests** dans `check_reaction_sim()` (contexte standalone, `ReactionDiffusion(ctx, vbo, SHADER_DIR)` avec un `vbo` de quad comme celui du Renderer). Helper de test `components(mask) -> list[set]` (parcours en largeur, voisinage 4, bords en boucle). Mesures sur `V > 0.25` :
  - deterministe : deux simulations, meme graine, 500 pas a la forme 0,6 -> `read_v` identiques ;
  - vivant : formes 0, 0,5 et 1, 2000 pas -> part de V > 0,25 entre 0,05 et 0,70 (point 4 de la revue) ;
  - forme 0, 2000 pas : au moins 30 composantes, la plus grande < 4 x la mediane, et aucune paire de composantes a moins de 2 cellules (distance mesuree par dilatation : dilater chaque composante de 1 cellule ne la fait toucher aucune autre) ;
  - forme 1, 2000 pas : rapport perimetre / aire moyen superieur de 30 % a celui de la forme 0 (lignes allongees) ;
  - `step(0, ...)` ne change rien ;
  - germes : apres 2000 pas, `step(1, shape, [(x, y)])` sur une zone vide (V < 0,05 dans un rayon de 6 cellules) puis 300 pas -> une composante est apparue a cet endroit ;
  - taille changee (`ensure(1.5)` apres 1000 pas) : grille `grid_size(1.5)`, motif toujours vivant 500 pas plus tard, et `seed` inchange (pas de reinitialisation) (point 3 de la revue) ;
  - extinction : etat V = 0 ecrit a la main, `watch(t)` puis `watch(t + 2.1)` -> True, `seed` incremente, V de nouveau non nul.
- [ ] **Etape 2 : verifier l'echec** (`ModuleNotFoundError: engine.reaction`).
- [ ] **Etape 3 : implementer** `engine/reaction.py` et `rd_step.frag` (Interfaces). Si un test « vivant » ou « taches separees » echoue avec SPOTS / MAZE : resserrer la plage dans `core/reaction.py` (valeurs plus proches du milieu), noter la nouvelle plage dans la spec et le registre ; ne pas affaiblir les tests.
- [ ] **Etape 4 : verifier** : `t.check_reaction_sim()` -> `ECHECS []`.
- [ ] **Etape 5 : commit** `Fond labyrinthe : simulation Gray-Scott sur le GPU`.

### Tache 3 : rendu et son

**Fichiers :**
- Modifier : `gl_shaders/scene.frag` (uniforms, `labyrinth_pattern`, aiguillage `u_bg_palette > 3.5` en tete de `background_pattern`) ; `engine/render.py` (`self.rd`, pilotage dans `draw` pres des uniforms du fond)
- Test : `tools/check_gl.py`, nouvelle `check_bg_labyrinthe()` (ajoutee a `main`)

**Interfaces :**
- Consomme : `ReactionDiffusion` (tache 2), `core.reaction` (tache 1), `bg_au` (basses, aigus, kick, depuis), `react`, `has_kick(src_bg)`.
- Produit (Renderer) : `self.rd: ReactionDiffusion | None` (cree au premier besoin), `self._rd_acc: float`, `self._rd_beats: int | None`, `self._rd_active: bool` (palette active a l'image precedente). Dans `draw`, seulement si `bg_mode == "pattern"` et `bg_palette == "labyrinthe"` :
  - activation (pas active a l'image d'avant) -> `rd.ensure(scale)` puis `rd.reset(rd.seed)` ;
  - forme effective `clamp(bg_rd_shape + 0.4 * react * bass, 0, 1)` ; `n, self._rd_acc = steps_for(self._rd_acc, dt, bg_speed, react * bass)` (`dt` deja multiplie par le tempo) ;
  - germes : si `react > 0` et `has_kick(src_bg)`, un `kick_seeds(k)` par nouveau kick `k` depuis `self._rd_beats` (au plus 4 germes) ;
  - `rd.step(n, shape, germes)`, `rd.watch(t)`, `rd.texture.use(6)`.
- Produit (GLSL) : `u_bg_palette = 4.0` pour `labyrinthe` ; `uniform sampler2D u_rd_tex;` (unite 6), `uniform vec4 u_rd;` = (ecart effectif en fraction de la hauteur, demi-texel de la simulation, 0, 0) ; `uniform vec3 u_rd_c1, u_rd_c2, u_rd_c3, u_rd_ground;`. Ecart effectif = `bg_rd_offset * (1 + 1.5 * react * kick)`. `labyrinth_pattern(p)` : trois lectures aux decalages a 0, 120 et 240 degres (corriges du ratio), masques `smoothstep(0.25 - w, 0.25 + w, V)`, couches 1, 2, 3 posees dans l'ordre sur le fond, puis `hue_rotate(u_bg_hue)` et `clamp`. Hors de cette palette, `u_rd_tex` reste lie a une texture valide (celle de la video) pour ne pas laisser une unite vide.

- [ ] **Etape 1 : tests** dans `check_bg_labyrinthe()` (modele de `check_bg_stochastic` : `render`, `lit`, etat `quiet`, `base` = fond genere, palette `labyrinthe`, `bg_react=0`, couleurs 1 / 2 / 3 = rouge, vert, bleu purs, fond noir, `auto_master=0`) :
  - image non vide (au moins deux des trois couleurs presentes) et deterministe (deux `Renderer` neufs, memes images) (point 5 de la revue) ;
  - `bg_rd_offset=0` : les trois couches confondues (aucun pixel ou une seule des trois couleurs domine) ; `0.03` : pixels d'une seule couleur presents ;
  - `bg_hue=1/3` : les couleurs tournent ;
  - 640 x 360 contre 1280 x 720 (reduit 2 x 2) : ecart moyen < 6 niveaux ;
  - tempo 0 : 20 images de 0,05 s -> image identique ; vitesse 1 : image qui change ;
  - son (`bg_react=1`) : basses fortes pendant 60 images -> rapport perimetre / aire de la couche 1 plus grand qu'au silence ; kicks (`beats` 0 -> 3) -> plus de composantes que sans kick apres 40 images ; enveloppe du kick (`beat=1`) -> l'ecart des couches change l'image ; `bg_react=0` + etat fort = image identique au silence ;
  - palette stochastique -> labyrinthe -> stochastique dans le meme `Renderer` : la seconde image stochastique est identique a la premiere, et le labyrinthe repart de la graine (identique a un `Renderer` neuf apres le meme nombre d'images) (point 1) ;
  - le meme `Renderer` rendu a 640 x 360 puis a 960 x 540 : `rd.read_v()` garde sa forme (point 2) ;
  - `check_render` et `check_bg_stochastic` restent verts.
- [ ] **Etape 2 : verifier l'echec**.
- [ ] **Etape 3 : implementer** (Interfaces).
- [ ] **Etape 4 : verifier** : `t.check_bg_labyrinthe(); t.check_bg_stochastic(); t.check_render()` -> `ECHECS []`. Sauver une image de 960 x 540 avec les couleurs par defaut dans le scratchpad et la regarder (ressemblance avec le pull, aucun artefact de grille).
- [ ] **Etape 5 : commit** `Fond labyrinthe : rendu en trois couches et reaction au son`.

### Tache 4 : GUI et grisages

**Fichiers :**
- Modifier : `gui/gui_fond.py` (5e choix de palette, `rd_box`) ; `gui/gl_gui.py` (re-liaison de `rd_box`, cle `"rd_box"` dans le dictionnaire des tests)
- Test : etape GUI de `tools/check_gl.py` (boucle `g_pal` / `sto_shown`, verification pres de celle de la palette stochastique)

**Interfaces :**
- Consomme : `param_radio`, `param_slider`, `color_row`, `Zone`, `palette_var` (deja rendu par `param_radio` dans `gui_fond`).
- Produit : `rd_box` (`tk.Frame` dans `pattern_box`, visible seulement pour `labyrinthe`, `grid()` / `grid_remove()` sur la trace de `palette_var`) avec : curseurs « Forme » (`bg_rd_shape`, 0..1, 0,05), « Taille du motif » (`bg_rd_scale`, 0,5..2, 0,05), « Ecart des couches » (`bg_rd_offset`, 0..0,05, 0,002), et quatre `color_row` : « Couche 1 », « Couche 2 », « Couche 3 », « Fond ».

- [ ] **Etape 1 : tests** : la boucle des palettes parcourt aussi `labyrinthe` ; `out["rd_shown"]` = `rd_box.winfo_ismapped()` par palette -> vrai seulement pour `labyrinthe`, et `sto_box` jamais visible avec elle ; pour `labyrinthe` : « Teinte », « Vitesse » et « Reaction a l'audio » actifs, « Couleurs 1 / 2 », « Angle du degrade » et les trois reglages du damier gris ; bouger le curseur dont `to` vaut 2,0 dans `rd_box` ecrit `params["bg_rd_scale"]`.
- [ ] **Etape 2 : verifier l'echec** (suite complete : l'etape GUI n'est pas autonome).
- [ ] **Etape 3 : implementer** (Interfaces). Les regles de `gates` existantes grisent deja ces lignes hors de leurs palettes : verifier, ne rien ajouter si le test passe.
- [ ] **Etape 4 : verifier** : suite complete -> code 0 (ou 3 avec seulement des `FRAGILES`, relancer une fois) ; fenetre toujours sous 700 px.
- [ ] **Etape 5 : commit** `Fond labyrinthe : bloc de reglages`.

### Tache 5 : Randomizer

**Fichiers :**
- Modifier : `core/randomizer.py` (`random_background_params`)
- Test : `check_randomizer` dans `tools/check_gl.py`

**Interfaces :**
- Produit : palette tiree dans `("classic", "duo", "duo", "stochastique", "labyrinthe")` ; pour `labyrinthe` seulement : `bg_rd_shape` 0,2..0,9, `bg_rd_scale` 0,7..1,5, `bg_rd_offset` 0..0,03, trois couleurs de teintes espacees d'au moins 0,2 (saturation 0,6..1, valeur 0,8..1) et `bg_rd_ground` sombre (valeur 0,03..0,12).

- [ ] **Etape 1 : test qui echoue** : sur 300 graines, plus de 30 tirages `labyrinthe`, valeurs dans les plages, couleurs `#rrggbb`, aucune valeur ramenee par `coerce_params`, aucune cle `bg_rd_*` pour les autres palettes ; la liste des palettes admises du test existant inclut `labyrinthe`.
- [ ] **Etape 2 : verifier l'echec** : `t.check_randomizer()`.
- [ ] **Etape 3 : implementer**.
- [ ] **Etape 4 : verifier** : `t.check_randomizer(); t.check_params_schema()` -> `ECHECS []` ; `--pur` -> code 0.
- [ ] **Etape 5 : commit** `Fond labyrinthe : tire par le Full Randomizer`.

### Tache 6 : mesure, documentation, PR

- [ ] **Etape 1 : mesure GPU** avec le script jetable du scratchpad (`mesure_sto.py`, palette `labyrinthe`), fenetre 1280 x 720, contre `duo`. Ramener le surcout a 1080p (x 2,25 pour la partie au pixel ; la simulation a grille fixe ne change pas) et le mettre dans `BG_RD_COST_MS`. Ajuster le test de `check_safeguards` s'il depend de la valeur.
- [ ] **Etape 2 : documentation** : paragraphe « Palette `labyrinthe` » dans `CLAUDE.md` (sous la palette stochastique : modele, grille fixe, germes, garde-fou, son, cout, tests) ; une phrase dans `README.md` ; mettre a jour la plage `(F, K)` dans la spec si la tache 2 l'a resserree.
- [ ] **Etape 3 : verification finale** : `--pur`, puis la suite complete -> code 0 (ou 3 + relance).
- [ ] **Etape 4 : revue de branche** (un relecteur neuf), correctifs, puis commit, push, PR, CI (pur, complet, portable), fusion, `git pull --ff-only origin main` dans le depot principal, retrait du worktree et de la branche.
- [ ] **Etape 5 : demander a l'utilisateur de juger le rendu** (ressemblance avec le pull, vitesse d'evolution) ; ajustements de `STEPS_PER_S`, de la plage de forme et de l'ecart dans une PR de suivi.
