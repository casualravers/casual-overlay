# core/ : Python pur (stdlib seulement)

Regle : aucun import du moteur, de la GUI, de Tk, numpy, GL, PIL ni audio2wave (`check_core_purity`). Avant de modifier un module, lis sa fiche :

- `params_schema.py`, `scenes.py`, `randomizer.py`, `safety.py`, `uistats.py` : [docs/fonctions/scenes-vj.md](../docs/fonctions/scenes-vj.md)
- `brain.py` (journal du visualizer du cerveau, issue #192) : [docs/fonctions/cerveau.md](../docs/fonctions/cerveau.md)
- `onset.py`, `rhythm.py`, `tempo.py`, `phase.py`, `audio_layers.py`, `explain.py`, `routing.py` (routage audio par effet, #198) : [docs/fonctions/audio.md](../docs/fonctions/audio.md)
- `pulse.py`, `latency.py` : [docs/fonctions/synchro.md](../docs/fonctions/synchro.md)
- `strobe_auto.py`, `foudre.py`, `controls.py` (strobe, montee) : [docs/fonctions/strobe-foudre.md](../docs/fonctions/strobe-foudre.md)
- `controls.py` (panique, tap tempo), `sessionlog.py`, `autosave.py`, `palette.py` : [docs/fonctions/exploitation.md](../docs/fonctions/exploitation.md)
- `reaction.py`, `playback.py`, `sto_fields.py` (champs du stochastique, #229), `stotex.py` (texture de la tache du stochastique, #220) : [docs/fonctions/fond.md](../docs/fonctions/fond.md)
- `textcycle.py`, `textfx.py` (vague de lettres et remplissage du texte, #199), `schedule.py`, `automation.py` : [docs/fonctions/logo.md](../docs/fonctions/logo.md)
- `fuzz.py` (tirage reproductible de jeux de reglages depuis la table, #195) : [docs/fonctions/outillage.md](../docs/fonctions/outillage.md)
- `params.py` (presets overlay / fond, `PresetBook`, `menu_layout`) : [docs/fonctions/gui.md](../docs/fonctions/gui.md)
- `doodles.py`, `qrcode.py`, `recording.py`, `remote_auth.py`, `remote_state.py`, `setpack.py` : [docs/fonctions/medias-externes.md](../docs/fonctions/medias-externes.md)
