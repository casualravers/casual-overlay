# Rapport de bug en zip : plan d'implementation

> Execution : tache par tache, dans l'ordre ; cases `- [ ]` a cocher.

**But :** un bouton (et `--rapport-bug`) ecrit sur le Bureau un zip prive : journal, plantages, diagnostic, systeme, fluidite, reglages legers
masques, version.

**Architecture :** logique pure dans `core/bugreport.py` (choix des fichiers, masquage, resume des scenes, zip, nom, version) ; `engine/bugreport.py`
rassemble ce qui demande le moteur (diagnostic, systeme, Bureau) et ecrit le rapport ; la GUI et la ligne de commande l'appellent.

**Spec :** `docs/plans/2026-11-13-rapport-bug-design.md`

## Contraintes globales

- Textes, commentaires, messages en francais SANS accents.
- `core/bugreport.py` : stdlib seulement (`check_core_purity`).
- Secrets : toute cle de dict dont le nom en minuscules contient `key`, `token`, `secret`, `password` ou `anon` vaut `"***"`.
- Limites : journal tronque a ses 2 derniers Mo (`LOG_MAX = 2_000_000`) ; reglage de plus de 1 Mo (`SETTING_MAX = 1_000_000`) remplace par une note.
- Nom du zip : `casual-bug-AAAA-MM-JJ_HHhMM.zip`, suffixes `-2`, `-3`.
- Toute nouvelle verification : dans `SUITE`, et dans `PURES` si elle ne demande rien de materiel.

## Points d'attention de la revue

1. `depot.json` : jamais copie ; son URL seule dans `LISEZMOI.txt` (une cle dans l'URL ? l'URL est notee sans sa partie requete).
2. JSON invalide : non copie (il ne peut pas etre masque), seulement signale.
3. Bureau redirige (OneDrive) : le vrai dossier du Bureau vient de Windows, pas de `~/Desktop`.
4. Projection ouverte : le diagnostic ne cree pas de second contexte OpenGL.
5. Rapport demande deux fois de suite dans la meme minute : deux fichiers distincts (suffixe), aucun ecrase.

---

### Tache 1 : `core/bugreport.py` (pur)

**Fichiers :** creer `core/bugreport.py` ; tests dans `tools/check_gl.py` (`check_bugreport`, ajoutee a `SUITE` et `PURES`).

**Produit :**
- `SECRET_WORDS = ("key", "token", "secret", "password", "anon")`, `LOG_MAX`, `SETTING_MAX`
- `redact(obj) -> obj` (copie profonde)
- `scenes_summary(data: dict) -> dict` : `{"scenes": [{"nom", "mode", "fond", "medias": [chemins]}], "sets": [{"nom", "scenes": [noms]}]}`
- `collect(settings_dir: Path, extras: dict[str, str]) -> tuple[list[tuple[str, bytes]], list[str]]` : `extras` = `{"description", "version",
  "portable", "diagnostic.txt", "systeme.json", "fluidite.txt"}` (une cle absente = entree absente) ; rend les entrees du zip (table de la spec,
  `LISEZMOI.txt` en premier) et la liste des notes (« absent : crash.log », « illisible : masters.json », « tronque : casual-overlay.log.1 »,
  « trop gros : ... »), aussi ecrites dans `LISEZMOI.txt`
- `write_zip(path: Path, entries) -> None` : `ZIP_DEFLATED`, fichier provisoire `*.tmp` puis `os.replace`
- `output_path(folder: Path, now: datetime) -> Path`
- `version_info(repo_dir: Path, run=subprocess.run) -> str` : `git -C repo rev-parse --short HEAD` + `git log -1 --format=%cs` (« 1231ef4 du
  2026-11-13 ») ; sinon le texte de `repo_dir / "VERSION"` ; sinon `"inconnue"`

- [ ] Etape 1 : ecrire `check_bugreport` avec un faux dossier (`tempfile`) contenant : `gl_params.json` avec `{"a": 1, "remote_token": "SECRET1",
  "x": {"Anon_Key": "SECRET2"}, "l": [{"api_key": "SECRET3"}]}`, `depot.json` `{"url": "https://p.supabase.co?apikey=SECRET4", "anon_key": "SECRET5"}`,
  `masters.json` invalide (`{pas du json` contenant `SECRET6`), `scenes.json` (2 scenes, 1 set), un journal de 2,5 Mo, `crash.log` vide.
  Assertions :
  - `redact` : valeurs secretes = `"***"`, `a` et `x` intactes en structure, casse ignoree ;
  - aucun `SECRET1`..`SECRET6` dans les octets du zip ecrit par `write_zip` (zip relu avec `zipfile`, toutes les entrees concatenees) ;
  - noms d'entrees attendus (`LISEZMOI.txt`, `journal/casual-overlay.log`, `reglages/gl_params.json`, `reglages/scenes_resume.json`) ; pas de
    `depot.json`, pas de `reglages/masters.json` ;
  - notes : « illisible : masters.json », « absent : autosave.precedent.json », « tronque » pour le journal ; `crash.log` vide non inclus ;
  - journal inclus <= `LOG_MAX` octets et finit par la derniere ligne du fichier ;
  - `LISEZMOI.txt` contient la description, la version et `https://p.supabase.co` sans `apikey` ;
  - `scenes_summary` : 2 scenes avec nom et medias, 1 set ; JSON plus petit que l'original ;
  - `output_path` : `casual-bug-2026-11-13_21h05.zip`, puis `-2` quand le premier existe ;
  - `version_info` : `run` factice qui rend le commit ; `run` qui leve `OSError` + fichier `VERSION` ; ni l'un ni l'autre = `"inconnue"`.
