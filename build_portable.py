"""Fabrique la version PORTABLE de casual-overlay : un dossier qui demarre sur un PC Windows sans rien installer.

    python build_portable.py                       # dist/casual-overlay-portable/
    python build_portable.py --zip                 # + un .zip a copier sur une cle USB
    python build_portable.py --ffmpeg-dir C:\\ffmpeg\\bin --no-verify

Contenu du dossier :
    python/        un Python 3.12 complet (copie de l'installation locale : tkinter compris) + les modules de requirements-gl.txt
    audio2wave/    les scripts du depot audio2wave (dependance, jamais modifies)
    ffmpeg/        ffmpeg.exe et ffprobe.exe (ajoutes au PATH par le programme, aucune installation)
    *.py, gl_shaders/, asset/ (+ asset/images/, sans animations ni videos), start.bat, LISEZMOI.txt

On ne gele PAS le programme (PyInstaller) : audio2wave est importe dynamiquement, tkinter et les modules de calcul changent selon
le mode, et un Python complet reste le plus fiable. Fichier stdlib seulement, Windows seulement.
Licence : un build ffmpeg « full » (GPL) accompagne le dossier : ne le redistribue pas publiquement sans en respecter les termes.
"""
from __future__ import annotations

import argparse
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
APP_FILES = ("audio2wave_gl.py", "gl_gui.py", "gui_colors.py", "gui_gates.py", "py_modes.py", "diagnostics.py", "skin.py", "theme.py",
             "check_gl.py", "requirements-gl.txt", "start.bat", "README.md")
APP_DIRS = ("gl_shaders", "core")
PY_SKIP_DIRS = {"Doc", "include", "libs", "Scripts", "__pycache__"}            # a la racine de l'installation Python
LIB_SKIP = {"test", "tests", "idlelib", "lib2to3", "turtledemo", "ensurepip", "__pycache__", "site-packages"}
SITE_SKIP = ("pip", "setuptools", "wheel", "_distutils_hack", "pkg_resources", "__pycache__")
TCL_SKIP = ("demos",)

README_PORTABLE = """casual-overlay - version portable
================================

1. Branche ton micro (ou ta carte son).
2. Double-clique sur start.bat : la fenetre de reglages et la fenetre OpenGL s'ouvrent.
   (Avec deux ecrans, la fenetre OpenGL passe en plein ecran sur le second ; touche F pour basculer, Echap pour quitter.)

Rien a installer : Python, ffmpeg et audio2wave sont dans ce dossier.

Verifier ce PC :   start.bat --diagnostic
   -> dit clairement ce qui manque (ffmpeg, OpenGL 3.3, micro...) et comment y remedier.
Sans micro ni ffmpeg : start.bat --synthetic --no-fullscreen

Tes scenes, sets et presets sont ranges dans le dossier de l'utilisateur (.audio2wave), pas dans ce dossier :
on peut remplacer ce dossier par une version plus recente sans rien perdre.

Si Windows SmartScreen se plaint au premier lancement : "Informations complementaires" puis "Executer quand meme".
"""


def log(msg: str) -> None:
    print(msg, flush=True)


def find_base_python() -> Path:
    """Dossier de l'installation Python qui sert de base au venv (ou a l'interpreteur courant)."""
    cfg = HERE / ".venv" / "pyvenv.cfg"
    if cfg.is_file():
        for line in cfg.read_text(encoding="utf-8").splitlines():
            if line.lower().startswith("home") and "=" in line:
                return Path(line.split("=", 1)[1].strip())
    return Path(sys.base_prefix)


def copy_tree(src: Path, dst: Path, skip=lambda p: False) -> int:
    """Copie recursive (octets copies) sans les entrees pour lesquelles `skip(chemin_relatif)` est vrai."""
    total = 0
    for root, dirs, files in os.walk(src):
        rel_root = Path(root).relative_to(src)
        dirs[:] = [d for d in dirs if not skip(rel_root / d)]
        (dst / rel_root).mkdir(parents=True, exist_ok=True)
        for name in files:
            rel = rel_root / name
            if skip(rel) or name.endswith(".pyc"):
                continue
            shutil.copy2(Path(root) / name, dst / rel)
            total += (Path(root) / name).stat().st_size
    return total


