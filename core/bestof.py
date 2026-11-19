"""Best of des medias recus en fin de soiree (issue #106) : logique pure (dossier de la soiree, choix et duree des medias, commande ffmpeg,
reglage de la musique). Stdlib seulement : les fichiers et ffmpeg sont dans `engine/bestof.py`.

Le montage : les medias recus (doodles et photos du site) sont gardes sur disque pendant la soiree ; un bouton les assemble en une video
verticale (chaque media plein cadre, fondus), sur une musique choisie par l'utilisateur. Aucune moderation : tout ce qui a ete affiche est monte.
"""
from __future__ import annotations

import datetime as _dt
import json
import re
from pathlib import Path

SETTINGS_NAME = "bestof.json"
SLOT_MIN_S = 1.5              # duree minimale d'un media dans le montage
SLOT_MAX_S = 5.0              # et maximale (peu de medias : on ne s'attarde pas)
MAX_MEDIA = 60                # plafond (la commande ffmpeg liste chaque media : limite de longueur de commande de Windows)
ROLLOVER_H = 6                # une soiree qui passe minuit reste la meme jusqu'a 6 h du matin
FADE_S = 0.35                 # fondu d'entree et de sortie de chaque media
END_FADE_S = 1.5              # fondu final de la musique
SIZE = (720, 1280)            # vertical HD (comme l'enregistrement par defaut : pret pour TikTok / Instagram)
FPS = 30
IMAGE_SUFFIXES = (".png", ".jpg", ".jpeg")


def settings_path() -> Path:
    return Path.home() / ".audio2wave" / SETTINGS_NAME


def load_settings(path: Path | None = None) -> dict:
    """Reglage du best of (`{"music": chemin}`). Fichier absent ou illisible = valeurs par defaut, jamais d'exception."""
    out = {"music": ""}
    try:
        data = json.loads((path or settings_path()).read_text(encoding="utf-8-sig"))
    except (OSError, ValueError):
        return out
    if isinstance(data, dict) and isinstance(data.get("music"), str):
        out["music"] = data["music"].strip()
    return out


def save_settings(settings: dict, path: Path | None = None) -> bool:
    target = path or settings_path()
    try:
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(json.dumps({"music": str(settings.get("music", ""))}, ensure_ascii=False, indent=1), encoding="utf-8")
        return True
    except OSError:
        return False


def evening_key(timestamp: float) -> str:
    """« AAAA-MM-JJ » de la soiree a laquelle appartient cet instant (heure locale ; avant 6 h on est encore la veille)."""
    return (_dt.datetime.fromtimestamp(timestamp) - _dt.timedelta(hours=ROLLOVER_H)).strftime("%Y-%m-%d")


def safe_name(name: str) -> str:
    """Nom de fichier sur : le nom du bucket peut venir de n'importe qui, il ne sort jamais du dossier de la soiree."""
    base = re.split(r"[\\/]", str(name))[-1]
    base = re.sub(r"[^A-Za-z0-9._-]", "_", base).lstrip(".")
    return base or "media"


def out_name(key: str) -> str:
    return f"best-of-{key}.mp4"


def plan(count: int, music_s: float) -> tuple[int, float]:
    """(nombre de medias gardes, duree de chacun en secondes). La musique fixe la duree totale : chaque media dure musique / nombre
    (entre SLOT_MIN_S et SLOT_MAX_S) ; trop de medias pour la musique = on garde ce qu'elle permet."""
    if count <= 0 or music_s <= 0.0:
        return 0, 0.0
    n = max(1, min(int(count), MAX_MEDIA, int(music_s // SLOT_MIN_S)))
    slot = min(max(music_s / n, SLOT_MIN_S), SLOT_MAX_S)
    return n, min(slot, music_s)


def select_even(items: list, n: int) -> list:
    """`n` elements repartis sur toute la liste (le premier et le dernier compris)."""
    if n <= 0 or not items:
        return []
    if n >= len(items):
        return list(items)
    if n == 1:
        return [items[0]]
    last = len(items) - 1
    return [items[round(i * last / (n - 1))] for i in range(n)]


def build_command(ffmpeg: str, images: list[str], music: str, out: str, slot_s: float, size: tuple[int, int] = SIZE, fps: int = FPS, fade_s: float = FADE_S) -> list[str]:
    """Commande ffmpeg : chaque image plein cadre (bandes noires, jamais deformee) pendant `slot_s`, fondus, puis la musique (fondu final) ; duree = n x slot_s."""
    w, h = size
    total = len(images) * slot_s
    fade = min(fade_s, slot_s / 3.0)
    cmd = [ffmpeg, "-y", "-loglevel", "error"]
    for img in images:
        cmd += ["-loop", "1", "-framerate", str(fps), "-t", f"{slot_s:.3f}", "-i", img]
    cmd += ["-i", music]
    parts = [f"[{i}:v]scale={w}:{h}:force_original_aspect_ratio=decrease,pad={w}:{h}:(ow-iw)/2:(oh-ih)/2:color=black,setsar=1,fps={fps},format=yuv420p,"
             f"fade=t=in:st=0:d={fade:.3f},fade=t=out:st={slot_s - fade:.3f}:d={fade:.3f}[v{i}]" for i in range(len(images))]
    parts.append("".join(f"[v{i}]" for i in range(len(images))) + f"concat=n={len(images)}:v=1:a=0[v]")
    cmd += ["-filter_complex", ";".join(parts), "-map", "[v]", "-map", f"{len(images)}:a",
            "-af", f"afade=t=out:st={max(0.0, total - END_FADE_S):.3f}:d={END_FADE_S}", "-t", f"{total:.3f}",
            "-c:v", "libx264", "-preset", "veryfast", "-crf", "18", "-pix_fmt", "yuv420p", "-g", str(fps),
            "-colorspace", "bt709", "-color_primaries", "bt709", "-color_trc", "bt709",
            "-c:a", "aac", "-b:a", "320k", "-ar", "48000", "-movflags", "+faststart", out]
    return cmd
