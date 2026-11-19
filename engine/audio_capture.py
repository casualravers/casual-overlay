"""Capture du son de l'enregistrement dans un PROCESSUS A PART (19/11/2026).

Pourquoi : la capture, l'analyse, le rendu GL et l'interface partagent le verrou Python du programme. Un enregistrement de 3 min en 1920x1080 a montre 15 gels
de plus d'1 s (lecture d'image GL qui garde le verrou pendant l'attente du GPU) : le callback de la carte son ne passait plus et ~15 s de son etaient perdues.
Ce module ouvre la meme entree dans un petit programme sans interface ni GL (il n'importe que numpy, sounddevice et la bibliotheque standard) : un gel du
programme principal ne l'atteint plus. WASAPI partage l'entree entre plusieurs clients ; si l'ouverture echoue (pilote exclusif, ASIO...), `Recorder` retombe sur
l'ecoute dans le programme (l'ancien fonctionnement).

Contenu : `capture_age` et `RawAudioWriter` (ecriture brute du son stereo, sur un fil), `CaptureChild` (cote programme principal : lance le processus, lit son etat,
expose les memes attributs que `RawAudioWriter`), `main` (cote processus : `python engine/audio_capture.py ...`, lance comme un script : il n'importe rien du programme,
ce qui marche aussi dans le Python embarque du dossier portable).
Horloge : `time.monotonic()` (la meme dans les deux processus, c'est l'horloge du systeme).
Protocole : le fils ecrit sur stdout une ligne JSON `{"ready": true}` quand le flux est ouvert, puis son etat toutes les 0,25 s, puis `{"final": true, ...}` ; le parent
lui envoie `stop <instant de fin ou ->` sur stdin (la fermeture de stdin arrete aussi proprement : le fils ne survit jamais au programme).
"""
from __future__ import annotations

import json
import os
import queue
import subprocess
import sys
import threading
import time

import numpy as np

_NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)
_ABOVE_NORMAL = getattr(subprocess, "ABOVE_NORMAL_PRIORITY_CLASS", 0)       # le son passe avant le rendu pour le systeme
REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

GAP_S = 0.04                       # une PERTE SIGNALEE (pilote : input_overflow, ou bloc abandonne par la file) avec plus de 40 ms d'ecart d'horodatage est comblee par du silence
GAP_HARD_S = 1.0                   # sans signal du pilote, seul un trou de plus d'1 s (carte debranchee) est comble : un bloc livre EN RETARD (verrou Python tenu, interface
                                   # qui gele) arrive entier juste apres, y ajouter du silence ferait glisser la fin du son (18/11/2026 : 126 trous de 35 a 61 ms, 5,2 s de silence en trop sur 162 s)
T_FIRST_REFINE_S = 3.0             # le 1er bloc peut arriver en retard (le demarrage du REC charge la machine) : l'instant de depart se corrige sur les premieres secondes
CHUNK_FRAMES = 1 << 16             # le silence d'un long trou est ecrit par morceaux (pas de gros tableau d'un coup)
STATUS_EVERY_S = 0.25              # le fils donne son etat au parent a cette cadence
READY_TIMEOUT_S = 6.0              # un fils qui n'a pas ouvert la carte son apres ce delai est abandonne (repli sur l'ecoute dans le programme)
STOP_TIMEOUT_S = 30.0              # arret : le fils vide sa file et ferme le fichier


def capture_age(time_info, frames: int, rate: float, max_age_s: float = 1.0) -> float:
    """Age (secondes) du DERNIER echantillon d'un bloc au moment du callback : horloge du pilote (`currentTime` moins l'instant
    de capture du premier echantillon, `inputBufferAdcTime`, plus la duree du bloc). Un pilote qui ne renseigne pas ces temps (0, valeurs
    absurdes, negatives ou absentes) donne 0 : le bloc est alors date a l'instant du callback, comme avant."""
    try:
        adc = float(time_info.inputBufferAdcTime)
        cur = float(time_info.currentTime)
    except (AttributeError, TypeError, ValueError):
        return 0.0
    if adc <= 0.0 or cur <= 0.0:
        return 0.0
    age = cur - (adc + frames / float(rate))
    return age if 0.0 <= age <= max_age_s else 0.0


