"""Extraits automatiques (issue #148) : decoupe des moments forts d'un enregistrement (ffmpeg), fichier texte, un dossier par enregistrement.

`generate` est la chaine complete (analyse du son -> choix -> decoupe) ; elle tourne dans le fil d'assemblage de `RecordingControl` (non daemon).
Chaque fichier est ecrit sous un nom provisoire puis `os.replace` : jamais de fichier a moitie fait sous le nom final. Un extrait qui echoue n'arrete pas les autres.
"""
from __future__ import annotations

import os
import shutil
import subprocess

from core import highlights as hl
from core import sessionlog
from engine import highlights_audio as ha

MIN_RECORDING_S = 60.0                    # en dessous, pas d'extrait (rien a choisir)
MIN_FREE_BYTES = 200 * 1024 * 1024        # comme l'enregistrement : on ne decoupe pas sur un disque presque plein
STEP_S = 0.25
_NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)


def txt_name(path: str) -> str:
    """Fichier texte a cote de l'enregistrement : `<nom>-extraits.txt`."""
    return f"{os.path.splitext(path)[0]}-extraits.txt"


def _remove(*paths: str) -> None:
    for p in paths:
        try:
            os.remove(p)
        except OSError:
            pass


def make_clips(path: str, clips, ffmpeg: str, run=subprocess.run, dest: str | None = None) -> list[dict]:
    """Decoupe chaque `Clip` de `path` ; rend un dict par extrait reussi (`number`, `path`, `start`, `end`, `kind`). Le numero est celui du rang du clip (extrait-1 = le meilleur).
    `dest` : chemin sous lequel l'enregistrement sera range (les extraits et le texte sont nommes et ecrits a cote de lui) ; par defaut `path`."""
    done_clips, numbers, results = [], [], []
    target = dest or path
    for n, c in enumerate(clips, 1):
        mp4 = hl.clip_names(target, n)
        tmp_mp4 = mp4[:-4] + ".tmp.mp4"
        try:
            done = run(hl.clip_command(ffmpeg, path, tmp_mp4, c.start, c.end - c.start), capture_output=True, timeout=600, creationflags=_NO_WINDOW)
            if done.returncode != 0 or not os.path.isfile(tmp_mp4):
                raise RuntimeError("ffmpeg a echoue")
            os.replace(tmp_mp4, mp4)
            results.append({"number": n, "path": mp4, "start": c.start, "end": c.end, "kind": c.kind})
            done_clips.append(c)
            numbers.append(n)
        except (OSError, RuntimeError, subprocess.SubprocessError) as exc:
            _remove(tmp_mp4)
            sessionlog.event(f"Extrait {n} impossible : {exc}")
    if results:
        try:
            with open(txt_name(target), "w", encoding="utf-8") as handle:
                handle.write(hl.caption_text(os.path.splitext(os.path.basename(target))[0], done_clips, numbers))
        except OSError as exc:
            sessionlog.event(f"Fichier texte des extraits impossible : {exc}")
    return results


def generate(path: str, ffmpeg: str, directory: str | None = None, *, run=subprocess.run, free_bytes=None) -> dict:
    """Chaine complete. Rend `{"state", "clips", "message", "txt", "video"}` ; `state` : `pret`, `vide` (aucun moment fort), `trop_court`, `disque`, `erreur`.
    Quand des extraits sont faits, l'enregistrement et ses extraits sont ranges dans UN dossier `<nom>/` (a cote de l'enregistrement) ; `video` = le nouveau
    chemin du MP4 (sinon `path`, inchange)."""
    free_bytes = free_bytes or (lambda d: shutil.disk_usage(d).free)
    folder = directory or os.path.dirname(os.path.abspath(path))

    def out(state: str, message: str, clips=None, txt=None, video=None) -> dict:
        return {"state": state, "clips": clips or [], "message": message, "txt": txt, "video": video or path}

    try:
        if free_bytes(folder) < MIN_FREE_BYTES:
            return out("disque", "Extraits : moins de 200 Mo libres, pas de decoupe.")
        energy, bpm, duration = ha.energy_series(path, ffmpeg, STEP_S)
        if not energy and duration == 0.0:
            return out("erreur", "Extraits : analyse du son impossible.")
        if duration < MIN_RECORDING_S:
            return out("trop_court", "Extraits : enregistrement trop court (moins de 60 s).")
        picked = hl.pick_clips(energy, STEP_S, bpm=bpm)
        if not picked:
            return out("vide", "Extraits : aucun moment fort trouve.")
        dest, made = path, None
        try:                                          # un dossier par ensemble (enregistrement + extraits) ; s'il ne peut pas etre cree, tout reste a cote
            made = os.path.join(os.path.dirname(os.path.abspath(path)), os.path.splitext(os.path.basename(path))[0])
            os.makedirs(made, exist_ok=True)
            dest = os.path.join(made, os.path.basename(path))
        except OSError:
            made = None
        files = make_clips(path, picked, ffmpeg, run=run, dest=dest)
        if made and dest != path:
            if files:
                try:
                    os.replace(path, dest)
                except OSError as exc:
                    sessionlog.event(f"Enregistrement non range dans son dossier : {exc}")
                    dest = path
            else:
                try:
                    os.rmdir(made)
                except OSError:
                    pass
                dest = path
    except Exception as exc:                          # jamais d'exception vers le fil d'assemblage : l'enregistrement est deja sauve
        sessionlog.error("Extraits automatiques", exc)
        return out("erreur", f"Extraits impossibles : {exc}")
    if not files:
        return out("erreur", "Extraits : la decoupe a echoue.")
    sessionlog.event(f"Extraits : {len(files)} pret(s) pour {path}" + (f" (BPM {bpm:.0f})" if bpm else ""))
    return out("pret", f"{len(files)} extrait(s) pret(s)" + (f" dans {os.path.dirname(dest)}" if dest != path else ""), files, txt_name(dest), dest)
