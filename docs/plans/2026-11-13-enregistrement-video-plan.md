# Enregistrement video + audio : plan d'implementation

> **Pour l'executant** : suivre tache par tache (skill `superpowers:executing-plans`). Les cases `- [ ]` suivent l'avancement. Tout texte ecrit (code, commentaires, messages) en francais SANS accents. Une branche neuve depuis `origin/main`, jamais de commit sur `main`.

**But** : un bouton Enregistrer qui ecrit un MP4 (H.264 + AAC) pret a poster, image rendue au format voulu + audio stereo brut de la carte son.

**Architecture** : logique pure dans `core/recording.py` (TDD) ; `engine/recorder.py` possede les fils d'ecriture et les processus ffmpeg ; la boucle GL (`engine/projection.py`) rend UNE fois vers un tampon hors ecran puis le copie dans la fenetre ; la GUI ne parle qu'a `Session`.

**Pile** : Python 3.12, moderngl, sounddevice, ffmpeg (deja requis), Tk.

**Spec** : `docs/plans/2026-11-13-enregistrement-video-design.md` (validee). Issue #140.

## Contraintes globales

- `core/` : stdlib seulement, aucun import du moteur, de la GUI, de numpy ni de GL (`check_core_purity`).
- Toute nouvelle verification : son nom dans `SUITE` de `tools/check_gl.py` (sinon `check_groupes` echoue), dans `PURES` si elle ne demande rien de materiel, dans `GROUPES["moteur"]` si elle rend avec GL.
- Nouveaux reglages : une ligne dans `core/params_schema.py` (portee `device`, plage contenue dans celle de la GUI).
- Nouveaux fichiers embarques dans le portable (`tools/build_portable.py`, `check_portable_modules`).
- Pas de regression hors enregistrement : sans enregistrement en cours, aucun cout ni changement de rendu (images identiques pixel pour pixel).
- Rien de ce qui demande le materiel (carte son, GPU reel, TikTok / Instagram) n'est declare verifie : « non verifie ».
- Valeurs de la spec : formats vertical 1080x1920 (defaut), horizontal 1920x1080, carre 1080x1080 ; 30 (defaut) ou 60 images/s ; AAC 320 kb/s 48 kHz stereo ; `libx264 -preset veryfast -crf 17` ; `rec_delay_ms` 0..300, defaut 40 ; avertissement disque sous 2 Go ; alerte au-dela de 2 % d'images dupliquees.

## Points a surveiller (non couverts par les taches nominales)

Chaque ligne a son test dans la tache qui possede le code.

1. Carte son debranchee pendant l'enregistrement : la video continue, un silence garde la duree (tache 4).
2. Disque plein ou presque : arret propre, ce qui est ecrit est assemble (tache 4).
3. Deux enregistrements dans la meme seconde ou dossier avec espaces / accents : noms uniques, aucun echec (taches 2 et 4).
4. Projection fermee ou relancee en cours d'enregistrement : arret propre, assemblage (tache 5).
5. GPU trop lent : images dupliquees, duree juste, message au-dela de 2 % (taches 2 et 5).

## Fichiers

| Fichier | Role |
|---|---|
| `core/recording.py` (neuf) | formats, planification des images, decalage audio, noms, commandes ffmpeg, choix de l'encodeur, crete |
| `core/params_schema.py` | `rec_format`, `rec_fps`, `rec_dir`, `rec_delay_ms` |
| `core/explain.py` | `rec_status_text` (voyant) |
| `engine/recorder.py` (neuf) | `Recorder`, `RawAudioWriter`, assemblage |
| `engine/audio.py` | point d'ecoute `tap` de `SoundDeviceInput` |
| `engine/session.py` | `Session.recorder`, commandes `rec_start` / `rec_stop` |
| `engine/projection.py` | cible hors ecran, relecture asynchrone, copie vers la fenetre, touche E |
| `gui/gui_audio.py`, `gui/gui_palette.py` | section « Enregistrement », commande de palette |
| `tools/check_gl.py`, `tools/build_portable.py`, `CLAUDE.md` | verifications, portable, documentation |

---

### Tache 1 : spike de faisabilite (jetable, ne se fusionne pas)

**But** : savoir si le rendu hors ecran en 1080x1920 + relecture + encodage tient a 30 puis 60 images/s sur le GPU integre de l'utilisateur, et si `draw` se comporte a une autre taille que la fenetre.

