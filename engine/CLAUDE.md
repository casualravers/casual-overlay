# engine/ : moteur sans GUI

Avant de modifier un module, lis sa fiche :

- `projection.py`, `window.py`, `app.py`, `render.py` (PerfGuard) : [docs/fonctions/projection.md](../docs/fonctions/projection.md)
- `render.py` (logo, halo, fonte, cellules, mosaique, texte) : [docs/fonctions/logo.md](../docs/fonctions/logo.md) ; (strobe, foudre) : [docs/fonctions/strobe-foudre.md](../docs/fonctions/strobe-foudre.md)
- `audio.py` : [docs/fonctions/audio.md](../docs/fonctions/audio.md) et [docs/fonctions/synchro.md](../docs/fonctions/synchro.md)
- `video.py`, `reaction.py` : [docs/fonctions/fond.md](../docs/fonctions/fond.md) ; `video.py` (ProducerManager) : [docs/fonctions/gui.md](../docs/fonctions/gui.md)
- `native_modes.py`, `sphere_mode.py` : [docs/fonctions/gui.md](../docs/fonctions/gui.md)
- `preview.py` : [docs/fonctions/scenes-vj.md](../docs/fonctions/scenes-vj.md)
- `doodle_feed.py`, `recorder.py`, `remote.py` : [docs/fonctions/medias-externes.md](../docs/fonctions/medias-externes.md)
- `diagnostics.py`, `deps.py` (dependances facultatives, `MISSING`) : [docs/fonctions/outillage.md](../docs/fonctions/outillage.md)
- `session.py` (etat partage GL / GUI / fils : la regle des threads est dans la racine, section Architecture) : [docs/fonctions/projection.md](../docs/fonctions/projection.md)
