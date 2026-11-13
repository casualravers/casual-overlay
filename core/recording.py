"""Enregistrement video + audio : logique pure (stdlib seulement, rien de GL, de ffmpeg lance ni de fenetre).

Spec : docs/plans/2026-11-13-enregistrement-video-design.md (issue #140). Ce module decide, sans rien executer :
  - le format et la cadence (`FORMATS`, `clean_fps`, `clean_format`) ;
  - combien d'images ecrire a un instant donne pour que la video soit a cadence constante (`frames_due`) ;
  - le decalage de l'audio par rapport a l'image (`audio_offset`) ;
  - le nom du fichier (`output_name`) et le dossier (`resolve_dir`) ;
  - les commandes ffmpeg (`video_command`, `mux_command`) et le choix de l'encodeur (`choose_encoder`) ;
  - la crete du son et les alertes (`peak_db`, `clipped`, `drop_warning`).
`engine/recorder.py` les assemble avec les fils d'ecriture et les processus.
"""
from __future__ import annotations

import math
import os
from datetime import datetime
from typing import Callable, Iterable

# (largeur, hauteur) de l'image enregistree. `vertical_hd` : pour les looks lourds (cellules, cumul d'effets), qui ne tiennent pas 30 images/s en 1080x1920.
FORMATS: dict[str, tuple[int, int]] = {
    "vertical": (1080, 1920),
    "vertical_hd": (720, 1280),
    "horizontal": (1920, 1080),
    "carre": (1080, 1080),
}
DEFAULT_FORMAT = "vertical"
DEFAULT_DIR = "~/Videos/casual-overlay"
HARDWARE_ENCODERS = ("h264_nvenc", "h264_amf", "h264_qsv")      # ordre de preference quand l'encodeur materiel est demande
DROP_WARN_RATIO = 0.02                                         # au-dela, le GPU n'a pas suivi : message
CLIP_LEVEL = 0.999                                             # crete (0..1) a partir de laquelle on parle de saturation
FLOOR_DB = -120.0
AUDIO_RATE_OUT = 48000
AUDIO_BITRATE = "320k"
X264_ARGS = ["-c:v", "libx264", "-preset", "veryfast", "-crf", "17"]


def clean_fps(value) -> int:
    """30 ou 60 : tout ce qui n'est pas un nombre, ou est sous 45, donne 30."""
    try:
        return 60 if float(value) >= 45 else 30
    except (TypeError, ValueError):
        return 30


def clean_format(value) -> str:
    return value if isinstance(value, str) and value in FORMATS else DEFAULT_FORMAT


def resolve_dir(value) -> str:
    """Dossier d'enregistrement : `~` developpe ; vide ou invalide = le dossier par defaut."""
    text = value.strip() if isinstance(value, str) else ""
    return os.path.normpath(os.path.expanduser(text or DEFAULT_DIR))        # normpath : pas de separateurs melanges sous Windows


def frames_due(t0: float, now: float, fps: int, sent: int) -> int:
    """Nombre d'images a ecrire maintenant pour que l'image numero k tombe a `t0 + k / fps` (`sent` = images deja ecrites). Un rendu en retard
    donne plusieurs images (l'appelant duplique la derniere : la duree reste juste), un rendu en avance donne 0 ; jamais negatif."""
    if fps <= 0:
        raise ValueError("fps doit etre positif")
    if now < t0:
        return 0
    return max(0, int(math.floor((now - t0) * fps + 1e-9)) + 1 - sent)


def audio_offset(t_video0: float, t_audio0: float, delay_ms: float) -> float:
    """Decalage (secondes) de l'audio dans le fichier : l'instant du premier echantillon moins celui de la premiere image, plus le delai de
    synchro (le visuel reagit apres le son : l'audio est retarde pour que l'effet tombe dessus). Positif = audio retarde."""
    return (t_audio0 - t_video0) + delay_ms / 1000.0


