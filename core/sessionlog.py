"""Journal de session de casual-overlay (stdlib seulement).

Un fichier `~/.audio2wave/casual-overlay.log` (rotation : 3 x 500 Ko) qui garde les evenements (scene appliquee, projection
ouverte / fermee, panique, strobe limite...) et TOUTES les erreurs, y compris celles qui ne s'affichent nulle part : callbacks Tk,
fils (GL, lecteurs), exceptions non rattrapees. Il sert a comprendre apres coup ce qui s'est passe en soiree.

Tout est sans danger : si le fichier est inscriptible nulle part, le journal se tait (jamais une erreur de plus).
"""
from __future__ import annotations

import faulthandler
import logging
import logging.handlers
import sys
import threading
import traceback
from pathlib import Path

LOG_PATH = Path.home() / ".audio2wave" / "casual-overlay.log"
CRASH_PATH = Path.home() / ".audio2wave" / "crash.log"          # piles Python de tous les fils si le processus meurt d'un plantage natif
_NAME = "casual-overlay"
_FORMAT = "%(asctime)s %(levelname)-7s %(message)s"
_hooks_done = False
logging.getLogger(_NAME).addHandler(logging.NullHandler())      # sans fichier branche : silence (pas de trace sur stderr via lastResort)


def logger() -> logging.Logger:
    return logging.getLogger(_NAME)


def setup(path: Path | str | None = None) -> logging.Logger:
    """Branche le fichier (une seule fois par chemin). Rend le logger ; sans fichier possible, il ecrit dans le vide."""
    log = logger()
    log.setLevel(logging.INFO)
    log.propagate = False
    target = Path(path) if path is not None else LOG_PATH
    for h in list(log.handlers):                                   # un changement de chemin (tests) remplace le fichier
        if getattr(h, "_casual_path", None) != str(target):
            log.removeHandler(h)
            try:
                h.close()
            except Exception:
                pass
    if not any(getattr(h, "_casual_path", None) == str(target) for h in log.handlers):
        try:
            target.parent.mkdir(parents=True, exist_ok=True)
            handler = logging.handlers.RotatingFileHandler(target, maxBytes=500_000, backupCount=3, encoding="utf-8")
            handler.setFormatter(logging.Formatter(_FORMAT, "%Y-%m-%d %H:%M:%S"))
            handler._casual_path = str(target)
            log.addHandler(handler)
        except OSError:
            log.addHandler(logging.NullHandler())
    return log


def event(message: str) -> None:
    """Un evenement ordinaire (une ligne)."""
    try:
        logger().info(message)
    except Exception:
        pass


def error(message: str, exc: BaseException | None = None) -> None:
    """Une erreur, avec la trace si on a l'exception."""
    try:
        if exc is not None:
            text = "".join(traceback.format_exception(type(exc), exc, exc.__traceback__)).rstrip()
            logger().error("%s\n%s", message, text)
        else:
            logger().error(message)
    except Exception:
        pass


def install_hooks() -> None:
    """Les exceptions non rattrapees (fil principal ou autre fil) finissent dans le journal, puis suivent leur chemin habituel."""
    global _hooks_done
    if _hooks_done:
        return
    _hooks_done = True
    previous = sys.excepthook

    def hook(etype, value, tb):
        error(f"Exception non rattrapee ({etype.__name__})", value)
        previous(etype, value, tb)

    sys.excepthook = hook
    previous_thread = threading.excepthook

    def thread_hook(args):
        error(f"Exception non rattrapee dans le fil {getattr(args.thread, 'name', '?')}", args.exc_value)
        previous_thread(args)

    threading.excepthook = thread_hook


_crash_file = None


def install_crash_dump(path: Path | str | None = None) -> bool:
    """Un plantage natif (pilote graphique, bibliotheque C) tue Python sans passer par `excepthook` : plus rien dans le journal, le programme sort
    avec un code brut. `faulthandler` ecrit alors la pile de CHAQUE fil dans `crash.log` (signal de plantage, ou arret force). L'ancien contenu est
    garde dans `crash.precedent.log` : `previous_crash()` le rend au lancement suivant."""
    global _crash_file
    target = Path(path or CRASH_PATH)
    try:
        target.parent.mkdir(parents=True, exist_ok=True)
        if target.exists() and target.stat().st_size > 0:
            target.replace(target.with_name(target.stem + ".precedent" + target.suffix))
        _crash_file = open(target, "a", encoding="utf-8")
        faulthandler.enable(file=_crash_file, all_threads=True)
        return True
    except Exception:
        return False


def previous_crash(path: Path | str | None = None) -> str:
    """Contenu du `crash.log` du lancement precedent (vide si le dernier arret n'a laisse aucune pile)."""
    target = Path(path or CRASH_PATH)
    old = target.with_name(target.stem + ".precedent" + target.suffix)
    try:
        return old.read_text(encoding="utf-8", errors="replace").strip()
    except OSError:
        return ""


def tail(lines: int = 40, path: Path | str | None = None) -> list[str]:
    """Les dernieres lignes du journal (pour un diagnostic rapide)."""
    try:
        text = Path(path or LOG_PATH).read_text(encoding="utf-8", errors="replace")
    except OSError:
        return []
    return text.splitlines()[-lines:]