class RawAudioWriter:
    """Range les blocs stereo `float32` de la capture dans un fichier brut `f32le` entrelace, sur un fil a part.

    `push` est appele par le callback audio : il ne bloque jamais (file pleine = bloc perdu, compte dans `lost`). Les blocs sont dates ;
    un bloc qui manque (file pleine, carte debranchee un instant) est remplace par du silence pour que la duree du fichier reste celle
    de l'enregistrement, y compris en fin de fichier (jusqu'au dernier instant recu). Attributs lus par l'interface : `t_first` (instant du premier
    echantillon, sur l'horloge des blocs), `peak` (crete absolue depuis le debut, 0..1), `frames`, `bytes_written`, `lost`.
    """

    def __init__(self, path: str, channels: int, rate: int, max_queue: int = 512):
        self.path = path
        self.channels = channels
        self.rate = rate
        self.t_first: float | None = None
        self.peak = 0.0
        self.frames = 0                                   # echantillons par canal ecrits (silence compris)
        self.bytes_written = 0
        self.lost = 0
        self.cuts = 0                                     # coupures comblees en silence (pertes signalees, carte disparue) ...
        self.cut_frames = 0                               # ... et leur duree en echantillons
        self.late_blocks = 0                              # blocs livres en retard de plus de GAP_S (information : rien n'est ajoute au fichier)
        self.late_max_s = 0.0
        self.error: str | None = None                     # erreur d'ecriture (cle retiree, disque plein) : le fil vide la file sans bloquer
        self._t_last_end: float | None = None             # fin du dernier bloc RECU (meme perdu)
        self._pushed = 0                                  # echantillons recus par push (meme abandonnes), pour corriger l'instant de depart
        self._lost_pending = False                        # un bloc a ete abandonne (file pleine) : le prochain porte la perte
        self._closed = False
        self._lock = threading.Lock()
        self._queue: queue.Queue = queue.Queue(maxsize=max_queue)
        self._thread = threading.Thread(target=self._run, name="rec-audio", daemon=True)
        self._thread.start()

    def push(self, block, t_end: float, lost: bool = False) -> None:
        """Appele par le callback audio (jamais bloquant). `block` : (echantillons, canaux) float32 ; `t_end` : instant de fin du bloc (celui de l'arrivee quand le
        pilote ne date pas ses blocs) ; `lost` : le pilote a signale des blocs perdus (input_overflow) avant celui-ci."""
        if self._closed:
            return
        frames = len(block)
        self._pushed += frames
        start = t_end - self._pushed / self.rate          # instant du premier echantillon DEDUIT de ce bloc : au plus tard (un bloc arrive jamais en avance)
        if self.t_first is None:
            self.t_first = t_end - frames / self.rate
        elif not lost and not self._lost_pending and self._pushed / self.rate <= T_FIRST_REFINE_S and start < self.t_first:
            self.t_first = start                          # le plus petit ecart = le bloc le moins retarde : l'instant le plus juste
        self._t_last_end = t_end
        peak = float(np.abs(block).max()) if frames else 0.0
        if peak > self.peak:
            self.peak = peak
        try:
            self._queue.put_nowait((block, t_end, lost or self._lost_pending))
            self._lost_pending = False
        except queue.Full:
            self.lost += 1
            self._lost_pending = True

    @property
    def t_last_end(self) -> float | None:
        """Instant de fin du dernier bloc recu (None tant qu'aucun n'est arrive) : l'enregistreur s'en sert pour dire que le son a disparu."""
        return self._t_last_end

    def close(self, end_t: float | None = None) -> None:
        """Vide la file, comble le silence de fin et ferme le fichier. `end_t` : instant de fin de l'enregistrement (le son qui a disparu en
        cours de route est complete en silence jusque-la). Sans effet la deuxieme fois."""
        with self._lock:
            if self._closed:
                return
            self._closed = True
        if end_t is not None and self.t_first is not None and (self._t_last_end is None or end_t > self._t_last_end):
            self._t_last_end = end_t
        self._queue.put(None)
        self._thread.join(timeout=30)

    # --- fil d'ecriture -------------------------------------------------------------------------------

    def _write(self, handle, data: bytes) -> None:
        handle.write(data)
        self.bytes_written += len(data)

    def _silence(self, handle, frames: int) -> None:
        silence = bytes(4 * self.channels * min(frames, CHUNK_FRAMES))
        while frames > 0:
            k = min(frames, CHUNK_FRAMES)
            self._write(handle, silence[:4 * self.channels * k])
            self.frames += k
            frames -= k

    def _fill_to(self, handle, instant: float, signalled: bool = False) -> None:
        """Ecrit du silence si le fichier est en retard sur `instant` (horloge des blocs) : de plus de `GAP_S` quand une perte est SIGNALEE, de plus de `GAP_HARD_S`
        sinon. Un simple retard de livraison n'est pas une perte : les echantillons arrivent, le silence les ferait glisser."""
        expected = self.t_first + self.frames / self.rate
        gap = instant - expected
        if gap > GAP_S and not signalled:
            self.late_blocks += 1
            self.late_max_s = max(self.late_max_s, gap)
        if gap > (GAP_S if signalled else GAP_HARD_S):
            frames = int(round(gap * self.rate))
            self._silence(handle, frames)
            self.cuts += 1
            self.cut_frames += frames

    def _run(self) -> None:
        try:
            with open(self.path, "wb") as handle:
                while True:
                    item = self._queue.get()
                    if item is None:
                        break
                    block, t_end, lost = item
                    self._fill_to(handle, t_end - len(block) / self.rate, lost)
                    self._write(handle, np.ascontiguousarray(block, dtype="<f4").tobytes())
                    self.frames += len(block)
                if self._t_last_end is not None and self.t_first is not None:
                    self._fill_to(handle, self._t_last_end, self._lost_pending)       # fin : le son disparu en cours de route (plus d'1 s) ou des blocs abandonnes
        except OSError as exc:          # cle retiree, disque plein : on garde ce qui est ecrit et on vide la file (close() ne doit jamais bloquer)
            self.error = str(exc)
            while self._queue.get() is not None:
                pass


