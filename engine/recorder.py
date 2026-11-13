"""Enregistrement video + audio (issue #140, spec docs/plans/2026-11-13-enregistrement-video-design.md).

Ce module contient les morceaux qui possedent des fils et des fichiers. La logique pure (cadence, noms, commandes ffmpeg) est dans
`core.recording`. Pour l'instant : `RawAudioWriter`, l'ecriture brute du son stereo de la carte son (tache 3 du plan) ;
`Recorder` (images, encodage, assemblage) vient ensuite.
"""
from __future__ import annotations

import os
import queue
import shutil
import subprocess
import threading
import time
from datetime import datetime
from typing import Callable

from core import recording as core_rec
from core import sessionlog
from engine.deps import moderngl, np

_NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)       # pas de fenetre de console qui clignote sous Windows
QUEUE_FRAMES = 8                   # images en attente de l'encodeur : au-dela, le rendu attend (jamais d'image perdue en silence)
QUEUE_TIMEOUT_S = 2.0              # un encodeur qui ne rend pas la main en 2 s est considere comme bloque : arret propre
DISK_CHECK_S = 1.0                 # espace disque relu une fois par seconde de video
SILENT_WARN_S = 1.0                # plus de son depuis cette duree : avertissement


class RecorderError(Exception):
    """Un enregistrement qui ne peut pas demarrer (message destine a l'utilisateur)."""

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
        self.error: str | None = None                     # erreur d'ecriture (cle retiree, disque plein) : le fil vide la file sans bloquer
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

    def _fill_to(self, handle, instant: float) -> None:
        """Ecrit du silence si le fichier est en retard de plus de `GAP_S` sur `instant` (par rapport a l'horloge des blocs)."""
        expected = self.t_first + self.frames / self.rate
        gap = instant - expected
        if gap > GAP_S:
            self._silence(handle, int(round(gap * self.rate)))

    def _run(self) -> None:
        try:
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
        except OSError as exc:          # cle retiree, disque plein : on garde ce qui est ecrit et on vide la file (close() ne doit jamais bloquer)
            self.error = str(exc)
            while self._queue.get() is not None:
                pass


_ENCODER_CACHE: dict = {}                    # (ffmpeg, encodeur) -> resultat : lancer 1 a 4 ffmpeg a chaque REC gelait la projection (fil GL)


def list_encoders(ffmpeg: str) -> list[str]:
    """Noms des encodeurs video que ffmpeg dit connaitre (`ffmpeg -encoders`) ; liste vide si la commande echoue. Resultat garde pour la session."""
    if ("list", ffmpeg) in _ENCODER_CACHE:
        return list(_ENCODER_CACHE[("list", ffmpeg)])
    names = _list_encoders(ffmpeg)
    if names:
        _ENCODER_CACHE[("list", ffmpeg)] = names
    return list(names)


def _list_encoders(ffmpeg: str) -> list[str]:
    try:
        out = subprocess.run([ffmpeg, "-hide_banner", "-encoders"], capture_output=True, text=True, timeout=20, creationflags=_NO_WINDOW).stdout
    except (OSError, subprocess.SubprocessError):
        return []
    names = []
    for line in out.splitlines():
        parts = line.split()
        if len(parts) >= 2 and parts[0].startswith("V"):
            names.append(parts[1])
    return names


def try_encoder(ffmpeg: str, encoder: str) -> bool:
    """Un encodeur liste peut echouer sans le materiel : on encode une image noire pour voir. Resultat garde pour la session."""
    key = ("try", ffmpeg, encoder)
    if key not in _ENCODER_CACHE:
        _ENCODER_CACHE[key] = _try_encoder(ffmpeg, encoder)
    return _ENCODER_CACHE[key]


def _try_encoder(ffmpeg: str, encoder: str) -> bool:
    cmd = [ffmpeg, "-v", "error", "-f", "lavfi", "-i", "color=c=black:s=256x256:d=0.2", "-frames:v", "1", "-c:v", encoder, "-f", "null", "-"]
    try:
        return subprocess.run(cmd, capture_output=True, timeout=30, creationflags=_NO_WINDOW).returncode == 0
    except (OSError, subprocess.SubprocessError):
        return False


