"""Diagnostic de demarrage : ce qu'il manque sur ce PC, dit clairement, au lieu d'un plantage.

Chaque verification rend un `Check` (`ok`, `warn` ou `fail`) avec un detail et, si quelque chose manque, comment y remedier.
`fail` = le programme ne peut pas demarrer comme demande ; `warn` = il demarre mais sans une fonction (micro, glisser-deposer...).
Tout ce qui touche au systeme (import, PATH, sous-processus, contexte OpenGL, peripheriques audio) est **injectable** : les tests
simulent un PC auquel il manque ffmpeg, OpenGL ou le micro sans rien desinstaller.

Utilisation : `python audio2wave_gl.py --diagnostic` (rapport complet, code de sortie 1 s'il y a une erreur) ; au lancement
normal, `preflight()` s'execute seul : une erreur arrete le programme avec le rapport, les avertissements sont affiches et
repris dans la fenetre de reglages (Affichage > Diagnostic).
"""
from __future__ import annotations

import importlib
import os
import shutil
import subprocess
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path

MIN_PYTHON = (3, 10)
MIN_OPENGL = 330                      # version_code : 3.3 (les shaders sont en #version 330)
SOFTWARE_RENDERERS = ("gdi generic", "llvmpipe", "softpipe", "swrast", "microsoft basic render", "software")
LABELS = {"ok": "OK", "warn": "ATTENTION", "fail": "ERREUR"}

FFMPEG_HINT = ("Installe ffmpeg (winget install --id Gyan.FFmpeg) ou pose ffmpeg.exe et ffprobe.exe dans le dossier "
               "ffmpeg/ a cote du programme.")


@dataclass
class Check:
    name: str
    level: str                          # "ok" | "warn" | "fail"
    detail: str
    hint: str = ""


def check_python(version=None) -> Check:
    v = tuple(version if version is not None else sys.version_info[:3])
    text = ".".join(str(x) for x in v)
    if v[:2] < MIN_PYTHON:
        return Check("Python", "fail", f"version {text} trop ancienne", "Installe Python 3.12 (python.org).")
    if v[:2] != (3, 12):
        return Check("Python", "ok", f"{text} (3.12 recommande : moderngl n'a pas de version pour les plus recentes)")
    return Check("Python", "ok", text)


def check_modules(import_module=importlib.import_module, synthetic: bool = False, gui: bool = True) -> list[Check]:
    """Modules Python : obligatoires (fail), tkinter (la GUI), tkinterdnd2 (facultatif)."""
    out: list[Check] = []
    required = (("numpy", "numpy"), ("glfw", "glfw"), ("moderngl", "moderngl"), ("PIL", "Pillow"), ("sounddevice", "sounddevice"))
    missing: list[str] = []
    for module, package in required:
        try:
            import_module(module)
        except Exception as exc:        # ImportError, ou OSError si PortAudio manque pour sounddevice
            if module == "sounddevice" and synthetic:
                out.append(Check("sounddevice", "warn", f"indisponible ({exc.__class__.__name__}) : inutile en mode --synthetic"))
                continue
            missing.append(package)
    if missing:
        out.append(Check("Modules Python", "fail", "manquants : " + ", ".join(dict.fromkeys(missing)),
                         "pip install -r requirements-gl.txt (ou utilise la version portable)."))
    else:
        out.append(Check("Modules Python", "ok", "numpy, glfw, moderngl, Pillow, sounddevice"))
    try:
        import_module("tkinter")
        out.append(Check("tkinter", "ok", "fenetre de reglages disponible"))
    except Exception:
        out.append(Check("tkinter", "fail" if gui else "warn", "indisponible : la fenetre de reglages (--gui) ne peut pas s'ouvrir",
                         "Reinstalle Python avec l'option tcl/tk, ou utilise la version portable."))
    try:
        import_module("tkinterdnd2")
        out.append(Check("tkinterdnd2", "ok", "glisser-deposer disponible"))
    except Exception:
        out.append(Check("tkinterdnd2", "warn", "absent : le glisser-deposer de fichiers est desactive (le reste marche)",
                         "pip install tkinterdnd2"))
    return out


