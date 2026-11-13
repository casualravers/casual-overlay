"""Enregistrement video + audio (issue #140, spec docs/plans/2026-11-13-enregistrement-video-design.md).

Ce module contient les morceaux qui possedent des fils et des fichiers. La logique pure (cadence, noms, commandes ffmpeg) est dans
`core.recording`. Pour l'instant : `RawAudioWriter`, l'ecriture brute du son stereo de la carte son (tache 3 du plan) ;
`Recorder` (images, encodage, assemblage) vient ensuite.
"""
from __future__ import annotations

import queue
import threading

from engine.deps import np

GAP_S = 0.04                       # un trou de plus de 40 ms dans l'horodatage des blocs est comble par du silence (la gigue normale est de quelques ms)
CHUNK_FRAMES = 1 << 16             # le silence d'un long trou est ecrit par morceaux (pas de gros tableau d'un coup)


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
        self._t_last_end: float | None = None             # fin du dernier bloc RECU (meme perdu)
        self._closed = False
        self._lock = threading.Lock()
        self._queue: queue.Queue = queue.Queue(maxsize=max_queue)
        self._thread = threading.Thread(target=self._run, name="rec-audio", daemon=True)
        self._thread.start()

    def push(self, block, t_end: float) -> None:
        """Appele par le callback audio (jamais bloquant). `block` : (echantillons, canaux) float32 ; `t_end` : instant de fin du bloc."""
        if self._closed:
            return
        frames = len(block)
        if self.t_first is None:
            self.t_first = t_end - frames / self.rate
        self._t_last_end = t_end
        peak = float(np.abs(block).max()) if frames else 0.0
        if peak > self.peak:
            self.peak = peak
        try:
            self._queue.put_nowait((block, t_end))
        except queue.Full:
            self.lost += 1

    def close(self) -> None:
        """Vide la file, comble le silence de fin et ferme le fichier. Sans effet la deuxieme fois."""
        with self._lock:
            if self._closed:
                return
            self._closed = True
        self._queue.put(None)
        self._thread.join()

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

    def _fill_to(self, handle, instant: float) -> None:
        """Ecrit du silence si le fichier est en retard de plus de `GAP_S` sur `instant` (par rapport a l'horloge des blocs)."""
        expected = self.t_first + self.frames / self.rate
        gap = instant - expected
        if gap > GAP_S:
            self._silence(handle, int(round(gap * self.rate)))

    def _run(self) -> None:
        with open(self.path, "wb") as handle:
            while True:
                item = self._queue.get()
                if item is None:
                    break
                block, t_end = item
                self._fill_to(handle, t_end - len(block) / self.rate)
                self._write(handle, np.ascontiguousarray(block, dtype="<f4").tobytes())
                self.frames += len(block)
            if self._t_last_end is not None and self.t_first is not None:
                self._fill_to(handle, self._t_last_end)
