# casual-overlay

Fenetre OpenGL plein ecran pour videoprojecteur : le visuel `audio2wave_live` (spectre du
micro), un logo PNG, un texte tape a la main ou une animation video detouree incruste par-dessus, et des deformations (wobble, onde de choc, aberration
chromatique, glitch, pulsation du logo) pilotees en temps reel par les basses et le kick.

Ce depot depend du depot voisin **audio2wave** (`../audio2wave`, ou `--a2w-dir`).

## Installation

Prerequis : Python 3.12 (3.14 n'a pas encore de wheel `moderngl`), `ffmpeg` dans le PATH,
depot `audio2wave` a cote de celui-ci.

```bash
py -3.12 -m venv .venv
.venv\Scripts\python.exe -m pip install -r requirements-gl.txt
```

## Lancement

**Double-clic sur [start.bat](start.bat)** : ouvre la fenetre de reglages et la fenetre video (choisis
l'entree audio dans la fenetre de reglages). Il accepte les memes options que le script, par exemple
`start.bat -d "Microphone (Realtek(R) Audio)"` ou `start.bat --text "CASUAL RAVERS"`.

En ligne de commande :

```bash
python audio2wave_gl.py --list-devices                    # nom exact de l'entree dshow
python audio2wave_gl.py -d "Microphone (Realtek(R) Audio)"
python audio2wave_gl.py -d "Microphone (Realtek(R) Audio)" --gui   # avec la fenetre de reglages
python audio2wave_gl.py --gui --no-fullscreen             # GUI seule: choisir l'entree dans la fenetre
python audio2wave_gl.py --background pattern --gui        # fond en motif genere (pas besoin de -d)
python audio2wave_gl.py --synthetic --no-fullscreen       # essai sans micro ni ffmpeg
python check_gl.py                                        # verifications hors materiel
```

La fenetre s'ouvre sur le premier moniteur non principal (`--monitor N` pour choisir).
Logo anime : `--logo-video anim.webm` (WebM VP9 avec alpha, MOV ProRes 4444, GIF, APNG ; pour un MP4 a fond
uni, ajoute `--logo-key #00ff00`), ou GUI > Source > Video. Lu en boucle, avec pulsation et contour comme le logo.

Options principales : `--logo`, `--logo-video`, `--logo-key`, `--text "LIGNE 1\nLIGNE 2"`, `--logo-scale`, `--logo-pos x,y`, `--render-size`,
`--audio-device`, `--live-args "..."`, `--hud`, `--stats`.

## Fenetre de reglages (`--gui`)

La fenetre a **deux grandes parties**, separees par un trait violet et chacune coiffee d'un bandeau
de couleur ; chaque section porte aussi son bandeau.

**SCENES (rangee ambre, tout en haut, toujours visible)** : une scene change **tout le look d'un clic** (ou F1 a F9 dans la
fenetre de rendu, avec `--gui`). Une scene = **le fond (audio2wave OU motif genere) + l'overlay**, car le motif genere
remplace le visuel d'audio2wave : s'il est choisi, la scene retient son reglage, sinon elle retient le mode (Live, Snap ou
Ridge) et son preset. Pour en creer une : regle tout comme d'habitude, clique *+ Nouvelle scene*, donne un nom, *Enregistrer*
(elle capture l'etat actuel). Clic sur une touche = charger ; **clic droit** = mettre a jour avec l'etat actuel, supprimer.
**Mode VJ** (a droite de la rangee) : *▶ VJ* enchaine tes scenes tout seul, une toutes les N secondes, avec un **flash blanc
et des bandes de glitch d'une demi-seconde** a chaque changement (le chiffre sur le bouton = secondes avant la prochaine).
*▶▶* passe tout de suite a la suivante. *reglages VJ* : **dans l'ordre** ou **au hasard** (jamais la meme scene deux fois
de suite), et la duree (10 s, 20 s, 30 s, 1 min, 2 min, 5 min). Choisir une scene a la main met le VJ en pause, pour qu'il ne
se batte pas avec toi. Dans la fenetre de rendu : **Espace** = lecture / pause, **fleche droite** = scene suivante. Il faut
au moins 2 scenes dans le set. Un changement de mode (Live, Snap, Ridge) prend environ une seconde : le flash le masque en partie.
**Une scene ne change ni l'entree audio (elle reste celle qui est active), ni le plein ecran / la taille de la fenetre de
rendu**, et quand le VJ tourne la fenetre de reglages garde sa taille meme si une scene change de mode.
**Sets** : un set est un enchainement de scenes enregistre sous un nom. Les scenes sont des **briques** (une
bibliotheque, sans limite) : un set les **reference**, autant de fois qu'on veut et dans plusieurs sets ; modifier une scene
la modifie partout. Chaque entree d'un set peut porter son **propre media** (logo image, texte ou video : clic droit >
*Media de cette entree : utiliser le logo / texte actuel*, pastille ● sur la touche) : la meme scene peut passer deux fois,
avec deux logos differents. Le menu **SET** (a gauche de la rangee) change de set, en cree (vide ou copie), renomme,
supprime (les scenes restent), **exporte** le set dans un fichier `.json` autonome (le set et les scenes qu'il utilise ;
les medias restent des chemins, un media absent sur l'autre machine est signale) et **importe** un set. A l'import, un nom de
set ou de scene deja pris : *Renommer* (on garde les deux : `nom (2)`) ou *Remplacer* (avec confirmation) ; un fichier illisible
ou d'une version plus recente donne un message, sans rien effacer. **Changer de set redemarre le VJ** s'il tournait (premiere
scene du nouveau set), et chaque set a son ordre et sa duree de VJ. F1..F9 sont les 9 premieres entrees du set actif ; au-dela,
un menu *+N* les liste (le VJ les joue toutes). *+ Scene* : nouvelle scene depuis l'etat actuel, ou ajout d'une scene deja
enregistree. Clic droit sur une touche : mettre a jour la scene, media, monter / descendre, retirer du set, supprimer
de la bibliotheque.
**Bouton *Etendre*** (a droite de la barre) : un panneau sous la barre montre le set en detail, et la fenetre grandit de sa
hauteur (elle reprend sa taille quand on *Reduit*). Une ligne par entree : sa scene (clic = la charger), son **media lie avec
sa vignette** (image, premiere image d'une video ; pour un texte, le texte ; en rouge si le fichier est introuvable), la
**duree propre** a l'entree dans le VJ (ou celle du set), une case **VJ** (decochee : le VJ la saute, mais elle reste
chargeable a la main), monter / descendre, *Renommer* (la scene, dans tous les sets) et *Retirer*. En tete : l'ordre et la
duree par defaut du set, et la duree d'une boucle.
**Deux familles de medias** : ceux de l'**overlay** (logo image, texte, video du logo) et ceux d'**audio2wave** (en mode
Snap, style pencil : *video interieure* et *video exterieure*). Une scene capture les siens a l'enregistrement (ceux de
l'overlay, et ceux du panneau Snap) ; chaque entree d'un set peut en plus en **remplacer** (pose par-dessus ceux de la
scene). Le panneau etendu montre, pour chaque entree, trois zones : OVERLAY, AUDIO2WAVE video int., AUDIO2WAVE video ext.
(media propre a l'entree en couleur avec sa croix, media capture par la scene en gris ; les zones audio2wave sont grisees
si la scene n'est pas une scene Snap).
**Glisser-deposer** : depose un fichier **sur la zone voulue** du panneau : la video interieure ou exterieure d'audio2wave (une
video seulement) ou l'overlay (image png / jpg / bmp, video ou animation gif / webm / mp4 / mov / mkv / avi / webp). Depose
sur une **touche de la barre**, un petit menu demande la destination. Si la scene est a l'ecran, le media s'affiche tout de
suite. Plusieurs fichiers : le premier est pris. Demande le paquet `tkinterdnd2` (dans `requirements-gl.txt` ; s'il manque,
tout marche sauf le glisser-deposer).
**Enregistrement** : les champs de fichier tapes a la main sans Entree (logo, video du logo, videos d'audio2wave, texte)
sont valides juste avant la capture : une scene retient toujours ce qui est a l'ecran.
Tout est dans `~/.audio2wave/scenes.json` (un ancien fichier est lu comme un set `principal`), **tout y compris l'etat du panneau d'audio2wave** : les scenes
n'ajoutent rien aux presets d'audio2wave (une scene et un preset sont deux choses differentes). Les scenes creees avec
une ancienne version (elles rangeaient un preset `scene-<nom>` chez audio2wave) sont migrees toutes seules au
lancement suivant, et ces presets sont retires de leurs listes.

**LIVE (bandeau turquoise, a gauche)** : **la fenetre de reglages d'`audio2wave_live.py --gui`, reprise telle
quelle** du depot audio2wave (meme theme, automations de courbes, info-bulles) pour le visuel de fond
ffmpeg : chaque reglage remplace le flux a chaud (environ 0,4 s apres le dernier), sans que la
fenetre video ne bouge. La taille de fenetre n'y est pas reprise ; la case *Plein ecran* commande la fenetre
video. **Ses presets sont la** (section *PRESETS LIVE*, en bas : Charger / Mettre a jour / Supprimer /
Sauvegarder sous), partages avec audio2wave (memes fichiers).

**Live, Snap, Ridge** : les boutons *Live / Snap / Ridge* (a cote de l'entree audio) changent le mode d'audio2wave
qui fournit le fond, dans la meme fenetre : le panneau de gauche devient celui du mode (ses reglages, ses
presets *PRESETS SNAP* / *PRESETS RIDGE*, ceux d'audio2wave), la partie OVERLAY reste telle quelle. Snap (photo de
l'onde : crayon, rekordbox, onde pleine) et Ridge (vagues empilees) sont dessines en Python par audio2wave, puis
affiches comme fond avec le logo et les effets par-dessus. Le panneau de ces modes est plus haut que celui de
Live : il defile (ascenseur ou molette). Ridge demande une entree audio (a choisir d'abord dans Live ou Snap).

**OVERLAY (bandeau violet, a droite)** : les panneaux propres a casual-overlay, en commencant par
les **presets overlay** (section *PRESETS OVERLAY*, en haut) : un preset = le look du logo ou
texte, des effets, du halo, de la fonte, des cellules et leurs automations (**pas le fond**, qui a ses propres presets,
ni la sensibilite du kick, qui depend du micro). Memes gestes que les presets live : *Charger* (le menu applique
tout de suite), *Mettre a jour*, *Supprimer*, *Sauvegarder sous* un nom, *Restaurer*. **Tous les presets se modifient
et se suppriment, integres compris** (`default`, `sobre`, `neon`, `chaos`) : *Restaurer* rend les integres
d'origine, sans toucher aux tiens. `default` reste toujours le premier de la liste (modifie, il devient le point de
depart ; le supprimer le remet a sa version d'origine). Ils sont dans `~/.audio2wave/overlay_presets.json`.
Le **fond** a le meme jeu de presets (section *PRESETS FOND*, dans le bloc Fond de la partie LIVE) : source
Audio2wave / motif genere, palette, couleurs, reglages du motif et leurs automations, dans
`~/.audio2wave/background_presets.json`. Charger l'un ne touche jamais l'autre.

Sous les presets, le reste de la partie OVERLAY est range en **onglets** pour que la fenetre reste courte
(environ 650 px de haut) : *Effets*, *Logo*, *Aura du logo* (halo holographique et reaction du logo a l'audio),
*Fonte du logo*, *Cellules*, *Noise*, *Affichage*.

- **Fond** (en tete de la partie LIVE : un choix *Audio2wave* / *Fond genere*, et **un seul des deux est affiche a la fois** : le
  panneau d'audio2wave, ou les reglages du fond genere avec leurs presets) : *Audio2wave* (le mode de gauche : Live, Snap ou Ridge) ou *Fond genere* (degrades + damier animes par la
  carte graphique : palette Arc-en-ciel, Duo ou Banc de test (copie exacte du motif de test, sans reglage), couleurs, angle, teinte, vitesse, taille des
  carreaux, contraste, cadence, reaction au kick ; effet immediat).
- **Noise** : un seul hasard, prereglé, qui joue le role d'une musique imaginaire : des ondulations et des coups au hasard,
  meme sans son, sur les effets que tu coches (wobble, onde de choc, aberration, glitch, reaction du logo). Deux reglages :
  son *intensite* et les cases de ce sur quoi il agit (rien n'est coche par defaut). Un indicateur montre ce qu'il envoie.
- **Effets** : les 5 effets (interrupteur + intensite), intensite globale, sensibilite du kick.
  Wobble, onde de choc, aberration et glitch s'appliquent au **fond** et au **logo** chacun dans sa
  couche : par defaut « Memes effets que le fond » est coche (comportement d'origine), decochee, le
  logo a ses propres interrupteurs et intensites, ses ondes partent de son centre et son glitch tire
  d'autres bandes (ex. un fond qui ondule sur les basses, un logo net qui n'encaisse que le glitch).
  L'effet 5 (pulsation, tremblement, contour du logo) reste a part.
- **Halo holographique** : une lumiere irisee a grande portee autour du logo (image, texte ou video) qui
  **deforme le fond** (lentille, ondes qui partent du logo, franges colorees) et y traine de la poussiere
  d'etoiles ; le logo reste net. Actif par defaut, anime par le temps seulement (reaction a l'audio a 0 ;
  touche C). Reglages (intensite, portee, deformation, poussiere, vitesse, reaction audio) dans le bloc
  (onglet *Aura du logo*).
- **Fonte acide du logo** (onglet *Fonte du logo*, touche M) : le logo, le texte ou la video se **dissout sur
  place**, ronge par un acide : des trous grandissent, leur lisiere vire au vert-jaune acide et brille, puis
  la matiere se reforme, en boucle (intact, fond, dissous, se reforme). Reglages : profondeur (1 = il disparait
  entierement), duree du cycle, grain des trous, bord acide, couleur de l'acide, reaction audio. Coupee par
  defaut et non audioreactive ; l'aura suit la forme rongee.
- **Cellules organiques** (onglet *Cellules*, touche V) : le logo, le texte ou la video **se decompose en
  cellules** (Voronoi). Il garde sa forme : il est fait de bulles qui glissent, se retrecissent et s'ecartent les
  unes des autres, chacune en aplat translucide avec contour et reflet de bulle (cell shading). Les cellules ne
  reprennent que la forme du logo, jamais la zone qu'il occupe. Reglages : decomposition (0 = logo intact, 1 =
  cellules separees), rayon des cellules (petit = bulles isolees, grand = elles se touchent), taille des
  cellules, vitesse, aplat (couleurs d'origine ou couleur du centre de la cellule), couleur du contour, reaction
  audio. Coupee par defaut et non audioreactive. Limite : les cellules restent dans le rectangle du logo.
- **Logo / texte** : source *Image* (PNG) ou *Texte* (tape directement dans la fenetre, plusieurs lignes, police, couleur, alignement, taille), puis position X/Y, opacite, pulsation au kick, tremblement, contour
  lumineux (intensite, rayon, couleur). Effet immediat.
- **Analyse audio et affichage** : entree d'analyse, plein ecran, barres debug, recharger les
  shaders, sauver les reglages, mesures et statut.

**Automations** : chaque curseur du motif, du logo et des effets a une case `~` (il varie tout seul) et un
bouton `∿` qui ouvre l'editeur de courbe d'audio2wave (sinus, triangle, carre, dents de scie, aleatoire,
ou dessin libre, avec sa propre vitesse). Par defaut, le fond et les effets sont animes (periodes toutes
differentes, donc jamais deux fois la meme image) et le logo reste fixe ; « Automations actives » (ou la
touche T, ou `--no-automation`) fige tout, « Ambiance par defaut » remet l'etat d'origine.
Les automations fonctionnent aussi sans `--gui`.
Trois sauvegardes distinctes : les presets live (audio2wave), les presets overlay (plusieurs, nommes), et
le bouton *Sauver reglages* (ou la touche P) qui enregistre les reglages courants de l'overlay dans
`gl_params.json`, rechargee au lancement.
Les touches ci-dessous restent actives dans la fenetre video, et la GUI suit leurs changements.
## Touches

| Touche | Action |
|---|---|
| Echap | quitter |
| F | fenetre / plein ecran (plein ecran au lancement seulement s'il y a un 2e ecran) |
| B | fond : spectre audio / motif genere |
| T | automations : actives / figees |
| H | barres de debug (basses, mediums, aigus, RMS, kick + etat des effets) |
| 1 a 5 | wobble, ripple, aberration chromatique, glitch (fond), effets du logo (pulsation, contour) |
| Maj + 1 a 4 | le meme effet sur la couche du logo (delie fond et logo) |
| F1 a F9 | scenes 1 a 9 (avec `--gui`) |
| Espace | mode VJ : lecture / pause (avec `--gui`) |
| Fleche droite | mode VJ : scene suivante (avec `--gui`) |
| L | lier / separer les effets du fond et du logo |
| C | halo holographique : on / off |
| M | fonte acide du logo : on / off |
| V | cellules organiques du logo : on / off |
| + / - (ou PageUp / PageDown) | intensite globale |
| Haut / Bas | sensibilite de la detection du kick |
| R | recharge les shaders (`gl_shaders/*.glsl`) et les reglages |
| P | sauve les reglages dans `~/.audio2wave/gl_params.json` |

## Ameliorations futures

Pistes identifiees, pas encore faites (les plus lourdes en dernier) :

- **Sources audio par couche** : piloter le fond sur les basses et le logo sur les aigus (ou un autre
  detecteur de kick, ou un autre micro) en plus des effets separes, pour decorreler vraiment les deux
  couches. Suppose des enveloppes `bass/mid/high/beat` par couche dans le shader et un choix de source
  dans la GUI.
- **Mode VJ plus riche** : la base est faite (enchainement toutes les N secondes, dans l'ordre ou au hasard, flash a
  chaque changement, Espace et fleche droite). Le developpement prevu :
  - **Bouton « etendre » (expand)** sur le VJ : un panneau deplie qui donne plus de granularite sans alourdir la barre
    (qui reste « une scene = une touche, un bouton VJ ») : liste des scenes dans l'ordre de passage, duree propre a chaque
    scene, scenes a exclure du VJ, ordre libre (programme a composer), reglages de transition.
  - **Glisser-deposer d'images ou d'animations** (PNG, GIF, WebM...) sur le panneau : soit **liees a l'overlay de la scene**
    (logo, texte ou video qui font partie de la scene : aujourd'hui ces medias restent a part des presets), soit **ajoutees
    en parametre d'audio2wave (mode Snap)**, qui sait deja jouer des videos / images dans son trace (reglages `video` et
    `video2`). A trancher : copier le fichier dans un dossier de medias du depot ou garder le chemin d'origine, et que faire
    d'un media introuvable au chargement d'une scene (repli sans erreur, message).
  - **Changer en rythme** (toutes les N mesures, en comptant les kicks detectes) plutot qu'a la seconde.
  - **Une vraie transition** (fondu des reglages numeriques ; un changement de mode Live -> Snap coupe la source, donc
    plutot un fondu enchaine d'image a image).
  L'interface doit rester simple et agreable : les options avancees vivent dans le panneau deplie, jamais dans la barre.
- **Sets plus riches** : la base est faite (bibliotheque de scenes, sets, media par entree, export / import, changement de
  set qui redemarre le VJ, panneau etendu, glisser-deposer, duree et exclusion par entree). Pistes : medias embarques dans
  le fichier exporte (archive avec les images et videos plutot que des chemins), glisser-deposer pour reordonner les
  lignes, depot sur les parametres Snap (`video` / `video2`), raccourci clavier pour changer de set en soiree, et un acces
  plus rapide aux scenes au-dela de F9 (pages).
- **Halo holographique plus riche** : automations de l'intensite et de la portee, teinte reglable, forme de
  la poussiere (etoiles a branches, comete), trainee directionnelle (vent) plutot que radiale, et mode
  « ombre » (soustractif) pour assombrir aussi le fond.
- **Synchro du logo anime sur le kick** : vitesse de lecture ou saut d'image au rythme de la musique
  (le fichier est lu a sa cadence propre).
- **Rendu GL natif de Snap et Ridge** : aujourd'hui ils sont dessines en Python pur par audio2wave (CPU, une
  image au rythme de leur trace progressif) ; les porter en shaders les rendrait aussi fluides que le motif
  genere et utilisables a 60 images/s. Pistes liees : brancher leurs automations de courbes sur le moteur GL et
  charger leur Mode VJ (enchainement de presets) en meme temps qu'un preset overlay.
- **Doodle dessine a la main depuis un site web** : une page de dessin (PNG transparent) depose le trait sur
  un serveur ; casual-overlay l'interroge toutes les quelques secondes et l'incruste comme logo (nouvelle
  source « Doodle (web) »). A trancher : hebergement, validation manuelle avant affichage (moderation), un doodle
  a la fois ou une file ; l'image est toujours revalidee (taille, dimensions, Pillow) avant chargement.
  Hebergement prevu : GitHub Pages pour la page de dessin. Attention, c'est un hebergement **statique** : il
  sert la page mais ne peut pas recevoir un dessin. Il faudra un point de depot a cote (fonction serverless,
  Firebase/Supabase, ou commit d'un `latest.png` via l'API GitHub) que casual-overlay interrogerait ensuite.
- **Detourage plus fin du logo video** : similarite et fondu reglables dans la GUI (valeurs fixes),
  et relance du decodage sans a-coup.
- **Plusieurs logos ou textes a la fois**, chacun avec sa couche d'effets.
- **Latence audio -> image** : mesuree seulement par estimation (~45-65 ms) ; mesure acoustique de bout
  en bout et leviers (bloc de capture plus petit, fenetre d'analyse plus courte).
- **Sortie Spout/NDI** vers un logiciel de VJing, et entree MIDI pour piloter les reglages.

Details d'architecture, pieges et mesures : [CLAUDE.md](CLAUDE.md).
