# Latence : bloc de capture de 256, analyse toutes les 5,3 ms (issue #110, fin du lot 3)

Complement de `docs/plans/2026-11-12-synchro-son-image-design.md` (lot 3, pistes 3 a 5).

## Objectif

Gagner encore quelques millisecondes entre le son et l'effet, cote logiciel. Etat actuel (`tools/mesure_latence.py`, poste de dev) : mediane
17,5 ms, pire cas 28 ms (analyse 9 / 17 ms, rendu 0 / 16 ms). Le rendu attend au plus une image (60 Hz) : on ne le reduit pas. Il reste l'analyse.

Le fil d'analyse (`AudioAnalyzer._run`) traite chaque bloc recu de la carte son : **le pas d'analyse EST le bloc de capture** (512 echantillons =
10,7 ms a 48 kHz). Passer a 256 divise les deux par deux : le son attend moins avant d'etre analyse, et le kick est date plus finement.

## Essai jetable (banc du kick, corpus synthetique, sensibilite 1)

| | bloc 512 | bloc 256 sans adaptation |
|---|---|---|
| retard median de datation | 16,2 ms | 13,7 ms |
| kick sature (F1) | 0,92 | 0,97 |
| rock kick + caisse claire (precision) | 0,89 | **0,73** |
| claps sans kick (faux / min) | 42 | 28 |
| CPU de l'analyse (banc) | 3,4 % d'un coeur | 5,9 % |

La regression du rock vient du classifieur kick / caisse claire (`FeatureExtractor._classify`) : il compare au spectre d'il y a 4 PAS (43 ms a 512,
21 ms a 256). Tout ce qui compte en pas doit compter en temps.

## Design

1. **Une constante** `CAPTURE_BLOCK = 256` (`engine/audio.py`) : capture (`SoundDeviceInput`), banc (`tools/bench_kick.py`, `HOP`), mesure
   (`tools/mesure_latence.py`). `calibrer_avance.py` (lecture d'un clic) n'est pas concerne.
2. **Ce qui compte en pas passe en temps** (audit fait) :
   - `_mag_hist` : spectres dates ; le spectre « d'avant l'attaque » est le plus recent qui a au moins `ATTACK_LOOKBACK_S` = 0,043 s
     (= 4 pas de 512 : comportement d'avant a 512) ; historique garde sur 0,1 s.
   - `STAMP_KEEP` : en blocs -> garde ~11 s quelle que soit la taille du bloc (1024 a 256).
   - Flux d'attaques (`levels["flux"]`, `core/tempo.py`) : additionne par cases de 10 ms, il reste coherent (somme des hausses) ; verifie par la
     colonne BPM du banc, pas modifie sauf si le banc le montre faux.
   - Deja en temps (rien a faire) : lignes de base (`dt`), periodes refractaires, `WideRise` (120 ms), `FastAttack`, `OnsetDetector`, `Rhythm`,
     `PhaseTracker`, enveloppes.
3. **Repli automatique** : `SoundDeviceInput` compte les blocs signales en debordement (`status`, deja compte dans `dropped`). Plus de
   `OVERFLOW_MAX` (5) debordements en `OVERFLOW_WINDOW_S` (60 s) avec un bloc de 256 : la session rouvre l'entree avec 512 (`AudioController`),
   message dans le statut et ligne dans le journal (« Carte son : blocs de 256 trop courts, retour a 512 »). Une seule fois par session.
   `CASUAL_AUDIO_BLOCK=512` force 512 des le lancement (secours si une carte son crepite).
4. **Fraicheur de l'etat** (piste 5) : verifier par la mesure que la boucle de rendu lit l'etat le plus recent a chaque image (`latest()` sous
   verrou court). On ne change rien si la mesure ne montre pas de retard.
5. **Hors perimetre** : WASAPI exclusif (prend le micro aux autres applications, a mesurer sur le materiel de l'utilisateur), fenetre FFT plus
   courte (perte de finesse des basses).

## Criteres pour garder le changement

- `mesure_latence.py` (interne) : mediane meilleure d'au moins 3 ms qu'a 512, sur 3 executions.
- Banc : aucun morceau ne perd plus d'un point de F1 a la sensibilite 1 (rock compris) ; BPM justes la ou ils l'etaient.
- CPU de l'analyse sous 8 % d'un coeur (banc).
- Sinon : `CAPTURE_BLOCK` reste a 512, les adaptations en temps restent (sans effet a 512), la mesure est documentee.

## Tests (`check_gl.py`)

- `check_bench_kick` : comparaison 512 / 256 sur le meme corpus (seuils ci-dessus) ; le classifieur donne la meme precision au rock aux deux tailles.
- Le spectre d'avant l'attaque : a 256 comme a 512, il date de 43 ms au moins (test direct de l'extracteur).
- Repli : faux flux d'entree qui signale des debordements -> l'entree est rouverte a 512, message ; sous le seuil : rien ; `CASUAL_AUDIO_BLOCK=512`.
- `check_latence` (deja la) tourne avec `CAPTURE_BLOCK`.

## Non verifie (demande le materiel)

- Comportement de la vraie carte son de l'utilisateur avec des blocs de 256 (crepitements, debordements) ; le repli est le filet.
- Latence de bout en bout avec le projecteur.
