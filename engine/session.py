"""Session du moteur : etat partage GL / GUI / fils de fond, entree audio d'analyse, gestionnaire sans producteur.

Extrait tel quel de `audio2wave_gl` (qui re-exporte ces noms).
"""
from __future__ import annotations

import copy
import queue
import threading
import time
from typing import TYPE_CHECKING

from core import sessionlog
from engine.audio import AudioAnalyzer, FeatureExtractor, RingBuffer, SoundDeviceInput, SyntheticAudio, capture_block
from engine.deps import sd
from engine.video import FrameReader, ProducerManager, spawn_producer

if TYPE_CHECKING:
    from engine.render import Renderer
    from engine.window import Window


class AudioController:
    """Entree audio d'analyse (sounddevice ou synthetique) + analyseur, avec changement
    d'entree a chaud (GUI). L'extracteur est recree si la frequence d'echantillonnage change."""

    GUARD_EVERY_S = 1.0

    def __init__(self, ring: RingBuffer, sensitivity: float, synthetic: bool, device=None, *, input_factory=SoundDeviceInput,
                 block: int | None = None, on_message=None):
        self.ring = ring
        self.synthetic = synthetic
        self.device = device
        self._factory = input_factory
        self.block = block or capture_block()          # taille de bloc de capture en cours (512 ; 256 avec CASUAL_AUDIO_BLOCK=256, 512 apres un repli)
        self.fell_back = False
        self.on_message = on_message                   # rappel (texte) : statut de la GUI, pose par run_app
        self._guard_stop = threading.Event()
        self._lock = threading.RLock()                 # repli (fil de garde), changement d'entree et relecture des peripheriques (fil Tk) : un a la fois
        if synthetic:
            self.src = SyntheticAudio(ring)
            self.rate = 48000
            self.name = "synthetique (kick 60 Hz, 120 BPM)"
        else:
            self.src = self._factory(device, ring, blocksize=self.block)
            self.rate = self.src.rate
            self.name = self.src.name
        self.analyzer = AudioAnalyzer(ring, FeatureExtractor(self.rate, sensitivity=sensitivity))

    def start(self) -> None:
        self.analyzer.start()
        self.src.start()
        if not self.synthetic:
            threading.Thread(target=self._guard, name="audio-garde", daemon=True).start()

    def stop(self) -> None:
        self._guard_stop.set()
        self.analyzer.stop()
        try:
            self.src.stop()
        except Exception:
            pass

    def _guard(self) -> None:
        """Toutes les secondes : la carte son deborde-t-elle avec des blocs courts ? (repli a 512, une fois)."""
        while not self._guard_stop.wait(self.GUARD_EVERY_S):
            try:
                self.check_block(time.monotonic())
            except Exception as exc:                   # la garde ne doit jamais tuer l'analyse
                sessionlog.error("Garde de la capture audio", exc)

    def check_block(self, now: float) -> str | None:
        """Repli automatique : avec des blocs de moins de 512, plus de OVERFLOW_MAX debordements en OVERFLOW_WINDOW_S = la meme entree est
        rouverte avec des blocs de 512. Un enregistrement en cours s'arrete proprement (comme a tout changement d'entree : le point d'ecoute
        n'est pas recopie). Une seule fois par session ; jamais en synthetique. Rend le message (aussi passe a `on_message` et au journal), ou None."""
        with self._lock:
            if self.synthetic or self.fell_back or self.block >= 512:
                return None
            burst = getattr(self.src, "overflow_burst", None)
            if burst is None or not burst(now):
                return None
            self.fell_back = True
            try:
                new = self._factory(self.device, self.ring, blocksize=512)
                new.start()
            except Exception as exc:                   # entree debranchee entre-temps : on garde celle de 256 et on le dit
                msg = f"Carte son : debordements avec des blocs de 256, retour a 512 impossible ({exc})"
                sessionlog.event(msg)
                self._say(msg)
                return msg
            old, self.src = self.src, new
            try:
                old.stop()
            except Exception:
                pass
            self.block = 512
        msg = "Carte son : blocs de 256 trop courts (debordements), retour a 512"
        sessionlog.event(msg)
        self._say(msg)
        return msg

    def _say(self, msg: str) -> None:
        if self.on_message is not None:
            try:
                self.on_message(msg)
            except Exception:
                pass

    def switch(self, device) -> str:
        """Bascule sur une autre entree sounddevice. Leve une exception si elle est refusee
        (l'entree precedente reste alors active). La taille de bloc en cours est gardee."""
        with self._lock:
            new = self._factory(device, self.ring, blocksize=self.block)
            new.start()
            self.device = device
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
    def input_choices(keep: str | None = None) -> list[tuple[int, str]]:
        """Entrees disponibles. Windows expose chaque entree physique une fois par API audio (MME, DirectSound, WASAPI, WDM-KS) :
        quand WASAPI en fournit, on ne garde qu'elle (les doublons ne servent a rien), sauf l'entree `keep` (celle qui tourne)."""
        hostapis = sd.query_hostapis()
        rows = [(i, d, hostapis[d["hostapi"]]["name"]) for i, d in enumerate(sd.query_devices()) if d["max_input_channels"] > 0]
        if any("WASAPI" in api for _i, _d, api in rows):
            rows = [r for r in rows if "WASAPI" in r[2] or (keep and keep in r[1]["name"])]
        return [(i, f"[{api.replace('Windows ', '')}] {d['name']}") for i, d, api in rows]

    def refresh_devices(self) -> bool:
        """Relit la liste des peripheriques (PortAudio la fige au demarrage : un micro branche apres n'apparait pas). Reinitialise PortAudio,
        ce qui coupe le flux un instant : l'entree courante est rouverte par son nom. Faux si rien n'a ete fait (synthetique, erreur)."""
        if self.synthetic:
            return False
        with self._lock:                               # la garde ne doit pas ouvrir une entree pendant que PortAudio est reinitialise
            name = self.name
            try:
                self.src.stop()
            except Exception:
                pass
            try:
                sd._terminate()
                sd._initialize()
            except Exception:
                pass
            for query in (name, None):
                try:
                    from engine.audio import find_input_device
                    dev = find_input_device(query) if query else None
                    new = self._factory(dev, self.ring, blocksize=self.block)          # taille de bloc en cours (512 apres un repli)
                    new.start()
                    self.src, self.device = new, dev
                    return True
                except (Exception, SystemExit):
                    continue
            return False


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
        self.doodle_queue: queue.SimpleQueue = queue.SimpleQueue()   # doodles decodes par le fil de recuperation, lus par le fil GL
        self.status: dict = manager.status if manager else {}
        self.status.setdefault("fps", 0.0)
        self.commands: queue.SimpleQueue = queue.SimpleQueue()
        self.stop_event = threading.Event()               # fermeture de tout le programme
        self.finished_event = threading.Event()
        self.proj_stop = threading.Event()                # fermeture de la projection seule (la GUI reste ouverte)
        self.projection: object | None = None         # avec --gui : la fenetre de rendu s'ouvre a la demande
        self.gui_point_fn = None           # GUI : point de la fenetre de reglages (fil Tk) ; `gui_point` en est la copie lue par le fil GL
        self.gui_point: tuple[int, int] | str | None = None       # point (x, y) de la GUI, ou NOM de son moniteur (Windows : fiable meme si un ecran est mis a l'echelle)
        self.gui_hwnd: int | None = None                          # fenetre de reglages (Windows) : permet de relire son moniteur depuis le fil GL, a jour si on la deplace
        self.renderer: Renderer | None = None
        self.window: Window | None = None
        self.mode = "live"                 # source du fond : "live" (ffmpeg), "snap" ou "ridge" (py_modes)
        self.pysrc = None                  # PyModeSource courant (modes snap / ridge)
        self.mode_args: dict = {}          # options de chaque mode Snap / Ridge, gardees entre deux bascules
        self.scene_requests: queue.SimpleQueue = queue.SimpleQueue()   # F1..F9 (fil GL) -> la GUI applique la scene
        self.preview_requests: queue.SimpleQueue = queue.SimpleQueue()  # chemins de fichiers d'apercu a ecrire (GUI -> fil GL, voir engine.preview)
        self.remote_requests: queue.SimpleQueue = queue.SimpleQueue()   # commandes validees de la telecommande telephone (fil HTTP -> fil Tk, voir gui_remote)
        self.recording = None              # RecordingControl de la boucle GL (engine.recorder) tant que la projection est ouverte


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
