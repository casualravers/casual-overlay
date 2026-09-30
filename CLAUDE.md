# CLAUDE.md

Guide pour Claude Code (claude.ai/code) dans ce depot.

## Contexte

casual-overlay affiche le visuel `audio2wave_live` (flux ffmpeg) dans une fenetre OpenGL
plein ecran sur le videoprojecteur, avec un logo PNG incruste et des deformations pilotees
en temps reel par l'audio du micro. Le plan d'origine est dans `PLAN_POC_GL.md`.

**Dependance : le depot voisin `audio2wave`** (dossier `../audio2wave` par defaut, sinon
`--a2w-dir` ou variable `AUDIO2WAVE_DIR`). Son CLAUDE.md fait foi pour tout ce qui touche
au visuel de base (filtres ffmpeg, `--averaging`, latence). Ici on n'en modifie **aucun
fichier** ; on importe `audio2wave_live` (`parse_args`, `producer_command`,
`resolve_size`, `list_audio_devices`).

**Toute la documentation, les commentaires et les messages CLI sont en francais sans
accents** (meme convention qu'audio2wave, compatibilite console Windows).

Contrairement a audio2wave (stdlib seulement), ce depot a des dependances :
`requirements-gl.txt` (moderngl, glfw, numpy, sounddevice, Pillow). **Python 3.12
recommande** : `moderngl`/`glcontext` n'ont pas de wheel pour Python 3.14 et leur
compilation exige MSVC. Environnement de dev : `py -3.12 -m venv .venv` puis
`.venv\Scripts\python.exe -m pip install -r requirements-gl.txt`.

## Commandes

```bash
python audio2wave_gl.py --list-devices                 # entrees dshow (ffmpeg)
python audio2wave_gl.py --list-audio-devices           # entrees sounddevice
python audio2wave_gl.py -d "<entree>" --dry-run        # meme commande ffmpeg que audio2wave_live
python audio2wave_gl.py -d "<entree>"                  # live (plein ecran auto seulement avec un 2e moniteur)
python audio2wave_gl.py --synthetic --no-fullscreen    # sans ffmpeg ni micro
python check_gl.py                                     # verifications hors materiel
```

Options utiles : `--gui` (fenetre de reglages, voir plus bas), `--live-args "--style radio
--gain 20"` (transmis a audio2wave_live),
`--render-size`, `--logo`, `--logo-video anim.webm [--logo-key #00ff00]`, `--text "LIGNE 1\nLIGNE 2"`, `--logo-scale`, `--logo-pos`, `--monitor`, `--hud`, `--stats`,
`--automation/--no-automation`, `--fps-cap`, `--max-seconds` + `--screenshot` (captures de test).

Touches : Echap quitte, F fenetre/plein ecran, H barres de debug, B fond spectre/motif, T automations, 1-5 effets
(wobble/ripple/chroma/glitch/logo; Maj+1-4 = meme effet sur la couche du logo, L = lier/separer fond et logo), +/- ou PageUp/PageDown intensite globale, haut/bas
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

Tout est dans [audio2wave_gl.py](audio2wave_gl.py) ; les GLSL sont dans `gl_shaders/`
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
- **Plein ecran par defaut** (`resolve_fullscreen`) : actif seulement s'il y a un **second
  moniteur** (`has_secondary_monitor`, `GetSystemMetrics(SM_CMONITORS)`, avant d'ouvrir la
  moindre fenetre pour que la case de la GUI soit juste au depart) ou si `--monitor` en
  designe un ; `--fullscreen` / `--no-fullscreen` l'emportent. Avec un seul ecran, l'ancien
  defaut recouvrait le poste de travail en plein ecran sur le moniteur principal, curseur
  masque et GUI cachee dessous (signale en usage reel : "je perds ma souris"; F ou Echap
  rendent la main). `hide_cursor()` : le curseur n'est masque qu'en plein ecran **hors**
  moniteur principal.
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

### Fond genere (`--background pattern`, touche B)

Second mode de fond, a cote du spectre ffmpeg (`bg_mode` = `live` | `pattern`) : le motif
du banc de test (degrades qui defilent + damier qui bascule), calcule **dans `scene.frag`**
(`background_pattern()`), donc sans flux video ni limite de numpy. Palette `classic` = le
visuel d'origine (canaux R/V/B en dents de scie, damier de 80 px a 720 p, valeurs reprises
de `SyntheticVideoStream`) ou `duo` = degrade lisse (aller-retour, sans couture) entre deux
couleurs avec un angle. Reglages (tous dans `params`, cles `bg_*`, sauves avec P) : palette,
couleurs 1/2, angle, teinte (rotation autour de l'axe du gris), vitesse, taille des carreaux
(en px pour 720 px de haut, mise a l'echelle), contraste du damier, cadence de bascule,
reaction a l'audio (flash au kick, damier qui bascule a chaque kick, defilement accelere par
les basses).
- Les phases d'animation (`_bg_scroll`, `_bg_flip_t`) sont **integrees image par image**
  (`dt * vitesse`), pas calculees par `vitesse * temps` : changer la vitesse dans la GUI ne
  fait jamais sauter le motif.
- Le producteur ffmpeg continue de tourner en mode motif (negligeable, et le retour au
  spectre est instantane) ; la GUI n'affiche alors plus son statut.
- Le logo et tous les effets post-traitement s'appliquent aussi au motif.

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
### GUI (`--gui`, [gl_gui.py](gl_gui.py))

**La partie Live n'est pas reecrite : c'est la fenetre de reglages d'`audio2wave_live.py`
(`build_gui`), importee du depot audio2wave comme dependance**, donc avec ses presets, ses
automations de courbes, ses info-bulles, son theme et toute evolution future de ce depot.
`gl_gui.run_gui()` :

1. appelle `live.build_gui(live_args, largeur, hauteur, status, restart_event, stop_event,
   finished_event, root=root)` sur une fenetre Tk creee ici, exactement comme `run_app()` de
   live. Contrat (verifie dans son code, **aucune modification du depot audio2wave**) :
   `build_gui` mute les options `args` en place (un `apply()` par reglage, anti-rebond de
   400 ms, ou `AUTO_RESTART_INTERVAL_S` = 2 s pour les automations), puis positionne
   `restart_event` ; `status["text"]` alimente sa ligne de statut ; `WM_DELETE_WINDOW` ->
   `stop_event` ; son `refresh()` detruit la fenetre quand `finished_event` est positionne ;
   pas de `mainloop()` (il est ici) ;
2. la nettoie (`tidy_live_gui`, parcours de l'arbre de widgets) : boutons Snap/Ridge retires
   (ces modes ne sont pas des sources ici), "Taille fenetre" renommee "Taille du rendu" et
   figee (`readonly`) ;
3. fait le **pont** `restart_event` -> `ProducerManager` (`poll_bridge`, dans le fil tkinter
   toutes les 100 ms, donc apres que `apply()` a fini de muter `args`) : copie des options,
   `size` toujours remise a la taille du rendu (la texture video est fixee au lancement),
   aucun redemarrage si `producer_command()` est identique a celle du producteur courant
   (plein ecran seul, automation qui ne change rien), message du gestionnaire reecrit dans
   leur ligne de statut ;
4. accroche a droite, dans la meme fenetre, **deux colonnes a nous** (chaque curseur automatisable porte sa case `~` et son bouton de courbe dans une 3e colonne, sur la meme ligne : dessous, la fenetre devenait trop haute) : (A) Fond (selecteur
   Spectre audio / Motif genere + tous les reglages du motif) puis Effets ; (B) Logo ou texte (source Image/Texte), reaction
   a l'audio du logo, entree d'analyse sounddevice, boutons (plein ecran, barres debug,
   recharger shaders, sauver), mesures bass/mid/high/beat et statut (fps, logo, messages).
   Les cadres sont poses avec un `rowspan` sur toute la hauteur de leur grille : sur une
   grille commune, un bloc haut dans une ligne etirerait leurs panneaux (piege rencontre :
   fenetre de 1288 px de haut au premier essai). Taille obtenue : 1823 x 911 px.

- **Case "Plein ecran"** de leur fenetre : `args.fullscreen` change -> commande
  `fullscreen=0|1` envoyee au fil GL (`Session.commands`), sans redemarrer ffmpeg. La
  touche F de la fenetre GL ne remet pas la case a jour (sens unique).
- **Presets d'audio2wave** : ils sont partages avec le depot audio2wave (memes fichiers,
  `USER_PRESETS_PATH`) et ne contiennent que les options live + automations. Les reglages de
  casual-overlay (fond motif, logo, effets) restent dans `~/.audio2wave/gl_params.json`
  (touche P / bouton "Sauver") : **deux systemes de sauvegarde distincts**.
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
- Sans `-d`, la GUI demarre quand meme (`NoDeviceManager`) : le fond demarre au premier
  choix d'entree dans leur menu. En `--synthetic`, leur GUI s'affiche mais ses reglages sont
  ignores (message dans leur statut).
- La resolution du rendu ffmpeg (`--render-size`) n'est pas reglable dans la GUI.

### Audio (FeatureExtractor, fonctions pures, testables sans fil ni temps reel)

Fenetre 2048 + Hann, `rfft`, bandes basses 20-150 Hz / mediums 150-2000 / aigus 2000-10000 +
RMS. Auto-gain : maximum glissant a decroissance lente (demi-vie 8 s) avec un **seuil
absolu de silence** (sans lui, l'auto-gain amplifierait le bruit du micro : silence = niveaux
a 0). Lissage attaque 20 ms / relachement 250 ms.

Kick : energie des bins basses > ligne de base glissante x `1 + 1,2/sensibilite` ET ecart a
la base > `3/sensibilite` ecarts-types (statistique hors kicks), montee en cours, energie
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
- **`ProducerManager.args` doit etre une COPIE** des options d'audio2wave_live : leur GUI les mute en place,
  et `push_live` compare `producer_command(nouvelles)` a celle du gestionnaire pour eviter un
  redemarrage inutile. Avec le meme objet, la comparaison etait toujours vraie ("Reglages a jour") et
  plus aucun reglage du fond (style radio, nombre de barres...) n'arrivait jusqu'a ffmpeg. Les tests
  ne le voyaient pas : leur faux gestionnaire copiait deja les options. Verifie de bout en bout avec la
  vraie GUI + vrai ffmpeg + vrai micro (style analyzer -> radio, puis barres -> 64 : 2 redemarrages).
- Le producteur peut mourir en cours de route : message unique sur stderr, derniere image
  conservee (les effets continuent) plutot que fermer la fenetre en soiree.

## Verifications

`python check_gl.py` (aucun peripherique requis) : lecteur de frames avec faux producteur
par blocs de 4 Ko (aucune frame melangee, ordre conserve, partielle jetee, latest-wins) ;
features sur signaux synthetiques (un beat par salve a 48 et 44,1 kHz, periode 500 ms,
-30 dB, bruit blanc, sinus continu, silence, isolation des bandes, ring buffer) ; reglages
(P) ; remplacement du producteur a chaud (`switch_stream` sans frame melangee, ancien flux
conserve si le nouveau meurt, demandes fusionnees, arret propre) avec de faux producteurs ;
GUI : la vraie fenetre d'audio2wave_live integree, pilotee par ses propres widgets (curseur
Gain, radio Style, case Plein ecran) : Snap/Ridge retires, taille figee, presets et
automations presents, rafale de reglages = un seul redemarrage sur une copie, taille du
rendu toujours remise, plein ecran = commande GL sans redemarrage ffmpeg, statut relaye,
et nos panneaux (couleur invalide ignoree, logo, effets, fond) ;
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

Modes Snap/Ridge en source (leurs fenetres existent dans audio2wave, cf. `on_switch_mode` de
`build_gui`), presets nommes pour les reglages de casual-overlay (les automations sont la, mais pas de
"scenes" sauvegardables sous un nom ; les presets d'audio2wave ne couvrent que les options
live), automation des reglages live via le moteur GL (ils gardent celle d'audio2wave, qui
redemarre ffmpeg), MIDI,
sortie Spout/NDI, plusieurs logos a la fois, synchro de la lecture du logo anime sur le kick. Pas de `.bat` de lancement.
