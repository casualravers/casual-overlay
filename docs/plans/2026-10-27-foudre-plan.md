# Foudre Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Ajouter le mode Foudre (arbre de lumiere fractal declenche par les kicks) comme alternative au strobe, choisi par un interrupteur Strobe / Foudre de l'onglet *Live*.

**Architecture:** `core/foudre.py` (stdlib) genere l'arbre et porte la machine d'etat (charge, generations, croissance, decroissance). `engine/render.py` appelle cette machine a chaque image, ecrit les segments en rubans additifs dans une texture demi-resolution (`foudre.vert` / `foudre.frag`), et `post.frag` la compose (coeur + halo par mipmaps) juste avant le melange du strobe. En mode Foudre `_strobe` ne rend plus d'eclair blanc : ses decisions deviennent des departs de decharge.

**Tech Stack:** Python 3.12 (core pur), moderngl, numpy (engine seulement), GLSL 330, Tk. Tests dans `tools/check_gl.py`. Docs en francais SANS accents.

**Spec:** `docs/plans/2026-10-27-foudre-design.md`

## Global Constraints

- `core/` n'importe ni numpy, ni GL, ni Tk, ni le moteur (`check_core_purity`). Fichiers en CRLF, ASCII.
- Un reglage = une ligne dans `core/params_schema.py` ; curseurs de la GUI dans la plage de la table.
- Defauts : `strobe_kind` = `strobe`, `foudre_amount` 0.7 (0..1.5), `foudre_reach` 1.0 (0.4..1.5), `foudre_color` `#3a7bff`, portee `room` ; `foudre_drop` et `foudre_clear` : entiers, portee `state`, defaut 0.
- Constantes de `core/foudre.py` : `MAX_DEPTH = 6` (generations 0..5), `MAX_SEGMENTS = 300`, `MIN_PUSH_S = 0.25`, `GROW_S = 0.25`, `FLASH_S = 0.15`, `HALF_LIFE_S = 2.5`, `CLEAR_S = 4.0`, `GAIN = 0.4`.
- Mode Strobe : image identique pixel pour pixel a l'actuelle, aucun surcout. Jamais de flash plein ecran en Foudre.
- Les tests de rendu passent par `gl.Renderer(ctx, size, None)` et `rr.draw(fbo, size, state, p, t)` avec des images de 0,05 s (dt plafonne a 0,1 s), comme `check_bg_labyrinthe`.
- Git : branche depuis `origin/main`, PR, merge quand la CI est verte (consigne permanente de l'utilisateur). Messages de commit en francais sans accents.

## Review Focus

- Format d'image extreme (fenetre portrait, 4:1) : l'arbre reste dans le cadre pour `reach` <= 1, pas de division par zero.
- Redimensionnement de la fenetre pendant une decharge : texture recreee, pas de crash, pas de lumiere residuelle.
- Bascule Foudre -> Strobe -> Foudre en pleine decharge : pas d'eclair blanc parasite, pas de decharge fantome.
- Tempo a 0 (master) et `dt` enorme (fenetre gelee puis reprise) : croissance figee ou bornee, aucune valeur NaN / infinie.
- Rafale de kicks en une seule image (compteur `beats` qui saute de 50) : une seule poussee, jamais plus de 6 generations ni 300 segments.

---

### Task 1: Arbre et machine d'etat (`core/foudre.py`)

**Files:**
- Create: `core/foudre.py`
- Modify: `tools/check_gl.py` (nouvelle `check_foudre_pure`, ajoutee a `PURES` et appelee comme les autres verifications pures), `tools/build_portable.py` (`APP_FILES` : verifier que `core/*.py` est deja embarque ; sinon l'ajouter)
- Test: `tools/check_gl.py::check_foudre_pure`

**Interfaces:**
- Produces (tout stdlib) :
  - `class Seg(NamedTuple)`: `x0, y0, x1, y1: float` (repere centre, hauteur de l'image = 1 : y dans [-0.5, 0.5], x dans [-0.5*aspect, 0.5*aspect], y vers le haut), `gen: int`, `width: float` (en hauteurs d'image), `k: int` (rang du sous-segment dans sa branche), `n: int` (nombre de sous-segments de la branche), `branch: int` (numero de branche).
  - `build_tree(seed: int, aspect: float, reach: float = 1.0) -> list[Seg]` : arbre complet (toutes generations), trie par generation puis branche puis k, `len <= MAX_SEGMENTS`.
  - `phase_ceiling(name: str | None, build: float = 0.0) -> float` : plafond de charge 0..1.
  - `class Lightning`: `__init__(self, seed: int = 1)`, `configure(self, aspect: float, reach: float) -> None`, `update(self, dt: float, kicks: int = 0, ceiling: float = 0.5, hold: bool = False, speed: float = 1.0) -> None`, `drop(self) -> None`, `clear(self) -> None`, propriete `charge: float`, `gens: int`, `empty: bool`, `draw_list(self, bass: float = 0.0, high: float = 0.0, t: float = 0.0) -> list[tuple[float, float, float, float, float, float]]` (x0, y0, x1, y1, largeur, intensite 0..1), `len <= MAX_SEGMENTS`.

- [ ] **Step 1: Ecrire `check_foudre_pure` (test d'abord) avec ces verifications** (graine, `aspect` 16/9 sauf mention) :
  - `build_tree(1, 16/9) == build_tree(1, 16/9)` ; `build_tree(1, ..) != build_tree(2, ..)`.
  - `len(build_tree(s, a)) <= 300` pour 30 graines et `a` dans (0.5, 16/9, 4.0) ; `reach=1` : tous les points dans `|x| <= 0.5*aspect + 1e-6` et `|y| <= 0.5 + 1e-6` ; aucun NaN ; l'etendue radiale maximale croit avec `reach` (0.4 < 1.0).
  - Asymetrie : pour 30 graines, la norme du centre de masse des pointes (segments a `k == n-1` de la derniere generation de chaque branche) depasse 0.02 pour au moins 24 d'entre elles.
  - Vraies ramifications : un arbre compte des branches de generation >= 2 dont le point de depart est un point d'une branche de generation inferieure (distance < 1e-6) et la generation 0 compte entre 5 et 7 branches.
  - `phase_ceiling` : silence 0, ambient 0, groove 0.5, build 0.7, break 0.17, peak 1.0, `None` 0.5 ; `build=0.9` pendant groove -> 0.9 (max des deux).
  - Accumulation : `L = Lightning(1)`; 10 fois `L.update(0.5, kicks=1, ceiling=1.0)` avec `L.configure(16/9, 1.0)` d'abord : `gens` passe 1,2,3,4,5,6 puis reste 6 ; `charge` croit, reste <= 1 ; `len(L.draw_list()) <= 300`.
  - Plafond par phase : ceiling 0.5 -> `gens` plafonne a 3 (`round(0.5*6)`), ceiling 0 -> aucune poussee (`empty` reste vrai).
  - `MIN_PUSH_S` : deux `update(0.1, kicks=1, ...)` de suite = une seule poussee ; `update(0.1, kicks=50)` = une seule poussee.
  - Croissance : juste apres la premiere poussee (`update(0.0, kicks=1)`) `draw_list()` est vide ou tres courte ; apres `update(0.3, ...)` la liste contient tout le tronc (generation 0 complete) ; avec `speed=0` la liste reste vide.
  - Decroissance : sans kick, `charge` apres 2.5 s vaut ~la moitie (tolerance 5 %) ; apres 4.5 s `empty` est vrai ; les generations hautes disparaissent avant les basses : a `charge` 0.3, aucune entree de `draw_list()` ne vient d'une generation > 2 (mesurer en comptant les segments par largeur : plus fin = generation haute).
  - `hold=True` : `charge == 1.0` et une generation de plus toutes les `GROW_S` jusqu'a 6, meme sans kick.
  - `drop()` : `gens == 6`, `charge == 1.0` ; apres `update(2.0)` la liste a le maximum de segments de l'arbre.
  - `clear()` : `empty` vrai ; la poussee suivante cree un arbre different (graine suivante).
  - Scintillement : avec `high=1.0`, les intensites des segments de generation >= 3 varient entre deux `t` differents, pas celles de la generation 0 ; avec `high=0.0` elles sont identiques. Les basses elargissent : `bass=1.0` -> largeur > largeur a `bass=0.0`. Toutes les intensites dans [0, 1].
  - Robustesse : `update(float("inf"), ...)` et `update(-1.0)` ne levent pas et ne creent pas de NaN ; `dt` est borne a 0.1 s.
- [ ] **Step 2:** lancer `.venv\Scripts\python.exe tools/check_gl.py --pur` ; attendu : `check_foudre_pure` ECHEC (module absent).
- [ ] **Step 3: Implementer `core/foudre.py`.** Regles que les tests ne fixent pas : zigzag par deplacement du point milieu (amplitude 0.12 x longueur, 2 niveaux de recursion, donc 4 sous-segments par branche) ; generation 0 = 5 a 7 troncs a angles irreguliers (angle de base tire + ecart aleatoire d'au plus 35 % du pas, biais global tire par graine), longueur visee = 0.6 a 1.0 x distance du centre au bord du cadre dans cette direction x `reach` ; generation g+1 = 1 a 2 branches par branche de la generation g (a 25 %-90 % de sa longueur), angle 20-50 degres, longueur x 0.6-0.8, largeur x 0.65, largeur du tronc 0.006 ; generation par generation (largeur d'abord) en s'arretant a 300 segments ; points ramenes dans le cadre quand `reach <= 1`. `Lightning` : etat (graine d'episode `seed0 + episode`, `gens`, `charge`, `since_push`, `born[g]` = horloge interne a la poussee de la generation g, horloge interne `now`). Une poussee exige `kicks > 0`, `ceiling > 0` et `since_push >= MIN_PUSH_S` ; `gens = min(gens + 1, max(1, round(ceiling*MAX_DEPTH)), MAX_DEPTH)`, `charge += GAIN*(ceiling - charge)`. Visibilite de la generation g : `charge >= 0.12*g`, fondu `min(1, (charge - 0.12*g)/0.1)`. Croissance : fraction `f = clamp((now - born[g])*speed/GROW_S, 0, 1)`, sous-segments d'une branche reveles dans l'ordre (le dernier tronque au prorata). Intensite : `level = 0.25 + 0.5*charge`, plus `(1 - level)*flash**2` avec `flash = max(0, 1 - since_push/FLASH_S)`, fois le fondu, fois `1 + 0.25*bass` borne a 1 ; scintillement : segments de generation >= 3 multiplient par `0.7 + 0.3*high*(0.5 + 0.5*sin(40*t + 13*i))`. Largeur : `seg.width*(1 + 0.3*bass)`. Decroissance : `charge *= 0.5**(dt/HALF_LIFE_S)` (sauf `hold`) ; `clear()` auto si `since_push > CLEAR_S` ou `charge < 0.02` avec `gens > 0`. `drop()` : `gens = 6`, `charge = 1`, `since_push = 0`, `born[g] = now + g*0.12`.
- [ ] **Step 4:** relancer `--pur` ; attendu : `check_foudre_pure` OK, `check_core_purity` OK, aucune autre regression.
- [ ] **Step 5: Commit** (`core/foudre.py`, `tools/check_gl.py`) : « Foudre : arbre fractal et machine d'etat (core pur) ».

---

### Task 2: Reglages du schema

**Files:**
- Modify: `core/params_schema.py` (apres `strobe_dark`), `core/controls.py` ou le module qui definit `PANIC_RESET` / `panic` (le trouver par `grep -n "def panic"` ; la PANIQUE incremente `foudre_clear`), `tools/check_gl.py` (`check_params_schema` et `check_safeguards`)
- Test: `check_params_schema`, `check_safeguards`

**Interfaces:**
- Produces : cles `strobe_kind` (enum `("strobe", "foudre")`), `foudre_amount`, `foudre_reach`, `foudre_color` (couleur texte `#rrggbb`, meme type que `bg_rd_color1`), `foudre_drop`, `foudre_clear` (entiers, `scope="state"`, comme `strobe_hits`).

- [ ] **Step 1: Tests d'abord** : (a) les six cles existent, defauts et portees comme dans Global Constraints, `strobe_kind`, `foudre_*` (hors compteurs) dans `ROOM_KEYS` et exclues des presets overlay ; `foudre_drop` / `foudre_clear` dans `TRANSIENT_KEYS` ; (b) valeur hors plage ramenee dans la plage, enum inconnu refuse ; (c) `gl.panic(params)` avec `foudre_clear = 3` rend `4` et ne change PAS `strobe_kind` (verifier avec `foudre`).
- [ ] **Step 2:** lancer `--pur` ; attendu : echec de ces trois verifications.
- [ ] **Step 3: Ajouter les six lignes `_p(...)`** (docs en francais sans accents) et l'incrementation dans `panic`.
- [ ] **Step 4:** `--pur` ; attendu : tout vert, y compris « aucun reglage lu ou ecrit sans etre dans la table ».
- [ ] **Step 5: Commit** : « Foudre : reglages strobe_kind, foudre_* et compteurs ».

---

### Task 3: Passe GL de la foudre et composition (`render.py`, shaders)

**Files:**
- Create: `gl_shaders/foudre.vert`, `gl_shaders/foudre.frag`
- Modify: `engine/render.py` (`__init__`, `reload_shaders`, `_ensure_scene`, nouvelle methode `_draw_bolt`, `draw`), `gl_shaders/post.frag`, `tools/check_gl.py` (`check_foudre_render`, appele comme `check_bg_labyrinthe` dans la suite complete)
- Test: `tools/check_gl.py::check_foudre_render`

**Interfaces:**
- Consumes : `Lightning`, `Seg`, `draw_list` de la Task 2 ; cles du schema de la Task 3.
- Produces : `Renderer.bolt: Lightning` (un par Renderer, graine 1) ; `Renderer._draw_bolt(self, p: dict, items: list, aspect: float) -> bool` (ecrit la texture, rend vrai s'il y a quelque chose a composer) ; uniforms de `post.frag` : `u_foudre` (float, 0 = coupe), `u_foudre_tex` (sampler2D, unite 7), `u_foudre_color` (vec3).

- [ ] **Step 1: Ecrire `check_foudre_render`** (contexte standalone, `fbo_for(size)`, base = `dict(gl.DEFAULT_PARAMS, bg_mode="pattern", bg_palette="classic", bg_react=0.0, fx_on=[0]*5, holo_on=0.0, auto_master=0.0, strobe_auto=0.0)`, images de 0,05 s) ; la machine est pilotee DIRECTEMENT (`rr.bolt.configure(aspect, 1.0)` puis `rr.bolt.update(...)`) car l'integration aux kicks est la Task 4 :
  - `strobe_kind="strobe"` : image identique, octet pour octet, a celle de `strobe_kind="foudre"` quand la foudre est vide (aucune poussee) -> la foudre vide ne change rien.
  - Apres `rr.bolt.update(0.0, kicks=1, ceiling=1.0)` et 8 images : l'image differe de l'image de reference en au moins 200 pixels, tous dans le cadre, et le pixel moyen des 5 % pixels centraux est plus lumineux que celui d'un anneau de bord (centre < peripherie en rayon de la lumiere ajoutee).
  - La lumiere ne fait jamais baisser un pixel : `foudre >= reference` canal par canal ; aucun pixel de l'image n'est > 250 sur les trois canaux sur plus de 2 % de la surface (pas de flash plein ecran).
  - Couleur : avec `foudre_color="#ff0000"` la lumiere ajoutee au voisinage des branches a un rouge > vert et > bleu ; avec `#00ff00`, vert dominant.
  - 4 poussees (`ceiling=1.0`, 0,5 s entre elles, 12 images de 0,05 s apres chacune) : l'etendue lumineuse (rayon maximal des pixels ajoutes) croit a chaque poussee puis ne bouge plus a partir de la 6e.
  - Logo : avec un logo par defaut charge, la zone du logo ne s'assombrit pas (additif).
  - Redimensionnement : rendre en (640,360), puis (320,180), puis (640,360) avec le meme Renderer sans exception ; format 4:1 et portrait (360,640) : aucune exception, pixels dans le cadre.
  - Bascule : poussee en mode `foudre`, puis `strobe_kind="strobe"` : l'image redevient identique a la reference strobe sans foudre ; repasser en `foudre` ne ressuscite pas l'ancien arbre tant qu'il n'a pas ete reconfigure ... (la machine continue de decroitre normalement, test : apres 5 s hors Foudre, `rr.bolt.empty`).
- [ ] **Step 2:** lancer la suite complete restreinte (ou `python tools/check_gl.py` en arriere-plan) ; attendu : `check_foudre_render` ECHEC (uniforms / uniform `strobe_kind` sans effet).
- [ ] **Step 3: Implementer.**
  - `foudre.vert` : entrees `in_pos` (vec2, repere centre hauteur 1), `in_across` (float, -1..1), `in_int` (float) ; uniform `u_aspect` ; sort `gl_Position = vec2(x/(0.5*aspect), y/0.5)`.
  - `foudre.frag` : `a = 1 - abs(across)` ; coeur blanc `a^3`, corps de la couleur `u_color * a`, sortie `vec4(core + body, 1) * intensity` (melange additif `ONE, ONE`).
  - `_draw_bolt` : numpy ; pour chaque segment (x0,y0,x1,y1,largeur,intensite) construire 2 triangles (6 sommets) : demi-largeur = max(largeur/2, 1/hauteur_texture) et intensite multipliee de `largeur/(2*demi-largeur)` quand elle est clampee ; sommets ecrits dans un VBO dynamique de `300*6*4*4` octets (`orphan` / `write`) ; la texture demi-resolution (RGBA `f2`, `LINEAR_MIPMAP_LINEAR`) est effacee puis dessinee avec `ctx.blend_func = ONE, ONE`, puis `build_mipmaps()` ; le blend est remis comme avant et le framebuffer de sortie restaure. Texture et fbo crees / liberes dans `_ensure_scene` (pas d'allocation hors du changement de taille).
  - `post.frag` : uniforms `u_foudre`, `u_foudre_tex`, `u_foudre_color` ; avant `// Le strobe ne blanchit...` : si `u_foudre > 0.001`, `vec3 add = textureLod(tex, cuv, 0).rgb + u_foudre_color * (0.8*textureLod(tex, cuv, 3).rgb + 0.5*textureLod(tex, cuv, 5).rgb)` ; `add = min(add * u_foudre, 0.9)` ; `col = 1 - (1 - col) * (1 - add)`.
  - `draw` : dans le bloc des uniforms de `post`, `u_foudre` = 0 sauf si la liste est non vide ET `p["strobe_kind"] == "foudre"` ; la texture est liee a l'unite 7 uniquement dans ce cas. Appeler `self.bolt.configure(width/height, foudre_reach)` a chaque image (ne regenere que si l'un des deux change). Pour cette tache `draw` ne pousse rien : l'integration est la Task 4.
- [ ] **Step 4:** relancer ; attendu : `check_foudre_render` OK, toute la suite verte (aucune autre image n'a change : le mode strobe est inchange).
- [ ] **Step 5: Commit** : « Foudre : passe GL des segments et composition dans post.frag ».

---

### Task 4: Branchement sur les kicks, le strobe, la montee, le drop et la panique

**Files:**
- Modify: `engine/render.py` (`_strobe`, `draw`), `gui/gui_vj.py` (`do_drop`, `handle_drop` : incrementer `foudre_drop`), `core/explain.py` si le texte du voyant du strobe y est fabrique (sinon `gui_vj.py`)
- Test: `tools/check_gl.py::check_foudre_render` (cas ajoutes) et le test GUI du Drop existant (`x_phase_build`)

**Interfaces:**
- Consumes : `Lightning.update/drop/clear`, `phase_ceiling` ; `Renderer._strobe(p, state) -> float` existant.
- Produces : `_strobe` rend toujours 0.0 quand `p["strobe_kind"] == "foudre"` (limiteur et cerveau continuent de tourner) ; `Renderer._new_kicks: int` et `Renderer._fires_accepted: int` lus par `draw` ; `Renderer._foudre_drop_seen`, `_foudre_clear_seen`.

- [ ] **Step 1: Ajouter des cas a `check_foudre_render`** (vrai pilotage par l'etat `beats`, plus d'appels directs a `rr.bolt`) :
  - Etat `{"beats": i, "phase": {"name": "peak", "since": 5.0}, ...}` avec `beats` qui augmente toutes les 10 images (0,5 s) : apres 6 kicks `rr.bolt.gens == 6` ; avec la phase `groove` : `gens <= 3` ; avec `silence` ou `ambient` : `rr.bolt.empty` toujours vrai.
  - `auto_phase=0` (phase absente du state) : plafond 0.5.
  - `react_master=0` : les kicks ne poussent rien.
  - Mode Strobe avec les memes kicks : `rr.bolt.empty` (rien en Strobe) et image identique a la reference.
  - Mode Foudre + `strobe_hits` incremente (appui) : une poussee ; `strobe_on=1.0` en `strobe_mode="manual"` (bouton maintenu) : `charge == 1.0` et `gens` monte jusqu'a 6 en 2 s sans kick ; au relachement la charge decroit.
  - `build=0.9` + kicks pendant `groove` : `gens == 5` (plafond 0.9).
  - `foudre_drop` incremente : `gens == 6` apres 2 s ; `foudre_clear` incremente : `empty`.
  - Rafale : `beats` saute de 0 a 50 en une image -> une seule poussee (`gens == 1`).
  - Tempo 0 (`tempo=0.0`) : croissance figee (aucun segment visible malgre les kicks), pas d'exception ; dt enorme (`rr.draw(..., t + 5.0)`) : pas de NaN, `gens` borne.
  - Aucun pixel entierement blanc sur plus de 2 % de l'image, pendant toute la sequence, y compris sur un Drop.
- [ ] **Step 2:** lancer ; attendu : echecs sur ces cas.
- [ ] **Step 3: Implementer** : (a) `_strobe` : memoriser `self._new_kicks = new_kicks`, compter `self._fires_accepted` quand un eclair est accepte par le limiteur, et rendre `0.0` si `strobe_kind == "foudre"` ; (b) `draw` : appeler `_strobe` AVANT la passe foudre (garder sa valeur pour `u_strobe`) ; calculer `ceiling = phase_ceiling(phase_name if auto_phase >= 0.5 else None, build)` ; `kicks = self._new_kicks + (nouveaux eclairs acceptes)` ; 0 si `react_master <= 0.001` ; `hold = strobe_on >= 0.5 and strobe_mode == "manual"` ; `speed = effective_tempo(p)` ; `self.bolt.update(dt_reel, kicks, ceiling, hold, speed)` seulement en mode Foudre (en Strobe : `self.bolt.update(dt_reel)` sans kick pour la laisser decroitre) ; detecter `foudre_drop` / `foudre_clear` par difference avec la valeur vue ; (c) `gui_vj.py` : `do_drop` et `handle_drop` incrementent `foudre_drop` en plus du reste ; (d) le texte du voyant « strobe auto : ... » commence par `foudre : ` en mode Foudre.
- [ ] **Step 4:** relancer toute la suite ; attendu : tout vert (dont `x_phase_build` et `check_strobe_auto`, inchanges).
- [ ] **Step 5: Commit** : « Foudre : branchement sur les kicks, le strobe, la montee, le drop et la panique ».

---

### Task 5: Interface (onglet *Live*), documentation, mesure

**Files:**
- Modify: `gui/gui_vj.py` (`fill_1`), `gui/gl_gui.py` (exposer les widgets dans le dictionnaire de test si besoin), `tools/check_gl.py` (etape GUI), `CLAUDE.md` (section sous *Strobe auto*), `README.md`
- Test: l'etape GUI de `check_gl.py`

**Interfaces:**
- Consumes : cles de la Task 3 ; helpers `toggle`, `slider`, `button` de `fill_1`, `color_row` (selecteur de couleur existant ; le trouver par `grep -n "def color_row"`).

- [ ] **Step 1: Tests GUI d'abord** (dans l'etape GUI, onglet *Live* selectionne) : deux radios « Strobe » et « Foudre » existent, au-dessus du bouton Strobe (`winfo_y` inferieur) ; cliquer « Foudre » met `params["strobe_kind"]` a `foudre` ; en Foudre : curseur « Sombre » grise, curseurs « Intensite » et « Portee » + couleur actifs ; en Strobe : l'inverse ; un curseur `foudre_amount` deplace met `params` a jour dans sa plage ; `strobe_kind` et `foudre_color` survivent a la sauvegarde (P) / rechargement ; la hauteur de la fenetre reste < 700 px ; le libelle du bouton Strobe reste « Strobe ».
- [ ] **Step 2:** lancer ; attendu : echec (radios absentes).
- [ ] **Step 3: Implementer** `fill_1` : radios en ligne 0 (les autres lignes decalees d'une ligne), curseurs « Intensite » (`foudre_amount`, 0..1.5, pas 0.05, affichage en %) et « Portee » (`foudre_reach`, 0.4..1.5), selecteur de couleur « Couleur », fonction `sync_kind()` qui grise / degrise (jamais de widget retire : la mise en page ne bouge pas) appelee au changement et par `sync_master_panel`. Info-bulles en francais sans accents.
- [ ] **Step 4: Documenter** : paragraphe « Foudre » dans `CLAUDE.md` (decisions, constantes, pieges rencontres, cout GPU mesure avec une passe standalone 1280x720 en comparant avec et sans foudre : l'ecrire en ms) et dans `README.md` ; mettre `docs/plans/...-design.md` en statut « implemente ».
- [ ] **Step 5:** `.venv\Scripts\python.exe tools/check_gl.py --pur` puis la suite complete en arriere-plan ; attendu : tout vert (code 3 = seuls des tests fragiles).
- [ ] **Step 6: Commit** : « Foudre : interrupteur Strobe / Foudre dans l'onglet Live, docs ».

---

## Self-review

- Couverture de la spec : forme (T1), accumulation (T1), audio kick / basses / aigus / tempo / phases (T1 + T4), boutons maintenu / montee / drop / strobe auto (T4), rendu et composition (T3), reglages (T2), interface (T5), securite (plafonds T1, limiteur T4, aucun blanc plein ecran T3 / T4), tests (T1, T3, T4, T5).
- Ecarts a la spec, rules : composition apres la teinte globale (comme le strobe) au lieu d'avant ; reecriture du tampon a chaque image ; pas de changement de `core/safety.py` ; deux compteurs d'etat `foudre_drop` / `foudre_clear` ajoutes. La spec est mise a jour dans la meme PR.
- Valeurs a regler a l'oeil apres le premier rendu (hors tests) : `GAIN`, `HALF_LIFE_S`, largeur du tronc 0.006, poids du halo (0.8 / 0.5), amplitude du zigzag.
