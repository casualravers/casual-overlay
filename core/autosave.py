"""Auto-sauvegarde de l'etat courant (reglages + scene / set actifs) et reprise apres un arret brutal.

Stdlib seulement. Sortie propre = fichier efface ; un fichier present au lancement signale un plantage.
"""
from __future__ import annotations

import json
import os
import time
from pathlib import Path

from core.params import TRANSIENT_KEYS, coerce_params
from core import sessionlog

AUTOSAVE_PATH = Path.home() / ".audio2wave" / "autosave.json"
AUTOSAVE_EVERY_S = 60.0


def write_autosave(params: dict, extra: dict | None = None, path: Path | None = None) -> bool:
    """Instantane de l'etat courant (reglages + scene / set actifs). Ecriture atomique ; un echec est silencieux (rend False)."""
    path = path or AUTOSAVE_PATH
    try:
        data = {"saved_at": time.time(), "params": {k: v for k, v in params.items() if k not in TRANSIENT_KEYS}, **(extra or {})}
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(".tmp")
        tmp.write_text(json.dumps(data), encoding="utf-8")
        os.replace(tmp, path)
        return True
    except (OSError, TypeError, ValueError):
        return False


def read_autosave(path: Path | None = None) -> dict | None:
    """Dernier instantane, ou None (absent, illisible)."""
    try:
        data = json.loads((path or AUTOSAVE_PATH).read_text(encoding="utf-8"))
        return data if isinstance(data, dict) and isinstance(data.get("params"), dict) else None
    except (OSError, ValueError):
        return None


def clear_autosave(path: Path | None = None) -> None:
    """Sortie propre : plus d'instantane (sa presence au lancement suivant signale un arret brutal)."""
    try:
        (path or AUTOSAVE_PATH).unlink()
    except OSError:
        pass


def recover_autosave(params: dict, path: Path | None = None) -> tuple[dict, str]:
    """Un instantane present au lancement = la session precedente ne s'est pas terminee proprement (plantage, coupure) : ses reglages
    reprennent la place de `gl_params.json`. L'instantane est mis de cote (`.precedent`), pas efface. Rend (reglages, message)."""
    path = path or AUTOSAVE_PATH
    snap = read_autosave(path)
    if snap is None:
        return params, ""
    try:
        os.replace(path, path.with_suffix(".precedent.json"))
    except OSError:
        pass
    try:
        recovered = coerce_params({k: v for k, v in snap["params"].items() if k not in TRANSIENT_KEYS})
    except Exception as exc:
        sessionlog.error("Auto-sauvegarde illisible : ignoree", exc)
        return params, ""
    when = time.strftime("%H:%M", time.localtime(float(snap.get("saved_at", time.time()))))
    scene = snap.get("scene") or "?"
    sessionlog.event(f"Reprise apres arret brutal : reglages auto-sauves de {when} (scene '{scene}')")
    return recovered, f"Arret brutal detecte : reglages de {when} restaures (derniere scene : {scene})"
