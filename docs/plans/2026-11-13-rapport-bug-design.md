# Rapport de bug : un zip pour diagnostiquer (issue #136)

## Objectif

Remplacer les descriptions « a l'oeil » d'un probleme vu en vrai (cas vu : infobulles qui ne s'affichent pas, non reproductibles en test) par un
dossier lisible. Le zip est **prive** : l'utilisateur le lit ou le donne a une session Claude Code. Il n'est jamais publie ; le masquage vise les
secrets (cles, jetons), pas les chemins ni les noms de scenes.

Criteres de reussite :
- un clic (ou une commande si la GUI ne demarre plus) produit un zip sur le Bureau ;
- le zip contient journal, plantages, diagnostic, systeme, fluidite, reglages legers et version ;
- aucun secret (`anon_key` de `depot.json`, toute cle `key` / `token` / `secret` / `password`) n'y figure ;
- un fichier absent ou illisible ne fait jamais echouer le rapport.

## Architecture

- **`core/bugreport.py`** (pur, stdlib, horloge et dossiers injectes) :
  - `redact(obj)` : copie profonde ; toute cle de dict dont le nom (minuscules) contient `key`, `token`, `secret`, `password` ou `anon`
    vaut `"***"` ; dicts et listes parcourus a tous les niveaux.
  - `scenes_summary(data)` : a partir du contenu de `scenes.json`, liste `{nom, mode, fond, medias, set(s)}` par scene (pas les reglages complets).
  - `collect(settings_dir, extras) -> (entrees, absents)` : `entrees` = liste `(nom dans le zip, octets)`, `absents` = noms des fichiers non trouves
    ou illisibles. `extras` = textes fournis par l'appelant (description, diagnostic, systeme, fluidite, version).
  - `write_zip(chemin, entrees)` : ecriture atomique (fichier provisoire puis `os.replace`).
  - `output_path(dossier, now)` : `casual-bug-AAAA-MM-JJ_HHhMM.zip`, suffixe `-2`, `-3` si deja pris.
  - `version_info(repo_dir)` : commit git court + date (`git rev-parse`, injectable) ; sinon contenu du fichier `VERSION` a cote du programme ;
    sinon `"inconnue"`.
- **`engine/bugreport.py`** : rassemble les `extras` qui demandent le moteur :
  diagnostic (`run_checks`, sans OpenGL si la projection tourne : pas de second contexte GL), systeme, mode portable / venv.
- **`tools/rapport_bug.py`** et option `--rapport-bug` de `audio2wave_gl.py` : version ligne de commande (utile si la GUI ne demarre plus),
  description en argument facultatif, chemin du zip imprime.
- **GUI** : bouton « Rapport de bug » dans l'onglet *Affichage* du panneau de controle, a cote de « Diagnostic ». Il demande une ligne facultative
  (« ce qui s'est passe », via `scene_ui` : remplacable par les tests), ecrit le zip dans un fil (`run_job` de `gui_sets` ou equivalent),
  puis ouvre l'Explorateur sur le fichier (`explorer /select,`). Message dans le statut (« Rapport ecrit : <nom> »).
- **Portable** : `build_portable.py` ecrit `VERSION` (commit + date de fabrication) dans le dossier ; `core/bugreport.py` dans `APP_FILES`.

## Contenu du zip

| Entree | Contenu |
|---|---|
| `LISEZMOI.txt` | description, date, version / commit, portable ou venv, liste des fichiers absents |
| `journal/casual-overlay.log`, `.log.1`, `.log.2` | journal de session (rotations) |
| `journal/crash.log`, `journal/crash.precedent.log` | si presents et non vides |
| `diagnostic.txt` | `format_report(run_checks(...))` complet |
| `systeme.json` | Windows, Python, Tk, moniteurs (noms, rectangles, echelle), taille / position de la fenetre de reglages, projection ouverte ou non, `gpu_ms` / `gpu_budget_ms` |
| `fluidite.txt` | rapport de fluidite de l'interface (`STATS`) ; absent en ligne de commande |
| `reglages/gl_params.json`, `reglages/autosave.precedent.json`, `reglages/masters.json` | passes par `redact` |
| `reglages/scenes_resume.json` | `scenes_summary(scenes.json)` |

Exclus : `depot.json` (seule son URL est notee dans `LISEZMOI.txt`), presets, `scenes.json` complet, `previews/`, medias, captures d'ecran.

## Erreurs et limites

- Fichier absent / illisible / JSON invalide : note dans `LISEZMOI.txt` (« absent : crash.log », « illisible : masters.json »), le reste est ecrit.
  Un JSON invalide n'est PAS copie (il ne peut pas etre masque et pourrait contenir un secret) : il est seulement signale.
- Taille : un journal est tronque a ses 2 derniers Mo ; un fichier de reglages de plus de 1 Mo est remplace par une note.
- Bureau introuvable (`~/Desktop` absent, dossier OneDrive deplace) : `~/.audio2wave/rapports/`. Echec d'ecriture : message, la GUI continue.
- Pas de version git dans le portable sans `VERSION` : « inconnue ».

## Tests (`check_bugreport`, dans `PURES`, ecrits avant le code)

- `redact` : cles imbriquees dans dicts et listes, casse ignoree, valeurs non secretes intactes.
- `collect` sur un faux `~/.audio2wave` (dossier temporaire) avec des secrets plantes (`anon_key`, `remote_token`...) : entrees attendues, et
  **aucun secret dans le zip entier** (recherche octet par octet).
- Fichiers absents, vides, JSON invalide : notes dans `LISEZMOI.txt`, pas d'exception, JSON invalide non copie.
- Journal de plus de 2 Mo tronque ; reglage de plus de 1 Mo remplace par une note.
- `scenes_summary` : noms et medias presents, plus petit que l'original.
- `output_path` : nom, suffixes.
- `version_info` : git injecte, fichier `VERSION`, ni l'un ni l'autre.
- `check_portable_modules` voit le nouveau module ; GUI : le bouton existe et produit un zip dans un dossier temporaire (description remplacee).

## Non verifie (demande le materiel)

- Un vrai rapport apres un vrai probleme en soiree ; le portable sur un PC sans git.
