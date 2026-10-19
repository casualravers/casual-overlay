"""Flux video : lecteur de frames (latest wins), flux de test, producteurs ffmpeg (visuel d'audio2wave, logo anime, fond video) et leur gestionnaire.

Extrait tel quel de `audio2wave_gl` (qui re-exporte ces noms). Les dependances facultatives viennent de `engine.deps`.
"""
from __future__ import annotations

import copy
import ctypes
import functools
import json
import os
import shlex
import shutil
import subprocess
import sys
import threading
import time
from pathlib import Path

from engine.deps import np


# --- Video ------------------------------------------------------------------------

class FrameReader:
    """Lit un flux rawvideo et publie la derniere frame ENTIERE (latest wins).

    Le fil lecteur accumule exactement `frame_size` octets avant de publier (jamais un
    bloc de taille arbitraire, cf. relay_loop() dans le CLAUDE.md d'audio2wave), puis
    echange les deux tampons sous verrou. Une frame non consommee est ecrasee : pas de
    file, donc pas de latence croissante. Une frame partielle en fin de flux est jetee.
    """

    CHUNK = 1 << 20

    def __init__(self, stream, frame_size: int, on_frame=None, hold_on_eof: bool = False):
        self._stream = stream
        self.frame_size = frame_size
        self.on_frame = on_frame          # crochet de test, appele avec la frame complete
        self.hold_on_eof = hold_on_eof    # fin de flux: attendre un remplacant au lieu de s'arreter
        self._front = bytearray(frame_size)
        self._back = bytearray(frame_size)
        self._lock = threading.Lock()
        self._pending = None
        self._stop = threading.Event()
        self._seq = 0
        self._uploaded = 0
        self.frames_read = 0
        self.ended = False
        self._thread = threading.Thread(target=self._run, daemon=True)

    def start(self):
        self._thread.start()

    def stop(self):
        self._stop.set()

    def join(self, timeout: float | None = None):
        self._thread.join(timeout)

    def switch_stream(self, stream) -> None:
        """Passe a un nouveau flux (producteur remplace a chaud). Le lecteur finit le flux
        courant jusqu'a sa fin (l'appelant termine l'ancien producteur), jette toute frame
        PARTIELLE de l'ancien flux, puis repart sur le nouveau: aucune frame melangee."""
        self._pending = stream

    def _take_pending(self) -> bool:
        if self._pending is None:
            return False
        old, self._stream, self._pending = self._stream, self._pending, None
        try:
            old.close()
        except Exception:
            pass
        self.ended = False
        return True

    def _run(self):
        size = self.frame_size
        while not self._stop.is_set():
            stream = self._stream
            use_readinto = hasattr(stream, "readinto")
            mv = memoryview(self._back)
            got = 0
            eof = False
            while got < size:
                try:
                    if use_readinto:
                        n = stream.readinto(mv[got:])
                    else:
                        chunk = stream.read(min(size - got, self.CHUNK))
                        n = len(chunk)
                        mv[got:got + n] = chunk
                except (ValueError, OSError):
                    n = 0
                if not n:
                    eof = True
                    break
                got += n
            if eof:
                mv.release()          # frame partielle: jetee
                if self._take_pending():
                    continue
                self.ended = True
                if not self.hold_on_eof:
                    return
                while not self._stop.is_set() and not self._take_pending():
                    time.sleep(0.05)
                continue
            self.frames_read += 1
            if self.on_frame:
                self.on_frame(mv)
            mv.release()
            with self._lock:
                self._front, self._back = self._back, self._front
                self._seq += 1

    def upload_to(self, texture) -> bool:
        """Copie la derniere frame publiee dans la texture GL si elle est nouvelle."""
        with self._lock:
            if self._seq == self._uploaded:
                return False
            texture.write(self._front, alignment=1)
            self._uploaded = self._seq
            return True

    def take(self) -> bytes | None:
        """Derniere frame publiee (copie), ou None si aucune nouvelle. Pour les tests."""
        with self._lock:
            if self._seq == self._uploaded:
                return None
            self._uploaded = self._seq
            return bytes(self._front)


