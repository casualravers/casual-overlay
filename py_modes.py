"""Modes Snap et Ridge d'audio2wave comme sources du fond.

audio2wave_snap.py et audio2wave_ridge.py dessinent leurs images eux-memes (en Python) et les
ecrivent dans l'entree standard d'un `ffplay` (`viewer.stdin.write(image)`, rgb24). Ici on les
fait tourner TELS QUELS dans notre processus, avec leur propre fil de rendu (`run()`), et on
remplace seulement ce `ffplay` par un faux visionneur qui range chaque image dans un
`PushStream` : le `FrameReader` du rendu GL la lit comme n'importe quel flux video.

Aucun fichier d'audio2wave n'est modifie : le module `subprocess` de snap/ridge est remplace par
une enveloppe (`_SubprocessShim`) qui laisse tout passer sauf le lancement de `ffplay`.
"""

from __future__ import annotations

import subprocess
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


class _Stdin:
    def __init__(self, viewer: "FakeViewer") -> None:
        self._viewer = viewer

    def write(self, data) -> int:
        v = self._viewer
        if v.closed:
            raise BrokenPipeError("visionneur ferme")
        if v.stream.frame_size is not None and len(data) != v.stream.frame_size:
            return len(data)               # image d'une autre taille que le rendu: ignoree (jamais de decalage)
        return v.stream.write(data)

    def flush(self) -> None:
        pass

    def close(self) -> None:
        self._viewer.closed = True


class FakeViewer:
    """Se fait passer pour le Popen de `ffplay` : ce que `run()` de snap/ridge en utilise.

    Fermer un visionneur ne ferme PAS le flux : `run()` en remplace un (nouveau `Popen`) quand la taille ou
    le plein ecran change (par exemple en chargeant un preset 'club' qui coche Plein ecran) ; seul
    `PyModeSource.stop()` ferme le flux."""

    def __init__(self, stream: PushStream) -> None:
        self.stream = stream
        self.closed = False
        self.stdin = _Stdin(self)
        self.returncode = None

    def poll(self):
        return 0 if (self.closed or self.stream.closed) else None

    def terminate(self) -> None:
        self.closed = True

    def wait(self, timeout=None):
        return 0


class _SubprocessShim:
    """Enveloppe de `subprocess` : tout est delegue, sauf `Popen(["ffplay", ...])` qui rend un
    `FakeViewer` branche sur `self.sink`."""

    def __init__(self, real=subprocess) -> None:
        self._real = real
        self.sink: PushStream | None = None

    def __getattr__(self, name):
        return getattr(self._real, name)

    def Popen(self, cmd, *args, **kwargs):          # noqa: N802 (meme nom que subprocess.Popen)
        if cmd and Path(str(cmd[0])).stem.lower() == "ffplay" and self.sink is not None:
            return FakeViewer(self.sink)
        return self._real.Popen(cmd, *args, **kwargs)


def install_shim(module) -> _SubprocessShim:
    """Pose l'enveloppe dans `module` (une seule fois) et la renvoie."""
    shim = module.subprocess
    if not isinstance(shim, _SubprocessShim):
        shim = _SubprocessShim(module.subprocess)
        module.subprocess = shim
    return shim


def load_mode(a2w_dir: Path, name: str):
    """Importe audio2wave_snap / audio2wave_ridge (le dossier d'audio2wave est deja dans sys.path
    des que audio2wave_live est charge)."""
    if str(a2w_dir) not in sys.path:
        sys.path.insert(0, str(a2w_dir))
    return __import__(f"audio2wave_{name}")


def make_args(module, name: str, device: str | None, size: tuple[int, int]):
    """Options du mode, comme `enter_gui()` d'audio2wave, mais a la taille du rendu et sans plein
    ecran (la fenetre GL s'en charge)."""
    argv = [f"audio2wave_{name}.py", "--gui"] + (["-d", device] if device else [])
    saved = sys.argv
    sys.argv = argv
    try:
        args = module.parse_args()
    finally:
        sys.argv = saved
    args.size = f"{size[0]}x{size[1]}"
    args.fullscreen = False
    return args


class PyModeSource:
    """Un mode Snap/Ridge qui tourne dans un fil et alimente `self.reader` (FrameReader du rendu GL).

    `reader_cls` : la classe FrameReader du rendu (passee pour ne pas importer le script principal).
    `capture_factory(args, module) -> (capture_proc, capture)` : remplacable par un faux (tests) ; par defaut
    le vrai ffmpeg dshow d'audio2wave."""

    def __init__(self, name: str, module, args, size: tuple[int, int], reader_cls, status: dict | None = None,
                 capture_factory=None) -> None:
        self.name, self.module, self.args, self.size = name, module, args, size
        self.status = status if status is not None else {}
        self.stream = PushStream(size[0] * size[1] * 3)
        self.reader = reader_cls(self.stream, size[0] * size[1] * 3)
        self.stop_event = threading.Event()
        self.finished_event = threading.Event()
        self.capture_state: dict = {}
        self.capture_proc = None
        self.thread: threading.Thread | None = None
        self._capture_factory = capture_factory or (lambda a, m: self._default_capture(a))
        self._shim = install_shim(module)

    # -- construction ------------------------------------------------------------------
    def _default_capture(self, args):
        m = self.module
        if not args.device:
            if self.name == "snap":                   # GUI lancee sans entree: rien a capturer pour l'instant
                return m.NoDeviceProcess(), m.NoDeviceCapture()
            raise RuntimeError("Choisis d'abord une entree audio")
        proc = m.subprocess.Popen(m.capture_command(args), stdout=subprocess.PIPE)
        return proc, m.LiveCapture(proc.stdout, m.chunk_size(args))

    def start(self) -> None:
        m, args, size = self.module, self.args, self.size
        if args.save_dir:
            args.save_dir.mkdir(parents=True, exist_ok=True)
        if args.rate is None:
            native = m.probe_device_rate(args) if args.device else None
            args.rate = native or m.DEFAULT_CAPTURE_RATE
        if self.name == "snap":
            background = m.probe_color(m.resolve_bg(args))
            ink = m.probe_color(m.resolve_colors(args)[0])
        else:
            background = m.probe_color(args.bg_color)
            ink = m.probe_color(args.colors)
        self.capture_proc, capture = self._capture_factory(self.args, m)
        self._shim.sink = self.stream
        viewer = FakeViewer(self.stream)
        if self.name == "snap":
            self.capture_state["capture"] = capture
            run_args = (args, size, background, ink, self.capture_proc, viewer, capture, self.status,
                        self.capture_state, self.stop_event, self.finished_event)
        else:
            canvas = bytearray(background * (size[0] * size[1]))
            run_args = (args, size, background, ink, self.capture_proc, viewer, capture, canvas,
                        self.status, self.stop_event, self.finished_event)
        self.reader.start()
        self.thread = threading.Thread(target=self._run, args=run_args, name=f"mode-{self.name}", daemon=True)
        self.thread.start()

    def _run(self, *run_args) -> None:
        try:
            self.module.run(*run_args)
        except Exception as exc:                      # un mode ne doit jamais faire tomber la fenetre
            self.status["text"] = f"{self.name}: arret ({exc})"
        finally:
            self.finished_event.set()

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
