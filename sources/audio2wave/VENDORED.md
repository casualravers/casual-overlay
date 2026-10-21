# audio2wave (copie interne)

Cette copie est celle du depot `https://github.com/casualravers/audio2wave` (branche `main`), **sans modification** :

- commit : `4ee1496e452cf54a4b928ee668b30dc6bb33d7ba` (Merge pull request #17, `fix/analyzer-barres-saturees`)
- date de la copie : 2026-10-19
- fichiers : `audio2wave.py`, `audio2wave_live.py`, `audio2wave_snap.py`, `audio2wave_ridge.py`, `common.py`, `preset_registry.py`, `check_contract.py`, `CLAUDE.md`, `README.md`
  (pas `audio2wave.bat` : lanceur du depot d'origine ; pas `asset/`, `output/`, `bin/` : donnees et binaires ignores par leur `.gitignore`)

audio2wave est desormais interne a casual-overlay : le programme charge ce dossier par defaut (`audio2wave_gl.bundled_paths`), plus le depot voisin.
`--a2w-dir` / `AUDIO2WAVE_DIR` permettent toujours d'en indiquer un autre (essai d'une version).

Les modules sont **plats** (ils s'importent entre eux par leur nom : `import common`) : le dossier est ajoute a `sys.path`, ce n'est pas un paquet.
`python sources/audio2wave/check_contract.py` verifie les noms et signatures dont casual-overlay depend (lance par `check_gl.py`).

Modifier ces fichiers est maintenant permis ici, mais **chaque modification se note ci-dessous** (fichier, raison) pour pouvoir
rapporter ou refaire une synchronisation avec le depot d'origine.

## Modifications locales

- `audio2wave_live.py` (`style_var`, `shape_var`), `audio2wave_snap.py` (`style_var`, `wave_var`, `kick_glow_var`, `glow_live_var`, `gain_auto_var`),
  `audio2wave_ridge.py` (`gain_auto_var`) : la variable Tk de chaque choix qui grise d'autres reglages est rangee dans `widgets` (`GuiController.widgets`).
  Raison : `gui_gates` retrouvait ces variables en cherchant un bouton par son texte ; un libelle change et le grisage cassait en silence.
- `audio2wave_sphere.py` (**fichier ajoute**) : le mode Sphere (options, presets, fenetre de reglages ; dessine en GL par `engine/sphere_mode.py`). Il n'existe pas dans le depot
  d'origine et n'a pas de mode ligne de commande. `audio2wave_live.py`, `audio2wave_snap.py`, `audio2wave_ridge.py` : un bouton « Sphere » a cote de Live / Snap / Ridge
  (bascule de mode seulement sous casual-overlay) ; `preset_registry.py` (`MODES`) et `check_contract.py` : le mode `sphere` en plus.
- `common.py` (`add_mode_buttons`, `MODE_BUTTONS`), `audio2wave_live.py`, `audio2wave_snap.py`, `audio2wave_ridge.py` : les boutons de bascule de mode viennent d'une seule fonction (tous les modes toujours presents, dans le meme ordre ; le mode en cours est colore). Raison : l'ancien code dupliquait trois boutons par fenetre et en omettait un (le mode en cours), d'ou un bouton Sphere a rajouter a la main partout.