class SyntheticVideoStream:
    """Flux rawvideo rgb24 de test (degrade + damier animes), cadence temps reel.
    N'expose que read(), ce qui exerce le meme chemin que le vrai producteur."""

    def __init__(self, width: int, height: int, fps: int = 30):
        self.w, self.h, self.fps = width, height, fps
        self._gx = (np.arange(width, dtype=np.uint16) * 255 // max(width - 1, 1)).astype(np.uint8)[None, :]
        self._gy = (np.arange(height, dtype=np.uint16) * 255 // max(height - 1, 1)).astype(np.uint8)[:, None]
        self._xx = (np.arange(width) // 80)[None, :]
        self._yy = (np.arange(height) // 80)[:, None]
        self._data = b""
        self._off = 0
        self._n = 0
        self._t_next = time.monotonic()

    def _frame(self) -> bytes:
        n = self._n
        r = (self._gx + np.uint8((n * 3) & 255)) * np.ones((self.h, 1), np.uint8)
        g = (self._gy + np.uint8((n * 2) & 255)) * np.ones((1, self.w), np.uint8)
        check = (((self._xx + self._yy + n // 15) & 1) * 60).astype(np.uint8)
        b = (255 - self._gx) * np.ones((self.h, 1), np.uint8)
        img = np.stack([r, g, b], axis=2).astype(np.uint16) + check[:, :, None]
        return np.minimum(img, 255).astype(np.uint8).tobytes()

    def read(self, n: int) -> bytes:
        if self._off >= len(self._data):
            delay = self._t_next - time.monotonic()
            if delay > 0:
                time.sleep(delay)
            self._t_next = max(self._t_next + 1.0 / self.fps, time.monotonic() - 0.5)
            self._data = self._frame()
            self._off = 0
            self._n += 1
        chunk = self._data[self._off:self._off + n]
        self._off += len(chunk)
        return chunk


# --- Pont vers audio2wave_live ---------------------------------------------------------

def load_live(a2w_dir: Path):
    if not (a2w_dir / "audio2wave_live.py").is_file():
        print(f"audio2wave_live.py introuvable dans {a2w_dir}. Indique le depot avec "
              "--a2w-dir ou la variable AUDIO2WAVE_DIR.", file=sys.stderr)
        sys.exit(2)
    sys.path.insert(0, str(a2w_dir))
    import audio2wave_live
    return audio2wave_live


def build_live_args(live, device: str | None, size: tuple[int, int], fps: int, extra: str | None,
                    fullscreen: bool = True):
    argv = ["--size", f"{size[0]}x{size[1]}", "--fps", str(fps)]
    if not fullscreen:
        # Sans effet sur ffmpeg, mais la case "Plein ecran" de la GUI d'audio2wave la reflete.
        argv.append("--no-fullscreen")
    if device:
        argv += ["-d", device]
    argv += shlex.split(extra or "")
    return live.parse_args(argv)             # API hote d'audio2wave : pas d'echange de sys.argv


def format_command(cmd: list[str]) -> str:
    return " ".join(f'"{c}"' if " " in c else c for c in cmd)


PIPE_BUFFER = 1 << 24   # 16 Mo: plusieurs frames 1080p
PRODUCER_WARMUP_CAP_S = 2.0


@functools.lru_cache(maxsize=1)
def ffmpeg_color_names() -> frozenset[str]:
    """Noms de couleur connus d'ffmpeg (`ffmpeg -colors`, en minuscules). Vide si la liste est illisible
    (on n'invalide alors rien)."""
    try:
        out = subprocess.run([shutil.which("ffmpeg") or "ffmpeg", "-hide_banner", "-colors"],
                             capture_output=True, text=True, timeout=10).stdout
    except (OSError, subprocess.SubprocessError):
        return frozenset()
    names = [line.split()[0].lower() for line in out.splitlines()[1:] if line.split()]
    return frozenset(names) | {"random"}


def is_ffmpeg_color(text: str) -> bool:
    """Couleur acceptee par ffmpeg : nom connu, 0xRRGGBB ou #RRGGBB (+ alpha 'AA' ou '@0.5' eventuel)."""
    base = text.strip().split("@")[0].lower()
    if not base:
        return False
    body = base[2:] if base.startswith("0x") else base[1:] if base.startswith("#") else None
    if body is not None:
        return len(body) in (6, 8) and all(c in HEX_DIGITS for c in body)
    names = ffmpeg_color_names()
    return not names or base in names


def invalid_colors(new_args, current_args) -> list[str]:
    """Couleurs des nouvelles options (trace, separees par |, et fond) qu'ffmpeg ne connait pas. Une
    valeur deja en service est toleree (le defaut 'grey' d'audio2wave_live n'existe pas pour ffmpeg mais
    n'arrete pas le producteur). Sert a NE PAS relancer ffmpeg sur un nom a moitie tape ('t' pour 'teal')."""
    bad = []
    current = {str(getattr(current_args, "colors", "")), str(getattr(current_args, "bg_color", ""))}
    current |= set(str(getattr(current_args, "colors", "")).split("|"))
    candidates = [c for c in str(getattr(new_args, "colors", "")).split("|")] + [str(getattr(new_args, "bg_color", ""))]
    for color in candidates:
        color = color.strip()
        if color and color not in current and not is_ffmpeg_color(color):
            bad.append(color)
    return bad


def spawn_big_pipe(cmd: list[str], **popen_kw) -> subprocess.Popen | None:
    """Popen(cmd) dont stdout est un pipe Windows a GROS tampon (16 Mo). Sous Windows un pipe
    anonyme fait 4 Ko : le lecteur devait alors reprendre le GIL ~700 fois par frame 720p, et
    des que la boucle de rendu tournait vite il ne suivait plus (mesure: 30 -> 7 frames/s).
    Avec 16 Mo, une frame se lit en un ou deux appels. None hors Windows ou en cas d'echec."""
    if os.name != "nt":
        return None
    try:
        import msvcrt
        from ctypes import wintypes
        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        read_h, write_h = wintypes.HANDLE(), wintypes.HANDLE()
        if not kernel32.CreatePipe(ctypes.byref(read_h), ctypes.byref(write_h), None, PIPE_BUFFER):
            raise OSError(ctypes.get_last_error(), "CreatePipe")
        write_fd = msvcrt.open_osfhandle(write_h.value, 0)
        read_fd = msvcrt.open_osfhandle(read_h.value, os.O_RDONLY)
        try:
            proc = subprocess.Popen(cmd, stdout=write_fd, **popen_kw)
        finally:
            os.close(write_fd)          # le parent ne garde pas l'extremite d'ecriture
        proc.stdout = open(read_fd, "rb", buffering=0)
        return proc
    except Exception as exc:
        print(f"Pipe a gros tampon indisponible ({exc}), repli sur le pipe standard.", file=sys.stderr)
        return None


def spawn_producer(live, live_args) -> subprocess.Popen:
    """Lance le producteur ffmpeg de audio2wave_live (meme commande), stdout relie a un
    pipe a gros tampon (voir spawn_big_pipe). Repli sur live.spawn_producer()."""
    return spawn_big_pipe(live.producer_command(live_args)) or live.spawn_producer(live_args)


# --- Logo anime (video) -----------------------------------------------------------------

LOGO_VIDEO_MAX_SIDE = 960       # cote max decode (un logo n'a pas besoin de plus)
LOGO_KEY_SIMILARITY = 0.3
LOGO_KEY_BLEND = 0.1
HEX_DIGITS = "0123456789abcdefABCDEF"
LOGO_VIDEO_EXTENSIONS = "*.webm *.mov *.mp4 *.mkv *.gif *.apng *.webp *.avi"


def probe_video(path: str) -> tuple[int, int, str]:
    """(largeur, hauteur, codec) du premier flux video, via ffprobe. Leve RuntimeError sinon."""
    ffprobe = shutil.which("ffprobe")
    if not ffprobe:
        raise RuntimeError("ffprobe introuvable (installe ffmpeg complet)")
    out = subprocess.run([ffprobe, "-v", "error", "-select_streams", "v:0", "-show_entries",
                          "stream=width,height,codec_name", "-of", "json", path],
                         capture_output=True, text=True, timeout=15)
    try:
        st = json.loads(out.stdout)["streams"][0]
        return int(st["width"]), int(st["height"]), st.get("codec_name", "")
    except (KeyError, IndexError, ValueError):
        raise RuntimeError(f"pas de flux video lisible ({out.stderr.strip()[:120] or 'fichier invalide'})")


def logo_video_command(path: str, size: tuple[int, int], codec: str, key: str = "") -> list[str]:
    """Commande ffmpeg: decode `path` en boucle, a sa cadence native (-re), en rawvideo rgba
    (alpha DROIT, non premultiplie: le shader le premultiplie). `key` (#rrggbb) detoure cette
    couleur pour un fichier sans canal alpha (fond vert...)."""
    cmd = [shutil.which("ffmpeg") or "ffmpeg", "-hide_banner", "-loglevel", "error", "-re", "-stream_loop", "-1"]
    # Le decodeur vp9/vp8 natif d'ffmpeg ignore l'alpha des .webm: il faut celui de libvpx.
    if codec == "vp9":
        cmd += ["-c:v", "libvpx-vp9"]
    elif codec == "vp8":
        cmd += ["-c:v", "libvpx"]
    filters = [f"scale={size[0]}:{size[1]}:flags=bilinear", "format=rgba"]
    if key:
        filters.append(f"colorkey=0x{key.lstrip('#')}:{LOGO_KEY_SIMILARITY}:{LOGO_KEY_BLEND}")
    return cmd + ["-i", path, "-an", "-vf", ",".join(filters), "-f", "rawvideo", "-pix_fmt", "rgba", "-"]


class LogoVideo:
    """Logo anime: un ffmpeg qui boucle sur le fichier + un FrameReader (latest wins). La texture
    est mise a jour depuis le fil GL par `upload_to` ; la cadence est celle du fichier."""

    def __init__(self, path: str, key: str = ""):
        w, h, codec = probe_video(path)
        ratio = min(1.0, LOGO_VIDEO_MAX_SIDE / max(w, h))
        self.size = (max(2, round(w * ratio)), max(2, round(h * ratio)))
        cmd = logo_video_command(path, self.size, codec, key)
        self.proc = spawn_big_pipe(cmd, stderr=subprocess.PIPE) or subprocess.Popen(
            cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, bufsize=0)
        self.reader = FrameReader(self.proc.stdout, self.size[0] * self.size[1] * 4)
        self.reader.start()

    def error(self) -> str:
        """Message d'ffmpeg si le processus est mort sans avoir produit une seule image."""
        if self.proc.poll() is None or self.reader.frames_read:
            return ""
        try:
            return self.proc.stderr.read().decode(errors="replace").strip().splitlines()[-1][:160]
        except Exception:
            return "ffmpeg s'est arrete"

    def stop(self) -> None:
        self.reader.stop()
        stop_process(self.proc)
        for stream in (self.proc.stdout, self.proc.stderr):
            try:
                stream.close()
            except Exception:
                pass


# --- Fond video (un fichier brut, en boucle, plein cadre) -------------------------------------

BG_VIDEO_FITS = ("cover", "stretch")


def bg_video_command(path: str, size: tuple[int, int], fit: str = "cover") -> list[str]:
    """Commande ffmpeg : decode `path` en boucle, a sa cadence native (-re), a la taille du rendu, en rawvideo rgb24 (meme
    format que le producteur d'audio2wave : la texture du fond la lit telle quelle). `cover` remplit le cadre en recadrant
    le debordement (le ratio du fichier est garde), `stretch` etire."""
    w, h = size
    if fit == "stretch":
        vf = f"scale={w}:{h}:flags=bilinear"
    else:
        vf = f"scale={w}:{h}:force_original_aspect_ratio=increase:flags=bilinear,crop={w}:{h}"
    return [shutil.which("ffmpeg") or "ffmpeg", "-hide_banner", "-loglevel", "error", "-re", "-stream_loop", "-1",
            "-i", path, "-an", "-vf", vf + ",setsar=1", "-f", "rawvideo", "-pix_fmt", "rgb24", "-"]


class BgVideo(LogoVideo):
    """Fond video : un ffmpeg qui boucle sur le fichier + un FrameReader (latest wins), comme le logo anime mais en rgb24 a
    la taille du rendu."""

    def __init__(self, path: str, size: tuple[int, int], fit: str = "cover"):
        self.size = size
        cmd = bg_video_command(path, size, fit)
        self.proc = spawn_big_pipe(cmd, stderr=subprocess.PIPE) or subprocess.Popen(
            cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, bufsize=0)
        self.reader = FrameReader(self.proc.stdout, size[0] * size[1] * 3)
        self.reader.start()


class BgVideoManager:
    """Fond video du fil GL : lance / arrete le decodage quand `bg_mode`, `bg_video` ou `bg_video_fit` changent et rend le
    lecteur a utiliser (None tant que la mode n'est pas "video" ou que la premiere image n'est pas arrivee : on garde alors le
    fond d'avant, jamais d'ecran noir). Message dans `status["bgvideo"]`."""

    def __init__(self, size: tuple[int, int], status: dict):
        self.size, self.status = size, status
        self.video: BgVideo | None = None
        self.key: tuple | None = None
        self._err = ""

    def reader_for(self, params: dict):
        mode = params.get("bg_mode")
        path = str(params.get("bg_video") or "")
        want = (path, params.get("bg_video_fit", "cover")) if mode == "video" and path else None
        if want != self.key:
            self.stop()
            self.key = want
            self._err = ""
            if want is not None:
                if not Path(path).is_file():
                    self.status["bgvideo"] = f"Fond video introuvable: {path}"
                else:
                    try:
                        self.video = BgVideo(path, self.size, want[1])
                        self.status["bgvideo"] = f"Fond video: {Path(path).name}"
                    except OSError as exc:
                        self.status["bgvideo"] = f"Fond video illisible: {exc}"
            elif mode == "video":
                self.status["bgvideo"] = "Fond video: choisis un fichier"
            else:
                self.status.pop("bgvideo", None)
        if self.video is not None:
            err = self.video.error()
            if err and err != self._err:
                self._err = err
                self.status["bgvideo"] = f"Fond video illisible: {err}"
                print(self.status["bgvideo"], file=sys.stderr)
            if self.video.reader.frames_read:
                return self.video.reader
        return None

    def stop(self) -> None:
        if self.video is not None:
            self.video.stop()
            self.video = None


def stop_process(proc: subprocess.Popen | None) -> None:
    if proc is None or proc.poll() is not None:
        return
    proc.terminate()
    try:
        proc.wait(timeout=3)
    except subprocess.TimeoutExpired:
        proc.kill()
        proc.wait()


class ProducerManager:
    """Possede le producteur ffmpeg ET son FrameReader, et remplace le producteur a chaud
    quand un reglage du visuel change (GUI), sans jamais toucher a la fenetre GL.

    Meme principe que le redemarrage "doux" d'audio2wave_live (voir son CLAUDE.md) : le
    nouveau producteur est lance a cote de l'ancien puis "chauffe" (ses frames ENTIERES sont
    lues et jetees pendant `producer_warmup_seconds`, sinon ffmpeg bloque sur un pipe plein et
    sa fenetre --averaging ne converge pas: a-coup visible au basculement), et n'est montre
    qu'ensuite. S'il meurt pendant la chauffe (reglage invalide, ex. couleur inconnue),
    l'ancien reste en place et un message est laisse dans `status["live"]`.
    Les demandes rapprochees sont fusionnees: seule la derniere est jouee.
    """

    def __init__(self, live, live_args, frame_size: int, status: dict, spawn=None):
        self.live = live
        # COPIE: la GUI d'audio2wave mute ses options en place; si on gardait le meme objet, le gestionnaire
        # verrait toujours des options "deja appliquees" et ne redemarrerait plus jamais ffmpeg.
        self.args = copy.copy(live_args)
        self.frame_size = frame_size
        self.status = status
        self._spawn = spawn or (lambda a: spawn_producer(live, a))
        self.producer = self._spawn(live_args)
        self.reader = FrameReader(self.producer.stdout, frame_size, hold_on_eof=True)
        self.restarts = 0
        self._request: object | None = None
        self._lock = threading.Lock()
        self._wake = threading.Event()
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._run, daemon=True)

    def start(self) -> None:
        self.reader.start()
        self._thread.start()

    def request_restart(self, new_args) -> None:
        """Programme le remplacement du producteur par un lance avec `new_args`."""
        with self._lock:
            self._request = new_args
        self._wake.set()

    def stop(self) -> None:
        self._stop.set()
        self._wake.set()
        self.reader.stop()
        stop_process(self.producer)

    _suspended = False

    def suspend(self) -> None:
        """Un autre mode (Snap/Ridge) fournit le fond : arrete ffmpeg sans toucher au lecteur ni au
        fil du gestionnaire. Les demandes de redemarrage sont ignorees jusqu'a `resume()`."""
        with self._lock:
            self._suspended = True
            self._request = None
        stop_process(self.producer)

    def resume(self, new_args) -> None:
        """Retour au mode Live : relance ffmpeg avec `new_args` (chauffe comme un redemarrage doux)."""
        self._suspended = False
        if getattr(new_args, "device", None):          # sans entree audio, rien a relancer
            self.request_restart(new_args)

    def _run(self) -> None:
        while not self._stop.is_set():
            self._wake.wait(0.2)
            if self._stop.is_set():
                return
            with self._lock:
                new_args, self._request = self._request, None
                self._wake.clear()
            if new_args is not None and not self._suspended:
                self._replace(new_args)

    def _warmup_seconds(self, args) -> float:
        try:
            wanted = self.live.producer_warmup_seconds(args)
        except Exception:
            wanted = 0.5
        return max(getattr(self.live, "RESTART_GRACE_S", 0.3), min(wanted, PRODUCER_WARMUP_CAP_S))

    def _drain(self, proc, seconds: float) -> bool:
        """Lit et jette des frames ENTIERES pendant `seconds` (au moins une). False si le
        producteur s'arrete avant."""
        size = self.frame_size
        mv = memoryview(bytearray(size))
        deadline = time.monotonic() + seconds
        first = True
        while first or time.monotonic() < deadline:
            first = False
            got = 0
            while got < size:
                if self._stop.is_set():
                    return False
                try:
                    n = proc.stdout.readinto(mv[got:])
                except (ValueError, OSError):
                    n = 0
                if not n:
                    return False
                got += n
        return True

    def _replace(self, new_args) -> None:
        self.status["live"] = "Redemarrage du flux..."
        try:
            new = self._spawn(new_args)
        except Exception as exc:
            self.status["live"] = f"Lancement de ffmpeg impossible: {exc}"
            return
        if self._suspended:                       # un autre mode a pris la main pendant le lancement
            stop_process(new)
            return
        if not self._drain(new, self._warmup_seconds(new_args)):
            stop_process(new)
            self.status["live"] = ("Reglage refuse (ffmpeg s'est arrete): ancien flux conserve. "
                                   "Verifie couleurs / valeurs.")
            return
        if self._suspended:
            stop_process(new)
            return
        old = self.producer
        self.producer = new
        self.args = new_args
        self.reader.switch_stream(new.stdout)
        stop_process(old)          # EOF sur l'ancien flux -> le lecteur bascule sur le nouveau
        self.restarts += 1
        self.status["live"] = "Flux a jour"
