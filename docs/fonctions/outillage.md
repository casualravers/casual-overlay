# Outillage (diagnostic, portable, verifications, CI, mesures)

Fiche de [CLAUDE.md](../../CLAUDE.md) (issue #132 : detail deplace tel quel). Fichiers : engine/diagnostics.py, tools/build_portable.py, tools/check_gl.py, .github/workflows/check.yml, start.bat.

- **Version portable** : `build_portable.APP_FILES` inclut tous les `gui_*.py` (glob) ; `check_portable_modules` verifie qu'aucun module du depot
  importe par le code embarque ne manque (le script de fabrication ne lance pas la GUI : un oubli ne se verrait que sur le PC de la soiree).

## Diagnostic de demarrage et version portable

- **`engine/diagnostics.py`** (stdlib seulement, importe par `audio2wave_gl`) : `run_checks(a2w_dir, synthetic, gui, opengl, ...)` rend des
  `Check(name, level, detail, hint)` (`ok` / `warn` / `fail`) : Python (>= 3.10), modules (obligatoires = fail ; tkinter fail avec
  `--gui` sinon warn ; tkinterdnd2 warn ; sounddevice warn en `--synthetic`), ffmpeg / DirectShow / ffprobe (fail sauf
  `--synthetic`), audio2wave (fail sauf synthetique sans GUI, comme `load_live`), **OpenGL** (`moderngl.create_standalone_context()`,
  `version_code` >= 330 ; rendu logiciel = warn, sauté si moderngl manque), micro (`sounddevice.query_devices`), dossier des reglages
  inscriptible. **Tout est injectable** (`import_module`, `which`, `run`, `create_context`, `sd`, `settings_dir`, `python_version`) :
  les tests simulent un PC sans ffmpeg / OpenGL / micro. `format_report` (rapport, `only_problems` pour les manques), `summary_line`
  (une ligne pour la GUI), `preflight` (`(checks, ok)`), `run_cli` (`--diagnostic`, code 0 / 1).
- **Au lancement** (`main`) : `--diagnostic` rapporte et quitte ; sinon (hors `--dry-run` / `--list-*`) `preflight` s'execute, les
  manques sont imprimes sur stderr, **une erreur arrete le programme (code 1) avec un message clair au lieu d'une trace**,
  `args.diag_checks` garde le resultat. GUI : `scene_msg` affiche une fois `summary_line` ; bouton **Diagnostic** (section *Affichage et analyse*,
  `diagnostic_report`) = verifications refaites sauf OpenGL (resultat du demarrage : pas de second contexte GL pendant le rendu),
  dans `choice_dialog`.
- **Version portable** (`build_portable.py`, stdlib, Windows) : un dossier `dist/casual-overlay-portable/` = `python/` (copie de
  l'installation Python de base du venv, sans Doc / tests / pip, avec tcl/tk, + `site-packages` du `.venv`), les `.py`, `gl_shaders/`,
  `asset/` (fichiers du dossier et de `asset/images/`, ni `animations/` ni `videos/` perso), `audio2wave/` (leurs `*.py`, copie de `sources/audio2wave/`), `ffmpeg/` (ffmpeg.exe +
  ffprobe.exe), `start.bat`, `LISEZMOI.txt`. **Pas de gel (PyInstaller)** : audio2wave est importe dynamiquement et tkinter /
  modules varient selon le mode. `audio2wave_gl.bundled_paths(script_dir, path)` : `audio2wave/` a cote du programme l'emporte sur le
  copie interne (`DEFAULT_A2W_DIR`), `ffmpeg/` est ajoute en tete du `PATH` (donc `shutil.which`, audio2wave et nos sous-processus le
  trouvent). `start.bat` prend `python\python.exe` puis `.venv\Scripts\python.exe`. Le script **verifie le dossier fabrique** avec
  son propre Python (diagnostic + rendu synthetique de 3 s). Verifie a la main : lance avec un PATH reduit a `C:\Windows\System32`,
  tout est trouve dans le dossier (python, modules, ffmpeg, audio2wave), OpenGL et micro detectes. Taille ~520 Mo dont 424 Mo de
  ffmpeg « full » (un build « essentials » est plus leger ; licence GPL du build : ne pas redistribuer publiquement sans la respecter).
  Les reglages restent dans `~/.audio2wave` (pas dans le dossier). `dist/` est dans `.gitignore`.
- `check_gl.py` (`check_diagnostics`) : chaque verification avec des fausses sources, rapport, resume, `preflight`, `--diagnostic`
  en vrai (ce PC, PC sans ffmpeg = code 1, synthetique = avertissement, audio2wave absent), demarrage sans ffmpeg = arret propre sans
  trace, `bundled_paths`, assemblage du dossier portable (sans les animations perso), `copy_tree`.

## Verifications

`python tools/check_gl.py` (aucun peripherique requis) : lecteur de frames avec faux producteur
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
**Integration continue** (`.github/workflows/check.yml`, GitHub Actions, Windows ; `main` est protegee : une PR doit avoir les trois jobs verts) :
- `pur` (~40 s) : `python tools/check_gl.py --pur` (lecteur, features audio, scenes, schema des reglages, purete de `core/`, couches, portable, grille du temps, planning, palette... ; ni GPU, ni micro, ni ffmpeg). `PURES` dans `check_gl.py` en est la liste : y ajouter toute nouvelle verification qui ne demande rien de materiel.
- `complet` (4 jobs en parallele, **~8 a 12 min vises** ; avant le decoupage la suite sequentielle faisait 24 min) : TOUTE la suite sur le serveur, avec **Mesa llvmpipe** (OpenGL logiciel : `opengl32.dll` + `libgallium_wgl.dll` copies dans le dossier de Python par l'action `.github/actions/mesa`, version epinglee) et ffmpeg (choco). Le journal de chaque groupe est un artefact (`journaux-verifications-<groupe>`) et son tableau des durees est dans le resume du job.
  **Groupes et options de `check_gl.py`** (spec `docs/plans/2026-11-13-optimiser-tests-et-flux-design.md`, issue #121) : `SUITE` = toutes les verifications dans l'ordre d'execution ; `GROUPES` = `labyrinthe`, `moteur` (GL hors GUI), `gui` (dont `check_gui`) ; tout `check_*` absent de `GROUPES` va dans le groupe `reste` (une verification ajoutee n'est jamais oubliee). **Ajouter une verification** : son nom dans `SUITE` (`check_groupes` echoue sinon : elle ne tournerait jamais en CI), et dans `PURES` si elle ne demande rien de materiel. Options : `--groupe NOM`, `--seulement MOTIF` (sous-chaine du nom, repetable ; un motif sans correspondance est une erreur), `--sans-pur` (saute celles de `PURES`, deja passees par le job `pur`), `--duree` (tableau complet des durees ; la fin de chaque run imprime les 14 plus longues). Un groupe se lance seul en local et passe seul (`ENV_AVANT` pose les variables d'environnement qu'une verification demande, ex. `check_gui`). Les durees servent de budget : la famille labyrinthe etait de ~590 s sur 1440.
  **Quand ca tourne** : sur chaque PR (`pur`, `portable`, les 4 groupes) ; sur un push de `main` seulement `pur` et `portable` (la PR vient de passer la meme suite) ; `complet` en entier **chaque nuit** sur `main` (cron) et a la demande (`workflow_dispatch`).
  Deux garde-fous contre les faux echecs, dans `check_gl.py` : (1) **`FRAGILES`** = tests de mise en page de la fenetre qui echouent parfois seuls (etat de l'ecran, focus, minuteurs de Tk) ; si TOUS les echecs d'une execution en font partie le programme sort avec le **code 3**, ecrit les verifications concernees dans `CHECK_RERUN_FILE` et le workflow relance **ces seules verifications** (`--seulement`, pas tout le groupe ; un echec qui revient reste un echec) ;
  (2) **`SCREEN_DEPENDENT`** = tests qui supposent un grand ecran (mise en page large, fenetre sous 700 px...) : sur un ecran de moins de 1500 x 900 (le serveur est en 1024 x 768) ils sont ecartes, avec la liste affichee. Ajouter un test de mise en page : l'inscrire dans l'une des deux listes s'il en depend.
  Le job ecrit aussi la **latence logicielle** (`tools/mesure_latence.py`) dans le resume du run : indicatif, jamais un seuil (serveur partage).
- `portable` (~1 min) : `tools/build_portable.py --no-ffmpeg` dans un venv neuf, qui verifie le dossier fabrique (diagnostic + rendu synthetique de 3 s avec SON Python) : un module oublie dans `APP_FILES` s'y voit.
Piege rencontre : rejouer `check_gui()` dans le meme processus bloque (fenetre Tk et fils d'audio de la premiere passe) et la partie GUI n'est pas autonome (elle depend de l'etat laisse par les verifications d'avant) : on relance donc toute la suite (ou son groupe, `--groupe gui`), jamais un morceau de `check_gui` ; les autres verifications se relancent seules (`--seulement`).
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

## Hors POC

Presets combines live + overlay (les deux se chargent separement), automation des reglages live via le moteur GL (ils gardent celle d'audio2wave, qui
redemarre ffmpeg), MIDI,
sortie Spout/NDI, plusieurs logos a la fois, synchro de la lecture du logo anime sur le kick.

Lanceur : [start.bat](../../start.bat) (demande explicite, malgre le "pas de .bat" du plan d'origine) : va dans
son dossier, utilise `python\python.exe` puis `.venv\Scripts\python.exe` ; s'ils manquent il **propose (O/N) de creer `.venv`** (`py -3.12 -m venv` + `pip install -r requirements-gl.txt`, Python 3.12 par winget si besoin, `.venv` retire si pip echoue) et, si ffmpeg manque (PATH ou `ffmpeg\`), **de l'installer par winget** (`Gyan.FFmpeg`, source `winget` : `msstore` a echoue sur un certificat) puis de copier `ffmpeg.exe` / `ffprobe.exe` dans `ffmpeg\` (le PATH de la console n'est pas mis a jour par winget) ; ce controle est saute avec `--synthetic`, `--diagnostic` et `--list-*`. Le fichier doit rester en CRLF (etiquettes `goto`). Verifie par essai dans un dossier temporaire (refus, `.venv` present, `ffmpeg\` present, arguments a parentheses) ; **jamais essaye avec une vraie installation**. Lance
`audio2wave_gl.py --gui %*` (arguments transmis, donc `-d`, `--text`, `--synthetic`... marchent) et garde la
console ouverte (`pause`) si le programme sort avec un code d'erreur. Fichier en ASCII + CRLF (accents
interdits, comme le reste). Une sortie anormale isolee (code -1073740022) a ete vue une fois au tout premier
lancement, non reproduite sur 17 lancements suivants.
