# PLAN_POC_GL.md — POC rendu OpenGL (logo + deformation reactive a l'audio)

Document destine a Claude Code, a poser a la racine du depot. Meme convention que le
CLAUDE.md : francais sans accents dans le code, les commentaires et les messages CLI.

## 1. Objectif

Afficher le visuel `audio2wave_live` (flux ffmpeg) dans une fenetre OpenGL plein ecran sur
le videoprojecteur, avec :

- un logo PNG incruste par-dessus ;
- des deformations visuelles (logo ET visuel entier) pilotees en temps reel par l'audio
  du micro (basses, kick, energie).

## 2. Decisions deja prises

| Sujet | Decision |
|---|---|
| Mode source | **Live seul** : le flux `rawvideo rgb24` du producteur ffmpeg devient une texture GL. Snap/Ridge hors POC. |
| Analyse audio | **`sounddevice` en parallele** de la capture dshow de ffmpeg + FFT `numpy`. |
| Code | **Nouveau script isole `audio2wave_gl.py`**. Aucun fichier existant modifie (sauf ajout de doc dans CLAUDE.md/README a la fin). Le repo garde sa regle "stdlib seulement" ; les dependances du POC vivent dans `requirements-gl.txt`. |
| Perimetre des effets | **Logo + visuel entier** deformes. |
| Rendu | `moderngl` + `glfw`, 2 passes (scene puis post-traitement), fenetre borderless sur le moniteur secondaire. |
| Plateforme | Windows (dshow, comme le reste du depot). |

## 3. Architecture cible

```
micro --dshow--> ffmpeg (producteur, meme filtre que live) --rawvideo--> [fil lecteur]
                                                                              |
                                                     derniere frame ENTIERE (latest wins)
                                                                              v
micro --WASAPI--> sounddevice --> [ring buffer] --> FFT/bandes/beat --> uniforms
                                                                              |
                          fenetre glfw + moderngl (60 fps, vsync)             v
   passe 1 (FBO scene) : texture video + logo (pulse, jitter)
   passe 2 (ecran)     : wobble UV, ripple, aberration chromatique, glitch, HUD debug
```

Points d'architecture importants :

1. **Le rendu GL tourne a la cadence de l'ecran (60 fps), pas a celle de ffmpeg.** Le
   producteur peut rester a 30 fps : la derniere frame est re-echantillonnee, les effets
   restent fluides.
2. **Latest-frame-wins** : le fil lecteur ecrase la frame precedente non consommee. Jamais
   de file qui s'allonge (sinon latence croissante, cf. `--averaging` deja documente).