**Fichiers** : script `tools/spike_enregistrement.py` (supprime ensuite, jamais fusionne).

**Produit** : un compte rendu dans la PR (chiffres, decision).

- [x] **Etape 1** : ecrire le script : ouvre le moteur en synthetique, rend 300 images vers un FBO 1080x1920 par `Renderer.draw(fbo, (1080, 1920), ...)`, relit dans deux tampons en alternance (`ctx.buffer` + `fbo.read_into`), envoie a un `ffmpeg` (`libx264 veryfast`) sur son entree standard ; mesure le temps GPU (`PerfGuard`), le temps de relecture, le CPU de ffmpeg, le nombre d'images tenues.
- [x] **Etape 2** : lancer a 30 puis 60 images/s, avec fond `pattern` + halo + cellules ; verifier a l'oeil (MP4 lu) que pixelisation, halo, trainee et transition ne sont pas deformes par la taille.
- [x] **Etape 3** : noter dans la PR : GPU ms, CPU %, images tenues, encodeur materiel disponible (`ffmpeg -encoders`), ce qui change visuellement. Decision : 30 seul, 30 et 60, ou revoir la spec. **S'arreter et la demander a l'utilisateur si 30 ne tient pas.**
- Attendu : fichier de 10 s ouvrable, 300 images, aucune image noire.

#### Resultats du spike (fait le 2026-11-13 ; poste de dev : Ryzen 5 4600H 12 fils, Radeon integree, ffmpeg 9.0 ; script sur la branche `spike-enregistrement`, jamais fusionnee)

Rendu hors ecran 1080x1920, relecture asynchrone sur deux tampons GL, ecriture par un fil vers `ffmpeg`, etat audio synthetique (un kick toutes les 0,5 s). « Libre » = debit maximal, « cadence » = images attendues a l'heure.

| Profil (1080x1920) | rendu moyen | debit libre | a 30 images/s | a 60 images/s |
|---|---|---|---|---|
| leger (aucun effet couteux) | 8 ms | 87 i/s | tient | ~20 % en retard |
| halo seul | 13 ms | 63 i/s | tient | ne tient pas |
| fonte seule | 11 ms | 73 i/s | tient | ne tient pas |
| composition (trainee + pixelisation) | 9 ms | 81 i/s | tient | limite |
| **cellules seules** | **25 ms** | 37 i/s | tient de justesse | non |
| lourd (halo + cellules + fonte + composition) | **35 ms** | **27 i/s** | **ne tient pas (~10 % d'images dupliquees)** | non |
| lourd en **720x1280** | 16 ms | 57 i/s | **tient** | ~6 % de doublons |

- **Le goulot est le rendu GPU**, pas la relecture (0,2 a 0,4 ms pour la lancer, 1,5 a 3,5 ms pour la recuperer) ni l'encodage (x264 `veryfast` tient 80 images/s en libre).
- **CPU** (sur 12 fils) : x264 environ 230 % d'un coeur a 30 images/s (~19 % de la machine), encodeur materiel AMF environ 105 %. Fichiers de 18 a 25 Mo pour 10 s, soit 100 a 150 Mo par minute : l'ordre de grandeur de la spec est confirme.
- **Independance de la resolution** : ecart moyen de 0,7 a 2,7 sur 255 entre un rendu 1080x1920 reduit de moitie et un rendu direct en 540x960 : les effets lies a la taille (pixelisation, halo) se comportent pareil. Image regardee a l'oeil : sens correct (le texte du logo se lit a l'endroit, le `vflip` est bon), pixelisation, halo et trainee presents. MP4 valides : 300 images, 10 s, `yuv420p`, 1080x1920.
- **Limites du spike** : ni fenetre ni copie vers l'ecran, ni capture audio ni analyse en meme temps, ni GUI : les chiffres reels seront un peu moins bons. Le tampon hors ecran n'est **pas multi-echantillonne** alors que l'ecran de la fenetre l'est : bords moins lisses (voir tache 5). Un seul poste.

**Decisions tirees** (reportees dans la spec) :
1. **30 images/s tient** pour les looks courants ; les **cellules** (25 ms a elles seules) et le cumul des effets couteux font passer sous 30 images/s a 1080x1920.
2. Nouveau format **`vertical_hd` 720x1280** (tient 30 images/s avec tous les effets, ~57 images/s) a cote de 1080x1920, qui reste le defaut. L'alerte « plus de 2 % d'images dupliquees » le propose.
3. **60 images/s deconseille a 1080x1920** (ne tient pas, meme en leger, sur ce poste) ; il n'a de sens qu'en 720x1280.
4. **Encodeur par defaut `libx264`** (qualite previsible, CPU largement suffisant, le GPU est deja le goulot) ; l'encodeur materiel est une option (`rec_encoder`), au lieu d'etre prefere d'office.