class Recorder:
    """Un enregistrement : images a cadence constante vers un ffmpeg, son stereo brut a cote, puis assemblage en un MP4.

    Utilisation (fil GL) : `start()`, puis a chaque image affichee `n = due(now)` et, si `n > 0`, `write(image_rgb, n)` (l'image est ecrite `n` fois :
    un rendu en retard repete l'image du moment pour les cases manquees et la duree reste juste), enfin `stop()` qui rend le chemin du MP4 (ou None). Tout ce qui peut mal tourner est dit
    dans `status()["warning"]` ; `should_stop` devient vrai quand l'enregistrement doit s'arreter de lui-meme (disque presque plein, encodeur bloque).

    Fichiers : le dossier recoit `casual-....mp4`. Pendant l'enregistrement il y a des temporaires `casual-....video.tmp.mkv` et `....audio.tmp.raw` ; ils sont supprimes
    apres un assemblage reussi et gardes sinon. Le MP4 est ecrit sous un nom provisoire puis renomme : jamais de fichier a moitie ecrit sous le bon nom.
    Horloge : `now` (de `due`) et les instants des blocs audio sont sur la meme (time.monotonic dans le programme ; simulee dans les tests).
    """

    def __init__(self, ffmpeg: str, directory: str, fmt: str = "vertical", fps: int = 30, delay_ms: float = 40.0, audio_input=None, hardware: bool = False, *,
                 size: tuple[int, int] | None = None, now_fn: Callable[[], datetime] = datetime.now, free_bytes: Callable[[str], int] | None = None,
                 listed_encoders: Callable[[], list[str]] | None = None, test_encoder: Callable[[str], bool] | None = None):
        self.ffmpeg = ffmpeg
        self.mux_ffmpeg = ffmpeg                                  # separe pour que l'echec de l'assemblage se teste
        self.directory = core_rec.resolve_dir(directory)
        self.fmt = core_rec.clean_format(fmt)
        self.fps = core_rec.clean_fps(fps)
        self.size = tuple(size) if size else core_rec.FORMATS[self.fmt]
        self.delay_ms = float(delay_ms)
        self.audio_input = audio_input
        self.hardware = hardware
        self._now_fn = now_fn
        self._free_bytes = free_bytes or (lambda p: shutil.disk_usage(p).free)
        self._listed = listed_encoders or (lambda: list_encoders(self.ffmpeg))
        self._tested = test_encoder or (lambda e: try_encoder(self.ffmpeg, e))
        self.encoder = "libx264"
        self.path = ""                                            # chemin final du MP4
        self.should_stop = False
        self.warning: str | None = None
        self.sent = 0                                             # images ecrites (doublons compris)
        self.duplicated = 0
        self._t0: float | None = None                             # instant de la premiere image
        self._proc = None
        self._writer: RawAudioWriter | None = None
        self._queue: queue.Queue = queue.Queue(maxsize=QUEUE_FRAMES)
        self._thread: threading.Thread | None = None
        self._video_error: str | None = None
        self._log = None
        self._next_disk_check = 0
        self._started = False
        self._result: str | None = None
        self._stopped = False

    # --- noms ------------------------------------------------------------------------------------------

    def _tmp(self, kind: str, path: str | None = None) -> str:
        base = (path or self.path)[:-len(".mp4")]
        return f"{base}.{kind}.tmp.{ {'video': 'mkv', 'audio': 'raw', 'log': 'log', 'mux': 'mp4'}[kind] }"

    # --- demarrage -------------------------------------------------------------------------------------

    def pick_encoder(self) -> str:
        return core_rec.choose_encoder(self._listed() if self.hardware else [], self._tested, self.hardware)

    def start(self) -> None:
        try:
            os.makedirs(self.directory, exist_ok=True)
            free = self._free_bytes(self.directory)
        except OSError as exc:          # lecteur absent, caractere interdit, droits : un message, jamais une projection qui tombe
            raise RecorderError(f"Dossier inutilisable ({self.directory}) : {exc}") from exc
        if free < core_rec.FREE_STOP_BYTES:
            raise RecorderError(f"Disque presque plein ({free / 1e6:.0f} Mo libres) : enregistrement impossible.")
        if free < core_rec.FREE_WARN_BYTES:
            self.warning = f"Moins de 2 Go libres ({free / 1e9:.1f} Go) : l'enregistrement s'arretera si le disque se remplit."
        self.encoder = self.pick_encoder()
        self.path = core_rec.output_name(self._now_fn(), self.directory, exists=lambda p: os.path.exists(p) or os.path.exists(self._tmp("video", p)))
        try:
            open(self._tmp("video"), "wb").close()                # reserve le nom (une autre session dans la meme seconde en prendra un autre)
            self._log = open(self._tmp("log"), "wb")
        except OSError as exc:
            raise RecorderError(f"Ecriture impossible dans {self.directory} : {exc}") from exc
        try:
            self._proc = subprocess.Popen(core_rec.video_command(self.ffmpeg, self.size, self.fps, self.encoder, self._tmp("video")),
                                          stdin=subprocess.PIPE, stdout=subprocess.DEVNULL, stderr=self._log, creationflags=_NO_WINDOW)
        except OSError as exc:
            self._log.close()
            self._cleanup(("video", "log"))
            raise RecorderError(f"ffmpeg ne demarre pas : {exc}") from exc
        if self.audio_input is not None:
            self._writer = RawAudioWriter(self._tmp("audio"), 2, int(self.audio_input.rate))
            self.audio_input.tap = self._writer.push
        self._thread = threading.Thread(target=self._video_run, name="rec-video", daemon=True)
        self._thread.start()
        self._started = True
        self._next_disk_check = self.fps                          # premiere relecture apres une seconde d'images

    # --- images ----------------------------------------------------------------------------------------

    def due(self, now: float) -> int:
        """Nombre d'images a ecrire a l'instant `now` (la premiere fixe l'instant zero de la video)."""
        if self._t0 is None:
            self._t0 = now
        return core_rec.frames_due(self._t0, now, self.fps, self.sent)

    def write(self, frame: bytes, count: int = 1) -> None:
        """Ecrit `frame` (rgb24, premiere ligne en bas) `count` fois. Sans effet si l'enregistrement doit s'arreter."""
        if not self._started or self._stopped or self.should_stop or count <= 0:
            return
        try:
            self._queue.put((frame, count), timeout=QUEUE_TIMEOUT_S)
        except queue.Full:
            self._fail("L'encodeur ne suit plus : enregistrement arrete.")
            return
        self.sent += count
        self.duplicated += count - 1
        if self._video_error:
            self._fail(self._video_error)
        elif self.sent >= self._next_disk_check:
            self._next_disk_check = self.sent + int(DISK_CHECK_S * self.fps)
            free = self._free_bytes(self.directory)
            if free < core_rec.FREE_STOP_BYTES:
                self._fail(f"Disque presque plein ({free / 1e6:.0f} Mo libres) : enregistrement arrete, ce qui est ecrit est conserve.")

    def _fail(self, message: str) -> None:
        self.should_stop = True
        self.warning = message

    def _video_run(self) -> None:
        proc = self._proc
        while True:
            item = self._queue.get()
            if item is None:
                return
            if self._video_error:
                continue                                          # on vide la file : le rendu ne doit pas rester bloque
            frame, count = item
            try:
                for _ in range(count):
                    proc.stdin.write(frame)
            except (OSError, ValueError):
                self._video_error = "L'encodeur s'est arrete : enregistrement arrete."

    # --- etat ------------------------------------------------------------------------------------------

    def status(self, now: float | None = None) -> dict:
        """Ce que l'interface affiche : duree, taille (video ecrite + estimation de l'audio), crete, doublons, encodeur, avertissement."""
        seconds = self.sent / self.fps
        try:
            size = os.path.getsize(self._tmp("video")) if self.path else 0
        except OSError:
            size = 0
        peak = self._writer.peak if self._writer else 0.0
        warning = self.warning
        if warning is None and self.audio_input is None and self._started:
            warning = "Pas d'entree audio : la video sera sans son."
        if warning is None and self._writer is not None and self._writer.t_last_end is not None and not self._stopped:
            silent = (time.monotonic() if now is None else now) - self._writer.t_last_end
            if silent > SILENT_WARN_S:
                warning = f"Plus de son depuis {silent:.0f} s : la carte son est-elle branchee ?"
        warning = warning or core_rec.drop_warning(self.duplicated, self.sent)
        return {"recording": self._started and not self._stopped, "seconds": seconds, "bytes": size + int(seconds * core_rec.AAC_BYTES_PER_S),
                "peak_db": core_rec.peak_db(peak), "clipped": core_rec.clipped(peak), "dropped": self.duplicated, "frames": self.sent,
                "encoder": self.encoder, "warning": warning, "path": self.path, "should_stop": self.should_stop}

    # --- arret et assemblage ---------------------------------------------------------------------------

    def _cleanup(self, kinds=("video", "audio", "log", "mux")) -> None:
        for kind in kinds:
            try:
                os.remove(self._tmp(kind))
            except OSError:
                pass

    def stop(self) -> str | None:
        """Ferme les ecritures, assemble et rend le chemin du MP4 ; None si rien n'a ete ecrit ou si l'assemblage a echoue (temporaires gardes)."""
        if self._stopped or not self._started:
            return self._result
        self._stopped = True
        try:
            self._queue.put(None, timeout=QUEUE_TIMEOUT_S)
        except queue.Full:              # encodeur fige : le fil video est coince dans write(), on tue ffmpeg pour le debloquer
            self._proc.kill()
            try:
                self._queue.put(None, timeout=QUEUE_TIMEOUT_S)
            except queue.Full:
                pass
        self._thread.join(timeout=60)
        if self._thread.is_alive():
            self._proc.kill()
            self._thread.join(timeout=10)
        try:
            self._proc.stdin.close()
        except OSError:
            pass
        try:
            code = self._proc.wait(timeout=120)
        except subprocess.TimeoutExpired:
            self._proc.kill()
            code = self._proc.wait()
        self._log.close()
        audio_ok = False
        if self._writer is not None:
            if self.audio_input.tap == self._writer.push:
                self.audio_input.tap = None
            end_t = self._t0 + self.sent / self.fps if self._t0 is not None else None
            self._writer.close(end_t=end_t)
            audio_ok = self._writer.t_first is not None and self._writer.bytes_written > 0
            if not audio_ok and not self.warning:
                self.warning = "Aucun son recu de la carte son : la video est sans son."
        if self.sent == 0:
            self._cleanup()
            self.warning = self.warning or "Aucune image enregistree."
            return None
        if code != 0:
            self.warning = f"L'encodeur a echoue (code {code}) : {self._log_tail()} Fichiers temporaires gardes : {self._tmp('video')}"
            return None
        if audio_ok:
            offset = core_rec.audio_offset(self._t0, self._writer.t_first, self.delay_ms)
            cmd = core_rec.mux_command(self.mux_ffmpeg, self._tmp("video"), self._tmp("audio"), int(self.audio_input.rate), 2, offset, self._tmp("mux"),
                                         duration_s=self.sent / self.fps)
        else:
            cmd = core_rec.remux_command(self.mux_ffmpeg, self._tmp("video"), self._tmp("mux"))
        try:
            done = subprocess.run(cmd, capture_output=True, timeout=600, creationflags=_NO_WINDOW)
            ok = done.returncode == 0 and os.path.getsize(self._tmp("mux")) > 0
            detail = done.stderr.decode("utf-8", "replace").strip()[-300:]
        except (OSError, subprocess.SubprocessError) as exc:
            ok, detail = False, str(exc)
        if not ok:
            self._cleanup(("mux",))
            self.warning = f"L'assemblage a echoue : {detail} Fichiers temporaires gardes : {self._tmp('video')}"
            return None
        try:
            os.replace(self._tmp("mux"), self.path)
        except OSError as exc:
            self.warning = f"Le fichier final n'a pas pu etre ecrit : {exc} Fichiers temporaires gardes : {self._tmp('video')}"
            return None
        self._cleanup(("video", "audio", "log"))
        self._result = self.path
        return self._result

    def _log_tail(self) -> str:
        try:
            with open(self._tmp("log"), "rb") as fh:
                return fh.read()[-300:].decode("utf-8", "replace").strip()
        except OSError:
            return ""