# --- etat echange entre le fils et le parent ----------------------------------------------------------

STATE_FIELDS = ("t_first", "peak", "frames", "bytes_written", "lost", "cuts", "cut_frames", "late_blocks", "late_max_s", "t_last_end", "error")


def state_of(writer: RawAudioWriter) -> dict:
    """Etat du writer, tel qu'envoye au parent (une ligne JSON)."""
    return {name: getattr(writer, name) for name in STATE_FIELDS}


def parse_line(line: str) -> dict | None:
    """Une ligne du fils -> dict ; None si ce n'est pas du JSON (un message de bibliotheque, une ligne coupee)."""
    try:
        data = json.loads(line)
    except (ValueError, TypeError):
        return None
    return data if isinstance(data, dict) else None


def stop_command(end_t: float | None) -> str:
    return "stop " + ("-" if end_t is None else repr(float(end_t))) + "\n"


def parse_stop(line: str) -> tuple[bool, float | None]:
    """Ligne recue par le fils -> (arreter ?, instant de fin). Toute autre ligne est ignoree."""
    parts = line.split()
    if not parts or parts[0] != "stop":
        return False, None
    if len(parts) > 1 and parts[1] != "-":
        try:
            return True, float(parts[1])
        except ValueError:
            pass
    return True, None


# --- cote programme principal -----------------------------------------------------------------------

