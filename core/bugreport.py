"""Rapport de bug (issue #136) : ce qu'on met dans le zip, masquage des secrets, resume des scenes, ecriture, nom du fichier, version.

Python pur (stdlib) : dossiers, horloge et commandes injectes, testable sans materiel. Le zip est PRIVE (lu par l'utilisateur ou une session
Claude Code) : on masque les secrets (cles, jetons), pas les chemins ni les noms de scenes. Ce qui demande le moteur (diagnostic, systeme,
Bureau) est fourni par `engine.bugreport` dans `extras`.
"""

from __future__ import annotations

import copy
import json
import os
import subprocess
import tempfile
import zipfile
from datetime import datetime
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit

SECRET_WORDS = ("key", "token", "secret", "password", "anon")
LOG_MAX = 2_000_000              # octets gardes d'un journal (la fin)
SETTING_MAX = 1_000_000          # un fichier de reglages plus gros est remplace par une note
LOGS = ("casual-overlay.log", "casual-overlay.log.1", "casual-overlay.log.2", "crash.log", "crash.precedent.log")
SETTINGS = ("gl_params.json", "autosave.precedent.json", "masters.json")
EXTRA_FILES = ("diagnostic.txt", "systeme.json", "fluidite.txt")       # textes fournis par l'appelant, poses a la racine du zip
MEDIA_KEYS = ("logo_path", "logo_video", "text_font", "bg_video", "video", "video2")


def _secret(name) -> bool:
    low = str(name).lower()
    return any(w in low for w in SECRET_WORDS)


def redact(obj):
    """Copie profonde ou toute cle de dict dont le nom contient un mot de `SECRET_WORDS` (casse ignoree) vaut "***", a tous les niveaux."""
    if isinstance(obj, dict):
        return {k: ("***" if _secret(k) else redact(v)) for k, v in obj.items()}
    if isinstance(obj, list):
        return [redact(v) for v in obj]
    return copy.deepcopy(obj)


def _medias(obj) -> list[str]:
    """Chemins de medias trouves dans une scene (overlay, fond, etat audio2wave, entrees), sans doublon, dans l'ordre."""
    found: list[str] = []

    def walk(o):
        if isinstance(o, dict):
            for k, v in o.items():
                if k in MEDIA_KEYS and isinstance(v, str) and v and v not in found:
                    found.append(v)
                else:
                    walk(v)
        elif isinstance(o, list):
            for v in o:
                walk(v)
    walk(obj)
    return found


def scenes_summary(data: dict) -> dict:
    """Resume de `scenes.json` : par scene son nom, son mode, son fond et ses medias ; par set la liste des scenes. Pas les reglages complets."""
    data = data if isinstance(data, dict) else {}

    def items(obj, key) -> list:                    # un fichier abime peut avoir un nombre ou null a la place d'une liste
        val = obj.get(key) if isinstance(obj, dict) else None
        return [x for x in val if isinstance(x, dict)] if isinstance(val, list) else []
    scenes = [{"nom": sc.get("name", ""), "mode": sc.get("mode", ""), "fond": sc.get("bg", ""), "medias": _medias(sc)} for sc in items(data, "scenes")]
    sets = [{"nom": st.get("name", ""), "scenes": [e.get("scene", "") for e in items(st, "entries")]} for st in items(data, "sets")]
    return {"scenes": scenes, "sets": sets, "set_actif": data.get("active_set", "")}


def _read(path: Path) -> bytes | None:
    try:
        return path.read_bytes()
    except OSError:
        return None


def _depot_url(settings_dir: Path) -> str:
    """URL du depot des doodles SANS identifiants, requete ni fragment (une cle peut y figurer) ; "" si absente ou illisible."""
    raw = _read(settings_dir / "depot.json")
    try:
        url = str(json.loads(raw.decode("utf-8")).get("url", "")) if raw else ""
        parts = urlsplit(url)
        host = (parts.hostname or "") + (f":{parts.port}" if parts.port else "")
        return urlunsplit((parts.scheme, host, parts.path, "", "")) if url else ""
    except (ValueError, AttributeError, UnicodeDecodeError):
        return ""


