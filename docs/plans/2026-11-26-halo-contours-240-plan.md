# Halo holographique : contour du sujet (#240), plan

Spec : [2026-11-26-halo-contours-240-design.md](2026-11-26-halo-contours-240-design.md). Une seule PR, TDD pour la partie pure.

## Taches (dans l'ordre, chacune verifiee avant la suivante)

1. **Formules pures** : `core/holo.py` (`halo_range`, `round_blend`, `ring_distance`, `halo_v`, `light_gain`, `jfa_steps`). Test d'abord : `check_holo_champ` (PURES), rouge puis vert.
2. **Shaders** : `gl_shaders/holo_jfa.frag` (graines + passes de jump flooding), `gl_shaders/holo.frag` reecrit (distance -> `v`, arrondi, direction, densite locale), plafond de la lumiere dans `post.frag` (`holo()`).
3. **Moteur** : `engine/render.py` (programme `holo_jfa`, deux textures RG16F de ping-pong creees et liberees avec `holo_tex`, graines puis `jfa_steps` puis passe finale ; portee et rayon d'arrondi calcules avec `core.holo`). Verifie d'abord par `check_holo` existant (non-regression : le logo reste intact, portee, bords calmes, reglages, audio, resolution).
4. **Verification en pixels** : `check_holo_contour` (groupe moteur) : couche vide, `v` = 1 - distance / portee (texels du champ), direction vers le sujet, plafond a l'intensite 3 (proches x2, lointains ~x1), sujet 4:1, deux formes separees, coupe = origine.
5. **Mesure et documentation** : cout GPU 1080 p avant / apres (3,96 -> 3,93 ms, inchange), `docs/fonctions/logo.md`, fiches `core/` et `gl_shaders/`, puis la suite et la CI.

## Ecarts a la spec decides a l'implementation

- Rapport largeur / hauteur plafonne a 3 dans `halo_range` (un texte plein cadre donnait une portee de 0,6 au premier rendu).
- Courbe de `v` lineaire (`CURVE` = 1) : les franges (`sin(v x 30)`) restent regulieres.

## Hors de cette PR

Nouveaux reglages, deformation et poussiere, halo sur les doodles seuls (le halo des doodles reste calcule, sans arrondi lointain).
