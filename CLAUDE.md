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
(wobble/ripple/chroma/glitch/logo; Maj+1-4 = meme effet sur la couche du logo, L = lier/separer fond et logo), C halo holographique, M fonte acide du logo, V cellules du logo, F1-F9 scenes, Espace VJ lecture/pause, fleche droite scene suivante (avec --gui), +/- ou PageUp/PageDown intensite globale, haut/bas
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

### Scenes (scenes.py, barre en haut de la GUI, F1 a F9)

Une scene change tout le look d'un geste. **Scene = (fond audio2wave OU motif genere) + overlay** (le motif genere REMPLACE le
visuel d'audio2wave, il ne le complete pas) :
- `bg` = `"pattern"` : la scene retient `capture_background` (reglage du motif) ; le mode d'audio2wave n'est pas touche.
- `bg` = `"live"` : la scene retient le **mode** (live / snap / ridge) et **l'etat de son panneau** (`live_overrides`, meme forme
  qu'un preset d'audio2wave, automations comprises) **dans scenes.json : jamais dans les presets d'audio2wave** (une scene et un
  preset sont deux choses differentes ; une premiere version rangeait un preset `scene-<nom>` chez eux, qui apparaissait dans
  leurs menus). Capture (`capture_live_overrides`) : leur champ « Sauvegarder sous » sous un nom temporaire (`TEMP_LIVE_PRESET`,
  Entry + bouton, **aucune modification de leur code**), on relit le resultat dans leur magasin, puis on le retire (fichier et
  ligne du menu) et on remet l'affichage du menu comme avant. Application (`apply_live_overrides`) : pas d'ecriture du tout ;
  le temps d'un clic sur la ligne `default` de leur menu, leur magasin renvoie `overrides` pour `default`
  (`_ObjStore.override_default` : `preset_store.all` remplace sur l'instance pour Live et Ridge, `all_presets` pour Snap ; leur
  fenetre relit le magasin au clic, donc charge cet etat), puis tout est remis. Leur menu affiche alors `default`. Pas d'etat
  audio2wave pour une scene a motif.
