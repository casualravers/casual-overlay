# Fenetre de reglages (GUI)

Fiche de [CLAUDE.md](../../CLAUDE.md) (issue #132 : detail deplace tel quel). Fichiers : gui/gl_gui.py, gui/gui_*.py, gui/skin.py, gui/theme.py, gui/icons.py, gui/py_modes.py, gui/gui_gates.py, gui/gui_colors.py, engine/native_modes.py, engine/sphere_mode.py, engine/video.py (ProducerManager).

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
  mise a jour sur `<<NotebookTabChanged>>`), **cases et radios** de `ICON_TOGGLES` seulement (`with_icon`). Un libelle sur plusieurs lignes n'a pas d'icone. **Icone seule** (skin.ICON_ONLY : Supprimer, Mettre a jour, Restaurer, Actualiser, Sauvegarder, Dupliquer la derniere, Tirer maintenant, Rescanner, Centrer, Aucun / Aucune, Plein ecran (bouton)) : l'image ne porte que l'icone, le widget garde son libelle (cget, tests), et _tip_enter / _tip_leave l'affichent en info-bulle ; si le code de la GUI a deja pose une info-bulle (celle d'audio2wave est une Toplevel enfant du widget), le libelle en devient la premiere ligne au lieu d'en ouvrir une seconde. **Mode VJ** : la barre des scenes, le panneau etendu (menus SET / ordre / duree : skin pose l'icone sur les menus a fleche dans le texte, Tk la place a gauche ; Renommer / Retirer en icone seule), les onglets et boutons de l'espace de controle (Strobe, Verrou, Montee ↑ / Drop ↓, Tap tempo, Phases auto...) et la **Console VJ** (fenetre a boutons 	k.Button hors habillage : tn(..., icon=...) pose une icone de 26 px, check_gl.py x_console_icons) ont leurs icones. Les actions critiques ou ambigues gardent leur texte (PANIQUE, Demarrer la projection, Diagnostic, Tap tempo, Strobe, Verrou...). Pour
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