def build_python(base: Path, out: Path) -> None:
    for need in ("python.exe", "python312.dll", "Lib", "tcl", "DLLs"):
        if not (base / need).exists():
            raise SystemExit(f"Installation Python incomplete ({base}) : {need} manquant. Installe Python 3.12 depuis python.org "
                             "(avec tcl/tk), puis recree .venv.")
    dst = out / "python"
    dst.mkdir(parents=True, exist_ok=True)

    def lib_skip(rel: Path) -> bool:
        if len(rel.parts) == 1:
            return rel.parts[0] in LIB_SKIP                  # site-packages vient du venv, pas de l'installation
        return rel.parts[-1] in ("test", "tests", "__pycache__")

    for entry in base.iterdir():
        if entry.name in PY_SKIP_DIRS or entry.name.startswith("NEWS"):
            continue
        if entry.is_file():
            shutil.copy2(entry, dst / entry.name)
        elif entry.name == "Lib":
            copy_tree(entry, dst / "Lib", skip=lib_skip)
        elif entry.name == "tcl":
            copy_tree(entry, dst / "tcl", skip=lambda rel: rel.parts[-1] in TCL_SKIP)
        else:
            copy_tree(entry, dst / entry.name)
    site = HERE / ".venv" / "Lib" / "site-packages"
    if not site.is_dir():
        raise SystemExit("Pas de .venv/Lib/site-packages : cree l'environnement (py -3.12 -m venv .venv puis pip install "
                         "-r requirements-gl.txt) avant de fabriquer la version portable.")
    copy_tree(site, dst / "Lib" / "site-packages",
              skip=lambda rel: rel.parts[0].lower().split("-")[0] in SITE_SKIP or rel.parts[0] in SITE_SKIP)


def build_app(out: Path, a2w_dir: Path) -> None:
    for name in APP_FILES:
        src = HERE / name
        if src.is_file():
            shutil.copy2(src, out / name)
    for name in APP_DIRS:
        copy_tree(HERE / name, out / name)
    asset = out / "asset"
    asset.mkdir(exist_ok=True)
    for sub in ("", "images"):                              # les fichiers de asset/ et de asset/images/ (logos), pas les animations ni les videos perso
        src_dir = HERE / "asset" / sub
        if not src_dir.is_dir():
            continue
        (asset / sub).mkdir(exist_ok=True)
        for f in src_dir.iterdir():
            if f.is_file():
                shutil.copy2(f, asset / sub / f.name)
    if not (a2w_dir / "audio2wave_live.py").is_file():
        raise SystemExit(f"audio2wave introuvable ({a2w_dir}) : indique-le avec --a2w-dir.")
    dst = out / "audio2wave"
    dst.mkdir(exist_ok=True)
    for f in a2w_dir.glob("*.py"):
        shutil.copy2(f, dst / f.name)
    (out / "LISEZMOI.txt").write_text(README_PORTABLE, encoding="utf-8")


def build_ffmpeg(out: Path, ffmpeg_dir: Path | None) -> list[str]:
    """Copie ffmpeg.exe / ffprobe.exe (et les DLL voisines d'un build partage). Rend la liste des fichiers copies."""
    if ffmpeg_dir is None:
        found = shutil.which("ffmpeg")
        if not found:
            raise SystemExit("ffmpeg introuvable : installe-le (winget install --id Gyan.FFmpeg), indique --ffmpeg-dir, "
                             "ou construis sans (--no-ffmpeg : l'utilisateur devra l'installer).")
        ffmpeg_dir = Path(found).parent
    dst = out / "ffmpeg"
    dst.mkdir(exist_ok=True)
    copied = []
    for name in ("ffmpeg.exe", "ffprobe.exe"):
        src = ffmpeg_dir / name
        if not src.is_file():
            raise SystemExit(f"{name} introuvable dans {ffmpeg_dir}.")
        shutil.copy2(src, dst / name)
        copied.append(name)
    for dll in ffmpeg_dir.glob("*.dll"):
        shutil.copy2(dll, dst / dll.name)
        copied.append(dll.name)
    return copied


