# Densite variable du labyrinthe (#160) : plan

Spec : [2026-11-27-densite-rd-160-design.md](2026-11-27-densite-rd-160-design.md). Une seule PR (spec + plan + code). TDD pour `core/` (test d'abord dans `tools/check_gl.py`).

## Fichiers

`core/reaction.py`, `core/params_schema.py`, `core/automation.py`, `core/randomizer.py`, `core/safety.py`, `gl_shaders/rd_zone.frag`, `gl_shaders/rd_step.frag`, `engine/reaction.py`, `engine/render.py`, `gui/gui_fond.py`, `gui/gl_gui.py`, `docs/fonctions/fond.md`, `tools/check_gl.py`.

## Taches

1. **Formules pures** (`check_rd_dens_pure`, rouge d'abord). `core/reaction.py` : `DENS_MODES`, `DENS_MIN_DS` 0,25, `DENS_SPEED`, `DENS_R0` / `DENS_R1`, `Dens` (mode, amount, reach, count, t), `Zone.dens`, `dens_points`, `dens_dist` (tore, isotrope), `dens_value` (aimant : max des foyers, zones : bruit du masque), `diffusion_scale` (exactement 1 sans mode ou a intensite 0), `dens_uniforms`.
2. **Reglages** : 5 cles `bg_rd_dens*` (scope `bg`), automations intensite et portee (coupees), securite GPU (la densite compte comme une zone).
3. **Shader et moteur** (`check_rd_dens`, groupe labyrinthe). `rd_zone.frag` : 2e sortie `f_ds`, `dens_at` ; `rd_step.frag` : `lap *= ds` lu seulement si `u_ds_on` ; `engine/reaction.py` : texture R16F `ds_tex` en 2e attachement, uniforms ; `engine/render.py` : `_rd_dens`, `_rd_dens_t`. Verifie : mode `none` / intensite 0 = simulation identique bit pour bit, carte GPU = fonction pure, symetrie, derive, cohabitation avec masque et Vide, **longueur d'onde plus fine au foyer** (8,9 contre 16,1 cellules).
4. **GUI** : radio « Densite » + 4 curseurs dans `rd_box`, grisages (`gl_gui.py`). **Randomizer** : une fois sur trois, apres le masque.
5. **Docs** (`docs/fonctions/fond.md`), mesure du cout GPU, verifications, PR « Closes #160 ».

## Points a surveiller

- Une premiere version stockait `ds` dans la carte (RGBA32F) : +1,3 ms par image pour tous les labyrinthes a zones, meme sans densite. Remplacee par une 2e sortie R16F lue seulement avec une densite.
- Le motif met des centaines de pas a se reorganiser : `DENS_SPEED` lent.
- `ds` > 1 est instable : jamais plus large que la taille reglee.
