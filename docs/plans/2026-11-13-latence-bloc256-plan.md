# Bloc de capture de 256 : plan d'implementation

> Execution : tache par tache, dans l'ordre ; cases `- [ ]` a cocher.

**But :** analyse toutes les 5,3 ms (bloc de 256) sans degrader la detection, avec repli automatique a 512.

**Architecture :** une constante `CAPTURE_BLOCK` ; ce qui comptait en pas compte en temps (`FeatureExtractor`, `RingBuffer`) ; `SoundDeviceInput`
compte les debordements dates, `AudioController` rouvre l'entree a 512 quand ils sont trop frequents (fil de garde d'une seconde).

**Spec :** `docs/plans/2026-11-13-latence-bloc256-design.md`

## Contraintes globales

- Francais sans accents ; `core/` stdlib seulement.
- `ATTACK_LOOKBACK_S = 0.043` ; historique des spectres 0,1 s ; `STAMP_KEEP_S = 11.0` ; `OVERFLOW_MAX = 5` en `OVERFLOW_WINDOW_S = 60.0` ;
  `CASUAL_AUDIO_BLOCK` (512 ou 256) l'emporte sur `CAPTURE_BLOCK`.
- Criteres pour garder 256 : mediane `mesure_latence` -3 ms au moins (3 executions), F1 de chaque morceau du banc a -1 point au plus, CPU < 8 % d'un coeur.

## Points d'attention de la revue

1. Changement de frequence d'echantillonnage (44,1 kHz) : les durees en temps restent justes (pas de 512 / 48000 code en dur).
2. Le repli pendant un enregistrement (REC) : `tap` de l'ancienne entree doit passer a la nouvelle (sinon le son du fichier s'arrete).
3. Le repli ne doit pas boucler (une seule fois par session) ni partir en mode synthetique.
4. Changement d'entree par la GUI (`switch`) : la nouvelle entree garde la taille de bloc en cours.
5. Premiers pas apres le lancement : historique des spectres encore trop court = comportement d'avant (pas d'attaque classee).

---

### Tache 1 : ce qui compte en pas passe en temps

**Fichiers :** `engine/audio.py` (`_mag_hist`, `_classify`, `STAMP_KEEP`), `tools/check_gl.py`.

- `_mag_hist` : deque de `(now, spec)` ; elague ce qui a plus de 0,1 s ; `_spec_before(now)` = le plus recent dont `now - t >= ATTACK_LOOKBACK_S`
  (None s'il n'y en a pas : attaque non classee, comme `len < ATTACK_LOOKBACK` avant). `ATTACK_LOOKBACK` (en pas) disparait.
- `RingBuffer.stamps` : `maxlen` calcule pour ~`STAMP_KEEP_S` au bloc le plus petit (256 a 48 kHz : 2100) ; `STAMP_KEEP` garde son nom.

- [ ] Etape 1 : test (`check_bench_kick` ou nouvelle `check_capture_block` dans `PURES`) : extracteur alimente par blocs de 256 puis de 512 avec
  le meme signal ; a chaque attaque retenue, l'age du spectre de reference est >= 0,043 s et < 0,043 s + un pas ; precision du rock a 256 >=
  precision a 512 - 0,01 (banc, `replay(..., hop=256)`).
- [ ] Etape 2 : lancer, ECHEC (precision 0,73 / 0,89 ; age 21 ms).
- [ ] Etape 3 : coder.
- [ ] Etape 4 : relancer OK ; `--seulement check_bench_kick`, `--seulement check_features` OK (a 512 rien ne change : memes chiffres du banc).
- [ ] Etape 5 : commit.

### Tache 2 : `CAPTURE_BLOCK`, repli automatique

**Fichiers :** `engine/audio.py` (`CAPTURE_BLOCK`, `capture_block()`, `SoundDeviceInput` : `blocksize` par defaut = `capture_block()`,
`overflow_times` deque, `overflow_burst(now) -> bool`), `engine/session.py` (`AudioController` : `self.block`, garde), `tools/bench_kick.py`
(`HOP = CAPTURE_BLOCK`), `tools/mesure_latence.py` (`block` par defaut = `CAPTURE_BLOCK`), `tools/check_gl.py`.

- `capture_block() -> int` : `CASUAL_AUDIO_BLOCK` s'il vaut 256 ou 512, sinon `CAPTURE_BLOCK`.
- `AudioController.check_block(now) -> str | None` : si l'entree n'est pas synthetique, que le bloc est < 512, que le repli n'a pas deja eu lieu et
  que `src.overflow_burst(now)` : rouvre la meme entree a 512 (meme code que `switch`, `tap` recopie), garde `self.block = 512`, journal
  (`sessionlog.event`), rend le message ; sinon None. Appelee par un fil de garde (`daemon`, toutes les secondes) lance par `start()`, arrete par
  `stop()` ; le message va dans `status["msg"]` par un rappel `on_message` (pose par `run_app`).
- `switch(device)` ouvre la nouvelle entree avec `self.block`.

- [ ] Etape 1 : tests (`check_capture_block`) : `capture_block()` avec / sans la variable (valeur invalide = constante) ; faux `SoundDeviceInput`
  (fabrique injectable dans `AudioController`, ex. `input_factory`) qui signale 6 debordements en 10 s -> `check_block` rouvre a 512, message,
  `tap` recopie ; 4 debordements -> rien ; un second appel apres repli -> rien ; synthetique -> rien ; `switch` garde le bloc courant.
- [ ] Etape 2 : lancer, ECHEC.
- [ ] Etape 3 : coder.
- [ ] Etape 4 : relancer OK ; `check_latence` OK.
- [ ] Etape 5 : commit.

### Tache 3 : mesure, decision, documentation

**Fichiers :** `docs/fonctions/synchro.md` (lot 3 : resultat), `docs/plans/2026-11-13-latence-bloc256-design.md` (tableau des mesures), PR.

- [ ] Etape 1 : `mesure_latence.py` 3 fois a 512 (`CASUAL_AUDIO_BLOCK=512`) et 3 fois a 256 ; banc complet aux deux tailles ; CPU (banc).
- [ ] Etape 2 : criteres atteints -> `CAPTURE_BLOCK = 256` ; sinon 512 et la mesure documentee.
- [ ] Etape 3 : verification de la fraicheur de l'etat (piste 5) : dans `mesure_latence`, le « rendu » median ne doit pas depasser une demi-image ;
  sinon, l'ecrire comme suite (pas de changement dans cette PR).
- [ ] Etape 4 : `--pur`, suite complete en arriere-plan ; documentation ; PR `Refs #110` (l'utilisateur ferme l'issue apres essai).

## Non verifie (demande le materiel)

- Crepitements / debordements de la vraie carte son a 256 ; latence avec le projecteur.