### Tache 2 : `core/recording.py` (TDD)

**Fichiers** : creer `core/recording.py` ; test `tools/check_gl.py` (`check_recording`, dans `SUITE` et `PURES`) ; `core/params_schema.py`.

**Produit** (signatures exactes) :
- `FORMATS: dict[str, tuple[int, int]]` = `{"vertical": (1080, 1920), "vertical_hd": (720, 1280), "horizontal": (1920, 1080), "carre": (1080, 1080)}`.
- `frames_due(t0: float, now: float, fps: int, sent: int) -> int` : nombre d'images a ecrire pour que l'image numero k tombe a `t0 + k / fps` ; `max(0, floor((now - t0) * fps) + 1 - sent)`.
- `audio_offset(t_video0: float, t_audio0: float, delay_ms: float) -> float` : `(t_audio0 - t_video0) + delay_ms / 1000` (positif = audio retarde).
- `output_name(now: datetime, directory: str, exists=os.path.exists) -> str` : `casual-AAAA-MM-JJ_HHhMMmSS.mp4`, suffixe `-2`, `-3`... si le fichier existe.
- `video_command(ffmpeg: str, size: tuple[int, int], fps: int, encoder: str, out: str) -> list[str]` : entree `rawvideo rgb24` sur stdin, `-vf vflip`, `-pix_fmt yuv420p`, `-g <fps>`, etiquettes bt709 (`-colorspace`, `-color_primaries`, `-color_trc`), `libx264` : `-preset veryfast -crf 17`.
- `mux_command(ffmpeg: str, video: str, audio_raw: str, rate: int, channels: int, offset_s: float, out: str) -> list[str]` : video `-c copy`, audio `-f f32le` encode `aac -b:a 320k -ar 48000`, `-movflags +faststart` ; offset > 0 : `-itsoffset` sur l'audio ; offset < 0 : `-ss` de `-offset` sur l'audio.
- `choose_encoder(listed: Iterable[str], tested: Callable[[str], bool], hardware: bool = False) -> str` : `libx264` si `hardware` est faux ; sinon, dans l'ordre `h264_nvenc`, `h264_amf`, `h264_qsv`, le premier liste ET `tested` vrai, a defaut `libx264`.
- `peak_db(peak: float) -> float` : dBFS, plancher -120 ; `drop_warning(dropped: int, total: int) -> str | None` : message au-dela de 2 %.
- `core/explain.py` : `rec_status_text(seconds: float, size_bytes: int, peak_db: float, dropped: int, encoder: str) -> str` (« REC 01:23 | 412 Mo | crete -3 dBFS | images dupliquees 0 | libx264 »).

- [ ] **Etape 1 : tests d'abord** (`check_recording`) : `frames_due(0, 0, 30, 0) == 1` ; `frames_due(0, 0.0334, 30, 1) == 1` ; `frames_due(0, 0.10, 30, 1) == 3` (retard = doublons) ; `frames_due(0, 0.01, 30, 5) == 0` (jamais negatif) ; meme chose a 60 ; `audio_offset(10.0, 10.05, 40) == 0.09` (a 1 ms) ; `output_name` avec fichier existant donne `-2` puis `-3`, dossier avec espaces et accents ; `video_command` contient les trois etiquettes bt709, `-g 30`, `vflip`, `-crf 17` pour `libx264` et pas pour `h264_nvenc` ; `mux_command` : decalage positif -> `-itsoffset`, negatif -> `-ss` ; `choose_encoder(["libx264", "h264_nvenc"], lambda e: True) == "libx264"` (materiel non demande) ; avec `hardware=True` : `h264_nvenc` si l'essai reussit, `libx264` si l'essai echoue ; `peak_db(1.0) == 0.0`, `peak_db(0.0) == -120.0` ; `drop_warning(3, 100)` renvoie un texte, `drop_warning(2, 100)` `None` ; reglages : `rec_fps` hors {30, 60} ramene, `rec_delay_ms` 999 ramene a 300, `rec_format` inconnu ramene au defaut, `rec_encoder` inconnu idem.
- [ ] **Etape 2** : lancer `python tools/check_gl.py --seulement check_recording` : echec attendu (module absent).
- [ ] **Etape 3** : implementer `core/recording.py`, les cinq lignes de `params_schema.py` (portee `device` : `rec_format` defaut `vertical`, `rec_fps` 30, `rec_dir` `~/Videos/casual-overlay`, `rec_delay_ms` 40, `rec_encoder` `libx264` ou `materiel`) et `rec_status_text`.
- [ ] **Etape 4** : `--seulement check_recording` puis `--pur` : tout passe, `check_core_purity` et `check_params_schema` compris. Commit.