def child_command(device, rate: int, channels: int, blocksize: int, latency_s: float, out_path: str, source: str = "device") -> list[str]:
    """Ligne de commande du fils (`device` : numero de l'entree sounddevice)."""
    return [sys.executable, os.path.abspath(__file__), "--device", str(device), "--rate", str(int(rate)), "--channels", str(int(channels)),
            "--blocksize", str(int(blocksize)), "--latency", repr(float(latency_s)), "--out", out_path, "--source", source]


class CaptureChild:
    """Le processus de capture vu du programme principal : memes attributs que `RawAudioWriter` (`t_first`, `peak`, `frames`, `bytes_written`, `lost`, `cuts`,
    `cut_frames`, `late_blocks`, `late_max_s`, `t_last_end`, `error`, `path`, `rate`), plus `state` (`starting` / `ready` / `failed` / `done`) et `reason`.

    Ne bloque jamais le fil appelant : `spawn` lance le processus, un fil lit ses lignes. `close` (fil d'assemblage) demande l'arret et attend la fin du fichier.
    """

    def __init__(self, path: str, channels: int, rate: int):
        self.path, self.channels, self.rate = path, channels, int(rate)
        for name in STATE_FIELDS:
            setattr(self, name, None if name in ("t_first", "t_last_end", "error") else 0)
        self.peak = 0.0
        self.late_max_s = 0.0
        self.state = "starting"
        self.reason = ""
        self.proc = None
        self._reader: threading.Thread | None = None
        self._final = threading.Event()
        self._t_spawn = time.monotonic()

    @classmethod
    def spawn(cls, command: list[str], path: str, channels: int, rate: int) -> "CaptureChild":
        """Lance le processus (leve OSError si Python ne demarre pas)."""
        child = cls(path, channels, rate)
        child.proc = subprocess.Popen(command, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, cwd=REPO_ROOT, text=True, bufsize=1,
                                      creationflags=_NO_WINDOW | _ABOVE_NORMAL)
        child._reader = threading.Thread(target=child._read, name="rec-audio-child", daemon=True)
        child._reader.start()
        return child

    def _read(self) -> None:
        try:
            for line in self.proc.stdout:
                self.apply(parse_line(line))
        except (OSError, ValueError):
            pass
        finally:
            if self.state == "starting":
                self.state, self.reason = "failed", self.reason or "le processus s'est arrete avant d'avoir ouvert la carte son"
            elif self.state == "ready":
                self.state = "done"
            self._final.set()

    def apply(self, data: dict | None) -> None:
        """Prend en compte une ligne du fils (dict deja decode ; None ignore)."""
        if not data:
            return
        if data.get("failed"):
            self.state, self.reason = "failed", str(data.get("failed"))
            return
        for name in STATE_FIELDS:
            if name in data:
                setattr(self, name, data[name])
        if data.get("ready") and self.state == "starting":
            self.state = "ready"
        if data.get("final"):
            self.state = "done"
            self._final.set()

    @property
    def alive(self) -> bool:
        return self.proc is not None and self.proc.poll() is None

    def timed_out(self, now: float) -> bool:
        return self.state == "starting" and now - self._t_spawn > READY_TIMEOUT_S

    def abandon(self) -> None:
        """Tue le processus (repli : on ne garde rien de ce qu'il a ecrit)."""
        self.state = "failed"
        if self.proc is not None:
            try:
                self.proc.kill()
            except OSError:
                pass
        try:
            os.remove(self.path)
        except OSError:
            pass

    def close(self, end_t: float | None = None) -> None:
        """Demande l'arret (le fils comble le silence de fin jusqu'a `end_t`), attend la fermeture du fichier. Sans effet la deuxieme fois."""
        proc = self.proc
        if proc is None:
            return
        try:
            proc.stdin.write(stop_command(end_t))
            proc.stdin.flush()
            proc.stdin.close()
        except (OSError, ValueError):
            pass
        self._final.wait(STOP_TIMEOUT_S)
        try:
            proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            proc.kill()
        if self._reader is not None:
            self._reader.join(timeout=2)


