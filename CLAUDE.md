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
  controls       tempo effectif, SilenceGuard, strobe (appui), StrobeLimiter, panique, tap tempo, BarTimer
  rhythm         BPM estime a partir des kicks, montee et drop detectes a partir des basses (`Rhythm`, `BpmEstimator`, `DropDetector`)
  setpack        archive .zip d'un set avec ses medias (ecriture, lecture, extraction sure)
  safety         securite GPU du VJ : alleger la scene suivante selon ses effets couteux et la charge mesuree
  autosave       instantane toutes les minutes, reprise apres un arret brutal
  sessionlog     journal de session
  scenes         scenes, sets, memoires de masters (stockage, export / import)
  randomizer     Full Randomizer (banques d'assets, tirages)
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
- **`build_window` decoupee en blocs `gui_*.py`** (`gl_gui.py` : 3675 -> ~2000 lignes, la fonction de ~3500 -> ~1500) : chaque module est un bloc
  contigu de `build_window` **coupe tel quel** (memes noms, meme indentation) en `install(c)`. Les noms de la fenetre dont le bloc depend arrivent
  par le contexte `c` (`types.SimpleNamespace` construit a l'appel dans `build_window`), ceux que le reste utilise sont rendus par `install` puis
  re-lies dans `build_window` (`ask_delete_set = gui_sets_ctl.ask_delete_set`...), si bien que les fermetures d'origine continuent de marcher.
  Un nom defini PLUS LOIN dans `build_window` arrive par un renvoi tardif (`lambda *a, **k: refresh_scene_bar(*a, **k)` pour une fonction,
  `Late(lambda: scene_ui)` pour un objet : `gl_gui.Late` relit l'objet a chaque acces). Les constantes et fonctions de niveau module de `gl_gui`
  (`SCENE_COLOR`, `Zone`, `choice_dialog`...) sont importees dans `install` (import tardif : `gl_gui` importe ces modules, pas l'inverse).
  **Pour extraire un autre bloc** : trouver un bloc contigu dont les dependances sont peu nombreuses (analyse des noms libres avec `ast` : noms
  definis dans le bloc / utilises hors du bloc / a fournir), le couper, generer le module, relier les noms rendus, puis `check_gl.py`. Tout
  etat partage que deux blocs creent doit etre cree AVANT eux (ex. `panel_state`, partage par le panneau etendu et le Full Randomizer, est
  defini dans `build_window`). Les tests lisent les sources de `gl_gui.py` ET de tous les `gui_*.py` (curseurs, reglages lus par le code).
- **Version portable** : `build_portable.APP_FILES` inclut tous les `gui_*.py` (glob) ; `check_portable_modules` verifie qu'aucun module du depot
  importe par le code embarque ne manque (le script de fabrication ne lance pas la GUI : un oubli ne se verrait que sur le PC de la soiree).
- Reste dans `build_window` : mise en page adaptative, barre des scenes (`refresh_scene_bar`), scenes / VJ (`apply_scene*`, `vj_*`), presets,
  helpers de disposition (`add_slider`, `param_slider`...), pont vers le moteur. Suite prevue : continuer le decoupage (scenes / VJ, barre,
  mise en page), sortir le moteur de rendu en `engine/`, puis (si audio2wave est internalise) `sources/` ; la GUI ne devra parler au moteur que
  par `Session` (params, commandes, requetes).
## Design de la fenetre de reglages (`theme.py`, `skin.py`)

Direction : **sombre sobre, un accent fin par partie** (LIVE bleu `#3b82f6`, OVERLAY violet, SCENES ambre), boutons arrondis, radios en
anneau, cases a coche, listes a chevron, curseurs a poignee d'accent, titres de section a repere colore.
- **`theme.py`** (stdlib) : jetons (surfaces `BG` / `PANEL` / `CARD` / `HOVER` / `LINE` / `TROUGH`, texte, accents, rayons, polices) et
  `mix` / `lighten` / `darken`. `BG` et `PANEL` sont ceux d'audio2wave (`common.GUI_*`) : ses panneaux se fondent avec les notres.
- **`skin.py`** : habille les VRAIS widgets Tk (classe, `invoke()`, `state`, variables inchanges : `gui_gates`, `gui_colors`, les tests
  et le code d'audio2wave marchent tels quels) avec des **images arrondies Pillow** (dessinees a 4x, reduites ; coins transparents sur le
  fond du parent). `skin.apply(racine)` parcourt l'arbre **apres la construction**, y compris les panneaux d'audio2wave (sans option dediee dans leurs fichiers) ; idempotent. Appelee dans `build_window` **avant de mesurer `layout["wide_need"]`** (les images changent la largeur
  naturelle : apres, la fenetre se croyait trop etroite et passait en mise en page empilee, 1013 px de haut), par `refresh_scene_bar`
  (barre + panneau etendu) et `choice_dialog`. Un widget habille a une `_sk` ; `configure` / `cget` des classes Button, Radiobutton,
  Checkbutton, Menubutton, Scale sont interceptes **seulement pour lui** (`bg` = couleur de remplissage voulue, le vrai fond du widget
  est celui du parent ; `text`, `width`, `state` re-dessinent ; le `vj_tick` qui reecrit le bouton VJ toutes les 250 ms ne
  redessine que si la cle change).
  - Boutons : neutres (`CARD`) par defaut, **accent de la zone (`skin.zone(conteneur, couleur)`) seulement pour les actions principales**
    (`PRIMARY` : Sauvegarder, Enregistrer) ; le fond d'accent plein d'audio2wave (`option_add`, = `theme.LIVE`) devient neutre ; une couleur propre
    (touche de scene active en ambre) est respectee ; fond = fond du parent -> bouton « fantome » (survol `CARD`). Survol / appui /
    desactive = variantes. Un widget qui a deja une image (vignette, icone) n'est pas touche.
  - Radio (anneau + point) et case (carre arrondi + coche) : `indicatoron=0`, `image` / `selectimage`, a l'accent de la zone.
    OptionMenu : champ arrondi a chevron, largeur = la plus longue entree du menu. Menubutton « ghost » (fleche dans le texte) : survol.
  - Curseur (`tk.Scale`) : le `bg` d'un Scale est la couleur de sa poignee ; un Scale **sans valeur affichee** (les notres) a donc une
    poignee d'accent sur piste sombre ; un Scale **a valeur affichee** (audio2wave : `bg` en est aussi le fond du texte) garde `bg` et
    prend une poignee en relief, accent au survol. Gris (`gui_gates`) = poignee eteinte, **un seul `configure`** (piege de la valeur perimee).
  - Champs (`Entry`) : fond `PANEL`, filet `LINE`, accent au focus, lecture seule sombre (pas de blanc). Titres : `skin.title`
    (repere d'accent + texte, `style_band` et les deux bandeaux de partie l'utilisent) ; **le texte des titres n'est jamais modifie**
    (`tidy_mode_gui` et les tests le retrouvent par leur texte ; les bandeaux d'audio2wave par `widgets["section_titles"]`). Onglets ttk : selectionne = `CARD` + texte d'accent.
  - Couleurs lues sur un widget : `_norm` (nom Tk / systeme -> `#rrggbb`). Les `PhotoImage` sont gardees par racine Tk (`root._skin_photos`,
    plusieurs racines dans les tests). Hauteur : boutons ~24 px, champs 1 px de bord (la fenetre reste sous 700 px).
- **Icones** (`icons.py`, Pillow, aucun fichier image ni police d'emojis : leur rendu varie d'un PC a l'autre) : 50 icones sur une grille de 24 x 24, traits arrondis de 2
  unites (style Lucide), sur-echantillonnees puis reduites ; `icons.image(nom, taille, couleur)`. **Les libelles ne changent jamais** : `icons.for_text(libelle)`
  rend l'icone d'un libelle connu (`TEXT_ICONS`, casse ignoree ; un libelle qui commence deja par un glyphe ▶ ▾ ■ ◀ n'en recoit pas ; un nom libre comme
  « 1  Devil Kinky » non plus), et `skin` ajoute l'image : **boutons** (`_icon_button_image` : forme + icone + libelle dessines ensemble avec la police PIL equivalente
  a la police Tk, `compound="none"` ; ATTENTION : `compound="image"` n'existe pas pour un Button Tk, l'erreur `TclError` est avalee par `apply` et le bouton reste
  nu), **titres de section** (`tick_icon_image` : repere + icone a l'accent), **onglets** ttk (`skin_notebook` : icone grise, celle de l'onglet choisi a l'accent,
  mise a jour sur `<<NotebookTabChanged>>`), **cases et radios** de `ICON_TOGGLES` seulement (`with_icon`). Un libelle sur plusieurs lignes n'a pas d'icone. **Icone seule** (skin.ICON_ONLY : Supprimer, Mettre a jour, Restaurer, Actualiser, Sauvegarder, Dupliquer la derniere, Tirer maintenant, Rescanner, Centrer, Aucun / Aucune, Plein ecran (bouton)) : l'image ne porte que l'icone, le widget garde son libelle (cget, tests), et _tip_enter / _tip_leave l'affichent en info-bulle ; si le code de la GUI a deja pose une info-bulle (celle d'audio2wave est une Toplevel enfant du widget), le libelle en devient la premiere ligne au lieu d'en ouvrir une seconde. **Mode VJ** : la barre des scenes, le panneau etendu (menus SET / ordre / duree : skin pose l'icone sur les menus a fleche dans le texte, Tk la place a gauche ; Renommer / Retirer en icone seule), les onglets et boutons de l'espace de controle (Strobe, Verrou, Montee ↑ / Drop ↓, Tap tempo, Mode silence...) et la **Console VJ** (fenetre a boutons 	k.Button hors habillage : tn(..., icon=...) pose une icone de 26 px, check_gl.py x_console_icons) ont leurs icones. Les actions critiques ou ambigues gardent leur texte (PANIQUE, Demarrer la projection, Diagnostic, Tap tempo, Strobe, Verrou...). Pour
  ajouter une icone : une fonction `_nom(p)` dans `icons.py` (+ son nom dans `_NAMES`) et une ligne dans `TEXT_ICONS` ; `check_gl.py` (`check_icons`) verifie que
  chaque libelle pointe vers une icone dessinee, que toutes se dessinent, et que boutons / titres / onglets / cases gardent leur texte. `icons.py` est dans
  `APP_FILES` du portable (`check_portable_modules` l'a rappele).
- **Accent d'audio2wave** : leur `GUI_ACCENT` (turquoise) devient `theme.LIVE` par le point d'extension `common.set_gui_accent`
  (`apply_a2w_palette()`, avant `live.style_gui` : leurs widgets le lisent a la creation ; Snap / Ridge, importes plus tard, lisent la
  valeur de `common`). `skin` reconnait leurs boutons a accent par `theme.LIVE`.
- **Fenetre « courbe »** (editeur d'audio2wave) : `install_curve_skin` affecte le crochet de classe
  `AutomationManager.default_on_editor_open(manager, fenetre, attr, canvas)`, appele une fois la fenetre construite (aussi pour les
  gestionnaires crees par les `build_gui` d'audio2wave) : `skin.zone` a l'accent du gestionnaire (violet pour le notre, bleu pour
  celui d'audio2wave), canvas sombre, `Label` remis au fond du panneau, `skin.apply`. Le bouton d'edition est `curve_label` (« ∿ » par
  defaut chez eux : plus de boucle qui le restyle ; `check_gl.py` compte les boutons `∿`).
- **Ascenseurs** (`skin.scrollbar`) : le `tk.Scrollbar` de Windows ignore les couleurs (rail gris clair + fleches, un bloc clair dans une
  fenetre sombre, vu sur la page, le panneau d'audio2wave et le panneau etendu) ; on prend un `ttk.Scrollbar` (theme `clam`, seul theme
  dont les couleurs se reglent) au style `Casual.Vertical.TScrollbar` : rail `BG`, poignee discrete, plus claire au survol, accent
  LIVE quand on la tient, 10 px, sans fleches. Meme interface (`set`, `command`, pack / grid), a utiliser pour tout nouvel ascenseur.
  Pas encore habille : la liste du Mode VJ de Snap (dialogue d'audio2wave, leur `tk.Scrollbar`), voir l'audit des accrochages.
- **Menus deroulants et selects** : c'est audio2wave qui rend le survol lisible (`common.readable_fg(accent)` dans `style_gui` et
  `style_option_menu`, a partir de l'accent pose par `set_gui_accent`) ; overlay n'a plus de style de menu (`skin.style_menu` et le correctif
  de `_render_field` supprimes). `apply_a2w_palette` passe `theme.FG` comme texte « sur accent » (`readable_fg` choisit alors du texte clair sur
  le bleu LIVE ; avec le quasi-noir `ON_ACCENT` il choisissait du noir, d'ou le « texte noir au survol » d'avant). Contraste mesure : texte
  `FG` sur `#3b82f6` = 3,0 (clair mais moyen : un bleu plus profond, `#2563eb`, donnerait ~5). `check_menu_style` + le test de la fenetre.
- **Boutons jamais colles** : `BTN_MARGIN` = 3 x 1 px transparents autour de l'image d'un bouton (a hauteur totale constante : la forme
  est plus basse d'autant), meme dans les conteneurs d'audio2wave qui n'ont pas d'espacement.
- `check_gl.py` (`check_skin`) : classe / texte / `invoke` conserves, neutre / accent / couleur propre, desactive et cache d'images,
  redimensionnement, radio / case / curseur / champ / liste / titre, idempotence, widget a image, deuxieme racine Tk, fichiers embarques
  dans le portable ; le reste de la suite (hauteur sous 700 px, info-bulles, mise en page, gates) verifie que rien n'a casse.

## Diagnostic de demarrage et version portable

- **`engine/diagnostics.py`** (stdlib seulement, importe par `audio2wave_gl`) : `run_checks(a2w_dir, synthetic, gui, opengl, ...)` rend des
  `Check(name, level, detail, hint)` (`ok` / `warn` / `fail`) : Python (>= 3.10), modules (obligatoires = fail ; tkinter fail avec
  `--gui` sinon warn ; tkinterdnd2 warn ; sounddevice warn en `--synthetic`), ffmpeg / DirectShow / ffprobe (fail sauf
  `--synthetic`), audio2wave (fail sauf synthetique sans GUI, comme `load_live`), **OpenGL** (`moderngl.create_standalone_context()`,
  `version_code` >= 330 ; rendu logiciel = warn, sauté si moderngl manque), micro (`sounddevice.query_devices`), dossier des reglages
  inscriptible. **Tout est injectable** (`import_module`, `which`, `run`, `create_context`, `sd`, `settings_dir`, `python_version`) :
  les tests simulent un PC sans ffmpeg / OpenGL / micro. `format_report` (rapport, `only_problems` pour les manques), `summary_line`
  (une ligne pour la GUI), `preflight` (`(checks, ok)`), `run_cli` (`--diagnostic`, code 0 / 1).
- **Au lancement** (`main`) : `--diagnostic` rapporte et quitte ; sinon (hors `--dry-run` / `--list-*`) `preflight` s'execute, les
  manques sont imprimes sur stderr, **une erreur arrete le programme (code 1) avec un message clair au lieu d'une trace**,
  `args.diag_checks` garde le resultat. GUI : `scene_msg` affiche une fois `summary_line` ; bouton **Diagnostic** (section *Affichage et analyse*,
  `diagnostic_report`) = verifications refaites sauf OpenGL (resultat du demarrage : pas de second contexte GL pendant le rendu),
  dans `choice_dialog`.
- **Version portable** (`build_portable.py`, stdlib, Windows) : un dossier `dist/casual-overlay-portable/` = `python/` (copie de
  l'installation Python de base du venv, sans Doc / tests / pip, avec tcl/tk, + `site-packages` du `.venv`), les `.py`, `gl_shaders/`,
  `asset/` (fichiers du dossier et de `asset/images/`, ni `animations/` ni `videos/` perso), `audio2wave/` (leurs `*.py`, copie de `sources/audio2wave/`), `ffmpeg/` (ffmpeg.exe +
  ffprobe.exe), `start.bat`, `LISEZMOI.txt`. **Pas de gel (PyInstaller)** : audio2wave est importe dynamiquement et tkinter /
  modules varient selon le mode. `audio2wave_gl.bundled_paths(script_dir, path)` : `audio2wave/` a cote du programme l'emporte sur le
  copie interne (`DEFAULT_A2W_DIR`), `ffmpeg/` est ajoute en tete du `PATH` (donc `shutil.which`, audio2wave et nos sous-processus le
  trouvent). `start.bat` prend `python\python.exe` puis `.venv\Scripts\python.exe`. Le script **verifie le dossier fabrique** avec
  son propre Python (diagnostic + rendu synthetique de 3 s). Verifie a la main : lance avec un PATH reduit a `C:\Windows\System32`,
  tout est trouve dans le dossier (python, modules, ffmpeg, audio2wave), OpenGL et micro detectes. Taille ~520 Mo dont 424 Mo de
  ffmpeg « full » (un build « essentials » est plus leger ; licence GPL du build : ne pas redistribuer publiquement sans la respecter).
  Les reglages restent dans `~/.audio2wave` (pas dans le dossier). `dist/` est dans `.gitignore`.
- `check_gl.py` (`check_diagnostics`) : chaque verification avec des fausses sources, rapport, resume, `preflight`, `--diagnostic`
  en vrai (ce PC, PC sans ffmpeg = code 1, synthetique = avertissement, audio2wave absent), demarrage sans ffmpeg = arret propre sans
  trace, `bundled_paths`, assemblage du dossier portable (sans les animations perso), `copy_tree`.

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
- Ordre des moniteurs : premier moniteur **non principal** (comparaison par adresse du
  pointeur glfw, pas par `==`), `--monitor N` pour forcer. Fenetre `undecorated` calee sur
  le rect du moniteur, pas de plein ecran exclusif.
- Le nom dshow (ffmpeg) sert aussi de recherche par sous-chaine cote sounddevice (WASAPI
  prefere) ; `--audio-device` la remplace. Sinon entree par defaut.
- Textures : la video et le logo sont en memoire "haut en premier" ; `scene.frag` travaille
  en coordonnees origine haut-gauche (`p.y = 1 - v_uv.y`) pour les lire, le FBO scene est
  en orientation OpenGL, donc `post.frag` n'a aucun retournement.
- **Fenetre par defaut, plein ecran sur demande** (`resolve_fullscreen` rend toujours `False` sauf `--fullscreen` ; la case
  « Plein ecran » de la GUI demarre decochee). Les anciens defauts (plein ecran des qu'un second ecran existe) recouvraient le poste de travail en plein ecran sur le moniteur principal, curseur
  masque et GUI cachee dessous (signale en usage reel : "je perds ma souris"; F ou Echap
  rendent la main). `hide_cursor()` : le curseur n'est masque qu'en plein ecran **hors**
  moniteur principal.
- **Projection a la demande (`--gui`)** : `start.bat` n'ouvre que la fenetre de reglages ; la fenetre GL (`gl_main`, fil `gl`) part
  du bouton **Demarrer la projection** (barre des scenes, `toggle_projection`) ou de `--projeter`. `Projection(s)` (audio2wave_gl.py,
  `s.projection`) : `start()` ouvre **en fenetre** sauf `--fullscreen` (`resolve_fullscreen`), jette les commandes empilees projection fermee, lance le fil ; `stop()` positionne `s.proj_stop` (la boucle GL sort,
  `gl_main` ne positionne PAS `finished_event` en mode `--gui` : Echap ferme la projection, pas les reglages) ; rouvrable a volonte
  (glfw reinitialise a chaque ouverture). Un echec (`SystemExit`, exception) est dit dans le statut, la GUI continue. **Une seule fenetre a la fois** : `gl_main` prend `_GL_SLOT` (attend l'ancien au plus 3 s, puis renonce avec « La projection est deja ouverte » au lieu d'ouvrir une 2e fenetre par-dessus la GUI) ; le bouton ignore un second declenchement a moins de 0,6 s (double clic). Fermer les
  reglages (`on_close`) positionne `stop_event` et `finished_event`. Commandes (`send` dans `gl_gui`) : projection fermee, `save` ecrit
  `gl_params.json` directement, `reload` / `hud` / `fullscreen` repondent « Disponible quand la projection est ouverte ». Plein
  ecran : `Window.retarget(pick_monitor(...))` a chaque passage en plein ecran (un projecteur branche apres le lancement est
  trouve) ; **la commande `fullscreen=1` (case, preset qui la coche) est refusee sur l'ecran des reglages** (ullscreen_blocked ; l'ecran principal si leur position est inconnue) sans `--fullscreen` /
  `--monitor` (la touche F, elle, reste permise : la fenetre a le focus et Echap rend la main). **Ecran de la projection** : `Projection.start()` releve un point de la fenetre de reglages (`s.gui_point`, via `s.gui_point_fn` du fil Tk) ;
  `pick_monitor(index, avoid)` choisit un moniteur qui ne le contient pas (la GUI peut etre sur le moniteur non principal) ; s'il n'y
  en a pas, la fenetre s'ouvre sur l'ecran unique, **dans le coin bas droit** (windowed_rect, Window.shared) pour laisser les
  reglages atteignables (`--monitor` explicite l'emporte). Sur un ecran partage ou non, la fenetre est contenue dans le moniteur. Sans `--gui` rien ne change
  (`gl_main` direct). `check_gl.py` : `check_projection` (Projection avec cible et ecrans factices) + bouton dans le test GUI.
- **Texte a incruster** (source `text`, `--text`, GUI : Source > Texte) : `render_text()`
  dessine le texte avec Pillow (plusieurs lignes, police, couleur, alignement), le
  premultiplie comme un PNG (`_premultiplied`, plafond 2048 px) avec une marge transparente de
  0,18 x la taille (le contour lumineux n'est pas coupe), puis `Renderer.set_logo(..., "text")`
  : **tout le reste du logo s'applique** (position, opacite, pulsation, tremblement, contour).
  Differences : la taille du texte est sa **hauteur** (`logo_layout(fit="height")`, reduite si
  elle depasse la largeur), pas un carre englobant qui ecraserait une ligne. La texture n'est
  regeneree que si `logo_signature()` change (texte, police, couleur, alignement, source),
  donc taper est immediat et bouger/redimensionner ne coute rien. Polices : `TEXT_FONTS`
  (14 polices Windows courantes, seules les installees sont proposees), ou un .ttf/.otf par
  chemin ; police introuvable -> repli sur la police integree de Pillow (un texte doit toujours
  s'afficher). Piege : `multiline_textbbox` renvoie des **flottants** pour un texte sur
  plusieurs lignes (et des entiers pour une ligne), d'ou `floor`/`ceil` avant de creer l'image.
- **Logo anime** (source `video`, `--logo-video`, `--logo-key`, GUI : Source > Video) : un second ffmpeg
  (`LogoVideo`, `logo_video_command`) decode le fichier **en boucle** (`-stream_loop -1`) a sa cadence
  native (`-re`) en rawvideo **rgba** vers un pipe a gros tampon (`spawn_big_pipe`, factorise avec
  `spawn_producer`) lu par un `FrameReader` (latest wins) ; `Renderer.draw` reecrit la texture du logo
  depuis le fil GL a chaque nouvelle image (`upload_to`). Taille decodee plafonnee a 960 px (`ffprobe`
  pour la connaitre avant le lancement). Tout le reste du logo s'applique (position, taille en mode
  "boite", opacite, pulsation, tremblement, contour).
  - **Detourage** : il faut un canal alpha dans le fichier (WebM VP9/VP8 avec alpha, MOV ProRes 4444,
    GIF, APNG, WebP anime) ou `--logo-key #00ff00` (filtre `colorkey`, similarite 0,3 / fondu 0,1 fixes)
    pour un MP4 a fond uni. Sans l'un ni l'autre le rectangle entier est opaque.
  - Piege : le decodeur vp9 **natif** d'ffmpeg ignore l'alpha des .webm ; `-c:v libvpx-vp9` (ou
    `libvpx` pour vp8) est force avant `-i`.
  - Alpha **droit** (non premultiplie) dans la texture : `u_logo_straight` fait premultiplier dans
    `logo_at()` (scene.frag), sinon halo sombre. Pas de mipmaps (la texture change a chaque image).
  - Une video morte sans image (codec absent, fichier corrompu) : message dans le statut, logo retire.
    Changer de source arrete le ffmpeg (aucun processus residuel). La signature du logo
    (`logo_signature`) contient fichier + couleur de detourage : les changer relance le decodage.
  - Non automatisable dans la GUI quand la source video est affichee (le curseur "Taille" partage la
    variable de celui de l'image, dont c'est lui qui porte la case `~`).
- Logo : alpha **premultiplie** cote CPU (`load_logo`) et dans le shader, sinon halo sombre
  aux bords ; mipmaps + anisotropie. `logo_layout()` (fonction pure) calcule le rectangle et
  garantit que pulse/jitter ne le sortent jamais du cadre.
- Les uniforms inutilises sont elimines par le compilateur GLSL : `Renderer._set()` ignore
  un nom absent, sinon un shader retouche a chaud planterait.
- `Renderer.reload_shaders()` : une erreur de compilation leve `ShaderError` et **conserve
  l'ancien programme** (jamais d'ecran noir sur scene pour une faute de frappe).
- `spawn_producer()` : meme commande que `live.producer_command()`, mais stdout est un pipe
  Windows a **gros tampon** (16 Mo via `CreatePipe`), voir Pieges.

### Fond video (`bg_mode` = `video`, `--bg-video`, `--background video`)

Troisieme fond : un **fichier video brut** lu en boucle plein cadre, a la place du visuel d'audio2wave. Reglages `bg_video` (chemin, "" =
aucun) et `bg_video_fit` (`cover` = remplit en recadrant, ratio garde ; `stretch`), cles `bg_*` donc dans les presets de fond et dans
`capture_background`. Le shader ne change pas (`u_bg_mode` = 1 seulement pour le motif) : la video arrive par la meme texture que le flux
d'audio2wave. `BgVideoManager` (audio2wave_gl.py, instancie dans `loop`, `s.bgvideo`) lance / arrete le decodage quand mode, fichier ou
cadrage changent : `BgVideo` = `bg_video_command` (ffmpeg `-re -stream_loop -1`, `scale` + `crop` ou `scale`, rgb24 a la taille du rendu,
meme format que le producteur) + `FrameReader` (latest wins, pipe a gros tampon, sous-classe de `LogoVideo` pour `error()` / `stop()`).
`reader_for(params)` rend le lecteur a utiliser, **None tant que la premiere image n'est pas arrivee** (on garde le fond d'avant, jamais
d'ecran noir) ; messages dans `s.status["bgvideo"]` (introuvable, illisible avec le message d'ffmpeg, "choisis un fichier"), affiches
dans le bloc de la GUI. Quitter le mode arrete ffmpeg (aucun processus residuel). Touche B : live -> motif -> video (seulement si un fichier
est choisi). Scenes : `capture_scene` range un fond video comme un fond genere (`bg` = `"video"`, `fond` = `capture_background`, pas
d'etat audio2wave) et `scene_values` rend le mode, le fichier et le cadrage. GUI : bloc `bgvid_box` dans le meme `pattern_holder` que le
fond genere (un seul des trois contenus visible, `show_bg`), fichier tape sans Entree valide avant une capture (`flush_pending_inputs`),
glisser-deposer d'un fichier sur le bloc (`drop_bg_video`, `a2w_from_file` : une video seulement) qui passe le fond en mode video.
`check_gl.py` (`check_bg_video`, `gui_bgvideo_steps`) : commande, cover / stretch, decodage reel en boucle, relance au changement,
arret propre, fichier absent / vide / corrompu, scenes, trois modes exclusifs, depot.

### Fond genere (`--background pattern`, touche B)

Second mode de fond, a cote du spectre ffmpeg (`bg_mode` = `live` | `pattern` | `video`) : le motif
du banc de test (degrades qui defilent + damier qui bascule), calcule **dans `scene.frag`**
(`background_pattern()`), donc sans flux video ni limite de numpy. Palette `classic` = le
visuel d'origine (canaux R/V/B en dents de scie, damier de 80 px a 720 p, valeurs reprises
de `SyntheticVideoStream`) ou `duo` = degrade lisse (aller-retour, sans couture) entre deux
couleurs avec un angle. Reglages (tous dans `params`, cles `bg_*`, sauves avec P) : palette,
couleurs 1/2, angle, teinte (rotation autour de l'axe du gris), vitesse, taille des carreaux
(en px pour 720 px de haut, mise a l'echelle), contraste du damier, cadence de bascule,
reaction a l'audio (flash au kick, damier qui bascule a chaque kick, defilement accelere par
les basses).
- **Palette `test` ("Banc de test")** : copie **octet pour octet** de `SyntheticVideoStream._frame` (`test_pattern()`
  dans scene.frag, numero d'image `u_bg_frame` = horloge reelle x 30, `_bg_clock`). Aucun reglage du motif ne
  s'y applique (teinte, taille, contraste, vitesse, reaction) : carreaux de 80 px fixes. Elle reproduit un
  **defaut du flux de test** : `x*255` y est calcule en uint16 et deborde pour x > 257, d'ou un degrade coupe
  en bandes ; c'est ce qui la distingue d'Arc-en-ciel (version "propre" du meme visuel). `check_gl.py` compare
  8 images au flux de test, ecart 0.
- Les phases d'animation (`_bg_scroll`, `_bg_flip_t`) sont **integrees image par image**
  (`dt * vitesse`), pas calculees par `vitesse * temps` : changer la vitesse dans la GUI ne
  fait jamais sauter le motif.
- Le producteur ffmpeg continue de tourner en mode motif (negligeable, et le retour au
  spectre est instantane) ; la GUI n'affiche alors plus son statut.
- Le logo et tous les effets post-traitement s'appliquent aussi au motif.

### Halo holographique (`holo_*`, touche C)

Lumiere irisee a **grande portee** qui **deforme le fond** autour du logo (image, texte ou video) et y
traine de la poussiere d'etoiles ; **le logo reste intact**. Il remplace un premier "halo chrome" (relief
du logo + faux studio reflechi, ajoute en lumiere juste autour des traits) juge "completement flou" : il
etalait de la lumiere sur un logo a traits fins et n'avait qu'une portee de quelques pixels.
Trois passes, toutes dans le post-traitement :
1. **Champ** (`holo.frag`, **basse resolution**, 1/4 de l'ecran, `HOLO_DOWNSCALE`, RGBA16F) : alpha de la
   couche logo flou a 7 echelles par ses **mipmaps** (la couche logo est a la taille de l'ecran et ses
   mipmaps sont construites chaque image, filtre `LINEAR_MIPMAP_LINEAR` seulement quand c'est le cas, sinon
   texture incomplete = noire). Aux gros niveaux, 4 prises decalees cassent la structure en blocs des
   mipmaps. Sortie : `r` = distance **logarithmique** au logo (0 loin .. 1 contre lui), `gb` = son gradient
   (vers le logo), `a` = densite locale du logo. Calcule a 1/4 de resolution = ~16 fois moins de lectures
   que dans le post plein ecran (un premier jet plein ecran etait trop cher pour un GPU integre) et bien
   plus lisse.
2. **Portee** (`holo_reach`) : homothetie du champ autour du centre du logo (`u_holo_center`), donc
   monotone ; la faire varier via l'echelle du flou ne l'etait pas (plus de flou = halo plus *petit*).
   Fondu radial (0,30 -> 0,75 hauteur d'ecran, avant homothetie) + niveaux de flou bornes : sinon les mipmaps
   tres grossieres laissaient un **plateau** sur l'ecran entier (logo hors centre) et des bords durs.
3. **Application** (`post.frag`, `holo()`) : deformation du fond = deplacement le long du gradient modules par
   une onde `sin(v*24 - phase)` (ondes concentriques qui partent du logo) + dispersion spectrale (rouge
   pousse plus loin que bleu) dans `layer_fx(..., disp)` ; lumiere = palette irisee (franges sur les courbes
   de niveau, teinte = distance + direction + temps, **sans `atan`** : sa coupure d'angle faisait une
   couture de teinte) ; poussiere d'etoiles sur une **grille polaire** centree sur le logo qui derive vers
   l'exterieur, coeur etire le long du rayon (traine). Le logo est pose par-dessus, jamais deforme par ceci.
   `field.a` eteint lumiere, deformation et poussiere **dans** le logo : avec le PNG a traits fins du
   depot, sinon le halo se voyait a travers ses trous et le rendait illisible.
Teinte : palette irisee **tres metallique, peu saturee, un peu terne** (`HOLO_METAL` 0,70 = part de reflet
d'acier froid qui vire au champagne, `HOLO_SAT` 0,62, `HOLO_DULL` 0,86, constantes en tete de `holo_palette`
dans post.frag). Poussiere : **fluide de grains minuscules** : trois couches de grains de 3 a 8 px a 720 p
(coeur doux, quasi ronds) dont la densite suit des voiles lents qui ondulent et derivent (`flow`), plutot que
des etoiles isolees.
Reglages : `holo_on`, `holo_intensity` (lumiere) 1, `holo_reach` 1,3 (1 = ~1,5 x la taille du logo),
`holo_warp` 1, `holo_dust` 1, `holo_speed` 0,15, `holo_react` **0** (defaut : non audioreactif ; > 0 =
intensite et deformation au kick, animation acceleree par les basses). `u_holo` = interrupteur x reaction,
`u_holo_light` = intensite : **deformation et poussiere sont independantes de l'intensite** (un test a
revele qu'a intensite 0 tout s'eteignait). La phase est **integree** (`_holo_t`), comme celle du motif.
- Additif : la lumiere ne fonce jamais le fond, mais la **deformation** se voit aussi sur un fond sombre.
- Plus de marge transparente sur les textures du logo (l'ancien `LOGO_PAD`) : elle n'avait de sens que
  pour le chrome, et le logo retrouve exactement son echantillonnage d'avant.
- Actif par defaut ; `check_gl.py` le coupe pour les comparaisons de pixels
  (`DEFAULT_PARAMS["holo_on"] = 0`) et `check_holo()` le rallume. GUI : case "Halo holographique" et ses
  six curseurs dans l'onglet "Aura du logo".

### Fonte acide du logo (`melt_*`, touche M)

Le logo (image, texte ou video) se **dissout sur place** puis se reforme, en boucle. Tout est dans
`logo_at()` de `scene.frag`, donc dans la **couche logo** : la lisiere de glow, l'aura holographique (son
champ est calcule sur l'alpha de cette couche) et les effets par couche suivent la forme rongee.
- `melt_field()` : bruit de valeur a trois octaves, dans le repere du logo (isotrope a l'ecran, colle au logo
  quand il bouge), compare a un seuil `m` qui monte avec `u_melt`. Sous le seuil : la matiere disparait
  (`vis`) ; juste au-dessus (`band`) : elle **vire a l'acide** (remplacement de couleur, pas seulement additif :
  un logo blanc + additif ne donnait qu'un jaune pale) avec un coeur chaud (`core`) et un liseré additif.
  La matiere s'affaisse un peu (`q.y` decale) la ou elle cede. Rien ne deborde du rectangle du logo.
- Cycle cote CPU (`Renderer.draw`) : phase **integree** `_melt_t` (cycles), triangle 0 -> 1 -> 0 adouci avec
  un palier intact et un palier dissous ; `melt_depth` plafonne l'amplitude, `melt_period` en secondes.
  `u_melt` = 0 coupe tout (`melt_on` = 0, le defaut : une marque ne doit pas disparaitre sans l'avoir voulu).
  La branche `u_melt > 0.001` evite le cout du bruit quand l'effet est coupe (logo_at est appelee ~25 fois
  par pixel pour le glow).
- Reglages : `melt_on`, `melt_depth` 0,9, `melt_period` 14 s, `melt_scale` 1 (grain), `melt_edge` 1 (lisiere),
  `melt_color` `#a6ff00`, `melt_react` **0** (> 0 : cycle accelere par les basses). GUI : onglet "Fonte du logo".
  `check_gl.py` (`check_melt`) : intact aux deux bouts du cycle, symetrique, lisiere acide, rien hors du logo.

### Cellules organiques du logo (`cell_*`, touche V)

Le logo **se decompose en cellules** (Voronoi + cell shading) ; il garde sa forme. Comme la fonte, tout est dans
`logo_at()` de `scene.frag` (`logo_cells()`), donc dans la couche logo : glow, aura et effets par couche suivent.
- Une premiere version remplissait des cellules **dans le rectangle du logo** (champ de metaballes + logo flou) :
  elles reprenaient la zone occupee et non la forme (signale : "les cellules ne reprennent absolument pas le
  logo"). Remplacee par une **decomposition du vrai logo** : chaque cellule est un disque qui contient le morceau
  de logo correspondant (`qs` = position du morceau dans le logo : `graine + (v - centre)`), et qui glisse
  (`wob`, ondes triangulaires bon marche), s'ecarte du centre et se retrecit avec `u_cell` (decomposition). Les
  pixels sans logo restent vides car la couleur et l'alpha viennent du logo echantillonne en `qs`.
- Grille jitteree de graines (`cell_base`), 3x3 candidates par pixel, la cellule retenue est celle dont le
  disque (rayon `0,75 x cell_fusion x (1 - 0,25 x decomposition)`, un peu inegal d'une cellule a l'autre) contient
  le pixel au plus pres de son centre. Deplacement <= 0,28 case et rayon <= 0,9 : **3x3 suffisent** (au-dela,
  des cellules seraient coupees net). A `u_cell` = 0 le logo est intact (melange avec l'original par `u_cell`).
- Rendu cell shading : aplat de la couleur moyenne du logo autour de la graine (lod 3) melange a la couleur
  d'origine par `cell_flat`, deux tons, reflet de bulle, contour en `cell_ink`, translucide.
- Pieges : **`textureLod` et non `texture`** dans cette fonction (retour anticipe = derivees fausses, mauvais
  niveau de mipmap aux frontieres des cellules, traits parasites) ; hachage **sans `sin`** (`chash3`, un seul appel pour les trois valeurs d'une cellule), car
  `logo_at` est appelee ~13 fois par pixel avec le glow ; les cellules restent **dans le rectangle du logo**
  (les textes ont une marge transparente de 0,18 x la taille).
- Reglages : `cell_on` 0, `cell_amount` 0,65 (decomposition), `cell_fusion` 1 (rayon des cellules, 0,2 a 1,2),
  `cell_scale` 1, `cell_speed` 0,3, `cell_flat` 0,85, `cell_ink` `#14103a`, `cell_react` **0** (> 0 : derive
  acceleree par les basses, cellules gonflees au kick). GUI : onglet "Cellules". `check_gl.py`
  (`check_cells`) : intact a 0, decompose, couleurs gardees, **les cellules ne couvrent que la forme du logo** (logo
  a moitie transparent), rien hors du rectangle, contour de la couleur choisie, derive, audio.

### Doodles du site (case « Recuperer les medias », onglet Affichage)

Recupere les fichiers deposes sur le site Casual Ravers (bucket Supabase Storage public `doodles` : PNG / JPEG, 5 Mo max) et les affiche **par-dessus
la projection**. Desactive par defaut.
- **Configuration** : `~/.audio2wave/depot.json` = `{"url": "https://<projet>.supabase.co", "anon_key": "..."}` (jamais dans le code ni versionne). Absente
  ou incomplete : la case reste active, le statut (ligne d'etat de la GUI) dit ce qui manque ; la config est relue a chaque tour tant qu'elle manque.
- **Reglage `doodle_on`** : portee `state` (ni sauve par P, ni dans l'auto-sauvegarde, ni presets overlay / fond, ni scenes, ni memoires de masters) ;
  PANIQUE n'y touche pas (comme le logo et le texte). Decocher arrete l'interrogation ; les doodles deja affiches finissent leur fondu.
- **`core/doodles.py`** (pur) : `parse_config` / `load_config`, `list_request` (POST `/storage/v1/object/list/doodles`, `sortBy created_at desc`, `limit` 100 :
  les plus recents d'abord, sinon passe 100 fichiers les nouveaux n'apparaitraient plus), `download_url`, `parse_listing`, `Seen` (`arm` a l'activation =
  l'existant est memorise sans etre affiche ; `fresh` du plus ancien au plus recent ; `mark`), `check_download` (vide, > 5 Mo, signature PNG / JPEG : le
  suffixe ne prouve rien), `check_size` (<= 4096 px), `place` (rectangle tire au hasard, toujours entierement dans le cadre, hauteur 30 % de l'ecran, ratio
  garde), `alpha` (fondu 1,5 s / palier / fondu 2 s sur 15 s), `Wall` (5 visibles au plus, le plus ancien part), `poll_once` (un tour sans fil : reseau injecte).
- **`engine/doodle_feed.py`** : `DoodleFeed` = fil d'arriere-plan (tick toutes les 250 ms, interrogation toutes les 5 s, timeout 6 s), jamais dans le fil GL
  ni Tk. Une erreur reseau est dite dans le statut (`status["doodles"]`), journalisee **une seule fois** (`sessionlog`) et reessayee au tour suivant ; un
  telechargement en echec n'est pas marque « vu » (reessai), un fichier invalide l'est. Decodage Pillow (`decode` : taille lue avant tout decodage,
  `exif_transpose` pour les photos, `_premultiplied` comme le logo) puis `Session.doodle_queue`. Demarre par `run_app` (le fil dort tant que la case est decochee).
- **Rendu** (`Renderer.add_doodle` / `_draw_doodles`) : la boucle GL vide la file a chaque image. Chaque doodle visible relance la **passe LOGO de `scene.frag`** (`u_pass` 1) avec SA texture et SON rectangle a la place
  de ceux du logo, melangee en alpha premultiplie sur la couche du logo (`layer_fbo`) : il recoit donc **tout ce que recoit le logo** : fonte acide, cellules, contour lumineux, opacite / master overlay, puis (dans `post.frag`) les effets par couche
  (ondulation, onde de choc, aberration, glitch ; lies au fond ou propres au logo, `fx_link`), l'aura holographique (calculee sur l'alpha de la couche ; centree sur le logo), la traînee et la composition (symetrie, pixelisation, teinte, strobe).
  `u_layer_on` (post.frag) garde la couche active meme sans logo. Pas de shader dedie (doodle.vert / doodle.frag supprimes). Liste vide = aucune passe (aucun cout) ; au plus 5 doodles = 5 passes plein cadre en plus.
  Les doodles arrives projection fermee sont jetes (`Projection.start`). 
  **Animation** (`core.doodles.pose`, `Wall.posed`) : apparition en « pop » (echelle 0 -> 1 avec depassement de ~9 %, rotation de ~17 degres qui se redresse en balancant, 0,9 s), palier droit, disparition (gonfle un instant, rapetisse en pivotant, 0,9 s) ; le sens de rotation alterne d'un doodle a l'autre. Fondus d'opacite courts (0,4 s / 0,7 s). Le shader du logo sait pivoter (`u_logo_cs` = cos, sin ; sin = 0 pour le logo, qui reste droit) : fonte, cellules et contour suivent la rotation.
- `check_gl.py` (`check_doodles`) : logique pure, fil avec faux reseau et horloge injectee, **vrai serveur HTTP local** (en-tetes, corps, 404, serveur
  injoignable), rendu hors ecran (taille, ratio, position, sens, alpha premultiplie, fondu, limite de 5, expiration, panique), reglage hors presets /
  sauvegarde, case de la GUI.
### Noise : un hasard unique et prereglé (`noise_amount`, `noise_fx`)

**Un seul noise, non parametrable** (`NoiseDrive`, audio2wave_gl.py) : une valeur continue au hasard (`noise_value`, noise de
valeur 1D a hachage entier, deterministe ; `NOISE_SPEED` 3 valeurs/s, `NOISE_SMOOTH` 0,8, constantes en dur) dont on tire,
comme d'une **musique imaginaire**, une **houle** douce 0..1 (`swell()`, comme les basses) et des **coups** au hasard
(`beat()` = enveloppe 1 -> 0, `since` = secondes depuis le dernier ; un coup part quand le noise remonte franchement :
hysterese + delai minimum 0,45 s, environ un coup toutes les 1,7 s). Phase integree image par image.
- Deux reglages seulement : `noise_amount` (force 0..1, defaut 0,6 ; 0 = aucun effet) et `noise_fx` (5 cases : wobble, onde de
  choc, aberration, glitch, reaction du logo). **Rien n'est coche par defaut : le rendu est celui d'avant.**
- Il **s'ajoute a l'audio reel** (`max(reel, noise)`) pour les effets coches, donc il agit **meme sans aucun son** : une premiere
  version multipliait seulement l'intensite des effets (x `1 + dosage x bruit`), ce qui ne se voyait pas puisque tous les effets
  sont pilotes par l'audio (ondulation = basses, onde et aberration = kick, glitch = seuil) ; signale "je ne percois pas son effet".
- Shader (`post.frag`, `layer_fx`, uniforms `u_nz`, `u_nz_beat`, `u_nz_since`, `u_nz_amount`, `u_nz_fx`) : wobble =
  `max(basses, houle)`, aberration = `max(kick, coup)`, glitch declenche par un coup, onde de choc partant du dernier coup s'il
  est plus recent que le dernier kick (force `u_nz_amount`). S'applique aux deux couches (fond et logo). La reaction du logo
  (indice 4 : pulsation, tremblement, contour) est calculee cote CPU dans `Renderer.draw` (`beat_l`, `high_l`, `bass_l`). Seuls
  les effets **allumes** dans l'onglet Effets bougent : cocher le noise sur un effet coupe ne l'allume pas.
- Les cles sont dans `DEFAULT_PARAMS` (donc dans les presets overlay et la sauvegarde P, jamais dans les presets de fond).
- GUI : onglet **Noise** : curseur Intensite, 5 cases "Le noise agit sur", et un indicateur (houle / coup, relu dans
  `s.status["noise"]` que la boucle GL met a jour) qui montre ce que le noise envoie aux effets.
- `check_gl.py` (`check_noise`) : plage, determinisme, cadence des coups et delai minimum, houle ni plate ni saturee, dt = 0,
  rendu **sans aucun son** (chaque effet coche bouge ; rien de coche ou intensite 0 = image inchangee) et GUI (cases, preset).

### Scenes (core/scenes.py, barre en haut de la GUI, F1 a F9)

Une scene change tout le look d'un geste. **Scene = (fond audio2wave OU motif genere) + overlay** (le motif genere REMPLACE le
visuel d'audio2wave, il ne le complete pas) :
- `bg` = `"pattern"` : la scene retient `capture_background` (reglage du motif) ; le mode d'audio2wave n'est pas touche.
- `bg` = `"live"` : la scene retient le **mode** (live / snap / ridge) et **l'etat de son panneau** (`live_overrides`, meme forme
  qu'un preset d'audio2wave, automations comprises) **dans scenes.json : jamais dans les presets d'audio2wave** (une scene et un
  preset sont deux choses differentes ; une premiere version rangeait un preset `scene-<nom>` chez eux, qui apparaissait dans
  leurs menus). Capture (`capture_live_overrides`) : `GuiController.capture_state()` (rien n'est ecrit chez eux, aucun widget actionne).
  Application (`apply_live_overrides`) : `GuiController.apply_state(overrides)` (comme charger un preset, sans ecriture ; en live il
  relance aussi le producteur ; les cles inconnues sont rendues et signalees). Avant, un nom temporaire `scene-tmp` passait par leur
  bouton Sauvegarder et la ligne `default` de leur menu etait cliquee avec un faux magasin : tout cela est supprime. Pas d'etat
  audio2wave pour une scene a motif.
- **Exclusions** (`scenes.SCENE_EXCLUDED` = `device`, `fullscreen`, `size`, filtrees par `strip_live_overrides` a la capture, a
  l'application et a la migration, donc aussi pour les scenes deja enregistrees) : une scene change le look, pas
  l'installation. L'**entree audio** reste celle qui est active ; le **plein ecran** (case de leur panneau Live, qui commande
  la fenetre GL par `fullscreen=0|1`, et que le preset `club` de Snap coche) n'est jamais touche par une scene.
- **Fenetre de reglages gardee a sa taille pendant le VJ** : un changement de mode remplace le panneau d'audio2wave, dont la largeur change (vu : 1496 -> 1490 px) et, sans geometrie imposee, Tk redimensionne la fenetre.
  `swap_live_panel` memorise `keep_size` quand `s.vj_state["running"]` et la rejoue apres le remplacement (`root.geometry`). Piege de test : un choix **a la main** met le VJ en pause (donc pas de `keep_size`) ; il faut faire passer
  la scene par `vj_advance`, et partir de `root.geometry("")` (une geometrie deja imposee par un test precedent masque le defaut).
- **Migration** (`migrate_scene_presets`, au debut de chaque `build_window`, avant leur panneau pour que leurs menus soient
  propres) : une scene qui porte encore `live_preset` (anciennes versions) recupere ce preset dans le magasin de son mode,
  le range dans `live_overrides` et le supprime de leurs presets. Verifie sur une copie des vrais fichiers de l'utilisateur
  (5 scenes migrees, magasins live / snap propres).
- dans les deux cas : `capture_overlay` (jamais de `bg_*`). `scene_values(scene, current)` fabrique les reglages a poser
  (overlay + soit le motif, soit seulement `bg_mode = live` ; automations `bg_*` du fond, les autres de l'overlay).
- Stockage : `SceneBook` (`~/.audio2wave/scenes.json`) : **bibliotheque de scenes** (sans limite, meme nom a la casse pres =
  remplace) + **sets** (voir plus bas). Le rang d'une entree dans le set actif donne la touche F1..F9 (`scenes.KEYS` = 9
  premieres ; les suivantes n'ont pas de touche F mais ont leur bouton dans la barre).
- **Changement de mode** (Live -> Snap...) : `request_mode` ne reconstruit QUE le panneau d'audio2wave (`swap_live_panel`, ~0,25 s) ; le preset est garde dans
  `s.pending_live_overrides` et applique a la fin de `build_window` du panneau neuf (`after(150)`).
- **Interface** : rangee ambre posee en ligne 0 de `root` (au-dessus de la page defilante : toujours visible), une touche par
  scene (active = ambre) dans une **bande defilante** (`Canvas` `proj_ui["strip"]`, posee EN DERNIER avec `expand` : les boutons du VJ posees avant ne sont jamais repoussees ; molette via `on_wheel`, fleches ◀ ▶ visibles seulement si tout ne tient pas, la touche active est recentree si elle sort de la bande, `proj_ui["strip_x"]` garde la position d'une reconstruction a l'autre ; plus de menu `+N`), « + Nouvelle scene » (champ de nom + Enregistrer / Annuler, Entree / Echap), clic droit = mettre a
  jour (capture l'etat actuel) / supprimer (confirmation). `refresh_scene_bar` reconstruit la rangee.
- **Pas de clignotement a la reconstruction** : (1) `refresh_scene_bar` calcule une signature de ce que la barre affiche (`bar_signature` : lignes, sets, menus, verrou, etc., SAUF la scene active) ; identique = la barre n'est pas detruite, seule la touche active est repeinte (`proj_ui["bar_btns"]`, `bar_show`), donc le VJ qui enchaine les scenes ne la reconstruit plus. (2) `build_window` (changement de mode, panneau neuf) prend d'abord une image de la fenetre (`grab_window`, Pillow `ImageGrab`) et la pose dans un `Toplevel` sans bordure par-dessus (`show_cover`) pendant la destruction / reconstruction, puis la retire 350 ms apres (`drop_cover`) : on ne voit plus les widgets se recreer. `CASUAL_NO_COVER=1` desactive l'image figee ; capture impossible = comportement d'avant.
- **Fluidite de l'interface** (`core/uistats.py`, stdlib, horloge injectable ; `gl_gui.STATS`) : `Stats` chronometre les operations nommees (`STATS.timed` / `STATS.watched` : `refresh_scene_bar`, `refresh_scene_panel`, `build_window`, chaque callback de `after()`, sections `build.*` / `panel.*` / `bar.*`), signale dans le journal de session une operation de plus de 80 ms (au plus une fois par seconde et par nom) ;
  `StallWatch` (minuterie de 50 ms dans `run_gui`) detecte un **gel** du fil Tk (retard > 150 ms) et l'attribue a l'operation la plus longue de la fenetre ; menu « reglages VJ » -> **Rapport de fluidite de l'interface** (pire temps de chaque operation, derniers gels) ; `check_gl.py` imprime ce rapport a la fin (information).
  `Scheduler` : **une seule minuterie** pour tous les rafraichissements periodiques de la fenetre (`ui_every(nom, ms, fn)` / `ui_remove` : voyant de kick et BPM, rythme, voyant de l'onglet Audio, statut `refresh`, mesures `meter`) au lieu d'un `after` par rafraichissement ; une erreur dans une tache est journalisee et n'arrete pas les autres. Mesure : `bar.mesure` (update_idletasks de la barre, ~100 ms) n'est plus refait a chaque reconstruction (hauteur gardee) ; `build_window` ~1,5 s dans les tests : destruction des ~2500 widgets ~450 ms, panneau d'audio2wave ~90 ms (+ ~300 ms de ffmpeg pour la liste des entrees audio, maintenant gardee 60 s pendant une reconstruction : `install_device_cache`), habillage ~90 ms, le reste = construction des panneaux overlay.
  **Changement de mode sans tout reconstruire** : `build_live_panel(mode)` construit le panneau d'audio2wave dans un **cadre neuf** (`panel["frame"]`, enfant de `host`, c'est ce que les tests appellent `live_host`) : le detruire supprime aussi les minuteries `after` d'audio2wave liees a ce cadre (sinon la boucle d'un ancien panneau continue sur des widgets disparus : TclError). `swap_live_panel` : migration des anciennes scenes, `host_item` cache pendant la construction (jamais peint a moitie), habillage, bandeau (`mode_ref["v"]`), `layout["wide_need"]` ajuste de la difference de largeur des panneaux, `on_page_config`, taille gardee pendant le VJ, etat en attente (`pending_live_overrides`), puis `on_ready(controles)` rappelee (fabrique `controls_ref["f"]`). Le pont ffmpeg (`poll_bridge`, ordonnanceur) n'agit qu'en mode Live. Mesure : 1,5 s -> ~0,25 s.
  **Travaux lourds hors du fil Tk** : scan des banques du Full Randomizer (`random_bank(block=False)` : fil, banque precedente affichee, « scan en cours... », rafraichissement du panneau a la fin ; un tirage attend le scan avec `block=True`), export et import d'archive depuis le menu (`run_job` dans `gui_sets` : fil + message de progression ; avec un chemin donne `wait` est vrai et on attend, c'est ce que font les tests), vignettes (deja en fil), apercus (fil GL). **Panneau etendu** : les 10 premieres lignes sont construites tout de suite, les autres par lots de 4 entre deux evenements (`ROWS_FIRST`, `ROWS_BATCH` dans `gui_panel`, `panel_state["gen"]` abandonne un lot obsolete).
- **F1..F9** : `key_actions` du fil GL -> `s.scene_requests` (file) -> `poll_scene_requests()` dans `refresh()` du fil tkinter
  (la scene s'applique dans le fil de la GUI ; sans `--gui` : message). Un rang sans scene : message, rien ne casse.
- Pieges : (1) la barre en ligne 0 faisait calculer a Tk une taille transitoire (largeur minimale) avant que la page ait la
  sienne : la fenetre s'ouvrait empilee ; `layout["settled"]` interdit de passer en empile pendant les 400 premieres ms.
  (2) la hauteur : +36 px de barre, d'ou `LIVE_MAX_HEIGHT` 640 -> 600 pour rester sous 700 px. (3) l'ecran de test ne doit
  jamais ecrire dans les vrais fichiers : `scenes.SCENES_PATH` et `live.preset_store.path` sont remplaces le temps de
  `run_gui` ; le message d'audio2wave affiche pourtant le chemin reel (constante), sans y ecrire.
- **Mode VJ** (enchainement des scenes, dans le meme bloc de `build_window`) : decisions prises avec l'utilisateur : **duree fixe
  en secondes** (pas en mesures), transition **flash / glitch court**, **dans l'ordre ou au hasard** avec toutes les scenes,
  pilotage par **bouton dans la barre + touches**. `s.vj_state` = `{"running", "t_next"}` (survit aux reconstructions de la
  fenetre ; chaque `build_window` relance sa boucle `vj_tick` toutes les 250 ms, garde par `alive`). Reglages (`seconds` 5..300,
  `order` `seq` / `random`) **propres a chaque set** (`SceneBook.vj()` / `set_vj()` lisent et ecrivent ceux du set actif). `scenes.next_scene_index(count, current, order, rng)` (pure) : `seq` = la suivante en boucle (la
  premiere si aucune n'est active), `random` = une autre, jamais la meme deux fois de suite.
  - **En rythme** (`vj["unit"]` = `bars`, `vj["bars"]` dans `scenes.VJ_BARS` = 1, 2, 4, 8, 16, 32, propres a chaque set, menu « reglages VJ » et menu « duree par defaut » du panneau etendu) : le changement a lieu apres N mesures de 4 kicks detectes (`core.controls.BarTimer`, pure ; le compteur est `analyzer.beats`). Garde-fou : sans kicks, le changement a lieu apres le double de la duree attendue au BPM detecte s'il y en a un (sinon 120 BPM x master du tempo), au moins 8 s : le VJ ne reste jamais fige. Une duree propre a l'entree reste en secondes. `gl_gui.vj_arm(own)` arme le minuteur (a la place de `vj["t_next"] = ...`), `vj_due()` le teste, `vj_remaining_text()` donne « 12 s » ou « mesure 3/8 » (bouton, console, titre).
  - `vj_advance` : scene suivante + `s.commands.put("flash")` + minuteur relance ; `vj_toggle` (refuse si < 2 scenes) ;
    **choisir une scene a la main** (`apply_scene`, `apply_entry`, F1..F9 compris) **met le VJ en pause** (`apply_entry_core` /
    `apply_scene_core` sont les versions que le VJ appelle). Espace / fleche droite : le fil GL met `"vj"` / `"vjnext"` dans `s.scene_requests`, la GUI les lit.
  - **Flash** : `Renderer.flash_t` (instant monotonic), pose par la commande `"flash"` dans le fil GL ; `u_flash` = carre de
    `1 - age / 0,55 s` dans `post.frag` : melange vers le blanc (0,8) + force des bandes de glitch (0,9) meme avec les effets
    coupes. Ne passe pas par `params` (`save_params` ecrit tout `params` dans `gl_params.json`).
  - **Espace de controle global** (`build_master_panel`, a droite de la liste du panneau etendu, donc visible seulement panneau ouvert ; touches S, K et Retour arriere dans la fenetre de rendu restent actives) : un `ttk.Notebook` de huit onglets (le dernier, *Affichage*, = analyse audio, plein ecran, diagnostic) (l'onglet choisi survit aux reconstructions, `s.master_tab`), tout en **curseurs** (double-clic sur le nom = valeur d'origine) :
    - **Masters** : Reac, Tempo, Overlay, Fond, Teinte (details ci-dessous). **Live** : Strobe (bouton, modes Manuel / Kick / Leger en boutons radio, filtre sombre, 1 kick sur N), **Montee / Drop** (`start_build` / `do_drop`, `params["build"]` 0..1 sur `build_time` s (defaut 45 s, jusqu'a 180 s ; ensuite reste a 1 jusqu'au drop) : reactivite x(1+build), eclairs automatiques dans `Renderer._strobe` (`controls.build_strobe_hz` : 0,5 Hz au depart, courbe en carre, 16 Hz a 100 % ; le premier eclair part des que la montee demarre), teinte +120 degres ; le Drop met `build` a 0, envoie `flash` et un eclair blanc).
    - **Audio** : sensibilite du kick (`sensitivity`, comme les fleches haut / bas) avec un **voyant** (`led_tick`, 60 ms, allume si `beat` > 0,4) et le **mode silence** (`SilenceGuard` dans la boucle de rendu : niveau = max de rms / basses / mediums / aigus normalises < `silence_level` pendant `silence_delay` s -> `params["idle"]` monte vers 1 en 1,5 s ; en idle `effective_tempo` = tempo x (1 - idle x (1 - `silence_tempo`)) (courbes d'automation comprises) et l'overlay est efface : fond seul ; le son qui revient fait redescendre `idle`).
    - **Compo** (composition globale, tout dans `post.frag`, `comp_uv` calcule `cuv` utilise a la place de `v_uv`) : symetrie en boutons radio (`comp_sym` : 0 aucune, 1 miroir gauche, 2 miroir haut, 3 quatre quadrants, 4 kaleidoscope a `comp_seg` segments, rotation `comp_rot` degres/s), pixelisation (`comp_pix`), deformation globale (`comp_warp`), **trainee** (`comp_trail` : l'image finale est ecrite dans l'une de deux textures hors ecran, l'autre sert d'« image precedente » ; l'ecran de la fenetre est multi-echantillonne et ne peut pas etre lu en texture ni copie vers une texture : `copy_framebuffer(target, dest)` recopie ensuite sur la cible).
    - **Auto** : cycle de teinte (`hue_cycle` degres/s, phase integree `_hue_phase`), respiration de l'overlay et du fond (`breath_overlay` / `breath_bg`, periode `breath_period`, en opposition de phase).
    - **Memoires** : quatre memoires de masters (`scenes.MasterBook`, `~/.audio2wave/masters.json`, `gl.MEMORY_KEYS` = reglages de salle sans etats du moment) : un clic rappelle (`sync_master_panel` remet les curseurs), « Sauver » puis une memoire (ou clic droit) enregistre sous un nom. **Historique** : `s.scene_history` (50 dernieres scenes appliquees, enregistrees dans `apply_scene_dict` avec media, videos et fond de l'entree), bouton **Retour** (`back_scene`, aussi la touche Retour arriere ; refuse par le Verrou ; met le VJ en pause comme un choix a la main) et liste (double-clic = rejouer).
    - **Construction** : le panneau est construit **une seule fois** (`master_box`, parent persistant = la barre, replace a droite de chaque nouvelle liste par `pack(in_=...)` + `lift()`) et **chaque onglet se remplit a sa premiere selection** (`new_tab(nom, fill)`) : le reconstruire avec la liste et habiller ~30 curseurs gelait la fenetre. Les tests selectionnent les onglets un a un avant de chercher leurs widgets. Le **Verrou** actif est aussi signale par un bouton ambre « Verrou ● » dans la barre (cliquer deverrouille) : sans lui Play, Suivante, F1..F9 et les tirages semblaient morts. `vj_tick` isole chaque etape et reprogramme TOUJOURS le tick suivant (une erreur ne doit jamais tuer le minuteur du VJ). Mesure : ouvrir le panneau etendu avec 12 scenes prend ~2,3 s et un rafraichissement force ~1,2 s (lignes de la liste, avant et apres l'espace de controle : pas lie a lui).
    - Reglages de salle : `gl.ROOM_KEYS` (dans `OVERLAY_PRESET_EXCLUDED` : ni presets ni scenes ni `reload` n'y touchent) ; `gl.TRANSIENT_KEYS` (`idle`, `build`, `strobe_on`, `strobe_hits`) ne sont ni ecrits par P ni recharges. Details des masters : `params["react_master"]` multiplie tout ce qui vient de l'audio (`bass` / `mid` / `high` / `rms` / `beat` au debut de `Renderer.draw`, 0 = plus aucune reaction, sans toucher au noise) ; `params["tempo"]` (0..2) met a l'echelle le `dt` des animations (halo, fonte, cellules, defilement et bascule du fond, noise) et l'horloge des courbes d'automation (`AutomationEngine._clock`). « Overlay » (0 a 100 %, `params["overlay_master"]`) regle la **presence de l'overlay** : il multiplie l'opacite du logo, son contour et le halo holographique (0 = logo invisible, le fond continue). « Fond » (`bg_master`, 0 a 100 %) attenue le fond (`u_bg_level` dans `post.frag`) : 0 = fond noir, logo seul. Le bouton « Strobe » (`Renderer._strobe`, `u_strobe` : eclair blanc de 70 ms, sans bandes de glitch) : un eclair par appui (ou touche S dans la fenetre de rendu) ; boutons radio = mode (`strobe_mode` : `manual`, `kick` = un eclair par kick, `light` = un kick sur N ; kick et leger : un clic arme, un autre desarme) ; `strobe_hits` (compteur d'appuis) garantit un eclair meme pour un appui plus court qu'une image ; `gl.strobe_press` / `gl.strobe_release` partages par le bouton et la touche. « Teinte » (`hue_master`, 0..360) fait tourner les couleurs de **toute l'image** (fond et logo, `hue_rotate` autour de l'axe du gris dans `post.frag`, avant flash et strobe ; les gris restent gris). Le bouton « Verrou » (touche K dans la fenetre de rendu, `toggle_scene_lock`, `s.scene_lock`) **gele la scene courante** : le VJ se met en pause et `apply_scene` / `vj_advance` / `random_draw` / demarrage du VJ / F1..F9 / fleche droite sont refuses avec un message (`scene_locked`) ; les masters restent actifs ; deverrouiller relance le VJ s'il tournait (`s.lock_resume`). Le master du tempo est manuel (curseur, tap) sauf si « Tempo auto » est coche (BPM detecte, voir *Rythme detecte*) : la video de fond (ffmpeg `-re`) et le logo anime gardent leur cadence. Reglages de salle : dans `OVERLAY_PRESET_EXCLUDED` (ni presets ni scenes ni `reload` n'y touchent), sauves avec P. La rangee ne dicte plus la largeur de la fenetre (`pack_propagate(False)`, hauteur mesuree une fois) : trop de touches sont rognees a droite au lieu d'elargir les reglages.
  - Un changement de mode (Live -> Snap) ne reconstruit que le panneau d'audio2wave (~0,25 s, masque par le flash) : barre des scenes, partie OVERLAY, panneau etendu et espace de controle gardent leurs widgets.
- **Sets** (core/scenes.py `SceneBook`, GUI : menu `SET` de la barre) : `scenes.json` = `{"version": 2, "scenes": [...], "sets": [{"name",
  "entries": [{"scene", "media"}], "vj"}], "active_set"}`. Un set **reference** les scenes par nom (les scenes sont des briques,
  partagees ; une meme scene peut figurer plusieurs fois dans un set, chaque entree avec son `media`) ; `put` d'une scene
  nouvelle l'ajoute aussi a la fin du set actif, `delete` la retire de la bibliotheque **et de tous les sets** ; une entree
  orpheline (scene absente du fichier) est ignoree a la lecture. Un **ancien fichier** (`{"scenes", "vj"}`) est lu comme un set
  `principal` qui les contient toutes (reecrit au format 2 a la premiere ecriture). `rows()` = entrees du set actif
  (`index`, `name`, `scene`, `media`) ; la GUI suit l'entree active par `s.active_entry` **et** `s.active_scene` (`current_index` :
  le nom fait foi si l'entree a ete deplacee ou retiree).
  - **Media par entree** (`capture_media` / `media_values` / `media_missing`, `MEDIA_KEYS`) : la source courante (image, texte
    ou video) posee **par-dessus** les valeurs de la scene a l'application ; fichier introuvable = message, la scene garde son
    propre media. La scene elle-meme contient toujours le sien (overlay).
  - **Changer de set** (`switch_set`) : si le VJ tournait, `vj_advance` le **redemarre** (flash, premiere scene du nouveau set ou
    une au hasard) ; un set de moins de 2 entrees arrete le VJ avec un message. Au moins un set existe toujours.
  - **Export / import** (`export_set`, `parse_import`, `import_conflicts`, `import_set`, fonctions pures) : un fichier
    `{"format": "casual-overlay-set", "version": 1, "set", "scenes"}` autonome (le set + les scenes utilisees ; medias = chemins,
    signales s'ils manquent a l'import). `parse_import` refuse sans rien modifier un JSON illisible, un autre format, une
    version plus recente (`EXPORT_VERSION`). Conflits : nom de set deja pris, nom de scene deja pris **avec un autre contenu**
    (une scene identique est reutilisee) ; `rename` (`nom (2)`, `unique_name`) ou `replace` ; l'ecriture est faite en une fois.
    La GUI (`import_set_now`, `resolve_conflict`) pose la question puis **demande confirmation avant de remplacer**.
  - **Archive avec medias** (`core/setpack.py`, stdlib) : un export dont le nom finit par `.zip` (defaut du dialogue) est une archive `set.json` (meme contenu que l'export JSON, mais chaque chemin de media est une
    reference `pack:media/<empreinte>_<nom>`) + `media/` (une copie par contenu distinct : le meme fichier sous deux noms n'est embarque qu'une fois). Emplacements : logo / video / police des scenes et des entrees,
    video de fond, videos audio2wave (`_walk_media`). Un media introuvable n'est pas embarque (liste `missing`, signale dans le message). A l'import (`read_pack`, detecte par `zipfile.is_zipfile`) les medias sont extraits
    dans `scenes.IMPORT_DIR/media` (`~/.audio2wave/imported/media`, fichier deja present de meme taille = reutilise) et les references redeviennent des chemins absolus : reimporter la meme archive donne exactement les memes chemins,
    donc les scenes identiques sont reconnues (pas de faux conflit). Noms d'archive suspects (`..`, absolu, hors `media/`) refuses sans ecrire ailleurs. L'export `.json` seul reste possible (sans medias).
    Limite : la copie est synchrone (un set de plusieurs Go fige la fenetre le temps de l'ecriture) ; les dossiers des banques du Full Randomizer ne sont pas embarques.
  - **Annuler / Retablir** (`SceneBook.undo` / `redo` / `group`, Ctrl+Z / Ctrl+Y hors champ de saisie, menu SET, boutons du panneau etendu) : `_save` garde en memoire le contenu du fichier AVANT chaque ecriture (40 pas, partage
    entre les fenetres reconstruites : `SceneBook._HISTORY` par chemin de fichier). Etiquette = nom de la methode qui ecrit (`LABELS`) ou celle du groupe ; `with book.group("...")` regroupe plusieurs ecritures en UN pas (+5,
    Dupliquer la derniere, Garder). Changer de set et poser un apercu (`track=False`) ne sont pas des pas. Annuler ne change pas le set affiche (sauf s'il n'existait pas encore). Une nouvelle modification efface les pas a retablir.
    Ce que l'on peut annuler : tout ce qui passe par `SceneBook` (scenes, sets, entrees, medias, reglages du VJ, import, +5, retirer, deplacer, lignes cochees). Pas les reglages de salle ni les presets.
  - **Apercu des scenes** (`engine/preview.py`, `scene["preview"]` = nom d'un JPEG de 240 px dans `scenes.PREVIEW_DIR` = `~/.audio2wave/previews`) : capture de la fenetre de rendu, lue dans le fil GL (`s.preview_requests`, chemins a ecrire,
    traites juste avant `swap_buffers`). Declencheurs : enregistrement d'une scene (`capture_scene_now`), premier passage d'une scene sans apercu projection ouverte (1,5 s apres, si elle est toujours a l'ecran), clic droit
    « Mettre a jour l'apercu ». Projection fermee : message, rien n'est demande. La ligne du panneau etendu montre la miniature (96 x 54). Pas un pas d'annulation.
  - **Securite GPU du VJ** (`core/safety.py`, reglage de salle `gpu_cap`, case « Securite GPU » de l'onglet *Masters*, defaut 1) : quand le VJ (`vj_advance`, Full Randomizer compris : `vj_auto["on"]`) pose une scene, `cap_values` estime
    le cout de ses effets couteux (cellules 6,7 ms, halo 3,3 ms, fonte 1,5 ms a 1080p, proportionnel aux pixels du rendu) et, si `gpu_ms` (temps GPU lisse mesure par `PerfGuard`, `s.status["gpu_ms"]` / `gpu_budget_ms`) + la difference
    depasse 90 % du budget d'une image, coupe d'abord les cellules, puis le halo, puis la fonte DE CETTE SCENE (le reste est intact) ; message « allegee pour le GPU » et ligne du journal. Sans mesure du GPU : rien. Une scene choisie a la main
    n'est jamais touchee. `check_gl.py` : `check_safeguards` (pur), `x_gpu_cap`, `x_undo`, `x_preview`.
  - Dialogues dans `scene_ui` (`chooser`, `ask_name`, `ask_save`, `ask_open`) : les tests les remplacent, jamais de fenetre
    bloquante. `choice_dialog` (module) = fenetre modale a boutons.
- **Full Randomizer** (`core/randomizer.py`, ordre VJ `alea`, `scenes.VJ_ORDERS`, libelle « Full Randomizer » partout dans la GUI) : a chaque changement `vj_advance` appelle `random_draw()`
  au lieu de lire une entree : `randomizer.draw_scene(rng, settings, Context)` (pur, graine injectable) rend une **scene temporaire**
  (meme format que `capture_scene`, nom `Full Randomizer`), appliquee par `apply_scene_dict` (extrait de `apply_scene_core`, qui lui
  passe la scene de la bibliotheque) : flash, minuteur = `duree` du set, `s.active_scene` = `Full Randomizer`, `s.rnd` = etat (`last`,
  `scene`, `info`, `bank` = `{"fond", "overlay"}`, `rng`). **Marche avec un set vide** (`vj_toggle` ne demande 2 scenes que hors `alea`).
  - Reglages dans `vj["random"]` **du set** (validation `randomizer.clean_settings` appelee par `SceneBook._clean_vj`, donc aussi a
    la copie, l'export et l'import) : `folders_fond` / `folders_overlay`, `categories_fond` / `categories_overlay` ([] = toutes), `bg_sources` (`preset` / `a2w` / `video` / `params`),
    `overlay` (`overlay_preset` / `params` / `fx` / `media` / `text`), `text` (defaut `CASUAL RAVERS`, `randomizer.DEFAULT_TEXT`), `lock_bg` / `lock_media` / `lock_fx`. Un ordre inconnu revient a `seq`.
  - **Deux banques**, autant de dossiers que l'on veut : **FOND** (videos plein cadre : le fond) et **OVERLAY** (images, animations,
    videos : le logo). **Le dossier classe, jamais l'extension** (un .mov peut etre une animation a fond transparent) ; pour qu'une
    video serve aux deux, son dossier est declare dans les deux. `scan_bank(dossiers, "fond" | "overlay")` (os.walk, 4 niveaux, 5000
    fichiers, caches ignores, doublons de chemins ecartes ; FOND = videos seulement), `Asset(path, kind, category)` (`kind` image /
    video = la source du logo a utiliser), categorie = premier sous-dossier (`ROOT_CATEGORY` a la racine) ; `Context.bank_fond` /
    `bank_overlay` ; `random_bank()` (GUI, `{"fond", "overlay"}`) garde le scan tant que les dossiers ne changent pas (Rescanner).
    Un fichier disparu depuis le scan est ecarte au tirage (`usable(bank, categories, kind)`).
  - Fond : `preset` -> `BACKGROUND_BOOK.all()` (scene `pattern`, ou `live` sans toucher au panneau si le preset est en mode live),
    `a2w` -> presets du mode courant (`mode_store(...).all()`, **jamais `default` vide**, jamais un autre mode : pas de reconstruction de la fenetre),
    `video` -> video de la banque FOND (`bg_video_fit` cover / stretch), `params` = **config aleatoire** -> soit `random_background_params` (plages des curseurs, jamais
    la palette banc de test), soit un **visuel audio2wave** : mode (`modes` du set, `Context.a2w_modes` = presets de chaque mode, charges par
    `random_draw` seulement si `params` est coche) et preset de ce mode au hasard (`default` vide = `live_overrides` None) ; changer de mode
    remplace le panneau d'audio2wave (~0,25 s, masque par le flash) via `apply_scene_dict` ; `record["bg"]` = `params:gen` ou `params:a2w:<mode>:<nom>`. Une source cochee mais indisponible est ignoree ; aucune = le fond ne change pas (message).
  - Overlay : une base, **preset overlay** ou **config entierement nouvelle** (`params`, `random_overlay_params` : disposition et contour du
    logo dans des valeurs sures, effets du fond et de la couche logo, `fx_link` 0 / 1, halo / fonte / cellules, noise ; ni media ni
    automation ; les deux bases alternent si les deux sont cochees ; `record["overlay"]` = `preset:<nom>` ou `params`) ; `fx` ajoute
    `random_effects` par-dessus un preset (inutile avec `params`, qui en contient deja) ; `random_effects` (halo / fonte / cellules, **`MAX_COSTLY` = 2** pour ne pas declencher le
    garde-fou GPU ; les effets non tires sont coupes explicitement, le tirage ne depend pas de l'etat d'avant) + media de la banque OVERLAY
    (`media_for`). **Sans media a tirer (banque vide, non coche) le media courant reste** : un preset applique les valeurs par
    defaut et remettrait sinon le logo par defaut. La video du fond n'est pas aussi le logo (sauf si c'est la seule).
    **Texte** (case `texte` + champ, `settings["text"]`, defaut `CASUAL RAVERS`) : candidat comme un fichier de la banque (`text_media` :
    source texte, couleur tiree dans `GLOW_COLORS`, centre, police du moment) ; `record["media"]` = `text` ; jamais deux fois de suite
    le meme media ; champ vide = pas de texte. Une liste `overlay` deja enregistree sans `text` ne le coche pas (a cocher dans le panneau).
  - **Overlay toujours centre et statique** : `randomizer.centered(overlay, overlay_du_moment)` est applique a **tout** tirage (preset, config,
    verrou des effets compris) : `logo_x` = `logo_y` = 0,5 (`CENTER`) et automation de position coupee (`_automation` de l'overlay, sinon
    celle du moment ; rien n'est ajoute si aucune n'est allumee : fournir `_automation` reinitialiserait les autres automations).
  - Verrous : le fond verrouille reprend `current_scene["bg"]` / `fond` (jamais d'etat audio2wave), le media et les effets reprennent
    l'overlay courant (`capture_scene`). Pas de repetition immediate (`last`).
  - GUI : le panneau etendu affiche `build_random_body` a la place des lignes d'entrees (banques FOND et OVERLAY avec leurs dossiers, categories + sources + verrous +
    *Tirer maintenant* / *Garder cette scene* + le resume du dernier tirage) ; **`Garder ce tirage...`** ouvre `keep_dialog` (modale, `scene_ui["ask_keep"]`, remplacable par les
    tests) : trois noms, preset de fond (`BACKGROUND_BOOK.save` de `scene["fond"]`, seulement si le fond est genere ou video), preset
    overlay (`OVERLAY_BOOK.save` de `scene["overlay"]`) et scene (`scene_book.put`), un champ vide = pas enregistre ; defauts uniques
    (`Tirage fond`, `Tirage overlay`, `Tirage`) ; `random_keep_check(noms)` valide (un nom deja pris ou `default` est refuse, la modale
    reste ouverte avec le message ; rien n'est ecrit en cas d'erreur) ; `random_save(noms)` ecrit puis rafraichit les menus de presets. `scene_ui["ask_dir"]` (remplacable par les tests). Controles : `c["random"]` (`draw`, `draw_now`, `keep`, `bank`, `option`, `state`).
  - **Garder un tirage audio2wave / fond sans video** : un fond `video` n'est propose en preset de fond que s'il y a un fichier
    qui existe (`randomizer.fond_saveable` ; un mode video sans fichier = ecran noir, rien a enregistrer, vu avec le verrou du
    fond). Un tirage dont le fond est un visuel audio2wave ajoute a la modale un champ **Preset Live / Snap / Ridge** : enregistre
    par `save_live_preset` : leur champ nomme `widgets["save_name"]` + Entree (leur menu est alors a jour ; l'evenement clavier exige un
    widget visible et le focus), sinon, panneau masque (fond genere affiche), `ModeStore.save` directement (leur menu ne le montre qu'a la
    prochaine construction du panneau : besoin cote audio2wave, un rafraichissement du menu par le controleur). La scene gardee retient
    l'**etat reel du panneau** (`capture_live_overrides`), pas seulement celui du tirage (retouches depuis, fond verrouille). Les trois
    modes normalisent le nom (`preset_registry.normalize_name` : minuscules) ; `ModeStore.save` rend le nom reellement enregistre. Refus
    aussi si le nom est un integre (`default`, `club`...) ou existe deja (`random_keep_check`).
  - **Piege (modales)** : `a2w` (palette d'audio2wave) n'est pas un nom du module `gl_gui`, seulement un import local de `build_window` : une
    modale de niveau module (`choice_dialog`, `keep_dialog`) qui l'utilise doit faire `import audio2wave as a2w` elle-meme. `choice_dialog`
    plantait ainsi sans qu'aucun test le voie (ils remplacent les dialogues par `scene_ui`) ; `check_dialogs` ouvre les vraies modales.
  - `check_gl.py` (`check_randomizer`, `gui_random_steps`) : banque, reglages, plages, effets, determinisme, sources, verrous, banque
    vide, scene applicable (`scene_values`), reglages par set / copie / export, puis dans la fenetre : panneau, tirage + flash, VJ sur set
    vide, Garder + rejouer, verrou, categories, dossier, retour a l'ordre normal.
- **Panneau etendu** (`scene_top` = barre + `scene_panel`, `set_expanded`, `refresh_scene_panel`, `build_panel_row`) : bouton
  `Etendre` / `Reduire` dans la barre ; l'etat est `s.scenes_expanded` (survit aux reconstructions de la fenetre). La fenetre
  grandit de `scene_panel.winfo_reqheight()` (memorise dans `s.scene_panel_h`, plafonne a la hauteur de l'ecran - 80) et la
  reprend a la fermeture ; le corps du panneau a une hauteur fixe (190 px) avec son ascenseur. Une ligne par entree du set :
  scene, **media avec vignette**, duree propre, case VJ, renommer, retirer. **Ordre par glisser-deposer** : on glisse le numero de la ligne (`row_drag_start` / `row_drag_move` / `row_drag_end`, la ligne visee est entouree) ; `move_entry_to(index, cible)` -> `move_entry_now`. Les fleches ▲ ▼ n'existent plus (« Monter / Descendre » reste dans le menu du clic droit d'une touche). Sous la liste : « + » (une scene aleatoire), « +5 » (`add_random_scenes`, cinq d'un coup, une seule reconstruction) et « Dupliquer la derniere ». `refresh_scene_bar` appelle
  `refresh_scene_panel`, qui **ne reconstruit presque jamais** :
  - **Signature** (`panel_signature` : reglages du set, lignes, resume du tirage, nombre de vignettes, glisser-deposer) : identique =
    rien n'est reconstruit, seule la ligne active est **repeinte sur place** (`restyle_row` : fond de ligne, cartes, bouton du nom, via
    `skin.retint` pour les coins transparents). Case VJ et duree propre se mettent a jour **sur place** aussi (`panel_update_in_place` :
    resume + texte du bouton, aucun widget detruit).
  - Une vraie reconstruction (ordre, ajout / retrait, vignette prete — au plus une fois par 0,8 s —, `Rescanner`) **garde la position de
    l'ascenseur** (restauree a chaque passe de mise en page pendant 0,5 s) et **rehabille toujours** les widgets (`skin.apply(scene_panel)`
    dans `refresh_scene_panel` meme : avant, les vignettes pretes reconstruisaient le panneau sans habillage, d'ou des cases VJ brutes a
    l'ouverture puis stylisees apres un rechargement par la barre).
  - Disposition : le bouton du nom et les boutons d'action sont des **boutons etires** (`widget._skin_stretch = True` avant `skin.apply` :
    l'image suit la taille allouee par la mise en page, mise a jour a chaque `<Configure>`, taille voulue = taille naturelle pour pouvoir
    retrecir ; le nom est affiche **en entier** : sa largeur est celle de la plus longue entree (`panel_state["name_w"]`, 14 a 70 caracteres), identique sur toutes les lignes, comme celle du bouton de duree et des boutons d'action (colonnes alignees) ; un nom plus large que la liste fait apparaitre un ascenseur horizontal ; tous la hauteur de la ligne). `scenes.MAX_NAME` = 200 (noms de scenes, sets et presets enregistres par le Full Randomizer) ; un champ de liste (`skin` OptionMenu) plafonne sa largeur a `FIELD_MAX_TEXT` px.
  - **Molette** : `on_wheel` (bind_all) defile d'abord la liste du panneau (`panel_state["cv"]`) quand le pointeur est dessus.
    **Poignee** (`...` sous la liste) : glisser change la hauteur de la liste (`s.panel_body_h`, min 80 px, memorisee d'une
    reconstruction a l'autre) et la fenetre suit tant qu'elle tient dans l'ecran ; au-dela la liste grandit quand meme (s.panel_overflow, rendu en premier a la reduction) ; maximum = hauteur de l'ecran - 200. **Vignettes** : la signature du panneau compte les vignettes PRETES (s.thumb_cache, pas les PhotoImage deja creees : sinon une vignette terminee ne reconstruisait jamais le panneau) ; une video dont la 1re image est vide (fond transparent, noir) est relue a 0,6 s puis 1,5 s ; `s.scene_panel_h` est ajuste du meme delta pour que
    Reduire rende la taille d'origine. `check_gl.py` (`gui_expand_steps` : `x_fill`, `x_skinned`, `x_noreload`, `x_rebuild`,
    `x_wheel_grip`).
  - **Boutons sous la liste** (barre `foot`, entre la liste et la poignee) : **« Dupliquer la derniere »** (`duplicate_last_scene` : copie de la derniere scene sous un nom inedit `X (2)` avec le media, les videos audio2wave, la video de fond, la duree et le VJ de l'entree, ajoutee a la suite, liste defilee jusqu'a elle, puis dialogue pour la renommer ; les medias se changent ensuite par glisser-deposer sur sa ligne) et **« + »** (`add_random_scene`) : ajoute au **set actif** une scene tiree par `random_scene()` (memes reglages du hasard que le Full Randomizer, centree et statique), sous un nom aleatoire (`randomizer.random_name` : Neon Comete, jamais un nom pris) ; ajoutee a la suite ; **aucun preset enregistre, rien d'applique** (le spectacle n'est pas perturbe), `scene_book.put` (bibliotheque + fin du set). `rnd["last"]` est mis a jour (pas de repetition avec le tirage suivant) mais pas `rnd["scene"]` (le candidat de « Garder »).
  - **Organisation du set** (`gui_panel.py`, operations dans `SceneBook` : `remove_entries`, `set_entries_vj`, `duplicate_entries`, `copy_entries_to_set`, `add_entry(at=)`, `keep_scene`, `temp_names`) :
    (1) **lignes cochees** : une case (`refs["box"]`, un `Label` ☐ / ☑, Maj = plage) par ligne ; la selection (`panel_state["sel"]`) n'est PAS dans la signature (cocher ne reconstruit rien) ; la barre « LIGNES COCHEES » (`build_selbar`) offre
    Tout / Aucune, Retirer du set, VJ off / on, Duree, Dupliquer (juste apres l'original), Copier vers le set (a la fin d'un autre set) ; les operations sur les rangs vident la selection et mettent `s.active_entry` a None.
    (2) **scenes provisoires** : « + » et « +5 » posent `temp: True` dans la scene (scenes.json) ; la ligne montre ✱ a cote du numero et un bouton **Garder** dont le clic transforme le nom en champ (Entree = garder sous ce nom,
    Echap = annuler, nom pris = refus et la scene reste provisoire) ; « Retirer les ✱ » supprime de la bibliotheque (donc de tous les sets, apres confirmation) toutes les scenes encore provisoires. Mettre une scene a jour (clic droit) la rend definitive.
    (3) **bibliotheque** : bouton « Bibliotheque ▶ » (`s.library_open`) = colonne de 220 px a gauche de la liste avec TOUTES les scenes (✱ provisoire, ×N = nombre d'entrees dans ce set) ; on glisse une scene sur la liste pour l'ajouter AU RANG VISE
    (`drop_pos` : moitie haute d'une ligne = avant, basse = apres ; la ligne visee est entouree ; une etiquette suit le pointeur), double-clic = a la fin, clic droit = renommer / garder. Le glisser-deposer des lignes (poignee = numero) et celui de la
    bibliotheque partagent `drop_pos` / `mark_target`. `check_gl.py` : `x_temp`, `x_select`, `x_library` (evenements simules), `x_drag`.
  - **Colonne FOND video** (`bg_video` de l'entree, `set_entry_bg_video`, cible de depot `bgvideo`, `bg_clear`) : une video de fond propre a l'entree **remplace le fond de la scene** a l'application (`scenes.scene_with_bg_video` : copie de la scene en fond `video`, cadrage de la scene s'il existe sinon `cover`, aucun motif ni etat audio2wave ; chemin introuvable = message, la scene garde son fond). Le meme fichier d'une entree a l'autre ne relance pas le decodage (`BgVideoManager`) : c'est le moyen d'enchainer des scenes sur une video de fond continue sans recapturer. Zone grisee en sourdine = la video que la scene a capturee elle-meme (`scene_bg_video`) ; touche de la barre : menu « Fond : video ». Export / import la gardent (`missing` la signale).
  - **Entrees** : `{"scene", "media", "seconds", "skip"}` (+ `a2w`, `bg_video`) (`SceneBook._entry` valide ; `set_entry_vj`). `seconds` (5..300, ou None =
    duree du set) arme le minuteur du VJ a `vj_advance` ; `skip` fait sauter l'entree par `scenes.next_entry_index` (pure, meme
    regles que `next_scene_index`, plusieurs fois la meme scene possible) ; moins de 2 entrees jouables = le VJ s'arrete avec un
    message. `rename_scene` renomme dans la bibliotheque et dans tous les sets. Export / import gardent `seconds` et `skip`.
  - **Deux familles de medias** : `media` (overlay : `capture_media` / `media_values`, cles `MEDIA_KEYS`) et `a2w` (audio2wave :
    `A2W_MEDIA_KEYS` = `video` / `video2`, les controles « Video interieure / exterieure » du panneau **Snap**, pencil seul ; chemins
    texte). Une scene retient ceux de l'overlay dans `overlay` et ceux de Snap dans `live_overrides` (`scene_a2w_media`) ; une
    entree peut les **remplacer** : a l'application `apply_scene_core(..., a2w)` fusionne `a2w` dans `live_overrides` avant de
    passer par `apply_state` du panneau (aucun code d'audio2wave touche) ; scene non Snap = message ; chemin absolu
    absent (`a2w_missing`) = message. `set_entry_a2w`, `a2w_clear`. Export / import les gardent (`missing` les signale).
  - **Capture** : `flush_pending_inputs` (appelee par `capture_scene_now`) applique les champs de fichier / texte tapes sans
    Entree ni perte de focus (`apply_logo_path`, `apply_video_path`, `apply_text`) et appelle `GuiController.commit_pending()` pour ceux du
    panneau d'audio2wave (dont leurs champs video) : cliquer un menu ne fait pas perdre le focus, la scene capturait sinon
    l'ancienne valeur (`gui_a2w_steps` le verifie sur la video exterieure de Snap et la video du logo).
  - **Cibles de depot** : `drop_media(index, data, target)` avec `target` = `overlay` / `video` / `video2` ; `register_drop(widget, index,
    target)` ; une zone du panneau = une cible fixe, une touche de la barre = `drop_on_button` (menu au pointeur : overlay /
    video int. / video ext., ces deux dernieres grisees hors scene Snap). Une image est refusee pour audio2wave.
  - **Glisser-deposer** (`dnd`, `register_drop`, `register_tree`, `drop_media`, `media_from_file`) : `tkinterdnd2`, charge dans le
    Tk **existant** par `TkinterDnD._require(root)` (une fois par racine, marque `root._casual_dnd`) ; chaque widget cible est
    enregistre (`drop_target_register(DND_FILES)`, `<<Drop>>`), **y compris tous les enfants d'une ligne** (tkdnd ne propage pas a
    l'enfant). `e.data` = noms separes par des espaces, ceux qui en contiennent entre accolades : `root.tk.splitlist`. Image
    (`.png .jpg .jpeg .bmp`) -> `{"logo_source": "image", "logo_path"}`, video / animation (`.gif .webm .mp4 .mov .mkv .avi .webp
    .apng`) -> `{"logo_source": "video", "logo_video", "logo_key": ""}` ; autre format = message. Si l'entree deposee est a
    l'ecran, `apply_entry_core` repose le media tout de suite. Sans le paquet : `dnd["ok"]` False, le reste marche. Le test
    appelle `drop_media` (un vrai glisser depuis l'Explorateur n'est pas automatisable).
  - **Vignettes** (`make_thumb`, `thumb_photo`) : calculees dans un **fil** (PIL pour une image / un GIF, `ffmpeg -frames:v 1` pour
    une video, 6 s maximum, `libvpx-vp9` d'abord pour garder l'alpha d'un webm), cachees dans `s.thumb_cache` / `s.thumb_photos` ;
    quand une est prete le fil leve `s.thumb_dirty` et `vj_tick` (fil Tk) rafraichit le panneau (jamais d'appel Tk depuis le fil).
- Limites / suite : pas de changement en mesures (kicks) ni de vraie transition (voir README, ameliorations
  futures : bouton « etendre » du VJ pour la granularite, glisser-deposer de medias, medias embarques dans l'export).
- `check_gl.py` (`check_scenes` + etape GUI) : stockage (ordre, bibliotheque sans limite, remplacement, fichier illisible), sets (ancien format migre, scenes partagees, doublons, media par entree, VJ par set, export / import et conflits, GUI : changement de set avec VJ qui redemarre, plus de 9 scenes), capture et
  valeurs (motif / audio2wave, automations, micro intact), barre, F1/F2/F8, nom deja pris, mise a jour au meme rang, VJ (ordre, reglages persistants, minuteur reel, flash, Espace / fleche droite, pause a la main),
  suppression du preset, **changement de mode Live -> Snap avec chargement du preset `club`**.

### Table unique des reglages (`core/params_schema.py`)

**Un reglage = une ligne** dans `params_schema.TABLE` (stdlib, aucune dependance au moteur ni a la GUI) : cle, valeur par defaut, plage
(`lo` / `hi`, par element pour une liste), valeurs permises (`choices`, pour les enums), **portee** (`scope`), drapeau `keep` (la touche R ne
le recharge pas) et documentation. L'ordre de la table est celui des fichiers sauves. Tout le reste en est **derive** :
- `gl.DEFAULT_PARAMS` = `params_schema.defaults(logo du depot)` ;
- `gl.ROOM_KEYS` (portees `room` + `state`), `gl.TRANSIENT_KEYS` (`state`), `gl.MEMORY_KEYS` (`room`), `gl.BG_KEYS` (`bg`),
  `gl.OVERLAY_PRESET_EXCLUDED` (`device` + salle), et l'ensemble garde par R (`RELOAD_KEEP_KEYS` + `_automation`) ;
- `gl.coerce_params` appelle `params_schema.validate` : type, longueur d'une liste, valeur d'enum ; **un nombre hors plage est ramene dans la plage**
  (fichier abime, preset ancien) au lieu d'atteindre le rendu ; une cle inconnue est ignoree.
- Portees : `look` (overlay : presets overlay et scenes), `bg` (fond : presets de fond et scenes), `room` (reglages de salle : jamais captures,
  sauves avec P, enregistres par les memoires de masters), `state` (etat du moment : ni sauve, ni recharge, ni memorise), `device`
  (sensibilite du kick : sauvee, hors presets et scenes).
- **Ajouter un reglage** : une ligne dans la table (defaut, plage, portee, doc) ; si c'est un curseur, l'ajouter a la GUI avec une plage
  contenue dans celle de la table ; si c'est un reglage de salle, rien d'autre (les listes se mettent a jour toutes seules).
- **Bug trouve et corrige par cette table** : `bg_master` (master « Fond » du VJ) etait compte parmi les reglages du fond (`BG_KEYS` = toutes les
  cles `bg_*`) : charger un preset de fond ou une scene a motif / video **remettait le curseur « Fond » a 100 %** (ou a la valeur
  capturee). C'est un reglage de salle : il n'est plus dans `BG_KEYS` (les anciens presets qui le contiennent se chargent sans erreur).
- `check_gl.py` (`check_params_schema`) : valeurs par defaut dans leur plage, table = `DEFAULT_PARAMS`, portees disjointes et completes,
  regression `bg_master`, validation (hors plage, mauvais type, enum, listes), **presets integres, courbes d'automation, curseurs de la GUI
  (lus dans le source : `param_slider(...)` et les curseurs des masters) et tirages du Full Randomizer (300 tirages) tous dans les plages**,
  aucun reglage lu ou ecrit par le code sans etre dans la table, `core/params_schema.py` dans le portable (`APP_FILES`).
### Garde-fous d'exploitation (soiree : fiabilite, robustesse, aisance)

- **Panique** (`gl.panic(params)`, `PANIC_RESET`; bouton rouge **PANIQUE** tout a droite de la barre, touche **X** dans la fenetre de rendu, bouton de la console) :
  coupe d'un geste montee, strobe arme, composition (symetrie, pixel, trainee, deformation, rotation), teinte globale et cycle, respirations,
  mode silence (`silence_on` et `idle`), effets 1-5 du fond et du logo, noise, halo / fonte / cellules ; remet les masters (reac, tempo,
  overlay, fond, effets) a 100 % ; un fond `video` sans fichier (ecran noir) repasse en `live`. **Ne touche pas** au logo, au texte, aux
  medias, au fond choisi ni aux automations. Idempotente, rend la liste des reglages changes (journal). La touche X agit **dans le fil GL**
  (immediat, flash) puis met `"panic"` dans `s.scene_requests` : la GUI (`do_panic(from_gl=True)`) met le VJ en pause, **retire le verrou**,
  arrete la montee et resynchronise curseurs (`sync_widgets`, `sync_master_panel`). Le bouton fait la meme chose cote GUI (`do_panic`).
- **Limiteur de strobe** (`StrobeLimiter`, pure, temps injecte ; reglages de salle `strobe_max_hz` 10 et `strobe_max_s` 8, curseurs « Limite » et
  « Duree max » de l'onglet *Live*) : applique dans `Renderer._strobe` a **tous** les eclairs : jamais plus de
  `strobe_max_hz` eclairs par seconde (montee comprise), et une rafale continue (eclairs espaces de moins de 1 s) est coupee apres `strobe_max_s` secondes
  (pause forcee `STROBE_COOLDOWN_S` = 3 s, evenement journalise). **Les eclairs de la seule montee ne sont pas soumis a cette duree** (`allow(..., timed=False)`) : la montee clignote sans s'arreter jusqu'au drop, seule la cadence maximale la plafonne ; un strobe manuel, au kick ou a cadence fixe reste coupe. Le flash du VJ / du Drop (un seul eclair) n'est pas concerne.
- **Master des effets** (`fx_master` 0..2, curseur « Effets » de l'onglet *Masters*, reglage de salle) : `Renderer.draw` en multiplie, sur une copie
  des reglages, `master` (les 4 deformations), `melt_depth`, `cell_amount`, `noise_amount` et le halo (intensite, deformation, poussiere).
  0 % = image identique a tous les effets coupes ; ne touche pas aux composition / teinte (masters a part).
- **Tap tempo** (`gl.tap_bpm(taps)`, pure : moyenne des 6 derniers intervalles, un trou de plus de 2 s recommence ; bouton de l'onglet *Masters*,
  touche **point** dans la fenetre de rendu, console) : BPM -> master du tempo (**120 BPM = 100 %**, 30..300 BPM ; le curseur « Tempo » est gradue en **BPM** (0..240, `factor` 120 : le reglage `tempo` reste 0..2) ; un **voyant de kick + BPM detecte** est affiche dans la barre du haut, `kick_tick` 50 ms, « cassure » / « MONTEE » quand le detecteur les repere) ; en strobe a cadence fixe
  (`strobe_mode` = `rate`) la cadence suit (BPM / 60 Hz, bornee a 1..20 puis par le limiteur). Le tap reste la reference manuelle ; le BPM detecte est decrit juste apres.
- **Rythme detecte** (`core/rhythm.py`, pur, temps injecte ; `AudioAnalyzer.rhythm`, nourri par le fil d'analyse a chaque kick et chaque pas d'analyse : independant de la projection) :
  `BpmEstimator` garde les kicks des 24 dernieres secondes ; les intervalles entre kicks consecutifs ET distants de 2, 3 ou 4 rangs (divises par l'ecart : un kick manque ne fausse rien) sont ramenes dans 70..180 BPM
  (`fold_bpm` : un kick sur deux = le meme morceau) ; le groupe d'estimations qui s'accordent a 3 % pres le plus nombreux donne le BPM, sa part du total la confiance (>= 0,45 sinon pas de BPM, 6 kicks au moins).
  `Rhythm.bpm` est lisse (+25 % de l'ecart, saute apres 3 estimations de suite en desaccord = changement de morceau, perdu apres 5 s sans kick). `DropDetector` : basses sous 20 % de leur niveau habituel
  (plus haut recent, oublie avec une demi-vie de 40 s, au moins 0,30) pendant 3 s = cassure (`break`) ; apres 5 s de cassure ET si la courbe des mediums / aigus a gagne au moins 0,12 en 4 s et continue de monter
  (`_rising` : une simple absence de basses n'est PAS une montee) = evenement `build` ; retour des basses a plus de 60 % = `drop` (pause de 12 s entre deux ;
  un retour pendant la pause remet au calme sans drop en retard ; cassure de plus de 45 s abandonnee et non rearmee tant que les basses ne reviennent pas). Un silence depuis le debut ne declenche rien.
  GUI (`gui_vj.rhythm_step`, toutes les 100 ms, ne reagit qu'aux EVENEMENTS survenus depuis le dernier pas) : case **Tempo auto** (`bpm_auto`, onglet *Masters* : tempo = BPM / 120, cadence du strobe en mode Cadence) ;
  case **Montee / drop auto** (`drop_auto` : une cassure detectee lance la montee, le drop la relache avec flash et eclair ; montee annulee si la cassure finit sans drop) ; reglage du set `vj["on_drop"]` (menu « reglages VJ ») :
  le VJ en marche change de scene au drop. `bpm_auto` et `drop_auto` sont des reglages de salle coupes par la PANIQUE. Le libelle BPM de l'onglet *Masters* et du titre indique « BPM auto » (detecte) ou « BPM (tap) ».
  `check_gl.py` (`check_safeguards` : kicks a 128 BPM, 64 ramene a 128, gigue, kicks manques, irreguliers, perte, changement de morceau, montee / drop, pause, silence ; `gui_expand_steps` : `x_rhythm*` avec un faux detecteur).
  **Limites** : pas de phase (on ne sait pas ou tombe le temps), mesure = 4 kicks ; un silence qui precede la musique ressemble a une cassure suivie d'un drop.
- **Reprise automatique de la projection** (`Projection._run`) : une erreur qui tue la fenetre GL (pas `SystemExit`, pas une fermeture voulue :
  `proj_stop` / `stop_event`) relance `gl_main` apres `RESTART_DELAY_S` = 1 s avec les reglages du moment (ils vivent dans `s.params`) ; au plus
  `MAX_RESTARTS` = 3 reprises en `RESTART_WINDOW_S` = 120 s, ensuite le statut dit « trop de reprises : bouton Demarrer pour reessayer ». Verifie
  en vrai : une boucle de rendu qui plante est relancee et retombe a 60 fps.
- **Journal de session** (`core/sessionlog.py`, stdlib ; `~/.audio2wave/casual-overlay.log`, rotation 3 x 500 Ko) : branche par `run_app` seulement (les
  tests n'ecrivent jamais dans le vrai fichier ; sans fichier branche tout se tait). Evenements (lancement, scene appliquee... verrou, VJ, panique,
  strobe limite, projection ouverte / fermee / relancee, arret propre ou anormal) et **toutes les erreurs avec leur trace** : callbacks Tk
  (`root.report_callback_exception` dans `run_gui`), exceptions non rattrapees du fil principal et des autres fils (`install_hooks`),
  fenetre GL morte. `sessionlog.tail(n)` rend les dernieres lignes.
- **Auto-sauvegarde** (`write_autosave` / `read_autosave` / `clear_autosave` / `recover_autosave`, `~/.audio2wave/autosave.json`) : toutes les
  minutes la GUI (`autosave_tick`) ecrit atomiquement les reglages (sans `TRANSIENT_KEYS`) + scene et set actifs. **Sortie propre = fichier efface** ;
  un fichier present au lancement signale un arret brutal : `run_app` charge ces reglages a la place de `gl_params.json`, met l'ancien fichier
  de cote (`autosave.precedent.json`, jamais efface) et le dit dans le statut. Les scenes, sets, presets et masters sont deja ecrits a chaque
  geste : seul l'etat des curseurs du moment en dependait.
- **Aide et console** : touche **/** ou **?** (hors champ de saisie) ou menu « reglages VJ » -> fenetre des raccourcis (`SHORTCUTS_HELP`, une seule
  fenetre) ; **Console VJ** (menu « reglages VJ », `console_window`, fenetre toujours au premier plan) = grosses commandes (Retour, VJ, Suivante,
  Strobe maintenu, Montee, Drop, Verrou, Tap tempo, aide, PANIQUE) + ligne d'etat + niveaux basses / mediums / aigus et voyant du kick.
  `status_text()` (scene, VJ en marche avec le temps restant / en pause / VERROUILLE, BPM, projection fermee) sert la console **et le titre de
  la fenetre des reglages** (lisible dans la barre des taches), mis a jour par `update_master_panel` (250 ms).
- `check_gl.py` (`check_safeguards`, etapes `x_panic`, `x_panic_key`, `x_tap`, `x_help`, `x_console`, `x_title`, `x_autosave`, projection : reprise
  et arret voulu) ; le harnais redirige `gl.AUTOSAVE_PATH` vers un dossier temporaire. `core/sessionlog.py` est dans `APP_FILES` du portable
  (sinon la version portable ne demarrerait pas).
### Strobe : parties sombres et mode leger (`strobe_dark`, `strobe_div`)

Modes `manual` (un eclair par appui, pas de rafale), `kick` et `light` (arme comme Kick, mais un eclair tous les `strobe_div` kicks, 4 par defaut = un par mesure ; le compteur repart a zero a chaque armement). Le mode Cadence n'existe plus (le tap tempo et « Tempo auto » ne touchent plus au strobe).
- **Filtre de parties sombres** `strobe_dark` (0,05..1, curseur « Sombre » de l'onglet *Live*, reglage de salle) : dans `post.frag`, l'eclair ne blanchit que les pixels dont la luminance de l'image finale est SOUS le seuil (bord adouci de 0,1) ; 1 = toute l'image (defaut, comme avant). Le flash du VJ / du Drop n'est jamais filtre. Remplace les anciens choix couche / zone.
- Le bouton **Verrou** est dans l'onglet *Masters*. « 1 kick sur » = deux fleches ◀ ▶ (N divise / multiplie par 2 : 2, 4, 8, 16). Curseur « Duree » de la montee : 2 a 60 s (defaut 45).
- `check_gl.py` : mode leger (8 kicks = 2 eclairs, remise a zero) et strobe sombre en pixels (moitie noire / moitie claire, seuil 50 %, bas, 100 %).

### Effets par couche (fond / logo decorreles)

Le rendu a 3 passes : `scene.frag` tourne **deux fois** (`u_pass` 0 = fond opaque dans `scene_fbo`,
1 = logo seul dans `layer_fbo`, rgba **premultiplie** avec le contour lumineux AJOUTE en rgb et
l'alpha du logo : composite `rgb + fond*(1-a)` = additif, comme avant), puis `post.frag` applique
`layer_fx()` (wobble, ripple, glitch, aberration) a chaque couche avec ses intensites
(`u_fx_bg`, `u_fx_logo`, vec4) et pose le logo sur le fond, puis le HUD.
- **`fx_link` = 1 (defaut)** : le logo reprend les reglages du fond et le centre de l'image, donc le
  resultat est celui de l'ancien post-traitement unique. `fx_link` = 0 : reglages propres
  (`fxl_on`, `fxl_int`, 4 effets), ondes de choc et aberration centrees sur le **centre du logo**
  (`u_center_logo`), glitch avec un autre tirage (`u_salt_logo`). `layer_effects()` calcule les
  intensites effectives ; `set_fx_link()` copie les reglages du fond en deliant (rien ne saute).
- `fx_on[0..3]`/`fx_int[0..3]` = fond ; l'indice 4 (pulsation, tremblement, contour) reste l'effet
  "logo" a part, il n'est pas duplique. Automations `fxl_int0..3` (actives par defaut, periodes
  differentes de celles du fond) ; `get_param`/`set_param` gerent `fx_intN` et `fxl_intN`.
- Aberration chromatique sur la couche logo : chaque canal de couleur vient de son decalage, l'alpha
  est le max des trois (pas de frange sombre aux bords).
- Touches : Maj+1..4 (effet sur le logo, delie si besoin), L (lier/separer). GUI : case « Memes effets que
  le fond » (panneau Effets sur le logo, masque tant qu'elle est cochee).
- Un glitch de la couche logo decale ses bandes jusqu'a ~100 px a 640 px de large : un test qui
  compare "hors du logo" doit raisonner par **lignes**, pas par marge autour du logo.

### Automations (`AUTOMATION_SPECS`, touche T, `--no-automation`)

Pour que les visuels vivent tout seuls : 23 reglages (dont 4 pour les effets de la couche logo) peuvent suivre une courbe (fond motif :
teinte, angle, vitesse, carreaux, contraste, cadence ; logo : position X/Y, tailles,
opacite, pulsation, contour et son rayon ; effets : les 5 intensites). Meme principe que
"Variation automatique" d'audio2wave (points de controle boucles, interpoles lineairement,
une courbe et une vitesse PAR reglage) et **meme editeur** (`live.AutomationManager` :
case `~`, bouton de courbe, presets sinus/triangle/carre/dents de scie/aleatoire, vitesse).
- **Le moteur tourne dans le fil de rendu** (`AutomationEngine.step`, une fois par image) :
  valeur = fonction pure du temps, precision a l'image, et ca marche aussi **sans `--gui`**.
  La GUI n'est que l'editeur : elle copie son etat dans `params["_automation"]` toutes les
  200 ms (remplacement atomique du dict) et fait suivre les curseurs automatises toutes les
  60 ms. On n'appelle pas `AutomationManager.tick`, c'est ce moteur qui pilote.
- `AUTOMATION_SPECS` (cle -> libelle, min, max, courbe, periode, active par defaut) est la
  source unique : la GUI s'en sert pour enregistrer les editeurs, le moteur pour les plages
  (plus etroites que celles des curseurs : un reglage automatise est PILOTE, le curseur suit ;
  tant que la case `~` est cochee, deplacer le curseur a la main est ecrase).
- **Actives par defaut** (18) : fond, effets, contour du logo, pulsation. **Coupees par
  defaut** : position X/Y, tailles et opacite du logo (une marque ne doit pas se promener sans
  l'avoir voulu). Periodes toutes differentes entre les actives (79, 53, 37, 29, 23...), donc
  l'ensemble ne se repete jamais a l'identique. Plages volontairement moderees (contour <=
  1,5, damier <= 0,32, glitch <= 1,0) : une premiere version (contour jusqu'a 2,2, damier 0,40)
  noyait le texte et saturait des zones en blanc, vu sur un montage a quatre instants.
- `auto_master` (case "Automations actives", touche T, `--no-automation`) fige toutes les
  valeurs sans toucher aux cases individuelles ; a la reprise chaque courbe repart de son debut.
  Bouton "Ambiance par defaut" : remet les courbes et interrupteurs d'origine.
- Sauvegarde : `_automation` (cases, points, periodes) et `auto_master` sont dans
  `~/.audio2wave/gl_params.json` (P / "Sauver reglages"), completes par les valeurs par defaut
  pour toute cle absente ou invalide (`merge_automation`). Les presets d'audio2wave ne les
  contiennent pas (ils ne couvrent que les options live).
- Interpolation "boucle" : le dernier point rejoint le premier. Une courbe en dents de scie
  fait donc un retour en arriere rapide sur sa derniere portion ; pour une grandeur cyclique
  comme la teinte, les valeurs par defaut utilisent un sinus (aller-retour) plutot qu'un tour complet.
### GUI (`--gui`, [gl_gui.py](gui/gl_gui.py))

**La partie Live n'est pas reecrite : c'est la fenetre de reglages d'`audio2wave_live.py`
(`build_gui`), importee du depot audio2wave comme dependance**, donc avec ses presets, ses
automations de courbes, ses info-bulles, son theme et toute evolution future de ce depot.
`gl_gui.run_gui()` :

1. appelle `live.build_gui(live_args, largeur, hauteur, status, restart_event, stop_event, finished_event, on_switch_mode=...,
   parent=host, embedded=True, compact=True, presets_first=True)` et garde le `common.GuiController` renvoye (`panel["ctrl"]`).
   Contrat (API hote de la copie interne) : `build_gui` mute les options `args` en place (un `apply()` par reglage,
   anti-rebond de 400 ms, ou `AUTO_RESTART_INTERVAL_S` = 2 s pour les automations), puis positionne `restart_event` ;
   `status["text"]` alimente sa ligne de statut ; `finished_event` ne ferme plus rien (`embedded`) ; pas de `mainloop()` (il est
   ici). Le **controleur** remplace tout ce qu'on faisait en fouillant leurs widgets : `capture_state()` (etat du panneau, automations
   comprises, valide d'abord la saisie en cours), `apply_state(dict)` (charge un etat comme un preset, rend les cles ignorees ; en live il
   relance aussi le producteur), `commit_pending()`, `widgets` (un widget nomme par attribut d'`args`) ;
2. le mise en page se fait par leurs options (`PANEL_OPTIONS` = `embedded`, `compact`, `presets_first`, conteneur dedie `parent=host`),
   puis `tidy_mode_gui(host, mode, widgets)` : titre de fenetre (« Reglages Live ») retire, titres de section
   (`widgets["section_titles"]`) en bandeaux, "Taille fenetre" renommee "Taille du rendu" et figee (`readonly`). Les grisages
   (`gui_gates`) et les aides de couleur (`gui_colors`) retrouvent leurs lignes par les **widgets nommes** (`bar_gap`, `averaging`,
   `colors`...) ; restent trouves par leur texte : boutons radio et cases (style, forme, halo...), crossover et gain de Snap (absents
   de `widgets`). Les boutons de bascule Live / Snap / Ridge sont **gardes** : voir "Modes Snap et Ridge" plus bas ;
3. fait le **pont** `restart_event` -> `ProducerManager` (`poll_bridge`, dans le fil tkinter
   toutes les 100 ms, donc apres que `apply()` a fini de muter `args`) : copie des options,
   `size` toujours remise a la taille du rendu (la texture video est fixee au lancement),
   aucun redemarrage si `producer_command()` est identique a celle du producteur courant
   (plein ecran seul, automation qui ne change rien), message du gestionnaire reecrit dans
   leur ligne de statut ;
4. accroche a droite, dans la meme fenetre, la partie OVERLAY : les **presets overlay** toujours visibles en haut
   (`preset_bar`), puis un `ttk.Notebook` en **onglets** (chaque curseur automatisable porte sa case `~` et son
   bouton de courbe dans une 3e colonne, sur la meme ligne) : *Fond* (**en tete de la partie LIVE** : un choix Audio2wave / Fond genere (`tab_fond`, hors du canvas) ; dessous, **exclusivement** l'un des deux, deux fenetres du meme canvas defilant (`host_item` = panneau d'audio2wave, `pattern_item` = `pattern_holder` avec presets fond + reglages), basculees par `show_bg` (`state` hidden / normal : le panneau masque n'est pas detruit, les scenes et les tests continuent de l'actionner) ; la taille du canvas ne depend pas du fond affiche (la fenetre ne saute pas) et `cap` en retire la hauteur de l'en-tete ; reglages du fond genere +
   reglages du motif), *Effets* (les 5 effets, intensite globale, sensibilite, effets propres au logo), *Logo*
   (source Image/Texte/Video, position, opacite), *Aura du logo* (halo holographique + reaction a l'audio :
   pulsation, tremblement, contour), *Fonte du logo* (fonte acide), *Cellules*, *Noise* (hasard sur les effets coches) ; **dans la partie VJ** (pas dans l'overlay) : onglet *Affichage* de l'espace de controle global du panneau etendu (le contenu `tab_aff` est construit avec la fenetre, parent = la barre, puis pose dans l'onglet par `pack(in_=...)` ; `toggle_aff(True)` ouvre le panneau sur cet onglet, `False` le referme) *Affichage et analyse* (entree d'analyse sounddevice, boutons plein ecran / barres
   debug / recharger shaders / sauver, automations, mesures bass/mid/high/beat et statut fps/logo/messages).
   **Hauteur** : la fenetre depassait la hauteur d'un ecran (deux colonnes empilees : 850 px au repos, plus de
   1100 px avec le motif ouvert + les effets du logo delies + le halo deplie). Les onglets l'ont ramenee a
   **648 px au repos et 653 px dans le pire cas** (1300-1380 px de large) ; elle est maintenant dictee par le
    panneau LIVE d'audio2wave (763 px a l'origine), dont on **resserre les marges** par leur option `compact=True` (6 / 2 / 4 px au
    lieu de 11 / 4 / 7, plus de boucle qui reecrit leurs `pady`). Le panneau d'audio2wave est dans un **canvas defilant** plafonne a 600 px
   (`LIVE_MAX_HEIGHT`, ascenseur + molette) : Snap (~950 px) et Ridge (~850 px) sont plus hauts que Live. `check_gl.py` verifie qu'elle reste sous 700 px. Pieges : (1) une cellule
   de grille qui s'etend sur plusieurs lignes (`rowspan`) etire les lignes voisines (la ligne 1 de la fenetre a
   maintenant trois cellules sans `rowspan` : panneau d'audio2wave | trait | partie OVERLAY) ; (2) `ttk.Notebook` ne prend pas le theme sombre tout seul (`theme_use("clam")` + couleurs
   des onglets, filets clairs a neutraliser `bordercolor/lightcolor/darkcolor`) ; (3) un widget d'un onglet
   non affiche ne recoit pas les evenements clavier : les tests selectionnent l'onglet Logo avant de taper du texte.
   **Fenetre redimensionnable et adaptative** (`resizable(True, True)`, minimum `MIN_WIN_W` x `MIN_WIN_H` = 940 x 420 ;
   elle etait figee par un `root.resizable(False, False)` herite de `build_gui` d'audio2wave). Tout le contenu est dans
   une **page** (`page`, un frame dans `page_canvas`, avec son ascenseur et la molette) : rien n'est jamais coupe, et
   la page prend au moins la hauteur de son contenu. Trois etats, choisis par la largeur du canvas (`set_layout`,
   `on_page_config`) : (1) **large** (>= `layout["wide_need"]`, la largeur naturelle cote a cote mesuree a l'ouverture) :
   LIVE | trait | OVERLAY, la hauteur et la largeur en plus vont aux panneaux (colonne OVERLAY et ligne 1 ont un poids) ;
   (2) **moyenne** : bandeau LIVE, panneau d'audio2wave a sa hauteur naturelle, bandeau OVERLAY, partie OVERLAY a toute
   la largeur ; (3) **basse** : meme chose, avec l'ascenseur de la page. En mode large le panneau d'audio2wave garde
   son propre ascenseur (`cap_now`, 600 px au depart) ; empile il n'a plus de plafond et c'est la page qui defile.
   **Cote a cote la page ne defile pas** : LIVE (canvas d'audio2wave) et OVERLAY (`right_cv` / `right_bar`, la colonne `right` est dans un canvas defilant, `right_wrap` est ce qui est place dans la grille) ont chacun leur ascenseur et se partagent la hauteur de la fenetre ; empilee, c'est la page qui defile. La molette defile ce qui est sous la souris (`under(widget, conteneur)`, test exact du chemin Tk), **Maj + molette = defilement horizontal** (bande des scenes, liste du panneau etendu).
   Pieges : la taille **voulue** du canvas de page suit celle du contenu (sinon `root.winfo_reqheight()` ne bouge plus et la
   fenetre ne s'ouvre pas a la bonne taille) ; changer de mise en page change les tailles voulues, d'ou un deuxieme
   passage de `on_page_config` (`after_idle`), sans quoi la page garde la hauteur de l'etat precedent (vu : 1250 px de
   haut en revenant en large).
   **Taille voulue de la page surveillee** (`watch_page`, 300 ms) : ouvrir « Affichage et analyse » (ou un onglet plus haut) change la taille VOULUE de la page sans changer sa taille, donc aucun `<Configure>` : l'ascenseur et la zone de defilement restaient figes (contenu coupe, plus defilable en fenetre agrandie). `on_page_config` est rappelee des que la taille voulue change.
   Le bouton « Plein ecran » de « Affichage et analyse » demande un ETAT (`fullscreen=0|1` selon `s.window.fullscreen`), comme la case : refuse sur l'ecran des reglages avec un message au lieu de les recouvrir ; seule la touche F de la fenetre de rendu bascule sans garde.
   Les curseurs de casual-overlay (`compact_scale`) affichent leur valeur a COTE et non au-dessus (-22 px par ligne).
- **Presets en tete de chaque bloc** : presets overlay (`preset_bar`), presets fond (haut du fond genere) et presets d'audio2wave
  (`presets_first=True` de leur `build_gui` : le bloc `PRESETS ...`, `Charger`, `Sauvegarder sous` et `Mode VJ` de Snap passent sous
  le titre, la ligne de statut reste en bas ; c'est `common.move_rows_to_top`, sur des numeros de lignes enregistres, plus un
  deplacement par texte de libelle). `check_gl.py` (`presets_top_state` : les presets viennent avant SOURCE, tout en haut).
- **Info-bulles dans la fenetre** : `live.Tooltip` (audio2wave, aussi pour Snap, Ridge et nos panneaux) mesure l'info-bulle puis la garde
  dans la fenetre de reglages (decalee a gauche si elle depasse a droite, au-dessus du widget si elle depasse en bas) : c'est
  audio2wave qui le fait (avant, `install_tooltip_clamp` remplacait sa methode `_show` ; supprime, comme `tooltip_position`).
  `check_gl.py` (`tooltip_violations`) survole chaque widget qui porte une info-bulle et verifie qu'elle est dans la fenetre (large, 940 px, 420 px de haut, panneau etendu, panneau Snap). Seuil de la mise en page empilee
  (`layout["wide_need"]`) : sans la largeur de l'ascenseur du panneau d'audio2wave, qui apparait ou disparait selon la hauteur (une
  fenetre a sa taille naturelle sans ascenseur passait en empile ; course avec la minuterie `settled`, vue par le test de taille VJ).
- **Structure visible** : deux grandes parties, LIVE (titre bleu, fenetre d'audio2wave) et OVERLAY
  (bandeau violet), separees par un trait violet de 3 px ; le titre d'audio2wave est retire de la ligne 0
  pour y poser les bandeaux. Toutes les sections sont des **bandeaux** (`style_band`, fond `GUI_PANEL_BG`, texte
  gras colore) : ceux d'audio2wave (SOURCE, SPECTRE, COULEURS, SORTIE, PRESETS -> "PRESETS LIVE") sont
  restyles par `tidy_mode_gui`, les notres par `add_section_title`. Le bloc "Motif genere" doit etre pose
  avec `sticky="new"` + `columnconfigure(2, weight=1)` pour que son bandeau prenne toute la largeur.
  Le halo holographique n'est plus replie (il a son onglet).

- **Modes Snap et Ridge** ([py_modes.py](gui/py_modes.py), `activate_mode` / `build_window` dans gl_gui.py) : les boutons
  Live / Snap / Ridge d'audio2wave changent la **source du fond**, sans nouvelle fenetre et sans modifier
  audio2wave (leurs `build_gui` appellent `on_switch_mode`, qu'on branche). Snap et Ridge dessinent leurs images
  en Python : avec l'API hote d'audio2wave on les lance **sans fenetre ni ffplay** : `parse_args(argv)` (`make_args`) construit leurs
  options, `start_headless(args, size=..., frame_sink=f, status=...)` ouvre la capture micro (ffmpeg dshow, comme leur `run_app`) et leur
  fil de rendu, et chaque image va dans un `PushStream` (derniere gagne, aucune image melangee) lu par un `FrameReader` normal.
  `PyModeSource` = un mode en marche (**repli** depuis que Snap et Ridge sont dessines en GL : voir `engine/native_modes.py` juste apres ; actif avec
  `CASUAL_MODES_PYTHON=1` ou sans numpy / moderngl). Plus de remplacement de leur module `subprocess` ni d'echange de `sys.argv`. Les tests mettent un
  faux micro dans le module `subprocess` de snap / ridge (`check_gl.fake_microphone`, test seulement) et verifient qu'aucun ffplay
  n'est lance.
  - Le fil GL relit `s.reader` a chaque image : changer de mode = changer `s.reader` (flux Live du
    `ProducerManager`, ou flux du `PyModeSource`). **Live est suspendu** pendant Snap/Ridge
    (`ProducerManager.suspend()` arrete ffmpeg, `resume(args)` le relance en doux au retour) ; l'arret d'un mode
    Python ferme son flux (le `run()` s'arrete a sa prochaine ecriture) puis se termine en arriere-plan.
  - **Seul le panneau d'audio2wave est reconstruit a chaque bascule** (`swap_live_panel`, appelee par `request_mode` ; `build_window` n'est appelee qu'une fois, a l'ouverture) : l'etat vit dans `s.params` (reglages overlay, `_automation` copie avant la
    reconstruction), les widgets le relisent a leur creation ; les `root.after` de la fenetre passent par
    `after()` qui s'arrete tout seul (`alive`). Les options de chaque mode sont gardees (`s.mode_args`) : revenir
    a Snap retrouve ses reglages. L'entree audio suit d'un mode a l'autre.
  - Le panneau du mode est construit dans un `tk.Frame` dedie pose dans un canvas (`parent=host` de leur `build_gui`, avec `embedded` : pas
    de titre, de redimensionnement ni de `protocol`, et la fenetre n'est jamais detruite par eux). Leur `refresh()` ne ferme rien en
    mode `embedded` ; on leur donne quand meme un evenement jamais positionne pour Snap/Ridge, et c'est notre `refresh()` qui ferme la
    fenetre quand `s.finished_event` l'est.
  - **Plus de visionneur a fermer** : `run()` / `start_headless()` d'audio2wave envoient chaque image a `frame_sink(frame, size)` (un
    `FrameSinkViewer` dans leur code remplace ffplay) ; `--fullscreen` n'agit plus, la taille se pilote par `args.size`. Une image d'une
    autre taille que le rendu est ignoree (`PyModeSource._sink`), jamais de decalage ; seul `PyModeSource.stop()` ferme le flux. La
    frame recue est le tampon de travail de `run()` : `PushStream.write` la copie. Le champ taille et la case Plein ecran de Snap/Ridge
    sont figes dans la GUI. (Ancien piege, disparu avec le faux `ffplay` : fermer un visionneur fermait le flux, fond noir apres le
    preset `club`.)
  - Presets : chaque mode a ses presets (`live_presets.json`, `snap_presets.json`, `ridge_presets.json` dans
    `~/.audio2wave/`, memes fichiers que dans audio2wave), section "PRESETS LIVE / SNAP / RIDGE". Ridge exige
    une entree audio (refus avec message sinon) ; Snap sans entree attend qu'on en choisisse une.
  - **Snap et Ridge dessines en GL** (`engine/native_modes.py`, `gl_shaders/mode_pencil.frag`, `mode_bars.frag`, `mode_ridge.frag` ; par defaut quand
    numpy et moderngl sont la, **meme en `--synthetic`**, sans ffmpeg ni ffplay ni micro supplementaire) : l'ANALYSE reste celle d'audio2wave (`pencil_heights`,
    `detect_kicks`, `render_ridge_line`, `resolve_gain`, `RidgeGain`... appelees telles quelles sur le PCM du tampon `session.audio.ring`, mono) mais le DESSIN est un
    shader : une photo (a chaque `args.interval`, calee sur l'horloge) devient des tableaux de hauteurs de colonnes envoyes en texture (R32F), et la revelation colonne
    par colonne qui occupait tout l'intervalle n'est qu'un reglage anime (`u_drawn`, calcule sur l'horloge de la photo : `t0` -> `t1` = l'echeance). `NativeModeSource`
    (fil d'analyse + pont) a les memes attributs que `PyModeSource` (`args`, `status`, `stop_event`, `finished_event`, `capture_state` = `RingCapture` pour le bouton
    « Mesurer », `module`) ; `render(ctx, now)` est appele par le fil GL (`engine.projection.loop`) et rend la texture du fond, que `Renderer.mode_tex` met a la place de
    `video_tex` (fond « video » du VJ prioritaire) ; les ressources GL (`ModeGL`) se creent dans le fil GL et se refont si le contexte change (projection fermee puis
    rouverte). **Verifie pixel a pixel** : pencil (trait, `--wave`, halo des kicks, trait epais / couleurs, video interieure et exterieure, revelation progressive) et Ridge
    (6 lignes de suite : decalage, occultation, trait) = EXACTEMENT les memes pixels que `compose_pencil` / `paint_ridge_line` (ecart <= 1 niveau) ; simple et rekordbox
    (barres de `showwavespic` : `bar_scale` = lin / log10(1 + 32767 a) / log10(32768) / racine carree / racine cubique, crete ou moyenne de |x| par colonne, agrandissement au
    plus proche voisin centre sur le pixel, passe-bas du rekordbox refaits en numpy a 24 dB/octave, sans phase) comparees a ffmpeg : 0,4 a 3 % de pixels differents, des
    bords a un pixel. Les roundings d'audio2wave (`round()` pair) sont faits cote Python (rangees 5 / 6 des donnees) pour que les halos soient identiques. Videos `--video` /
    `--video2` : `BgVideo` (cover) -> textures `u_vid` / `u_vid_out`. Changer l'entree dans le panneau du mode fait suivre l'analyse (`_follow_device`, meme nom,
    comparaison par prefixe). `--stereo` / `--split-channels` : sans objet (tampon mono) ; l'image du mode n'est plus codee en 8 bits RGB par le tube, mais le
    rendu final passe de toute facon par le post-traitement du fond.
  - Limites : la case "Plein ecran" de Snap/Ridge n'existe pas (c'est notre fenetre GL qui gere l'affichage) ; le Mode VJ
    d'audio2wave (enchainement de presets) n'est pas verifie ici ; le repli Python (`PyModeSource`) ouvre le micro une fois de plus (ffmpeg dshow).
- **Reglages incompatibles grises** ([gui_gates.py](gui/gui_gates.py)) : un reglage sans objet dans le mode ou l'etat
  courant est **grise** (jamais retire : la disposition ne bouge pas et on voit qu'il existe). Pour les panneaux
  d'audio2wave (sans option dediee dans leur code) : `install_mode_gates` retrouve les widgets par leur texte et
  suit leurs variables Tk par une **trace Tcl** (`trace add variable`) ; surtout pas `tk.StringVar(name=...)`, dont
  le ramasse-miettes detruirait la variable d'audio2wave. Regles : Live radio -> Forme, Lissage, Echelle des
  frequences, Espace entre barres ; analyzer en ligne -> Espace entre barres ; Snap pencil -> Echelle, Filtre
  colonne, Crossover ; Snap autre style -> trait, sinusoide, halo des kicks, videos (+ "Temps reel" et "Rayon du
  halo" sans halo coche, oscillations sans sinusoide) ; Snap hors rekordbox -> Crossover ; Snap/Ridge en gain
  automatique -> Gain manuel. Chez nous (`Gates`) : curseurs du halo / de la fonte / des cellules tant que l'effet
  est coupe ; palette du fond (couleurs 1/2 et angle seulement en Duo, rien en Banc de test).
  Un widget vise par plusieurs regles n'est actif que si **toutes** sont satisfaites, et son etat d'origine
  (champ deja en lecture seule) est restaure, pas ecrase. Piege corrige au passage : la taille du rendu etait figee
  en rendant en lecture seule tous les champs de 6 caracteres, ce qui figeait aussi le **crossover de Snap** ;
  on cible maintenant la ligne "Taille du rendu". Un test qui detruit une fenetre d'audio2wave doit laisser tourner
  leur `refresh()` apres `finished_event.set()` (sinon bruit Tcl, ou boucle d'evenements sans fin).
- **Aides a la saisie des couleurs** ([gui_colors.py](gui/gui_colors.py)) : les champs texte de couleur d'audio2wave
  ("Couleurs", "Couleur de fond" ; "Couleur du trait" de Ridge) gardent leur champ mais gagnent, dans la meme cellule
  de la grille, une **pastille par couleur** (un clic ouvre le selecteur et remplace CETTE couleur, ecrite en
  `0xRRGGBB`), un **menu de 14 couleurs nommees** (remplace la premiere) et un **"+"** pour ajouter une couleur
  aux champs qui en acceptent plusieurs ("Couleurs" de Live et de Snap/rekordbox). Les pastilles suivent le champ
  par une trace Tcl (taper, charger un preset, choisir une couleur). Nos propres champs (`color_row`) avaient deja
  leur selecteur. Pieges : le champ est **re-grille dans un conteneur** (`entry.grid(in_=box)`) et doit etre remonte
  (`tkraise(box)`), sinon le conteneur le cache ; Snap et Ridge n'appliquent leur champ que sur Entree / perte de
  focus, d'ou `focus_force()` + `<Return>` simules apres un choix (Tk ne livre un evenement clavier qu'a la fenetre
  qui a le focus) ; un nom que PIL ne connait pas donne une pastille grise "?" (le champ reste valide pour ffmpeg).
- **Case "Plein ecran"** de leur fenetre : `args.fullscreen` change -> commande
  `fullscreen=0|1` envoyee au fil GL (`Session.commands`), sans redemarrer ffmpeg. La
  touche F de la fenetre GL ne remet pas la case a jour (sens unique).
- **Presets live (audio2wave)** : deja presents dans la fenetre d'audio2wave integree (section "PRESETS",
  rebaptisee "PRESETS LIVE"), partages avec le depot audio2wave (memes fichiers, `USER_PRESETS_PATH`) ; ils
  ne contiennent que les options live + automations des reglages live. Aucun code de notre cote.
- **Presets overlay et presets fond** (deux jeux **decorreles**, comme les deux parties de la GUI) : `OVERLAY_PRESETS`
  (`OVERLAY_PRESETS_PATH` = `~/.audio2wave/overlay_presets.json`) = logo, effets, halo, fonte, cellules, automations
  non `bg_` ; `BACKGROUND_PRESETS` (`BACKGROUND_PRESETS_PATH` = `~/.audio2wave/background_presets.json`) = cles `bg_*`
  (`BG_KEYS`) + automations `bg_*` (`BG_AUTOMATION_KEYS`). `capture_overlay` / `capture_background` fabriquent un preset,
  `overlay_preset_values` / `background_preset_values` le rendent applicable : un preset overlay **ignore** toute cle
  `bg_*` (meme dans un ancien fichier) et garde le fond courant, un preset de fond ne change que `bg_*`
  (`_split_automation` fusionne les automations de chaque cote). Memes gestes dans l'interface (`make_preset_bar`
  dans gl_gui.py, une fois pour la partie OVERLAY, une fois dans le bloc Fond de la partie LIVE) : menu qui applique,
  Mettre a jour, Supprimer avec `live.confirm_dialog`, Sauvegarder sous, Restaurer. **Tout est modifiable** : la classe
  `PresetBook` (audio2wave_gl.py, remplace `live.PresetStore` qui interdisait de toucher aux integres) enregistre une
  version utilisateur qui remplace l'integre, masque un integre supprime (cle reservee `_deleted` du meme JSON),
  `restore_builtins()` rend les integres d'origine ; `default` est toujours present et **toujours en tete**
  (`all()`/`names()`), le supprimer le remet a sa version d'origine. Charger = valeurs validees par `coerce_params`
  puis `sync_widgets()`. Les integres sont partiels et ne touchent pas aux automations sauf si le preset en contient ;
  un preset utilisateur les contient toujours. Les tests utilisent des fichiers temporaires
  (`gl.OVERLAY_PRESETS_PATH` / `gl.BACKGROUND_PRESETS_PATH` remplaces le temps de `run_gui`), jamais ceux de l'utilisateur.
  **Piege (vu en ecrivant ces tests)** : `Gates` grise des groupes de curseurs en reconfigurant leur `state`, et Tk rend
  alors a un `Scale` la valeur perimee de sa variable, qui reecrit `params` par la trace : charger un preset restaurait
  l'ancienne valeur d'un curseur. `Gates._apply` ne reconfigure que si l'etat change et reprend la valeur de la variable,
  et `sync_widgets` part d'un instantane, passe deux fois et le remet dans `params`.
- Trois sauvegardes distinctes : presets live, presets overlay, et `gl_params.json` (touche P / bouton
  "Sauver reglages", reglages courants recharges au lancement).
- **Threads** : tkinter garde le fil principal, `gl_main()` (glfw + moderngl) tourne dans un
  fil. **Tous les appels glfw partent du fil qui a fait `glfw.init()`** ; ce que la GUI veut
  faire executer cote GL passe par `Session.commands` (file lue a chaque image), le reste
  (`Session.params`, relu a chaque image) est ecrit directement. Fermer l'une des deux
  fenetres ferme l'autre.
- **Reglages logo / effets / fond motif / sensibilite** : `params` (dict), effet immediat.
  `--logo`, `--text`, `--logo-scale`, `--logo-pos`, `--sensitivity`, `--background` l'emportent sur les
  valeurs sauvees.
- **`ProducerManager`** (dans audio2wave_gl.py) possede le producteur et son `FrameReader` et
  reprend le principe du redemarrage "doux" d'audio2wave_live : nouveau producteur lance a
  cote, **chauffe** (frames ENTIERES lues et jetees pendant `producer_warmup_seconds`, sinon
  ffmpeg bloque sur un pipe plein et sa moyenne `--averaging` ne converge pas), puis
  `FrameReader.switch_stream()` : le lecteur finit l'ancien flux, jette sa frame partielle et
  repart sur le nouveau ; l'ancien producteur est termine ensuite. Si le nouveau meurt
  pendant la chauffe, l'ancien reste et le statut l'indique. Les demandes rapprochees sont
  fusionnees. Mesure avec le vrai ffmpeg/micro : changement de style analyzer -> radio,
  plus grand trou dans le flux video 63 ms (~2 frames), aucun ffmpeg residuel. La fenetre GL
  n'est jamais touchee.
- **Sans `-d`, le micro du PC est choisi d'office** (`main`) : `pick_default_device()` (pure) prend l'entree
  DirectShow qui correspond a l'entree par defaut de Windows (`system_default_input_name()`, sounddevice ; noms
  tronques a 31 caracteres cote DirectShow, d'ou la comparaison par prefixe), sinon un nom de micro evident, en
  ecartant mixage stereo / boucles / cables virtuels (`NOT_A_MIC`). Fonctionne donc sur n'importe quel PC, `--dry-run`
  compris ; `-d` l'emporte. `NoDeviceManager` ne sert plus que si aucune entree n'existe : le fond demarre alors au
  premier choix d'entree dans leur menu. En `--synthetic`, leur GUI s'affiche mais ses reglages sont
  ignores (message dans leur statut).
- La resolution du rendu ffmpeg (`--render-size`) n'est pas reglable dans la GUI.

### Audio (FeatureExtractor, fonctions pures, testables sans fil ni temps reel)

Fenetre 2048 + Hann, `rfft`, bandes basses 20-150 Hz / mediums 150-2000 / aigus 2000-10000 +
RMS. Auto-gain : maximum glissant a decroissance lente (demi-vie 8 s) avec un **seuil
absolu de silence** (sans lui, l'auto-gain amplifierait le bruit du micro : silence = niveaux
a 0). Lissage attaque 20 ms / relachement 250 ms.

Kick : energie des bins basses > ligne de base glissante x `1 + 1,2/sensibilite` ET ecart a
la base > `2/sensibilite` ecarts-types (`3,5` pendant les 1,5 premieres secondes, le temps que l'ecart-type se stabilise ; l'ancien `3` ne voyait qu'un kick sur trois sur un morceau realiste : le test `check_features` a un corpus de morceaux synthetiques avec basse sur les contretemps, charleston et kick a balayage de frequence, F1 0,49 -> 0,98) (statistique hors kicks), montee en cours, energie
minimale, periode refractaire 150 ms, chauffe 0,5 s. Sortie : enveloppe `u_beat`
(`exp(-t/0,2 s)`) et `u_since_beat`. **Constantes reglees par balayage sur signaux
synthetiques**, pas a l'oreille : a sensibilite 1,0, zero faux positif sur 60 s de bruit
blanc (amplitude 0,05) et kicks detectes a -30 dB de niveau micro. Une ligne de base qui
redescend plus vite qu'elle ne monte (premier essai 1,5 s / 0,3 s) donnait ~2 faux positifs
par seconde sur bruit ; les seuils du premier jet (0,8 / 2,5) aussi (28 / 60 s).
**Limite connue** : un kick pose sur une basse continue a peu pres au meme niveau est
manque a sensibilite 1,0 (battement entre les deux frequences) ; la touche haut (~2,5) le
rattrape (39/40 en test). Valider en soiree avec le vrai son : c'est le composant le plus
fragile.

## Charge GPU : garde-fou et mesure

- **Garde-fou** (`PerfGuard`, audio2wave_gl.py) : une image sur 8 (toutes avec `--stats`) est chronometree par une
  requete GPU ; moyenne lissee comparee au budget d'une image a 60 Hz au plus (un ecran a 144 Hz ne rend pas le budget
  impossible). Au-dessus de 90 % du budget pendant 2 s : message dans la ligne de statut de la GUI (`status["perf"]`,
  en tete) et une fois sur stderr, qui nomme les effets couteux allumes (cellules > halo > fonte) ou conseille
  `--render-size` plus petit ; levee sous 80 % (hysterese, pas de clignotement). Il previent, il ne degrade rien tout seul.
- **Optimisations** (mesure ci-dessus) : le contour lumineux (~25 appels de `logo_at` par pixel) est saute hors du
  rectangle du logo agrandi du rayon du contour (alpha nul au-dela, cellules et fonte comprises) ; une cellule candidate
  ne coute plus qu'un hachage (`chash3`). Un shader de mesure : `--synthetic --stats --max-seconds 13` avec les
  reglages voulus (`load_params` remplacable par un script).

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
- Ouvrir le micro deux fois (ffmpeg dshow + sounddevice WASAPI partage) **fonctionne** sans
  degrader le debit video (mesure : 27,8 / 28,5 / 28,5 fps producteur sans / avec WASAPI /
  avec MME). Le repli "un seul flux + PCM par stdin" du plan n'est pas necessaire.
- `--colors grey`, le defaut d'`audio2wave_live.py`, n'existe pas pour ffmpeg 9.0 (Gyan full
  build, la graphie valide est `gray`) : il logue `Cannot find color 'grey'` **mais ne
  s'arrete pas** et retombe sur du blanc (mesure : 25,7 fps, pixel max 255 ; `gray` donne 128).
  Une premiere version de ce depot affirmait a tort que le producteur mourait et forcait
  `--colors white` : c'etait faux (le debit de 4 frames/s observe alors venait du pipe de
  4 Ko, voir plus haut). Le defaut d'audio2wave est donc laisse tel quel, ce qui garde
  `--dry-run` strictement identique a celui de live. Consequence pour la GUI : un nom de
  couleur inconnu n'est PAS refuse par le producteur, il s'affiche en blanc.
- **Analyzer : reglages qu'on contournait, corriges a la source** (signales par casual-overlay, repris par audio2wave ; `check_bar_gap` et
  `check_line_resolution` verifient le nouveau comportement, vrai filtre ffmpeg a l'appui) : (1) `bar_gap` >= 1 faisait un separateur noir plus
  large que la barre = ecran noir (preset `devil` a 1,4) : plafonne a `MAX_BAR_GAP` = 0,7 dans `build_filter` ET dans la plage du curseur ;
  (2) analyzer en courbe (`--shape line`) = escalier flou avec 128 points / fenetre de 512 : 400 points (`LINE_ANALYZER_BARS`) et fenetre de 2048
  (`LINE_WIN_SIZE`) par defaut. Overlay n'y touche plus (`limit_bar_gap` et `sharpen_line` supprimes).
- **`ProducerManager.args` doit etre une COPIE** des options d'audio2wave_live : leur GUI les mute en place,
  et `push_live` compare `producer_command(nouvelles)` a celle du gestionnaire pour eviter un
  redemarrage inutile. Avec le meme objet, la comparaison etait toujours vraie ("Reglages a jour") et
  plus aucun reglage du fond (style radio, nombre de barres...) n'arrivait jusqu'a ffmpeg. Les tests
  ne le voyaient pas : leur faux gestionnaire copiait deja les options. Verifie de bout en bout avec la
  vraie GUI + vrai ffmpeg + vrai micro (style analyzer -> radio, puis barres -> 64 : 2 redemarrages).
- Le producteur peut mourir en cours de route : message unique sur stderr, derniere image
  conservee (les effets continuent) plutot que fermer la fenetre en soiree.

## Verifications

`python tools/check_gl.py` (aucun peripherique requis) : lecteur de frames avec faux producteur
par blocs de 4 Ko (aucune frame melangee, ordre conserve, partielle jetee, latest-wins) ;
features sur signaux synthetiques (un beat par salve a 48 et 44,1 kHz, periode 500 ms,
-30 dB, bruit blanc, sinus continu, silence, isolation des bandes, ring buffer) ; reglages
(P) ; remplacement du producteur a chaud (`switch_stream` sans frame melangee, ancien flux
conserve si le nouveau meurt, demandes fusionnees, arret propre) avec de faux producteurs ;
GUI : la vraie fenetre d'audio2wave_live integree, pilotee par ses propres widgets (curseur
Gain, radio Style, case Plein ecran) : taille figee, presets et
automations presents, rafale de reglages = un seul redemarrage sur une copie, taille du
rendu toujours remise, plein ecran = commande GL sans redemarrage ffmpeg, statut relaye,
et nos panneaux (couleur invalide ignoree, logo, effets, fond) ; bascule Live -> Snap -> Ridge -> Live avec un
faux micro (mode actif, ffmpeg live suspendu puis relance sur la meme entree, images du mode lues par le rendu,
bandeau / presets / boutons du mode, fenetre sous 700 px) ;
presets overlay et fond decorreles (chargement d'un integre, widgets qui suivent, sensibilite exclue, fond jamais touche par un preset overlay et inversement, integres modifiables / supprimables / restaurables, `default` toujours en tete, sauvegarde /
rechargement d'un preset utilisateur avec texte et curseurs, integre non ecrasable, `default` non modifiable,
menu, suppression) ;
halo holographique (actif par defaut et non audioreactif ; logo strictement intact ; fond modifie loin du logo ;
portee, deformation / lumiere / poussiere independantes ; holo_reach monotone ; independant de la resolution ;
suit la position du logo ; texte et video ; couper apres allumer rend l'image d'origine) ;
effets par couche (intensites effectives lie/separe, delier copie, wobble sur le logo seul = fond strictement
intact, wobble sur le fond seul = logo strictement intact, lie = les deux deformes, glitch du logo limite a
ses lignes, pas de frange noire, sans logo = fond seul) ;
logo anime (webm VP9 alpha conserve et non premultiplie, boucle, detourage colorkey, changement de
source sans ffmpeg residuel, rendu : moitie opaque rouge / moitie transparente) ;
fond motif (non vide, defile, fige a vitesse 0, taille des carreaux, palette duo, teinte de
120 degres, flash et bascule au kick, logo par-dessus) et selecteur de fond de la GUI ;
rendu en contexte standalone (image identique a la video quand l'audio est coupe,
chaque effet visible isolement, image ni noire ni saturee, logo incruste et video intacte
ailleurs, logo jamais hors cadre, shader invalide refuse sans perdre l'ancien).
Controle visuel : `--screenshot fichier.png --max-seconds N` (avec `--hud`).

Non verifie automatiquement (demande le materiel) : touches clavier (F, H, R, P, 1-5, +/-),
rendu sur le vrai videoprojecteur (DPI, ordre des moniteurs), phase du kick avec la musique
reelle, latence acoustique bout en bout.

## Mesures (poste de dev : AMD Radeon integre, ffmpeg 9.0, micro Realtek, moniteur cible 59 Hz)

| Mesure | Resultat |
|---|---|
| Rendu, 720p et 1080p, plein ecran | 58-60 fps stables (limiteur), pire intervalle 32 ms occasionnel |
| Producteur ffmpeg (720p et 1080p) | 29-30 frames/s |
| Upload texture | 0,5 ms (720p), 1,0-1,2 ms (1080p) |
| Temps GPU par frame (2 passes) | 5-7 ms |
| Halo holographique (champ a 1/4 de resolution + post), image + relecture | +1,7 ms a 720 p (6,9 -> 8,6), +3,3 ms a 1080 p (10,6 -> 13,9) |
| Temps GPU 1080 p, effets du logo (fonte, cellules, halo, glow) tous allumes | 21,2 ms avant optimisation -> **14,5 ms** (budget 16,9 ms a 59 Hz) ; cellules seules 19,6 -> 12,7 ms ; defaut 12,8 -> 6,0 ms |
| Temps GPU 720 p, tout allume | 11,1 ms avant optimisation |
| Capture sounddevice | latence rapportee ~22 ms (blocs de 512, WASAPI, 48 kHz) |
| Detection du kick (logicielle) | 25 ms mediane, 29 ms max (hop 10,7 ms + fenetre 2048), sur un vrai kick a attaque instantanee |

Latence audio -> effet estimee : capture ~22 ms + detection ~25 ms + jusqu'a une frame
(16,7 ms) = ~45-65 ms, **au-dessus de la cible de 50 ms dans le pire cas**, et non mesuree
acoustiquement (impulsion sur le micro -> premiere image modifiee). Leviers : bloc de
capture de 256, fenetre d'analyse plus courte (au prix de la finesse des basses). Le visuel
de base garde en plus la latence propre d'`--averaging` (voir CLAUDE.md d'audio2wave).
Le generateur video `--synthetic` est limite par numpy a ~16 frames/s en 1080p : ce n'est pas
une mesure de l'application.

## Hors POC

Presets combines live + overlay (les deux se chargent separement), automation des reglages live via le moteur GL (ils gardent celle d'audio2wave, qui
redemarre ffmpeg), MIDI,
sortie Spout/NDI, plusieurs logos a la fois, synchro de la lecture du logo anime sur le kick.

Lanceur : [start.bat](start.bat) (demande explicite, malgre le "pas de .bat" du plan d'origine) : va dans
son dossier, utilise `.venv\Scripts\python.exe` (message d'installation s'il manque), lance
`audio2wave_gl.py --gui %*` (arguments transmis, donc `-d`, `--text`, `--synthetic`... marchent) et garde la
console ouverte (`pause`) si le programme sort avec un code d'erreur. Fichier en ASCII + CRLF (accents
interdits, comme le reste). Une sortie anormale isolee (code -1073740022) a ete vue une fois au tout premier
lancement, non reproduite sur 17 lancements suivants.
