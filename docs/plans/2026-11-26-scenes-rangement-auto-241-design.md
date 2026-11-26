# Rangement automatique des scenes par type de fond (#241)

## Intention
Quand la bibliotheque grossit, retrouver une scene est long. Ranger seules les scenes dans des dossiers par type de fond (snap, ridge,
stochastique...). Les dossiers existent deja : un dossier est le prefixe du nom (`club/neon`, un seul niveau), `core.scenes.library_tree`
les affiche et le dialogue « Ranger » deplace a la main. Ce qui manque : **choisir le dossier automatiquement**.

## Decisions (utilisateur)
- Rangement **reel** (option 1) : le dossier est ecrit dans le nom de la scene, pas une simple vue.
- **Reglage pour desactiver** le rangement automatique.

## Decisions (Claude, a corriger si besoin)
- **Type de fond -> dossier** (noms sans accent) : fond genere = sa palette (`duo`, `test`, `stochastique`, `labyrinthe`), fond video = `video`,
  fond audio2wave = son mode (`live`, `snap`, `ridge`, `sphere`). Fonction pure `core.scenes.fond_folder(scene)` ; un cas inconnu retombe sur `fond`.
- **A l'enregistrement** (`capture_scene_now`) : une scene NOUVELLE sans dossier dans son nom prend `<type>/<nom>` si le reglage est actif. Une scene
  deja connue (mise a jour) ne bouge pas ; un nom saisi avec son propre dossier (`club/neon`) est respecte.
- **Scenes existantes** : bouton « Ranger par type » dans l'en-tete de la bibliotheque, `SceneBook.organize()` : seules les scenes **a la racine**
  sont rangees (les dossiers de l'utilisateur ne sont jamais touches) ; si `<type>/<nom>` existe deja, la scene reste a la racine et le message
  le dit (« N rangees, M laissees : nom deja pris »). Le renommage passe par `rename_scene` (sets et planning suivent, comme le dialogue « Ranger »).
- **Reglage** `scene_auto_folder` (reglage de salle, 0 / 1, **actif par defaut**) : ne vaut que pour les NOUVELLES scenes ; jamais capture par une
  scene ni un preset ; une case a cocher dans l'en-tete de la bibliotheque. Le bouton « Ranger par type » marche meme reglage coupe (action voulue).
- Pas de fichier a migrer : le format de `scenes.json` ne change pas.

## Hors perimetre
Dossiers a plusieurs niveaux, tri personnalise, regles de rangement editables par l'utilisateur.

## Verifications
Tests purs (`core/`, d'abord) : `fond_folder` pour chaque type, `auto_name`, `organize` (racine seulement, collision, sets suivis, idempotent),
reglage dans la table. GUI : la case et le bouton existent, une scene enregistree arrive dans le bon dossier, reglage coupe = a la racine.
Non verifie (materiel) : rien.
