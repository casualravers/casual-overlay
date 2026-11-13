"""Rapport de bug en zip (issue #136) : meme chose que `audio2wave_gl.py --rapport-bug`, depuis le dossier tools/.

    python tools/rapport_bug.py "ce qui s'est passe"     -> zip sur le Bureau (journal, plantages, diagnostic, systeme, reglages sans secrets)
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from engine import bugreport  # noqa: E402

if __name__ == "__main__":
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("description", nargs="?", default="", help="Ce qui s'est passe (facultatif)")
    p.add_argument("--dossier", default=None, help="Dossier du zip (defaut : le Bureau)")
    p.add_argument("--synthetic", action="store_true", help="Diagnostic sans exiger ffmpeg ni micro")
    a = p.parse_args()
    sys.exit(bugreport.run_cli(a.description, ROOT / "sources" / "audio2wave", a.synthetic, Path(a.dossier) if a.dossier else None))