### Tache 3 : point d'ecoute audio et fichier brut

**Fichiers** : `engine/audio.py`, `engine/recorder.py` (neuf, classe `RawAudioWriter`), `tools/check_gl.py` (`check_recorder_audio`, dans `SUITE`, groupe `reste`).

**Interfaces** :
- `SoundDeviceInput.tap: Callable[[np.ndarray, float], None] | None = None` ; dans `_callback`, si `tap` est pose : `tap(indata.copy(), t_end)` (copie stereo ; mono duplique en deux canaux), sans autre travail dans le callback.
- `RawAudioWriter(path: str, channels: int, rate: int)` : `push(block, t_end)` (file, jamais bloquante : une file pleine incremente `lost`), fil d'ecriture `float32` entrelace, `t_first: float | None` = instant du premier echantillon (`t_end - n / rate`), `peak: float` (crete absolue depuis le debut), `bytes_written`, `close() -> None` (vide la file et attend le fil).

- [ ] **Etape 1 : test** : un faux `SoundDeviceInput` (callback appele a la main) avec `tap` : 100 blocs stereo connus -> le fichier brut relu est identique, `t_first` correct, `peak` exact, `tap = None` ne change pas le comportement d'analyse (le tampon mono recoit les memes valeurs qu'avant).
- [ ] **Etape 2** : lancer, echec attendu. **Etape 3** : implementer. **Etape 4** : relancer, puis `--seulement check_features` (analyse inchangee). Commit.

### Tache 4 : `Recorder` et assemblage

**Fichiers** : `engine/recorder.py`, `engine/session.py`, `tools/check_gl.py` (`check_recorder`, dans `SUITE`, groupe `moteur`).

**Interfaces** :
- `Recorder(ffmpeg: str, directory: str, fmt: str, fps: int, delay_ms: float, audio_input=None, clock=time.monotonic)`.
- `start() -> None` : verifie l'espace disque (avertissement sous 2 Go), choisit l'encodeur (`choose_encoder` avec un essai d'une image par encodeur), lance `ffmpeg` (`core.recording.video_command`), pose `audio_input.tap`.
- `due(now: float) -> int` (`frames_due` avec `t0` = premier appel) ; `write(frame: bytes, count: int = 1) -> None` (ecrit `count` fois la meme image : doublons) ; `rec_dropped`, `frames` comptes.
- `stop() -> str | None` : ferme les ecritures, assemble (`mux_command`, decalage `audio_offset(t_video0, audio.t_first, delay_ms)`) sous un nom provisoire puis renomme, supprime les temporaires seulement apres un assemblage reussi ; rend le chemin final ou `None` + message dans `status`.
- `status() -> dict` : `seconds`, `bytes`, `peak_db`, `dropped`, `encoder`, `warning`.
- Entree audio absente en cours de route : le fil audio ecrit du silence aux instants manquants (duree gardee) et `warning` le dit.

- [ ] **Etape 1 : tests** (faux flux audio, ffmpeg reel) : 3 s d'images synthetiques + audio synthetique -> `ffprobe` : duree 3 s a 0,1 s pres, `h264` `yuv420p` de la bonne taille, `aac` 2 canaux 48000, etiquettes bt709 ; aucun fichier au nom final apres un echec d'assemblage (ffmpeg force a echouer) ; temporaires supprimes apres succes et conserves apres echec ; deux demarrages dans la meme seconde -> deux fichiers ; dossier avec espaces et accents ; arret pendant l'enregistrement ; audio coupe a la moitie -> duree juste + `warning` ; disque presque plein simule (fonction d'espace injectable) -> arret propre, fichier partiel assemble ; images ecrites en retard (`due` rend 3) -> `rec_dropped` = 2 ; **synchro** : clic audio a l'instant t et eclair video a l'instant t, mesure dans le fichier (premiere crete audio, premier changement de luminosite), ecart sous 60 ms ; **sensibilite** : avec le decalage mis a zero alors que l'audio demarre 0,2 s apres la video, le meme test doit echouer ; aucun processus ffmpeg residuel.
- [ ] **Etape 2** : lancer, echec attendu. **Etape 3** : implementer. **Etape 4** : relancer. Commit.

