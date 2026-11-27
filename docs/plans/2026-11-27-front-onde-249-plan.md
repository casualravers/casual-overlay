# Front d'onde du stochastique courbe (#249) : plan

Spec : [2026-11-27-front-onde-249-design.md](2026-11-27-front-onde-249-design.md). Une seule PR (spec + plan + code). TDD pour `core/` (test d'abord dans `tools/check_gl.py`).

## Fichiers

`core/stotex.py`, `core/params_schema.py`, `core/randomizer.py`, `gl_shaders/scene.frag`, `engine/render.py`, `gui/gui_fond.py`, `gui/gl_gui.py`, `docs/fonctions/fond.md`, `tools/check_gl.py`.

## Taches

1. **Formules pures** (`check_front_pure`, rouge d'abord). `core/stotex.py` : `FRONT_SHAPES`, `FRONT_ORIGINS`, `front_geometry(shape, origin, angle, curve, aspect)` -> (ox, oy, d0, d1), `front_pos(shape, origin, angle, curve, rough, epoch, x, y, aspect)`, `front_uniforms(...)` -> (u_sto_front, u_sto_front2, u_sto_dir), bruit de valeur a deux octaves (hachage de `core.reaction._zhash`). Valeurs : droit = formule d'avant ; cercle 0 au centre / 1 aux coins, anneaux ronds, cadre vertical ; arc : sens selon l'angle, couvre 0..1, courbure croissante ; organique : rough 0 = cercle / arc exact, deformation <= 0,25, change a chaque epoque, continu.
2. **Reglages** : 5 cles `bg_sto_front*` (scope `bg`, droit par defaut, aucune automatisation).
3. **Shader et moteur** : `scene.frag` (`u_sto_front`, `u_sto_front2`, `u_sto_dir`, `front_noise`), `engine/render.py` (`_sto_texture` recoit le rapport largeur / hauteur). Test de rendu dans `check_bg_stochastic_tex`, pendant la premiere vague : droit de la gauche, l'angle du degrade ne le fait plus pivoter, direction propre, cercle revele du centre, arc courbe, organique rough 0 = cercle, irregularite deforme, origine cote, deterministe.
4. **GUI** : 5 lignes dans `sto_box`, grisages par texture et forme (`gl_gui.py`), « Angle du degrade » n'est plus actif pour le pavage seul. Test dans `check_gui`.
5. **Randomizer** : tire avec le pavage seulement, apres les autres.
6. **Docs** (`docs/fonctions/fond.md`), verifications, PR « Closes #249 ».

## Points a surveiller

- Un `pos` non monotone (bruit fort) est voulu : front dechire ; `pos` reste borne 0..1.
- L'origine de l'arc ne doit jamais etre plus pres que le bord du cadre (courbure 1 = origine au bord) : pas de division par zero (`d1 - d0` borne).
- Compatibilite : une scene avec un angle de degrade different voit son front partir de la gauche (a noter dans la PR).
