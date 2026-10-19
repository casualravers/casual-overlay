"""Recuperation des doodles du site : fil d'arriere-plan qui interroge le bucket Supabase, valide et decode les nouveaux fichiers et les
confie au rendu par une file (`Session.doodle_queue`). Ni le fil GL ni le fil Tk ne font de reseau.

La logique pure (liste, fichiers vus, validation, cycle de vie) est dans `core.doodles` ; ici : urllib, Pillow, le fil.
"""
from __future__ import annotations

import io
import threading
import time
import urllib.request

from core import doodles as dd
from core import sessionlog
from engine.deps import PIL_Image, PIL_ImageOps
from engine.render import _premultiplied


def http_post(url: str, body: bytes, headers: dict) -> bytes:
    req = urllib.request.Request(url, data=body, headers=headers, method="POST")
    with urllib.request.urlopen(req, timeout=dd.REQUEST_TIMEOUT_S) as resp:
        return resp.read(1 << 22)


def http_get(url: str) -> bytes:
    """Telecharge au plus MAX_BYTES + 1 octets : un fichier plus gros est lu tronque, donc refuse par `check_download`."""
    with urllib.request.urlopen(url, timeout=dd.REQUEST_TIMEOUT_S) as resp:
        return resp.read(dd.MAX_BYTES + 1)


def decode(data: bytes):
    """Octets PNG / JPEG -> (tableau uint8 RGBA premultiplie, largeur, hauteur). Leve ValueError avec la raison si le fichier est refuse."""
    try:
        img = PIL_Image.open(io.BytesIO(data))
        why = dd.check_size(*img.size)          # avant tout decodage : une image enorme ne charge pas la memoire
        if why:
            raise ValueError(why)
        img = PIL_ImageOps.exif_transpose(img)   # photos : orientation du telephone
        arr = _premultiplied(img.convert("RGBA"))
    except ValueError:
        raise
    except Exception as exc:                     # decodage impossible, image tronquee, bombe de decompression...
        raise ValueError(f"decodage impossible ({exc})") from None
    return arr, arr.shape[1], arr.shape[0]


class DoodleFeed:
    """Fil de recuperation. Il surveille `params["doodle_on"]` : a l'activation il memorise les fichiers deja presents (sans les
    afficher), ensuite chaque nouveau fichier valide est decode et mis dans `s.doodle_queue`. Les parametres reseau sont injectables."""

    def __init__(self, s, config_loader=dd.load_config, post=http_post, get=http_get, poll_s: float = dd.POLL_S,
                 clock=time.monotonic) -> None:
        self.s = s
        self._load, self._post, self._get = config_loader, post, get
        self.poll_s, self._clock = poll_s, clock
        self.seen = dd.Seen()
        self.armed = False
        self.config: dict | None = None
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._was_on = False
        self._next = 0.0
        self.received = 0
        self._last_error = ""

    # ---- cycle de vie du fil
    def start(self) -> None:
        if self._thread is None:
            self._thread = threading.Thread(target=self._run, name="doodles", daemon=True)
            self._thread.start()

    def stop(self) -> None:
        self._stop.set()

    def _set_status(self, text: str) -> None:
        self.s.status["doodles"] = text

    def _run(self) -> None:
        while not self._stop.wait(0.25):
            try:
                self.tick()
            except Exception as exc:             # le fil ne meurt jamais : une erreur est journalisee, on reessaie au tour suivant
                sessionlog.error("Doodles : erreur inattendue", exc)

    # ---- un pas (appele toutes les 250 ms par le fil ; les tests l'appellent a la main)
    def tick(self) -> None:
        on = float(self.s.params.get("doodle_on", 0.0)) >= 0.5
        if not on:
            if self._was_on:
                self._was_on = False
                self._set_status("")
                sessionlog.event("Doodles : recuperation arretee")
            return
        now = self._clock()
        if not self._was_on:                      # activation : on repart de zero, l'existant sera memorise sans etre affiche
            self._was_on = True
            self.armed, self.config, self._next, self.received, self._last_error = False, None, 0.0, 0, ""
            self.seen = dd.Seen()
            sessionlog.event("Doodles : recuperation activee")
        if now < self._next:
            return
        self._next = now + self.poll_s
        self.poll()

    def poll(self) -> None:
        if self.config is None:
            self.config, why = self._load()
            if self.config is None:
                self._set_status(why)
                self._journal_error(why)
                return
        try:
            if not self.armed:
                url, body, headers = dd.list_request(self.config)
                self.seen.arm(dd.parse_listing(self._post(url, body, headers)))
                self.armed = True
                self._set_status("Doodles : en attente de nouveaux fichiers")
                sessionlog.event(f"Doodles : {len(self.seen.names)} fichiers deja presents (non affiches)")
                return
            good, rejected, error = dd.poll_once(self.config, self.seen, self._post, self._get)
        except Exception as exc:
            why = f"Doodles : reseau indisponible ({exc})"
            self._set_status(why)
            self._journal_error(why)
            return
        for line in rejected:
            sessionlog.event(f"Doodles : fichier ignore - {line}")
        for name, data in good:
            try:
                rgba, w, h = decode(data)
            except ValueError as exc:
                sessionlog.event(f"Doodles : fichier ignore - {name} : {exc}")
                continue
            self.s.doodle_queue.put({"name": name, "rgba": rgba, "w": w, "h": h})
            self.received += 1
            sessionlog.event(f"Doodles : {name} recu ({w}x{h})")
        if error:
            self._set_status(f"Doodles : {error}")
            self._journal_error(error)
        else:
            self._last_error = ""
            self._set_status(f"Doodles : {self.received} recu(s)" if self.received else "Doodles : en attente de nouveaux fichiers")

    def _journal_error(self, why: str) -> None:
        if why != self._last_error:               # une meme erreur n'est ecrite qu'une fois, pas toutes les 5 s
            self._last_error = why
            sessionlog.event(why)
