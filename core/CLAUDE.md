# core/ : Python pur (stdlib seulement)

Regle : aucun import du moteur, de la GUI, de Tk, numpy, GL, PIL ni audio2wave (`check_core_purity`). Avant de modifier un module, lis sa fiche :

- `params_schema.py`, `scenes.py`, `randomizer.py`, `safety.py`, `uistats.py` : [docs/fonctions/scenes-vj.md](../docs/fonctions/scenes-vj.md)
- `onset.py`, `rhythm.py`, `tempo.py`, `phase.py`, `audio_layers.py`, `explain.py` : [docs/fonctions/audio.md](../docs/fonctions/audio.md)
- `pulse.py`, `latency.py` : [docs/fonctions/synchro.md](../docs/fonctions/synchro.md)
- `strobe_auto.py`, `foudre.py`, `controls.py` (strobe, montee) : [docs/fonctions/strobe-foudre.md](../docs/fonctions/strobe-foudre.md)
- `controls.py` (panique, tap tempo), `sessionlog.py`, `autosave.py`, `palette.py` : [docs/fonctions/exploitation.md](../docs/fonctions/exploitation.md)
- `reaction.py`, `playback.py` : [docs/fonctions/fond.md](../docs/fonctions/fond.md)
- `textcycle.py`, `schedule.py`, `automation.py`, `params.py` (presets) : [docs/fonctions/logo.md](../docs/fonctions/logo.md)
- `doodles.py`, `qrcode.py`, `recording.py`, `remote_auth.py`, `remote_state.py`, `setpack.py` : [docs/fonctions/medias-externes.md](../docs/fonctions/medias-externes.md)
