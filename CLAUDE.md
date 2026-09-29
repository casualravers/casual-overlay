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
python audio2wave_gl.py -d "<entree>"                  # live, plein ecran sur le 2e moniteur
python audio2wave_gl.py --synthetic --no-fullscreen    # sans ffmpeg ni micro
python check_gl.py                                     # verifications hors materiel
```

Options utiles : `--live-args "--colors white --gain 30"` (transmis a audio2wave_live),
`--render-size`, `--logo`, `--logo-scale`, `--logo-pos`, `--monitor`, `--hud`, `--stats`,
`--fps-cap`, `--max-seconds` + `--screenshot` (captures de test).

Touches : Echap quitte, F fenetre/plein ecran, H barres de debug, 1-5 effets
(wobble/ripple/chroma/glitch/logo), +/- ou PageUp/PageDown intensite globale, haut/bas
sensibilite du kick, R recharge shaders + reglages, P sauve dans
`~/.audio2wave/gl_params.json` (recharge au lancement).

## Architecture

```
micro --dshow--> ffmpeg (producteur) --rawvideo--> FrameReader (fil) --derniere frame entiere-->
micro --WASAPI--> sounddevice -> RingBuffer -> AudioAnalyzer (fil) -> FeatureExtractor -> etat
                                                                          |
        fenetre glfw + moderngl : passe 1 (FBO scene: video + logo + contour) -> passe 2 (post + HUD)
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
- Logo : alpha **premultiplie** cote CPU (`load_logo`) et dans le shader, sinon halo sombre
  aux bords ; mipmaps + anisotropie. `logo_layout()` (fonction pure) calcule le rectangle et
  garantit que pulse/jitter ne le sortent jamais du cadre.
- Les uniforms inutilises sont elimines par le compilateur GLSL : `Renderer._set()` ignore
  un nom absent, sinon un shader retouche a chaud planterait.
- `Renderer.reload_shaders()` : une erreur de compilation leve `ShaderError` et **conserve
  l'ancien programme** (jamais d'ecran noir sur scene pour une faute de frappe).
- `spawn_producer()` : meme commande que `live.producer_command()`, mais stdout est un pipe
  Windows a **gros tampon** (16 Mo via `CreatePipe`), voir Pieges.

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
- `--colors grey`, le defaut d'`audio2wave_live.py`, est refuse par ffmpeg 9.0 (Gyan full
  build) : `Cannot find color 'grey'`, le producteur meurt tout de suite. Contournement :
  `--live-args "--colors white"`. C'est un bug d'audio2wave, non corrige ici (aucun fichier
  du depot dependance n'est modifie).
- Le producteur peut mourir en cours de route : message unique sur stderr, derniere image
  conservee (les effets continuent) plutot que fermer la fenetre en soiree.

## Verifications

`python check_gl.py` (aucun peripherique requis) : lecteur de frames avec faux producteur
par blocs de 4 Ko (aucune frame melangee, ordre conserve, partielle jetee, latest-wins) ;
features sur signaux synthetiques (un beat par salve a 48 et 44,1 kHz, periode 500 ms,
-30 dB, bruit blanc, sinus continu, silence, isolation des bandes, ring buffer) ; reglages
(P) ; rendu en contexte standalone (image identique a la video quand l'audio est coupe,
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

Modes Snap/Ridge en source, GUI de reglages (tkinter entre en conflit avec la boucle
principale de glfw), MIDI, presets/automation de courbes, sortie Spout/NDI, plusieurs logos
ou logo anime. Pas de `.bat` de lancement.
