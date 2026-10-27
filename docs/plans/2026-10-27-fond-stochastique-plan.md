# Fond genere stochastique : plan d'implementation

> **Pour les agents :** sous-skill requis : superpowers:subagent-driven-development (recommande) ou superpowers:executing-plans, tache par tache. Les etapes sont des cases a cocher (`- [ ]`).

**But :** ajouter la palette `stochastique` au fond genere : points ronds dont la densite dessine un champ qui bouge (bruit fluide, vagues, degrade), modules par le son.

**Architecture :** une valeur de plus pour `bg_palette` et sept reglages `bg_sto_*` (portee `bg`, donc presets de fond, scenes et capture sans autre code). Le dessin est une fonction de `gl_shaders/scene.frag` (grille jitteree, rang R2 / hachage par cellule, 3 x 3 voisines) ; `Renderer.draw` lui passe les reglages et les niveaux audio du fond deja calcules (`bg_au`). La GUI ajoute un sous-bloc au bloc « Fond genere », le Randomizer tire aussi cette palette.

**Technologies :** Python 3.12, moderngl / GLSL 330, Tk, numpy (tests).

**Spec :** `docs/plans/2026-10-27-fond-stochastique-design.md` (a lire avant de commencer).

## Contraintes globales

- Documentation, commentaires et messages en francais **sans accents**.
- `core/` reste stdlib seulement (`check_core_purity`).
- Un reglage = une ligne de `core/params_schema.py` ; une plage de curseur de la GUI doit tenir dans celle de la table.
- Les autres palettes (`classic`, `duo`, `test`) restent identiques pixel pour pixel ; la palette `test` reste identique octet pour octet a `SyntheticVideoStream`.
- Hachage sans `sin` dans le nouveau code GLSL (`chash`, deja dans `scene.frag`).
- Fichiers CRLF : editer avec un outil qui garde la fin de ligne.
- Travail dans un worktree depuis `origin/main` (`git worktree add ..\casual-overlay-sto -b feat/fond-stochastique origin/main`), Python : `..\casual-overlay\.venv\Scripts\python.exe` (note `PY` plus bas).
- Lancer une verification seule : `PY -c "import sys; sys.argv=['x']; sys.path[:0]=['tools','.']; import check_gl as t; t.NOM(); print('ECHECS', t.failures)"` (attendu : `ECHECS []`).

## Points a surveiller en revue

