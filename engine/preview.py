"""Apercu d'une scene : une capture reduite de la fenetre de rendu, enregistree a la demande.

La lecture du tampon se fait dans le fil GL (voir `engine.projection.loop`) ; ici seulement la mise en forme du fichier.
"""
from __future__ import annotations

from pathlib import Path

from engine.deps import PIL_Image

PREVIEW_WIDTH = 240


def write_preview(rgb: bytes, size: tuple[int, int], dest: str | Path, width: int = PREVIEW_WIDTH) -> bool:
    """Ecrit un JPEG reduit (`width` px de large) de l'image `rgb` (RGB, premiere ligne en BAS comme le lit OpenGL, `size` = largeur x hauteur).
    Rend False (sans exception) si Pillow manque ou si l'ecriture echoue : un apercu manque ne doit jamais gener la projection."""
    if PIL_Image is None or size[0] <= 0 or size[1] <= 0:
        return False
    try:
        img = PIL_Image.frombytes("RGB", tuple(size), rgb).transpose(PIL_Image.FLIP_TOP_BOTTOM)
        height = max(int(round(width * size[1] / size[0])), 1)
        img = img.resize((width, height), PIL_Image.LANCZOS)
        dest = Path(dest)
        dest.parent.mkdir(parents=True, exist_ok=True)
        img.save(dest, "JPEG", quality=82)
        return True
    except Exception:
        return False