def output_name(now: datetime, directory: str, exists: Callable[[str], bool] = os.path.exists) -> str:
    """Chemin du fichier : `casual-AAAA-MM-JJ_HHhMMmSSs.mp4`, suffixe -2, -3... si le nom existe deja (deux enregistrements dans la meme seconde)."""
    stem = now.strftime("casual-%Y-%m-%d_%Hh%Mm%Ss")
    path = os.path.join(directory, stem + ".mp4")
    n = 2
    while exists(path):
        path = os.path.join(directory, f"{stem}-{n}.mp4")
        n += 1
    return path


def video_command(ffmpeg: str, size: tuple[int, int], fps: int, encoder: str, out: str) -> list[str]:
    """ffmpeg qui lit des images `rgb24` brutes (premiere ligne EN BAS, comme la lecture OpenGL : `vflip`) sur son entree standard et ecrit la video seule
    (a cadence constante, une image cle par seconde pour couper sans re-encoder, etiquettes bt709 pour l'etalonnage)."""
    cmd = [ffmpeg, "-y", "-loglevel", "error", "-f", "rawvideo", "-pix_fmt", "rgb24", "-s", f"{size[0]}x{size[1]}", "-r", str(fps), "-i", "-",
           "-an", "-vf", "vflip"]
    cmd += X264_ARGS if encoder == "libx264" else ["-c:v", encoder]
    cmd += ["-pix_fmt", "yuv420p", "-g", str(fps), "-colorspace", "bt709", "-color_primaries", "bt709", "-color_trc", "bt709", out]
    return cmd


def mux_command(ffmpeg: str, video: str, audio_raw: str, rate: int, channels: int, offset_s: float, out: str) -> list[str]:
    """Assemblage : la video est recopiee, l'audio brut (`f32le`, frequence et canaux d'origine) est encode en AAC. `offset_s` > 0 retarde l'audio
    (`-itsoffset`), < 0 coupe son debut (`-ss`)."""
    audio_in = ["-f", "f32le", "-ar", str(rate), "-ac", str(channels)]
    if offset_s > 0:
        audio_in += ["-itsoffset", f"{offset_s:.3f}"]
    elif offset_s < 0:
        audio_in += ["-ss", f"{-offset_s:.3f}"]
    return [ffmpeg, "-y", "-loglevel", "error", "-i", video, *audio_in, "-i", audio_raw, "-map", "0:v", "-map", "1:a", "-c:v", "copy",
            "-c:a", "aac", "-b:a", AUDIO_BITRATE, "-ar", str(AUDIO_RATE_OUT), "-movflags", "+faststart", out]


def choose_encoder(listed: Iterable[str], tested: Callable[[str], bool], hardware: bool = False) -> str:
    """`libx264` par defaut. Avec `hardware`, le premier encodeur materiel (nvenc, amf, qsv) qui est liste par ffmpeg ET dont l'essai d'une image
    reussit (un encodeur liste peut echouer sans le materiel) ; sinon `libx264`."""
    if hardware:
        available = set(listed)
        for name in HARDWARE_ENCODERS:
            if name in available and tested(name):
                return name
    return "libx264"


def peak_db(peak: float) -> float:
    """Crete (valeur absolue maximale, 1 = pleine echelle) en dBFS, avec un plancher a -120."""
    if peak <= 0.0:
        return FLOOR_DB
    return max(FLOOR_DB, 20.0 * math.log10(peak))


def clipped(peak: float) -> bool:
    """L'enregistrement est brut : un niveau a la pleine echelle est sature et ne se rattrape pas."""
    return peak >= CLIP_LEVEL


def drop_warning(dropped: int, total: int) -> str | None:
    """Message si plus de 2 % des images ont ete dupliquees (le GPU n'a pas suivi), sinon None."""
    if total <= 0 or dropped / total <= DROP_WARN_RATIO:
        return None
    return (f"{100 * dropped / total:.0f} % d'images dupliquees : le GPU n'a pas suivi. "
            "Choisis le format vertical_hd (720x1280), coupe les cellules ou baisse la cadence.")
