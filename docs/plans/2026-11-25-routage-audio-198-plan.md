# Routage audio par effet (#198) : plan d'implementation

> **Pour les agents** : sous-skill requis : superpowers:subagent-driven-development (recommande) ou superpowers:executing-plans. Les etapes utilisent des cases `- [ ]`.

**But** : chaque chose qui reagit au son (environ 25 lignes) peut recevoir sa propre source audio et son dosage, reglables dans un onglet Routage et par une pastille a cote de l'effet, sans rien changer aux scenes existantes.

**Architecture** : `core/routing.py` (pur) porte la table des lignes, la resolution et `RouteBank` (un `SourceMixer` + `LayerFilter` par ligne modifiee). Deux reglages par ligne (`rt_<id>`, `rg_<id>`) dans la table unique. `Renderer.draw` remplace, ligne par ligne, le vecteur de couche par le vecteur de la ligne SEULEMENT si elle est modifiee. L'interface (`gui/gui_routing.py`) edite les memes variables Tk depuis le tableau et depuis les pastilles.

**Pile** : Python 3.12, Tk, moderngl (rien de nouveau), tests dans `tools/check_gl.py`.

**Spec** : [2026-11-25-routage-audio-198.md](2026-11-25-routage-audio-198.md)

## Contraintes globales (reprises de la spec et de CLAUDE.md)

