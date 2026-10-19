"""Archive d'un set : le fichier de set (voir `scenes.export_set`) et les medias qu'il utilise, dans un seul .zip qui se reimporte
sur une autre machine.

Python pur (stdlib seulement). Dans l'archive : `set.json` (meme contenu qu'un export JSON, mais chaque chemin de media est remplace
par une reference `pack:media/<empreinte>_<nom>`) et `media/<empreinte>_<nom>` (une copie par fichier distinct : deux emplacements
qui pointent vers le meme contenu ne l'embarquent qu'une fois). A l'import, les medias sont extraits dans un dossier stable et les
references redeviennent des chemins absolus de cette machine ; reimporter la meme archive redonne exactement les memes chemins
(les scenes identiques sont donc reconnues comme telles, voir `scenes.import_conflicts`).
"""
from __future__ import annotations

import hashlib
import json
import re
import zipfile
from pathlib import Path
from typing import Callable

PACK_PREFIX = "pack:"
MANIFEST = "set.json"
MEDIA_DIR = "media/"
MAX_UNPACKED = 16 * 1024 ** 3                  # garde-fou contre une archive piegee (taille totale declaree des medias)
_COMPRESSED = {".png", ".jpg", ".jpeg", ".gif", ".webp", ".mp4", ".webm", ".mov", ".mkv", ".avi", ".apng", ".ttf", ".otf"}


def _walk_media(data: dict, fn: Callable[[str], str]) -> dict:
    """Copie de `data` (un export de set) dont chaque chemin de media est remplace par `fn(chemin)`.

    Emplacements : par scene, `overlay` (logo_path, logo_video, text_font), `fond` (bg_video), `live_overrides` (video, video2) ;
    par entree, `media` (logo_path, logo_video, text_font), `a2w` (videos d'audio2wave), `bg_video`."""
    out = json.loads(json.dumps(data))

    def fix(d, keys) -> None:
        if isinstance(d, dict):
            for k in keys:
                if isinstance(d.get(k), str) and d[k].strip():
                    d[k] = fn(d[k])

    for scene in out.get("scenes", []):
        fix(scene.get("overlay"), ("logo_path", "logo_video", "text_font"))
        fix(scene.get("fond"), ("bg_video",))
        fix(scene.get("live_overrides"), ("video", "video2"))
    for entry in out.get("set", {}).get("entries", []):
        fix(entry.get("media"), ("logo_path", "logo_video", "text_font"))
        fix(entry.get("a2w"), ("video", "video2"))
        fix(entry, ("bg_video",))
    return out


def media_paths(data: dict) -> list[str]:
    """Tous les chemins de media cites par un export de set (sans doublon, dans l'ordre)."""
    seen: list[str] = []

    def note(p: str) -> str:
        if p not in seen:
            seen.append(p)
        return p
    _walk_media(data, note)
    return seen


def _is_file(p: str) -> bool:
    try:
        return not p.startswith(PACK_PREFIX) and Path(p).is_file()
    except OSError:
        return False


def _digest(path: Path) -> str:
    h = hashlib.sha1()
    with path.open("rb") as f:
        for block in iter(lambda: f.read(1 << 20), b""):
            h.update(block)
    return h.hexdigest()[:12]


def _safe_name(name: str) -> str:
    return re.sub(r"[^A-Za-z0-9._\- ]", "_", name).strip() or "media"


