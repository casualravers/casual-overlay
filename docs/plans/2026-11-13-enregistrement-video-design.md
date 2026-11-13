# Enregistrement video + audio (issue #140)

Statut : **spec validee par l'utilisateur le 2026-11-13** (cadence par defaut : 30 images/s). Plan : `2026-11-13-enregistrement-video-plan.md`.

## Objectif

Un bouton **Enregistrer** qui produit un fichier **MP4 pret a poster** (TikTok, Instagram Reels) : l'image rendue par casual-overlay et le son de la carte son, en qualite complete. Usage : **a la maison, pour creer du contenu**. Ce n'est pas une fonction de soiree : pas de contrainte de charge GPU en spectacle, pas de risque pour la projection.

Le montage se limite a **couper le debut et la fin** et a **etalonner**, dans le logiciel de l'utilisateur : aucun format « montage » (pas de MOV intermediaire, pas de WAV a part).

## Hors perimetre

- Tampon de rejeu des N dernieres secondes (utile en soiree, pas ici).
- Rendu hors ligne depuis un fichier audio (image par image, sans temps reel) : autre chantier, plus lourd (horloge injectable dans tout le moteur). A rouvrir si le temps reel ne suffit pas.
- Publication automatique (planificateur externe, voir la discussion de l'issue).
- Coupe ou etalonnage dans l'appli.

## Decisions

| Sujet | Decision |
|---|---|
| Sortie | un MP4 : H.264 `yuv420p` + AAC 320 kb/s stereo 48 kHz, `faststart`, etiquettes bt709 (primaires, transfert, matrice) pour que l'etalonnage parte d'une base connue ; une image cle par seconde (`-g` = fps) pour couper sans re-encoder |
| Formats | `vertical` 1080x1920 (defaut), `vertical_hd` 720x1280 (pour les looks lourds : cellules, cumul d'effets), `horizontal` 1920x1080, `carre` 1080x1080 ; cadence 30 (defaut) ou 60 images/s (60 deconseille a 1080x1920 : ne tient pas sur le poste de dev) |
| Audio | l'entree d'analyse choisie dans l'appli, enregistree **brute** : stereo, frequence native du peripherique, **sans auto-gain** ni mixage mono |
| Rendu | passe unique dans un tampon hors ecran a la taille du format (voir *Rendu*) |
| Dossier | `rec_dir`, defaut `~/Videos/casual-overlay/`, fichiers `casual-AAAA-MM-JJ_HHhMMmSS.mp4` |
| Commande | bouton **Enregistrer / Arreter** (onglet *Affichage*, section « Enregistrement »), touche **E** dans la fenetre de rendu, commande de la palette Ctrl+K |
| Portee des reglages | `device` (sauves, hors presets et scenes) ; la PANIQUE n'y touche pas ; un enregistrement en cours n'est jamais interrompu par un changement de scene |

## Conception

### Audio (`engine/audio.py`)

`SoundDeviceInput._callback` mixe en mono pour l'analyse et ne garde que ca. On ajoute un **point d'ecoute** (`tap`, `None` par defaut) : tant qu'il est pose, le callback y pousse une **copie** du bloc stereo `float32` et son instant de capture (`now - capture_age`, le meme que pour le tampon d'analyse) dans une file ; aucune ecriture disque ni allocation lourde dans le callback. Un fil d'ecriture range les blocs dans un fichier temporaire brut `f32le` et note l'instant du **premier echantillon** (`t_audio0`). Hors enregistrement : aucun cout, le comportement actuel est inchange.

Limites assumees : on prend les deux premiers canaux ; WASAPI partage peut re-echantillonner a la frequence du mixeur Windows (le mode exclusif ou ASIO n'est pas pris en charge dans cette version).

**Indicateur de crete** : crete en dBFS depuis le debut de l'enregistrement, rouge si l'audio a sature (l'enregistrement est brut : un niveau trop fort ne se rattrape pas).

### Rendu (`engine/projection.py`, `engine/render.py`)

`Renderer.draw(target, size, state, params, t)` accepte deja une cible et une taille. **Piege** : `draw` integre ses phases animees image par image (fond, halo, fonte, bruit...), donc l'appeler **deux fois** par image (fenetre + enregistrement) doublerait toutes les animations. Pendant l'enregistrement la boucle appelle donc `draw` **une seule fois**, vers le tampon hors ecran a la taille du format, puis **copie ce tampon dans la fenetre** (reduit, avec bandes noires si le ratio differe) : la fenetre devient un moniteur de l'enregistrement. Hors enregistrement, rien ne change.

A verifier au premier jalon (spike) : les effets qui dependent de la taille en pixels (pixelisation, tailles de grain, halo) se comportent comme a l'ecran (il existe deja des tests « independant de la resolution » pour le halo) ; la trainee et les transitions fonctionnent a une taille differente de la fenetre.

### Images

A chaque image affichee, le fil GL decide s'il faut envoyer une image a l'encodeur : `core.recording.frames_due(t0, now, fps, sent)` rend le nombre d'images a ecrire pour que l'image numero k corresponde a l'instant `t0 + k / fps`. Si le rendu a pris du retard, l'image precedente est **dupliquee** (la duree reste juste, le compteur `rec_dropped` augmente) ; si le rendu est en avance, rien n'est ecrit. La video est donc **a cadence constante** par construction, ce qui rend la synchro avec l'audio deterministe.

Relecture : `fbo.read` bloque le GPU ; on lit dans deux tampons GL en alternance (relecture asynchrone, `read_into`) et on envoie l'image de l'image precedente. A mesurer sur le poste de dev (GPU integre) : ~500 Mo/s a 1080x1920 en 60 images/s, probablement raisonnable a 30.

### Encodage

Un processus `ffmpeg` recoit les images brutes (`rgb24`, premiere ligne en bas : `vflip`) sur son entree standard et ecrit la **video seule** dans un fichier temporaire. Encodeur par defaut : `libx264` (`-preset veryfast -crf 17`) ; le spike a montre que le GPU est le goulot et que x264 tient 80 images/s pour ~19 % du CPU. Option `rec_encoder` = `materiel` : `core.recording.choose_encoder(disponibles, testes, hardware=True)` prend `h264_nvenc`, `h264_amf` ou `h264_qsv` s'il est liste par `ffmpeg -encoders` **et** qu'un essai d'une image reussit (un encodeur liste peut echouer sans le materiel), sinon retombe sur `libx264`. L'encodeur retenu est dit dans le statut.

### Assemblage a l'arret

A l'arret, `ffmpeg` assemble les deux fichiers temporaires : video recopiee (`-c copy`), audio encode en AAC, **decalage de l'audio** `audio_offset = (t_audio0 - t_video0) + rec_delay_ms`. Le fichier final est ecrit sous un nom provisoire puis renomme (jamais de MP4 a moitie ecrit sous le bon nom) ; les temporaires sont supprimes seulement apres un assemblage reussi.

**Synchro son / image** : le visuel reagit **apres** le son (capture, analyse, rendu : de l'ordre de 16 a 45 ms, voir la section latence de CLAUDE.md). Pour que l'effet tombe sur le son dans le fichier, l'audio est retarde de `rec_delay_ms` (reglage `device`, 0 a 300 ms, **defaut 40 ms**, a regler avec `tools/calibrer_avance.py` ou a l'oeil). Valeur par defaut non mesuree sur le materiel de l'utilisateur.

### Interface

Section « Enregistrement » de l'onglet *Affichage* : format, cadence, dossier (champ + selecteur), delai audio, bouton **Enregistrer** (rouge) devenant **Arreter**, voyant « ● 01:23 | 412 Mo | crete -3 dBFS | images dupliquees 0 », et le nom de l'encodeur. Largeur fixe du bouton et emplacement reserve du voyant (regle « les boutons ne bougent pas »). Le bouton est **grise avec un message** si la projection est fermee (rien a enregistrer), si aucune entree audio n'est choisie, ou si ffmpeg manque.

### Erreurs et cas limites

- **Espace disque** : a l'ouverture, avertissement sous 2 Go libres ; arret propre si le disque se remplit (le fichier deja ecrit est assemble).
- **Projection fermee ou relancee** pendant l'enregistrement : arret propre et assemblage de ce qui existe, message dit.
- **Entree audio qui disparait** (carte debranchee) : l'enregistrement continue en video, un silence est insere pour garder la duree, message dit.
- **Plus de 2 % d'images dupliquees** : message « le GPU n'a pas suivi : baisse la cadence ou le format ».
- **Plantage de l'appli** : les temporaires restent sur le disque avec leur nom de base ; le journal de session (`sessionlog`) dit quels fichiers sont a recuperer. Pas d'outil de reprise dans cette version.
- **Debit** : a titre indicatif, `crf 17` donne de l'ordre de 20 a 40 Mb/s sur ces visuels (environ 1 Go pour 5 minutes) ; non mesure.

## Structure du code

- `core/recording.py` (stdlib, **TDD**, dans `PURES`) : `FORMATS`, `frames_due`, `audio_offset`, `output_name`, `video_command` / `mux_command` (listes d'arguments ffmpeg), `choose_encoder`, `peak_db`, formatage du voyant (`explain`). Reglages ajoutes a `core/params_schema.py` : `rec_format`, `rec_fps`, `rec_dir`, `rec_delay_ms` (portee `device`).
- `engine/recorder.py` : `Recorder` (fil d'ecriture audio, fil d'ecriture video, processus ffmpeg, assemblage), `Session.recorder`, commandes `rec_start` / `rec_stop` dans `Session.commands`.
- `engine/projection.py` : cible hors ecran, relecture asynchrone, copie vers la fenetre, touche E.
- `gui/gui_audio.py` : section « Enregistrement » ; `gui/gui_palette.py` : commande.
- `tools/build_portable.py` : `engine/recorder.py` et `core/recording.py` dans le dossier portable (`check_portable_modules`).

## Verifications (`tools/check_gl.py`)

**Pures** (`check_recording`, dans `PURES`) : `frames_due` (retard = doublon, avance = rien, jamais de retour en arriere, `fps` 30 et 60), `audio_offset` (signes, delai), `output_name`, commandes ffmpeg (formats, encodeur, etiquettes bt709, `faststart`, image cle), `choose_encoder` (liste sans essai reussi = `libx264`), `peak_db`, valeurs hors plage des reglages.

**Rendu et ffmpeg reels** (groupe `moteur`, Mesa llvmpipe et ffmpeg de la CI) : enregistrer 3 s d'un fond de test et d'un audio synthetique (clics a instants connus, eclair visible), puis `ffprobe` : duree 3 s a 0,1 s pres, flux `h264 yuv420p` de la bonne taille, `aac` stereo 48 kHz, etiquettes bt709 ; **synchro** : l'ecart entre le premier clic et le premier eclair, mesure dans le fichier, reste sous 60 ms (calcul de l'ecart par `tools/`, pas a l'oeil) ; rendu lent simule (images volontairement retardees) : duree juste, `rec_dropped` > 0 ; arret pendant l'enregistrement ; projection fermee ; entree audio absente ; fichier sous nom provisoire jamais visible apres un echec ; aucun processus ffmpeg residuel. Sensibilite : sans le decalage d'audio, le test de synchro doit echouer.

**GUI** : bouton grise sans projection / sans entree, voyant, format et cadence pris en compte.

**Non verifie (demande le materiel)** : enregistrement avec la vraie carte son, GPU reel de l'utilisateur (images dupliquees a 30 et 60 images/s, encodeur materiel), delai audio par defaut, acceptation et re-encodage par TikTok / Instagram, niveau sonore des plateformes.

## Questions ouvertes

1. ~~Cadence par defaut~~ : **30 images/s** (decide), 60 en option.
2. ~~Plein ecran pendant l'enregistrement~~ : possible, la fenetre affiche la copie du tampon, sans autre reglage (propose, non conteste).
3. Delai audio par defaut a 40 ms : a confirmer apres une premiere mesure sur le materiel de l'utilisateur (`tools/calibrer_avance.py`).

## Resultats du spike (2026-11-13)

Voir le plan (`2026-11-13-enregistrement-video-plan.md`, tache 1). En bref : 30 images/s tient a 1080x1920 pour les looks courants ; le rendu GPU est le goulot (cellules : 25 ms, tous effets couteux : 35 ms soit 27 images/s) ; relecture et encodage ne le sont pas ; 720x1280 tient 30 images/s avec tous les effets. Consequences : format `vertical_hd` ajoute, encodeur `libx264` par defaut, 60 images/s deconseille a 1080x1920, tampon hors ecran a multi-echantillonner (a mesurer).

## Jalons proposes

1. ~~**Spike** (jetable)~~ : fait, faisable a 30 images/s.
2. `core/recording.py` + tests purs.
3. Audio : point d'ecoute et fichier brut ; assemblage ; test de synchro.
4. Interface (bouton, voyant, touche, palette) et cas limites.
5. Mesures sur le poste de l'utilisateur, avec les chiffres dans le plan.
