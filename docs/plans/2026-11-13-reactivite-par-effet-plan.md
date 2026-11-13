# Reactivite par effet : plan d'implementation

> **Pour les executants** : sous-skill requis : superpowers:subagent-driven-development ou superpowers:executing-plans, tache par tache. Les etapes sont des cases a cocher (`- [ ]`).

**But** : en mode auto, chaque effet (ondulation, onde de choc, glitch, aberration) de chaque couche recoit sa propre source audio et son propre dosage selon la phase du morceau, le logo etant decale du fond.

**Architecture** : un module pur (`core/audio_layers.py`) porte le tableau phase x couche x effet et un `EffectBank` (fondus + dosages lisses) ; `Renderer.draw` calcule 4 vecteurs `(bass, high, beat, since)` par couche au lieu d'un et les envoie au shader (`u_au_bg[4]`, `u_au_logo[4]`) ; `layer_fx` lit le vecteur de chaque effet. Hors mode auto les 4 vecteurs sont identiques a l'ancien vecteur de couche.

**Stack** : Python 3.12 pur (`core/`), numpy / moderngl (rendu), GLSL (`gl_shaders/post.frag`), tests dans `tools/check_gl.py`.

**Spec** : `docs/plans/2026-11-13-reactivite-par-effet-design.md`

## Contraintes globales

- Documentation, commentaires et messages en francais SANS accents ; `core/` reste stdlib seulement (aucun import moteur / GUI / numpy).
- Mode manuel (source de couche choisie a la main) et auto sans phase connue : image identique pixel pour pixel a celle d'avant (comparaison avec un rendu de reference).
- Aucun nouveau reglage pour l'utilisateur ; reglages de couche existants (`src_*`, `sens_*`, `lag_*`, `smooth_*`, `fx_link`) inchanges.
- Constantes d'effet et dosages en tete de `core/audio_layers.py` (reglables a l'oeil). Une nouvelle verification `check_*` s'ajoute a `SUITE` de `tools/check_gl.py` (et a `PURES` si pure).
- Hors perimetre : reactions CPU du logo (pulsation, tremblement, contour), halo, fonte, cellules, mosaique, strobe, foudre : ils gardent le vecteur de COUCHE.
- Un git propre : branche depuis `origin/main`, jamais de commit sur `main`.

## Points de vigilance (Review Focus)

