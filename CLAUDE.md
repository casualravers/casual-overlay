# CLAUDE.md

Guide pour Claude Code (claude.ai/code) dans ce depot.

## Contexte

casual-overlay affiche le visuel `audio2wave_live` (flux ffmpeg) dans une fenetre OpenGL
plein ecran sur le videoprojecteur, avec un logo PNG incruste et des deformations pilotees
en temps reel par l'audio du micro. Le plan d'origine est dans `docs/PLAN_POC_GL.md`.

**audio2wave est interne** : une copie du depot `audio2wave` (`https://github.com/casualravers/audio2wave`, `main`) vit dans `sources/audio2wave/` (modules **plats** : ils\ns'importent par leur nom, le dossier est ajoute a `sys.path` ; `audio2wave_gl.bundled_paths` le choisit : `audio2wave/` a cote du programme (portable) > `sources/audio2wave/` >\ndepot voisin `../audio2wave` (dernier recours) ; `--a2w-dir` / `AUDIO2WAVE_DIR` en essaient un autre). On importe `audio2wave_live` (`parse_args`, `producer_command`,\n`resolve_size`, `list_audio_devices`) et les autres modules. Son CLAUDE.md (copie : `sources/audio2wave/CLAUDE.md`) fait foi pour tout ce qui touche au visuel de base (filtres ffmpeg, `--averaging`,\nlatence). **Origine et ecarts** : `sources/audio2wave/VENDORED.md` (commit copie, section « Modifications locales ») et `ORIGINE.json` (empreinte de chaque fichier d'origine) ;\n`check_gl.py` (`check_audio2wave_interne`) echoue si un fichier differe de l'origine **sans etre note** dans VENDORED.md : modifier ces fichiers est permis maintenant, mais toute\nmodification se note (fichier, raison) pour pouvoir resynchroniser avec le depot d'origine. Le depot `audio2wave` reste vivant a part : un correctif qui y est fait se reporte a la main ici\n(et inversement), c'est le prix d'avoir deux copies tant qu'il existe.

**API hote d'audio2wave utilisee** (`common.API_VERSION` >= 2 ; la copie interne l'expose et la garantit par `check_contract.py`, on ne reecrit plus ses constantes ni ses methodes, on ne fouille plus ses
widgets) : `common.set_gui_accent(accent, accent_fg)` (accent de la palette : `apply_a2w_palette`), `AutomationManager(..., curve_label,
on_editor_open)` et son crochet de classe `AutomationManager.default_on_editor_open(manager, fenetre, attr, canvas)` (fenetre « courbe » :
`install_curve_skin`), `Tooltip` qui reste dans la fenetre, `common.make_scrollbar` et `common.readable_fg` (survol des menus lisible sur
le bleu : plus de style de menu chez nous), **`GuiController`** renvoye par les trois `build_gui` (`capture_state` / `apply_state` /
`commit_pending` / `widgets`) avec les options `parent`, `embedded`, `compact`, `presets_first`, `parse_args(argv)` (plus d'echange de
`sys.argv`), **`start_headless(args, size, frame_sink, status)`** pour Snap et Ridge (plus de faux ffplay), `AutomationManager.snapshot()`,
et **`preset_registry`** (`store_for(mode)` -> `ModeStore` : `user` / `all` / `save` (rend le nom enregistre) / `delete`, `normalize_name`,
`CROSS_MODE_UNSAFE_KEYS`, `strip_foreign` ; c'est `gl_gui.mode_store`). **Garde de version** : `gl_gui.require_audio2wave_api(live)` (appelee
par `audio2wave_gl` avant de lancer le rendu, et par `run_gui`) compare `common.API_VERSION` a `AUDIO2WAVE_API_MIN` et s'arrete avec un message
clair (« mets a jour le depot audio2wave ») au lieu d'un `AttributeError` (`audio2wave_api_missing`, injectable ; `check_gl.py` :
`check_audio2wave_api`). **Test de contrat** : `check_gl.py` (`check_contract`) lance `check_contract.py` du depot audio2wave (noms et
signatures dont on depend) et echoue sur un code de sortie non nul ; un nom casse se corrige cote audio2wave.

