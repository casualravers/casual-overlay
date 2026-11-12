# Synchro son / image : retard, detection du kick, respiration du visuel (spec)

Issues : **#110** (moins de latence entre le son et l'effet) et **#96** (le kick doit imposer le rythme de la projection). Liees : #97 (effets par bande
de frequence), #85 (animations sur 1 temps sur N), #105 (apprentissage supervise des phases). Statut : **spec a valider**, rien n'est code.

## 1. Ce que veut l'utilisateur

Constat (usage reel, micro du portable, projecteur) : **les effets overlay (kick, strobe, logo) arrivent trop tard pour etre associes au son entendu**,
le kick est mal detecte (trop ou pas assez), et le visuel ne « respire » pas avec la musique. Ordre voulu : (1) le retard, (2) la fiabilite du kick,
(3) une analyse temps reel qui pilote le visuel de facon continue, jusque dans les nuances. Le test final est la musique reelle en soiree.

## 2. Diagnostic : d'ou viennent les millisecondes

Chaine actuelle d'un kick, du haut-parleur a l'oeil (les valeurs marquees « mesure » viennent de `tools/mesure_latence.py`, les autres sont a mesurer) :

| Etape | Ordre de grandeur | Source |
|---|---|---|
| Son de la salle -> micro du portable | distance / 343 m/s (3 m = 9 ms, 15 m = 44 ms) | acoustique |
| Micro integre + pilote (« ameliorations audio » Windows, WASAPI partage) | 10 a 40 ms, **inconnu** | a mesurer |
| Bloc de capture (512 echantillons) | ~11 ms ; `latency="low"` ~22 ms rapportes | mesure |
| Fenetre d'analyse 2048 (Hann, ~43 ms) : l'energie du kick n'atteint son pic que quand l'attaque est au centre de la fenetre | ~20 a 25 ms (analyse mesuree : ~18 ms mediane) | mesure |
| Fil d'analyse -> fil GL (lecture a la prochaine image) | 0 a 16,7 ms | mesure (rendu ~16 ms) |
| Affichage : balayage de l'ecran + traitement du videoprojecteur | 30 a 100 ms et plus, **inconnu** | hors mesure aujourd'hui |

Le chemin **logiciel** pese donc ~30 a 45 ms (mediane 29 ms, pire cas 44 ms mesures). Ce qui reste (pilote du micro, projecteur) est probablement **plus
gros que tout le logiciel**, et on ne peut pas le reduire en accelerant le code : on peut seulement l'**anticiper**.

Defauts reperes a la lecture du code, a confirmer par le banc (lot 1) :
- `AudioAnalyzer._run` date chaque kick par `time.monotonic()` **au moment du traitement**, pas au moment ou le son a ete capture : le retard de capture
  et la gigue d'ordonnancement du fil entrent dans l'estimation du BPM et dans la grille (`Rhythm`, `BeatGrid`).
- Le kick est detecte sur une energie de bande FFT (fenetre 2048) : bonne pour la fiabilite, mauvaise pour la precision temporelle.
- `beat` (enveloppe `exp(-t/0,2 s)`) ne part qu'apres la detection : le visuel **reagit**, il n'anticipe jamais. Avec un retard fixe de l'affichage,
  c'est la cause structurelle de « ca arrive apres ».
- Le BPM et la grille existent (`core/rhythm.py`, `Rhythm.grid`, `beat_index`) mais ne servent qu'au strobe auto, au VJ en rythme et aux phases ;
  les effets sur kick (onde de choc, aberration, pulsation, foudre, flash) suivent uniquement le kick detecte (#96 : « beaucoup de choses suivent le
  temps ou une horloge propre, pas le kick »).

## 3. Principes

1. **Mesurer avant de corriger** : un outil donne le retard reel de bout en bout (projecteur compris) et un banc donne la qualite du detecteur. Toute
   retouche de l'analyse est jugee par ces deux mesures, pas a l'oreille.
2. **Deux leviers distincts** : reduire ce qui peut l'etre (logiciel), **compenser** le reste par une avance reglable (prediction de la pulsation).
3. **Le rythme est une horloge, pas une impulsion** : un oscillateur de pulsation (phase 0..1 calee sur les kicks) pilote les effets ; un kick manque
   ou en trop ne le decale pas, l'absence de kick ne le fige pas (il tourne librement puis s'efface).
4. **Jamais de pire visuel qu'avant** : sans tempo fiable, retour au comportement reactif actuel ; l'anticipation ne remplace jamais la detection reelle
   (cassure, drop, musique irreguliere).
5. **Rien de nouveau a regler en salle** hormis l'**Avance** (ms), remplie par la calibration.

## 4. Lots (un lot = une PR, dans cet ordre)

### Lot 1 : mesure (sans changer le comportement)