1. **Ancien preset de fond ou ancienne scene sans cle `bg_sto_*`** : se charge, valeurs par defaut ; teste en tache 1 (`coerce_params` d'un dict sans ces cles).
2. **Reaction a 0 avec un son fort** : image strictement identique a celle du silence ; teste en tache 3.
3. **Teinte (`bg_hue`, et son automation) sur la palette stochastique** : doit tourner les couleurs comme pour les autres palettes ; teste en tache 2.
4. **Taille minimale (3 px a 720 p) en 1280 x 720** : pas d'image vide ni de moire noir ; teste en tache 2 (couverture > 5 % a densite 0,5).
5. **Scene capturee avec la palette stochastique puis rejouee** : tous les `bg_sto_*` reviennent ; teste en tache 4.

---

### Tache 1 : reglages du schema

**Fichiers :**
- Modifier : `core/params_schema.py:169` (choix de `bg_palette`) et lignes suivantes (nouvelles cles apres `bg_react`, ligne 178)
- Test : `tools/check_gl.py`, fonction `check_params_schema` (ligne ~7852)

**Interfaces :**
- Produit : cles `bg_sto_field` (`noise` | `waves` | `gradient`, defaut `noise`), `bg_sto_size` (10, 3..60), `bg_sto_regular` (1, 0..1), `bg_sto_vary` (0, 0..1), `bg_sto_density` (0,5, 0..1), `bg_sto_invert` (0, 0..1), `bg_sto_rainbow` (0, 0..1), toutes `scope="bg"` ; `bg_palette` accepte `stochastique`.

- [ ] **Etape 1 : test qui echoue**, a la fin de `check_params_schema` :

```python
d = gl.DEFAULT_PARAMS
check(d["bg_sto_field"] == "noise" and d["bg_sto_size"] == 10.0 and d["bg_sto_regular"] == 1.0 and d["bg_sto_vary"] == 0.0
      and d["bg_sto_density"] == 0.5 and d["bg_sto_invert"] == 0.0 and d["bg_sto_rainbow"] == 0.0,
      "stochastique: valeurs par defaut (tramage pur)")
sto = [k for k in d if k.startswith("bg_sto_")]
check(len(sto) == 7 and all(k in gl.BG_KEYS for k in sto), "stochastique: 7 reglages, tous du fond (presets de fond, scenes)")
v = gl.coerce_params({"bg_palette": "stochastique", "bg_sto_size": 999, "bg_sto_field": "spirale"})
check(v["bg_palette"] == "stochastique" and v["bg_sto_size"] == 60.0 and v["bg_sto_field"] == "noise",
      "stochastique: palette acceptee, taille bornee, champ inconnu ramene au defaut")
check(gl.coerce_params({"bg_palette": "duo"})["bg_sto_density"] == 0.5, "stochastique: un ancien preset sans ces cles prend les defauts")
```

- [ ] **Etape 2 : verifier l'echec** : `PY ... t.check_params_schema() ...` -> `ECHECS` contient « stochastique: valeurs par defaut ».
- [ ] **Etape 3 : implementer** : `stochastique` dans `choices` de `bg_palette`, sept lignes `_p(...)` avec la doc de la spec (tableau « Reglages »).
- [ ] **Etape 4 : verifier** : meme commande -> `ECHECS []`, puis `PY tools/check_gl.py --pur` -> « Toutes les verifications pures passent. ».
- [ ] **Etape 5 : commit** `Fond stochastique : reglages du schema`.

### Tache 2 : dessin dans le shader

**Fichiers :**
- Modifier : `gl_shaders/scene.frag` (uniforms apres `u_beat`, ligne 34 ; `stochastic_pattern` avant `background_pattern`, ligne ~89 ; aiguillage au debut de `background_pattern`)
- Modifier : `engine/render.py:749-764` (uniforms du fond)
- Test : `tools/check_gl.py`, nouvelle fonction `check_bg_stochastic()` (apres `check_melt`, ligne ~2359), appelee dans `main` apres `check_melt()` (ligne ~8748)

**Interfaces :**
- Consomme : cles de la tache 1.
- Produit (GLSL) : `uniform vec4 u_sto;` = (taille en px pour 720 px, regularite, variation, densite) ; `uniform vec4 u_sto2;` = (champ 0 noise / 1 waves / 2 gradient, inversion 0/1, arc-en-ciel, phase du scintillement) ; `uniform vec4 u_sto_au;` = (basses x reaction, kick x reaction, nombre de kicks si reaction, 0) ; `vec3 stochastic_pattern(vec2 p)`. `u_bg_palette` = 3 pour `stochastique` (dict de `render.py:755`).
- Produit (Python) : `Renderer._sto_flick: float` (phase integree, 0 au depart ; ecrite en tache 3, mise a 0 ici).

- [ ] **Etape 1 : ecrire `check_bg_stochastic()`** sur le modele de `check_melt` (contexte standalone, `size = (640, 360)`, `read_target`). Rendu : `base = dict(gl.DEFAULT_PARAMS, bg_mode="pattern", bg_palette="stochastique", bg_color1="#ff0000", bg_color2="#0000ff", bg_speed=0.0, bg_react=0.0, fx_on=[0]*5, holo_on=0.0, logo_opacity=0.0)`, etat `quiet` de `check_melt`. Helper `lit(img)` = part des pixels ou rouge > bleu. Verifications :
  - deux `Renderer` neufs, memes reglages, meme `t` -> images identiques (`max == 0`) : « deterministe » ;
  - `lit` croissant strictement pour `bg_sto_density` 0,2 / 0,5 / 0,8, `lit < 0,02` a 0, `lit > 0,45` a 1 ;
  - regularite : couverture par blocs de 20 x 20 px (`B`), residu = `B` moins sa moyenne glissante 5 x 5 blocs ; `std(residu)` a `bg_sto_regular=1` < 0,7 x celui a 0 (champ `gradient`, densite 0,5) ;
  - variation : `bg_sto_field="gradient"`, `bg_angle=0` (degrade de gauche a droite) ; a `bg_sto_vary=1` la moitie dense a plus de pixels allumes qu'a `vary=0`, la moitie claire moins ;
  - inversion : `abs(inv[..., 0] - img[..., 2]).max() <= 2` et inversement ;
  - arc-en-ciel 0 : vert max < 5 ; arc-en-ciel 1 : vert max > 100 ;
  - teinte : `bg_hue=1/3` -> plus de bleu (max < 5), du vert present ;
  - les trois champs donnent trois images differentes (ecart moyen > 3 deux a deux) ;
  - taille 3 en 1280 x 720 : `lit > 0,05` a densite 0,5 ; couverture a 640 x 360 et a 1280 x 720 (reduite 2 x 2) a moins de 0,03 pres (independant de la resolution) ;
  - vitesse 1, `t` 0 puis 1,5 : image differente (le champ derive).
- [ ] **Etape 2 : verifier l'echec** (`t.check_bg_stochastic()` -> echecs sur densite / couverture : le shader ne connait pas la palette).
- [ ] **Etape 3 : `stochastic_pattern(vec2 p)`** dans `scene.frag`. Aiguillage : `if (u_bg_palette > 2.5) return stochastic_pattern(p);` **avant** le test `> 1.5` de la palette test. Algorithme a respecter (le reste est libre) :

```glsl
// cellule de cote s = max(u_sto.x * u_res.y / 720.0, 2.0) px ; px = p * u_res
// pour chaque voisine c (3 x 3) :
//   r2  = fract(c.x * 0.7548776662 + c.y * 0.5698402910)         // rang a faible discrepance (bruit bleu)
//   wh  = chash(c + floor(u_sto2.w + chash(c + 7.3)) * 13.1)    // rang blanc, renouvele par le scintillement
//   rank = mix(wh, r2, u_sto.y)
//   jit  = mix(0.5, 0.3, u_sto.y) * (vec2(chash(c + 1.7), chash(c + 4.1)) * 2.0 - 1.0)
//   ctr  = (c + 0.5 + jit) * s                                   // centre du point en px
//   f    = champ(ctr / u_res) ajuste par la densite : clamp(f + (u_sto.w - 0.5) * 2.0, 0, 1)
//   allume si f >= rank ; rayon = 0.5 * s * (1 + u_sto.z * (f - 0.5) * 2) ; couverture = 1 - smoothstep(rayon - 1, rayon + 1, distance)
// couverture = max sur les 9 voisines ; teinte du point = hue_rotate(c1, f * u_sto2.z)
// col = mix(c2, point, couverture) ; inversion : echanger c1 et c2 ; puis hue_rotate(col, u_bg_hue), clamp
```

  Champs (coordonnees normalisees corrigees du ratio, phase `u_bg_phase.x`) : `noise` = 2 octaves de `mnoise` qui derivent ; `waves` = `0.5 + 0.5 * sin(...)` le long de la direction `u_bg_angle` (bandes) ; `gradient` = aller-retour de `dot(p - 0.5, dir) + phase * 0.1` comme la palette duo. Pas de flash au kick ici (le kick est traite en tache 3).
- [ ] **Etape 4 : `render.py`** : `"stochastique": 3.0` dans le dict de `u_bg_palette` ; `u_sto` depuis les reglages ; `u_sto2` = (`{"noise": 0, "waves": 1, "gradient": 2}`, invert, rainbow, `self._sto_flick`) ; `u_sto_au` = (0, 0, 0, 0) pour l'instant ; `self._sto_flick = 0.0` dans `__init__` a cote de `_bg_scroll` (ligne 253).
- [ ] **Etape 5 : verifier** : `t.check_bg_stochastic()` -> `ECHECS []` ; puis la verification de rendu existante du motif (palette test octet pour octet, duo, classic) toujours verte (`t.check_render()`, qui contient ces verifications, `check_gl.py:1752`).
- [ ] **Etape 6 : commit** `Fond stochastique : dessin des points (shader)`.

### Tache 3 : modulation par le son

**Fichiers :**
- Modifier : `gl_shaders/scene.frag` (`stochastic_pattern`), `engine/render.py` (autour de la ligne 750)
- Test : `tools/check_gl.py`, `check_bg_stochastic()` (suite)

**Interfaces :**
- Consomme : `u_sto_au`, `Renderer._sto_flick` (tache 2) ; `bg_au` = (basses, aigus, kick, depuis) de `render.py:646` ; `react` (ligne 749) ; `has_kick(src_bg)`.
- Produit : `u_sto_au` = (`bg_au[0] * react`, `bg_au[2] * react`, `state["beats"]` si `react > 0` et `has_kick(src_bg)` sinon 0, 0) ; `self._sto_flick += dt * react * bg_au[1] * 6.0` (renouvellements par seconde par point a aigus = 1, reaction = 1).

- [ ] **Etape 1 : tests**, etats `loud = dict(quiet, bass=1.0, high=1.0, beat=1.0, since_beat=0.0, beats=4)` et `bg_react=1.0` :
  - basses : `lit(bass=1)` > `lit(quiet)` + 0,05 ;
  - kick : image a `beat=1` differente de `beat=0` (ecart moyen > 2) ;
  - aigus : meme `Renderer`, `bg_speed=0`, deux images a `t` = 0 et 1 avec `high=1` : plus de 10 % des blocs 20 x 20 changent ; avec `high=0` : images identiques ;
  - champ `waves` : `beats` 0 puis 1 (reaction 1) -> image differente ;
  - **reaction 0 + etat `loud`** = image identique a `quiet` (`max == 0`) ;
  - tempo : `tempo=0`, `bg_speed=1`, `t` 0 puis 2 -> image figee.
- [ ] **Etape 2 : verifier l'echec** (basses, aigus, kick).
- [ ] **Etape 3 : implementer**. Dans le shader : densite effective `u_sto.w + 0.25 * u_sto_au.x`, taille x `(1 + 0.3 * u_sto_au.x)`, contraste du champ `0.5 + (f - 0.5) * (1 + 0.8 * u_sto_au.y)`, phase des vagues `+ 0.25 * u_sto_au.z`. Dans `render.py` : les deux lignes de l'Interface. Le tempo passe deja par `dt` (`_bg_scroll`) : ne rien ajouter, seulement le verifier.
- [ ] **Etape 4 : verifier** : `t.check_bg_stochastic()` -> `ECHECS []`.
- [ ] **Etape 5 : commit** `Fond stochastique : basses, kick, aigus et tempo`.

### Tache 4 : GUI et grisages

**Fichiers :**
- Modifier : `gui/gui_fond.py:70-89` (palette, sous-bloc)
- Modifier : `gui/gl_gui.py:1376-1383` (regles `gates`)
- Test : `tools/check_gl.py` : etape GUI (`out["g_pal"]`, lignes ~593-597, et sa verification ligne ~1432)

**Interfaces :**
- Consomme : `param_radio`, `param_slider`, `bind_param`, `Zone`, `synced` (via `c`) ; cles de la tache 1.
- Produit : `sto_box` (`tk.Frame` dans `pattern_box`, rendu par `install` et expose dans le contexte de test sous `c["sto_box"]`) ; `synced["bg_sto_field"]` et `synced["bg_sto_invert"]`.

- [ ] **Etape 1 : tests GUI** dans l'etape existante : boucle `g_pal` sur `("classic", "duo", "test", "stochastique")` ; ajouter `out["sto_shown"][pal] = c["sto_box"].winfo_ismapped()` (apres `update_idletasks`). Verifications (a cote de la ligne 1432) :
  - `sto_box` visible seulement pour `stochastique` ;
  - pour `stochastique` : « Couleurs 1 / 2 » et « Teinte » actifs, « Taille des carreaux », « Contraste du damier », « Cadence du damier » gris ; « Angle du degrade » gris avec le champ `noise`, actif avec `waves` ;
  - choisir `stochastique` puis bouger le curseur « Taille des points » ecrit `params["bg_sto_size"]` ; cocher « Inverser » ecrit `params["bg_sto_invert"] == 1.0` ;
  - scene : `capture_scene_now`, changer `bg_sto_density`, rejouer la scene -> la densite capturee revient (point 5 de la revue).
- [ ] **Etape 2 : verifier l'echec** (suite complete en arriere-plan, ~7 min : `PY tools/check_gl.py`).
- [ ] **Etape 3 : implementer**. `("Stochastique", "stochastique")` en 4e choix de la palette et une phrase dans l'info-bulle. `sto_box` = `tk.Frame(pattern_box)` pose apres « Reaction a l'audio », avec sa `Zone` : `param_radio("Champ", "bg_sto_field", (("Bruit", "noise"), ("Vagues", "waves"), ("Degrade", "gradient")))`, curseurs « Taille des points » (3..60, pas 1), « Regularite » (0..1, 0,05), « Variation de taille » (0..1, 0,05), « Densite » (0..1, 0,02), « Arc-en-ciel » (0..1, 0,05), case « Inverser » (`IntVar` + `bind_param(..., float)` comme `holo_on`). Visible seulement pour la palette (trace sur `palette_var` : `grid()` / `grid_remove()`). Gates : « Couleurs 1 / 2 » actif pour `duo` OU `stochastique` ; « Angle du degrade » actif pour `duo`, ou `stochastique` avec un champ autre que `noise` ; « Taille des carreaux », « Contraste du damier », « Cadence du damier » actifs seulement pour `classic` et `duo` ; ajouter la variable du champ aux traces qui appellent `gates.refresh()`.
- [ ] **Etape 4 : verifier** : suite complete -> code 0 (ou 3 seulement si les echecs sont tous dans `FRAGILES` ; relancer alors une fois). Verifier aussi que la fenetre reste sous 700 px (test existant).
- [ ] **Etape 5 : commit** `Fond stochastique : bloc de reglages et grisages`.

### Tache 5 : Randomizer

**Fichiers :**
- Modifier : `core/randomizer.py:226-238` (`random_background_params`)
- Test : `tools/check_gl.py`, `check_randomizer` (lignes ~6419-6424)

**Interfaces :**
- Consomme : cles de la tache 1.
- Produit : `random_background_params(rng)` rend aussi `bg_palette="stochastique"` (un tirage sur quatre : `("classic", "duo", "duo", "stochastique")`), avec `bg_color1` / `bg_color2` (meme tirage que `duo`) et `bg_sto_field` (choix des trois), `bg_sto_size` 5..30, `bg_sto_regular` 0,4..1, `bg_sto_vary` 0..0,8, `bg_sto_density` 0,3..0,7, `bg_sto_invert` 0 ou 1, `bg_sto_rainbow` 0 (une fois sur deux) sinon 0..0,6.

- [ ] **Etape 1 : test qui echoue** : remplacer `v["bg_palette"] in ("classic", "duo")` par `("classic", "duo", "stochastique")`, la condition des couleurs par `v["bg_palette"] == "classic" or ...` ; ajouter :

```python
vals = [rz.random_background_params(rnd_mod.Random(s)) for s in range(300)]
st = [v for v in vals if v["bg_palette"] == "stochastique"]
check(len(st) > 30 and all(5 <= v["bg_sto_size"] <= 30 and 0.4 <= v["bg_sto_regular"] <= 1 and 0.3 <= v["bg_sto_density"] <= 0.7
                           and v["bg_sto_field"] in ("noise", "waves", "gradient") for v in st)
      and all(gl.coerce_params(v)[k] == v[k] for v in st for k in v),        # aucune valeur ramenee par la validation
      "fond aleatoire: la palette stochastique est tiree, dans les plages")
```

- [ ] **Etape 2 : verifier l'echec** : `t.check_randomizer()` -> echec « la palette stochastique est tiree ».
- [ ] **Etape 3 : implementer** (Interface ci-dessus ; les cles `bg_sto_*` seulement pour cette palette).
- [ ] **Etape 4 : verifier** : `t.check_randomizer()` et `t.check_params_schema()` (300 tirages dans les plages) -> `ECHECS []` ; `PY tools/check_gl.py --pur`.
- [ ] **Etape 5 : commit** `Fond stochastique : tire par le Full Randomizer`.

### Tache 6 : mesure, documentation, PR

**Fichiers :**
- Modifier : `CLAUDE.md` (sous-section « Fond genere », phrase `bg_mode` et `check_bg_stochastic`), `README.md` (fond genere)

- [ ] **Etape 1 : mesure GPU** sur le poste de dev : script jetable dans le dossier scratchpad qui remplace `load_params` dans le module qui l'appelle (voir « Charge GPU » de `CLAUDE.md`) par les reglages par defaut + `bg_mode="pattern"`, `bg_palette="stochastique"`, champ `noise`, puis `audio2wave_gl.py --synthetic --stats --max-seconds 13 --render-size 1920x1080` et `1280x720`. Noter le temps GPU, comparer a la palette `duo`. Si l'ajout depasse 3 ms a 1080 p : le dire dans la PR et evaluer le plan B de la spec (champ en basse resolution) avant la fusion ; sinon rien dans `core/safety.py`.
- [ ] **Etape 2 : documentation** : paragraphe « Palette stochastique » (reglages, algorithme en trois lignes, son, cout mesure, limite : approximation du bruit bleu) dans `CLAUDE.md` ; une ligne dans `README.md`.
- [ ] **Etape 3 : verification finale** : `PY tools/check_gl.py --pur` puis la suite complete -> code 0 (ou 3 + relance).
- [ ] **Etape 4 : commit, push, PR** (`gh pr create`), attendre les trois jobs (pur, complet, portable), fusionner (`gh pr merge --merge`), `git pull --ff-only origin main` dans le depot principal, retirer le worktree et la branche.
- [ ] **Etape 5 : demander a l'utilisateur de juger le rendu a l'oeil** (sur l'ecran et si possible au projecteur) avant la PR 2 (metaballes, hors de ce plan).