**Toute la documentation, les commentaires et les messages CLI sont en francais sans
accents** (meme convention qu'audio2wave, compatibilite console Windows).

Contrairement a audio2wave (stdlib seulement), ce depot a des dependances :
`requirements-gl.txt` (moderngl, glfw, numpy, sounddevice, Pillow, tkinterdnd2 : ce dernier facultatif, pour le glisser-deposer des scenes). **Python 3.12
recommande** : `moderngl`/`glcontext` n'ont pas de wheel pour Python 3.14 et leur
compilation exige MSVC. Environnement de dev : `py -3.12 -m venv .venv` puis
`.venv\Scripts\python.exe -m pip install -r requirements-gl.txt`.

## Structure du depot : `core/` (Python pur) au-dessus du moteur et de la GUI

```
core/            Python pur, stdlib seulement (aucune fenetre, aucun GL, aucun numpy) : se teste partout
  params_schema  table unique des reglages (defaut, plage, enum, portee, doc)
  params         DEFAULT_PARAMS, validation (coerce_params), presets overlay / fond, capture, gl_params.json, logo par defaut
  automation     courbes, AutomationEngine, liaison fond / logo (get_param, set_param, set_fx_link)
  controls       tempo effectif, strobe (appui), StrobeLimiter, panique, tap tempo, BarTimer
  phase          phases du morceau (silence / ambiance / groove / montee / cassure / pic) et multiplicateurs lisses : « Phases auto » (`PhaseTracker`)
  audio_layers   sources audio par couche (fond / logo : tout, basses, mediums, aigus, kick, frappes, rien) : `layer_levels`, retard et lissage (`LayerFilter`)
  explain        textes des voyants des fonctions automatiques (sources par couche, effets des phases, raison du dernier eclair du strobe)
  strobe_auto    strobe automatique : `StrobeBrain` decide seul quand eclairer (phase du morceau, kicks, frappes aigues, grille)
  onset          detecteur d'attaques parametrable (`OnsetDetector`) : frappes aigues et kicks par couche
  rhythm         BPM estime a partir des kicks, montee et drop detectes a partir des basses (`Rhythm`, `BpmEstimator`, `DropDetector`)
  tempo          BPM du son sans dependre des kicks : autocorrelation du flux d'attaques (`TempoTracker`), secours de `Rhythm` (telephone, kick non detecte)
  setpack        archive .zip d'un set avec ses medias (ecriture, lecture, extraction sure)
  safety         securite GPU du VJ : alleger la scene suivante selon ses effets couteux et la charge mesuree
  autosave       instantane toutes les minutes, reprise apres un arret brutal
  sessionlog     journal de session
  scenes         scenes, sets, memoires de masters (stockage, export / import)
  randomizer     Full Randomizer (banques d'assets, tirages)
  schedule       planning de la soiree (heure -> texte du logo)
  palette        classement de la palette de commandes (Ctrl+K)
  doodles        doodles du site : config, liste du bucket, fichiers vus, validation, position, cycle de vie, mur (voir plus bas)
engine/          moteur sans rendu GL (importe core/ et les dependances facultatives, jamais la GUI ni la facade) :
  deps           numpy / glfw / moderngl / sounddevice / Pillow importes une fois (`None` si absents, `MISSING`)
  audio          FeatureExtractor, RingBuffer, AudioAnalyzer, entrees micro / synthetique
  video          FrameReader, flux de test, producteurs ffmpeg, LogoVideo, BgVideo, ProducerManager
  window         fenetre glfw, choix du moniteur, plein ecran
  render         Renderer (passes GL), noise, texte / logo, PerfGuard
  session        Session (etat partage GL / GUI / fils), AudioController, NoDeviceManager
  preview        apercu d'une scene : capture reduite (JPEG) de la fenetre de rendu
  native_modes   Snap (pencil, simple, rekordbox) et Ridge dessines en GL : analyse d'audio2wave + shaders `mode_*.frag`, `NativeModeSource`, `ModeGL`
  doodle_feed    fil de recuperation des doodles (urllib + Pillow), file Session.doodle_queue`r
  projection     Projection (fenetre a la demande, reprise auto), logo, boucle de rendu (gl_main / loop)
  app            run_app (micro, producteur, session, arret propre ; la GUI lui est injectee, jamais importee)
gui/             fenetre de reglages Tk, modules PLATS (importes par leur nom : `audio2wave_gl` ajoute gui/ a `sys.path`) : gl_gui, gui_*, skin, theme, icons, py_modes, gui_colors, gui_gates
tools/           check_gl.py (`python tools/check_gl.py`), build_portable.py (`python tools/build_portable.py --zip`) ; ils ajoutent la racine du depot a `sys.path`
docs/            documents de conception (PLAN_POC_GL.md)
Racine : audio2wave_gl.py (point d'entree), start.bat (lanceur, a garder ici), README.md, CLAUDE.md, requirements-gl.txt. `engine/diagnostics.py` : diagnostic de demarrage.
sources/audio2wave/  audio2wave (copie interne, modules plats, voir plus haut)
audio2wave_gl.py  point d'entree (parse_args, main, require_deps, bundled_paths) + facade qui re-exporte core/ et engine/ (gl.X) ;
                  les tests qui remplacent un nom visent le module qui l'utilise (ex. engine.projection.loop)
gl_gui.py         GUI Tk : fenetre, mise en page, barre des scenes, scenes / VJ (apply_scene, vj_tick...), pont vers le moteur
gui_fond / gui_effects / gui_logo / gui_audio   onglets de la partie LIVE et OVERLAY (fond, effets + halo / fonte / cellules / noise, logo, analyse)
gui_sets / gui_drop / gui_random / gui_panel    sets, medias (glisser-deposer, vignettes), Full Randomizer, panneau etendu
gui_presets / gui_bar / gui_palette / gui_stage   barre de presets (overlay et fond), barre des scenes, palette Ctrl+K, mode scene plein ecran
gui_vj            espace de controle global du Mode VJ (masters, historique, montee / drop) + garde-fous (panique, tap tempo, aide, console, auto-sauvegarde)
skin / theme / gui_colors / gui_gates           habillage, couleurs, grisages```

**Regle de dependance** : `core/` n'importe jamais le moteur (`audio2wave_gl`), la GUI, Tk, numpy, GL, PIL ni audio2wave ; le moteur et la GUI
importent `core/`. `check_gl.py` (`check_core_purity`) l'impose par l'analyse des imports et en important tout `core/` dans un interpreteur
qui interdit ces modules. Un module deplace dans `core/` l'a ete **tel quel** (blocs coupes, pas reecrits) ; seuls changent les imports.
- **Facade** : `audio2wave_gl` re-exporte les noms deplaces (`gl.coerce_params`, `gl.panic`, `gl.AutomationEngine`...) pour que le code et les tests
  existants marchent ; les nouveaux usages importent `core/` directement. **Les chemins de fichiers se remplacent dans le module qui les possede**
  (`core.params.PARAMS_PATH` / `OVERLAY_PRESETS_PATH` / `BACKGROUND_PRESETS_PATH`, `core.autosave.AUTOSAVE_PATH`, `core.scenes.SCENES_PATH` /
  `MASTERS_PATH`) : remplacer l'alias `gl.X` ne change rien aux fonctions de core/ (c'est ce que font les tests).
- Le dossier portable embarque `core/` entier (`APP_DIRS`), verifie par le script (diagnostic + rendu synthetique avec le Python du dossier).
## Commandes

```bash
python audio2wave_gl.py --list-devices                 # entrees dshow (ffmpeg)
python audio2wave_gl.py --list-audio-devices           # entrees sounddevice
python audio2wave_gl.py -d "<entree>" --dry-run        # meme commande ffmpeg que audio2wave_live
python audio2wave_gl.py -d "<entree>"                  # live (fenetre ; --fullscreen pour le plein ecran)
python audio2wave_gl.py --gui [--projeter]             # reglages seuls ; bouton « Demarrer la projection » (ou --projeter)
python audio2wave_gl.py --synthetic --no-fullscreen    # sans ffmpeg ni micro
python tools/check_gl.py                                     # verifications hors materiel
python audio2wave_gl.py --diagnostic                   # ce PC est-il pret ? (ffmpeg, OpenGL, micro...)
python audio2wave_gl.py --rapport-bug "description"   # zip de diagnostic sur le Bureau (journal, plantages, reglages sans secrets)
python tools/build_portable.py --zip                         # dossier portable (Python + ffmpeg + audio2wave dedans)
```

Options utiles : `--gui` (fenetre de reglages, voir plus bas), `--live-args "--style radio
--gain 20"` (transmis a audio2wave_live),
`--render-size`, `--logo`, `--logo-video anim.webm [--logo-key #00ff00]`, `--text "LIGNE 1\nLIGNE 2"`, `--logo-scale`, `--logo-pos`, `--monitor`, `--hud`, `--stats`,
`--automation/--no-automation`, `--fps-cap`, `--max-seconds` + `--screenshot` (captures de test).

Touches : Echap quitte, F fenetre/plein ecran, H barres de debug, B fond spectre/motif/video, T automations, 1-5 effets
(wobble/ripple/chroma/glitch/logo; Maj+1-4 = meme effet sur la couche du logo, L = lier/separer fond et logo), C halo holographique, M fonte acide du logo, V cellules du logo, F1-F9 scenes, Espace VJ lecture/pause, fleche droite scene suivante (avec --gui), S strobe, K verrou, Retour arriere scene precedente, **X panique**, **.** tap tempo, **/** aide, +/- ou PageUp/PageDown intensite globale, haut/bas
sensibilite du kick, R recharge shaders + reglages, P sauve dans
`~/.audio2wave/gl_params.json` (recharge au lancement).

## Architecture

```
micro --dshow--> ffmpeg (producteur) --rawvideo--> FrameReader (fil) --derniere frame entiere-->
micro --WASAPI--> sounddevice -> RingBuffer -> AudioAnalyzer (fil) -> FeatureExtractor -> etat
                                                                          |
        fenetre glfw + moderngl : passe 1a (FBO fond: video ou motif) + passe 1b (FBO logo seul + contour,
        rgba premultiplie) -> passe 2 (effets par couche, logo pose sur le fond, HUD)
```

Le moteur est dans [audio2wave_gl.py](audio2wave_gl.py) (le Python pur est dans core/, voir plus haut) ; les GLSL sont dans `gl_shaders/`
(`quad.vert`, `scene.frag`, `post.frag`, rechargeables a chaud avec R).

- **Le rendu tourne a la cadence de l'ecran, pas a celle de ffmpeg** (30 fps) : la derniere
  frame est re-echantillonnee, les effets restent fluides.
- **Latest-frame-wins** (`FrameReader`) : le fil lecteur accumule exactement `frame_size`
  octets (`largeur x hauteur x 3`) avant de publier, echange deux tampons sous verrou, et
  ecrase toute frame non consommee. Jamais de file, donc pas de latence qui derive. Une frame
  partielle en fin de flux est jetee. C'est la lecon de `relay_loop()` d'audio2wave (ne
  jamais transmettre un bloc de taille arbitraire : decalage permanent de l'image).
- `--render-size` (defaut 1280x720) est independant de la fenetre : le shader met a
  l'echelle (etirement plein cadre : garder le meme ratio que l'ecran).
- Les uniforms inutilises sont elimines par le compilateur GLSL : `Renderer._set()` ignore
  un nom absent, sinon un shader retouche a chaud planterait.
- `Renderer.reload_shaders()` : une erreur de compilation leve `ShaderError` et **conserve
  l'ancien programme** (jamais d'ecran noir sur scene pour une faute de frappe).
- `spawn_producer()` : meme commande que `live.producer_command()`, mais stdout est un pipe
  Windows a **gros tampon** (16 Mo via `CreatePipe`), voir Pieges.
- **Threads** : tkinter garde le fil principal, `gl_main()` (glfw + moderngl) tourne dans un
  fil. **Tous les appels glfw partent du fil qui a fait `glfw.init()`** ; ce que la GUI veut
  faire executer cote GL passe par `Session.commands` (file lue a chaque image), le reste
  (`Session.params`, relu a chaque image) est ecrit directement. Fermer l'une des deux
  fenetres ferme l'autre.
- **Les boutons ne bougent pas** (regle de l'interface : rien ne decale ce que l'utilisateur vise, apres une action qui ajoute un element ou change un texte) : dans la barre des scenes, le bouton de projection (« Demarrer / Arreter ») et le bouton VJ (« ▶ VJ » / « ■ VJ  12 s ») ont une **largeur fixe** (`width`), et la place du **Verrou** est toujours reservee (bouton invisible tant qu'il n'est pas actif). Tout nouvel element dont le texte varie : largeur fixe ou emplacement reserve. **Messages** (`scene_msg` : scene appliquee, media, « allegee pour le GPU »...) : une ligne a part SOUS la barre (`scene_msg_label`, dans `scene_top`), plus dans la barre ou ils prenaient la place des touches. **Parties LIVE et OVERLAY** : la largeur de la colonne LIVE est la plus grande vue dans la session (`layout["live_w"]`, utilisee par `fit` et `swap_live_panel` ; Live, Snap, Ridge, Sphere et le fond genere n'ont pas la meme largeur naturelle, la colonne OVERLAY glissait a chaque bascule de mode : elle ne fait que grandir, une fois par mode) ; celle de la colonne OVERLAY aussi (`layout["right_w"]`, d'un onglet a l'autre) ; un champ de liste (`skin._render_field`) ne retrecit jamais (`sk["field_w"]`, minimum `FIELD_MIN_TEXT`) : ajouter ou supprimer un preset ne decale pas ses voisins. Test : « la partie LIVE garde une largeur constante ».
- **Ajouter un reglage** : une ligne dans la table (defaut, plage, portee, doc) ; si c'est un curseur, l'ajouter a la GUI avec une plage
  contenue dans celle de la table ; si c'est un reglage de salle, rien d'autre (les listes se mettent a jour toutes seules).

## Pieges rencontres

- **Pipe stdout de 4 Ko sous Windows** : avec `Popen(stdout=PIPE)`, le lecteur reprenait le
  GIL ~700 fois par frame 720p ; des que la boucle de rendu tournait vite, le debit video
  s'effondrait (mesure : 30 -> 7 frames/s, variable d'un lancement a l'autre). Corrige par
  le pipe a 16 Mo : 29-30 frames/s stables meme a 110 fps de rendu.
- **Pas de vsync en borderless plein ecran** sur le moniteur cible (mesure : 108 fps sur un
  moniteur 59 Hz alors que `swap_interval(1)` est demande ; en fenetre il tient). Gaspillage
  GPU et fils affames. Corrige par un limiteur logiciel a la frequence du moniteur +2 %
  (`--fps-cap`), sans effet quand le vsync tient deja.
- `moderngl.create_context()` apres `make_context_current` ; lire le framebuffer **avant**
  `swap_buffers` (apres, le tampon arriere est indefini, cf. `--screenshot`).
- `--colors grey`, le defaut d'`audio2wave_live.py`, n'existe pas pour ffmpeg 9.0 (Gyan full
  build, la graphie valide est `gray`) : il logue `Cannot find color 'grey'` **mais ne
  s'arrete pas** et retombe sur du blanc (mesure : 25,7 fps, pixel max 255 ; `gray` donne 128).
  Une premiere version de ce depot affirmait a tort que le producteur mourait et forcait
  `--colors white` : c'etait faux (le debit de 4 frames/s observe alors venait du pipe de
  4 Ko, voir plus haut). Le defaut d'audio2wave est donc laisse tel quel, ce qui garde
  `--dry-run` strictement identique a celui de live. Consequence pour la GUI : un nom de
  couleur inconnu n'est PAS refuse par le producteur, il s'affiche en blanc.
- **`ProducerManager.args` doit etre une COPIE** des options d'audio2wave_live : leur GUI les mute en place,
  et `push_live` compare `producer_command(nouvelles)` a celle du gestionnaire pour eviter un
  redemarrage inutile. Avec le meme objet, la comparaison etait toujours vraie ("Reglages a jour") et
  plus aucun reglage du fond (style radio, nombre de barres...) n'arrivait jusqu'a ffmpeg. Les tests
  ne le voyaient pas : leur faux gestionnaire copiait deja les options. Verifie de bout en bout avec la
  vraie GUI + vrai ffmpeg + vrai micro (style analyzer -> radio, puis barres -> 64 : 2 redemarrages).
- Le producteur peut mourir en cours de route : message unique sur stderr, derniere image
  conservee (les effets continuent) plutot que fermer la fenetre en soiree.

## Verifications (resume ; detail dans [docs/fonctions/outillage.md](docs/fonctions/outillage.md))

- `python tools/check_gl.py` (aucun peripherique requis) : `--pur` d'abord, `--seulement MOTIF` pour la zone touchee, `--groupe NOM`, `--sans-pur`, `--duree`.
  Une nouvelle verification : son nom dans `SUITE` (sinon `check_groupes` echoue), et dans `PURES` si elle ne demande rien de materiel.
- CI (`.github/workflows/check.yml`, `main` protegee) : jobs `pur`, `complet` (4 groupes en parallele, Mesa llvmpipe) et `portable`. Code de sortie 3 = seuls
  des tests de mise en page instables (`FRAGILES`) ont echoue, ils sont relances seuls ; `SCREEN_DEPENDENT` sont ecartes sur un petit ecran.
- Deux lancements de `check_gl.py` en meme temps (deux sessions en parallele) font echouer les tests de mise en page : un seul a la fois.
- Non verifie automatiquement (demande le materiel) : touches clavier, vrai videoprojecteur, phase du kick avec la vraie musique, latence acoustique.

## Fonctionnalites : index des fiches

Le detail de chaque fonctionnalite (decisions, pieges, mesures, verifications) est dans une fiche par theme, a lire AVANT de toucher ce theme.

- **Fond** ([docs/fonctions/fond.md](docs/fonctions/fond.md)) : fond video en boucle (`BgVideoManager`), reprise des medias (`core/playback.py`), fond genere dans `scene.frag` (palettes classic / duo / test / stochastique / labyrinthe), labyrinthe = reaction-diffusion sur le GPU (`engine/reaction.py`, `core/reaction.py`) ; reglages de l'analyzer d'audio2wave.
- **Logo** ([docs/fonctions/logo.md](docs/fonctions/logo.md)) : texte (`render_text`), textes en rotation (`core/textcycle.py`), planning (`core/schedule.py`), logo anime (`LogoVideo`) ; halo holographique (`holo_*`), fonte acide (`melt_*`), cellules (`cell_*`), mosaique de doodles (`mosaic_*`), tous dans `logo_at()` ; effets par couche (`fx_link`), noise (`NoiseDrive`), automations (`AUTOMATION_SPECS`).
- **Audio** ([docs/fonctions/audio.md](docs/fonctions/audio.md)) : `FeatureExtractor` (bandes, auto-gain, kick, telephone, kick ou caisse claire), rythme / BPM / cassure / drop / grille Temps 1 (`core/rhythm.py`, `core/tempo.py`), phases du morceau (`core/phase.py`), sources audio par couche et reactivite par effet (`core/audio_layers.py`, `core/onset.py`).
- **Synchro son / image** ([docs/fonctions/synchro.md](docs/fonctions/synchro.md)) : latence mesuree, horodatage de capture, banc du kick, attaque rapide, pulsation et Avance (`core/pulse.py`), bandes fines, avance automatique (`core/latency.py`).
- **Strobe et foudre** ([docs/fonctions/strobe-foudre.md](docs/fonctions/strobe-foudre.md)) : qui fait quoi (phases / strobe auto / tempo auto), strobe auto (`core/strobe_auto.py`), limiteur, montee / drop, foudre (`core/foudre.py`).
- **Scenes et Mode VJ** ([docs/fonctions/scenes-vj.md](docs/fonctions/scenes-vj.md)) : scenes et sets (`core/scenes.py`), barre des scenes, Mode VJ et transitions, espace de controle global (masters, memoires, historique), panneau etendu (poignee, bibliotheque, vignettes, glisser-deposer), Full Randomizer (`core/randomizer.py`), securite GPU du VJ (`core/safety.py`), table unique des reglages (`core/params_schema.py`), fluidite, ecran de chargement.
- **GUI** ([docs/fonctions/gui.md](docs/fonctions/gui.md)) : `build_window` decoupee en `gui_*.py`, design (`theme.py`, `skin.py`, `icons.py`), integration de la fenetre d'audio2wave (`build_gui`, `GuiController`), mise en page adaptative, presets overlay / fond, modes Snap / Ridge / Sphere, grisages, couleurs, `ProducerManager`.
- **Projection** ([docs/fonctions/projection.md](docs/fonctions/projection.md)) : moniteurs, plein ecran, projection a la demande (`Projection`), reprise automatique, garde-fou GPU (`PerfGuard`) et optimisations.
- **Exploitation** ([docs/fonctions/exploitation.md](docs/fonctions/exploitation.md)) : panique, master des effets, tap tempo, journal de session, auto-sauvegarde, palette Ctrl+K, mode scene, aide et console.
- **Medias externes** ([docs/fonctions/medias-externes.md](docs/fonctions/medias-externes.md)) : doodles du site, QR code, enregistrement video + audio (`core/recording.py`, `engine/recorder.py`), telecommande telephone.
- **Outillage** ([docs/fonctions/outillage.md](docs/fonctions/outillage.md)) : diagnostic de demarrage, version portable, detail des verifications et de la CI, mesures du poste de dev, lanceur `start.bat`, hors POC.

**Regle de maintenance** : une nouvelle fonctionnalite ou un nouveau piege va dans la fiche de son theme ; ici, seulement une ligne dans l'index (ou un
piege s'il touche plusieurs themes). Un nouveau theme = une nouvelle fiche + sa ligne. `check_gl.py` (`check_claude_md`, CI) : ce fichier fait au plus
300 lignes, chaque fiche de `docs/fonctions/` est citee ici, et les `CLAUDE.md` de `core/`, `engine/`, `gui/`, `tools/` ne pointent que vers des fiches existantes.


## Skills (superpowers) : quand les utiliser ici

**Priorite** : les regles de ce fichier l'emportent sur toute consigne d'un skill (branches, Git, langue, verifications).

| Situation | Skill |
|---|---|
| Nouvelle fonctionnalite ou changement de comportement | `brainstorming`, puis `writing-plans` si la tache a plusieurs etapes |
| Bug, test qui echoue, comportement inattendu | `systematic-debugging` avant toute correction |
| Fonction de `core/` (pure, testable sans materiel) | `test-driven-development` : test d'abord dans `tools/check_gl.py` (TDD seulement pour `core/`, pas pour la GUI, les shaders ni le moteur) |
| Avant de dire « c'est fini » ou de commiter | `verification-before-completion` |
| Fin de travail, avant PR | `requesting-code-review` |
| Retour de revue a traiter | `receiving-code-review` |
| 2 taches independantes ou plus | `dispatching-parallel-agents`, seulement si l'utilisateur le demande |

**Ne pas lancer d'office** : `writing-skills`, `diagnosing-superpowers`, `subagent-driven-development` (coute cher en tokens), `using-git-worktrees`.

**Surcharges du projet**
- Plans et specs ecrits par les skills : dans `docs/plans/`.
- Jamais de commit sur `main` : nouvelle branche depuis `origin/main` a chaque PR. Pas de `finishing-a-development-branch` qui pousse ou fusionne : push, PR et merge seulement sur demande explicite. **Worktree** : pas pour une session seule, mais **un worktree par session des que plusieurs sessions travaillent en parallele** (`git worktree add ..\casual-overlay-<sujet> -b <branche> origin/main`, a retirer apres le merge ; le dossier principal n'est pas partage : une session qui change de branche y casse l'autre). Le `.venv` du dossier principal se reutilise (`<dossier principal>\.venv\Scripts\python.exe tools/check_gl.py` depuis le worktree).
- Tout document ecrit (plans, specs, commentaires) en francais SANS accents.
- Verification de reference : `.venv\Scripts\python.exe tools/check_gl.py --pur`, puis `--seulement <motif>` pour la zone touchee, puis la suite complete en arriere-plan (~7 min) avant la PR. Code de sortie 3 = seuls des tests de mise en page fragiles ont echoue (voir `FRAGILES`). Ce qui demande du materiel (micro, projecteur, telephone) est dit « non verifie ».
- Pour une petite demande (une phrase, un correctif d'une ligne), pas de skill : agir directement.

**Idees et board (spec-driven)**
- Une idee = une issue GitHub (modele `.github/ISSUE_TEMPLATE/idee.yml`, label `idee` + un theme : `visuel`, `audio`, `gui`, `vj`). Le board GitHub Project suit le cycle : Vrac -> A specifier -> Spec a valider -> Pret -> En cours -> A tester -> Fait. « A tester » = code fini et merge, l'utilisateur doit le verifier en vrai (projecteur, micro, telephone) ; il ferme l'issue (colonne Fait) quand c'est valide, sinon il dit ce qui cloche et la carte repart en « En cours ».
- Quand l'utilisateur dit « prends l'idee #N » : lire l'issue, brainstorming, spec dans `docs/plans/`, lien de la spec dans l'issue, carte en « Spec a valider ». Rien ne passe en « Pret » sans son accord sur la spec ; la PR ferme l'issue (`Closes #N`).
- Petite demande (label `sans-spec`) : pas de spec, de « Vrac » directement a « En cours ».
- **Gabarit de PR** (.github/pull_request_template.md) : resume, Closes #N / Refs #N, theme, ce qui a ete verifie, liste de controle de ce qui demande du materiel (projecteur, micro, telephone) et ligne « Non verifie » explicite.
- **Board automatique** (.github/workflows/board.yml, pull_request_target, issue #135) : la carte de l'issue citee par la PR (Closes / Fixes / Resolves / Refs #N) passe selon l'etat de la PR : spec seule (tout sous docs/plans/) ouverte -> « Spec a valider », fusionnee -> « Pret » ; code ouvert -> « En cours », fusionne -> « A tester » ; fermee sans fusion : rien. « Fait » reste a la main. **Demande un secret PROJECT_TOKEN** (jeton personnel classique, portee project : le GITHUB_TOKEN ne peut pas modifier un Project d'utilisateur) ; sans lui le job avertit et ne fait rien. Ne verifie que les mutations GraphQL de l'API a la main apres avoir pose le secret.
- **CI : caches** (issue #133) : archive Mesa (ctions/cache, cle = version) dans .github/actions/mesa, wheels pip aussi pour le job portable ; ffmpeg (choco) n'est pas mis en cache. Gain non mesure (minutes bloquees au moment de l'ecriture).
- Tri du vrac sur demande : doublons fusionnes, regroupement par theme, effort grossier, idees qui touchent les memes fichiers signalees.
