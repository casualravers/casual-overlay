# Stochastique : texture de la tache (#220), plan

Spec : [2026-11-26-stochastique-texture-220-design.md](2026-11-26-stochastique-texture-220-design.md). Une seule PR, TDD pour la partie pure.

## Taches (dans l'ordre, chacune finie et verifiee avant la suivante)

1. **Partie pure** : `core/stotex.py` (`SweepClock`, `front_times`, `sweep_tau`, `epoch_offset`, `wave_params`) et les 6 reglages `bg_sto_tex*` de `core/params_schema.py`. Test d'abord : `check_stotex` (PURES), rouge puis vert.
2. **Shader** : `scene.frag`, `sto_texture(p)` (onde et pavage) appele dans `stochastic_pattern` sur la couleur du point, bordure par-dessus ; uniforms `u_sto_tex`, `u_sto_wave`, `u_sto_sweep`, `u_sto_off`, sampler `u_sto_tile` (unite 5).
3. **Moteur** : `Renderer._sto_texture` (tuile partagee `text_tile`, `SweepClock`, phase de l'onde, unite 5 ou texture vide). Test d'abord : `check_bg_stochastic_tex` (groupe fond), rouge puis vert (aplat si la tuile n'est pas prete, fond intact, onde, pavage, vague suivante).
4. **GUI et Randomizer** : bloc « Texture de la tache » dans `sto_box` (`gui_fond.py`), grisages (`gl_gui.py`), tirage (`core/randomizer.py`, test dans `check_randomizer`).
5. **Documentation et verification** : `docs/fonctions/fond.md`, `core/CLAUDE.md`, `check_gl.py --pur`, groupes touches, puis la CI complete.

## Hors de cette PR

Vrai spectre / forme d'onde dans la tache (il faudrait passer le spectre au shader du fond) ; une texture sur le fond entre les points.