1. Passage repos -> actif -> repos (changements de phase rapides, aller-retour en moins de 1 s) : aucun saut, aucun NaN.
2. `since` d'un effet au repos vaut `NO_KICK` : l'onde de choc ne part jamais d'un effet au repos.
3. Phase qui disparait (`auto_phase` coupe en route, phase inconnue) : retour fondu a l'ancien comportement, pas de sursaut.
4. Source de couche changee a la main pendant qu'un fondu auto est en cours : prise en compte immediate, sans memoire.
5. Fond lie manuel + logo lie : le logo suit toujours le fond (comportement d'avant) ; seul le cas fond AUTO donne sa colonne au logo.

---

### Tache 1 : tableau et banque d'effets (pur)

**Fichiers**
- Modifier : `core/audio_layers.py`
- Tests : `tools/check_gl.py` (`check_audio_layers_pure`)

**Interfaces**
- Produit : `EFFECTS = ("wobble", "ripple", "glitch", "chroma")` ; `DERIVE = "derive"` ; `FX_TABLE: dict[phase, dict[layer, dict[effect, tuple[str, float | str]]]]` (valeur = `(source, dosage)`, dosage `0` / `0..1` / `DERIVE`) ;
  `auto_fx(phase, since_hit=None) -> dict[str, dict[str, tuple[str, float | str]]] | None` (None : phase inconnue ou absente = comportement d'avant) ;
  `class EffectBank` avec `levels(now: float, dt: float, state: dict, layer: str, assign: dict[str, tuple], kick_layer: str, fade: bool) -> list[tuple[float, float, float, float]]` (4 vecteurs dans l'ordre `EFFECTS`) et `active(layer) -> list[tuple[str, str, float]]` (effet, source, dosage lisse : pour le voyant) ;
  constantes `DOSE_TAU_S = 1.0`, `DRIFT_WOBBLE = 0.25`, `DRIFT_CHROMA = 0.15`, `DRIFT_PERIOD_S = 20.0`.
- Consomme : `layer_levels`, `SourceMixer`, `NO_KICK`, `FADE_S`.

- [ ] **Etape 1 : ecrire les tests (rouges)** dans `check_audio_layers_pure` : (a) `FX_TABLE` couvre les 6 phases (`silence`, `ambient`, `groove`, `build`, `break`, `peak`) x `bg` / `logo` x 4 effets, sources dans `SOURCES`, dosages dans `[0, 1]` ou `DERIVE` ; (b) `silence` / `ambient` : aucun effet actif hors derive ; `break` : fond entierement au repos, seul le logo `wobble` actif ; (c) hors `peak`, au plus 2 effets actifs (dosage > 0, derive non compte) par couche ; au `peak` les 4 effets sont actifs et chaque effet l'est dans une seule couche ; (d) `auto_fx(None)` et `auto_fx("inconnue")` rendent None ; (e) `EffectBank.levels` : effet au repos = `(0, 0, 0, NO_KICK)` ; effet qui passe de repos a dosage 1 monte progressivement (a `DOSE_TAU_S` / 2 : entre 0,2 et 0,8 du plein), jamais de saut > 0,25 par pas de 1/60 s ; derive : `wobble` > 0 sans son et <= `DRIFT_WOBBLE`, `chroma` idem avec `DRIFT_CHROMA`, `ripple` / `glitch` = repos ; (f) aller-retour repos -> plein -> repos en 0,5 s : aucune valeur hors `[0, 1]`, aucun NaN ; (g) `fade=False` : le changement de source est immediat.
- [ ] **Etape 2 : lancer** `.venv\Scripts\python.exe tools/check_gl.py --pur` : les nouvelles verifications ECHOUENT (noms absents).
- [ ] **Etape 3 : implementer** `FX_TABLE` (valeurs des deux tableaux de la spec §4.2, la phase `silence` = `ambient`), `auto_fx` (comme `auto_sources` : en groove le logo `chroma` suit `frappes` si une frappe date de moins de `HIT_RECENT_S`, sinon `aigus`) et `EffectBank` : un `SourceMixer` et un dosage lisse (constante `DOSE_TAU_S`) par couple (couche, effet) ; vecteur = `layer_levels(source) x dosage lisse`, sauf `since` (non multiplie) qui vaut `NO_KICK` quand le dosage lisse < 0,02 ; la derive ajoute un vecteur independant de la source (`wobble` : composante 0, `chroma` : composante 2, sinus de periode `DRIFT_PERIOD_S` en `now`).
- [ ] **Etape 4 : relancer** `--pur` : tout est vert (sauf l'echec local connu `scene: capture sans l'entree audio`, propre au poste).
- [ ] **Etape 5 : commit** `git add core/audio_layers.py tools/check_gl.py` puis `git commit -m "Reactivite par effet : tableau phase x couche x effet et EffectBank (pur)"`.

### Tache 2 : rendu et shader

**Fichiers**
- Modifier : `engine/render.py` (`Renderer.draw`, creation des filtres), `gl_shaders/post.frag` (`layer_fx`, uniforms)
- Tests : `tools/check_gl.py` (`check_audio_layers`, rendu en pixels)

**Interfaces**
- Consomme : `auto_fx`, `EffectBank`, `EFFECTS` (tache 1) ; `LayerFilter.step(t, levels, lag, smooth)`.
- Produit : uniforms `vec4 u_au_bg[4]` et `vec4 u_au_logo[4]` (indices 0 `wobble`, 1 `ripple`, 2 `glitch`, 3 `chroma`) ; `layer_fx(sampler2D tex, vec4 fx, vec2 center, float salt, vec2 disp, vec4 au[4])` ; `Renderer.fx_now: dict[str, list[tuple[str, str, float]]]` (cle `bg` / `logo`, valeur = `EffectBank.active`) ; `Renderer._fx_filters` (2 x 4 `LayerFilter`).

- [ ] **Etape 1 : ecrire les tests en pixels (rouges)** dans `check_audio_layers` (memes images, memes outils que les tests existants de wobble / aberration par source) : (a) mode manuel (`src_bg` = `tout`, puis `basses`, `kick`) : image identique a celle du rendu de reference d'avant (ecart 0), pour le fond et pour le logo ; (b) auto sans phase connue : identique a `tout` ; (c) auto en `groove` avec un kick : le fond bouge par l'ondulation / l'onde de choc, l'aberration du fond ne change rien (pixels d'un fond a bords nets inchanges hors ondulation) ; (d) logo decale : un kick en `groove` ne deforme pas le logo (pas de wobble ni de choc sur la couche logo), alors qu'une frappe aigue deplace son aberration ; (e) derive : en `ambient`, sans son, deux images separees de 3 s different (mouvement non nul) et l'ecart reste borne ; (f) `fx_link` = 0, `src_logo` manuel : le logo suit sa source manuelle, les effets du fond restent auto.
- [ ] **Etape 2 : lancer** la suite du moteur : les nouveaux tests echouent.
- [ ] **Etape 3 : shader** : remplacer `uniform vec4 u_au_bg;` / `u_au_logo;` par des tableaux de 4 ; dans `layer_fx` lire `au[0].x` (ondulation), `au[1].w` (since de l'onde de choc), `au[2].z` / `au[2].y` (glitch), `au[3].z` (aberration) a la place des composantes de l'unique `au` ; les appels fond / logo passent `u_au_bg` / `u_au_logo`. Ecrire les tableaux comme `u_pulse` de `foudre` (un `_set` avec 16 floats).
- [ ] **Etape 4 : `Renderer.draw`** : calculer `fx_bg` et `fx_logo` (liste de 4 vecteurs) : en auto avec phase connue (`auto_fx` non None) et couche en auto (`bg_auto` pour le fond ; pour le logo `bg_auto` s'il est lie, `logo_auto` sinon) : `EffectBank.levels(...)` de la couche, sinon 4 copies du vecteur de couche actuel (`self._src_mix[...]`, comme aujourd'hui). Chaque vecteur passe par son `LayerFilter` (`_fx_filters`, memes `lag_*` / `smooth_*` que la couche). `bg_au` / `lg_au` d'avant restent calcules (reactions CPU du logo : `lg_bass`, `lg_high`, `lg_beat`). Publier `self.fx_now`.
- [ ] **Etape 5 : relancer** les tests de la suite moteur (`--groupe` ou la suite complete en arriere-plan) : verts ; en particulier les tests existants de wobble / aberration / glitch par couche (`check_audio_layers`) doivent passer SANS modification.
- [ ] **Etape 6 : commit** `git add engine/render.py gl_shaders/post.frag tools/check_gl.py` puis `git commit -m "Reactivite par effet : 4 vecteurs audio par couche dans le rendu et le shader"`.

### Tache 3 : voyant et documentation

**Fichiers**
- Modifier : `core/explain.py`, `engine/projection.py` (publication du statut), `gui/gl_gui.py` ou `gui/gui_vj.py` (libelle de l'onglet *Audio* qui affiche deja les sources), `CLAUDE.md`
- Tests : `tools/check_gl.py`

**Interfaces**
- Consomme : `Renderer.fx_now` (tache 2).
- Produit : `explain.fx_text(layer_name: str, active: list[tuple[str, str, float]]) -> str` (ex. `fond : ondulation <- basses 60 % | choc <- kick 50 % | aberration, glitch : repos`) ; `s.status["fx_now"]`.

- [ ] **Etape 1 : tests (rouges)** : `fx_text` pour chaque phase du tableau (texte non vide, nomme les effets actifs et regroupe ceux au repos), `active` vide = « tout au repos », derive = « derive lente ».
- [ ] **Etape 2 : implementer** `fx_text`, publier `s.status["fx_now"]` dans `engine/projection.py` a cote de `s.status["sources"]`, afficher deux lignes (fond, logo) dans l'onglet *Audio* sous la ligne « sources : ... » existante (meme planificateur de 250 ms que les autres voyants ; en mode manuel ou sans phase : « effets : source de la couche »).
- [ ] **Etape 3 : verifier** `--pur` puis la suite complete en arriere-plan (~7 min).
- [ ] **Etape 4 : documenter** `CLAUDE.md` : paragraphe dans « Sources audio par couche » (tableau auto par effet, dosages, derive, fondus, logo decale, voyant, limites : tableau = hypothese a regler a l'oeil, reactions CPU du logo non concernees).
- [ ] **Etape 5 : commit** `git add core/explain.py engine/projection.py gui tools/check_gl.py CLAUDE.md` puis `git commit -m "Reactivite par effet : voyant par effet et documentation"`.

### Verification finale

- [ ] `.venv\Scripts\python.exe tools/check_gl.py --pur` puis la suite complete en arriere-plan ; seuls les echecs locaux connus peuvent rester (`scene: capture sans l'entree audio`, `selecteur Source...`, `audio coupe: image identique a la video...`).
- [ ] PR, CI vert, merge (instruction permanente). **Non verifie** : le rendu sur de la vraie musique au videoprojecteur ; passe de reglage des dosages apres livraison.