def _run(cmd: list[str]) -> subprocess.CompletedProcess:
    return subprocess.run(cmd, capture_output=True, text=True, timeout=10,
                          creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))


def check_ffmpeg(which=shutil.which, run=_run, need: bool = True) -> list[Check]:
    """ffmpeg (fond audio2wave), son entree DirectShow (micro) et ffprobe (videos)."""
    out: list[Check] = []
    exe = which("ffmpeg")
    if exe is None:
        out.append(Check("ffmpeg", "fail" if need else "warn", "introuvable dans le PATH : le visuel d'audio2wave (spectre) ne peut pas tourner",
                         FFMPEG_HINT + " Le fond genere et le fond video n'ont pas besoin du spectre, mais ffmpeg reste requis."))
    else:
        try:
            first = (run([exe, "-version"]).stdout or "").splitlines()[0].split(" Copyright")[0]
            out.append(Check("ffmpeg", "ok", f"{first[:60]} ({exe})"))
            devices = (run([exe, "-hide_banner", "-devices"]).stdout or "")
            if os.name == "nt" and "dshow" not in devices:
                out.append(Check("ffmpeg / DirectShow", "warn", "cette version d'ffmpeg ne sait pas lire les micros (dshow absent)",
                                 "Utilise un build complet (Gyan.FFmpeg)."))
        except (OSError, subprocess.SubprocessError, IndexError) as exc:
            out.append(Check("ffmpeg", "fail" if need else "warn", f"trouve ({exe}) mais ne repond pas : {exc}", FFMPEG_HINT))
    probe = which("ffprobe")
    if probe is None:
        out.append(Check("ffprobe", "warn", "introuvable : le logo / fond video peuvent ne pas se charger", FFMPEG_HINT))
    else:
        out.append(Check("ffprobe", "ok", probe))
    return out


def check_audio2wave(a2w_dir: Path, need: bool = True) -> Check:
    target = Path(a2w_dir) / "audio2wave_live.py"
    if target.is_file():
        return Check("audio2wave", "ok", str(a2w_dir))
    return Check("audio2wave", "fail" if need else "warn", f"audio2wave_live.py introuvable dans {a2w_dir}",
                 "audio2wave est interne : le dossier sources/audio2wave/ (ou audio2wave/ pour la version portable) est incomplet ou absent. "
                 "Retablis-le depuis le depot, ou indique un autre dossier avec --a2w-dir (variable AUDIO2WAVE_DIR).")


def _create_standalone():
    import moderngl
    return moderngl.create_standalone_context()


def check_opengl(create=_create_standalone) -> Check:
    try:
        ctx = create()
    except Exception as exc:
        return Check("OpenGL", "fail", f"impossible d'ouvrir un contexte OpenGL ({exc})",
                     "Mets a jour les pilotes de la carte graphique. Pas de GPU (machine virtuelle, bureau a distance) : "
                     "OpenGL 3.3 est necessaire.")
    try:
        version = int(getattr(ctx, "version_code", 0))
        renderer = str((getattr(ctx, "info", None) or {}).get("GL_RENDERER", "?"))
    finally:
        try:
            ctx.release()
        except Exception:
            pass
    shown = f"{version // 100}.{version % 100 // 10}"
    if version < MIN_OPENGL:
        return Check("OpenGL", "fail", f"version {shown} (3.3 minimum) sur {renderer}",
                     "Mets a jour les pilotes de la carte graphique.")
    if any(word in renderer.lower() for word in SOFTWARE_RENDERERS):
        return Check("OpenGL", "warn", f"{shown} mais rendu LOGICIEL ({renderer}) : tres lent",
                     "Installe les pilotes du GPU ; en bureau a distance, lance le programme sur la session locale.")
    return Check("OpenGL", "ok", f"{shown} - {renderer}")