# --- cote processus de capture ----------------------------------------------------------------------

def _emit(obj: dict) -> None:
    sys.stdout.write(json.dumps(obj) + "\n")
    sys.stdout.flush()


class _TestSource:
    """Source de test (`--source test`) : un signal de 220 Hz livre en temps reel, sans carte son (verifie tout le protocole dans les tests)."""

    def __init__(self, rate: int, channels: int, blocksize: int, callback):
        self.rate, self.channels, self.blocksize, self.callback = rate, channels, blocksize, callback
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._run, daemon=True)

    def start(self) -> None:
        self._thread.start()

    def _run(self) -> None:
        k, t_next = 0, time.monotonic()
        while not self._stop.is_set():
            t = (np.arange(self.blocksize) + k * self.blocksize) / self.rate
            block = (0.2 * np.sin(2 * np.pi * 220 * t)).astype(np.float32)
            self.callback(np.repeat(block[:, None], self.channels, axis=1), self.blocksize, None, None)
            k += 1
            t_next += self.blocksize / self.rate
            delay = t_next - time.monotonic()
            if delay > 0:
                time.sleep(delay)

    def stop(self) -> None:
        self._stop.set()

    def close(self) -> None:
        self._thread.join(timeout=2)


def main(argv: list[str] | None = None) -> int:
    import argparse
    ap = argparse.ArgumentParser(prog="audio_capture")
    ap.add_argument("--device", default="0")
    ap.add_argument("--rate", type=int, required=True)
    ap.add_argument("--channels", type=int, default=2)
    ap.add_argument("--blocksize", type=int, default=512)
    ap.add_argument("--latency", type=float, default=0.1)
    ap.add_argument("--out", required=True)
    ap.add_argument("--source", choices=("device", "test"), default="device")
    args = ap.parse_args(argv)
    writer = RawAudioWriter(args.out, 2, args.rate)
    state = {"prev_end": None}

    def callback(indata, frames, time_info, status):
        now = time.monotonic()
        lost = bool(status) and bool(getattr(status, "input_overflow", False))
        t_end = now - capture_age(time_info, frames, args.rate)
        block = indata.copy() if indata.shape[1] == 2 else np.repeat(indata, 2, axis=1)
        writer.push(block, t_end, lost)

    try:
        if args.source == "test":
            stream = _TestSource(args.rate, 2, args.blocksize, callback)
        else:
            import sounddevice as sd
            device = int(args.device) if args.device.lstrip("-").isdigit() else args.device
            stream = sd.InputStream(device=device, channels=args.channels, samplerate=args.rate, blocksize=args.blocksize, dtype="float32",
                                    latency=args.latency, callback=callback)
        stream.start()
    except Exception as exc:                       # carte occupee en exclusif, pilote absent... : le parent retombe sur l'ecoute dans le programme
        _emit({"failed": f"{type(exc).__name__}: {exc}"[:200]})
        writer.close()
        try:
            os.remove(args.out)
        except OSError:
            pass
        return 2
    _emit({"ready": True, **state_of(writer)})
    stop = threading.Event()
    end_t: list = [None]

    def listen() -> None:
        try:
            for line in sys.stdin:
                ok, instant = parse_stop(line)
                if ok:
                    end_t[0] = instant
                    break
        except (OSError, ValueError):
            pass
        stop.set()                                  # arret demande, ou stdin ferme (le programme est mort) : on termine proprement

    threading.Thread(target=listen, name="stdin", daemon=True).start()
    while not stop.wait(STATUS_EVERY_S):
        _emit(state_of(writer))
    try:
        stream.stop()
        stream.close()
    except Exception:
        pass
    writer.close(end_t=end_t[0])
    _emit({"final": True, **state_of(writer)})
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