3. **Leçon du CLAUDE.md a reprendre** : le lecteur accumule des frames ENTIERES
   (`frame_size = largeur x hauteur x 3`). Ne jamais uploader un bloc de taille arbitraire
   (decalage permanent de l'image, bug deja rencontre avec `relay_loop`).
4. Resolution de rendu ffmpeg (`--render-size`, defaut 1280x720) **independante** de la
   fenetre : le shader met a l'echelle, ce qui allege ffmpeg et le PCIe.
5. Ordre des moniteurs : ne pas se fier a un numero. Prendre le premier moniteur non
   principal (`glfw.get_primary_monitor()` exclu), comme `secondary_monitor_rect()` cote
   ffplay. Fenetre `undecorated` calee sur le rect du moniteur, pas de vrai plein ecran
   exclusif (meme raison que le `-noborder` deja retenu).
6. Les noms de peripheriques dshow (ffmpeg) et sounddevice different : prevoir
   `--audio-device` separe avec correspondance par sous-chaine, sinon defaut systeme.

## 4. Decoupage en taches

Chaque tache = un objectif verifiable. Les commits se font entre les taches.

### Phase 0 — Cadrage

**T0 — Recon + squelette**
- Lire `audio2wave_live.py` (`producer_command`, `spawn_producer`, `resolve_size`,
  `window_title`, taille de frame) et `common.py` (`require_tools`, `list_audio_devices`,
  `capture_input_args`). Noter dans un court commentaire de tete de `audio2wave_gl.py` ce
  qui est reutilise par import et ce qui est reimplemente. **Verifier les signatures reelles
  avant de les appeler** ; si `spawn_producer` est trop couple a ffplay/`args` de live, repli :
  reconstruire la commande a partir de `build_filter`.
- Creer `audio2wave_gl.py` (argparse), `requirements-gl.txt` (moderngl, glfw, numpy,
  sounddevice, Pillow), verification des dependances a l'import avec message clair
  ("pip install -r requirements-gl.txt").
- Options : `-d/--device`, `--audio-device`, `--logo`, `--size` (fenetre), `--render-size`,
  `--fps`, `--no-fullscreen`, `--monitor`, `--dry-run`, `--list-devices`,
  `--list-audio-devices`, `--synthetic` (voir T10).
- Critere : `--dry-run` affiche la meme commande ffmpeg que `audio2wave_live.py --dry-run`
  a options equivalentes ; `--list-audio-devices` liste les entrees sounddevice.

### Phase 1 — Pipeline video

**T1 — Fenetre GL + texture de test**
- glfw : fenetre borderless sur moniteur secondaire (repli : principal), vsync, Echap = quitter,
  F = bascule fenetre/plein ecran, compteur de fps dans le titre.
- moderngl : quad plein ecran, shader de base, texture de test animee (degrade + damier).
- Critere : 60 fps stables sur le projecteur, fermeture propre (pas de process residuel).

**T2 — Lecteur de frames ffmpeg**
- Lancer le producteur (T0), fil lecteur qui accumule `frame_size` octets, publie la derniere
  frame entiere (verrou minimal), le fil principal fait `texture.write()`.
- Retournement vertical dans le shader (rawvideo = haut en premier).
- Arret propre : fenetre fermee ou Ctrl+C -> terminate/wait du producteur.
- Critere : le visuel live s'affiche dans la fenetre GL, sans decalage, sans latence qui derive
  sur 10 minutes.

**T3 — Logo statique**
- Charger un PNG RGBA (Pillow), alpha premultiplie, respect du ratio, options `--logo-scale`
  et `--logo-pos`, uniforms dedies.
- Composition dans un FBO "scene" (video + logo) : c'est la passe 1.
- Critere : logo net, bords propres (pas de halo sombre), centre par defaut.

### Phase 2 — Audio

**T4 — Capture sounddevice**
- `InputStream` en callback, mono, 48 kHz, blocs 512-1024, ring buffer numpy thread-safe,
  liste/selection de peripherique (T0), prefere WASAPI partage.
- Critere : le flux tourne en parallele du producteur ffmpeg sur le meme micro sans erreur ni
  glitch ; latence de capture < 30 ms.

**T5 — Extraction de features (fonctions pures)**
- Fenetre 2048 + Hann, `rfft`, bandes : basses (~20-150 Hz), mediums, aigus, plus RMS.
- Auto-gain : normalisation par maximum glissant a decroissance lente (equivalent du
  `--gain auto`), sinon les niveaux micro varient trop d'une salle a l'autre.
- Lissage attaque rapide / relachement lent par bande.
- Detection de kick : flux spectral sur la bande basses vs moyenne glissante + k x ecart-type,
  periode refractaire ~150 ms. Sortie **enveloppe** `beat` (1 -> 0, decroissance) plutot qu'un
  booleen, plus `time_since_beat`.
- Critere : test sur signal synthetique (salves de sinus 60 Hz toutes les 500 ms) : un beat par
  salve, pas de faux positifs sur bruit constant.

**T6 — Pont audio -> uniforms + HUD debug**
- Fil d'analyse, dernier etat lu par le fil GL a chaque frame. Uniforms : `u_time`, `u_res`,
  `u_bass`, `u_mid`, `u_high`, `u_rms`, `u_beat`, `u_since_beat`.
- Touche H : petites barres bass/mid/high/beat dessinees dans le shader (pas de rendu de texte).
- Critere : les barres suivent la musique ; le kick declenche `u_beat` en phase avec le son.

### Phase 3 — Effets

**T7 — Passe 1 : effets sur le logo**
- Pulse (zoom sur `u_beat`), leger jitter/tremblement sur les aigus, contour lumineux sur les basses.
- Critere : le logo "respire" avec le kick, jamais coupe hors cadre.

**T8 — Passe 2 : post-traitement sur l'image entiere**
- Wobble sinusoidal des UV (amplitude = basses), shockwave/ripple depuis le centre au kick
  (rayon = `u_since_beat`), aberration chromatique (decalage R/G/B sur `u_beat`), glitch par
  bandes horizontales (hash quantifie dans le temps, declenche par seuil).
- Chaque effet a un interrupteur et une intensite (uniforms `u_fx_*`).
- Critere : chaque effet est visible isolement, l'ensemble reste lisible ; aucun effet ne
  sature le noir/blanc au repos (musique coupee = image quasi neutre).

**T9 — Controle en direct**
- Touches 1-5 (activer/desactiver un effet), `+`/`-` (intensite globale), R (recharger les
  shaders depuis le disque : les GLSL vivent dans des fichiers `gl_shaders/*.glsl` pour iterer
  sans relancer), P (sauver les reglages dans `~/.audio2wave/gl_params.json`, recharges au
  lancement).
- Hors POC : GUI tkinter (conflit de boucle principale avec glfw) et MIDI.
- Critere : on peut regler les effets pendant que la musique joue, sans redemarrer.

### Phase 4 — Robustesse et livraison

**T10 — Tests hors materiel** (meme philosophie que le repo)
- `--synthetic` : source video generee (degrade anime) + audio synthetique (kick 60 Hz a
  120 BPM), aucun ffmpeg ni micro requis.
- Script de verification `check_gl.py` : lecteur de frames avec faux producteur livrant par
  blocs de 4 Ko (aucune frame melangee, total multiple de `frame_size`) ; features sur signaux
  synthetiques ; rendu d'une frame en contexte `moderngl` standalone vers un PNG, verifie
  non vide.
- Critere : `python check_gl.py` passe sans peripherique.

**T11 — Mesures perf et latence**
- Mesurer fps, temps d'upload de la texture, temps de frame GPU, latence audio -> pixel
  (impulsion sur le micro -> premiere frame modifiee). Cibles : 60 fps a 1920x1080, latence
  audio -> effet < 50 ms (hors `--averaging` de ffmpeg, propre au visuel de base).
- Consigner les chiffres dans le CLAUDE.md, comme les autres mesures du depot.

**T12 — Documentation**
- Section dediee dans CLAUDE.md (francais sans accents) : architecture, decisions, pieges,
  verifications. Section dans README : installation `pip install -r requirements-gl.txt`,
  lancement, touches.
- Ne pas ajouter de `.bat` (le CLAUDE.md fait de `audio2wave.bat` l'unique lanceur).

## 5. Ordre d'execution suggere (un prompt Claude Code par lot)

| Lot | Taches | Point de controle avant de continuer |
|---|---|---|
| A | T0 -> T3 | Le visuel live + le logo s'affichent sur le projecteur |
| B | T4 -> T6 | Les barres de debug suivent la musique |
| C | T7 -> T9 | Les effets reagissent en phase, reglables en direct |
| D | T10 -> T12 | Tests verts, chiffres perf, doc a jour |

Commit apres chaque lot (ou chaque tache si le lot est gros). Pour A et C, demander un plan
d'abord ; valider T5 (beat) en isole, c'est le composant qui fait ou defait le rendu en soiree.

## 6. Risques

1. **Double ouverture du micro** (dshow par ffmpeg + WASAPI par sounddevice) : normalement
   compatible en mode partage, a verifier tot (T4). Repli : capturer une seule fois via
   sounddevice et retirer le PCM a ffmpeg par stdin.
2. **Latence du visuel de base** : `--averaging` et la fenetre FFT de ffmpeg s'ajoutent a la
   nouvelle chaine. Les effets pilotes par sounddevice seront plus reactifs que le visuel
   lui-meme : c'est attendu, a regler par un decalage optionnel.
3. **Upload de texture 1080p** (~6 Mo/frame a 30 fps) : negligeable sur GPU dedie, a mesurer
   sur GPU integre (T11). `--render-size` est le levier.
4. **Detection de kick fragile en salle** (bruit ambiant, sub, saturation micro) : auto-gain et
   seuil adaptatif indispensables ; prevoir de quoi ajuster la sensibilite en direct.
5. **Ordre des moniteurs et DPI Windows** : tester avec le vrai projecteur branche, pas
   seulement en fenetre.

## 7. Hors POC (a garder pour la suite)

Modes Snap/Ridge en source, GUI de reglages, pilotage MIDI, presets/automation de courbes des
effets, sortie Spout/NDI vers Resolume/OBS, plusieurs logos ou logo anime (GIF/video).