- **Horodatage a la capture** : le callback `SoundDeviceInput` recoit `time_info.inputBufferAdcTime` ; le `RingBuffer` garde le couple
  (echantillons ecrits, instant d'acquisition). L'analyse date un kick par l'instant **audio** (position dans le tampon -> temps horloge), plus par
  l'heure de traitement. `AudioAnalyzer.latest()` publie aussi l'age du dernier echantillon analyse (retard de capture + analyse), pour le HUD.
- **Calibration a la camera** (`tools/calibrer_avance.py` ou bouton de l'onglet *Affichage*) : l'application joue un clic sonore et affiche un flash blanc
  plein ecran **au meme instant logiciel**, a 1 Hz, avec un decalage connu et variable ; l'utilisateur filme au telephone (120 ou 240 images/s) l'ecran
  et le haut-parleur ; le script lit les instants image par image (detection du flash par luminance, du clic par audio de la video) et rend le retard
  **son -> lumiere** moyen et sa dispersion. Variante sans camera : `--materiel` de `tools/mesure_latence.py` (haut-parleur contre le micro).
- Le retard rendu est l'**Avance recommandee** (lot 4). Il separe aussi, par soustraction avec la mesure logicielle, la part « ecran / projecteur ».
- Verification des « ameliorations audio » Windows : le diagnostic au demarrage (`engine/diagnostics.py`) signale si l'entree est en mode partage et
  conseille de couper les effets du micro (suppression de bruit, annulation d'echo, egaliseur automatique) quand le pilote l'expose ; sinon simple note.

### Lot 2 : banc du detecteur de kick (#96)

- `tools/bench_kick.py` : rejoue un fichier audio (WAV ; MP3 si decodable par ffmpeg) dans le vrai `FeatureExtractor` / `AudioAnalyzer` **a vitesse
  simulee** (horloge injectee, pas de temps reel) et compare les kicks detectes a une **verite terrain**.
- **Verite terrain** : fichier `.kicks.json` a cote de l'audio (instants des kicks en secondes). Deux sources : annotation assistee
  (`tools/annoter_kicks.py` : pre-annote avec le detecteur, l'utilisateur corrige en tapant au clavier au fil de l'ecoute) ou signaux synthetiques dont
  les kicks sont connus (corpus existant de `check_features`, etendu : kick sur basse continue, kick sature de telephone, house / techno a 120-135 BPM,
  breakbeat).
- **Metriques** : precision, rappel, F1 ; erreur de position (mediane, p95) ; kicks doubles ; faux kicks par minute sur passage sans kick ; resultat par
  morceau et par sensibilite. Le rapport sort un tableau et un fichier JSON comparable d'une version a l'autre.
- Le banc entre dans `check_gl.py` pour la partie synthetique (seuils de non-regression) ; les enregistrements reels de l'utilisateur restent **hors
  depot** (droits, poids) : `tools/bench_kick.py --dossier <chemin>`.
- Sortie attendue du lot : un **diagnostic chiffre** (le kick est-il manque ? en trop ? mal date ?) qui decide du contenu du lot 3, au lieu d'une
  intuition.

### Lot 3 : reduire le retard logiciel et ameliorer la datation (#110)

Pistes, retenues ou ecartees selon le banc (lots 1 et 2) :
1. **Datation exacte** (lot 1) : gain de precision sur BPM et grille, pas de retard supprime.
2. **Detecteur d'attaque rapide** sur la bande des basses (filtre temporel et enveloppe sur ~5 ms, sans attendre la fenetre FFT) qui declare l'attaque,
   la FFT 2048 gardant le role de confirmation et de mesure de niveau. Vise : detection ~5 a 10 ms apres le debut du kick au lieu de ~25 ms.
3. Pas d'analyse plus court (hop 5,3 ms au lieu de 10,7 ms) si le cout CPU le permet (mesure a l'appui).
4. Bloc de capture 256 ; essai WASAPI en mode exclusif / faible latence si l'entree le propose (a mesurer sur le portable de l'utilisateur).
5. Lecture de l'etat par le fil GL : publier l'etat le plus frais a chaque image (pas de copie perimee), eviter un verrou long.
Critere : la **latence logicielle mediane** descend sous ~20 ms (mesure interne de `mesure_latence.py`), sans degrader le F1 du lot 2 de plus de 1 point.

### Lot 4 : pulsation et avance (le coeur du #96)

- **Horloge de pulsation** (`core/pulse.py`, pur, temps injecte) : boucle a verrouillage de phase calee sur les kicks fiables et le BPM detecte
  (`Rhythm.bpm`, `BeatGrid`) ; sortie : phase 0..1, periode, **confiance** 0..1, prochain instant predit. Un kick detecte recale la phase partiellement ;
  un kick manque ne la decale pas ; un kick en trop (hors fenetre autour de la phase attendue) est ignore pour la phase ; sans kick, elle tourne
  quelques mesures puis la confiance retombe a 0. Le « Temps 1 » manuel ancre toujours la mesure.
- **Avance** (reglage de salle `sync_lead_ms`, 0 par defaut, 0 a 200 ms ; schema, GUI dans l'onglet *Affichage*, bouton « Calibrer ») : un effet « sur
  kick » est declenche a l'instant `kick_predit - avance` quand la confiance depasse un seuil, sinon sur la detection reelle comme aujourd'hui. Quand
  la prediction et la detection reelle tombent dans la meme fenetre, un seul declenchement (pas de double eclair).
- **Ce qui est cale sur la pulsation** : enveloppe `beat` (onde de choc, aberration, pulsation du logo), strobe auto et foudre, cycles lies au temps (fonte,
  cellules, respiration) en option de phase, VJ en rythme (deja sur la grille). Les effets sur frappes aigues (charleys) et les phases (montee, cassure,
  drop) restent **reactifs** : la prediction n'a pas de sens la.
- **Garde-fous** : confiance basse, cassure, montee ou drop detectes -> comportement reactif ; limiteur de strobe inchange ; PANIQUE coupe l'avance.
  Indicateur dans la GUI (voyant « pulsation calee / libre / perdue » et ecart moyen prediction - detection, en ms).

### Lot 5 : analyse temps reel pour la « respiration » (#96, #97, #85)

- **Enveloppes continues par bande** (6 a 8 bandes, de la sub-basse aux aigus) avec attaque et relachement propres a chaque bande, plus la derivee
  (montee) : le visuel suit le corps du son, pas seulement l'impact. `levels` actuel (3 bandes + rms) reste disponible.
- **Pulsation exposee aux effets** : phase de pulsation et sous-divisions (1/2, 1/4, 1/8 de temps ; 1 temps sur N, 8 / 16 / 32 temps : #85) utilisables
  comme source d'un effet ou d'un cycle, a cote des sources `basses`, `kick`, `frappes` (menu des couches ; #97 : un effet par bande).
- **Respiration** : la presence de l'overlay, la luminosite et la derive du fond peuvent suivre la phase de pulsation (courbe douce, pas un
  clignotement) et l'enveloppe des basses ; reglage unique d'amplitude, sans multiplier les curseurs.
- Peut s'appuyer, plus tard, sur #105 (phases apprises) ; hors de ce lot.

## 5. Criteres de reussite

| Mesure | Cible | Outil |
|---|---|---|
| Latence logicielle (analyse + rendu), mediane | < 20 ms (29 ms aujourd'hui) | `mesure_latence.py` |
| Retard bout en bout compense par l'avance | residu < +/-15 ms, controle a la camera | calibration (lot 1) |
| F1 du kick sur corpus synthetique | >= 0,97 (0,98 aujourd'hui sur le corpus d'origine) | `bench_kick.py` |
| F1 du kick sur les enregistrements de l'utilisateur | mesure d'abord ; cible fixee apres le premier rapport (probablement >= 0,92) | `bench_kick.py` |
| Erreur de datation du kick, mediane | < 10 ms | `bench_kick.py` |
| Faux kicks sur passage sans kick | < 1 par minute | `bench_kick.py` |
| Pulsation calee apres | < 8 s de musique reguliere ; reste calee a travers un kick manque ; libre sans kick | `core/pulse.py` (pur, dans `PURES`) |
| Regressions | suite `check_gl.py` inchangee (hors les 3 echecs connus du poste de dev) | CI |

## 6. Ce qui ne se verifie pas sans materiel (a dire « non verifie » jusqu'au test de l'utilisateur)

Retard reel du projecteur, pilote du micro du portable, acoustique de la salle, sensation de « respiration » avec la musique reelle : le test final est
la soiree. Chaque PR dit ce qui a ete mesure (synthetique, CI) et ce qui attend l'utilisateur.

## 7. Risques et limites

- **L'anticipation exige une musique reguliere** : house, techno, la plupart des sets ; pas pour un morceau tres irregulier ou live. D'ou le repli
  reactif et la confiance affichee.
- **Un mauvais BPM detecte** (demi-temps, double temps) calerait l'horloge au mauvais pas : la grille manuelle (« Temps 1 ») et la verification contre les
  kicks reels limitent le risque ; a surveiller dans le banc (lot 2).
- **Un detecteur d'attaque plus rapide detecte plus de faux kicks** : l'attaque rapide ne declare qu'avec la confirmation de la FFT ; le banc tranche.
- **Calibration = un retard fixe** : si le projecteur change, il faut recalibrer (le reglage est de salle, pas de scene).
- Cout CPU de l'analyse a hop plus court et de la pulsation : mesure a l'appui (cible : < 3 % d'un coeur).

## 8. Hors perimetre

Detection de la tonalite ou des voix, separation de sources, MIDI / Ableton Link comme source de tempo, apprentissage automatique du kick (#105 reste une
idee a part), changement du fond ffmpeg `Live` d'audio2wave (garde sa latence propre, voir CLAUDE.md d'audio2wave).

## 9. Questions encore ouvertes

- Quelques enregistrements representatifs des sets de l'utilisateur (WAV ou MP3, 1 a 3 minutes chacun) pour le lot 2 : chemin ou depot hors Git.
- Telephone capable de filmer a 120 ou 240 images/s pour la calibration (la plupart le font).
- Faut-il aussi caler la **Foudre** sur la pulsation (elle croit sur chaque kick) ? Propose : oui, comme le strobe, via le meme declenchement anticipe.
