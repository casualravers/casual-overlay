"""Rapport de bug en zip (issue #136) : raccourci vers `audio2wave_gl.py --rapport-bug`, depuis le dossier tools/.

    python tools/rapport_bug.py "ce qui s'est passe"     -> zip sur le Bureau (journal, plantages, diagnostic, systeme, reglages sans secrets)

Passe par `audio2wave_gl.py` : c'est lui qui trouve audio2wave et ffmpeg (copie interne, ou dossiers `audio2wave/` et `ffmpeg/` du portable).
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

if __name__ == "__main__":
    sys.exit(subprocess.call([sys.executable, str(ROOT / "audio2wave_gl.py"), "--rapport-bug", *sys.argv[1:2]] + sys.argv[2:]))
