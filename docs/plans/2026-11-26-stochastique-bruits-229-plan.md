# Stochastique : type de bruit du champ (#229) - plan

> Execution native (superpowers:executing-plans). Spec : `docs/plans/2026-11-26-stochastique-bruits-229-design.md`.

**Goal:** 8 champs pour le stochastique (dont 5 nouveaux bruits), champ precalcule dans une texture, liste deroulante avec icones.

## Global Constraints

- Francais sans accents ; `core/` stdlib seulement ; nouvelle verification dans `SUITE` (et `PURES` si pure).
- `bg_sto_field` : valeurs `noise` (defaut), `organic`, `cells`, `network`, `veins`, `marble`, `waves`, `gradient` (ordre de la liste).
- Index shader : noise 0, waves 1, gradient 2, organic 3, cells 4, network 5, veins 6, marble 7 (les 3 premiers inchanges).

## Review Focus

- Fenetre redimensionnee / enregistrement d'un autre cadre : la texture du champ suit la taille de la scene -> test Task 1 (resolution).
- Palette changee en cours de route (stochastique -> labyrinthe -> stochastique) : pas de texture perimee -> test Task 1.
- Valeur inconnue dans une vieille scene -> Taches -> test Task 1.
- Icone absente (portable incomplet) : la liste s'affiche sans image, pas d'exception -> test Task 2.
- Champ sombre ou vide a densite 0,5 pour un bruit mal normalise -> test Task 1 (plage de couverture).

### Task 1 : table, passe du champ, bruits

**Files:** `core/params_schema.py`, `gl_shaders/sto_field.frag` (nouveau), `gl_shaders/scene.frag`, `engine/render.py` ; test `check_bg_stochastic`.

- Table : `bg_sto_field` choices = l'ordre ci-dessus. Module `core/sto_fields.py` ? Non : `STO_FIELDS` (valeur, libelle) dans `core/params_schema.py`
  n'est pas sa place -> `core/sto_fields.py` (pur) : `FIELDS = (("noise", "Taches"), ...)`, `index(value) -> int` (inconnu -> 0), `labels()`.
- `Renderer._sto_field_pass(size, p, au)` : programme `sto_field.frag`, FBO R16F (taille scene // 4, au moins 16 x 9), recree si la taille change ;
  `u_sto_ftex` sur l'unite 5 ; appele seulement si la palette est stochastique.
- `scene.frag` : `sto_field(q)` lit la texture puis applique contraste du kick et densite.
- Tests : 8 champs -> 8 images deux a deux differentes ; pour chaque champ couverture a densite 0,2 < 0,5 < 0,8 et 0,08 <= couverture(0,5) <= 0,7 ;
  valeur inconnue = image de Taches ; changer la taille de la fenetre puis revenir = meme image ; tests existants verts.

### Task 2 : icones et liste deroulante

**Files:** `tools/make_sto_icons.py` (nouveau), `asset/sto_fields/*.png`, `gui/gui_fond.py` ; test `check_gui` (sonde du bloc stochastique), `check_sto_icons` (pur : fichiers).

- Outil : rend chaque champ en 192 x 108 avec le Renderer (points blancs sur fond sombre, taille des points 14), reduit a 48 x 27, ecrit le PNG.
- GUI : `Menubutton` largeur fixe + `Menu` (une entree par champ, `image`, `compound="left"`) ; images chargees par Pillow si presentes, sinon texte seul.
- Tests : 8 fichiers 48 x 27 ; la liste a 8 entrees dans l'ordre avec une image chacune ; choisir « Veines » ecrit `veins` ; la largeur du bouton ne change pas ;
  sans le dossier d'icones, la fenetre se construit (texte seul).

### Task 3 : Randomizer, securite GPU, docs

- Randomizer : `rng.choice` sur les 8 valeurs. Test : chaque champ tire au moins une fois sur 300.
- Mesure du cout par champ (avant / apres) ; `core/safety.BG_STO_COST_MS` = pire champ mesure ; fiche `fond.md`.