- Docs, commentaires, messages CLI en francais SANS accents. `core/` : stdlib seulement, aucun import du moteur / GUI / numpy / GL / PIL.
- **Zero perte** : routes par defaut = image identique a avant, pixel pour pixel. Aucun reglage existant retire ou renomme.
- Un nouveau test : son nom dans `SUITE` (et dans `PURES` s'il est pur) de `tools/check_gl.py`.
- Largeur constante de l'interface : pastilles et lignes a largeur fixe, jamais d'element qui se decale.
- TDD pour `core/` (test d'abord) ; pas de TDD strict pour GUI / shaders / moteur, mais un test de comportement par tache.
- Un seul `check_gl.py` a la fois. Ce qui demande du materiel est dit « non verifie ». Pas de commit sur `main`. Branche : `routage-audio-198` (existe). Trailer de commit : `Co-Authored-By: Claude Sonnet 5.5 <noreply@anthropic.com>`.
- Livraison en **3 PR** depuis `origin/main` (une branche par PR : `routage-audio-198` pour la PR 1 ; `-198-onglet`, `-198-lignes` ensuite, apres fusion de la precedente). Pas de PR en parallele (memes fichiers : `engine/render.py`, `core/params_schema.py`, `tools/check_gl.py`).

## Rulings de conception (a lire avant de commencer)

1. **Une ligne modifiee gagne sur tout** : pour les 4 effets shader, la ligne modifiee est appliquee APRES `ripple_on_downbeat` et `accent_vectors` (elle est explicite). Une onde de choc routee sur `kick` part donc a chaque kick meme si `ripple_beat` = mesure ; la source `mesure` reste disponible. Ligne par defaut : comportement d'avant, renforts compris.
2. **`herite` + dosage != 1** : le vecteur de la couche (deja retarde / lisse) multiplie par le dosage, sans nouveau `SourceMixer`.
3. **`auto` sur une ligne** : la source de la couche du tableau phase x couche (`auto_sources`), avec fondu de 1,5 s (`SourceMixer`, `fade=True`). Sans phase connue : `tout`. Ce n'est PAS la table par effet (`FX_TABLE`) : elle reste reservee aux lignes laissees a `herite` quand la couche est en auto.
4. **Lignes informatives** : `strobe`, `foudre`, `noise` decident deja seuls (phases, grille, cases propres) : lignes en lecture seule dans l'onglet (texte du voyant), PAS de menu. Le reste (`breath`, `hue`) est routable.
5. Mosaique et texte : pas de ligne (aucune reactivite audio aujourd'hui).

## Plan des fichiers

- Creer `core/routing.py` : table des lignes, schema des reglages, `route_of`, `is_default`, `RouteBank`, familles de menu.
- Modifier `core/params_schema.py` : ajoute `routing.route_params()` a la table.
- Modifier `engine/render.py` : `Renderer._routes` (`RouteBank`), application dans `draw`, `routes_now`.
- Modifier `engine/projection.py` : publie `s.status["routes_now"]`.
- Creer `gui/gui_routing.py` : onglet Routage, mini-spectre, pastille.
- Modifier `gui/gl_gui.py`, `gui/gui_effects.py`, `gui/gui_logo.py`, `gui/gui_fond.py`, `gui/gui_audio.py` : onglet et pastilles.
- Modifier `tools/check_gl.py` : `check_routage` (pur), `check_routage_rendu`, `check_routage_gui`.
- Docs : `docs/fonctions/audio.md`, `docs/fonctions/gui.md`, `core/CLAUDE.md` et `gui/CLAUDE.md` (pointeurs).

## Review Focus (cas que les tests de taches n'exercent pas d'office)

- Une scene ancienne (sans `rt_*` ni `rg_*`) ou un `gl_params.json` vieux : doit se charger et rendre pareil (couvert tache 2 et 4).
- Un nom de source inconnu ou supprime dans une scene (`rt_x` = « fin64 ») : doit retomber sur `herite`, sans exception (tache 2).
- Source `auto` avec `auto_phase` coupe ou phase absente : `tout`, pas de crash (tache 3).
- Retard (`lag_*`) change a chaud sur une ligne routee : silence de `lag` s puis reprise, pas de saut (tache 3).
- 25 lignes modifiees a la fois : budget CPU de l'image tenu, mesure `PerfGuard` (tache 5).

---

# PR 1 : modele et moteur, 8 effets shader

### Tache 1 : `core/routing.py` (table, resolution)

**Fichiers** : creer `core/routing.py` ; tester dans `tools/check_gl.py` (`check_routage`).

**Interfaces** :
- Produit : `Route = namedtuple("Route", "id zone layer label kind")`, `ROUTES: tuple[Route, ...]` (ordre = ordre d'affichage), `ZONES = ("fond", "logo", "global")`, `ROUTE_SOURCES = ("herite", *audio_layers.SOURCES)`, `SOURCE_FAMILIES: tuple[tuple[str, tuple[str, ...]], ...]` (libelle de famille, sources : Reglage, Niveaux, Bandes, Rythme, Structure), `route_params() -> list[Param]` (importe `_p` : voir tache 2), `source_key(rid) -> "rt_" + rid`, `dose_key(rid) -> "rg_" + rid`, `route_of(params, rid) -> (source: str, dose: float)` (source inconnue -> `"herite"`, dose bornee 0..2), `is_default(params, rid=None) -> bool` (une ligne, ou toutes), `routed_ids(params) -> list[str]`, `FX_LINES: dict[str, tuple[str, int]]` (`"bg_wobble" -> ("bg", 0)` ... `"lg_chroma" -> ("logo", 3)`, indices = ordre de `audio_layers.EFFECTS`).
- Consomme : `core.audio_layers.SOURCES`, `EFFECTS`, `LAYERS`.
- `kind` vaut `"fx"` (vecteur shader), `"vec"` (un vecteur de couche routable : motif, logo CPU, halo, fonte, cellules, respiration, teinte) ou `"info"` (strobe, foudre, noise : lecture seule).

- [ ] **Etape 1 : ecrire `check_routage` (rouge)** : assertions : ids uniques, 8 lignes `fx` exactement (`bg_wobble`, `bg_ripple`, `bg_glitch`, `bg_chroma`, `lg_wobble`, `lg_ripple`, `lg_glitch`, `lg_chroma`), au total les 23 lignes de la spec (lister les ids dans le test), `ROUTE_SOURCES[0] == "herite"` et contient chaque valeur de `SOURCES`, `route_of({}, "bg_wobble") == ("herite", 1.0)`, `route_of({"rt_bg_wobble": "fin64"}, "bg_wobble")[0] == "herite"`, `route_of({"rg_bg_wobble": 9}, "bg_wobble")[1] == 2.0`, `is_default({})` vrai et `is_default({"rt_bg_wobble": "basses"})` faux, `routed_ids` ordonne comme `ROUTES`, `SOURCE_FAMILIES` couvre toutes les sources sauf `herite` une seule fois chacune (herite dans la famille Reglage).
- [ ] **Etape 2 : lancer** : `.venv\Scripts\python.exe tools/check_gl.py --seulement routage` -> Attendu : echec (module absent). Ajouter `check_routage` a `SUITE` ET `PURES`.
- [ ] **Etape 3 : implementer `core/routing.py`** avec les signatures ci-dessus (table `ROUTES` ecrite en dur, libelles francais sans accents).
- [ ] **Etape 4 : relancer** -> Attendu : `check_routage` passe. Puis `--pur`, `check_core_purity` doit passer.
- [ ] **Etape 5 : commit** `Routage : table des lignes et resolution (Refs #198)`.

### Tache 2 : reglages dans la table unique

**Fichiers** : modifier `core/params_schema.py` ; tests dans `check_routage` et `check_params_schema`.

**Interfaces** : produit 2 x 23 = 46 reglages (`rt_<id>` : `choices=ROUTE_SOURCES`, defaut `"herite"` ; `rg_<id>` : 0.0 a 2.0, defaut 1.0) ; portee `look` ; les lignes `info` n'en ont pas (pas de menu).

- [ ] **Etape 1 : tests (rouges)** : `DEFAULT_PARAMS` contient les cles pour chaque ligne non `info` ; `coerce_params({"rt_bg_wobble": "basses", "rg_bg_wobble": 1.5})` conserve ; `coerce_params({"rt_bg_wobble": "fin64"})` retombe a `herite` ; `coerce_params({})` ne rend pas d'erreur et `is_default` vrai ; un preset overlay et une scene sans ces cles se chargent (`core.scenes` : charger une scene d'exemple existante) ; `rt_*` / `rg_*` sont dans les cles `look` (copiees par presets et scenes).
- [ ] **Etape 2 : lancer** `--seulement params_schema` -> echec.
- [ ] **Etape 3 : brancher** `routing.route_params()` dans la table (`core/params_schema.py` : etendre la liste des `_p`) ; verifier l'absence d'import circulaire (`routing` importe `audio_layers`, `params_schema` importe deja `audio_layers`).
- [ ] **Etape 4 : relancer** `--pur` -> Attendu : seuls les 2 echecs d'environnement connus (« scene: capture sans l'entree audio », « selecteur Source ») si presents sur `main` propre.
- [ ] **Etape 5 : commit** `Routage : reglages rt_ et rg_ (Refs #198)`.

### Tache 3 : `RouteBank` (resolution temps reel)

**Fichiers** : `core/routing.py` ; tests dans `check_routage`.

**Interfaces** :
- Produit : `class RouteBank` : `__init__()` ; `vector(now: float, state: dict, params: dict, rid: str, base: tuple, auto_source: str, lag: float, smooth: float) -> tuple` (4 valeurs `(bass, high, beat, since)`) ; `info(rid) -> tuple[str, float] | None` (source resolue, niveau courant pour l'onglet).
- Comportement : ligne par defaut -> `base` tel quel (identite, meme objet) ; `herite` + dose -> `(b*d, h*d, be*d, since)` (ruling 2) ; source explicite -> `SourceMixer` propre a la ligne (`fade` vrai seulement pour `auto`, resolu par `auto_source`), `LayerFilter` propre (`lag`, `smooth`), puis dosage ; `kick_layer` = couche de la ligne (`bg` ou `logo`, lu dans `ROUTES`).
- Consomme : `audio_layers.SourceMixer`, `LayerFilter`, `layer_levels`.

- [ ] **Etape 1 : tests (rouges)** : defaut = identite (`is`) ; `herite` dose 0.5 = moitie des niveaux, `since` inchange ; source `kick` sur une ligne : `(0, 0, beat, since)` du `state`, independant de `base` ; deux lignes d'une meme couche sur des sources differentes ne s'influencent pas ; `auto` avec `auto_source="basses"` = vecteur des basses, changer `auto_source` fait un fondu (valeur intermediaire a 0,75 s) ; `lag=0.2` : silence (`SILENT`) pendant 0,2 s puis le signal ; changer `lag` a chaud : pas de valeur > max du signal ; `state` sans cles optionnelles (`bands`, `pulse_since`) ne leve pas ; dose 0 = vecteur nul avec `since` = `NO_KICK`.
- [ ] **Etape 2 : lancer** -> echec. **Etape 3 : implementer** `RouteBank` (dictionnaires `{rid: (SourceMixer, LayerFilter)}` crees a la demande). **Etape 4 : relancer** -> passe. **Etape 5 : commit** `Routage : RouteBank (Refs #198)`.

### Tache 4 : brancher les 8 effets dans `Renderer.draw`

**Fichiers** : modifier `engine/render.py` (autour de `draw`, apres `accent_vectors`, voir lignes ~1150-1172) et `engine/projection.py` ; tests `check_routage_rendu` (pixels, dans `SUITE`, pas dans `PURES`).

**Interfaces** : `Renderer._routes = RouteBank()` ; dans `draw`, apres le bloc des renforts de fin de cycle : pour chaque `rid, (layer, i)` de `routing.FX_LINES` non par defaut, `au_<layer>_fx[i] = self._routes.vector(t, state, p, rid, au_<layer>_fx[i], auto_src_layer, p["lag_..."], p["smooth_..."])` (ruling 1 : apres le « 1 » de mesure et les renforts) ; `auto_src_layer` = `table_bg` / `table_logo` de `auto_sources` ; `Renderer.routes_now: dict[str, tuple[str, float]]` ; `projection.py` publie `s.status["routes_now"]`. Cerveau : ne rien changer (il lit `au_*_fx`, il verra les vecteurs routes).

- [ ] **Etape 1 : tests de rendu (rouges)** avec le contexte standalone habituel : (a) rendu avec `DEFAULT_PARAMS` + `rt_*` absents == rendu avant modification (reference calculee avec `routes` neutralisees) : image identique octet pour octet sur 3 images ; (b) ondulation du fond sur `basses` et aberration du logo sur `kick`, avec un `state` ou seuls les basses montent : la difference d'image par rapport a la reference ne se trouve que dans la zone de l'ondulation, l'aberration reste a zero ; (c) dosage 0 sur `bg_wobble` : image == image sans ondulation ; (d) source explicite sur `lg_glitch` pendant que la couche logo est en auto : seule cette ligne change.
- [ ] **Etape 2 : lancer** `--seulement routage_rendu` -> (a) passe deja (rien branche), (b)-(d) echouent.
- [ ] **Etape 3 : implementer** l'integration et `routes_now`.
- [ ] **Etape 4 : relancer** `--seulement routage`, puis `--seulement audio_layers`, `--seulement effets_auto`, `--seulement cerveau` -> doivent tous passer (zero perte).
- [ ] **Etape 5 : mesurer** : micro-test (cf. `tools/mesure_cerveau.py` comme modele) avec 8 lignes modifiees : cout moyen de `draw` CPU < 0,2 ms de plus ; consigner la mesure dans la spec.
- [ ] **Etape 6 : commit** `Routage : les 8 effets du shader (Refs #198)`.

### Tache 5 : doc et PR 1

- [ ] **Etape 1** : section « Routage audio par effet » dans `docs/fonctions/audio.md` (modele, resolution, ruling 1 a 3, zero perte, mesure) ; pointeur `routing.py` dans `core/CLAUDE.md` vers `audio.md`.
- [ ] **Etape 2** : `check_gl.py --pur`, puis `--seulement routage`, puis la suite complete en arriere-plan (~7 min). Regarder `check_claude_md` (CLAUDE.md <= 300 lignes).
- [ ] **Etape 3** : PR « Routage audio par effet : modele et moteur (Refs #198) » (gabarit, ligne « Non verifie » : ressenti sur vraie musique). **Pas de fusion sans CI verte ; relancer seulement les jobs en echec pour ffmpeg/choco.** Carte #198 : « Spec a valider » deja posee ; apres fusion elle reste « En cours » (PR 2 et 3 a venir).

---

# PR 2 : onglet Routage et pastilles (8 effets)

### Tache 6 : aides pures pour l'interface (`core/routing.py`)

**Interfaces** : `source_label(src: str) -> str` (libelles francais sans accents : `"snare"` -> `"Caisse claire"`, `"herite"` -> `"Comme la couche"`) ; `pill_text(src, dose, layer_label) -> str` (« Basses 80 % », « Comme le fond » ; dose 1.0 = pas de pourcentage) ; `auto_hint(phase, layer) -> str` (« Auto : groove -> kick ») depuis `auto_sources` ; `band_indices(src) -> tuple[int, ...] | None` (`BAND_SOURCES`, `niveaux` -> `None`) ; `line_enabled(params, rid) -> bool` (la ligne est-elle active : ex. `bg_wobble` selon `fx_on[0]`).

- [ ] Etapes TDD dans `check_routage` : un test par fonction (rouge, implementation, vert), commit `Routage : textes et aides de l'interface (Refs #198)`.

### Tache 7 : `gui/gui_routing.py` : pastille et onglet

**Fichiers** : creer `gui/gui_routing.py` ; modifier `gui/gl_gui.py` (declaration de l'onglet, variables) ; modifier `gui/gui_effects.py` et `gui/gui_logo.py` (pastilles) ; `gui/CLAUDE.md` (pointeur vers `gui.md`) ; tests `check_routage_gui` (dans `SUITE`).

**Interfaces** :
- `routing_pill(parent, ctx, rid) -> widget` : largeur fixe, texte `pill_text`, bleue si la ligne n'est pas par defaut ; clic = menu (familles de `SOURCE_FAMILIES`, separateurs entre familles, comme `gui_effects.source_menu`) + curseur de dosage 0..200 % ; lit / ecrit `ctx.vars["rt_<id>"]` et `["rg_<id>"]` (memes variables Tk que celles des autres reglages : voir `source_menu` dans `gui/gui_effects.py` pour le motif a reprendre).
- `build_routing_tab(parent, ctx)` : en-tete `SpectrumStrip` (canvas 20 Hz - 20 kHz, plages des `band_indices`, activite en direct depuis `s.status`), tableau Fond / Logo / Global (libelle, `routing_pill` etendu avec menu en ligne, barre de niveau depuis `s.status["routes_now"]`, curseur de dosage, bouton de remise a zero, « Tout remettre » par zone) ; les lignes `info` affichent seulement le texte du voyant.
- Le menu actuel « Le fond / Le logo reagit a » est conserve ; le survol d'une ligne surligne sa plage dans le spectre.

- [ ] **Etape 1 : tests GUI (rouges)** (fenetre reelle comme les autres `check_gui*`) : choisir `Basses` via la pastille de `bg_wobble` -> `ctx.vars["rt_bg_wobble"]` vaut `basses` ET la ligne de l'onglet l'affiche (meme variable) ; l'inverse ; « Tout remettre » -> `is_default` ; la largeur de l'onglet et des pastilles ne change pas quand le texte varie (regle « les boutons ne bougent pas ») ; une scene sauvee puis rechargee garde les routes ; effet coupe -> pastille grisee (`line_enabled`).
- [ ] **Etape 2 : implementer** `gui_routing.py`, declarer l'onglet, poser la pastille a cote du curseur d'intensite de chacun des 8 effets (fond : `gui_effects`, logo : `gui_logo`).
- [ ] **Etape 3 : verifier** `--seulement routage`, `--seulement gui` (largeur constante, `FRAGILES` relances seuls), `check_claude_md`.
- [ ] **Etape 4 : doc** `docs/fonctions/gui.md` (onglet, pastille, variables partagees) ; capture de l'onglet dans la PR ; **non verifie** : lisibilite au projecteur.
- [ ] **Etape 5 : commit** + PR 2 (nouvelle branche `routage-audio-198-onglet` depuis `origin/main` apres fusion de la PR 1 ; `Refs #198`).

---

# PR 3 : les autres lignes

### Tache 8 : motif du fond (`bg_flash`, `bg_scroll`, `bg_sparkle`)

**Fichiers** : `engine/render.py` (lignes ~1244, 1300-1312 et `_labyrinth` ~1019-1042) ; tests `check_routage_rendu`.

Mapping exact (chaque ligne lit son vecteur `v = routes.vector(...)` calcule avant son premier usage, `base = bg_au`) : `bg_flash` -> `u_beat` (`v[2]`) ; `bg_scroll` -> defilement `bg_au[0]` (`_bg_scroll`, labyrinthe `bass`), `has_kick(src_bg)` -> source de la ligne si elle est explicite (la bascule du damier suit alors cette source) ; `bg_sparkle` -> `_sto_flick` (`bg_au[1]`) et `u_sto_au` (`v[0]`, `v[2]`). Ligne par defaut : identique a avant.

- [ ] TDD de rendu : (a) identite avec routes par defaut (hash des 3 images) ; (b) flash sur `frappes` : le motif ne flashe que sur une frappe ; (c) scroll sur `rien` : `_bg_scroll` n'avance plus avec les basses ; (d) damier : bascule selon la source de `bg_scroll`. Commit `Routage : motif du fond (Refs #198)`.

### Tache 9 : reactions du logo (`lg_pulse`, `lg_jitter`, `lg_glow`, `lg_holo`, `lg_melt`, `lg_cells`)

**Fichiers** : `engine/render.py` (lignes ~1211-1241, 1260-1287).

Mapping : `lg_pulse` -> `beat_l` (`v[2]`) pour `pulse` ; `lg_jitter` -> `high_l` (`v[1]`) pour `jit` ; `lg_glow` -> `bass_l` (`v[0]`) pour `u_bass` ; `lg_holo` -> `lg_bass` (`v[0]`) dans `_holo_t` ; `lg_melt` -> `lg_bass` dans `_melt_t` ; `lg_cells` -> `lg_bass` dans `_cell_t` et `lg_beat` (`v[2]`) dans `u_cell_fusion`. Le noise (`max(reel, noise)`) s'applique APRES la ligne (il reste commun). Base : `lg_au`.

- [ ] TDD de rendu pour chaque ligne (identite par defaut ; ligne routee ne change que son effet ; `lg_pulse` sur `kick` + `lg_jitter` sur `aigus` independants). Commit `Routage : reactions du logo (Refs #198)`.

### Tache 10 : globaux (`breath`, `hue`) et lignes informatives

**Fichiers** : `engine/render.py` (`bp`, `spectral_hue` ~1186-1200) ; `gui/gui_routing.py`.

`breath` -> `bass` de la respiration sur la pulsation (`v[0]`) ; `hue` -> `bass`, `high` de `spectral_hue` (`v[0]`, `v[1]`). `strobe`, `foudre`, `noise` : lignes `info` (ruling 4) : texte de `explain` (`strobe_info`, `fx_now`, cases du noise) ; aucune cle de reglage.

- [ ] TDD de rendu (identite, ligne routee) ; test GUI des lignes `info` (aucun menu, texte present). Commit `Routage : globaux et lignes informatives (Refs #198)`.

### Tache 11 : pastilles restantes, doc, PR 3

- [ ] Pastilles a cote de : motif du fond (`gui_fond`), halo / fonte / cellules / pulsation / tremblement / contour (`gui_logo`), respiration / teinte (`gui_audio` ou l'onglet qui les porte). Test `check_routage_gui` : 23 lignes -> 23 pastilles (ou texte `info`), toutes liees aux variables du tableau.
- [ ] Doc (`audio.md`, `gui.md`), suite complete, PR 3 (`Closes #198`), carte -> « A tester » apres fusion (option `212b6db2`).
- [ ] **Non verifie** (a dire dans la PR) : lisibilite de l'onglet au projecteur, ressenti sur de la vraie musique, perf avec toutes les lignes modifiees sur le GPU de la soiree.

---

## Auto-revue

- **Couverture de la spec** : modele (taches 1-2), resolution et zero perte (3-4), onglet et pastille (6-7), 23 lignes (4, 8-10), lignes informatives (10), decoupage en 3 PR (en-tete), verifications (chaque tache), risques perf / largeur / conflits (taches 4, 7, contraintes).
- **Coherence des noms** : `ROUTES`, `FX_LINES`, `route_of`, `is_default`, `routed_ids`, `RouteBank.vector`, `routes_now`, `routing_pill`, `build_routing_tab` utilises identiquement partout.
- **Point a surveiller a l'execution** : l'API exacte de `source_menu` et des variables Tk de `ctx` n'est pas relue ici (tache 7) : la lire avant d'ecrire `gui_routing.py`, et noter un ruling si elle impose un autre motif.