def dir_size_mb(path: Path) -> float:
    return sum(f.stat().st_size for f in path.rglob("*") if f.is_file()) / 1048576


def verify(out: Path) -> bool:
    """Lance le dossier fabrique avec SON Python, comme le ferait l'utilisateur : diagnostic, puis un rendu synthetique court."""
    py = out / "python" / "python.exe"
    env = {k: v for k, v in os.environ.items() if k not in ("PYTHONPATH", "PYTHONHOME", "VIRTUAL_ENV")}
    ok = True
    for label, cmd in (("diagnostic", ["--diagnostic"]),
                       ("rendu synthetique 3 s", ["--synthetic", "--no-fullscreen", "--max-seconds", "3", "--screenshot",
                                                  str(out / "_verif.png")])):
        res = subprocess.run([str(py), str(out / "audio2wave_gl.py"), *cmd], capture_output=True, text=True, cwd=out, env=env,
                             timeout=120)
        good = res.returncode == 0
        ok = ok and good
        log(f"  verification ({label}) : {'OK' if good else 'ECHEC (code ' + str(res.returncode) + ')'}")
        if not good:
            log((res.stdout + res.stderr)[-1500:])
    (out / "_verif.png").unlink(missing_ok=True)
    return ok


def main() -> int:
    ap = argparse.ArgumentParser(description="Fabrique la version portable de casual-overlay (Windows).")
    ap.add_argument("--out", default=str(HERE / "dist" / "casual-overlay-portable"))
    ap.add_argument("--a2w-dir", default=os.environ.get("AUDIO2WAVE_DIR", str(HERE.parent / "audio2wave")))
    ap.add_argument("--ffmpeg-dir", default=None, help="Dossier de ffmpeg.exe / ffprobe.exe (defaut : celui du PATH)")
    ap.add_argument("--no-ffmpeg", action="store_true", help="Ne pas embarquer ffmpeg (il faudra l'installer sur le PC cible)")
    ap.add_argument("--no-verify", action="store_true", help="Ne pas lancer le dossier fabrique pour le verifier")
    ap.add_argument("--zip", action="store_true", help="Cree aussi <out>.zip")
    args = ap.parse_args()
    if os.name != "nt":
        raise SystemExit("La version portable est une version Windows (Python embarque).")
    out = Path(args.out).resolve()
    if out.exists():
        log(f"Remplace {out}")
        shutil.rmtree(out)
    out.mkdir(parents=True)
    t0 = time.monotonic()
    log("Python..."); build_python(find_base_python(), out)
    log("Programme et audio2wave..."); build_app(out, Path(args.a2w_dir))
    if args.no_ffmpeg:
        log("ffmpeg non embarque (--no-ffmpeg).")
    else:
        log("ffmpeg..."); files = build_ffmpeg(out, Path(args.ffmpeg_dir) if args.ffmpeg_dir else None)
        big = dir_size_mb(out / "ffmpeg")
        log(f"  {len(files)} fichier(s), {big:.0f} Mo" + ("  (un build 'essentials' est plus leger)" if big > 250 else ""))
    log(f"Dossier pret : {out} ({dir_size_mb(out):.0f} Mo, {time.monotonic() - t0:.0f} s)")
    if not args.no_verify and not verify(out):
        log("La verification a echoue : le dossier n'est pas fiable.")
        return 1
    if args.zip:
        log("Zip...")
        archive = shutil.make_archive(str(out), "zip", root_dir=out.parent, base_dir=out.name)
        log(f"  {archive} ({Path(archive).stat().st_size / 1048576:.0f} Mo)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