- [ ] Etape 2 : lancer `.venv\Scripts\python.exe tools/check_gl.py --seulement check_bugreport` : ECHEC (module absent).
- [ ] Etape 3 : ecrire `core/bugreport.py`.
- [ ] Etape 4 : relancer : tout OK ; puis `--seulement check_core_purity` OK.
- [ ] Etape 5 : commit.

### Tache 2 : `engine/bugreport.py`, ligne de commande et portable

**Fichiers :** creer `engine/bugreport.py`, `tools/rapport_bug.py` ; modifier `audio2wave_gl.py` (option `--rapport-bug [DESCRIPTION]`, traitee
comme `--diagnostic` : rapport puis sortie, ajoutee aussi a la liste de la ligne 46 qui saute l'ecran de chargement), `tools/build_portable.py`
(ecrit `VERSION` = `version_info(depot)` dans le dossier fabrique), `tools/check_gl.py`.

**Consomme :** tache 1. **Produit :**
- `desktop_dir() -> Path | None` : `SHGetFolderPathW(0, 0x10, 0, 0, buf)` (ctypes, Windows) ; sinon `Path.home() / "Desktop"` s'il existe ; sinon None
- `system_info(gui_root=None, s=None) -> dict` : `platform`, `python`, `tk` (si importable), moniteurs (glfw si initialisable sans fenetre, sinon
  absent), fenetre de reglages (`geometry`, `state`) si `gui_root`, `projection_ouverte`, `gpu_ms` / `gpu_budget_ms` lus dans `s.status` si `s`
- `diagnostic_text(args, opengl: bool) -> str` (memes regles que `gui_audio.diagnostic_report`)
- `make_report(description: str, *, settings_dir: Path, out_dir: Path | None, diag: str, system: dict, fluidity: str | None, now=None) -> tuple[Path, list[str]]`
  : `out_dir` None = `desktop_dir()`, sinon repli `settings_dir / "rapports"` (cree) ; rend (chemin, notes)

- [ ] Etape 1 : tests dans `check_bugreport_cli` (dans `SUITE`, pas `PURES` : lance un sous-processus) : `audio2wave_gl.py --rapport-bug "essai"
  --rapport-dossier <tmp> --synthetic` avec `USERPROFILE` / `HOME` pointes sur un dossier temporaire garni comme a la tache 1 ; code 0, chemin
  imprime, zip present avec `diagnostic.txt`, `systeme.json` (JSON valide, cle `python`) et `LISEZMOI.txt` contenant « essai » ; aucun secret ;
  `make_report(..., out_dir=None)` avec `desktop_dir` remplace par `lambda: None` = zip dans `settings_dir/rapports` ; deux appels dans la meme
  minute = deux fichiers.
- [ ] Etape 2 : lancer, ECHEC.
- [ ] Etape 3 : ecrire le module, l'option (`--rapport-dossier` cache, pour les tests), `tools/rapport_bug.py` (appelle la meme fonction),
  `VERSION` dans `build_portable.py`.
- [ ] Etape 4 : relancer `--seulement check_bugreport`, `--seulement check_portable_modules`, `--seulement check_diagnostics` : OK.
- [ ] Etape 5 : commit.

### Tache 3 : bouton de la GUI et documentation

**Fichiers :** modifier `gui/gui_audio.py` (bouton « Rapport de bug » sur la ligne de « Diagnostic », info-bulle ; nom `icons.TEXT_ICONS` si un
libelle d'icone est voulu, sinon texte seul), `gui/gui_sets.py` si `run_job` doit etre partage, `tools/check_gl.py` (`gui_diag_steps`),
`CLAUDE.md` (section « Diagnostic de demarrage et version portable »), `README.md` (une ligne).

**Consomme :** `make_report`, `diagnostic_text`, `system_info`, `STATS.report(16)`.
Comportement : clic -> `scene_ui["ask_name"]("Rapport de bug", "Ce qui s'est passe (facultatif) :", "")` ; `None` (annule) = rien ; sinon fil
d'arriere-plan qui ecrit le zip (diagnostic sans OpenGL si la projection tourne) ; a la fin, dans le fil Tk : statut « Rapport ecrit : <nom> »
et `explorer /select,<chemin>` (sauf si `s.bug_out_dir` est pose : les tests). Exposer `c["bug_btn"]` et `c["bug_report"](description, wait=True)`.

- [ ] Etape 1 : dans `gui_diag_steps`, avec `s.bug_out_dir` = dossier temporaire et `scene_ui["ask_name"]` remplace : le bouton existe a cote
  de Diagnostic (meme parent), un clic produit un zip avec `fluidite.txt` et la description ; annuler ne produit rien ; la hauteur de l'onglet
  *Affichage* ne change pas (`aff_height`).
- [ ] Etape 2 : lancer `--seulement check_gui` : ECHEC.
- [ ] Etape 3 : ecrire le bouton.
- [ ] Etape 4 : relancer : OK ; `--pur` OK ; suite complete en arriere-plan.
- [ ] Etape 5 : documenter (CLAUDE.md, README), commit, PR `Closes #136`.

## Non verifie (demande le materiel)

- Bureau redirige par OneDrive sur un autre PC ; portable sans git ; un vrai rapport apres un vrai probleme en soiree.
