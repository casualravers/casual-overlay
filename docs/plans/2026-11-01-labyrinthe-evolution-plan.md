# Labyrinthe : zones, symetries, automations - plan d'implementation

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal :** zones de formes (points / lignes / taches) qui derivent, seuil par couche, symetries de colonisation non strictes et automations pour la palette Labyrinthe.

**Architecture :** la logique pure (chemin (F, K) a trois points, orbites des germes, constantes) va dans `core/reaction.py` ; le shader `rd_step.frag` calcule (F, K) par texel a partir d'un bruit lent replie par la symetrie, et ajoute le bruit de Liberte ; `engine/reaction.py` passe ces uniforms et pose les germes en file ; `engine/render.py` integre la derive et colonise les germes ; `scene.frag` ecarte les seuils des couches. Reglages neutres = etat et image identiques a aujourd'hui.

**Tech Stack :** Python 3.12, moderngl, numpy, GLSL 330, Tk (GUI), `tools/check_gl.py` (pas de pytest).

**Spec :** `docs/plans/2026-11-01-labyrinthe-evolution-design.md`

## Global Constraints

- Documentation, commentaires et messages en francais **sans accents** ; fichiers `.py` / `.frag` en ASCII.
- `core/` reste stdlib seulement (`check_core_purity`) ; tout test pur va dans `PURES` de `check_gl.py`.
- Valeurs neutres (`bg_rd_mix` = 0, `bg_rd_layers` = 0, `bg_rd_sym` = `none`) : etat de simulation et image **identiques** a la version actuelle, quelle que soit `bg_rd_free`.
- `bg_rd_shape` garde sa plage 0..1 ; l'axe interne de forme est 0..2 (0 points `SPOTS`, 1 lignes `MAZE`, 2 taches `BLOB`).
- Nouveaux reglages (portee `bg`) : `bg_rd_mix` 0,0 (0..1), `bg_rd_drift` 0,3 (0..1), `bg_rd_layers` 0,0 (0..1), `bg_rd_sym` `none` (`none`, `mirror_lr`, `mirror_tb`, `quad`, `rot`), `bg_rd_branches` 4 (2..8), `bg_rd_free` 0,5 (0..1).
- Automations ajoutees (actives par defaut) : `bg_rd_shape` « Forme du labyrinthe » 0,3..0,9 sinus 73 ; `bg_rd_offset` « Ecart des couches » 0,004..0,030 sinus 89 ; `bg_rd_mix` « Variete des zones » 0,2..0,9 triangle 97 ; `bg_rd_layers` « Epaisseur des couches » 0,1..0,8 sinus 101 ; `bg_rd_free` « Liberte de la symetrie » 0,2..0,8 sinus 103. Pas d'automation pour `bg_rd_scale`, `bg_rd_drift`, `bg_rd_sym`, `bg_rd_branches`, les couleurs.
- Shader : au plus 4 germes par pas (`u_seeds[4]`) ; les orbites passent par une file.
- Le bruit de Liberte exige l'etat en RG32F (`dtype == "f4"`) ; en RG16F il est sans effet.
- Branche de travail : nouvelle branche depuis `origin/main` ; verification de reference `.venv\Scripts\python.exe tools/check_gl.py --pur` puis la suite complete en arriere-plan.

## Review Focus

