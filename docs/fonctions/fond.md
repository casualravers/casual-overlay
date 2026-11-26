# Fond (video, genere, labyrinthe)

Fiche de [CLAUDE.md](../../CLAUDE.md) (issue #132 : detail deplace tel quel). Fichiers : engine/video.py (BgVideo, BgVideoManager), engine/reaction.py, core/reaction.py, core/playback.py, gl_shaders/scene.frag (background_pattern, stochastic_pattern, labyrinth_pattern), gl_shaders/rd_step.frag, gl_shaders/rd_zone.frag, tools/rd_sweep.py.

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

**Reprise des medias** (`core/playback.py`, pur, horloge injectee ; `PLAYBACK`, registre unique) : un media (video du logo, video de fond, videos interieure / exterieure de Snap) qui disparait quand la scene change (VJ ou a la main) **repart la ou il s'etait arrete**
au retour, au lieu du debut. `LogoVideo.__init__` (donc aussi `BgVideo`, sous-classe) appelle `PLAYBACK.start(chemin, duree ffprobe)` et passe la position a `logo_video_command` / `bg_video_command` (`start`, `-ss` en entree **apres** `-stream_loop` : seule la premiere lecture
est decalee, les boucles suivantes repartent de 0) ; `LogoVideo.stop` appelle `PLAYBACK.stop(chemin)`. Position = (position au lancement + temps ecoule) ramenee dans la duree du fichier (il boucle) ; le temps passe scene absente ne compte pas (le media est « en pause ») ; moins de 0,5 s ou
moins de 0,5 s de la fin = du debut ; oublie depuis 1 h (`EXPIRE_S`) = du debut ; 64 fichiers retenus au plus ; duree inconnue (ffprobe absent, image animee) = position brute. Cle = chemin : deux decodeurs du meme fichier en meme temps se partagent l'entree (cas rare). Pas de reglage ni de voyant
(rien n'est decide automatiquement : le media reprend simplement). Limite : `-ss` tombe sur l'image cle precedente (quelques dixiemes de seconde d'ecart sur un mp4 long), et le temps de lancement de ffmpeg (~0,3 s) n'est pas compte. `check_gl.py` : `check_playback` (pure, dans `PURES`).

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
- **Palette `stochastique`** (halftone stochastique ; spec et plan : `docs/plans/2026-10-27-fond-stochastique-*.md`) : `stochastic_pattern()` de
  `scene.frag`, `u_bg_palette` = 3. **Grille jitteree** : une cellule de `bg_sto_size` px (pour 720 px de haut) porte un point, allume si le champ
  (evalue au centre du point) depasse son **rang** ; rang = suite R2 (`fract(c.x * 0,7549 + c.y * 0,5698)`, faible discrepance : points sans amas,
  esprit bruit bleu) + `(1 - bg_sto_regular) x hachage blanc`, modulo 1 (0 = grain aleatoire brut). **Le rang reste uniforme** quelle que soit la
  regularite (un `mix` des deux donnait une loi triangulaire : densite fausse entre 0 et 1, et qui pulsait avec les aigus). Decalage du point dans sa
  cellule 0,3 a 0,5 ; 3 x 3 cellules voisines par pixel, **rayon plafonne** a ce que ce voisinage montre (`(1,5 - decalage) x s - 1`, sinon les gros
  points etaient coupes a plat), et **une voisine hors de portee du pixel ne calcule pas le champ** (-40 % de temps GPU, image identique).
  Champs (`bg_sto_field`) : `noise` (2 octaves de bruit de valeur, hachage sans `sin`), `waves` (bandes vers `bg_angle`), `gradient` (aller-retour
  comme Duo) ; ils derivent avec `_bg_scroll`. Reglages propres : `bg_sto_vary` (le rayon suit le champ), `bg_sto_density` (decale le champ),
  `bg_sto_invert` (echange couleur 1 = points et couleur 2 = fond), `bg_sto_rainbow` (teinte des points = champ), `bg_sto_soft` (« Douceur », #223 :
  rayon x `smoothstep(rang - l, rang + l, champ)`, `l` = 0,15 x douceur, uniform `u_sto_soft` ; les points naissent et meurent en grossissant au lieu
  de s'allumer d'un coup, plus de gresillement aux bords des zones quand le champ derive ; 0 = image d'origine ; cout mesure +0,1 ms en 1280 x 720),
  `bg_sto_border` / `bg_sto_border_color` (« Taille de bordure » 0..12 px pour 720 px de haut et « Couleur de bordure », #219 : uniform `u_sto_border`,
  anneau trace DANS le point retenu, `smoothstep(rayon - bordure +- 0,5 px)`, le point garde sa taille et la grille ne change pas ; bordure >= rayon =
  disque plein de la couleur de bordure ; 0 = image d'origine ; le Randomizer la tire une fois sur deux, 1 a 4 px, blanc / noir / couleur 2),
  `bg_sto_levels` (« Paliers de taille », #222 : `continuous` / `2` / `3`, uniform `u_sto_levels` ; en paliers le rayon vaut
  `0,5 x s x (palier + 1) / n` avec `palier = min(floor(champ x n), n - 1)` : taille fixe selon le champ, facon trame d'imprimerie ; remplace
  la variation de taille et la croissance aux basses, mais les basses haussent le champ donc des points passent au palier suivant ; se combine avec
  douceur et bordure ; `continuous` = image d'origine ; le Randomizer tire continu une fois sur deux, sinon 2 ou 3),
  **texture de la tache** (`bg_sto_tex` = `none` / `wave` / `tiling`, #220 ; spec : [docs/plans/2026-11-26-stochastique-texture-220-design.md](../plans/2026-11-26-stochastique-texture-220-design.md) ; la tache = l'union
  des points, la texture est un masque `sto_texture(p)` en coordonnees ecran, continu a travers tous les points, qui eclaircit la couleur du point
  (assombrie si elle est deja claire : `bg_sto_tex_contrast`), la bordure restant par-dessus ; `none` = image d'origine). **Onde** : lignes horizontales
  d'espacement 12 px x `bg_sto_tex_scale` (pour 720 px de haut), cisaillees par un sinus continu (jamais de recouvrement) ; `core.stotex.wave_params` : basses =
  amplitude, aigus = frequence, mediums = vitesse de la phase (integree : `_sto_phase`), kick = epaisseur, `bg_react` = 0 : lignes droites. **Pavage** : la tuile
  de Gray-Scott du remplissage du texte (`Renderer.text_tile`, **partagee**, meme cache disque ; tant qu'elle n'est pas prete : aplat) lue deux fois, vague
  courante et precedente (decalages `core.stotex.epoch_offset`), front le long de `bg_angle` (`sweep_tau`, `front_times`) : derriere le front le motif pousse
  (formule de `textfx.fill_reveal`), devant lui l'ancien reste puis est efface. `core.stotex.SweepClock` : une vague toutes les `bg_sto_tex_every` s, ou toutes
  les `bg_sto_tex_bars` mesures sur le temps 1 (`DownbeatGate`, `since_down`) avec `bg_sto_tex_sync`, repli en secondes sans temps 1 connu et garde-fou en
  silence (`max(4 x mesures, 8)` s). Unites de texture : la tuile (ou une texture vide) sur l'**unite 5** de la passe de scene. Cout : un masque par pixel
  couvert (onde : un `sin` ; pavage : deux lectures de tuile) ; le Randomizer tire aucune une fois sur deux) ; `bg_hue` et `bg_speed`
  s'appliquent, pas le damier (grise par `gui_gates`). **Son** (`u_sto_au`, niveaux du fond `bg_au` x `bg_react`) : basses = densite + taille,
  kick = coup de contraste du champ + quart d'onde des vagues par kick, aigus = scintillement (`Renderer._sto_flick`, phase integree : chaque point
  change de rang a son tour, `fract(rang + epoque x 0,618)`, donc aussi en bruit bleu). Reglages de la GUI dans `sto_box` (bloc « Fond genere »,
  visible seulement pour cette palette). Le Full Randomizer la tire une fois sur quatre. **Cout mesure** (Radeon integre, fenetre 1280 x 720 ; la
  scene est rendue a la taille de la fenetre, pas a `--render-size`) : Duo 1,75 ms, stochastique bruit 4,0 ms, vagues 3,1 ms, donc ~+5 ms (bruit)
  et ~+3 ms (vagues) en 1080p plein ecran ; **compte par la securite GPU du VJ** (`core.safety.background_cost`, `BG_STO_COST_MS` : le fond n'est
  jamais coupe, mais les effets de la scene suivante sautent a temps, et un fond deja affiche n'est pas compte deux fois). Limite : approximation du
  bruit bleu (plan B : texture void-and-cluster). Ce n'est PAS un motif de reaction-diffusion (taches qui ne se touchent jamais et se soudent en
  lignes, type motif de Turing) : les points sont independants et se touchent quand la densite monte ; la reaction-diffusion est une autre palette.
  `check_gl.py` : `check_bg_stochastic` (pixels : densite, regularite mesuree par blocs, variation, inversion, arc-en-ciel, teinte, champs,
  resolution, derive, son, reaction 0) ; schema, scene rejouee, GUI et Randomizer dans leurs verifications.
- **Palette `labyrinthe`** (reaction-diffusion, motif de Turing ; spec et plan : `docs/plans/2026-10-27-fond-labyrinthe-*.md`) : taches qui se
  repoussent (jamais collees) et se soudent en lignes sinueuses, en trois couches colorees decalees (le look du pull montre par l'utilisateur).
  **Simulation Gray-Scott sur le GPU** : `engine/reaction.py` (`ReactionDiffusion`, possedee par le `Renderer`, creee au premier besoin) +
  `gl_shaders/rd_step.frag` (laplacien 3 x 3, Du 1, Dv 0,5, dt 1) ; logique pure dans `core/reaction.py` (`feed_kill`, `grid_size`, germes,
  `steps_for`). Etat **RG32F** (repli RG16F) en ping-pong sur une **grille fixe** de 384 x 216 (/ `bg_rd_scale`), bords en boucle, independante de la
  fenetre ; changer d'echelle recopie l'etat (bilineaire, numpy). **Piege du demi-flottant** : en RG16F un increment `F x (1 - U)` plus petit que le
  demi-ulp pres de 1 est perdu, et le motif se figeait en un point fixe d'arrondi apres ~15 s (vu en revue, test « se reorganise encore apres
  10000 pas ») ; il faussait aussi le reglage des taches. `bg_rd_shape` interpole (F, K) de **taches (0,0367 ; 0,0649)** a **labyrinthe
  (0,0290 ; 0,0570)** (valeurs classiques ; en RG32F : 238 taches regulieres, aucun contact, motif toujours actif). Etat initial : 40 germes (graine fixe), refait a chaque activation de la palette et a chaque nouveau
  Renderer (ouverture de la projection). Garde-fou : toutes les 2 s, moyenne de V (mipmaps 1 x 1) ; motif eteint = ressemee. Pas par image :
  600 / s x `bg_speed` x (1 + basses), plafond 24 (exces jete), `dt` deja multiplie par le tempo (tempo 0 = fige). Rendu (`scene.frag`,
  `labyrinth_pattern`, palette 4, texture unite 6) : V lu trois fois a 120 degres d'ecart (`bg_rd_offset`), seuil 0,25 anti-crenele, couches
  `bg_rd_color1/2/3` sur `bg_rd_ground`, puis `bg_hue`. **Son** (x `bg_react`) : basses = forme + 0,4 (vers les lignes) et vitesse, chaque kick seme
  3 germes (`kick_seeds`), l'enveloppe du kick ouvre l'ecart (x 2,5). Un kick tombe pendant une division : deux blobs colles un instant (normal).
  **Cout** : +0,6 ms a 1280 x 720 ; `core.safety.BG_RD_COST_MS` = 1,0. Bloc `rd_box` dans « Fond genere ». Le Full Randomizer la tire une fois sur
  cinq. `check_gl.py` : `check_reaction_pure` (pur), `check_reaction_sim` (vivant a trois formes, taches separees et regulieres, lignes, germes,
  echelle, garde-fou), `check_bg_labyrinthe` (pixels : couches, teinte, taille de fenetre, tempo, son, reaction 0, changement de palette).
  **Zones, symetries, epaisseur** (issue #102 ; spec et plan : `docs/plans/2026-11-01-labyrinthe-evolution-*.md`) : axe interne de forme **0..2**
  (`core.reaction.shape_feed_kill` : 0 points `SPOTS`, 1 lignes `MAZE`, 2 taches `BLOB` = (0,044 ; 0,060), trouve par `tools/rd_sweep.py` : grosse
  masse pleine percee de trous, ~50 % de l'image ; la bande utile est etroite, au-dessus de F 0,04 un K de 0,058 ou moins envahit tout). **Carte de
  forme** (`bg_rd_mix` variete, `bg_rd_drift` derive) : `gl_shaders/rd_zone.frag` calcule (F, K) par cellule **une fois par image** (texture RG32F,
  unite 1, lue par `rd_step.frag` ; recalculee a chaque pas elle coutait jusqu'a +11 ms en rotation sur Intel UHD 605) : bruit de valeur periodique
  a 2 octaves (`ZONE_CELLS` 5 x 3), temps integre `Renderer._rd_zone_t` (`ZONE_SPEED` x drift x dt du tempo, seulement quand des pas sont faits) ;
  une ligne qui entre dans une zone > 1 s'agglomere. **Symetrie de colonisation** (`bg_rd_sym` none / mirror_lr / mirror_tb / quad / rot,
  `bg_rd_branches`, `bg_rd_free` Liberte) : germes en orbite (`core.reaction.colonize`, ~40 au total via `base_seed_count`, decalage et rayon
  x Liberte, deterministes), zones lues aux coordonnees repliees (rotation : repli en miroir dans le secteur, pas de couture) + bruit non replie
  x Liberte, et un faible bruit de V (`FREE_NOISE`, sans effet mesurable : ce sont les germes et les zones qui liberent le motif). **Mesure** : la
  REPARTITION (part de V > 0,25 par bloc) reste en miroir (0,89 a Liberte 0,1 apres 10 s, 0,19 sans symetrie), les pixels se liberent en ~10 s ;
  Liberte 0 = miroir parfait qui ne se defait jamais (calcul deterministe). **Un motif deja installe ne prend pas la symetrie** (elle ne vit que dans les germes) : changer `bg_rd_sym` (ou `bg_rd_branches` en rotation) relance donc le motif des germes (`Renderer._rd_sig` / `_rd_want`, apres `RESEED_DELAY_S` 0,4 s de stabilite pour qu'un curseur en mouvement ne relance pas a chaque cran) ; variete, liberte, forme, derive et epaisseur (automatisables) ne relancent jamais. Germes en file (`queue_seeds`, 32 au plus, 4 par pas ; 4 sans symetrie
  comme avant). **Epaisseur des couches** (`bg_rd_layers`, `LAYER_SPREAD` 0,05) : seuils 0,20 / 0,25 / 0,30, couche 1 (dessous) epaisse, couche 3
  (dessus) fine : liseres emboites visibles meme sans ecart (V plafonne vers 0,35 : un seuil plus haut fait disparaitre la couche du dessus).
  **Zones vides et denses** (`bg_rd_void`, curseur « Vide », 0 par defaut ; contre la monotonie d'un motif qui remplit toujours tout l'ecran) : un
  second bruit a grande echelle (`VOID_CELLS` 3 x 2, autre graine, derive a `VOID_SPEED` x celle des formes), dans `rd_zone.frag` ; au-dessus du seuil
  `void_threshold(void)` (0,92 -> 0,6 : ~0 % a ~53 % de vide, mesure sur 12 cycles de derive) (F, K) glisse vers `VOID_FK` (0,037 ; 0,071), au-dela de la
  limite d'existence du motif : la matiere s'eteint, et le bord s'amincit en points epars (`VOID_EDGE`) ; sous `DENSE_THR` 0,5 (~13 % de l'image) la
  forme est tiree vers les taches (`DENSE_PULL`, x Vide : nid d'abeille, plus de matiere). Les vides suivent la symetrie (bruit replie, Liberte). Une
  zone qui se libere est recolonisee par les bords et les germes des kicks (ceux tombes dans un vide meurent). `u_zoned` (rd_step.frag) = carte lue
  si mix > 0 ou void > 0 ; a 0 partout : bit pour bit l'ancien comportement. **Valeurs neutres** (mix 0, layers 0, sym none, void 0) = simulation et
  image identiques bit pour bit a la version d'avant. Automations actives par defaut : forme, ecart, variete, epaisseur, liberte, vide (0,15 a 0,6,
  periode 107 s ; pas derive ni symetrie). Securite GPU : `BG_RD_ZONE_COST_MS` 1,5 ms si mix > 0 ou void > 0.
  **Apparitions** (issue #187 ; design et plan : `docs/plans/2026-11-26-labyrinthe-apparitions-187-*.md`) : en cours de route les nouvelles
  taches ne viennent QUE des germes des kicks. `bg_rd_spawn` (curseur « Apparitions », 1 par defaut) = part des kicks qui sement
  (`core.reaction.kick_spawns`, tirage deterministe par kick ; 0 = le motif grandit seul depuis ses bords). `bg_rd_bud` (case « Seulement contre
  le motif », 0 par defaut) = bourgeons : `rd_step.frag` (`u_bud`, `touches_matter`) ne pose un germe que si l'une de 8 lectures de V sur un cercle
  de 2 x rayon + 2 cellules depasse `BUD_LEVEL` 0,1 (grille en boucle comprise) ; `BUD_SEEDS` 4 candidats par kick au lieu de 3 (une partie tombe
  dans le vide et est jetee). Germes initiaux, relance de symetrie et garde-fou (`watch`, jamais d'ecran vide) : inchanges. Les taches qui se
  divisent ne sont pas des germes (croissance du motif). Defauts = simulation et image identiques. Full Randomizer : apparitions 0,4 a 1, bourgeons
  une fois sur trois (tires apres les autres reglages). **Non verifie** : ressenti au videoprojecteur avec de la vraie musique.
  **Taille du motif** (`bg_rd_scale`, 0,3 a 2) : automatisable (courbe 0,6 a 1,6, periode 127 s, **coupee par defaut** : chaque changement de
  taille floute un peu le motif). `ReactionDiffusion.ensure(scale, now)` recopie l'etat sur le GPU (passe de recopie bilineaire, ~0,1 ms ; l'ancien
  numpy coutait ~12 ms) et, avec `now`, ne change la taille de la grille qu'une fois par `RESIZE_MIN_S` (0,4 s). Echelle 0,3 = grille de 1280 x 720 :
  les pas par image sont plafonnes par cellules x pas (`MAX_CELL_STEPS` 5 millions, mesure a 0,85 ns par cellule et par pas : ~4 ms), donc ~5 pas par
  image a 0,3 (le motif y evolue deux fois moins vite), 15 a 0,5, 24 a 1. `core.reaction.step_load` entre dans l'estimation de la securite GPU.
  **Demi-precision sans symetrie (#225)** : quand `bg_rd_sym` = `none`, l'etat est en **RG16F** (`ReactionDiffusion.half`, `reset(half=)`, reallocation
  au changement de symetrie) : il y stocke **W = 1 - U** (shader `u_w` : `W' = W + lap(W) + U V^2 - F W`, le chemin RG32F reste identique bit pour bit,
  verifie par empreinte) et le shader arrondit **stochastiquement** (bruit de +-1/2 ulp avant l'ecriture : V varie de ~1e-4 par pas, sous l'ulp 2,4e-4,
  sans cela le motif se figeait). La Liberte (bruit de V) exige 32 bits : avec une symetrie l'etat reste en RG32F. Plafond `MAX_CELL_STEPS_HALF`
  (9,5 millions : 10 pas a 0,3, 24 a 0,5) et `HALF_STEP_COST` (0,55) dans `step_load` / securite GPU. Carte (F, K) toujours en RG32F. Mesure (AMD) :
  echelle 0,3 +5,7 -> +5,0 ms avec 2 fois plus de pas ; echelle 0,5 +3,9 -> +2,5 ms. Le motif n'est plus identique au pixel pres (meme nature).
  `check_gl.py` : `check_reaction_pure` (chemin, orbites, file), `check_reaction_sim` (regime taches, chemin vivant), `check_rd_zones`,
  `check_rd_symmetry`, `check_bg_labyrinthe` (tempo 0, relance a la symetrie, rafale, epaisseur).
  **Fluidite** (issue #189 ; spec : `docs/plans/2026-11-19-labyrinthe-fluide-189.md`) : le motif « claquait » a la naissance d'une tache, a la
  soudure de deux points et a la rupture d'une ligne (une image = ~10 pas : le pont franchit le seuil 0,25 +/- 0,02 d'un coup ; un germe etait un
  disque plein de V = 1 pose en un pas). Deux corrections : (1) **affichage lisse** : `ReactionDiffusion.smooth(alpha)` garde une copie affichee
  (ping-pong, meme format que l'etat, une passe a la taille de la grille, cout negligeable) qui rattrape l'etat (`mix(affiche, etat, alpha)`,
  `core.reaction.display_alpha(dt reel, bg_rd_smooth)` : tau = 0,25 s x fluidite, independant de la cadence, `dt` REEL et non multiplie par le
  tempo : un motif fige est rejoint) ; `scene.frag` lit `display` (texture unite 6). La simulation n'est pas touchee. Grille neuve ou redimensionnee :
  l'affichage repart de l'etat ; reactivation de la palette : recopie (alpha 1) ; relance (garde-fou, symetrie) : fondu. (2) **germes doux** :
  profil gaussien de hauteur `SEED_PEAK` 0,4 (moitie au rayon, rien au-dela de 2 rayons, `seed_value`, meme formule dans `rd_step.frag`), la
  simulation fait grandir la tache elle-meme ; l'etat initial (`reset`) garde ses disques pleins. **Mesure** (`check_rd_fluide`, kick toutes les
  20 images, aire qui bascule d'un coup dans une fenetre 9 x 9 cellules) : 80 cellules sur 81 avant, ~60 avec les germes doux seuls, ~17 avec la
  fluidite 0,5 par defaut (6 a 10 a la naissance) ; sans kick l'evolution ne fait basculer que ~7 cellules par image (pas de vrai saut). Curseur
  « Fluidite » (`bg_rd_smooth`, 0,5 ; 0 = affichage brut). Bord : jamais plus fin que `0,7 x fwidth(V)` (grille fine sur grand ecran).
- Les phases d'animation (`_bg_scroll`, `_bg_flip_t`) sont **integrees image par image**
  (`dt * vitesse`), pas calculees par `vitesse * temps` : changer la vitesse dans la GUI ne
  fait jamais sauter le motif.
- Le producteur ffmpeg continue de tourner en mode motif (negligeable, et le retour au
  spectre est instantane) ; la GUI n'affiche alors plus son statut.
- Le logo et tous les effets post-traitement s'appliquent aussi au motif.

- **Analyzer : reglages qu'on contournait, corriges a la source** (signales par casual-overlay, repris par audio2wave ; `check_bar_gap` et
  `check_line_resolution` verifient le nouveau comportement, vrai filtre ffmpeg a l'appui) : (1) `bar_gap` >= 1 faisait un separateur noir plus
  large que la barre = ecran noir (preset `devil` a 1,4) : plafonne a `MAX_BAR_GAP` = 0,7 dans `build_filter` ET dans la plage du curseur ;
  (2) analyzer en courbe (`--shape line`) = escalier flou avec 128 points / fenetre de 512 : 400 points (`LINE_ANALYZER_BARS`) et fenetre de 2048
  (`LINE_WIN_SIZE`) par defaut. Overlay n'y touche plus (`limit_bar_gap` et `sharpen_line` supprimes).
