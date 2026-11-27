# Masque d'expansion de la reaction-diffusion (#178) : plan

Spec : [2026-11-27-masque-rd-178-design.md](2026-11-27-masque-rd-178-design.md). Une seule PR (spec + plan + code). TDD pour `core/` (test d'abord dans `tools/check_gl.py`).

## Fichiers

`core/reaction.py` (formules pures), `core/params_schema.py`, `core/automation.py`, `core/randomizer.py`, `core/safety.py`, `gl_shaders/rd_zone.frag`, `engine/reaction.py`, `engine/render.py`, `engine/projection.py`, `gui/gui_fond.py`, `gui/gl_gui.py`, `docs/fonctions/fond.md`, `tools/check_gl.py`.

## Taches

1. **Formules pures** (test : `check_rd_mask_pure`, rouge d'abord). `core/reaction.py` : `MASK_SHAPES`, `MASK_SPEED`, `Mask` (NamedTuple : shape, count, size, angle, soft, invert, t), `Zone.mask`, `mask_cells(count)`, `mask_value(mask, x, y, aspect, sym, branches)` (replique de `zhash`, `pnoise` (avec `MASK_SALT`), `fold`), `mask_uniforms(mask)`. Valeurs : part vivante ~ taille pour chaque forme, cercles plafonnes a ~78 %, bord net a douceur 0, rotation, derive periodique et continue, symetrie.
2. **Reglages et automations** : 7 cles `bg_rd_mask*` (scope `bg`, plages de la spec) ; `AUTOMATION_SPECS` + `DEFAULT_RANGES` pour taille, angle, douceur, derive (coupees par defaut).
3. **Shader et moteur** (test : `check_rd_mask`, groupe `labyrinthe`). `rd_zone.frag` : `mask_at` + `gone = 1 - (1 - gone) x m` ; `engine/reaction.py` : `zoned` aussi avec un masque, uniforms de `mask_uniforms` ; `engine/render.py` : `_rd_mask(p)`, `_rd_mask_t` integre comme la derive des zones ; `engine/projection.py` : `s.status["rd_mask_t"]`. Verifie : `none` = simulation identique bit pour bit, carte GPU = fonction pure, matiere absente dehors / presente dedans, inversion, douceur, cohabitation avec le Vide, symetrie, derive.
4. **GUI** : bloc « Masque » dans `rd_box` (radio, vignette 96 x 54 calculee en Python, Inverser, 5 curseurs), grisages (`gui_gates.row_of_label(rd_box, ...)`), rafraichissement de la vignette dans `update_meter`. Test : lignes presentes, grisees sans forme, vignette qui se redessine.
5. **Randomizer** (une fois sur trois, tire apres le reste) et **securite GPU** (`core/safety.py` : le masque compte comme une zone).
6. **Docs** (`docs/fonctions/fond.md`), mesure du cout GPU, verifications (`--pur`, groupe labyrinthe, GUI, params, automation, randomizer, claude_md, portable), PR « Closes #178 ».

## Points a surveiller

- Un masque qui bouge plus vite que le motif ne le suit pas : `MASK_SPEED` lent.
- `hache(0, 0) = 0` : un reseau de bruit d'une cellule est plat, d'ou le decalage `MASK_SALT` et le minimum de 2 cellules par axe.
- Tk lance les rappels d'une variable du plus recent au plus ancien : la vignette lit la variable, pas `params`.