def collect(settings_dir: Path, extras: dict[str, str]) -> tuple[list[tuple[str, bytes]], list[str]]:
    """Entrees du zip (`LISEZMOI.txt` en premier) et notes (absents, illisibles, tronques, trop gros). `extras` : `description`, `version`,
    `portable` (texte de LISEZMOI) et `diagnostic.txt` / `systeme.json` / `fluidite.txt` (poses tels quels ; cle absente = entree absente)."""
    settings_dir = Path(settings_dir)
    entries: list[tuple[str, bytes]] = []
    notes: list[str] = []
    for name in LOGS:
        raw = _read(settings_dir / name)
        if raw is None:
            if not name.startswith("crash") and not name.endswith((".1", ".2")):
                notes.append(f"absent : {name}")
            continue
        if not raw:
            continue
        if len(raw) > LOG_MAX:
            raw = raw[-LOG_MAX:]
            cut = raw.find(b"\n")
            raw = raw[cut + 1:] if 0 <= cut < 4096 else raw
            notes.append(f"tronque : {name} (fin gardee, {LOG_MAX // 1_000_000} Mo)")
        entries.append((f"journal/{name}", raw))
    for name in SETTINGS:
        raw = _read(settings_dir / name)
        if raw is None:
            notes.append(f"absent : {name}")
            continue
        if len(raw) > SETTING_MAX:
            notes.append(f"trop gros : {name} ({len(raw)} octets, non inclus)")
            continue
        try:
            data = json.loads(raw.decode("utf-8"))
        except (ValueError, UnicodeDecodeError):
            notes.append(f"illisible : {name} (JSON invalide, non inclus : il ne peut pas etre masque)")
            continue
        entries.append((f"reglages/{name}", json.dumps(redact(data), indent=1, ensure_ascii=False).encode("utf-8")))
    raw = _read(settings_dir / "scenes.json")
    if raw is None:
        notes.append("absent : scenes.json")
    else:
        try:
            summary = scenes_summary(json.loads(raw.decode("utf-8")))
            entries.append(("reglages/scenes_resume.json", json.dumps(redact(summary), indent=1, ensure_ascii=False).encode("utf-8")))
        except Exception as exc:                    # un fichier abime ne doit jamais empecher le rapport
            notes.append(f"illisible : scenes.json ({type(exc).__name__})")
    for name in EXTRA_FILES:
        if extras.get(name):
            entries.append((name, str(extras[name]).encode("utf-8")))
    url = _depot_url(settings_dir)
    lines = ["Rapport de bug casual-overlay", "",
             f"Description : {extras.get('description') or '(aucune)'}",
             f"Version : {extras.get('version') or 'inconnue'}",
             f"Installation : {extras.get('portable') or 'inconnue'}",
             f"Depot des doodles : {url or '(non configure)'}", "",
             "Contenu :"] + [f"  {n}" for n, _ in entries] + ["", "Notes :"] + ([f"  {n}" for n in notes] or ["  (aucune)"])
    entries.insert(0, ("LISEZMOI.txt", ("\n".join(lines) + "\n").encode("utf-8")))
    return entries, notes


def write_zip(path: Path, entries: list[tuple[str, bytes]]) -> None:
    """Ecrit le zip d'un coup : fichier provisoire UNIQUE (deux rapports en meme temps ne se melangent pas) puis `os.replace` (jamais de zip a
    moitie ecrit sous le nom final)."""
    path = Path(path)
    fd, name = tempfile.mkstemp(prefix=path.name + ".", suffix=".tmp", dir=str(path.parent))
    os.close(fd)
    tmp = Path(name)
    try:
        with zipfile.ZipFile(tmp, "w", zipfile.ZIP_DEFLATED) as z:
            for entry, data in entries:
                z.writestr(entry, data)
        os.replace(tmp, path)
    finally:
        try:
            tmp.unlink()
        except OSError:                             # deja deplace (cas normal), ou impossible : ne pas masquer l'erreur d'origine
            pass


def output_path(folder: Path, now: datetime) -> Path:
    """`casual-bug-AAAA-MM-JJ_HHhMM.zip` dans `folder`, suffixe -2, -3... si le nom est deja pris. Le nom est RESERVE sur le disque (fichier vide
    cree en exclusif) : deux rapports lances en meme temps (bouton, ligne de commande) ne peuvent pas choisir le meme."""
    base = now.strftime("casual-bug-%Y-%m-%d_%Hh%M")
    n = 1
    while True:
        path = Path(folder) / (f"{base}.zip" if n == 1 else f"{base}-{n}.zip")
        try:
            with open(path, "xb"):
                return path
        except FileExistsError:
            n += 1


def version_info(repo_dir: Path, run=subprocess.run) -> str:
    """« commit du date » par git ; sinon le texte du fichier VERSION (ecrit par build_portable) ; sinon "inconnue"."""
    repo_dir = Path(repo_dir)
    try:
        flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
        sha = run(["git", "-C", str(repo_dir), "rev-parse", "--short", "HEAD"], capture_output=True, text=True, timeout=5, creationflags=flags)
        day = run(["git", "-C", str(repo_dir), "log", "-1", "--format=%cs"], capture_output=True, text=True, timeout=5, creationflags=flags)
        if sha.returncode == 0 and sha.stdout.strip():
            return f"{sha.stdout.strip()} du {day.stdout.strip()}" if day.returncode == 0 and day.stdout.strip() else sha.stdout.strip()
    except (OSError, subprocess.SubprocessError):
        pass
    raw = _read(repo_dir / "VERSION")
    if raw and raw.strip():
        return raw.decode("utf-8", "replace").strip()
    return "inconnue"
