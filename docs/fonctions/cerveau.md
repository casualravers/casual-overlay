# Visualizer du cerveau (issue #192)

Fiche de [CLAUDE.md](../../CLAUDE.md). Spec : [docs/plans/2026-11-20-visualizer-cerveau-192.md](../plans/2026-11-20-visualizer-cerveau-192.md) ;
maquette (allure, constantes de dessin) : `docs/plans/maquettes/visualizer-192.html`. Fichiers : core/brain.py, engine/audio.py
(`_publish_brain`), engine/render.py (`_brain_publish`, eclairs du strobe, `brain_cause`), engine/session.py (`Session.brain`),
engine/projection.py (pose `renderer.brain`), gui/gui_brain.py, gui/gl_gui.py (`brain_scene`, `brain_vj_status`, `gui_brain.install`),
core/params.py (`BRAIN_UI_PATH`, `save_brain_ui`), core/params_schema.py (`brain_*`), tools/mesure_cerveau.py.

## Role

Voir en direct, en lecture seule, ce que l'appli entend (kick, snare, hi-hat), predit (fantomes), decide (effets et leur raison, cause
frappe -> effet), ou on en est (tempo, grille, page / mesure, phase du morceau, montee / drop) et ce que les modes auto preparent (fins de
cycles 8 / 16 / 32 temps, prochaine scene VJ), plus la sante (images/s, GPU, latence, REC). Usages : coup d'oeil en soiree, reglage a la
maison, le montrer. Libelles en anglais (kick, snare, hi-hat, build, drop, groove, peak...), raisons en francais sans accents.

## Architecture

```
fil audio (engine/audio.py)  --publish kick/snare/hihat/phase/build/drop (t = temps du son), set_clock, set_status(bpm, grid...)-->
fil GL (engine/render.py)    --publish strobe/foudre/effets (wall=monotonic, converti), set_status(gauges)--------------------->  BrainLog
fil Tk (gl_gui.py)           --publish scene (wall=), set_status(next_scene, next_scene_at, vj)------------------------------->  (Session.brain)
                                                                                                                                     |
panneau Tk (gui_brain.py) : tick (ordonnanceur ui_every) -> core.brain.snapshot(log, now, rolling) -> vue.update(snap) -> Canvas
```

