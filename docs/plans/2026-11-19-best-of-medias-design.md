# Best of automatique des medias recus (issue #106)

Spec, plan et code dans une seule PR (choix de l'utilisateur : pas de PR de spec separee).

## Demande

En fin de soiree, un best of des medias recus du site, monte automatiquement avec de la musique.

## Decisions (reponses de l'utilisateur)

- **Montage sur le poste, avec ffmpeg** : l'appli garde les medias affiches ; un bouton monte la video. Aucun serveur.
- **Musique : un fichier choisi par l'utilisateur** (libre de droits, sous sa responsabilite). Pas la musique de la soiree.
- **Aucune moderation** : tous les medias affiches sont montes, sans validation.
- **Aucune limite de nombre de medias** (demande ulterieure de l'utilisateur : une premiere version plafonnait a 60).

## Conception

- `core/bestof.py` (pur) : dossier de la soiree (`evening_key`, changement a 6 h), nom sur (`safe_name`), plan (`plan` : duree par media = musique / nombre, entre 1,5 et 5 s, **aucune limite de medias** : la musique est rejouee si la video est plus longue), commandes ffmpeg par lots de 30 medias (`segment_command`) puis assemblage sans re-encodage (`join_command`), reglage de la musique (`bestof.json`).
- `engine/bestof.py` : `save_media` (garde le fichier d'origine), `list_media`, `music_duration` (`ffmpeg -i`), `build` (montage, etats `pret` / `vide` / `musique` / `disque` / `erreur`).
- `engine/doodle_feed.py` : parametre `saver`, appele pour chaque media decode ; `engine/app.py` y branche `save_media`.
- `gui/gui_vj.py`, onglet Rec : bouton « Monter le best of », bouton « Musique... », voyant d'une ligne (nombre de medias gardes, montage en cours, resultat), hauteur fixe.

## Hors perimetre

Moderation, musique fournie par l'appli, montage sur le site, ecran de previsualisation, transitions autres que le fondu.

## Verification

`check_bestof` (pur), `check_bestof_engine` (ffmpeg reel), `check_doodles`, `check_gui`. Non verifie : une vraie soiree, la diffusion sur les reseaux.
