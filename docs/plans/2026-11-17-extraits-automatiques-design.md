# Extraits automatiques des enregistrements (issue #148)

Statut : spec a valider. Issue : #148. Depend de l'enregistrement video + audio (#140, `docs/fonctions/medias-externes.md`).

## But

Apres un enregistrement (bouton REC), proposer 3 a 5 extraits de 15 a 30 s, cales sur les moments forts du morceau (une montee puis son drop, ou un pic),
prets a poster sur TikTok / Reels / Shorts. Aujourd'hui on enregistre plusieurs minutes puis on cherche a la main le bon passage.

Hors perimetre : publication automatique (API TikTok / Instagram), sons tendance, texte a l'ecran, legende fine (le morceau n'est pas connu).

## Decisions prises (brainstorming du 2026-11-17)

1. **Les moments forts viennent de l'analyse du son ENREGISTRE**, pas des evenements du direct (phases, drop). Raison : la detection en direct des phases et du drop est jugee
   peu fiable (issue #161) ; des extraits calés dessus en heriteraient. L'analyse apres coup est independante du detecteur en direct et recalculable plus tard.
2. **Decoupe par re-encodage court** (pas de copie sans re-encodage) : la copie coupe sur une image cle (jusqu'a 1 s d'ecart) et ruine le calage sur un temps.
3. **Option « Extraits automatiques » desactivee par defaut** (`rec_clips`) : l'analyse et la decoupe ajoutent du calcul a chaque arret.
4. **Pas de « garder »** dans l'interface : un extrait non jete reste. « Jeter » supprime ses fichiers.

## Architecture

```
arret du REC -> assemblage du MP4 (existant) -> [si rec_clips et duree >= 60 s] fil non daemon :
  engine/highlights_audio.py : ffmpeg decode l'audio du MP4 (mono, bas debit) -> numpy -> series d'energie (250 ms)
  core/highlights.py         : series -> extraits choisis (pur)
  engine/clips.py            : decoupe (ffmpeg, re-encodage), vignette, fichier texte
  s.status["rec_clips"]      : voyant « analyse... » puis « N extraits prets » ; liste lue par la GUI
```

### `core/highlights.py` (pur, stdlib seulement)

- `pick_clips(energy, step_s, *, bpm=None, clip_min=15.0, clip_max=30.0, count_max=5) -> list[Clip]`, `Clip = (start_s, end_s, score, kind)` avec `kind` = `"drop"` ou `"pic"`.
- `energy` : liste de `(niveau, basses)` toutes les `step_s` secondes (0,25), valeurs 0..1 deja normalisees par l'appelant (percentiles de l'enregistrement).
- **Drop** : saut d'energie des basses apres un creux : moyenne des ~2 s qui suivent comparee a celle des ~4 s qui precedent (rapport minimal, creux minimal) ; score = ampleur du saut x niveau apres. Extrait de **8 s avant a 12 s apres** le drop.
- **Pic** : fenetre glissante de 20 s a la plus forte energie moyenne, utilisee seulement si moins de `count_max` drops sont trouves ; extrait centre sur la fenetre.
- **Selection** : gloutonne par score decroissant, jamais de chevauchement (marge de 2 s), au plus `count_max`, au moins 1 s de marge avec le debut et la fin de l'enregistrement. Durees bornees a `clip_min..clip_max`.
- **Calage** : avec un `bpm`, debut et fin sont ramenes a la grille de temps la plus proche (le drop reste dans l'extrait). Sans `bpm`, bornes brutes.
- Enregistrement plat (ecart-type de l'energie quasi nul) ou plus court que `clip_min` : liste vide (pas d'extrait invente).
- `suggest_caption(clips)` : texte du fichier d'accompagnement (minutages, type, hashtags generiques `#vj #visuals #livemusic`).

### `engine/highlights_audio.py` (numpy, ffmpeg)

- `energy_series(mp4_path, ffmpeg, step_s=0.25) -> (series, bpm | None)` : decode l'audio du MP4 en mono 22,05 kHz `f32le`, RMS par fenetre de 250 ms, energie des basses (passe-bas ~150 Hz par FFT par fenetre), normalisation par percentiles (5e..95e).
- `bpm` : estime par autocorrelation du flux d'attaques des basses (60..200 BPM) ; `None` si la confiance est faible. Reutiliser `core/tempo.py` si son entree s'y prete, sinon une version courte locale.
- Aucune dependance a `Session` ni a la GUI : fonction pure d'un fichier.

### `engine/clips.py`

- `make_clips(mp4_path, clips, ffmpeg) -> list[ClipFile]` : pour chaque extrait, `ffmpeg -ss <debut> -i <mp4> -t <duree> -c:v libx264 -preset veryfast -crf 17 -pix_fmt yuv420p -c:a aac -b:a 320k -movflags +faststart` (memes reglages et metadonnees bt709 que le REC), nom `<enregistrement>-extrait-N.mp4`.
- Vignette `<enregistrement>-extrait-N.jpg` a l'instant du drop (une image).
- `<enregistrement>-extraits.txt` : minutages, type, hashtags.
- Ecriture sous un nom provisoire puis `os.replace` (jamais de fichier incomplet sous le nom final, comme l'assemblage du REC). Un echec d'un extrait n'arrete pas les autres ; le message le dit.
- Fil non daemon (l'arret de l'appli attend la fin, avec delai maximal), declenche par `RecordingControl` apres l'assemblage.

### Reglage et interface

- `rec_clips` : `0`/`1`, defaut 0, portee `device` (comme les autres reglages d'enregistrement : jamais dans un preset ni une scene). Une ligne dans `core/params_schema.py`.
- Onglet *Rec* (`gui/gui_vj.py`) : case **« Extraits automatiques »**, puis sous le bouton une liste des extraits du dernier enregistrement : nom, minutage, type, boutons **Ouvrir le dossier** et **Jeter**. Hauteur fixe de la liste avec ascenseur (rien ne bouge quand elle se remplit, regle de l'interface).
- Voyant du REC : « Extraits : analyse... » puis « 4 extraits prets » ; message en une ligne sous la barre (`scene_msg`).
- Pas d'extrait si l'enregistrement dure moins de 60 s.

## Verifications

**Pures (`PURES`, `check_highlights`)** sur des series synthetiques :
- creux puis drop net : un extrait `drop`, 8 s avant et 12 s apres, de 20 s ;
- pic soutenu sans drop : un extrait `pic` de 20 s centre sur le pic ;
- deux drops a moins de 20 s : un seul extrait (pas de chevauchement), le plus fort ;
- 6 drops espaces : au plus `count_max` = 5, les plus forts ;
- avec `bpm` : debut et fin sur la grille, le drop reste dans l'extrait ;
- enregistrement plat, plus court que 15 s, vide : liste vide ;
- bornes : jamais de debut negatif ni de fin au-dela de la duree.

**Engine (`check_clips`, ffmpeg reel, groupe moteur)** : un MP4 synthetique (audio : creux puis rafale de kicks) -> `energy_series` trouve le drop a +/- 1 s ; `make_clips` produit des fichiers dont la duree vaut celle demandee a 0,1 s pres, vignette et texte presents ; un extrait qui echoue n'arrete pas les autres ; aucun fichier sous le nom final en cas d'echec.

**GUI (`gui_rec_steps`)** : la case ecrit `rec_clips` ; la liste se remplit depuis `s.status["rec_clips"]` ; « Jeter » supprime les fichiers et la ligne ; la liste ne change pas la taille de l'onglet.

**Non verifie (a faire a l'oeil)** : pertinence des moments choisis sur de vrais morceaux (drops de house / techno, morceaux sans drop), calage sur le temps avec un vrai BPM, duree de l'analyse et de la decoupe sur un enregistrement de 10 minutes, ouverture des extraits dans TikTok / Instagram.

## Risques et points a surveiller

- **Faux drops** : un morceau a basses continues sans creux ne donne que des pics ; c'est voulu (mieux qu'un faux drop).
- **Duree de decoupe** : 5 extraits de 30 s en 1080x1920 x264 veryfast, quelques secondes chacun sur le poste de dev (a mesurer) ; l'encodeur materiel du REC n'est pas reutilise ici (qualite previsible).
- **Son avec delai audio** : l'analyse porte sur l'audio du MP4 final (delai deja applique) : les minutages collent a l'image.
- **Espace disque** : 5 extraits de 30 s ~ 40 Mo ; la regle du REC (arret sous 200 Mo libres) s'applique avant de decouper.

## Ordre de realisation (pour le plan)

1. `core/highlights.py` + `check_highlights` (TDD).
2. `engine/highlights_audio.py` + test avec un MP4 synthetique.
3. `engine/clips.py` + declenchement dans `RecordingControl` + `rec_clips`.
4. GUI : case, liste, voyant.
5. Fiche `docs/fonctions/medias-externes.md` (une ligne dans l'index racine seulement si necessaire : la racine reste a 300 lignes ou moins).
