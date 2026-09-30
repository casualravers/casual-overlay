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

A gauche, **la fenetre de reglages d'`audio2wave_live.py --gui`, reprise telle quelle** du depot
audio2wave (meme theme, presets, automations de courbes, info-bulles) pour le visuel de fond
ffmpeg : chaque reglage remplace le flux a chaud (environ 0,4 s apres le dernier), sans que la
fenetre video ne bouge. Les boutons Snap/Ridge et la taille de fenetre n'y sont pas repris ; la
case *Plein ecran* commande la fenetre video.

A droite, les panneaux propres a casual-overlay :

- **Fond** : *Spectre audio* (celui de gauche) ou *Motif genere* (degrades + damier animes par la
  carte graphique : palette Arc-en-ciel ou Duo, couleurs, angle, teinte, vitesse, taille des
  carreaux, contraste, cadence, reaction au kick ; effet immediat).
- **Effets** : les 5 effets (interrupteur + intensite), intensite globale, sensibilite du kick.
  Wobble, onde de choc, aberration et glitch s'appliquent au **fond** et au **logo** chacun dans sa
  couche : par defaut « Memes effets que le fond » est coche (comportement d'origine), decochee, le
  logo a ses propres interrupteurs et intensites, ses ondes partent de son centre et son glitch tire
  d'autres bandes (ex. un fond qui ondule sur les basses, un logo net qui n'encaisse que le glitch).
  L'effet 5 (pulsation, tremblement, contour du logo) reste a part.
- **Halo chrome** : halo metallique autour du logo (image, texte ou video) : relief tire du contour, reflet
  d'un faux studio a bandes claires/sombres, ajoute en lumiere (plus spectaculaire sur fond sombre).
  Actif par defaut, anime par le temps seulement (reaction a l'audio a 0 ; touche C). Reglages (intensite,
  relief, vitesse, bandes, reaction audio) dans le bloc repliable « Reglages ».
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
Les presets de gauche (options live + automations) sont ceux d'audio2wave ; les reglages de
droite se sauvent avec le bouton *Sauver reglages* (ou la touche P), a part.
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
| L | lier / separer les effets du fond et du logo |
| C | halo chrome du logo : on / off |
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
- **Scenes nommees** : sauvegarder et rappeler sous un nom l'ensemble des reglages casual-overlay (fond
  motif, logo, effets, automations), comme les presets d'audio2wave le font pour les options live ;
  aujourd'hui un seul jeu de reglages (`gl_params.json`).
- **Halo chrome plus riche** : automations de l'intensite et du relief, environnements au choix
  (studio, neon, arc-en-ciel), couleur de teinte reglable, et mode « ombre » (soustractif) pour qu'il
  reste lisible sur un fond clair ou sur le motif.
- **Synchro du logo anime sur le kick** : vitesse de lecture ou saut d'image au rythme de la musique
  (le fichier est lu a sa cadence propre).
- **Detourage plus fin du logo video** : similarite et fondu reglables dans la GUI (valeurs fixes),
  et relance du decodage sans a-coup.
- **Plusieurs logos ou textes a la fois**, chacun avec sa couche d'effets.
- **Latence audio -> image** : mesuree seulement par estimation (~45-65 ms) ; mesure acoustique de bout
  en bout et leviers (bloc de capture plus petit, fenetre d'analyse plus courte).
- **Sortie Spout/NDI** vers un logiciel de VJing, et entree MIDI pour piloter les reglages.

Details d'architecture, pieges et mesures : [CLAUDE.md](CLAUDE.md).
