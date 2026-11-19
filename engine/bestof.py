"""Best of des medias recus (issue #106) : garde les medias affiches pendant la soiree, puis les monte en une video (ffmpeg).

La logique pure (choix, duree, commande) est dans `core.bestof` ; ici : fichiers et ffmpeg. `build` tourne dans un fil de la GUI (jamais le fil Tk
ni le fil GL) et ne leve jamais : son resultat dit ce qui s'est passe.
"""
from __future__ import annotations

import os
import re
import shutil
import subprocess
import time
from pathlib import Path

from core import bestof as bo
from core import sessionlog

MIN_FREE_BYTES = 200 * 1024 * 1024
_NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)
_DURATION = re.compile(r"Duration:\s*(\d+):(\d+):(\d+(?:\.\d+)?)")


def root_dir() -> Path:
    return Path.home() / ".audio2wave" / "medias"


def media_dir(now: float | None = None, root: Path | None = None) -> Path:
    """Dossier des medias de la soiree en cours (un dossier par soiree, voir `core.bestof.evening_key`)."""
    return (root or root_dir()) / bo.evening_key(time.time() if now is None else now)


def save_media(name: str, data: bytes, now: float | None = None, root: Path | None = None) -> Path | None:
    """Garde le fichier d'origine d'un media affiche dans le dossier de la soiree. Jamais d'exception (le disque plein ne doit pas arreter la projection)."""
    try:
        folder = media_dir(now, root)
        folder.mkdir(parents=True, exist_ok=True)
        target = folder / bo.safe_name(name)
        if target.suffix.lower() not in bo.IMAGE_SUFFIXES:
            target = target.with_name(target.name + (".png" if data.startswith(b"\x89PNG") else ".jpg"))
        if target.exists():
            return target
        tmp = target.with_name(target.name + ".tmp")
        tmp.write_bytes(data)
        os.replace(tmp, target)
        return target
    except OSError as exc:
        sessionlog.event(f"Best of : media non garde ({exc})")
        return None


def list_media(folder: Path) -> list[str]:
    """Chemins des medias gardes, du plus ancien au plus recent (les noms du site commencent par l'heure d'envoi)."""
    try:
        names = [p.name for p in folder.iterdir() if p.is_file() and p.suffix.lower() in bo.IMAGE_SUFFIXES]
    except OSError:
        return []
    return [str(folder / n) for n in sorted(names)]


def music_duration(path: str, ffmpeg: str, run=subprocess.run) -> float:
    """Duree du fichier de musique en secondes (0.0 si illisible) : `ffmpeg -i` l'ecrit sur stderr."""
    try:
        done = run([ffmpeg, "-hide_banner", "-i", path], capture_output=True, timeout=60, creationflags=_NO_WINDOW)
    except (OSError, subprocess.SubprocessError):
        return 0.0
    text = (done.stderr or b"").decode("utf-8", "replace") if isinstance(done.stderr, (bytes, bytearray)) else str(done.stderr or "")
    m = _DURATION.search(text)
    return int(m.group(1)) * 3600 + int(m.group(2)) * 60 + float(m.group(3)) if m else 0.0


def build(folder: Path, music: str, out_dir: str, ffmpeg: str, *, run=subprocess.run, free_bytes=None) -> dict:
    """Monte le best of du dossier `folder`. Rend `{"state", "message", "path", "count"}` ; `state` : `pret`, `vide` (aucun media), `musique`
    (absente ou illisible), `disque` (moins de 200 Mo), `erreur`."""
    free_bytes = free_bytes or (lambda d: shutil.disk_usage(d).free)

    def out(state: str, message: str, path: str | None = None, count: int = 0) -> dict:
        return {"state": state, "message": message, "path": path, "count": count}

    try:
        images = list_media(folder)
        if not images:
            return out("vide", "Best of : aucun media recu pour cette soiree.")
        if not music or not os.path.isfile(music):
            return out("musique", "Best of : choisis d'abord la musique (fichier introuvable).")
        music_s = music_duration(music, ffmpeg, run)
        if music_s <= 0.0:
            return out("musique", "Best of : la musique est illisible.")
        os.makedirs(out_dir, exist_ok=True)
        if free_bytes(out_dir) < MIN_FREE_BYTES:
            return out("disque", "Best of : moins de 200 Mo libres, pas de montage.")
        count, slot = bo.plan(len(images), music_s)
        chosen = bo.select_even(images, count)
        target = os.path.join(out_dir, bo.out_name(folder.name))
        tmp = target[:-4] + ".tmp.mp4"
        try:
            done = run(bo.build_command(ffmpeg, chosen, music, tmp, slot), capture_output=True, timeout=1800, creationflags=_NO_WINDOW)
            if done.returncode != 0 or not os.path.isfile(tmp):
                raise RuntimeError("ffmpeg a echoue")
            os.replace(tmp, target)
        except (OSError, RuntimeError, subprocess.SubprocessError) as exc:
            try:
                os.remove(tmp)
            except OSError:
                pass
            return out("erreur", f"Best of impossible : {exc}")
    except Exception as exc:                          # jamais d'exception vers le fil appelant
        sessionlog.error("Best of", exc)
        return out("erreur", f"Best of impossible : {exc}")
    sessionlog.event(f"Best of : {count} media(s) sur {len(images)}, {count * slot:.0f} s -> {target}")
    return out("pret", f"Best of pret : {count} media(s), {count * slot:.0f} s", target, count)
