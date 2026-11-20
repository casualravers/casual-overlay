# Visualizer du cerveau (#192) : plan d'implementation

> **Pour les agents :** sous-skill requis : superpowers:subagent-driven-development (recommande) ou superpowers:executing-plans, tache par
> tache. Les etapes sont des cases a cocher (`- [ ]`).

**But :** un panneau en lecture seule (4 vues : iso, mandala, tunnel, sismo) qui montre en direct le tempo, la grille, les frappes, les
predictions, les effets declenches et leur cause, la phase et la sante, sans ralentir le rendu ni le son.

**Architecture :** `core/brain.py` (Python pur) tient un journal partage sous verrou et calcule un instantane pret a dessiner ; le fil
audio, le fil GL et le fil Tk y publient sans calcul ; `gui/gui_brain.py` dessine l'instantane dans un `Canvas` Tk a ~20 Hz, par mises a jour
incrementales, et se tait quand il est cache ou pendant un REC.

**Pile :** Python 3.12, stdlib (core), Tk (gui), moderngl (moteur, inchange).

**Spec :** `docs/plans/2026-11-20-visualizer-cerveau-192.md` ; maquette : `docs/plans/maquettes/visualizer-192.html` (elle fait foi pour
l'allure : couleurs, formes, disposition ; ses constantes de dessin peuvent etre reprises telles quelles).

## Contraintes globales

- Documentation, commentaires, messages : francais **sans accents** ; libelles du panneau **en anglais** (kick, snare, hi-hat, build, drop,
  groove, peak, strobe, foudre, glitch, ripple, chroma, wobble, logo, scene) ; raisons **en francais**.
- `core/brain.py` : stdlib seulement (`threading`, `time`, `math`, `dataclasses`) ; `check_core_purity` doit passer.
- Tests dans `tools/check_gl.py` : chaque nouvelle verification dans `SUITE`, et dans `PURES` si elle ne demande ni GL ni Tk.
- Une seule session lance `check_gl.py` a la fois. Verification de reference : `.venv\Scripts\python.exe tools/check_gl.py --pur`, puis
  `--seulement <motif>`, puis la suite complete avant la PR.
- Couleurs : kick #ff3d7f, snare #2ef2c8, hi-hat #ffd23f, strobe #ffffff, foudre #9d8cff, glitch #ff7a3d, ripple #3db8ff, chroma #ff4fd8,
  wobble #7dff6b, logo #ffb347, scene #c9d1ff ; phases groove #2ef2c8, build #ffb347, peak #ff3d7f, break #6b8bff, ambient / silence #5a6390 ;
  fond du panneau #07080c, teinte par phase : groove (6,18,20), build (24,16,6), peak (28,6,17), break (8,10,26), autres (7,8,12).
- Prediction : `0,6 x part des 4 mesures de la page precedente ayant frappe au meme pas + 0,4 x frappe au meme pas 128 pas (8 mesures) plus
  tot` ; fantome si > 0,2 ; « attendu » si >= 0,5.
- Branche : `visualizer-cerveau-192` (deja creee, la spec y est). Commits avec `Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>`.
  Une PR a la fin (spec + code, `Closes #192`), gabarit `.github/pull_request_template.md`.
- La regle « les boutons ne bougent pas » (CLAUDE.md) s'applique : bouton de bascule et bouton detach a largeur fixe.

## Points d'attention (non couverts par un test de tache, a verifier deliberement en revue)

1. **Changement de tempo ou de grille en cours de page** (recalage `nudge`, Temps 1 auto, perte du BPM) : la page ne doit pas sauter ni
   dupliquer des frappes ; attendu : placement recalcule depuis l'ancre courante, page recommencee si l'ancre bouge de plus d'un pas. Test
   ajoute a la tache 2 (`snapshot` apres changement d'ancre).
2. **Horloges** : les frappes sont datees dans le temps du son (`t_audio`), les evenements GL / Tk en `time.monotonic` : un decalage mal
   converti met les effets a cote des frappes. Test ajoute a la tache 1 (`to_audio`).
3. **Silence prolonge / pas de micro** : journal vide, aucune grille ; attendu : frise libre « tempo non detecte », aucune exception.
   Test ajoute a la tache 2.
4. **Fermeture de la fenetre detachee pendant un REC ou pendant la fermeture du programme** : pas de `TclError` dans le journal. Test
   ajoute a la tache 4.
5. **Charge** : le tunnel redessine tout a chaque image ; attendu : cout mesure et budget decide (tache 9), jamais de gel > 150 ms
   (`STALL_MS`) imputable au panneau.

---

### Tache 1 : `core/brain.py`, journal et placement

**Fichiers :** creer `core/brain.py` ; modifier `tools/check_gl.py` (nouvelle `check_brain_pure`, dans `SUITE` et `PURES`) ;
`core/CLAUDE.md` (ligne `brain.py` -> fiche `cerveau.md`, creee a la tache 9 : jusque-la pointer vers `docs/fonctions/scenes-vj.md`).

**Interfaces produites :**
- Constantes : `INSTRUMENTS = ("kick", "snare", "hihat")`, `TRACKS = ("strobe", "foudre", "glitch", "ripple", "chroma", "wobble", "logo",
  "scene")`, `STEPS_PER_BEAT = 4`, `STEPS_PER_BAR = 16`, `PAGE_STEPS = 64`, `PHRASE_STEPS = 128`, `KEEP_S = 120.0`, `MAX_EVENTS = 4000`,
  `LABELS = {"hihat": "hi-hat"}` (sinon le nom tel quel), `COLORS` (dict des couleurs ci-dessus).
- `@dataclass(frozen=True) class Event: t: float; family: str; strength: float = 1.0; reason: str = ""; cause: str | None = None`
  (`t` en temps du son ; `family` dans INSTRUMENTS, TRACKS ou `"phase"`, `"build"`, `"drop"` ; `cause` dans INSTRUMENTS ou None).
- `class BrainLog` :
  - `set_clock(t_audio: float, wall: float) -> None` (decalage = t_audio - wall, mis a jour par le fil audio) ;
    `to_audio(wall: float) -> float` (sans horloge connue : `wall`) ;
  - `publish(family: str, strength: float = 1.0, reason: str = "", cause: str | None = None, *, t: float | None = None,
    wall: float | None = None) -> None` (une des deux dates ; `wall` est converti ; famille inconnue ignoree ; ajout sous verrou, purge des
    evenements plus vieux que `KEEP_S` par rapport au plus recent et au-dela de `MAX_EVENTS`) ;
  - `set_status(**fields) -> None` / `status() -> dict` (copie) : dernier etat publie (`bpm`, `confidence`, `origin`, `grid` =
    `(anchor, period)` ou None, `phase`, `phase_since`, `next_scene`, `next_scene_at`, `gauges` = dict) ;
  - `events(since: float | None = None) -> list[Event]` (copie sous verrou, ordre chronologique).
- `step_of(t: float, anchor: float, period: float) -> int` : `round((t - anchor) / period * STEPS_PER_BEAT)` ;
  `step_time(step: int, anchor: float, period: float) -> float`.

- [ ] **Etape 1 : ecrire `check_brain_pure` (partie journal)**, assertions :
  - `step_of(10.0 + 0.5 * 1.75, 10.0, 0.5) == 7` (1,75 temps = 7 pas) ; `step_of(9.9, 10.0, 0.5) == -1` ; `step_time(8, 10.0, 0.5) == 11.0` ;
  - journal : 3 `publish` dans le desordre de dates -> `events()` trie ; famille `"bidon"` ignoree ; `MAX_EVENTS + 10` publications ->
    `len(events()) == MAX_EVENTS` ; evenement plus vieux que `KEEP_S` purge ;
  - horloge : `set_clock(100.0, 5.0)` puis `publish("strobe", wall=6.0)` -> `events()[-1].t == 101.0` ; sans `set_clock`, `to_audio(3.0) == 3.0` ;
  - concurrence : 4 fils x 2000 `publish` + lectures `events()` en parallele -> aucune exception, `len(events()) == MAX_EVENTS` ;
  - `status()` rend une copie (la modifier ne change pas le journal).
- [ ] **Etape 2 : lancer** `.venv\Scripts\python.exe tools/check_gl.py --seulement brain_pure` -> echec (`No module named 'core.brain'`).
- [ ] **Etape 3 : implementer** la partie journal de `core/brain.py` (docstring de module en francais sans accents : role, threads, regle).
- [ ] **Etape 4 : relancer** -> `Toutes les verifications passent.` ; puis `--pur` (inclut `check_core_purity`).
- [ ] **Etape 5 : commit** « Cerveau : journal partage et placement sur la grille (Refs #192) ».

### Tache 2 : `core/brain.py`, prediction, statuts, instantane

**Fichiers :** `core/brain.py`, `tools/check_gl.py` (`check_brain_pure` completee).

**Interfaces produites :**
- `hits_by_step(events: list[Event], anchor: float, period: float) -> dict[tuple[int, str], float]` (cle (pas, instrument), force max).
- `predict_page(hits, step: int, row: str, page_first: int) -> float` ; `predict_rolling(hits, step: int, row: str) -> float` (recent =
  pas - 16 k, k = 1..4) ; meme ponderation 0,6 / 0,4 ; `GHOST_MIN = 0.2`, `EXPECT_MIN = 0.5`.
- `status_of(v: float, p: float, past: bool) -> str | None` : passe : `v > 0` -> `"surprise"` si `p < 0,5` sinon `"real"` ; `v == 0` ->
  `"miss"` si `p >= 0,5` sinon None ; futur : `"ghost"` si `p > 0,2` sinon None.
- `cycle_left(beat_index: int) -> dict[int, int]` : `{8: 7 - b % 8, 16: 15 - b % 16, 32: 31 - b % 32}`.
- `veil_at(t: float, build_t0: float | None, drop_t: float | None) -> tuple[str | None, float]` : montee : `("build", min(1, ((t -
  build_t0) / 16) ** 2))` ; apres un drop : `("drop", max(0, 1 - (t - drop_t) / 4))` ; sinon `(None, 0.0)`. Le dernier `"build"` du journal
  ouvre la montee, un `"drop"` ou une `"phase"` autre que build la ferme.
- `snapshot(log: BrainLog, now_wall: float, rolling: bool = False) -> dict` avec les cles : `"grid"` (bool) ; `"first"` (pas du debut de
  page, multiple de 64, ou `cursor_step - 4` si rolling) ; `"cursor"` (float, pas depuis `first`) ; `"cells"` : liste de `(i, row, v, p,
  status)` pour i dans 0..63 (rolling : 0..67, les 4 premiers sont le passe) ; `"fx"` : liste de `(i, track, strength, reason, cause)` ;
  `"phase_steps"` : 64 noms de phase ; `"veil"` : 64 `(kind, intensity)` ; `"score"` : `(ok, miss, surprise)` ; `"header"` : `bpm`,
  `confidence`, `stable` (confiance >= 0,6), `origin`, `phase`, `phase_s`, `page`, `bar` (1..4), `cycles`, `next_scene`, `next_scene_s`,
  `build_s` ; `"gauges"` (copie du statut) ; `"feed"` : 4 dernieres lignes `(hh:mm:ss, family, reason, cause)` des pistes d'effets ;
  `"free"` (sans grille seulement) : frappes et effets des 8 dernieres secondes en `(age_s, family, strength)`.

- [ ] **Etape 1 : tests** (dans `check_brain_pure`) :
  - prediction : 4 mesures precedentes avec kick aux pas 0, 4, 8, 12 et rien 128 pas avant -> `predict_page(..., step=page_first + 4)
    == 0.6` ; meme chose avec kick 128 pas avant -> `1.0` ; pas 2 -> `0.0` ;
  - statuts : `status_of(0.8, 0.6, True) == "real"`, `status_of(0.8, 0.3, True) == "surprise"`, `status_of(0, 0.6, True) == "miss"`,
    `status_of(0, 0.3, True) is None`, `status_of(0, 0.25, False) == "ghost"`, `status_of(0, 0.1, False) is None` ;
  - `cycle_left(7) == {8: 0, 16: 8, 32: 24}` ;
  - voile : 8 s apres le debut d'une montee -> `("build", 0.25)` ; 2 s apres un drop -> `("drop", 0.5)` ;
  - instantane avec grille (anchor 0, periode 0,5, kicks sur chaque temps pendant 3 pages) : `first % 64 == 0`, `len(cells)` coherent,
    `score[0] > 0`, `header["bar"]` dans 1..4, `fx` porte la cause `"kick"` quand l'evenement en a une ;
  - **changement d'ancre** en cours de page (`anchor` decale de 0,05 s) : pas de frappe dupliquee (une cle (pas, instrument) par frappe) ;
    decale d'un temps entier : `first` recalcule, aucune exception ;
  - **sans grille** (`grid=None`) : `snapshot["grid"] is False`, `"free"` present, aucune exception ; journal vide : idem ;
  - `feed` : au plus 4 lignes, raisons telles que publiees (francais).
- [ ] **Etape 2 : lancer** -> echec sur la premiere fonction absente.
- [ ] **Etape 3 : implementer.** `snapshot` copie le journal une fois (`log.events(now - 2 pages)`), ne garde pas d'etat ; libelles via
  `LABELS`.
- [ ] **Etape 4 : relancer** `--seulement brain_pure` puis `--pur` -> tout passe.
- [ ] **Etape 5 : commit** « Cerveau : prediction, statuts et instantane (Refs #192) ».

### Tache 3 : publications du moteur et de l'interface

**Fichiers :** `engine/session.py` (`Session.brain = BrainLog()`), `engine/audio.py`, `engine/render.py`, `engine/projection.py` (donner
`session.brain` au `Renderer`), `gui/gl_gui.py` (scenes, prochaine scene VJ), `tools/check_gl.py` (`check_brain_engine`, dans `SUITE`,
groupe par defaut).

**Interfaces consommees :** `BrainLog.publish`, `set_clock`, `set_status` (tache 1).

**Ce qui est publie, et ou (aucun calcul nouveau, seulement des valeurs deja la) :**
- `AudioAnalyzer` (attribut `brain = None`, pose par `run_app` / la session) dans la boucle d'analyse, apres `self.rhythm.update` :
  `set_clock(t_audio, now)` ; kick (`beat`) -> `publish("kick", levels["bass"], t=t_audio)` ; `edges["snare"]` -> `("snare",
  levels["mid"])` ; `edges["hit"]` -> `("hihat", levels["high"])` (forces bornees 0..1) ; changement de phase -> `publish("phase", reason=
  nom)` et, si `"build"`, `publish("build")` ; nouveau drop (`phase.drops` change) -> `publish("drop", t=phase.drop_kick or t_audio)` ;
  `set_status(bpm=..., confidence=rhythm.confidence, origin=grid.origin, grid=(grid.anchor, grid.period) if grid.active else None,
  phase=..., phase_since=...)` au plus une fois tous les 50 ms.
- `Renderer` (attribut `brain = None`) :
  - dans `_strobe`, a chaque eclair accepte : `publish("foudre" if strobe_kind == "foudre" else "strobe", 1.0, reason=self.strobe_info[0],
    cause="kick" si l'eclair vient du cerveau du strobe sur un kick, sinon None, wall=now)` ;
  - effets du fond / logo : un `core.brain.FxEdge` par effet (`EFFECTS = wobble, ripple, glitch, chroma`) et un pour `logo` (reaction du
    logo) : `FxEdge(on=0.6, off=0.3, min_gap_s=0.12).step(t, value) -> bool` (front montant) ; `value = max(composantes du vecteur audio
    de l'effet, fond et logo confondus)` ; raison = texte de `explain.fx_text` pour cet effet ; cause = `"kick"` si la source contient
    « kick » ou « basses », `"snare"` si « snare » / « caisse », `"hihat"` si « aigus » / « frappes », sinon None. `FxEdge` est ajoute a
    `core/brain.py` avec ses tests purs (seuils, rearmement, ecart minimal) ;
  - sante, au plus une fois par seconde : `set_status(gauges={... "fps", "gpu" (PerfGuard), "latency_ms" (audio_age_ms), "rec" (bool),
    "sources" (bandes fond / logo), "auto" (fx master, phases auto, strobe auto, tempo auto, VJ)})`.
- `gl_gui` : `apply_scene*` -> `publish("scene", reason=f"scene : {nom} ({origine})", wall=...)` ; `vj_tick` -> `set_status(next_scene=...,
  next_scene_at=...)`.

- [ ] **Etape 1 : tests** `check_brain_engine` : `FxEdge` (pur, dans `check_brain_pure`) : 0 -> 0,7 -> True une fois ; reste a 0,7 -> False ;
  0,2 puis 0,7 avant 0,12 s -> False ; apres -> True. Moteur : un `AudioAnalyzer` nourri par le signal synthetique a kicks (comme
  `check_features`) publie des `kick` dates en temps du son et un statut avec `bpm` ; un `Renderer` en contexte standalone avec strobe
  force publie un `strobe` ; un `Renderer` sans `brain` fonctionne comme avant (image identique a celle d'avant pour un rendu de reference).
- [ ] **Etape 2 : lancer** -> echec.
- [ ] **Etape 3 : implementer** (chaque publication gardee par `if self.brain is not None`, jamais d'exception qui remonte : `try` comme le
  rythme).
- [ ] **Etape 4 : relancer** `--seulement brain` puis `--seulement render`, `--seulement features`, `--seulement masters` -> tout passe.
- [ ] **Etape 5 : commit** « Cerveau : publications du son, du rendu et des scenes (Refs #192) ».

### Tache 4 : panneau, bascule, detachement, en-tete, jauges, fil

**Fichiers :** creer `gui/gui_brain.py` ; modifier `gui/gui_panel.py` (pose de la boite a la place de `master_box()` selon l'etat),
`gui/gl_gui.py` (installation, tache `ui_every("brain", 50, ...)`), `core/params_schema.py` (reglages de salle : `brain_on` 0/1,
`brain_view` iso / mandala / tunnel / sismo, `brain_detached` 0/1, `brain_geom` texte, `brain_topmost` 0/1, `brain_causes` 0/1,
`brain_hidden` texte = pistes masquees separees par des virgules ; portee salle, jamais capturees par scenes ni presets),
`gui/CLAUDE.md` (ligne `gui_brain.py`), `tools/check_gl.py` (`check_brain_gui`, groupe `gui`).

**Interfaces produites :** `gui_brain.install(ns) -> BrainPanel` avec `box() -> tk.Frame` (construit une fois, parent persistant comme
`master_box`), `toggle()` (VJ <-> brain), `detach()`, `dock()`, `tick(now)` (appele par l'ordonnanceur), `set_view(name)` ; registre des
vues `VIEWS = {"iso": IsoView, ...}` ; interface d'une vue : `View(canvas)`, `.layout(w, h)`, `.update(snap: dict, causes: bool, hidden:
set[str], now: float)`, `.hover(x, y) -> str | None`.

**Comportements :**
- bouton « VJ / brain » (largeur fixe) a cote de l'espace de controle global ; le panneau occupe le meme emplacement (`pack(in_=wrap,
  side="right", fill="y")`) ;
- bouton « detach / dock » (largeur fixe) ; `Toplevel` qui retient `brain_geom` et `brain_topmost` (case « toujours au-dessus ») ; fermer
  (`WM_DELETE_WINDOW`) = `dock()` ;
- `tick` ne fait rien si le panneau n'est pas visible (`winfo_ismapped`) ; pendant un REC (`s.recording.active`) : dessine une derniere fois
  la mention « fige pendant le REC » puis plus rien ;
- en-tete, jauges (sources / auto / sante), fil (4 lignes), legende, pastilles pour masquer des pistes, bouton pause (fige la vue, pas
  l'appli), boutons des 4 vues, bouton « causes » ; bulle de survol (raison) ;
- fond du canvas teinte selon la phase (couleurs des contraintes globales) ;
- **sans grille** (`snap["grid"] is False`), quelle que soit la vue : `FreeView` commune, frise en secondes (8 dernieres secondes, une
  ligne par instrument et par piste, points a leur age) et la mention « tempo non detecte » ; la vue choisie reprend des que la grille
  revient ;
- chaque `tick` est chronometre (`STATS.timed("brain.tick")`).

- [ ] **Etape 1 : tests** `check_brain_gui` (vraie GUI de test, comme `check_gui`) : la bascule montre le panneau et cache l'espace VJ puis
  l'inverse, les deux boutons gardent leur largeur ; detacher ouvre un `Toplevel`, le fermer raccroche (et aucun `TclError` dans le journal,
  y compris si on ferme pendant un REC simule) ; geometrie retenue dans `brain_geom` ; `tick` pendant un REC simule ne redessine pas (nombre
  d'appels de `update` de la vue factice inchange) ; panneau cache -> `update` jamais appele ; vue factice qui leve -> le panneau ne tombe
  pas (erreur journalisee une fois) ; instantane sans grille -> `FreeView` dessinee avec « tempo non detecte », puis retour a la vue
  choisie quand la grille revient.
- [ ] **Etape 2 : lancer** `--seulement brain_gui` -> echec.
- [ ] **Etape 3 : implementer** (vue factice `BlankView` pour cette tache ; les vraies vues arrivent ensuite).
- [ ] **Etape 4 : relancer** `--seulement brain_gui` puis `--seulement check_gui` (la mise en page existante ne doit pas bouger).
- [ ] **Etape 5 : commit** « Cerveau : panneau, bascule VJ, fenetre detachee (Refs #192) ».

### Tache 5 : vue iso

**Fichiers :** `gui/gui_brain.py` (classe `IsoView`), `tools/check_gl.py` (`check_brain_gui` completee).

**Decisions :** projection de la maquette : rangee d'instrument `R = 4,2` unites de large, pas = 1 unite ; `x_ecran = OX + (x - y) * a`,
`y_ecran = OY + (x + y) * b - z`, `a = 5,2` (mis a l'echelle de la largeur du canvas), `b = a * tan(angle)`, angle 18..45 (defaut 30) ;
**le temps part de devant** (`y = 64 - i`) ; plateau instruments x = 0..3R, plateau effets x = 4R..12R ; bande des phases x = -1,4..-0,4.
Formes : pilier (3 faces, hauteur `5 + 22 v`, contour blanc si surprise), fil de fer pour les fantomes (alpha `0,2 + 0,6 p`, fondu 0,35 s en
debut de page), croix rouge #ff5a5a pour un manque ; effets : strobe colonne haute (56 au drop, sinon `34 x force`), foudre colonne 38,
scene dalle de 3 pas, autres cubes `3 + 14 x force` ; voile : 8 lignes de relief au-dessus des pistes d'effets ; causes : pointilles de la
frappe a l'effet ; plan translucide du present. **Mise a jour incrementale** : un pool d'elements de canvas par cellule cree au changement de
page ; a chaque `update`, seules les cellules dont le statut a change, la ligne du present, le voile et le fondu sont touches.

- [ ] **Etape 1 : tests** : avec un instantane fabrique (3 pages de kicks), `update` cree des elements, une seconde page n'augmente pas le
  nombre total d'elements du canvas (pool reutilise) ; deux `update` sans changement de statut ne touchent que la ligne et le voile (compteur
  d'appels `coords` / `itemconfig` borne) ; `hover` sur une frappe rend sa raison ; angle 18 et 45 -> aucun element hors du canvas.
- [ ] **Etape 2 : lancer** -> echec. **Etape 3 : implementer.** **Etape 4 : relancer** -> passe.
- [ ] **Etape 5 : commit** « Cerveau : vue isometrique (Refs #192) ».

### Tache 6 : vue sismo

**Fichiers :** `gui/gui_brain.py` (`SismoView`), tests dans `check_brain_gui`.

**Decisions (maquette) :** trois ondes (bases a 56 / 104 / 152 px pour une hauteur de 350, mises a l'echelle), amplitude 21, decroissance
par pas kick 0,9, snare 1,2, hi-hat 1,9, oscillation `sin(q * 2,3)` sur des quarts de pas ; passe en trait plein, futur predit en pointilles
(alpha 0,3) ; surprise = cercle blanc au sommet, manque = croix ; bande des phases ; portee de 8 lignes (ecart 15) pour les effets en notes ;
voile au-dessus de la portee ; une seule polyligne par onde (un element de canvas par onde, `coords` mis a jour).

- [ ] **Etapes 1 a 5** comme la tache 5 (tests : un element par onde quelle que soit la page, hover sur une note rend sa raison) ; commit
  « Cerveau : vue sismographe (Refs #192) ».

### Tache 7 : vue mandala

**Fichiers :** `gui/gui_brain.py` (`MandalaView`), tests dans `check_brain_gui`.

**Decisions (maquette, version recentree) :** centre du canvas ; rayon interieur 48, 28 par mesure, 9 par instrument ; **cases centrees
sur les axes** (debut de la case s : `-90 deg + s x 22,5 deg - 11,25 deg`), le 1 pile en haut ; couronne de 32 temps (crans 7 / 9 / 12 px aux
fins de 8 / 16 / 32) ; 8 anneaux d'effets (ecart 5) ; disque central : BPM + phase, ondes du voile ; aiguille du present ; arcs du canvas
(`create_arc` style `arc`, largeur = epaisseur de l'anneau) reutilises.

- [ ] **Etapes 1 a 5** (tests : la case du pas 0 de la mesure 1 a son milieu a -90 deg a 1 deg pres ; pool constant d'une page a l'autre) ;
  commit « Cerveau : vue mandala (Refs #192) ».

### Tache 8 : vue tunnel

**Fichiers :** `gui/gui_brain.py` (`TunnelView`), tests dans `check_brain_gui`.

**Decisions (maquette) :** instantane `rolling=True` ; profondeur `z(d) = 2,0 + 0,12 d` (d en pas, 4 pas de passe, 64 a venir), `F = 300`,
centre (320, 170) a l'echelle ; voies au sol x = -1,2 / -0,4 / 0,4 / 1,2 ; murs x = +-1,7, 4 pistes par mur ; plafond 1,1 (voile) ;
piliers `0,15 + 0,45 v` ; cadre blanc du present a d = 0 ; flash blanc (alpha 0,22, 0,5 au drop) pendant 2,5 pas apres un eclair du strobe.
C'est la vue a risque : pool fixe de quadrilateres (68 pas x 3 voies + murs), `coords` a chaque image.

- [ ] **Etapes 1 a 5** (tests : nombre d'elements fixe sur 200 `update` successifs avec un temps qui avance ; aucun point avec `z <= 0`) ;
  commit « Cerveau : vue tunnel (Refs #192) ».

### Tache 9 : mesure du cout, budget, documentation

**Fichiers :** `docs/fonctions/cerveau.md` (nouvelle fiche), `CLAUDE.md` (une ligne dans l'index des fiches), `core/CLAUDE.md`,
`engine/CLAUDE.md`, `gui/CLAUDE.md` (pointer vers `cerveau.md`), `tools/check_gl.py` (`check_claude_md` doit passer).

- [ ] **Etape 1 : mesurer** avec la vraie GUI et le signal synthetique (`--synthetic`), pour chaque vue, 60 s : pire et moyenne de
  `brain.tick` (`STATS`), gels (`StallWatch`), images/s du rendu avec et sans panneau. Seuils de decision : `brain.tick` moyen < 4 ms et pire
  < 20 ms, aucun gel > 150 ms imputable au panneau, images/s du rendu inchangees a 5 % pres.
- [ ] **Etape 2 : decider** par vue si un seuil est depasse, dans cet ordre : cadence 20 -> 10 Hz pour cette vue ; profondeur du tunnel 64 ->
  32 pas ; voile simplifie (une ligne) ; dessin hors du fil Tk (dernier recours, a valider avec l'utilisateur avant de le coder). Noter
  chiffres et decision.
- [ ] **Etape 3 : fiche `cerveau.md`** : role, architecture, publications, vues, prediction, budget mesure, pieges, verifications, non verifie
  (lisibilite dans le noir, predictions avec la vraie musique, cout sur le PC de soiree et pendant un REC).
- [ ] **Etape 4 : suite complete** en arriere-plan (`tools/check_gl.py`), seuls les echecs locaux connus de main toleres (a citer
  nommement dans la PR).
- [ ] **Etape 5 : commit** puis PR (gabarit, `Closes #192`, ce qui a ete verifie, liste « a verifier en vrai », ligne « Non verifie »).
