# Logo (texte, video, halo, fonte, cellules, mosaique, effets par couche, noise, automations)

Fiche de [CLAUDE.md](../../CLAUDE.md) (issue #132 : detail deplace tel quel). Fichiers : engine/render.py (render_text, load_logo, logo_layout, Renderer), engine/video.py (LogoVideo), core/textcycle.py, core/schedule.py, core/automation.py, gl_shaders/scene.frag (logo_at), gl_shaders/holo.frag, gl_shaders/post.frag (layer_fx, holo).

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
- **Textes en rotation** (`core/textcycle.py`, pur, temps et kicks injectes ; reglages `logo_texts` (un texte par ligne, `\\n` = retour a la ligne) et `text_bars` (1 a 16 mesures, 4 par defaut), portee `look` : presets overlay et scenes ; etats `text_idx` et `text_fade`, portee `state`) : sous le texte principal, la zone « Textes suivants » ; la boucle est le principal puis chaque ligne. `TextCycle.update(now, beats, count, bars, tempo, bpm)` rend (indice, fondu) : `BarTimer` compte N mesures de 4 kicks (repli au temps sans kick, au moins 8 s), puis le logo s'efface en 0,2 s, le texte change, il revient en 0,2 s. Tout tourne dans la boucle GL (`engine/projection.py`, avant `apply_logo_request`) : fonctionne sans GUI ; `logo_signature` prend `text_at(principal, logo_texts, text_idx)` donc la texture n'est regeneree qu'a chaque changement ; `text_fade` multiplie la presence de l'overlay dans `Renderer.draw`. Le champ du texte principal n'est jamais modifie par la rotation ; hors source Texte la rotation est suspendue (fondu 1). Le planning continue de poser le texte principal. **Full Randomizer** : le champ « texte » accepte plusieurs textes separes par `|` (`randomizer.texts_list`), un tirage en choisit un (memoire de recence comme les fichiers de la banque). `check_gl.py` : `check_textcycle` (pur, dans `PURES`).
- **Planning du texte** (`core/schedule.py`, pur, heure injectee ; reglages `sched_on` / `sched_lines`, portee `device` : sauves, jamais dans les presets ni les scenes) : dans la source Texte, la case *Suivre le planning* et une zone « une ligne par passage » (`22:30 NOM DU DJ`, `23h30 - NOM`, `\\n` = retour a la ligne).
  Toutes les 5 s (`gui_logo.sched_tick`) le passage en cours est cherche ; quand il CHANGE, la source passe en Texte et le texte est pose (une fois : taper a la main entre deux passages n'est pas ecrase). Les heures avant 18 h comptent apres minuit (`DAY_START`). Une ligne invalide est ignoree.
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

### Mosaique de doodles du logo (`mosaic_*`, section du bas de l'onglet « Cellules »)

Le logo (image, texte ou video) garde sa **forme** mais est reconstruit avec les doodles recus du public (voir *Doodles du site*). Comme la fonte et les cellules, tout est dans `logo_at()` de `scene.frag` (`logo_mosaic()`), donc dans la couche logo : contour, aura, effets par couche et fonte suivent.
- **Vignettes** (`engine/render.py`) : `Renderer.add_doodle` appelle `mosaic_tile(rgba)` (boite de l'alpha, recadrage carre centre, 96 x 96 px, rgba premultiplie) et garde les **64 dernieres** dans `Renderer.mosaic_tiles` **meme apres la sortie de l'ecran du doodle** ; elles sont ecrites dans un atlas de 8 x 8 (`_mosaic_atlas`, texture unite 4, recree seulement quand un doodle arrive). La liste vit dans le Renderer : elle repart de zero a chaque ouverture de la projection (limite connue ; precharger les derniers fichiers du bucket a l'activation est possible mais n'est pas fait).
- **Shader** : grille posee sur le logo (`10 x mosaic_size` cases en hauteur, isotrope), une vignette par case (hachage `chash3`), 3 % de joint de chaque cote (le joint et les parties transparentes d'une vignette laissent voir la couleur d'origine du logo, `rgb = m.rgb * a_logo + c.rgb * (1 - m.a)`) ; l'alpha du logo n'est pas touche (la forme reste). Chaque case change de vignette toutes les ~4 s, decalee des autres (`era = floor(u_mosaic_t * 0.25 + hash * 8)`), `_mosaic_t` integre le temps. `textureLod` obligatoire (bloc conditionnel). Un doodle affiche a l'ecran passe par la meme passe que le logo : `_draw_doodles` met `u_mosaic` a 0 (un doodle garde ses couleurs).
- **Reglages** (portee `look` : presets overlay et scenes) : `mosaic_on` (0), `mosaic_amount` (1), `mosaic_size` (1, 0,4 a 3). Rien n'est dessine tant qu'aucun doodle n'est arrive (`u_mosaic_n` = 0). PANIQUE coupe `mosaic_on`. Pas dans le Randomizer ni dans la securite GPU (un seul echantillon de texture par pixel du logo).
- **Voyant** : `s.status["mosaic"]` (nombre de vignettes) alimente le texte a cote de la case (« 12 doodles dans la mosaique » / « aucun doodle recu pour l'instant »), relu par `update_meter`. Le grisage (`gui_gates`) des curseurs de la mosaique et celui des cellules sont separes (les lignes de la section mosaique ne dependent pas de la case Cellules).
- **Limite visuelle** : un logo a traits fins (le PNG du depot) est plus fin que les cases ; on voit des fragments de couleur, pas des images reconnaissables. Un texte epais ou un logo plein est bien meilleur.
- `check_gl.py` (`check_mosaic`) : vignette, coupee / vide / part a 0 = image d'origine, forme du logo gardee, couleurs des vignettes presentes, rien hors du logo, renouvellement au cours du temps, taille reglable, 64 vignettes au plus.

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

Pour que les visuels vivent tout seuls : 28 reglages (dont 4 pour les effets de la couche logo) peuvent suivre une courbe (fond motif :
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
- **Actives par defaut** (23) : fond (dont 5 de la palette labyrinthe), effets, contour du logo, pulsation. **Coupees par
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
