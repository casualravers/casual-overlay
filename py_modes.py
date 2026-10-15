"""Modes Snap et Ridge d'audio2wave comme sources du fond.

audio2wave_snap.py et audio2wave_ridge.py dessinent leurs images eux-memes (en Python). Avec l'API hote d'audio2wave on les lance
SANS fenetre ni ffplay : `parse_args(argv)` construit leurs options, `start_headless(args, size=..., frame_sink=f, status=...)`
ouvre la capture et leur fil de rendu, et `f(frame, size)` range chaque image dans un `PushStream` : le `FrameReader` du rendu GL la lit
comme n'importe quel flux video. Aucun fichier d'audio2wave n'est modifie, plus aucun remplacement de leur module `subprocess` ni
d'echange de `sys.argv`.
"""

from __future__ import annotations

import sys
import threading
from pathlib import Path

MODES = ("live", "snap", "ridge")
MODE_LABELS = {"live": "Live", "snap": "Snap", "ridge": "Ridge"}


class PushStream:
    """Flux alimente par `write()` (une image entiere a la fois), lu par `readinto()`.

    Derniere image gagne : si le lecteur n'a pas encore pris l'image precedente, elle est
    remplacee. `readinto()` bloque tant qu'il n'y a rien, et rend 0 (fin de flux) apres
    `close()`. Une image n'est jamais melangee a une autre : celle en cours de lecture est
    figee dans `_cur` jusqu'a la fin de sa copie."""

    def __init__(self, frame_size: int | None = None) -> None:
        self.frame_size = frame_size
        self._cond = threading.Condition()
        self._next: bytes | None = None
        self._cur: bytes | None = None
        self._off = 0
        self.closed = False
        self.frames_written = 0

    def write(self, data) -> int:
        with self._cond:
            if self.closed:
                raise BrokenPipeError("flux ferme")
            self._next = bytes(data)
            self.frames_written += 1
            self._cond.notify_all()
        return len(data)

    def readinto(self, buf) -> int:
        with self._cond:
            while self._cur is None:
                if self._next is not None:
                    self._cur, self._next, self._off = self._next, None, 0
                    break
                if self.closed:
                    return 0
                self._cond.wait(0.1)
            n = min(len(buf), len(self._cur) - self._off)
            buf[:n] = self._cur[self._off:self._off + n]
            self._off += n
            if self._off >= len(self._cur):
                self._cur = None
            return n

    def read(self, n: int) -> bytes:
        tmp = bytearray(n)
        got = self.readinto(memoryview(tmp))
        return bytes(tmp[:got])

    def close(self) -> None:
        with self._cond:
            self.closed = True
            self._cond.notify_all()


def load_mode(a2w_dir: Path, name: str):
    """Importe audio2wave_snap / audio2wave_ridge (le dossier d'audio2wave est deja dans sys.path
    des que audio2wave_live est charge)."""
    if str(a2w_dir) not in sys.path:
        sys.path.insert(0, str(a2w_dir))
    return __import__(f"audio2wave_{name}")


def make_args(module, name: str, device: str | None, size: tuple[int, int]):
    """Options du mode, comme `enter_gui()` d'audio2wave (`parse_args(argv)`), mais a la taille du rendu."""
    args = module.parse_args(["--gui"] + (["-d", device] if device else []))
    args.size = f"{size[0]}x{size[1]}"
    args.fullscreen = False                  # sans objet avec un puits d'images (c'est notre fenetre GL qui affiche)
    return args


class PyModeSource:
    """Un mode Snap/Ridge qui tourne dans son fil (`start_headless`) et alimente `self.reader` (FrameReader du rendu GL).

    `reader_cls` : la classe FrameReader du rendu (passee pour ne pas importer le script principal). Apres `start()`, `stop_event`,
    `finished_event` et `capture_state` sont ceux de la session d'audio2wave (a passer a leur `build_gui`)."""

    def __init__(self, name: str, module, args, size: tuple[int, int], reader_cls, status: dict | None = None) -> None:
        self.name, self.module, self.args, self.size = name, module, args, size
        self.status = status if status is not None else {}
        self.stream = PushStream(size[0] * size[1] * 3)
        self.reader = reader_cls(self.stream, size[0] * size[1] * 3)
        self.stop_event = threading.Event()
        self.finished_event = threading.Event()
        self.capture_state: dict = {}
        self.session = None
        self.thread: threading.Thread | None = None

    def _sink(self, frame, size: tuple[int, int]) -> None:
        """Puits d'images : `frame` est le tampon de travail de `run()` (copie par `PushStream.write`) ; une taille differente de
        celle du rendu est ignoree (jamais de decalage)."""
        if tuple(size) == tuple(self.size):
            self.stream.write(frame)

    def start(self) -> None:
        self.reader.start()
        self.session = self.module.start_headless(self.args, size=self.size, frame_sink=self._sink, status=self.status)
        self.stop_event, self.finished_event = self.session.stop_event, self.session.finished_event
        self.capture_state = self.session.capture_state
        self.thread = self.session.thread
        threading.Thread(target=self._watch, name=f"mode-{self.name}-fin", daemon=True).start()

    def _watch(self) -> None:
        """Un mode ne doit jamais faire tomber la fenetre : sa fin (normale ou non) est dite dans son statut."""
        self.thread.join()
        if not self.stop_event.is_set() and not str(self.status.get("text", "")).startswith(self.name):
            self.status["text"] = f"{self.name}: arret"

    # -- arret -------------------------------------------------------------------------
    def stop(self) -> None:
        """Arret immediat cote GL (le flux est ferme, `run()` s'arrete a sa prochaine ecriture) ;
        la fin propre du fil (capture ffmpeg terminee) se fait en arriere-plan."""
        self.stop_event.set()
        self.stream.close()
        self.reader.stop()

    def join(self, timeout: float | None = None) -> bool:
        if self.thread is not None:
            self.thread.join(timeout)
            return not self.thread.is_alive()
        return True
