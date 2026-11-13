"""Rapport de bug (issue #136) : ce qui demande le moteur ou Windows (diagnostic, systeme, Bureau) puis ecriture du zip par `core.bugreport`.

Stdlib seulement (comme `engine.diagnostics`) : utilisable par `--rapport-bug` quand la GUI ne demarre plus. Aucun appel glfw ni OpenGL : les
moniteurs sont lus par Windows (le fil GL possede glfw, et un second contexte GL pendant le rendu est evite).
"""

from __future__ import annotations

import ctypes
import json
import platform
import subprocess
import sys
from datetime import datetime
from pathlib import Path

from core import bugreport as core_bug
from core import sessionlog
from engine import diagnostics

REPO_DIR = Path(__file__).resolve().parent.parent          # dossier du programme (git, ou VERSION dans le portable)


def settings_dir() -> Path:
    """Dossier des reglages (`~/.audio2wave`), celui du journal."""
    return Path(sessionlog.LOG_PATH).parent


def desktop_dir() -> Path | None:
    """Le Bureau tel que Windows le connait (il peut etre redirige par OneDrive) ; sinon `~/Desktop` s'il existe ; sinon None."""
    if sys.platform == "win32":
        try:
            buf = ctypes.create_unicode_buffer(260)
            if ctypes.windll.shell32.SHGetFolderPathW(None, 0x10, None, 0, buf) == 0 and buf.value and Path(buf.value).is_dir():   # CSIDL_DESKTOPDIRECTORY
                return Path(buf.value)
        except Exception:
            pass
    home = Path.home() / "Desktop"
    return home if home.is_dir() else None


def monitors() -> list[dict]:
    """Moniteurs vus par Windows : nom, rectangle, zone utile, principal, DPI ([] hors Windows ou en cas d'erreur)."""
    if sys.platform != "win32":
        return []
    found: list[dict] = []
    try:
        from ctypes import wintypes

        class MonitorInfoEx(ctypes.Structure):
            _fields_ = [("cbSize", wintypes.DWORD), ("rcMonitor", wintypes.RECT), ("rcWork", wintypes.RECT),
                        ("dwFlags", wintypes.DWORD), ("szDevice", ctypes.c_wchar * 32)]

        user32 = ctypes.windll.user32
        try:
            get_dpi = ctypes.windll.shcore.GetDpiForMonitor
        except Exception:
            get_dpi = None
        proc = ctypes.WINFUNCTYPE(ctypes.c_int, wintypes.HMONITOR, wintypes.HDC, ctypes.POINTER(wintypes.RECT), wintypes.LPARAM)

        def each(hmon, _hdc, _rect, _data):
            info = MonitorInfoEx()
            info.cbSize = ctypes.sizeof(MonitorInfoEx)
            if user32.GetMonitorInfoW(hmon, ctypes.byref(info)):
                m, w = info.rcMonitor, info.rcWork
                item = {"nom": info.szDevice, "rect": [m.left, m.top, m.right, m.bottom], "zone_utile": [w.left, w.top, w.right, w.bottom],
                        "principal": bool(info.dwFlags & 1)}
                if get_dpi is not None:
                    dx, dy = wintypes.UINT(), wintypes.UINT()
                    if get_dpi(hmon, 0, ctypes.byref(dx), ctypes.byref(dy)) == 0:
                        item["dpi"] = dx.value
                        item["echelle"] = round(dx.value / 96, 2)
                found.append(item)
            return 1
        user32.EnumDisplayMonitors(None, None, proc(each), 0)
    except Exception:
        return found
    return found


def system_info(gui_root=None, s=None) -> dict:
    """Systeme, Python, Tk, moniteurs ; avec `gui_root` la fenetre de reglages ; avec `s` (Session) la projection et le temps GPU."""
    info: dict = {"date": datetime.now().isoformat(timespec="seconds"), "systeme": platform.platform(), "python": sys.version.split()[0],
                  "executable": sys.executable, "portable": install_kind(), "moniteurs": monitors()}
    if gui_root is not None:                       # engine/ n'importe jamais Tk : la version vient de la fenetre de reglages
        try:
            info["tk"] = str(gui_root.tk.call("info", "patchlevel"))
            info["fenetre_reglages"] = {"geometrie": gui_root.winfo_geometry(), "etat": gui_root.state(),
                                        "ecran": [gui_root.winfo_screenwidth(), gui_root.winfo_screenheight()]}
        except Exception:
            pass
    if s is not None:
        status = getattr(s, "status", {}) or {}
        proj = getattr(s, "projection", None)
        running = getattr(proj, "running", None)
        try:
            info["projection_ouverte"] = bool(running()) if callable(running) else bool(running)     # Projection.running est une methode
        except Exception:
            info["projection_ouverte"] = "inconnu"
        for key in ("gpu_ms", "gpu_budget_ms", "perf", "msg"):
            if key in status:
                info[key] = status[key]
    return info