def check_microphone(sd=None, synthetic: bool = False) -> Check | None:
    if synthetic:
        return Check("Micro", "ok", "mode --synthetique : aucun micro necessaire")
    if sd is None:
        try:
            sd = importlib.import_module("sounddevice")
        except Exception:
            return None                  # deja signale par check_modules
    try:
        devices = sd.query_devices()
        inputs = [d for d in devices if d.get("max_input_channels", 0) > 0]
    except Exception as exc:
        return Check("Micro", "warn", f"liste des peripheriques audio illisible ({exc})",
                     "Verifie les pilotes audio, puis relance.")
    if not inputs:
        return Check("Micro", "warn", "aucune entree audio detectee : le visuel ne reagira a rien",
                     "Branche un micro ou une carte son. Windows : Parametres > Confidentialite > Microphone doit etre "
                     "autorise pour les applications de bureau.")
    try:
        default = sd.query_devices(kind="input").get("name", "")
    except Exception:
        default = ""
    return Check("Micro", "ok", f"{len(inputs)} entree(s)" + (f", par defaut : {default}" if default else ""))


def check_settings_dir(path: Path | None = None) -> Check:
    path = Path(path) if path is not None else Path.home() / ".audio2wave"
    try:
        path.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryFile(dir=path):
            pass
        return Check("Reglages", "ok", f"{path} (scenes, presets)")
    except OSError as exc:
        return Check("Reglages", "warn", f"dossier {path} non inscriptible ({exc})",
                     "Scenes et presets ne seront pas sauvegardes : verifie les droits du dossier.")


def run_checks(a2w_dir: Path, synthetic: bool = False, gui: bool = True, opengl: bool = True, *, import_module=None,
               which=shutil.which, run=_run, create_context=None, sd=None, settings_dir: Path | None = None,
               python_version=None) -> list[Check]:
    """Toutes les verifications, dans l'ordre d'affichage. `import_module`, `which`, `run`, `create_context`, `sd`,
    `settings_dir`, `python_version` : remplacables pour simuler un autre PC."""
    import_module = import_module or importlib.import_module
    checks = [check_python(python_version)]
    mods = check_modules(import_module, synthetic, gui)
    checks += mods
    checks += check_ffmpeg(which, run, need=not synthetic)
    checks.append(check_audio2wave(a2w_dir, need=not synthetic or gui))
    gl_ready = not any(c.name == "Modules Python" and c.level == "fail" for c in mods)
    if opengl and gl_ready:
        checks.append(check_opengl(create_context or _create_standalone))
    mic = check_microphone(sd, synthetic)
    if mic is not None:
        checks.append(mic)
    checks.append(check_settings_dir(settings_dir))
    return checks


def counts(checks: list[Check]) -> tuple[int, int]:
    return (sum(1 for c in checks if c.level == "fail"), sum(1 for c in checks if c.level == "warn"))


def format_report(checks: list[Check], only_problems: bool = False) -> str:
    lines = [] if only_problems else ["Diagnostic casual-overlay"]
    for c in checks:
        if only_problems and c.level == "ok":
            continue
        lines.append(f"  [{LABELS[c.level]}]".ljust(14) + f"{c.name} : {c.detail}")
        if c.hint and c.level != "ok":
            lines.append(" " * 14 + f"-> {c.hint}")
    fails, warns = counts(checks)
    if not only_problems:
        lines.append("Resultat : " + ("tout est en ordre." if not fails and not warns else
                                      f"{fails} erreur(s), {warns} avertissement(s)."))
    return "\n".join(lines)


def summary_line(checks: list[Check]) -> str:
    """Une ligne pour la fenetre de reglages ('' si tout est en ordre)."""
    fails, warns = counts(checks)
    if not fails and not warns:
        return ""
    names = ", ".join(c.name for c in checks if c.level != "ok")
    return f"Diagnostic : {fails + warns} point(s) a voir ({names}) - Affichage > Diagnostic"


def preflight(a2w_dir: Path, synthetic: bool = False, gui: bool = True, **injected) -> tuple[list[Check], bool]:
    """Verifications du lancement : rend (checks, ok). ok = False s'il y a une erreur (le programme ne peut pas demarrer)."""
    checks = run_checks(a2w_dir, synthetic, gui, **injected)
    return checks, counts(checks)[0] == 0


def run_cli(a2w_dir: Path, synthetic: bool = False, gui: bool = True) -> int:
    """`--diagnostic` : rapport complet, code de sortie 0 (aucune erreur) ou 1."""
    checks, ok = preflight(a2w_dir, synthetic, gui)
    print(format_report(checks))
    return 0 if ok else 1