### GUI (`--gui`, [gl_gui.py](../../gui/gl_gui.py))

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
   pulsation, tremblement, contour), *Fonte du logo* (fonte acide), *Cellules*, *Noise* (hasard sur les effets coches) ; **dans la partie VJ** (pas dans l'overlay) : onglet *Affichage* de l'espace de controle global du panneau etendu (le contenu `tab_aff` est construit avec la fenetre, parent = la barre, puis pose dans l'onglet par `pack(in_=...)` ; `toggle_aff(True)` ouvre le panneau sur cet onglet, `False` le referme) *Affichage et analyse* (entree d'analyse sounddevice, boutons plein ecran / sauver (les barres debug H et le rechargement des shaders R ne sont plus que des touches de la fenetre de rendu), automations, mesures bass/mid/high/beat et statut fps/logo/messages).
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
   **Fenetre agrandie au lancement** (`reveal` de `run_gui` : `gl_gui.maximize`, etat « zoomed » de Windows, barre des taches visible ; `CASUAL_NO_MAXIMIZE=1` le coupe, `check_gl.py` le pose : les tests mesurent la fenetre a sa taille naturelle). Agrandie, elle garde sa taille : `set_expanded`, la poignee du panneau et la taille gardee pendant le VJ ne lui imposent plus de geometrie (`is_maximized` ; Windows l'ignorait deja, la garde sert ailleurs). `check_gl.py` : `gui_zoom_steps`. **Fenetre redimensionnable et adaptative** (`resizable(True, True)`, minimum `MIN_WIN_W` x `MIN_WIN_H` = 940 x 420 ;
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

- **Modes Snap et Ridge** ([py_modes.py](../../gui/py_modes.py), `activate_mode` / `build_window` dans gl_gui.py) : les boutons
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
- **Mode Sphere** (`engine/sphere_mode.py`, `gl_shaders/mode_sphere.vert` / `.frag`, `sources/audio2wave/audio2wave_sphere.py` : options, presets, fenetre de reglages ; bouton « Sphere » a cote de Live / Snap / Ridge) : le spectre du son projete sur une sphere
  vue de face. **Les frequences partent du centre** (graves au milieu, aigus vers le bord ; la face arriere repete la face avant) et poussent la surface vers l'exterieur ; l'**angle autour du centre decale l'historique** (`history` secondes reparties
  sur le tour : relief en spirale, 0 = anneaux identiques). **La taille de la sphere suit l'intensite du morceau** (`size_react`, `SpectrumTracker.scale` : niveau RMS rapporte au plus fort niveau recent, oubli lent, attaque 0,15 s / relachement 0,7 s ;
  0 = taille fixe) et pulse sur les graves (`pulse`). Trois **sous-options de style** (`style`) : `wire` (fil de fer, additif), `surface` (eclairee, ombrage plat, tests de profondeur), `points` (nuage). Camera fixe (`tilt`, `spin` = 0 par defaut). **Curseur « Forme »** (`shape` 0..5, automatisable : une courbe fait morpher toute seule) : sphere, tore, noeud de trefle, ressort enroule, fleur (superformule de Gielis), bouteille de Klein ; entre deux valeurs les formes se melangent (fondu). Une forme = un point central Q + un decalage O (`shape_at` dans `mode_sphere.vert`), le relief grossit O ; la bande de frequences et l'historique ne dependent que de (azimut, angle depuis le centre), jamais de la forme.
  Dessin GL seulement (pas de repli Python : sans numpy / moderngl le mode est refuse avec un message). Pas de fil d'analyse : `SphereSource.render` (fil GL) lit 2048 echantillons du tampon (`session.audio.ring`), FFT, bandes logarithmiques
  (`bands`, 40 Hz - 12 kHz, compensation de la pente du spectre 4,5 dB / octave, dynamique `db_range` relative au plus fort niveau recent, silence = aucun relief), lissage (`smooth`), historique de 128 lignes (une par 1/60 s) en texture R32F lue par
  le sommet. Meme interface que `NativeModeSource` (dont elle herite), donc `activate_mode` / `build_live_panel` / scenes (`mode` = `sphere`) / presets (`sphere_presets.json`, `preset_registry.MODES`) la traitent comme Snap et Ridge.
  Dans les modes du Full Randomizer (`randomizer.A2W_MODES` : tout nouveau mode d'audio2wave s'y ajoute, avec la case de `gui_panel` ; un set deja enregistre ne coche pas Sphere tout seul ; sans numpy / moderngl `random_scene` ne le tire pas). Ecarts a l'origine d'audio2wave : `sources/audio2wave/VENDORED.md`. `check_gl.py` : `check_sphere_mode` (bandes, silence, taille qui suit l'intensite, trois styles, bascule, presets, fenetre).
- **Reglages incompatibles grises** ([gui_gates.py](../../gui/gui_gates.py)) : un reglage sans objet dans le mode ou l'etat
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
- **Aides a la saisie des couleurs** ([gui_colors.py](../../gui/gui_colors.py)) : les champs texte de couleur d'audio2wave
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
  `PresetBook` (core/params.py) ne connait qu'UN preset integre, `default` (vide), toujours present et **toujours en tete** (le supprimer le remet a sa version d'origine) ;
  tous les autres sont des presets d'utilisateur traites pareil (mettre a jour, supprimer). Plus de `sobre` / `neon` / `chaos` integres, plus de bouton Restaurer ; ceux deja ecrits dans le fichier restent.
  **Rangement** (`make_preset_bar`, une fois pour l'overlay, une fois pour le fond) : un nom `dossier/nom` (un seul niveau, minuscules) range le preset dans un sous-menu ; **favoris** (bouton ☆ / ★, cle reservee `_favorites` du fichier)
  et **derniers charges** (`_recent`) en tete du menu ; champ **Chercher** (liste plate des noms qui contiennent le texte) ; `core.params.menu_layout` est la mise en forme pure. Les cles du fichier qui commencent par `_` ne sont jamais des presets.
  **Indicateur « modifie »** : `base["snap"]` = la capture que donnerait le preset choisi ; toutes les 600 ms `check_modified` la compare a l'etat courant (`core.params.differs_from` : ignore les reglages que les automations allumees font bouger et le bruit d'arrondi) ;
  « ● modifie » + bouton **Revenir** (recharge le preset). Une scene retient les noms des presets overlay / fond (`scene["presets"]`) et les reaffiche au chargement (`select`), la comparaison dit alors si l'etat de la scene s'en ecarte. Charger = valeurs validees par `coerce_params`
  puis `sync_widgets()`. Les integres sont partiels et ne touchent pas aux automations sauf si le preset en contient ;
  un preset utilisateur les contient toujours. Les tests utilisent des fichiers temporaires
  (`gl.OVERLAY_PRESETS_PATH` / `gl.BACKGROUND_PRESETS_PATH` remplaces le temps de `run_gui`), jamais ceux de l'utilisateur.
  **Piege (vu en ecrivant ces tests)** : `Gates` grise des groupes de curseurs en reconfigurant leur `state`, et Tk rend
  alors a un `Scale` la valeur perimee de sa variable, qui reecrit `params` par la trace : charger un preset restaurait
  l'ancienne valeur d'un curseur. `Gates._apply` ne reconfigure que si l'etat change et reprend la valeur de la variable,
  et `sync_widgets` part d'un instantane, passe deux fois et le remet dans `params`.
- Trois sauvegardes distinctes : presets live, presets overlay, et `gl_params.json` (touche P / bouton
  "Sauver reglages", reglages courants recharges au lancement).
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

- **Piege (modales)** : `a2w` (palette d'audio2wave) n'est pas un nom du module `gl_gui`, seulement un import local de `build_window` : une
  modale de niveau module (`choice_dialog`, `keep_dialog`) qui l'utilise doit faire `import audio2wave as a2w` elle-meme. `choice_dialog`
  plantait ainsi sans qu'aucun test le voie (ils remplacent les dialogues par `scene_ui`) ; `check_dialogs` ouvre les vraies modales.