def install_kind() -> str:
    """« portable » (python\\ a cote du programme), « venv » ou « python du systeme »."""
    exe = Path(sys.executable).resolve()
    if exe.parent == (REPO_DIR / "python").resolve():
        return "portable"
    return "venv" if sys.prefix != getattr(sys, "base_prefix", sys.prefix) else "python du systeme"


def diagnostic_text(a2w_dir: Path, synthetic: bool = False, gui: bool = True, opengl: bool = True, startup_checks=None) -> str:
    """Rapport du diagnostic. `opengl` False : pas de second contexte GL (projection en cours) ; le resultat OpenGL du demarrage est repris."""
    checks = diagnostics.run_checks(Path(a2w_dir), synthetic, gui, opengl=opengl)
    if not opengl:
        gl_check = next((x for x in (startup_checks or []) if x.name == "OpenGL"), None)
        if gl_check is not None:
            checks.insert(next((i for i, x in enumerate(checks) if x.name == "Micro"), len(checks)), gl_check)
    return diagnostics.format_report(checks)


def make_report(description: str, *, settings: Path | None = None, out_dir: Path | None = None, diag: str = "", system: dict | None = None,
                fluidity: str | None = None, now: datetime | None = None, desktop=desktop_dir) -> tuple[Path, list[str]]:
    """Ecrit le zip et rend (chemin, notes). `out_dir` None : le Bureau, sinon `<reglages>/rapports` (cree)."""
    settings = Path(settings) if settings is not None else settings_dir()
    folder = Path(out_dir) if out_dir is not None else desktop()
    if folder is None:
        folder = settings / "rapports"
    folder.mkdir(parents=True, exist_ok=True)
    extras = {"description": description.strip(), "version": core_bug.version_info(REPO_DIR), "portable": install_kind(),
              "diagnostic.txt": diag, "systeme.json": json.dumps(system or {}, indent=1, ensure_ascii=False, default=str)}
    if fluidity:
        extras["fluidite.txt"] = fluidity
    entries, notes = core_bug.collect(settings, extras)
    path = core_bug.output_path(folder, now or datetime.now())
    core_bug.write_zip(path, entries)
    sessionlog.event(f"Rapport de bug ecrit : {path}")
    return path, notes


def explorer_command(path: Path) -> str:
    """Commande qui ouvre l'Explorateur sur le fichier, chemin ENTRE GUILLEMETS apres `/select,` (une liste d'arguments le citait en entier,
    `"/select,C:\\...\\a b.zip"`, et l'Explorateur ouvrait son dossier par defaut quand le chemin a des espaces : Bureau OneDrive)."""
    return f'explorer /select,"{path}"'


def diagnostic_subprocess(a2w_dir: Path, synthetic: bool = False, run=subprocess.run) -> str:
    """Diagnostic complet (OpenGL compris) dans un SOUS-PROCESSUS : un pilote graphique qui plante tue ce processus-la, pas le rapport (c'est
    justement le cas d'un plantage natif qu'on veut rapporter)."""
    cmd = [sys.executable, str(REPO_DIR / "audio2wave_gl.py"), "--diagnostic", "--a2w-dir", str(a2w_dir)] + (["--synthetic"] if synthetic else [])
    try:
        res = run(cmd, capture_output=True, text=True, timeout=120, cwd=str(REPO_DIR), creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    except (OSError, subprocess.SubprocessError) as exc:
        return f"Diagnostic impossible : {exc}"
    text = (res.stdout or "").strip()
    if res.returncode not in (0, 1) or "Resultat" not in text:
        return (f"Diagnostic interrompu (code de sortie {res.returncode}) : plantage probable (pilote graphique ?).\n"
                f"{text}\n{(res.stderr or '').strip()[-2000:]}").strip()
    return text


def run_cli(description: str, a2w_dir: Path, synthetic: bool = False, out_dir: Path | None = None, *, settings: Path | None = None,
            run=subprocess.run) -> int:
    """`--rapport-bug` : ecrit le rapport (diagnostic complet dans un sous-processus) et imprime le chemin. Code 0 / 1."""
    diag = diagnostic_subprocess(a2w_dir, synthetic, run)
    try:
        system = system_info()
    except Exception as exc:
        system = {"erreur": repr(exc)}
    try:
        path, notes = make_report(description, settings=settings, out_dir=out_dir, diag=diag, system=system)
    except Exception as exc:                       # jamais de trace : un message clair
        print(f"Rapport de bug impossible : {exc}", file=sys.stderr)
        return 1
    print(f"Rapport de bug ecrit : {path}")
    for n in notes:
        print(f"  {n}")
    return 0