1. **Ancienne scene ou ancien preset sans les nouvelles cles** : se charge, nouvelles cles a leur defaut, aucune erreur (test en tache 3).
2. **Symetrie changee pendant que le motif tourne** : pas de reinitialisation de la simulation (pas de saut d'image) ; la nouvelle symetrie s'applique aux zones et aux germes suivants (test en tache 5).
3. **Taille du motif changee avec des zones actives** : etat recopie, motif toujours vivant (test en tache 4).
4. **Rafale de kicks en rotation a 8 branches** : la file des germes reste bornee (`MAX_PENDING_SEEDS` = 32, les plus anciens jetes) (test en tache 5).
5. **Tempo 0 ou vitesse 0** : zones figees, germes en attente, etat inchange (test en tache 4).

---

### Task 1 : logique pure des zones et des orbites (`core/reaction.py`)

**Files :**
- Modify : `core/reaction.py`
- Test : `tools/check_gl.py` (`check_reaction_pure`, deja dans `PURES`)

**Interfaces :**
- Produces :
  - `BLOB: tuple[float, float]` (valeur provisoire `(0.0250, 0.0550)`, remplacee en tache 2) ;
  - `SYM_MODES = ("none", "mirror_lr", "mirror_tb", "quad", "rot")` ;
  - `ZONE_CELLS = (5, 3)` (reseau periodique du bruit des zones, en cellules sur la grille), `ZONE_SPEED = 0.08` (cycles de derive par seconde a `bg_rd_drift` = 1), `FREE_NOISE = 1e-3`, `FREE_ZONE = 0.35`, `FREE_JITTER = 0.04` (decalage max d'une copie de germe, fraction de la hauteur), `LAYER_SPREAD = 0.12`, `MAX_PENDING_SEEDS = 32` ;
  - `shape_feed_kill(s: float) -> tuple[float, float]` : chemin lineaire par morceaux 0 -> `SPOTS`, 1 -> `MAZE`, 2 -> `BLOB`, borne a 0..2 ; pour `s` dans 0..1 rend **exactement** `feed_kill(s)` ;
  - `orbit_size(sym: str, branches: int) -> int` : 1, 2, 2, 4, `branches` (borne 2..8) ;
  - `colonize(points, sym: str, branches: int, free: float, aspect: float, salt: int) -> list[tuple[float, float, float]]` : pour `sym` = `none` rend `[(x, y, SEED_RADIUS)]` sans rien changer ; sinon chaque point donne `orbit_size` copies (miroirs autour de 0,5 ; rotation autour de (0,5 ; 0,5) en coordonnees isotropes : x multiplie par `aspect` avant rotation puis divise), chaque copie decalee de au plus `FREE_JITTER x free` (x divise par `aspect`) et de rayon `SEED_RADIUS x (1 + 0,3 x free x u)` avec `u` dans [-1, 1], tires par `random.Random(salt)` ; positions ramenees dans [0, 1) par modulo ;
  - `base_seed_count(sym: str, branches: int) -> int` = `max(1, round(SEED_COUNT / orbit_size(sym, branches)))`.

- [ ] **Step 1 : tests a ajouter a `check_reaction_pure`**

```python
    check(rc.shape_feed_kill(0) == rc.SPOTS and rc.shape_feed_kill(1) == rc.MAZE and rc.shape_feed_kill(2) == rc.BLOB
          and rc.shape_feed_kill(5) == rc.BLOB and rc.shape_feed_kill(-1) == rc.SPOTS
          and all(rc.shape_feed_kill(s) == rc.feed_kill(s) for s in (0.0, 0.25, 0.6, 1.0)),
          "forme 0..2 : points, lignes, taches ; identique a feed_kill sur 0..1")
    mid = rc.shape_feed_kill(1.5)
    check(all(abs(a - (b + c) / 2) < 1e-12 for a, b, c in zip(mid, rc.MAZE, rc.BLOB)), "forme 1,5 : milieu lignes / taches")
    check([rc.orbit_size(s, 6) for s in rc.SYM_MODES] == [1, 2, 2, 4, 6] and rc.orbit_size("rot", 20) == 8 and rc.orbit_size("rot", 1) == 2,
          "taille des orbites, branches bornees 2..8")
    pts = rc.initial_seeds(3, 10)
    check(rc.colonize(pts, "none", 4, 0.9, 16 / 9, 1) == [(x, y, rc.SEED_RADIUS) for x, y in pts], "sans symetrie : germes inchanges, liberte sans effet")
    m = rc.colonize([(0.2, 0.3)], "mirror_lr", 4, 0.0, 16 / 9, 1)
    check(len(m) == 2 and abs(m[1][0] - 0.8) < 1e-9 and abs(m[1][1] - 0.3) < 1e-9, "miroir gauche-droite exact a liberte 0")
    q = rc.colonize([(0.2, 0.3)], "quad", 4, 0.0, 16 / 9, 1)
    check(sorted((round(x, 6), round(y, 6)) for x, y, _ in q) == [(0.2, 0.3), (0.2, 0.7), (0.8, 0.3), (0.8, 0.7)], "quatre quadrants exacts")
    r = rc.colonize([(0.7, 0.5)], "rot", 4, 0.0, 16 / 9, 1)
    dist = [((x - 0.5) * 16 / 9) ** 2 + (y - 0.5) ** 2 for x, y, _ in r]
    check(len(r) == 4 and max(dist) - min(dist) < 1e-9, "rotation : copies a egale distance du centre (coordonnees isotropes)")
    j1, j2 = rc.colonize(pts, "quad", 4, 0.8, 16 / 9, 5), rc.colonize(pts, "quad", 4, 0.8, 16 / 9, 5)
    check(j1 == j2 and j1 != rc.colonize(pts, "quad", 4, 0.0, 16 / 9, 5)
          and all(0 <= x < 1 and 0 <= y < 1 and r_ > 0 for x, y, r_ in j1), "liberte : decalage deterministe, dans le cadre")
    check(rc.base_seed_count("none", 4) == 40 and rc.base_seed_count("quad", 4) == 10 and rc.base_seed_count("rot", 8) == 5,
          "germes de base reduits selon l'orbite (~40 au total)")
```

- [ ] **Step 2 : lancer `.venv\Scripts\python.exe tools/check_gl.py --pur` ; attendu : echec sur `shape_feed_kill` absent.**
- [ ] **Step 3 : implementer les noms de l'Interfaces block dans `core/reaction.py`.** Pour `s` dans 0..1, `shape_feed_kill` appelle `feed_kill(s)` (egalite exacte exigee).
- [ ] **Step 4 : relancer `--pur` ; attendu : tout passe.**
- [ ] **Step 5 : commit** `core/reaction.py tools/check_gl.py` - « Labyrinthe : chemin de forme a trois points et orbites de germes (logique pure) ».

### Task 2 : trouver `BLOB` par balayage

**Files :**
- Create : `tools/rd_sweep.py` (script de recherche, non lance par la suite)
- Modify : `engine/reaction.py` (`step` : `rc.shape_feed_kill(shape)` a la place de `rc.feed_kill(shape)`), `core/reaction.py` (`BLOB`), `tools/check_gl.py` (`check_reaction_sim`)

**Interfaces :**
- Consumes : `rc.shape_feed_kill`, `rc.BLOB` (tache 1).
- Produces : `rc.BLOB` definitif ; `ReactionDiffusion.step(n, shape)` accepte `shape` dans 0..2.

- [ ] **Step 1 : test a ajouter a `check_reaction_sim`**

```python
    blobs = sim(2.0, 6000).read_v() > 0.25
    _lb, bsizes = rd_components(blobs)
    check(0.45 <= blobs.mean() <= 0.85 and np.mean(bsizes) > 5 * np.mean(maze), "forme 2 : grosses taches pleines, sans tout envahir",
          f"couverture {blobs.mean():.2f}, aire moyenne {np.mean(bsizes):.0f}")
    path = {s: float((sim(s, 3000).read_v() > 0.25).mean()) for s in (1.2, 1.4, 1.6, 1.8)}
    check(all(0.05 < x < 0.9 for x in path.values()), "chemin lignes -> taches vivant a chaque etape", str(path))
```

- [ ] **Step 2 : `tools/rd_sweep.py`** : pour F de 0,020 a 0,070 et K de 0,050 a 0,068 par pas de 0,002, affecte `rc.BLOB = (F, K)`, simule `shape = 2.0` 6000 pas (contexte standalone, comme `check_reaction_sim`), imprime les candidats qui verifient : couverture de V > 0,25 entre 0,45 et 0,85, aire moyenne des composantes > 5 x celle de la forme 1, motif qui change encore entre 6000 et 8000 pas (> 300 cellules), et chemin 1,2 / 1,4 / 1,6 / 1,8 vivant. Lancer : `.venv\Scripts\python.exe tools/rd_sweep.py`. Attendu : au moins un candidat ; prendre celui dont la couverture est la plus proche de 0,65.
- [ ] **Step 3 : figer le candidat dans `BLOB`** (commentaire : valeur et mesures comme pour `SPOTS`), modifier `step`.
- [ ] **Step 4 : lancer la suite complete en arriere-plan ; attendu : les deux nouvelles verifications passent, les anciennes de `check_reaction_sim` et `check_bg_labyrinthe` aussi.** Si aucun candidat : s'arreter et le signaler (la spec prevoit de resserrer le chemin).
- [ ] **Step 5 : commit** - « Labyrinthe : regime taches (BLOB) trouve par balayage ».

### Task 3 : reglages, automations, Randomizer

**Files :**
- Modify : `core/params_schema.py` (apres `bg_rd_ground`), `core/automation.py` (`AUTOMATION_SPECS`), `core/randomizer.py` (`random_background_params`), `tools/check_gl.py` (`check_params_schema` vers la ligne 8264, `check_automation`, `check_randomizer` vers la ligne 6673)

**Interfaces :**
- Produces : les six cles de Global Constraints dans `DEFAULT_PARAMS` / `BG_KEYS` ; cinq entrees dans `AUTOMATION_SPECS` (valeurs exactes de Global Constraints, `True` en 6e champ).

- [ ] **Step 1 : tests**
  - schema : `len(rd_keys) == 13`, defauts exacts de Global Constraints, `coerce_params({"bg_rd_sym": "spirale", "bg_rd_branches": 12, "bg_rd_free": 3})` rend `none`, 8, 1,0 ;
  - **ancienne scene** (Review Focus 1) : `gl.scene_values` d'une scene labyrinthe capturee **sans** les six cles rend les defauts, sans exception ;
  - automations : `len(gl.AUTOMATION_SPECS) == 28`, les cinq cles actives par defaut, periodes actives toujours toutes differentes (verification existante), chaque plage contenue dans la plage du schema ;
  - Randomizer (300 tirages) : pour la palette labyrinthe, `bg_rd_mix` dans 0..0,9, `bg_rd_layers` dans 0..0,8, `bg_rd_drift` dans 0,1..0,8, `bg_rd_sym` dans `SYM_MODES` et non `none` dans 25 a 45 % des cas, `bg_rd_free` >= 0,2 quand `bg_rd_sym` = `rot` ; aucune cle `bg_rd_*` hors de cette palette (verification existante).
- [ ] **Step 2 : `--pur` ; attendu : echecs sur les nouvelles cles.**
- [ ] **Step 3 : ajouter les six lignes `_p(...)` (portee `bg`, docs sans accents), les cinq specs, et les tirages dans `random_background_params`** (symetrie : `rng.random() < 1/3` puis `rng.choice(SYM_MODES[1:])`, branches `rng.randint(3, 6)`, liberte `rng.uniform(0.2, 0.8)`).
- [ ] **Step 4 : `--pur` ; attendu : tout passe.**
- [ ] **Step 5 : commit** - « Labyrinthe : reglages des zones et de la symetrie, automations, Randomizer ».

### Task 4 : carte de forme spatiale (shader + moteur)

**Files :**
- Modify : `gl_shaders/rd_step.frag`, `engine/reaction.py`, `engine/render.py` (`_labyrinth`, `__init__`), `tools/check_gl.py` (nouvelle `check_rd_zones`, appelee juste apres `check_reaction_sim`)

**Interfaces :**
- Consumes : `rc.shape_feed_kill`, `rc.SPOTS`, `rc.MAZE`, `rc.BLOB`, `rc.ZONE_CELLS`, `rc.ZONE_SPEED`, `rc.SYM_MODES`.
- Produces :
  - `core.reaction.Zone` (`NamedTuple` : `mix: float = 0.0`, `t: float = 0.0`, `sym: str = "none"`, `branches: int = 4`, `free: float = 0.0`, `aspect: float = 16 / 9`) ;
  - `ReactionDiffusion.step(n: int, shape: float, seeds=(), zone: Zone | None = None) -> list` (rend les germes non poses, voir tache 5 ; ici rend toujours `[]`) ;
  - `Renderer._rd_zone_t: float` (temps de derive integre).
- Uniforms de `rd_step.frag` : `u_fk` (inchange : (F, K) exact de la forme de base), `u_base` (forme de base 0..1), `u_mix`, `u_zone_t`, `u_fk3[3]` (`SPOTS`, `MAZE`, `BLOB`), `u_zone_cells` (vec2), `u_sym` (int, indice dans `SYM_MODES`), `u_branches` (int), `u_free`, `u_aspect`, `u_step` (int, compteur de pas).

- [ ] **Step 1 : tests `check_rd_zones`** (contexte standalone, helpers `sim` / `rd_components` comme `check_reaction_sim`) :
  - **non-regression** : 1000 pas a la forme 0,6 avec `zone=None` puis avec `Zone(mix=0.0, t=3.0, free=0.9)` : `np.array_equal` des deux `read_v()` ;
  - **coexistence** : `Zone(mix=0.8, t=0.0)` a la forme 1,0, 6000 pas ; decouper la grille en 15 blocs (5 x 3) ; l'aire moyenne des composantes du bloc le plus « gras » > 4 x celle du bloc le plus « fin » ;
  - **vivant en derivant** : 40 fois (100 pas, `t += 0.05`) a `mix=0.9` : couverture de V > 0,25 toujours entre 0,05 et 0,9 ;
  - **agglomeration** : forme 1,0, 6000 pas, avec `Zone(mix=0.9)` contre sans zone : l'aire de la plus grande composante est au moins 1,5 x plus grande avec les zones (une partie de l'image passe au-dela de 1, vers les taches) ;
  - **taille du motif** (Review Focus 3) : `ensure(1.5)` apres 1000 pas a `mix=0.8`, puis 500 pas : couverture entre 0,05 et 0,9 ;
  - **tempo 0** (Review Focus 5) : dans `check_bg_labyrinthe`, `run(..., tempo=0, bg_rd_mix=0.8)` deux fois de suite avec des kicks : image identique et `_rd_zone_t` inchange.
- [ ] **Step 2 : lancer la suite complete ; attendu : echecs de `check_rd_zones`.**
- [ ] **Step 3 : shader** : si `u_mix <= 0.0` alors `fk = u_fk` (chemin actuel, bit pour bit) ; sinon `s = clamp(u_base + u_mix * z, 0.0, 2.0)` et `fk` interpole entre `u_fk3[0..2]` comme `shape_feed_kill`. `z = 2 * vnoise(q) - 1`, ou `vnoise` = bruit de valeur a 2 octaves (poids 0,65 / 0,35, la seconde a frequence double), hachage sans `sin` (style `chash3` de `scene.frag`), sur un reseau **periodique** de `u_zone_cells` cellules (indices modulo), et `q = uv_zone * u_zone_cells + vec2(u_zone_t, 0.37 * u_zone_t)`. En tache 4, `uv_zone = v_uv` (le repli par symetrie arrive en tache 5).
- [ ] **Step 4 : moteur** : `step` pose les nouveaux uniforms (`"nom" in prog` comme aujourd'hui) ; `_labyrinth` integre `self._rd_zone_t += dt * rc.ZONE_SPEED * float(p["bg_rd_drift"])` (`dt` deja multiplie par le tempo ; seulement quand `n > 0`) et passe `Zone(mix=p["bg_rd_mix"], t=self._rd_zone_t, ...)`. La forme de base reste `min(max(bg_rd_shape + 0.4 * bass, 0), 1)`.
- [ ] **Step 5 : suite complete ; attendu : tout passe (dont les verifications existantes du labyrinthe, inchangees).**
- [ ] **Step 6 : commit** - « Labyrinthe : carte de forme spatiale qui derive (points, lignes, taches) ».

### Task 5 : symetries de colonisation

**Files :**
- Modify : `gl_shaders/rd_step.frag`, `engine/reaction.py` (`reset`, `step`), `engine/render.py` (`_labyrinth`), `tools/check_gl.py` (nouvelle `check_rd_symmetry` apres `check_rd_zones`, pure : extension de `check_reaction_pure` pour la file)

**Interfaces :**
- Consumes : `rc.colonize`, `rc.base_seed_count`, `rc.Zone`, `rc.FREE_NOISE`, `rc.FREE_ZONE`, `rc.MAX_PENDING_SEEDS`.
- Produces :
  - `ReactionDiffusion.reset(seed: int, zone: Zone | None = None)` : germes = `rc.colonize(rc.initial_seeds(seed, rc.base_seed_count(sym, branches)), sym, branches, free, aspect, salt=seed)` (sans zone ou `sym` = `none` : comportement actuel exact) ;
  - `ReactionDiffusion.step(...)` pose les germes (triplets `(x, y, rayon)`) **4 par pas** sur les pas successifs et rend ceux qui restent ;
  - `core.reaction.queue_seeds(pending: list, new: list) -> list` : concatene et garde les `MAX_PENDING_SEEDS` plus recents.

- [ ] **Step 1 : tests**
  - pur : `queue_seeds([1] * 30, [2] * 10)` a 32 elements, finit par les dix `2` (Review Focus 4) ;
  - `check_rd_symmetry` : `corr(a, b)` = coefficient de correlation de Pearson de deux tableaux aplatis ;
    - `sym=mirror_lr`, `mix=0.6`, 3000 pas : `c_lo` = corr(V, fliplr(V)) a `free=0.1`, `c_hi` a `free=0.9` ; attendus `c_lo > 0.6`, `c_hi < c_lo - 0.15`, `c_lo < 0.999` ;
    - `sym=none`, meme graine : corr(V, fliplr(V)) < 0,3 ;
    - `sym=none`, `free=0.9`, `mix=0.0` : `np.array_equal` avec la simulation sans zone (aucun bruit de Liberte hors symetrie) ;
    - `sym=quad`, `free=0.1` : corr(V, flipud(V)) > 0,5 ;
    - **changement de symetrie a chaud** (Review Focus 2) : dans `check_bg_labyrinthe`, un `run` en `none` puis la meme instance en `mirror_lr` : `rr.rd.seed` inchange et l'image ne repart pas d'un etat seme (ecart moyen a un Renderer neuf en `mirror_lr` > 1) ;
    - **rafale** : 50 kicks d'un coup en `rot` a 8 branches : `len(rr._rd_pending) <= 32`.
- [ ] **Step 2 : suite complete ; attendu : echecs des nouvelles verifications.**
- [ ] **Step 3 : shader** : repli des coordonnees des zones selon `u_sym` : `mirror_lr` x' = 0,5 - |x - 0,5| ; `mirror_tb` meme chose en y ; `quad` les deux ; `rot` en coordonnees isotropes autour du centre (x multiplie par `u_aspect`), angle ramene dans [0, 2 pi / `u_branches`) puis replie en miroir dans le secteur, rayon garde. Si `u_sym > 0` : `z += u_free * FREE_ZONE * (2 * vnoise(v_uv * u_zone_cells + 11.3) - 1)` (non replie) et, apres la mise a jour, `v += u_free * FREE_NOISE * (hash(texel, u_step) - 0.5)`. Si `u_sym == 0` : aucun de ces deux bruits.
- [ ] **Step 4 : moteur** : `reset` et `step` comme l'Interfaces block ; `_labyrinth` colonise les germes de chaque kick (`rc.colonize(rc.kick_seeds(k), sym, branches, free, aspect, salt=k)`), remplace `self._rd_pending[-4:]` par `rc.queue_seeds`, garde les germes rendus par `step`, et passe la `Zone` complete. Le compteur `u_step` est un entier de `ReactionDiffusion` incremente a chaque pas. Bruit de Liberte ignore si `self.dtype != "f4"` ; dans ce cas `Renderer.rd_note` = « Liberte sans effet : etat de la simulation en 16 bits », publie dans `s.status["rd"]` par `engine/projection.py` (comme `sources_now`), sinon "". Changer `bg_rd_sym` ne reinitialise rien.
- [ ] **Step 5 : suite complete ; attendu : tout passe.**
- [ ] **Step 6 : commit** - « Labyrinthe : symetries de colonisation (germes et zones) avec liberte ».

### Task 6 : seuil par couche (`scene.frag`)

**Files :**
- Modify : `gl_shaders/scene.frag` (`rd_layer`, `labyrinth_pattern`), `engine/render.py` (`_labyrinth` : `u_rd` z = `bg_rd_layers * rc.LAYER_SPREAD`), `tools/check_gl.py` (`check_bg_labyrinthe`)

**Interfaces :**
- `rd_layer(vec2 p, float angle, float thr)` ; seuils couche 1 = 0,25 + `u_rd.z`, couche 2 = 0,25, couche 3 = max(0,25 - `u_rd.z`, 0,05).

- [ ] **Step 1 : tests dans `check_bg_labyrinthe`** : `bg_rd_layers=0` = image identique a l'actuelle (comparaison avec un run sans la cle, `abs(...).max() == 0`) ; `bg_rd_layers=1`, ecart 0 : trois runs ou une seule couche a une couleur (`#ffffff`) et les deux autres la couleur du fond (`#000000`) ; `aire(i)` = part des pixels blancs du run de la couche `i` ; attendu `aire(1) < aire(2) < aire(3)`.
- [ ] **Step 2 : suite complete ; attendu : echec.**
- [ ] **Step 3 : implementer.**
- [ ] **Step 4 : suite complete ; attendu : tout passe.**
- [ ] **Step 5 : commit** - « Labyrinthe : epaisseur differente par couche ».

### Task 7 : GUI (`rd_box`)

**Files :**
- Modify : `gui/gui_fond.py` (apres le curseur « Ecart des couches »), `gui/gui_gates.py` (grisage de « Branches » hors `rot`), `tools/check_gl.py` (etape GUI vers la ligne 603, verification vers la ligne 1494)

**Interfaces :**
- Curseurs (`param_slider`, qui pose seul la case `~` des cles automatisees) : « Variete » `bg_rd_mix` 0..1 pas 0,05 ; « Derive » `bg_rd_drift` 0..1 pas 0,05 ; « Epaisseur des couches » `bg_rd_layers` 0..1 pas 0,05 ; « Liberte » `bg_rd_free` 0..1 pas 0,05 ; « Branches » `bg_rd_branches` 2..8 pas 1 ; menu « Symetrie » `bg_rd_sym` (libelles : Aucune, Miroir gauche-droite, Miroir haut-bas, Quatre quadrants, Rotation). Info-bulles sans accents, une phrase chacune.

- [ ] **Step 1 : tests de l'etape GUI** : les six controles sont dans `c["rd_box"]` ; le curseur Variete ecrit `params["bg_rd_mix"]` ; « Branches » grise pour `mirror_lr`, actif pour `rot` ; les quatre curseurs automatisables ont leur case `~` (`auto.snapshot()` contient les cinq cles).
- [ ] **Step 2 : suite complete ; attendu : echec.**
- [ ] **Step 3 : implementer** (le menu suit le modele des menus deroulants existants du bloc, valeur ecrite telle quelle dans `params`).
- [ ] **Step 4 : suite complete ; attendu : tout passe ; hauteur de fenetre toujours sous 700 px (verification existante).**
- [ ] **Step 5 : commit** - « Labyrinthe : reglages des zones, des couches et de la symetrie dans la fenetre ».

### Task 8 : cout GPU, documentation, verification finale

**Files :**
- Modify : `core/safety.py` (`BG_RD_COST_MS` si la mesure le demande), `CLAUDE.md` (section palette labyrinthe + compte des automations 23 -> 28), `README.md`

- [ ] **Step 1 : mesurer** : `--synthetic --stats --max-seconds 13` (palette labyrinthe, `bg_rd_mix` 0,9, `bg_rd_sym` `rot`, 8 branches) contre la palette actuelle ; reporter la difference a 1080p dans le commentaire de `BG_RD_COST_MS` et l'ajuster si elle depasse 0,2 ms. Sur le serveur de CI : non mesure (logiciel).
- [ ] **Step 2 : documentation** : `CLAUDE.md` (zones, axe 0..2, `BLOB` mesure, symetries, Liberte, file des germes, seuils par couche, automations, tests), `README.md` (une ligne par nouveau reglage).
- [ ] **Step 3 : verification** : `--pur` puis suite complete en arriere-plan ; attendu : code 0 (ou 3 si seuls des tests `FRAGILES` echouent, la CI relance).
- [ ] **Step 4 : commit** - « Labyrinthe : cout mesure et documentation » ; PR `Closes #102`.

Non verifie automatiquement : rendu sur le projecteur, plages des automations jugees a l'oeil, cout sur le PC de la soiree.
