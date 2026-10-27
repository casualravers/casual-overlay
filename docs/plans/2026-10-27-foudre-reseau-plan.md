# Foudre v2 (reseau vivant) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Remplacer l'arbre fixe de la Foudre (PR #62) par un reseau persistant qui pousse, se soude en boucles, se consolide et s'efface, avec un pouls qui suit les routes a chaque kick.

**Architecture:** `core/foudre.py` est reecrit (graphe de noeuds, pointes actives, grille spatiale, ondes, vigueur, elagage) et expose `segments()` / `pulses()` / `stats()`. Le moteur envoie la geometrie (centre du segment + decalage de coin + attributs) dans un tampon reecrit a chaque image ; le shader de sommets calcule l'intensite (vigueur, onde, basses, aigus). Le reste (interrupteur, reglages, branchements, composition dans `post.frag`, securite) est inchange.

**Tech Stack:** Python 3.12 (core pur), moderngl + numpy (engine), GLSL 330. Tests dans `tools/check_gl.py`. Documents en francais SANS accents.

**Spec:** `docs/plans/2026-10-27-foudre-reseau-design.md` (remplace le modele de `2026-10-27-foudre-design.md`).

## Global Constraints

- `core/` : stdlib seulement (`check_core_purity`). Fichiers ASCII, CRLF.
- Constantes de `core/foudre.py` (valeurs de depart, a regler a l'oeil, toutes nommees en tete de module) : `STEP` 0.012, `STEPS_PER_S` 18, `FORK_P` 0.045, `JOIN_R` 0.018, `JOIN_P` 0.85, `AVOID_R` 0.05, `MAX_SEGMENTS` 1200, `MAX_TIPS` 100, `MAX_STEPS_FRAME` 60, `MIN_PUSH_S` 0.25, `PULSE_SPEED` 1.6, `PULSE_BAND` 0.06, `MAX_PULSES` 4, `DECAY` 0.18, `W_FINE` 0.0012, `W_TRUNK` 0.0065, `DESC_REF` 40, `W_INERTIA_S` 0.5, `MAX_DT` 0.1.
- `phase_ceiling(name, build)` et `PHASE_CEILING` restent tels quels. Les reglages (`strobe_kind`, `foudre_amount`, `foudre_reach`, `foudre_color`, `foudre_drop`, `foudre_clear`) ne changent pas.
- Mode Strobe : image identique pixel pour pixel, aucun surcout. Jamais de flash plein ecran ; plafond de luminosite 0,9 de `post.frag` conserve.
- Git : branche `feat/foudre-reseau` depuis `origin/main` (apres fusion de la PR de spec), PR, merge quand la CI est verte. Commits en francais sans accents.
- Trois echecs locaux deja presents sur `origin/main` dans cet environnement (scene : capture sans l'entree audio ; selecteur Source ; audio coupe) : ne pas les attribuer a ce travail.

## Review Focus

- Un calcul qui devient quadratique ou qui depasse le budget d'image (1200 segments, 100 pointes, 60 pas par image) : le reseau ne doit jamais ralentir le rendu.
- Elagage et vigueur : un noeud supprime alors qu'il a encore des descendants, une soudure qui reste pendue a une pointe supprimee, un identifiant reutilise (reseau incoherent, segment qui saute ou disparait).
- Format d'image extreme et redimensionnement en cours de croissance : pointes bloquees hors cadre, reseau etire.
- Bascule Foudre -> Strobe -> Foudre, PANIQUE dans les deux modes, Drop sans flash blanc, tempo 0, dt enorme, rafale de kicks en une image.
- Aucun clignotement global du reseau (bouton maintenu compris) et aucun blanc plein ecran, avec ou sans trainee.

---

### Task 1: Croissance du reseau (`core/foudre.py`, premiere moitie)

**Files:**
- Modify: `core/foudre.py` (reecriture : on garde `phase_ceiling`, `PHASE_CEILING`, `DEFAULT_CEILING`, `_unit` ; on supprime `Seg`, `build_tree`, la machine a generations), `tools/check_gl.py` (`check_foudre_pure` reecrite : les anciens cas d'arbre / generations / charge disparaissent, le cas de schema et panique reste)
- Test: `tools/check_gl.py::check_foudre_pure`

**Interfaces:**
- Produces : `class Lightning` avec `__init__(self, seed: int = 1)`, `configure(self, aspect: float, reach: float) -> None`, `update(self, dt: float, kicks: int = 0, ceiling: float = DEFAULT_CEILING, hold: bool = False, speed: float = 1.0, high: float = 0.0) -> None`, `clear(self) -> None`, propriete `empty: bool`, `segments(self) -> list[tuple[float, float, float, float, float, float, float]]` (x0, y0, x1, y1, largeur, vigueur 0..1, dist), `stats(self) -> dict` avec les cles `segments`, `tips`, `joins`, `forks`, `max_dist`. Dans cette tache `update` fait pousser le reseau (pas de pouls, pas d'elagage encore) : la vigueur vaut 1 et la largeur est constante `W_FINE`.

- [ ] **Step 1: Ecrire les tests (rouges)** dans `check_foudre_pure`, `Lightning(1)`, `configure(16/9, 1.0)`, helper `adv(L, secs, **kw)` = pas de 0,1 s dont seul le premier recoit `kicks` :
  - deterministe : deux `Lightning(1)` menes pareil donnent `segments()` egaux ; `Lightning(2)` donne autre chose.
  - un premier kick (`kicks=1, ceiling=1.0`) cree une racine et 6 a 8 troncs : `stats()["tips"]` entre 6 et 8 a l'instant, `empty` faux ; `ceiling=0` ne cree rien.
  - croissance : apres 3 s de kicks reguliers (un toutes les 0,5 s, `ceiling=1.0`) `stats()["segments"] > 150`, strictement croissant a chaque seconde pendant les 3 premieres, jamais > `MAX_SEGMENTS` (1200) ni `tips` > `MAX_TIPS` (100) sur 60 s de pic.
  - ramification : `stats()["forks"] > 10` ; soudures : `stats()["joins"] > 5` avec `Lightning(1)` apres 20 s de pic (si ce n'est pas le cas pour la graine 1, regler `JOIN_*` / `AVOID_R` ; le test porte sur 5 graines : au moins 4 ont des soudures).
  - cadre : pour `reach` 1 et les formats 0.5, 16/9, 4.0, aucun segment hors `|x| <= 0.5*aspect + 1e-6`, `|y| <= 0.5 + 1e-6` ; pour `reach` 0.4 la dist maximale est plus petite qu'a `reach` 1 (moyenne sur 5 graines).
  - asymetrie : la norme du centre de masse des extremites des segments depasse 0.02 pour 4 graines sur 5.
  - aucun NaN / infini dans `segments()` ; `dt` infini, negatif ou NaN, `kicks` negatif, `ceiling` hors plage : pas d'exception.
  - pas de poussee plus d'une fois par `MIN_PUSH_S` : 50 kicks en une image = une seule salve de troncs.
  - le test de schema / panique (strobe_kind, foudre_*, `panic`) est conserve tel quel.
- [ ] **Step 2:** `.venv\Scripts\python.exe tools/check_gl.py --pur` ; attendu : echecs de `check_foudre_pure`.
- [ ] **Step 3: Implementer.** Structures : listes paralleles (`x`, `y`, `parent`, `dist`, `heading`, `steps_left`, `alive`), grille spatiale `dict[(i, j)] -> list[int]` (cellule = `AVOID_R`), liste des pointes actives, liste des soudures `(a, b)`. Une pointe active avance par pas `STEP` a `STEPS_PER_S x speed x (0.6 + 0.4 x energie)` pas par seconde (accumulateur par `update`, au plus `MAX_STEPS_FRAME` pas par appel) ; nouveau cap = cap + bruit (+-0.35 rad x derive) + biais vers l'exterieur (0.08 x ecart angulaire a la direction centre -> point) + repulsion (somme des vecteurs qui eloignent des noeuds voisins non ancetres a moins de `AVOID_R`) ; bifurcation avec `FORK_P` (angle +-25 a 60 degres, vie restante x 0.7) tant que `tips < MAX_TIPS` et `segments < MAX_SEGMENTS` ; une pointe s'arrete quand sa vie est finie ou qu'elle sort du cadre x `reach` ; soudure : si un noeud non ancetre (ni dans les 8 derniers ancetres, ni a moins de 4 `STEP` de dist de chemin dans le meme rameau) est a moins de `JOIN_R` du nouveau point, avec `JOIN_P` on ajoute la soudure et la pointe s'arrete. Premier kick : racine a (0, 0) +- 0.02, 6 a 8 troncs a angles irreguliers, vie 30 a 45 pas. Chaque kick suivant (poussee limitee a une par `MIN_PUSH_S`) fait aussi naitre `round(3 + 7 x energie)` pousses sur des noeuds au hasard (poids plus fort pour les pointes actives et les noeuds proches de la racine), cap = cap du noeud +- 25 a 70 degres, vie 6 a 20 x energie. `segments()` rend un segment par noeud non racine (vers son parent) plus un par soudure ; largeur `W_FINE`, vigueur 1, `dist` du noeud.
- [ ] **Step 4:** relancer `--pur` ; attendu : `check_foudre_pure` vert, `check_core_purity` vert.
- [ ] **Step 5: Commit** : « Foudre v2 : croissance du reseau (bifurcations, soudures, grille spatiale) ».

---

### Task 2: Dynamique du reseau (pouls, consolidation, effacement)

**Files:**
- Modify: `core/foudre.py`, `tools/check_gl.py` (`check_foudre_pure`)
- Test: `tools/check_gl.py::check_foudre_pure`

**Interfaces:**
- Consumes : tout ce que produit la Task 1.
- Produces : `Lightning.pulses(self) -> list[tuple[float, float]]` (rayon en distance de chemin, energie ; au plus `MAX_PULSES`), `Lightning.drop(self) -> None`, vigueur et largeur vivantes dans `segments()`, `stats()` enrichi des cles `pulses` et `energy`.

- [ ] **Step 1: Ajouter les tests (rouges)** :
  - pouls : apres un kick, `pulses()` a une onde de rayon 0 qui croit de `PULSE_SPEED x dt x speed` par `update` (tolerance 5 %) ; elle disparait quand son rayon depasse `max_dist + 0.2` ; au plus 4 en meme temps meme avec des kicks toutes les 0,25 s.
  - renforcement dans l'ordre : avec un reseau etabli dont toutes les vigueurs ont ete baissees (silence 2 s puis kick), le premier noeud qui revient a une vigueur plus haute est plus pres de la racine que le dernier (mesure par `dist` des segments dont la vigueur a augmente a deux instants successifs).
  - consolidation : la largeur du segment le plus proche de la racine est >= 2 x celle de la moyenne des 10 segments aux plus grandes `dist` ; la largeur d'un tronc augmente de kick en kick (5 kicks a 0,5 s : strictement croissante sur la mesure a chaque kick, jamais > `W_TRUNK`) ; changer de `speed` ne change pas les largeurs deja atteintes.
  - evolution sous musique soutenue (`ceiling` 0.6, 8 s de kicks toutes les 0,5 s) : des segments presents a 3 s (identifies par leur extremite) ne le sont plus a 8 s ET d'autres sont nes apres 5 s (les routes naissent et s'effacent).
  - effacement sans kick : les segments de largeur < `W_FINE x 2` disparaissent avant les troncs (instant ou le nombre de segments fins vaut 0 < instant ou le reseau est vide) ; `empty` vrai en 8 s au plus ; pour tout instant, chaque segment de `segments()` a un parent present (aucun segment pendu : verifier que chaque extremite de depart est la racine ou l'extremite d'un autre segment, soudures comprises).
  - hold : `hold=True` pendant 3 s donne un reseau dense ; l'intensite moyenne (`vigor`) ne pulse pas (ecart < 0.05 sur 10 `update` de 0,1 s une fois etabli) ; au relachement tout decroit.
  - `drop()` : `pulses()` contient une onde d'energie 1.5, au moins 24 pousses ajoutees (segments +), vigueurs a 1 ; `clear()` : `empty`, `pulses() == []`.
  - plafond par energie : `ceiling=1.0` donne apres 20 s plus de segments que `ceiling=0.5` (au moins +30 %) ; `ceiling=0` : aucune croissance.
  - tempo : `speed=0` = rien ne bouge (segments identiques sur 1 s) ; un `speed` qui baisse ne fait jamais reculer un segment.
  - cout : 600 appels `update(0.016, ceiling=1.0)` avec un kick toutes les 30 appels, puis 600 appels de `segments()`, moyenne < 20 ms par image (garde contre un calcul quadratique ; imprimer la moyenne).
  - `dt` NaN / infini, `kicks` enorme : pas de valeur non finie dans `segments()` ni `pulses()`.
- [ ] **Step 2:** `--pur` ; attendu : echecs de ces cas.
- [ ] **Step 3: Implementer.** Vigueur par noeud decroit de `DECAY x (1.6 - 0.6 x importance)` par seconde ( x 1.5 si `energie <= 0.2`) ; `importance = min(1, sqrt(desc) / sqrt(DESC_REF))` ou `desc` = nombre de pointes en aval (increment le long de la chaine des parents quand une pointe est creee, decrement quand elle est supprimee). Une onde part de dist 0, avance a `PULSE_SPEED x speed` ; aux noeuds dont la dist est passee entre deux appels : `vigor = min(1, vigor + energie_onde x (0.25 + 0.75 x importance))`. Energie d'onde = `0.4 + 0.6 x ceiling` (1.5 pour `drop`). Largeur : `w` tend vers `W_FINE + (W_TRUNK - W_FINE) x importance` avec une inertie `W_INERTIA_S` (`w += (cible - w) x min(1, dt x speed / W_INERTIA_S)` : integre, jamais de recul quand le tempo baisse). Elagage : une feuille (aucun enfant, aucune soudure entrante) dont la vigueur est <= 0 est retiree (grille, pointes, soudures, `desc` des ancetres mis a jour) ; la vigueur renvoyee par `segments()` est le max de la vigueur du noeud et de celle de ses descendants (parcours des noeuds dans l'ordre inverse de creation). `aigus` (`high` 0..1) augmentent la probabilite de pousses fines a chaque appel (`high x 0.3` pousse supplementaire par seconde). `hold` : energie 1 en continu, une onde toutes les 0,5 s, sans eclat global. `energie` = `_unit(ceiling)` ; croissance et pousses proportionnelles ; `clear()` remet l'etat a zero et incremente l'episode (graine suivante).
- [ ] **Step 4:** `--pur` vert (hors echec local deja present).
- [ ] **Step 5: Commit** : « Foudre v2 : pouls, consolidation, effacement et energie par phase ».

---

### Task 3: Rendu du reseau (shader de sommets, tampon, `post.frag` inchange)

**Files:**
- Modify: `gl_shaders/foudre.vert`, `gl_shaders/foudre.frag`, `engine/render.py` (`__init__` : taille du tampon ; `reload_shaders` : format du VAO ; `_draw_bolt`), `tools/check_gl.py` (`check_foudre_render`, adaptation provisoire)
- Test: `tools/check_gl.py::check_foudre_render`

**Interfaces:**
- Consumes : `Lightning.segments()`, `Lightning.pulses()`.
- Produces : `Renderer._draw_bolt(self, p: dict, size: tuple[int, int], bass: float, high: float, t: float) -> bool` ; format de sommet (8 flottants) : `in_ctr` (2f, centre = extremite du segment), `in_off` (2f, decalage du coin, base = demi-largeur x normale, avec l'extension des bouts), `in_across` (1f), `in_vig` (1f), `in_dist` (1f), `in_fine` (1f, 0 = tronc .. 1 = fin : `1 - min(1, largeur / W_TRUNK)`) ; uniforms de `foudre.vert` : `u_aspect`, `u_pulse` (`vec2[4]`, (rayon, energie), energie 0 = inactive), `u_time`, `u_bass`, `u_high`.

- [ ] **Step 1: Adapter `check_foudre_render`** (tests rouges) : les cas qui pilotaient `rr.bolt.update(0.0, kicks=1, ceiling=1.0)` puis `frame` continuent (la machine avance par `draw` : `advance=False`), mais les attendus changent : (a) apres un kick et 1 s de rendu a 1280 x 720, `lit(img)` > 2 x la valeur de la v1 (3000 a 640 x 360 -> utiliser > 6000 a 640 x 360) ; (b) **pouls** : deux images prises 0,15 s et 0,45 s apres un kick sur un reseau etabli : le rayon du maximum du profil radial de l'intensite ajoutee augmente d'au moins 0.05 (hauteurs d'image) ; (c) **evolution** : l'image a t = 3 s et celle a t = 5 s sous kicks reguliers different d'au moins 2 % des pixels ; (d) couleur, logo jamais assombri, formats extremes, redimensionnement, trainee / blanc : inchanges. Les anciens cas fondes sur `gens` / `charge` (profondeur 6, plafond par phase, bouton maintenu = charge 1...) sont reecrits en Task 4 : pour cette tache les mettre en commentaire `# Task 4`.
- [ ] **Step 2:** lancer `check_foudre_render` (runner du scratchpad) ; attendu : echec (anciens attributs).
- [ ] **Step 3: Implementer.** `foudre.vert` : position = `in_ctr + in_off x (1 + 0.3 x u_bass)` converti en clip comme avant (`/ (0.5 x aspect)`, `/ 0.5`) ; intensite : `base = 0.15 + 0.5 x in_vig` ; `pulse = somme sur k de u_pulse[k].y x exp(-pow((in_dist - u_pulse[k].x) / 0.06, 2.0))` ; `scint = 1.0 - 0.3 x in_fine x (1.0 - u_high x (0.5 + 0.5 x sin(40.0 x u_time + 13.0 x float(gl_VertexID / 6))))` ; `v_int = min(1.0, (base + pulse) x (1.0 + 0.25 x u_bass) x scint)`. `foudre.frag` inchange (coeur `a^3` blanc + corps couleur, fois `v_int`). `_draw_bolt` : convertit `segments()` avec numpy (tableau (n, 7)), construit les 6 sommets par segment (2 triangles, extension de `half` aux deux bouts, demi-largeur >= 1 texel du tampon : l'intensite n'est PAS reduite ici, elle l'est par `v_int` via `in_fine`... garder la regle v1 : si la largeur est clampee, multiplier `in_vig` par `largeur / (2 x half)`), ecrit dans `foudre_vbo` (agrandi a `MAX_SEGMENTS x 6 x 8 x 4` octets), regle les uniforms (4 ondes de `pulses()` completees par (0, 0)), rend, `build_mipmaps()`. `draw` appelle `_draw_bolt(p, size, bass, high, t)` a la place de `draw_list`.
- [ ] **Step 4:** relancer `check_foudre_render` ; attendu : vert pour les cas non commentes.
- [ ] **Step 5: Commit** : « Foudre v2 : rendu du reseau (intensite et pouls dans le shader de sommets) ».

---

### Task 4: Branchement, voyant et tests de pilotage

**Files:**
- Modify: `engine/render.py` (`draw`), `engine/projection.py` (`s.status["foudre"]`), `core/explain.py` (`foudre_info_text`), `gui/gui_vj.py` (`strobe_line` si besoin), `tools/check_gl.py` (cas laisses en commentaire, voyant)
- Test: `check_foudre_render`, `check_foudre_pure` (voyant)

**Interfaces:**
- Consumes : `Lightning.update(dt, kicks, ceiling, hold, speed, high)`, `stats()`.
- Produces : `explain.foudre_info_text(info: dict | None) -> str` (« foudre : 312 segments, 14 pointes, pouls 2 », « foudre : au repos, attend un kick » si `segments == 0`, « foudre : en attente de la projection » si `None`) ; `s.status["foudre"] = renderer.bolt.stats()`.

- [ ] **Step 1: Reecrire les cas en commentaire** de `check_foudre_render` avec `drive(...)` (inchange) : pic (6 kicks, 0,5 s) : `stats()["segments"]` > 250 ; groove : strictement moins de segments que le pic a l'identique et > 0 ; silence / ambiance : `empty` ; `auto_phase=0` : entre groove et pic ; `react_master=0` : `empty` ; mode Strobe : `empty` et image noire ; appui du bouton Strobe : le reseau naît ; bouton maintenu 2 s sans kick : > 250 segments et aucune pulsation (ecart de luminosite moyenne image par image < 5 % sur 10 images consecutives) ; montee a 90 % en groove : plus de segments qu'en groove sans montee ; `foudre_drop` : une onde d'energie >= 1.5 (`pulses()`), image sans pixel blanc plein ecran ; `foudre_clear` (mode Strobe compris) : `empty` ; rafale de 50 kicks : un seul lot de troncs ; tempo 0 : image noire apres kicks, pas d'exception ; images tres espacees (7 s) : pas de saut ni de valeur infinie ; retour en Strobe : le reseau s'eteint seul en 8 s ; aucune image n'a plus de 2 % de pixels blancs ; trainee 0,97 : max <= 231. Test pur du voyant : `foudre_info_text(None)`, `(stats vide)`, `(stats a 312 segments)`.
- [ ] **Step 2:** lancer ; attendu : rouge.
- [ ] **Step 3: Implementer** dans `draw` : `kicks` comme avant ; `ceiling = phase_ceiling(phase_name, build)` ; `self.bolt.update(raw_dt, kicks, ceiling, hold, tempo, high)` ; `foudre_drop` -> `bolt.drop()` ; `foudre_clear` -> `bolt.clear()` (dans les deux modes, comme aujourd'hui) ; hors mode Foudre `bolt.update(raw_dt)` fait decroitre. `projection.py` publie `stats()` ; `explain.foudre_info_text` lit le dict.
- [ ] **Step 4:** relancer ; attendu : vert.
- [ ] **Step 5: Commit** : « Foudre v2 : branchement sur les kicks, la phase, le strobe, la montee, le drop et la panique ; voyant ».

---

### Task 5: Documentation, suite complete, mesure, revue

**Files:**
- Modify: `CLAUDE.md` (section Foudre reecrite), `README.md` (paragraphe Foudre), `docs/plans/2026-10-27-foudre-design.md` (statut : remplace par la v2)

- [ ] **Step 1: Documenter** : modele du reseau (noeuds, pointes, grille, soudures, pouls, vigueur, consolidation par les tuyaux, effacement), plafonds, intensite dans le shader, voyant, constantes a regler a l'oeil, pieges rencontres.
- [ ] **Step 2: Mesurer** le temps de `update` + `segments()` + `_draw_bolt` a 1200 segments sur le poste de dev (script jetable) et l'ecrire dans CLAUDE.md ; si > 4 ms par image, reduire `MAX_SEGMENTS` ou la frequence de `segments()` (recalcul toutes les 2 images) et le noter.
- [ ] **Step 3:** `.venv\Scripts\python.exe tools/check_gl.py --pur` puis la suite complete en arriere-plan ; attendu : tout vert hors les trois echecs locaux deja presents.
- [ ] **Step 4: Revue** : un relecteur Opus sur `git diff origin/main...HEAD` avec la section Review Focus ; corriger Critical et Important par TDD, noter les mineurs.
- [ ] **Step 5: Commit, PR, merge** quand la CI est verte.

---

## Self-review

- Couverture de la spec : croissance, soudures, pouls, consolidation, effacement, energie par phase, bouton maintenu, Drop, PANIQUE, plafonds, API (T1, T2) ; rendu et audio basses / aigus (T3, T2) ; branchement et voyant (T4) ; docs, mesure, revue (T5).
- Ecarts a la spec : le recalcul de `segments()` peut etre espace d'une image sur deux si la mesure l'exige (T5) ; les pousses de kick sont limitees a `MIN_PUSH_S` comme la v1.
- Proportion : le plan decide les signatures, le format de sommet, la formule d'intensite et les assertions ; les corps (marche, soudure, elagage) sont decrits par leurs regles, pas ecrits.
- Valeurs a regler a l'oeil apres le premier rendu (hors tests) : tout le bloc de constantes de `core/foudre.py`, la formule d'intensite du shader.