- **Exclusions** (`scenes.SCENE_EXCLUDED` = `device`, `fullscreen`, `size`, filtrees par `strip_live_overrides` a la capture, a
  l'application et a la migration, donc aussi pour les scenes deja enregistrees) : une scene change le look, pas
  l'installation. L'**entree audio** reste celle qui est active ; le **plein ecran** (case de leur panneau Live, qui commande
  la fenetre GL par `fullscreen=0|1`, et que le preset `club` de Snap coche) n'est jamais touche par une scene.
- **Fenetre de reglages gardee a sa taille pendant le VJ** : un changement de mode reconstruit la fenetre (panneaux de largeurs
  differentes) et, sans geometrie imposee, Tk la redimensionne a la taille voulue par le contenu (vu : 1496 -> 1490 px).
  `build_window` memorise `keep_size` en tete quand `s.vj_state["running"]` et la rejoue en fin de construction
  (`root.geometry`). Piege de test : un choix **a la main** met le VJ en pause (donc pas de `keep_size`) ; il faut faire passer
  la scene par `vj_advance`, et partir de `root.geometry("")` (une geometrie deja imposee par un test precedent masque le defaut).
- **Migration** (`migrate_scene_presets`, au debut de chaque `build_window`, avant leur panneau pour que leurs menus soient
  propres) : une scene qui porte encore `live_preset` (anciennes versions) recupere ce preset dans le magasin de son mode,
  le range dans `live_overrides` et le supprime de leurs presets. Verifie sur une copie des vrais fichiers de l'utilisateur
  (5 scenes migrees, magasins live / snap propres).
- dans les deux cas : `capture_overlay` (jamais de `bg_*`). `scene_values(scene, current)` fabrique les reglages a poser
  (overlay + soit le motif, soit seulement `bg_mode = live` ; automations `bg_*` du fond, les autres de l'overlay).
- Stockage : `SceneBook` (`~/.audio2wave/scenes.json`, `{"scenes": [...]}`, **ordre = rang = touche F1..F9**, 9 maximum, meme nom
  a la casse pres = remplace au meme rang).
- **Changement de mode** (Live -> Snap...) : la fenetre est reconstruite par `request_mode` ; le preset est garde dans
  `s.pending_live_overrides` et applique a la fin de `build_window` du panneau neuf (`after(150)`).
- **Interface** : rangee ambre posee en ligne 0 de `root` (au-dessus de la page defilante : toujours visible), une touche par
  scene (active = ambre), « + Nouvelle scene » (champ de nom + Enregistrer / Annuler, Entree / Echap), clic droit = mettre a
  jour (capture l'etat actuel) / supprimer (confirmation). `refresh_scene_bar` reconstruit la rangee.
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
  `order` `seq` / `random`) dans le meme `scenes.json` (cle `vj`, `SceneBook.vj()` / `set_vj()`, gardes quand les scenes sont
  ecrites et inversement). `scenes.next_scene_index(count, current, order, rng)` (pure) : `seq` = la suivante en boucle (la
  premiere si aucune n'est active), `random` = une autre, jamais la meme deux fois de suite.
  - `vj_advance` : scene suivante + `s.commands.put("flash")` + minuteur relance ; `vj_toggle` (refuse si < 2 scenes) ;
    **choisir une scene a la main** (`apply_scene`, F1..F9 compris) **met le VJ en pause** (`apply_scene_core` est la version
    que le VJ appelle). Espace / fleche droite : le fil GL met `"vj"` / `"vjnext"` dans `s.scene_requests`, la GUI les lit.
  - **Flash** : `Renderer.flash_t` (instant monotonic), pose par la commande `"flash"` dans le fil GL ; `u_flash` = carre de
    `1 - age / 0,55 s` dans `post.frag` : melange vers le blanc (0,8) + force des bandes de glitch (0,9) meme avec les effets
    coupes. Ne passe pas par `params` (`save_params` ecrit tout `params` dans `gl_params.json`).
  - Un changement de mode (Live -> Snap) reconstruit la fenetre : ~1 s, en partie masque par le flash.
- Limites / suite : les medias (logo, texte, video) ne sont pas dans les scenes ; pas de changement en mesures (kicks), de
  duree par scene ni de vraie transition (voir README, ameliorations futures : bouton « etendre » du VJ pour la granularite,
  glisser-deposer de medias lies a l'overlay de la scene ou ajoutes aux parametres d'audio2wave en mode Snap).
  Pistes aussi notees : des **sets** (enchainement de scenes enregistre sous un nom, exportable / importable, avec un moyen de
  basculer d'un set a un autre) ; `scenes.json` deviendrait un conteneur de sets (migration de l'existant en un set principal).
- `check_gl.py` (`check_scenes` + etape GUI) : stockage (ordre, 10e refusee, remplacement, fichier illisible), capture et
  valeurs (motif / audio2wave, automations, micro intact), barre, F1/F2/F8, nom deja pris, mise a jour au meme rang, VJ (ordre, reglages persistants, minuteur reel, flash, Espace / fleche droite, pause a la main),
  suppression du preset, **changement de mode Live -> Snap avec chargement du preset `club`**.

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
2. la nettoie (`tidy_mode_gui`, parcours de l'arbre de widgets) : titre retire, sections en bandeaux,
   "Taille fenetre" renommee "Taille du rendu" et figee (`readonly`). Les boutons de bascule
   Live / Snap / Ridge sont **gardes** : voir "Modes Snap et Ridge" plus bas ;
3. fait le **pont** `restart_event` -> `ProducerManager` (`poll_bridge`, dans le fil tkinter
   toutes les 100 ms, donc apres que `apply()` a fini de muter `args`) : copie des options,
   `size` toujours remise a la taille du rendu (la texture video est fixee au lancement),
   aucun redemarrage si `producer_command()` est identique a celle du producteur courant
   (plein ecran seul, automation qui ne change rien), message du gestionnaire reecrit dans
   leur ligne de statut ;
4. accroche a droite, dans la meme fenetre, la partie OVERLAY : les **presets overlay** toujours visibles en haut
   (`preset_bar`), puis un `ttk.Notebook` en **onglets** (chaque curseur automatisable porte sa case `~` et son
   bouton de courbe dans une 3e colonne, sur la meme ligne) : *Fond* (**dans la partie LIVE**, sous le panneau d'audio2wave, dans le meme canvas defilant ; selecteur Audio2wave / Motif genere +
   reglages du motif), *Effets* (les 5 effets, intensite globale, sensibilite, effets propres au logo), *Logo*
   (source Image/Texte/Video, position, opacite), *Aura du logo* (halo holographique + reaction a l'audio :
   pulsation, tremblement, contour), *Fonte du logo* (fonte acide), *Cellules*, *Noise* (hasard sur les effets coches), *Affichage* (entree d'analyse sounddevice, boutons plein ecran / barres
   debug / recharger shaders / sauver, automations, mesures bass/mid/high/beat et statut fps/logo/messages).
   **Hauteur** : la fenetre depassait la hauteur d'un ecran (deux colonnes empilees : 850 px au repos, plus de
   1100 px avec le motif ouvert + les effets du logo delies + le halo deplie). Les onglets l'ont ramenee a
   **648 px au repos et 653 px dans le pire cas** (1300-1380 px de large) ; elle est maintenant dictee par le
   panneau LIVE d'audio2wave (763 px a l'origine), dont `build_window` **resserre les marges verticales** (x0,35 :
   -115 px) sans toucher a son code. Le panneau d'audio2wave est dans un **canvas defilant** plafonne a 600 px
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
   La molette defile d'abord le panneau d'audio2wave s'il est sous la souris, sinon la page.
   Pieges : la taille **voulue** du canvas de page suit celle du contenu (sinon `root.winfo_reqheight()` ne bouge plus et la
   fenetre ne s'ouvre pas a la bonne taille) ; changer de mise en page change les tailles voulues, d'ou un deuxieme
   passage de `on_page_config` (`after_idle`), sans quoi la page garde la hauteur de l'etat precedent (vu : 1250 px de
   haut en revenant en large).
   Les curseurs de casual-overlay (`compact_scale`) affichent leur valeur a COTE et non au-dessus (-22 px par ligne).
- **Structure visible** : deux grandes parties, LIVE (bandeau turquoise, fenetre d'audio2wave) et OVERLAY
  (bandeau violet), separees par un trait violet de 3 px ; le titre d'audio2wave est retire de la ligne 0
  pour y poser les bandeaux. Toutes les sections sont des **bandeaux** (`style_band`, fond `GUI_PANEL_BG`, texte
  gras colore) : ceux d'audio2wave (SOURCE, SPECTRE, COULEURS, SORTIE, PRESETS -> "PRESETS LIVE") sont
  restyles par `tidy_mode_gui`, les notres par `add_section_title`. Le bloc "Motif genere" doit etre pose
  avec `sticky="new"` + `columnconfigure(2, weight=1)` pour que son bandeau prenne toute la largeur.
  Le halo holographique n'est plus replie (il a son onglet).

- **Modes Snap et Ridge** ([py_modes.py](py_modes.py), `activate_mode` / `build_window` dans gl_gui.py) : les boutons
  Live / Snap / Ridge d'audio2wave changent la **source du fond**, sans nouvelle fenetre et sans modifier
  audio2wave (leurs `build_gui` appellent `on_switch_mode`, qu'on branche). Snap et Ridge dessinent leurs images
  en Python et les ecrivent dans l'entree d'un `ffplay` (`viewer.stdin.write`, rgb24, meme taille que la video
  live) : on lance leur `run()` **tel quel dans un fil de notre processus** et on remplace seulement ce ffplay :
  le module `subprocess` de snap/ridge est remplace par une enveloppe (`_SubprocessShim`) qui rend un
  `FakeViewer` pour `Popen(["ffplay", ...])` ; chaque image va dans un `PushStream` (derniere gagne, aucune
  image melangee) lu par un `FrameReader` normal. `PyModeSource` = un mode en marche (capture micro ffmpeg
  propre au mode, comme leur `run_app`), `make_args` = options a la taille du rendu et sans plein ecran.
  - Le fil GL relit `s.reader` a chaque image : changer de mode = changer `s.reader` (flux Live du
    `ProducerManager`, ou flux du `PyModeSource`). **Live est suspendu** pendant Snap/Ridge
    (`ProducerManager.suspend()` arrete ffmpeg, `resume(args)` le relance en doux au retour) ; l'arret d'un mode
    Python ferme son flux (le `run()` s'arrete a sa prochaine ecriture) puis se termine en arriere-plan.
  - **Toute la fenetre est reconstruite a chaque bascule** (`build_window(s, live, root, mode)`, appelee par
    `request_mode`) : l'etat vit dans `s.params` (reglages overlay, `_automation` copie avant la
    reconstruction), les widgets le relisent a leur creation ; les `root.after` de la fenetre passent par
    `after()` qui s'arrete tout seul (`alive`). Les options de chaque mode sont gardees (`s.mode_args`) : revenir
    a Snap retrouve ses reglages. L'entree audio suit d'un mode a l'autre.
  - Le panneau du mode est dans un `HostFrame` (un `tk.Frame` qui joue le role de `root` : `title`,
    `resizable`, `protocol` sans effet) pose dans un canvas ; on ne surcharge **pas** `destroy()` : le parent le
    detruit en cascade et fermerait la vraie fenetre. Leur `refresh()` ferme la fenetre quand `finished_event`
    est positionne : on leur donne un evenement jamais positionne pour Snap/Ridge, et c'est notre `refresh()`
    qui ferme la fenetre quand `s.finished_event` l'est.
  - **Piege (fond noir en mode Snap)** : `run()` remplace son visionneur quand `fullscreen` ou la taille change
    (charger le preset `club`, qui coche Plein ecran) et ferme l'ancien (`stdin.close()`). Si fermer un
    `FakeViewer` fermait le flux, plus aucune image n'arrivait. Un visionneur ferme donc seulement lui-meme ;
    seul `PyModeSource.stop()` ferme le flux. Les images de taille differente de celle du rendu sont ignorees,
    et le champ taille et la case Plein ecran de Snap/Ridge sont figes dans la GUI.
  - Presets : chaque mode a ses presets (`live_presets.json`, `snap_presets.json`, `ridge_presets.json` dans
    `~/.audio2wave/`, memes fichiers que dans audio2wave), section "PRESETS LIVE / SNAP / RIDGE". Ridge exige
    une entree audio (refus avec message sinon) ; Snap sans entree attend qu'on en choisisse une.
  - Limites : les rendus Snap/Ridge sont en Python pur (CPU, une image au rythme du trace progressif) et leur
    micro est ouvert une fois de plus (ffmpeg dshow, en plus de sounddevice et du producteur live suspendu) ;
    la case "Plein ecran" de Snap/Ridge n'existe pas (c'est notre fenetre GL qui gere l'affichage) ; le Mode VJ
    d'audio2wave (enchainement de presets) n'est pas verifie ici.
- **Reglages incompatibles grises** ([gui_gates.py](gui_gates.py)) : un reglage sans objet dans le mode ou l'etat
  courant est **grise** (jamais retire : la disposition ne bouge pas et on voit qu'il existe). Pour les panneaux
  d'audio2wave (aucune modification de leur code) : `install_mode_gates` retrouve les widgets par leur texte et
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
- **Aides a la saisie des couleurs** ([gui_colors.py](gui_colors.py)) : les champs texte de couleur d'audio2wave
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

Rendu GL natif de Snap/Ridge (aujourd'hui Python pur), presets combines live + overlay (les deux se chargent separement), automation des reglages live via le moteur GL (ils gardent celle d'audio2wave, qui
redemarre ffmpeg), MIDI,
sortie Spout/NDI, plusieurs logos a la fois, synchro de la lecture du logo anime sur le kick.

Lanceur : [start.bat](start.bat) (demande explicite, malgre le "pas de .bat" du plan d'origine) : va dans
son dossier, utilise `.venv\Scripts\python.exe` (message d'installation s'il manque), lance
`audio2wave_gl.py --gui %*` (arguments transmis, donc `-d`, `--text`, `--synthetic`... marchent) et garde la
console ouverte (`pause`) si le programme sort avec un code d'erreur. Fichier en ASCII + CRLF (accents
interdits, comme le reste). Une sortie anormale isolee (code -1073740022) a ete vue une fois au tout premier
lancement, non reproduite sur 17 lancements suivants.