class RecordingRun:
    """Un enregistrement vu de la boucle GL : la cible hors ecran a la taille du format, la lecture des images et la copie vers la fenetre.

    Le moteur dessine UNE fois par image, vers `fbo` (a la taille du format) : `Renderer.draw` integre ses animations image par image, l'appeler deux fois
    (fenetre + enregistrement) les ferait avancer deux fois plus vite. La fenetre est donc un moniteur : `finish_frame` y copie l'image enregistree.
    """

    def __init__(self, ctx, renderer, recorder: Recorder):
        self.ctx, self.renderer, self.recorder = ctx, renderer, recorder
        self.size = recorder.size
        self.tex = ctx.texture(self.size, 4)
        self.tex.filter = (moderngl.LINEAR, moderngl.LINEAR)
        self.fbo = ctx.framebuffer(color_attachments=[self.tex])

    def finish_frame(self, now: float, screen, screen_size: tuple[int, int]) -> None:
        """Apres le rendu de l'image : l'ecrit (une fois, ou plusieurs si le rendu a du retard : la duree reste juste), puis la copie dans la fenetre."""
        count = self.recorder.due(now)
        if count > 0:
            self.recorder.write(self.fbo.read(components=3, alignment=1), count)      # lecture synchrone : l'image est celle de cet instant
        self.renderer.blit_fit(screen, self.tex, self.size, screen_size)

    def release(self) -> None:
        self.fbo.release()
        self.tex.release()