def write_pack(dest: str | Path, data: dict) -> dict:
    """Ecrit l'archive. Un media introuvable (ou un nom de police simple, qui n'est pas un fichier) reste tel quel dans le fichier de set
    (chemin d'origine) et est liste dans `missing` (fichiers reels seulement). Rend {"files": n, "bytes": taille des medias, "missing": [...]}."""
    refs: dict[str, str] = {}                       # chemin d'origine -> reference dans l'archive
    stored: dict[str, Path] = {}                    # reference -> fichier source
    missing: list[str] = []
    by_digest: dict[str, str] = {}

    def pack_one(p: str) -> str:
        if p in refs:
            return refs[p]
        if not _is_file(p):
            looks_like_path = ("/" in p or "\\" in p) and not p.startswith(PACK_PREFIX)
            if looks_like_path and p not in missing:
                missing.append(p)
            refs[p] = p
            return p
        src = Path(p)
        digest = _digest(src)
        ref = by_digest.get(digest)
        if ref is None:                              # meme contenu sous un autre nom : une seule copie
            ref = f"{PACK_PREFIX}{MEDIA_DIR}{digest}_{_safe_name(src.name)}"
            by_digest[digest] = ref
            stored[ref] = src
        refs[p] = ref
        return ref

    manifest = _walk_media(data, pack_one)
    manifest["archive"] = True
    total = 0
    with zipfile.ZipFile(dest, "w", zipfile.ZIP_DEFLATED, compresslevel=1) as z:
        z.writestr(MANIFEST, json.dumps(manifest, indent=2, ensure_ascii=False))
        for ref, src in stored.items():
            arc = ref[len(PACK_PREFIX):]
            mode = zipfile.ZIP_STORED if src.suffix.lower() in _COMPRESSED else zipfile.ZIP_DEFLATED
            z.write(src, arc, compress_type=mode)
            total += src.stat().st_size
    return {"files": len(stored), "bytes": total, "missing": missing}


def is_pack(path: str | Path) -> bool:
    try:
        return zipfile.is_zipfile(path)
    except OSError:
        return False


def read_pack(path: str | Path, media_dir: str | Path) -> dict:
    """Lit une archive : extrait ses medias dans `media_dir` (sans ecraser un fichier deja present de meme nom) et rend le contenu
    du fichier de set, references remplacees par les chemins absolus extraits. Leve ValueError si l'archive n'en est pas une.
    Les noms d'archive suspects (`..`, chemin absolu, hors `media/`) sont refuses."""
    try:
        z = zipfile.ZipFile(path)
    except (zipfile.BadZipFile, OSError) as exc:
        raise ValueError(f"archive illisible ({exc})") from None
    with z:
        if MANIFEST not in z.namelist():
            raise ValueError("ce zip n'est pas un set de casual-overlay (set.json absent)")
        try:
            data = json.loads(z.read(MANIFEST).decode("utf-8"))
        except (ValueError, UnicodeDecodeError):
            raise ValueError("archive de set illisible (set.json invalide)") from None
        infos = {i.filename: i for i in z.infolist()}
        if sum(i.file_size for n, i in infos.items() if n.startswith(MEDIA_DIR)) > MAX_UNPACKED:
            raise ValueError("archive trop volumineuse")
        media_dir = Path(media_dir)
        extracted: dict[str, str] = {}

        def unpack_one(p: str) -> str:
            if not p.startswith(PACK_PREFIX):
                return p
            arc = p[len(PACK_PREFIX):]
            if p in extracted:
                return extracted[p]
            parts = Path(arc).parts
            if arc not in infos or not arc.startswith(MEDIA_DIR) or ".." in parts or Path(arc).is_absolute() or len(parts) != 2:
                extracted[p] = ""                    # media absent ou nom refuse : la reference tombe, le set se charge quand meme
                return ""
            target = media_dir / parts[1]
            if not target.is_file() or target.stat().st_size != infos[arc].file_size:
                media_dir.mkdir(parents=True, exist_ok=True)
                tmp = target.with_name(target.name + ".part")
                with z.open(arc) as src, tmp.open("wb") as dst:
                    while True:
                        block = src.read(1 << 20)
                        if not block:
                            break
                        dst.write(block)
                tmp.replace(target)
            extracted[p] = str(target)
            return str(target)
        out = _walk_media(data, unpack_one)
    out.pop("archive", None)
    return out
