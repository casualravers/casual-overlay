# Extraits automatiques des enregistrements : plan (issue #148)

Spec : `docs/plans/2026-11-17-extraits-automatiques-design.md` (validee le 2026-11-17). Execution : en une session, une seule PR (`Closes #148`).

**Ecarts decides au plan** (a la spec pres) :
- `Clip` porte aussi `at` (instant du drop, ou centre du pic) : la vignette est prise la.
- Le BPM vient d'une estimation pure `estimate_bpm` (autocorrelation) sur un flux d'attaques des basses a 20 ms, ecrite dans `core/highlights.py` : `core/tempo.py` (`TempoTracker`) est un suiveur en direct, pas fait pour un fichier. Le flux de 250 ms de la spec est trop grossier pour un BPM (Nyquist 2 Hz = 120 BPM).
- Le calage sur la grille est ancre sur le drop (un drop tombe sur un temps) : `at +/- k x periode`.
- Les extraits sont rendus par score decroissant : `extrait-1` est le meilleur.
- Commandes ffmpeg (decoupe, vignette) et noms de fichiers : fonctions pures dans `core/highlights.py`.

## Taches

### 1. `core/highlights.py` (TDD, `check_highlights`, dans `PURES`)
Interfaces produites :
- `Clip(NamedTuple): start, end, score, kind, at` (secondes ; `kind` = `"drop"` | `"pic"`).
- `normalize_series(values, lo_pct=5.0, hi_pct=95.0) -> list[float]` (percentiles -> 0..1, borne).
- `pick_clips(energy, step_s, *, bpm=None, clip_min=15.0, clip_max=30.0, count_max=5, pre=8.0, post=12.0) -> list[Clip]` ; `energy` = liste de `(niveau, basses)` normalises.
- `estimate_bpm(flux, step_s, lo=60.0, hi=200.0) -> float | None`.
- `clip_names(base_path, n) -> (mp4, jpg)`, `clip_command(ffmpeg, src, out, start, duration)`, `thumb_command(ffmpeg, src, out, at)`, `caption_text(base_name, clips) -> str`.
Tests (series synthetiques) : creux puis drop (un `drop`, 8 s avant, 12 s apres) ; pic soutenu (un `pic` de 20 s centre) ; deux drops a moins de 20 s (un seul) ; six drops (5 au plus, les plus forts, aucun chevauchement) ; avec bpm (bornes sur la grille, drop dans l'extrait, duree 15-30) ; plat / trop court / vide (liste vide) ; bornes (marge de 1 s, jamais negatif) ; `estimate_bpm` 120 +/- 2, silence = None ; commandes (ré-encodage x264, `-ss` avant `-i`, `-t`).

### 2. `engine/highlights_audio.py` (`check_highlights_audio`, ffmpeg reel, groupe moteur)
- `energy_series(path, ffmpeg, step_s=0.25) -> (list[(niveau, basses)], bpm | None, duree_s)` : decode mono 22,05 kHz `f32le`, RMS par fenetre, basses par moyenne glissante (~150 Hz), flux a 20 ms -> `estimate_bpm`, normalisation par `normalize_series`.
- Test : WAV synthetique de 40 s (20 s calmes, puis kicks a 120 BPM) -> drop trouve a 20 s +/- 1 s, bpm 120 +/- 3.

### 3. `engine/clips.py` + branchement (`check_clips`, groupe moteur)
- `make_clips(path, clips, ffmpeg) -> list[dict]` (`path`, `thumb`, `start`, `end`, `kind`) : ré-encodage, vignette, `<nom>-extraits.txt` ; nom provisoire puis `os.replace` ; un echec n'arrete pas les autres.
- `generate(path, ffmpeg, directory) -> dict` (`state`, `clips`, `message`) : analyse + choix + decoupe ; refuse si enregistrement < 60 s ou disque < 200 Mo libres.
- `RecordingControl` : `rec_clips` lu au demarrage ; dans `finish()` (fil non daemon), apres `rec_done`, `s.status["rec_clips"]` = `{"state": "analyse"}` puis le resultat ; messages `s.status["msg"]`.
- Reglage `rec_clips` (0, `device`) dans `core/params_schema.py`.

### 4. GUI (onglet Rec, `gui_rec_steps`)
- Case « Extraits automatiques » (`rec_clips`), liste de hauteur fixe sous le bouton (nom, minutage, type ; Ouvrir le dossier, Jeter), voyant « Extraits : analyse... » / « N extraits prets » dans `rec_refresh`.

### 5. Docs et fin
- Fiche `docs/fonctions/medias-externes.md` ; la racine ne change pas. `--pur`, `--seulement`, suite complete (CI), PR `Closes #148`, merge sur CI verte, carte « A tester ».

## Non verifie (a dire dans la PR)
Pertinence des moments sur de vrais morceaux, calage avec un vrai BPM, duree sur 10 minutes, ouverture dans TikTok / Instagram.
