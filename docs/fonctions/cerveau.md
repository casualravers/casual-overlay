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
  `MAX_EVENTS` = 4000), un seul verrou ; publier = une insertion triee, aucun calcul. `Event(t, family, strength, reason, cause)` ; familles :
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
  au plus une fois par `BRAIN_HEALTH_S` = 1 s (`gauges` : fps, gpu, latency_ms, rec, sources, auto) ; fil Tk : scene posee (VJ ou manuel),
  prochaine scene et son heure. **Une panne du journal n'arrete jamais l'analyse ni le rendu** (try / except autour de chaque publication).

## Instantane (`snapshot(log, now_wall, rolling)`)

Sans etat garde d'un appel a l'autre ; le journal est copie une fois. Cles : `grid` (bool), `first` (premier pas absolu), `cursor`
(pas courant + fraction, relatif a `first`), `cells` [(i, rangee, force, probabilite, statut)], `fx` [(i, piste, force, raison, cause)],
`phase_steps` et `veil` (un par pas), `score` (realise, manque, surprise), `header` (bpm, confidence, stable, origin, phase, phase_s, page,
bar, cycles, next_scene, next_scene_s, build_s), `gauges`, `feed` (4 derniers effets : heure, famille, raison, cause), `free` (sans grille).

- **Page** (iso, mandala, sismo) : 64 pas depuis le debut de la page (multiple de 64 depuis l'ancre). **Glissant** (`rolling=True`, tunnel) :
  68 pas depuis le pas courant - 4 (les 4 premiers sont le passe) ; `phase_steps` et `veil` ont alors 68 entrees.
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
  absente -> `miss` (croix rouge) ; futur -> `ghost` si > 0,2.
- **Score de l'en-tete** : toujours les pas deja passes de la page courante avec la **prediction de page**, meme dans le tunnel. Les cases
  passees du tunnel (prediction glissante) peuvent donc differer du score : assume (le score compte une page, le tunnel n'en a pas).
- Voile (`veil_at`) : montee ouverte par `build` (intensite au carre sur `BUILD_RAMP_S` = 16 s), relachement sur `DROP_FADE_S` = 4 s apres un
  drop. Fins de cycles : `cycle_left(temps)` = temps restants avant la fin des cycles de 8, 16, 32 temps.

## Panneau et vues (`gui/gui_brain.py`)

- Place : dans le panneau etendu du Mode VJ, a l'emplacement de l'espace de controle global ; bouton « brain / VJ » (largeur fixe) de la tete
  du panneau. L'emplacement a une largeur **reservee** qui ne fait que grandir (`slot_w`, `sync_slot` toutes les 300 ms ; regle « les
  boutons ne bougent pas ») : la liste de gauche garde sa largeur quand on bascule. Bouton « detach » : fenetre a part (Toplevel), option
  « toujours au-dessus » ; **fermer la fenetre = la raccrocher** (`dock`), meme pendant un REC.
- Contenu : barre (vues, causes, pause, detach), en-tete 2 lignes, la vue (un Canvas), legende et pastilles pour masquer des pistes, fil des
  4 derniers evenements (`<- kick` quand les causes sont actives), jauges sources / auto / sante (sources = noms des sources par couche en v1).
- **Interface d'une vue** : `View(canvas)`, `.layout(w, h)`, `.update(snap, causes, hidden, now)`, `.hover(x, y) -> str | None` ; registre
  `VIEWS`. Une vue qui leve ne fait pas tomber le panneau (erreur journalisee une fois par vue et par type).
- **Pools** : chaque vue cree ses elements une fois puis les deplace / recolore (nombre d'elements constant d'une page a l'autre, teste) ;
  pas d'alpha sous Tk : les couleurs sont melangees vers la teinte de la phase (`blend`). Changer de vue recree la vue (`cv.delete("all")`).
- **iso** : grille isometrique, le temps part de devant ; plateau des instruments (piliers), plateau des effets, voile en lignes de relief,
  plan du present ; angle 18..45 degres. **mandala** : un anneau par mesure, le 1 pile en haut ; couronne de la phrase de 32 temps ; anneaux
  des effets ; aiguille. **tunnel** : couloir en perspective, voies au sol, effets sur les murs, voile au plafond, cadre blanc du present ;
  `TUN_DEPTH` = 32 pas devant (2 mesures, voir Budget). **sismo** : une onde par instrument (passe plein, futur en pointilles), portee des
  effets, voile au-dessus.
- **Rafraichissement** : `scheduled_tick` (ordonnanceur `ui_every`, toutes les `TICK_MS` = 50 ms) laisse passer selon `VIEW_TICK_MS`
  (mandala, sismo, tunnel : 100 ms) ; les actions (bascule, vue, causes...) appellent `tick` directement. Rien quand le panneau n'est pas
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
mesure finale (apres les decisions) :

| Vue | Cadence | tick moyen (mur) | pire en regime | bascule de vue | CPU / tick | charge du fil Tk | gels > 150 ms | images/s |
|---|---|---|---|---|---|---|---|---|
| sans cerveau | - | 0,01 ms | 0,1 ms | - | 0 | 15,7 % | 0 | 60,2 |
| iso | 20 Hz | 3,9 ms | 27 ms | 87 ms | 2,9 ms | 44,2 % | 0 | 60,2 |
| mandala | 10 Hz | 5,0 ms | 22 ms | 17 ms | 2,4 ms | 23,4 % | 0 | 60,2 |
| tunnel | 10 Hz, 32 pas, voile 1 ligne | 10,6 ms | 28 ms | 17 ms | 6,5 ms | 28,2 % | 0 | 60,2 |
| sismo | 10 Hz | 8,1 ms | 25 ms | 17 ms | 4,4 ms | 24,4 % | 0 | 60,2 |
| frise libre | (celle de la vue choisie) | 3,6 ms | 20 ms | 25 ms | 2,2 ms | 21,6 % | 0 | 60,2 |

Premiere mesure (avant toute decision, tout a 20 Hz) : iso 6,3 ms, mandala 6,9, tunnel 14,8, sismo 11,5, frise libre 6,8 ms de moyenne ;
pires 30 a 45 ms ; un gel de 302 ms pendant mandala (`ui_pump`, non reproduit ensuite) ; images/s 60,2 partout.

- **Images/s du rendu** : inchangees (60,2 avec et sans le panneau, toutes vues) : seuil tenu.
- **Gels** : aucun en regime dans la mesure finale. **A la bascule vers iso** (creation de ~3100 elements), le tick monte a 70-215 ms et
  deux mesures ont vu un gel de 173-183 ms a ce moment (`ui_pump` ; l'une sous profileur) : imputable au panneau, mais seulement quand on choisit iso ou quand la
  grille revient apres une frise libre (la vue est recreee).
- **Le temps mur de `brain.tick` est gonfle par le verrou Python** : chaque appel Tk rend le verrou, que le fil GL (et l'analyse) reprend ;
  le tick attend alors son tour. CPU / tick (`time.thread_time`) = la moitie environ du temps mur. Le pire par tick (20-28 ms) depasse 20 ms
  dans toutes les vues, **frise libre comprise** (quelques points seulement) : c'est un plancher de partage du verrou avec le rendu, que
  baisser la cadence ou la profondeur ne change pas. Sans la projection (`--sans-rendu`, premiere mesure courte) les moyennes etaient
  2 a 3 fois plus basses.
- **Redessin du canvas** : fait par Tk a l'inactivite, hors de `brain.tick` mais dans la charge du fil Tk. Hors application (canvas seul,
  `update_idletasks`) : iso ~10 ms par image (le plan du present et le voile couvrent toute la grille de ~3100 elements), tunnel ~10 ms,
  sismo ~5 ms, mandala ~3 ms. C'est pourquoi iso, a 3,9 ms de tick, est la vue qui charge le plus le fil Tk (+28 points).

**Decisions** (ordre du plan : cadence 20 -> 10 Hz, profondeur du tunnel 64 -> 32, voile simplifie, dessin hors du fil Tk en dernier
recours avec l'accord de l'utilisateur) :

1. Optimisations sans effet visible, d'abord (toutes les vues) : `snapshot` ne formate l'heure que des 4 lignes du fil, reprend la
   prediction de page des cases pour le score, ne place sur la grille que les frappes des pas regardes (une phrase avant la page) ; 2,4 ->
   0,7 ms hors application. Sismo : points de l'onde calcules une fois par (valeurs, geometrie) puis coupes au curseur (2,0 -> 0,9 ms).
   Tunnel : texte de survol des cases forme au survol seulement. Frise libre : couleurs et libelles reecrits seulement s'ils changent.
2. **mandala et sismo a 10 Hz** (moyenne au-dessus de 4 ms dans les mesures 2 a 4 : mandala 4,0-4,7, sismo 5,7-5,9). Le cout par tick ne
   baisse pas (il monte un peu : plus de changements par tick), le cout par seconde baisse (sismo 94 -> 66 ms/s, l'ordonnanceur tournant a ~16 Hz
   et non 20).
3. **tunnel** : 10 Hz, puis 32 pas de profondeur (tick 16,4 -> 10,4 ms), puis voile d'une seule ligne : encore au-dessus (10,6 ms par tick,
   ~86 ms/s). **Reste le dernier recours (dessin hors du fil Tk), non code** : a decider par l'utilisateur. A noter : une image calculee
   hors du fil Tk resterait du Python, donc partagerait toujours le verrou avec le rendu.
4. iso reste a 20 Hz (tick sous 4 ms) ; si l'interface rame en soiree avec iso, la passer a 10 Hz (`VIEW_TICK_MS`) est la premiere piste.

## Pieges

- **Jamais d'evenement date dans le futur** : le journal purge par rapport a l'evenement le plus recent ; un `t` futur viderait tout. Le drop
  est date `min(drop_kick, t_audio)`.
- Deux horloges : `next_scene_at` en temps mur (fil Tk), le reste en temps du son ; `phase_since` 0,0 = inconnu.
- Un evenement d'effet par front montant (`FxEdge`), sinon un evenement par image et le journal deborde (`MAX_EVENTS`).
- Les reglages `brain_*` en portee « salle » passaient dans les memoires de masters et l'auto-sauvegarde : portee `device` + `brain_ui.json`.
- Tk n'a pas d'alpha : melange vers la teinte de la phase ; quand la phase change, les couleurs posees sont reecrites (signature `bg`).
- Le cout reel n'est pas `brain.tick` seul : le redessin du canvas (hors tick) et l'attente du verrou Python comptent (voir Budget).
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