- **`core/brain.py`** (Python pur) : `BrainLog` = journal trie par date, borne (`KEEP_S` = 120 s par rapport a l'evenement le plus recent,
  `MAX_EVENTS` = 4000), un seul verrou ; publier = une insertion triee (et le pas de la grille d'alors, voir « Pas fige »). `Event(t, family, strength, reason, cause, step,
  anchor, epoch)` ; familles :
  `INSTRUMENTS` (kick, snare, hihat), `TRACKS` (strobe, foudre, glitch, ripple, chroma, wobble, logo, scene), `STRUCTURE` (phase, build, drop).
  Une famille inconnue est ignoree, une cause qui n'est pas un instrument devient None.
- **Horloges** : toutes les dates du journal sont dans l'horloge du son (`t_audio` du fil audio). Le fil audio publie le decalage
  (`set_clock(t_audio, wall)`) ; le fil GL et le fil Tk publient avec `wall=time.monotonic()`, converti par ce decalage. Exceptions voulues :
  `next_scene_at` (fil Tk) reste en temps mur, `phase_since` (fil audio) en temps du son.
- **`FxEdge`** (fil GL) : un evenement par front montant d'effet (seuils 0,6 / 0,3 avec hysteresis, 0,12 s mini entre deux) et non un par image.
- **Publications** : fil audio a chaque bloc d'analyse (frappes, phase, build a l'entree en phase build, drop date au kick du drop) et le
  statut au plus tous les `BRAIN_STATUS_S` = 50 ms (bpm, confiance, origine de la grille, `grid=(ancre, periode)` ou None, phase,
  `phase_since`) ; fil GL : eclair du strobe (raison de `StrobeBrain` ou du limiteur, cause kick en pic / groove), effets du fond / logo
  (raison de `core.explain.fx_text`, cause `brain_cause(source)` : basses / kick -> kick, caisse -> snare, aigus / frappes -> hihat), sante
  au plus une fois par `BRAIN_HEALTH_S` = 1 s (`gauges` : fps, gpu, latency_ms, rec, sources, auto avec `foudre`), niveaux par couche
  au plus tous les `BRAIN_BANDS_S` = 0,1 s (`bands` : `{"bg": (basses, aigus, kick), "logo": ...}`, 0..1, ce que la couche voit avec sa
  source, son retard et son lissage) ; fil Tk : scene posee (VJ ou manuel),
  prochaine scene et son heure. **Une panne du journal n'arrete jamais l'analyse ni le rendu** (try / except autour de chaque publication).

## Instantane (`snapshot(log, now_wall, rolling)`)

Sans etat garde d'un appel a l'autre ; le journal est copie une fois. Cles : `grid` (bool), `first` (premier pas absolu), `cursor`
(pas courant + fraction, relatif a `first`), `cells` [(i, rangee, force, probabilite, statut)], `fx` [(i, piste, force, raison, cause)],
`phase_steps` et `veil` (un par pas), `score` (realise, manque, surprise), `header` (bpm, confidence, stable, origin, phase, phase_s, page,
bar, cycles, next_scene, next_scene_s, build_s, strobe_in_s, strobe_hz), `gauges`, `bands`, `feed` (4 derniers effets : heure, famille,
raison, cause), `free` (sans grille).

- **Pas fige** (`placed_step`) : une frappe ou une marque d'effet recoit son pas a la **publication**, avec la grille d'alors (`step`,
  `anchor`, `epoch`). Avant, chaque instantane replacait tout le passe avec la grille courante ; or `BeatGrid.nudge` reajuste la periode a
  chaque kick (de moitie vers l'estimation du BPM) : une frappe vieille de N pas glissait de N x (variation relative de la periode) pas,
  toutes ensemble et d'autant plus qu'elles etaient anciennes (« la grille change d'echelle », retour utilisateur). Dans une meme epoque,
  seuls les sauts d'ancre de phrases entieres la deplacent (`PHRASE_STEPS` x arrondi de l'ecart d'ancre en phrases). **Epoque** +1
  (`_new_grid`) quand la grille revient apres une absence, quand le 1 est re-ancre d'autre chose que des phrases entieres (ecart >
  `REANCHOR_BEATS` = 0,5 temps : `BeatGrid.anchor_at` au drop ou a la reprise, `set_one` a l'appui) ou quand la periode change de plus de
  `PERIOD_JUMP` = 12 % d'un coup (tempo double) ; les frappes des epoques passees, et celles publiees sans grille, sont placees par leur
  date, comme avant. Le kick du drop, publie juste avant le statut re-ancre, est de l'ancienne epoque : place par sa date, il tombe sur le 1.
  Limite : une grille effacee puis retrouvee en moins de `BRAIN_STATUS_S` (50 ms) sans re-ancrage n'est pas vue comme nouvelle.
- **Strobe a venir** : pendant une montee (`build_s`), si le strobe auto est actif et pas en mode Foudre (`gauges.auto`), `strobe_in_s` =
  `BUILD_CONFIRM_S` - duree (borne a 0) et `strobe_hz` = `auto_build_hz(duree)` (None pendant les 4 premieres secondes) ; sinon None.

- **Page** (iso, mandala, sismo) : 64 pas depuis le debut de la page (multiple de 64 depuis l'ancre). **Glissant** (`rolling=True`, tunnel) :
  68 pas depuis le pas courant - 4 (les 4 premiers sont le passe) ; `phase_steps` et `veil` ont alors 68 entrees. `depth` borne les pas a
  venir (`depth=32` : 36 pas, identiques au debut de l'instantane complet) : le tunnel ne calcule que ce qu'il dessine.
- **Sans grille** (`grid` None ou periode <= 0) : `free` = (age, famille, force) des `FREE_S` = 8 dernieres secondes ; la vue devient la
  frise libre (`FreeView`, « tempo non detecte ») quelle que soit la vue choisie, et revient des que la grille est calee.
- `phase_since` = 0,0 publie avant le premier changement de phase = duree inconnue (pas affichee).

## Prediction et score

- **Page** (`predict_page`) : 0,6 x part des 4 mesures de la page precedente ayant frappe au meme pas de mesure + 0,4 x frappe au meme pas
  128 pas (une phrase de 8 mesures) plus tot. Fantome si > 0,2, « attendu » si >= 0,5.
- **Glissant, tunnel** (`predict_rolling`) : meme ponderation, mais les « 4 mesures recentes » sont les **4 dernieres mesures deja entendues**
  au meme pas de mesure (pas - 16 k, k choisi pour que pas - 16 k <= pas courant) ; le pas une phrase plus tot compte 0 s'il n'est pas encore
  entendu. Le plan disait « pas - 16 k » sans condition : cela lisait des mesures futures (journal vide a cet endroit), la spec dit « mesures
  qui ont frappe ».
- **Statuts** (`status_of`) : passe et frappe venue -> `real` (attendue) ou `surprise` (non attendue, contour blanc) ; passe, attendue et
  absente -> `miss` (croix rouge) ; futur -> `ghost` si > 0,2. **Pas courant** : passe seulement si sa frappe est deja venue (`real` /
  `surprise`), sinon encore `ghost` (une frappe attendue ne clignote pas en `miss` au debut de son pas).
- **Score de l'en-tete** : toujours les pas finis de la page courante (pas courant exclu) avec la **prediction de page**, meme dans le tunnel. Les cases
  passees du tunnel (prediction glissante) peuvent donc differer du score : assume (le score compte une page, le tunnel n'en a pas).
- Voile (`veil_at`) : montee ouverte par `build` (intensite au carre sur `BUILD_RAMP_S` = 16 s), relachement sur `DROP_FADE_S` = 4 s apres un
  drop. Fins de cycles : `cycle_left(temps)` = temps restants avant la fin des cycles de 8, 16, 32 temps.

## Panneau et vues (`gui/gui_brain.py`)

- Place : dans le panneau etendu du Mode VJ, a l'emplacement de l'espace de controle global ; bouton « brain / VJ » (largeur fixe) de la tete
  du panneau. L'emplacement a une largeur **reservee** qui ne fait que grandir (`slot_w`, `sync_slot` toutes les 300 ms ; regle « les
  boutons ne bougent pas ») : la liste de gauche garde sa largeur quand on bascule. Bouton « detach » : fenetre a part (Toplevel), option
  « toujours au-dessus » ; **fermer la fenetre = la raccrocher** (`dock`), meme pendant un REC.
- Contenu : barre (vues, causes, pause, detach), en-tete 2 lignes, la vue (un Canvas), legende et pastilles pour masquer des pistes, fil des
  4 derniers evenements (`<- kick` quand les causes sont actives), jauges sources / auto / sante. **Sources** (`sources_text`) : par
  couche, la source puis 3 barres texte `B` (basses), `H` (aigus), `K` (kick) de `BAND_CELLS` = 4 cases ; texte a chasse fixe (Consolas)
  de longueur constante, donc largeur fixe. Ce sont les trois composantes que la couche voit (le vecteur audio d'une couche n'a pas de
  mediums : « mediums » y passe par basses et aigus). **En-tete** : `build 6 s  strobe 0.6 Hz` / `build 2 s  strobe in 2.5 s`
  (`build_text`, largeur `BUILD_W`).
- **Interface d'une vue** : `View(canvas)`, `.layout(w, h)`, `.update(snap, causes, hidden, now)`, `.hover(x, y) -> str | None` ; registre
  `VIEWS`. Une vue qui leve ne fait pas tomber le panneau (erreur journalisee une fois par vue et par type).
- **Pools** : chaque vue cree ses elements une fois puis les deplace / recolore (nombre d'elements constant d'une page a l'autre, teste) ;
  pas d'alpha sous Tk : les couleurs sont melangees vers la teinte de la phase (`blend`). Un canvas par vue (et par surface), construit a la premiere
  apparition puis montre / cache (`_Surface.show_view`) : changer de vue ou perdre puis retrouver la grille ne recree rien.
- **iso** : grille isometrique, le temps part de devant ; plateau des instruments (piliers), plateau des effets, voile en lignes de relief,
  plan du present ; angle 18..45 degres. **mandala** : un anneau par mesure, le 1 pile en haut ; couronne de la phrase de 32 temps ; anneaux
  des effets ; aiguille. **tunnel** : couloir en perspective, voies au sol, effets sur les murs, voile au plafond, cadre blanc du present ;
  `TUN_DEPTH` = 32 pas devant (2 mesures, voir Budget) ; eclair du strobe : le fond s'eclaircit et un voile blanc trame (`stipple`
  gray75 / 50 / 25 selon l'age de l'eclair, Tk n'a pas d'alpha) couvre la scene, sous le cadre du present et les libelles. **sismo** : une onde par instrument (passe plein, futur en pointilles), portee des
  effets, voile au-dessus.
- **Rafraichissement** : `scheduled_tick` (ordonnanceur `ui_every`, toutes les `TICK_MS` = 50 ms) laisse passer selon `VIEW_TICK_MS`
  (les quatre vues : 100 ms, voir Budget) ; les actions (bascule, vue, causes...) appellent `tick` directement. Rien quand le panneau n'est pas
  visible (espace VJ affiche, panneau replie, fenetre reduite) ; **fige pendant un REC** (une derniere image « fige pendant le REC »).
  Chaque tick est chronometre `brain.tick` (`STATS`, rapport du menu « reglages VJ »).
- **Persistance** : reglages `brain_on`, `brain_view`, `brain_detached`, `brain_geom`, `brain_topmost`, `brain_causes`, `brain_hidden`, en
  **portee `device`** (pas « salle » : ils entraient dans les memoires de masters) ; ecrits tout seuls dans `~/.audio2wave/brain_ui.json`
  (`save_brain_ui`, sans attendre P) et relus au lancement ; geometrie de la fenetre detachee sauvee 0,5 s apres le dernier deplacement.

## Budget mesure (tache 9)

Outil : `python tools/mesure_cerveau.py [--duree 60] [--sans-rendu] [--json f] [--profil]`. Il lance la vraie application
(`--gui --synthetic --projeter --no-fullscreen` : fenetre de projection GL ouverte, son et video synthetiques), le panneau raccroche lit un
journal synthetique nourri comme une soiree (126 BPM, kick a chaque temps, snare 2 / 4, hi-hat en croches + doubles, quelques manques et
surprises, phases groove 16 -> build 8 -> peak 8 avec drop -> break 4 mesures, effets avec raison et cause, scene toutes les 16 mesures).
Segments de 60 s : sans le cerveau (reference), iso, mandala, tunnel, sismo, frise libre. Fichiers de l'utilisateur non touches.

Poste de dev (AMD Radeon integre, moniteur 59 Hz, projection 1280 x 720 en fenetre, panneau raccroche dans une fenetre 1500 x 900),
mesure finale (mesure 6, tous les remedes appliques sauf le dernier recours) :

| Vue | Remedes | tick moyen (mur) | pire en regime | bascule de vue | ms / s | CPU / tick | charge du fil Tk | gels > 150 ms | images/s |
|---|---|---|---|---|---|---|---|---|---|
| sans cerveau | - | 0,01 ms | 0,0 ms | - | 0 | 0 | 18,1 % | 0 | 60,0 |
| iso | 10 Hz, voile 1 ligne | 5,2 ms | 28 ms | 73 ms | 42 | 4,0 ms | 33,5 % | 0 | 60,2 |
| mandala | 10 Hz, voile 1 anneau | 5,3 ms | 50 ms | 9 ms | 43 | 3,0 ms | 24,6 % | 0 | 59,8 |
| tunnel | 10 Hz, 32 pas (dessines et calcules), voile 1 ligne | 9,9 ms | 28 ms | 16 ms | 80 | 6,2 ms | 28,9 % | 0 | 60,2 |
| sismo | 10 Hz, voile 1 ligne | 8,5 ms | 31 ms | 22 ms | 69 | 5,2 ms | 28,1 % | 0 | 60,1 |
| frise libre | (cadence de la vue choisie, ici 10 Hz) | 3,5 ms | 23 ms | 17 ms | 29 | 2,3 ms | 24,4 % | 0 | 60,2 |

`ms / s` = temps mur de `brain.tick` par seconde (l'ordonnanceur donne ~8 ticks/s a 10 Hz, ~16 a 20 Hz). Les ecarts d'une mesure a l'autre
sont de l'ordre de 1 ms sur la moyenne et de 5 a 20 ms sur le pire (le pire de mandala, 50 ms ici, etait 22 ms a la mesure 5).

Historique des moyennes par tick (mur, ms) :

| Mesure | iso | mandala | tunnel | sismo | frise libre |
|---|---|---|---|---|---|
| 1 : avant toute decision, tout a 20 Hz | 6,3 | 6,9 | 14,8 | 11,5 | 6,8 |
| 2 : optimisations sans effet visible | 4,2 | 4,3 | 12,1 | 5,9 | 4,6 |
| 3 : + tunnel a 10 Hz | 3,9 | 4,0 | 16,4 | 5,7 | 4,8 |
| 4 : + tunnel sur 32 pas dessines | 4,0 | 4,7 | 10,4 | 5,8 | 3,0 |
| 5 : + mandala, sismo a 10 Hz, voile du tunnel en 1 ligne | 3,9 | 5,0 | 10,6 | 8,1 | 3,6 |
| 6 : + iso a 10 Hz, voile en 1 ligne partout, tunnel calcule sur 32 pas | 5,2 | 5,3 | 9,9 | 8,5 | 3,5 |
| 7 : + complements (pas fige, barres des sources, strobe a venir, voile de l'eclair du tunnel) | 5,1 | 5,2 | 10,0 | 9,5 | 3,4 |

Mesure 7 (2026-11-21), en ms/s : iso 41, mandala 42, tunnel 81, sismo 76, frise libre 27 ; aucun gel, 60,1 a 60,2 images/s partout.
Le tunnel est a la limite du budget (81 pour 80 : l'ecart d'une mesure a l'autre est de l'ordre de 1 ms par tick, soit ~8 ms/s).

Mesure 1 : pires 30 a 45 ms, un gel de 302 ms pendant mandala (`ui_pump`, non reproduit ensuite) ; images/s 60,2 partout.

**Decisions** (ordre du plan : cadence 20 -> 10 Hz, profondeur du tunnel 64 -> 32, voile simplifie, dessin hors du fil Tk en dernier
recours avec l'accord de l'utilisateur), toutes appliquees sauf la derniere :

1. Optimisations sans effet visible, d'abord (toutes les vues) : `snapshot` ne formate l'heure que des 4 lignes du fil, reprend la
   prediction de page des cases pour le score, ne place sur la grille que les frappes des pas regardes (une phrase avant la page) ; 2,4 ->
   0,7 ms hors application. Sismo : points de l'onde calcules une fois par (valeurs, geometrie) puis coupes au curseur (2,0 -> 0,9 ms).
   Tunnel : texte de survol des cases forme au survol seulement. Frise libre : couleurs et libelles reecrits seulement s'ils changent.
2. **Les quatre vues a 10 Hz** (`VIEW_TICK_MS`). Le cout par tick ne baisse pas (il monte : plus de changements par tick), le cout par
   seconde baisse (sismo 94 -> 69 ms/s, iso ~63 (estimation : 3,9 ms x ~16 ticks/s, non mesure) -> 42 ms/s ; charge du fil Tk avec iso
   44 -> 34 %). **Iso a 20 Hz tenait le seuil de la moyenne par tick** (3,9 ms < 4 ms, mesure 5) : le passer a 10 Hz a echange ce seuil
   contre environ 10 points de charge du fil Tk en moins (moyenne par tick 5,2 ms a la mesure 6, au-dessus du seuil).
3. **Tunnel sur 32 pas** : 36 pas dessines (4 de passe + `TUN_DEPTH`) et seulement ceux-la calcules (`snapshot(..., rolling=True,
   depth=32)`) ; tick 16,4 -> 10,4 ms, puis 9,9 ms.
4. **Voile simplifie** dans les quatre vues : une ligne (iso, sismo, tunnel) ou un anneau (mandala) au lieu de 8 / 4 / 5 / 4. Le voile n'est
   dessine que pendant une montee ou juste apres un drop : gain faible a la mesure.
5. **Dernier recours (dessin hors du fil Tk) : non code.**

**Conclusion, apres tous les remedes : seuils du plan pris a la lettre NON tenus.**

- Moyenne par tick < 4 ms : **depassee par les quatre vues** : iso 5,2, mandala 5,3, tunnel 9,9, sismo 8,5 ms. Seule la frise libre (3,5 ms)
  passe. Iso le tenait a 20 Hz (3,9 ms) : le passage a 10 Hz l'a echange contre ~10 points de charge du fil Tk (voir decision 2).
- Pire par tick < 20 ms : **depasse partout, frise libre comprise** : iso 28, mandala 50, tunnel 28, sismo 31, frise libre 23 ms (19,6 ms a la
  mesure 5 : la frise libre est a la limite, elle passe ou non selon la mesure).
- Gels > 150 ms imputables au panneau : aucun en regime ; **a la bascule vers iso** (creation de ~3100 elements, tick de 70 a 215 ms),
  deux mesures sur six ont vu un gel de 173-183 ms (`ui_pump` ; l'une sous profileur). Cela arrivait quand on choisit iso et quand la grille
  revient apres une frise libre (la vue etait recreee), donc aussi en soiree si le tempo se perd puis revient (au drop). **Corrige depuis** :
  un canvas par vue, construit a sa premiere apparition puis montre / cache (`_Surface.show_view`) ; seule la toute premiere apparition
  d'iso coute encore la creation (test : iso -> free -> iso et iso -> sismo -> iso, aucun element recree, retour ~4 ms).
- Images/s du rendu a 5 % pres : **tenu** (60,2 sans et avec le panneau, 59,8 au pire).

Pourquoi les remedes ne suffisent pas : le temps mur de `brain.tick` est gonfle par le **verrou Python**. Chaque appel Tk rend le verrou,
que le fil GL (et l'analyse) reprend ; le tick attend son tour. Le CPU par tick (`time.thread_time`) n'est que 50 a 75 % du temps mur, et le
pire depasse 20 ms meme pour la frise libre (quelques points). Sans la projection (`--sans-rendu`, premiere mesure courte) les moyennes
etaient 2 a 3 fois plus basses. Baisser la cadence ou la profondeur reduit le cout par seconde, pas ce plancher par tick. Le **redessin du
canvas** (fait par Tk a l'inactivite, hors de `brain.tick` mais dans la charge du fil Tk) coute en plus, hors application : iso ~10 ms par
image (le plan du present et le voile couvrent toute la grille), tunnel ~10 ms, sismo ~5 ms, mandala ~3 ms.

**Decision de l'utilisateur (2026-11-21) : budget par seconde.** Le seuil du plan « moyenne < 4 ms et pire < 20 ms par tick » est remplace
par **80 ms/s** de `brain.tick` (4 ms x 20 Hz), avec « aucun gel en regime, images/s du rendu a 5 % pres ». Toutes les vues le tiennent
(29 a 80 ms/s, le tunnel tout juste). Le dessin hors du fil Tk n'est pas fait (une image calculee en Python partagerait toujours le
verrou avec le rendu). Les vues construites sont gardees (plus de gel au retour vers iso, voir plus haut).

## Pieges

- **Jamais d'evenement date dans le futur** : le journal purge par rapport a l'evenement le plus recent ; un `t` futur viderait tout. Le drop
  est date `min(drop_kick, t_audio)`.
- **Ne jamais replacer le passe avec la grille courante** : la periode bouge a chaque kick, les frappes anciennes glissaient (voir « Pas
  fige »). Un nouvel usage des pas passe par `placed_step`, pas par `step_of(e.t, ...)`.
- Deux horloges : `next_scene_at` en temps mur (fil Tk), le reste en temps du son ; `phase_since` 0,0 = inconnu.
- Un evenement d'effet par front montant (`FxEdge`), sinon un evenement par image et le journal deborde (`MAX_EVENTS`).
- Les reglages `brain_*` en portee « salle » passaient dans les memoires de masters et l'auto-sauvegarde : portee `device` + `brain_ui.json`.
- Tk n'a pas d'alpha : melange vers la teinte de la phase ; quand la phase change, les couleurs posees sont reecrites (signature `bg`).
- Le cout reel n'est pas `brain.tick` seul : le redessin du canvas (hors tick) et l'attente du verrou Python comptent (voir Budget).
- Reprise apres arret brutal (`recover_autosave`) : l'instantane a jusqu'a 60 s d'age ; ses `brain_*` sont ignores, ceux de `brain_ui.json`
  (relus par `load_params`) sont gardes.
- VJ en rythme sans grille : `t_next` n'est que le garde-fou de `BarTimer` (double de la duree attendue) ; aucun delai n'est publie
  (`next_scene_at` None), seulement la prochaine scene.
- `brain_ui.json` est ecrit sans fichier temporaire (non atomique) : un arret pendant l'ecriture perd l'etat du panneau (simple confort).

## Verifications

`tools/check_gl.py` : `check_brain_pure` (pur : placement, journal borne et concurrent, prediction page / glissante, statuts, score, voile,
cycles, sans tempo, libelles), `check_brain_engine` (publications du fil audio et du rendu synthetique, image inchangee),
`check_brain_gui` (vraie fenetre de reglages : bascule VJ / brain a la meme place, largeurs fixes, detacher / raccrocher, geometrie retenue
sans P, fige en REC, rien quand cache, vue qui leve, frise libre, cadence par vue), `check_brain_iso`, `check_brain_sismo`,
`check_brain_mandala`, `check_brain_tunnel` (pools constants, mises a jour incrementales, survol, causes, rien hors du canvas).
Mesure du cout : `tools/mesure_cerveau.py` (voir Budget ; ~7 min pour 60 s par segment, une seule a la fois, pas pendant `check_gl.py`).

## Non verifie (demande le materiel)

Lisibilite dans le noir sur l'ecran du PC de soiree ; justesse des predictions et des causes avec la vraie musique (le journal mesure est
synthetique) ; cout sur le PC de soiree pendant un set et **pendant un REC** (le panneau est fige, mais le son juste avant / apres n'a pas ete
ecoute) ; allure a l'ecran des vues a 10 Hz et du tunnel raccourci a 2 mesures ; fenetre detachee sur un deuxieme ecran.