### Tache 5 : integration a la boucle de rendu

**Fichiers** : `engine/projection.py`, `engine/render.py` (si le tampon hors ecran demande un point d'entree), `tools/check_gl.py` (`check_recording_render`, groupe `moteur`).

**Interfaces** : commandes `rec_start` / `rec_stop` lues par la boucle ; touche E ; `s.status["rec"]` = `Recorder.status()` ; `s.recorder`.

- [ ] **Etape 1 : test** (Mesa) : `Projection` ouverte, `rec_start` : la boucle appelle `Renderer.draw` UNE fois par image (compteur) vers un tampon de la taille du format ; la fenetre recoit la copie (pixels non noirs, ratio respecte) ; sans enregistrement, image identique pixel pour pixel a avant ; pixelisation, halo et trainee a 1080x1920 donnent la meme image qu'un rendu de reference de meme taille ; rendu ralenti volontairement -> doublons, duree juste ; fermeture de la projection en cours d'enregistrement -> fichier assemble et message ; relance automatique de la projection -> idem ; fenetre redimensionnee en cours -> fichier inchange.
- [ ] **Etape 2** : echec attendu. **Etape 3** : implementer : cible hors ecran creee a `rec_start` a la taille du format, **multi-echantillonnee 4x puis resolue** vers la texture relue (la fenetre l'est, le tampon du spike ne l'etait pas : sans cela les bords sont moins lisses ; mesurer son cout, le retirer si 30 images/s ne tient plus), `draw` vers elle, relecture asynchrone (deux tampons `read_into`, ecriture de l'image precedente), copie vers `ctx.screen` (`copy_framebuffer`, bandes noires si le ratio differe), arret propre dans le `finally` de `gl_main`. **Etape 4** : relancer, puis `--seulement check_render`, `--seulement check_projection`. Commit.

### Tache 6 : interface

**Fichiers** : `gui/gui_audio.py` (section « Enregistrement »), `gui/gui_palette.py`, `engine/projection.py` (`key_actions` : touche E), `tools/check_gl.py` (`gui_rec_steps` dans le test GUI).

**Contenu** : menus format et cadence, champ dossier + selecteur, curseur « Delai audio », bouton **Enregistrer** / **Arreter** (largeur fixe, rouge pendant l'enregistrement), voyant (`rec_status_text`, mis a jour par `update_master_panel`), commande de palette. Bouton grise avec message si projection fermee, entree audio absente (hors tests : un faux `audio_input`) ou ffmpeg absent.

- [ ] **Etape 1 : test** : bouton grise dans chaque cas avec son message ; clic -> `rec_start` envoye, texte du bouton change sans decaler les voisins ; voyant relu ; reglages sauves (`gl_params.json`) et absents des presets et scenes ; la PANIQUE n'arrete pas l'enregistrement ; skin et info-bulles dans la fenetre (`tooltip_violations`) ; fenetre sous 700 px.
- [ ] **Etape 2** : echec attendu. **Etape 3** : implementer. **Etape 4** : relancer `--seulement check_gui`. Commit.

### Tache 7 : documentation, portable, mesures

**Fichiers** : `CLAUDE.md` (nouvelle section « Enregistrement »), `tools/build_portable.py`, `tools/check_gl.py`.

- [ ] **Etape 1** : ajouter `engine/recorder.py` aux fichiers du portable si `check_portable_modules` le demande ; lancer la verification.
- [ ] **Etape 2** : section de `CLAUDE.md` (decisions, pieges : un seul `draw` par image, synchro, assemblage, « non verifie »).
- [ ] **Etape 3** : suite complete (`--pur`, puis en arriere-plan toute la suite) ; mesures sur le poste de dev (images tenues a 30 et 60, encodeur choisi, CPU) ecrites dans la PR.
- [ ] **Etape 4** : PR (`Closes #140` seulement apres validation de l'utilisateur sur le materiel : l'issue passe en « A tester »).

## Execution

Seule la tache 1 peut mener a un arret de la spec ; les taches 2 a 7 se suivent. **Non verifie par la suite** : vraie carte son, GPU reel, delai audio de 40 ms, acceptation par TikTok / Instagram.