class RecordingControl:
    """Ce que la boucle GL possede : zero ou un enregistrement. La boucle appelle `frame_target` avant `Renderer.draw` et `after_draw` apres ; `toggle` /
    `start` / `stop` viennent des commandes (touche E, bouton) ; `close` a la fermeture de la projection. L'assemblage du MP4 (quelques secondes pour un
    long enregistrement) se fait dans un fil : la projection ne gele pas. Etat lu par la GUI : `s.status["rec"]` (voyant, pendant l'enregistrement),
    `s.status["rec_done"]` (`{"path", "warning"}` une fois le fichier ecrit), `s.status["msg"]` (messages).
    """

    STATUS_EVERY_S = 0.25

    def __init__(self, s, ctx, renderer, which=shutil.which, factory: Callable | None = None):
        self.s, self.ctx, self.renderer = s, ctx, renderer
        self._which = which
        self._factory = factory or (lambda ffmpeg, directory, fmt, fps, delay_ms, **kw: Recorder(ffmpeg, directory, fmt, fps, delay_ms, **kw))
        self._run: RecordingRun | None = None
        self._audio_src = None
        self._last_status = 0.0
        self._threads: list[threading.Thread] = []
        self._last_toggle = -1e9
        self.debounce_s = 0.6

    @property
    def active(self) -> bool:
        return self._run is not None

    def toggle(self, params: dict) -> None:
        now = time.monotonic()
        if now - self._last_toggle < self.debounce_s:           # double clic : une seule bascule (comme le bouton de projection)
            return
        self._last_toggle = now
        if self.active:
            self.stop()
        else:
            self.start(params)

    def start(self, params: dict) -> None:
        if self._run is not None:
            return
        ffmpeg = self._which("ffmpeg")
        if not ffmpeg:
            self.s.status["msg"] = "ffmpeg introuvable : enregistrement impossible."
            return
        src = getattr(getattr(self.s, "audio", None), "src", None)
        audio_input = src if src is not None and hasattr(src, "tap") and hasattr(src, "rate") else None        # l'entree synthetique n'a pas de point d'ecoute
        recorder = self._factory(ffmpeg, str(params.get("rec_dir", core_rec.DEFAULT_DIR)), params.get("rec_format", core_rec.DEFAULT_FORMAT),
                                 params.get("rec_fps", 30), core_rec.effective_delay_ms(float(params.get("rec_delay_ms", 40.0)), float(params.get("sync_lead_ms", 0.0))), audio_input=audio_input,
                                 hardware=params.get("rec_encoder") == "materiel")
        try:
            recorder.start()
        except RecorderError as exc:
            self.s.status["msg"] = str(exc)
            return
        try:
            self._run = RecordingRun(self.ctx, self.renderer, recorder)
        except Exception as exc:        # pas assez de memoire video : ni ffmpeg ni point d'ecoute ne restent
            recorder.stop()
            self.s.status["msg"] = f"Enregistrement impossible : {exc}"
            return
        self._audio_src = src
        self._last_status = 0.0
        self.s.status.pop("rec_done", None)
        self.s.status["msg"] = f"Enregistrement : {recorder.size[0]}x{recorder.size[1]} a {recorder.fps} images/s, encodeur {recorder.encoder}"
        sessionlog.event(f"Enregistrement demarre : {recorder.path} ({recorder.size[0]}x{recorder.size[1]}, {recorder.fps} i/s, {recorder.encoder}, "
                         f"audio {'oui' if audio_input is not None else 'non'})")

    def frame_target(self, screen, screen_size: tuple[int, int]):
        """(cible, taille) du rendu de cette image : la cible hors ecran du format pendant l'enregistrement, sinon la fenetre."""
        if self._run is not None:
            return self._run.fbo, self._run.size
        return screen, screen_size

    def after_draw(self, now: float, screen, screen_size: tuple[int, int]) -> None:
        run = self._run
        if run is None:
            return
        run.finish_frame(now, screen, screen_size)
        if now - self._last_status >= self.STATUS_EVERY_S:
            self._last_status = now
            self.s.status["rec"] = run.recorder.status()
        if getattr(getattr(self.s, "audio", None), "src", None) is not self._audio_src:
            self.stop("L'entree audio a change : enregistrement arrete.")                  # une autre frequence d'echantillonnage fausserait le son
        elif run.recorder.should_stop:
            self.stop(run.recorder.warning or "Enregistrement arrete.")

    def stop(self, reason: str | None = None) -> None:
        """Arrete l'enregistrement (la cible GL est liberee tout de suite) ; l'assemblage se termine dans un fil."""
        run, self._run = self._run, None
        if run is None:
            return
        run.release()
        self.s.status.pop("rec", None)
        recorder = run.recorder
        if reason:
            self.s.status["msg"] = reason

        def finish() -> None:
            path = recorder.stop()
            warning = recorder.warning
            self.s.status["rec_done"] = {"path": path, "warning": warning, "frames": recorder.sent}
            if path:
                said = "" if not reason or reason == warning else reason + " "          # la raison de l'arret reste dite (le message de fin ne l'ecrase pas)
                self.s.status["msg"] = f"{said}Enregistrement sauve : {path}" + (f" ({warning})" if warning else "")
                sessionlog.event(f"Enregistrement termine : {path} ({recorder.sent} images, {recorder.duplicated} dupliquees)")
            else:
                self.s.status["msg"] = warning or "Enregistrement : rien d'ecrit."
                sessionlog.event(f"Enregistrement sans fichier : {warning}")
        thread = threading.Thread(target=finish, name="rec-assemble", daemon=False)          # non daemon : fermer l'appli attend la fin de l'assemblage
        self._threads = [t for t in self._threads if t.is_alive()] + [thread]
        thread.start()

    def close(self) -> None:
        """Fermeture de la projection : arrete l'enregistrement en cours et ATTEND la fin de l'assemblage (le fichier ne doit pas rester a moitie fait)."""
        self.stop()
        for thread in self._threads:
            thread.join(timeout=600)
        self._threads = []
