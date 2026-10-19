"""Session du moteur : etat partage GL / GUI / fils de fond, entree audio d'analyse, gestionnaire sans producteur.

Extrait tel quel de `audio2wave_gl` (qui re-exporte ces noms).
"""
from __future__ import annotations

import copy
import queue
import threading
from typing import TYPE_CHECKING

from engine.audio import AudioAnalyzer, FeatureExtractor, RingBuffer, SoundDeviceInput, SyntheticAudio
from engine.deps import sd
from engine.video import FrameReader, ProducerManager, spawn_producer

if TYPE_CHECKING:
    from engine.render import Renderer
    from engine.window import Window


class AudioController:
    """Entree audio d'analyse (sounddevice ou synthetique) + analyseur, avec changement
    d'entree a chaud (GUI). L'extracteur est recree si la frequence d'echantillonnage change."""

    def __init__(self, ring: RingBuffer, sensitivity: float, synthetic: bool, device=None):
        self.ring = ring
        self.synthetic = synthetic
        if synthetic:
            self.src = SyntheticAudio(ring)
            self.rate = 48000
            self.name = "synthetique (kick 60 Hz, 120 BPM)"
        else:
            self.src = SoundDeviceInput(device, ring)
            self.rate = self.src.rate
            self.name = self.src.name
        self.analyzer = AudioAnalyzer(ring, FeatureExtractor(self.rate, sensitivity=sensitivity))

    def start(self) -> None:
        self.analyzer.start()
        self.src.start()

    def stop(self) -> None:
        self.analyzer.stop()
        try:
            self.src.stop()
        except Exception:
            pass

    def switch(self, device) -> str:
        """Bascule sur une autre entree sounddevice. Leve une exception si elle est refusee
        (l'entree precedente reste alors active)."""
        new = SoundDeviceInput(device, self.ring)
        new.start()
        old, self.src = self.src, new
        try:
            old.stop()
        except Exception:
            pass
        if new.rate != self.rate:
            self.rate = new.rate
            self.analyzer.extractor = FeatureExtractor(
                new.rate, sensitivity=self.analyzer.extractor.sensitivity)
        self.name = new.name
        return new.name

    @staticmethod
    def input_choices() -> list[tuple[int, str]]:
        hostapis = sd.query_hostapis()
        return [(i, f"[{hostapis[d['hostapi']]['name'].replace('Windows ', '')}] {d['name']}")
                for i, d in enumerate(sd.query_devices()) if d["max_input_channels"] > 0]


class Session:
    """Etat partage entre le fil GL, la GUI (fil principal) et les fils de fond.

    `params` (reglages du rendu) est relu a chaque image par le fil GL et muté librement par la
    GUI (affectations atomiques). `commands` transporte les actions qui doivent s'executer DANS
    le fil GL (tous les appels glfw doivent partir du fil qui a initialise glfw)."""

    def __init__(self, args, params, live, live_args, render_size, win_size, audio, manager, reader):
        self.args, self.params = args, params
        self.live, self.live_args = live, live_args
        self.render_size, self.win_size = render_size, win_size
        self.audio, self.manager, self.reader = audio, manager, reader
        self.status: dict = manager.status if manager else {}
        self.status.setdefault("fps", 0.0)
        self.commands: queue.SimpleQueue = queue.SimpleQueue()
        self.stop_event = threading.Event()               # fermeture de tout le programme
        self.finished_event = threading.Event()
        self.proj_stop = threading.Event()                # fermeture de la projection seule (la GUI reste ouverte)
        self.projection: object | None = None         # avec --gui : la fenetre de rendu s'ouvre a la demande
        self.gui_point_fn = None           # GUI : point de la fenetre de reglages (fil Tk) ; `gui_point` en est la copie lue par le fil GL
        self.gui_point: tuple[int, int] | None = None
        self.renderer: Renderer | None = None
        self.window: Window | None = None
        self.mode = "live"                 # source du fond : "live" (ffmpeg), "snap" ou "ridge" (py_modes)
        self.pysrc = None                  # PyModeSource courant (modes snap / ridge)
        self.mode_args: dict = {}          # options de chaque mode Snap / Ridge, gardees entre deux bascules
        self.scene_requests: queue.SimpleQueue = queue.SimpleQueue()   # F1..F9 (fil GL) -> la GUI applique la scene


class NoDeviceManager(ProducerManager):
    """ProducerManager sans producteur (GUI lancee sans -d): le premier request_restart()
    avec une entree choisie demarre le flux."""

    def __init__(self, live, live_args, frame_size: int):
        self.live, self.args, self.frame_size, self.status = live, copy.copy(live_args), frame_size, {}
        self._spawn = lambda a: spawn_producer(live, a)
        self.producer = None
        self.reader = FrameReader(_EmptyStream(), frame_size, hold_on_eof=True)
        self.restarts = 0
        self._request = None
        self._lock = threading.Lock()
        self._wake = threading.Event()
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._run, daemon=True)
        self.status["live"] = "Choisis une entree audio dans la fenetre de reglages"


class _EmptyStream:
    def read(self, n: int) -> bytes:
        return b""
