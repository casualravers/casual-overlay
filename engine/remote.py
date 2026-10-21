"""Serveur de la telecommande telephone : une petite page web servie en local, un flux SSE pour l'etat, des POST pour les commandes.

stdlib seulement (`http.server`), dans un fil dedie du processus de casual-overlay. **Ce module ne touche jamais Tk ni glfw** : il lit un instantane
d'etat (`state_fn()`, un dict publie par la GUI) et remet chaque commande validee a `command_fn(cmd)`, qui la pose dans une file lue par le fil Tk
(`gui/gui_remote.py`). Toute la decision « qui a le droit » est dans `core/remote_auth.py` ; ici on ne fait que la brancher sur HTTP.

Securite (docs/PLAN_VJ_REMOTE.md, section 5) : pairs prives seulement (403 sinon), en-tete `Host` verifie (DNS rebinding), `Origin` refuse s'il
differe, `Content-Type: application/json` exige, corps limite a 4 Ko, jeton de session dans `Authorization: Bearer` (jamais de cookie), 30 commandes
par seconde, aucune trace dans les reponses d'erreur, CSP stricte. Le flux d'etat est lu avec `fetch` + lecteur de flux cote page (EventSource ne sait
pas envoyer d'en-tete `Authorization`), au format SSE standard.
"""
from __future__ import annotations

import json
import socket
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from core import remote_auth, remote_state

PING_S = 15.0
SSE_POLL_S = 0.1
HEADER_TIMEOUT_S = 10.0
SSE_WRITE_TIMEOUT_S = 5.0
DEFAULT_PORT = 8765

# Pages servies : table fixe (aucun chemin libre, donc aucune traversee de dossier possible).
STATIC = {
    "/": ("index.html", "text/html; charset=utf-8"),
    "/app.js": ("app.js", "text/javascript; charset=utf-8"),
    "/style.css": ("style.css", "text/css; charset=utf-8"),
    "/manifest.webmanifest": ("manifest.webmanifest", "application/manifest+json; charset=utf-8"),
    "/icon.svg": ("icon.svg", "image/svg+xml"),
}
CSP = ("default-src 'none'; script-src 'self'; style-src 'self'; img-src 'self'; connect-src 'self'; manifest-src 'self'; "
       "base-uri 'none'; form-action 'none'; frame-ancestors 'none'")


def pick_private_ip() -> str | None:
    """Adresse du PC sur le reseau local (pas la boucle locale), ou None s'il n'y en a pas. Aucun paquet n'est envoye."""
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        try:
            s.connect(("10.255.255.255", 1))
            ip = s.getsockname()[0]
        finally:
            s.close()
        if remote_auth.is_private(ip) and not ip.startswith(("127.", "169.254.")):
            return ip
    except OSError:
        pass
    try:
        for info in socket.getaddrinfo(socket.gethostname(), None, socket.AF_INET):
            ip = info[4][0]
            if remote_auth.is_private(ip) and not ip.startswith(("127.", "169.254.")):
                return ip
    except OSError:
        pass
    return None


def _bare_host(host: str) -> str:
    """Nom d'hote sans le port (`192.168.1.5:8765` -> `192.168.1.5`, `[fe80::1]:8765` -> `fe80::1`)."""
    if host.startswith("["):
        return host[1:host.find("]")] if "]" in host else host
    return host.rsplit(":", 1)[0] if host.count(":") == 1 else host


class _Server(ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = False          # sous Windows SO_REUSEADDR laisserait un autre programme prendre le meme port
    request_queue_size = 8

    def handle_error(self, request, client_address) -> None:          # pas de trace sur la console pour un client qui part
        pass


class RemoteServer:
    """`access` : `core.remote_auth.Access` ; `state_fn()` -> dict (instantane publie) ; `command_fn(cmd)` -> None ou message d'erreur ;
    `static_dir` : dossier de la page ; `host` / `port` : adresse d'ecoute ; `allowed_hosts` : valeurs acceptees pour l'en-tete Host ;
    `preview_fn()` -> octets JPEG ou None ; `log(texte)` : journal (jamais de secret)."""

    def __init__(self, access, state_fn, command_fn, static_dir, host: str, port: int = DEFAULT_PORT, *, allowed_hosts=None,
                 preview_fn=None, log=None, peer_ok=remote_auth.is_private) -> None:
        self.access, self.state_fn, self.command_fn = access, state_fn, command_fn
        self.static_dir = Path(static_dir)
        self.host, self.port = host, int(port)
        self.allowed_hosts = set(allowed_hosts) if allowed_hosts else {host}
        self.preview_fn, self.log, self.peer_ok = preview_fn, log or (lambda text: None), peer_ok
        self.stop_event = threading.Event()
        self.httpd: _Server | None = None
        self.thread: threading.Thread | None = None
        self.pages: dict[str, tuple[bytes, str]] = {}
        self.sse_gen = 0
        self.sse_lock = threading.Lock()
        self.handlers = 0
        self.handlers_lock = threading.Lock()

    @property
    def url(self) -> str:
        return f"http://{self.host}:{self.port}/"

    def running(self) -> bool:
        return self.thread is not None and self.thread.is_alive()

    # ---- cycle de vie
    def start(self):
        """(ok, message). Echec propre (port pris, adresse absente, page introuvable) : jamais d'exception."""
        if self.running():
            return True, "deja demarre"
        try:
            self.pages = {path: ((self.static_dir / name).read_bytes(), ctype) for path, (name, ctype) in STATIC.items()}
        except OSError:
            return False, "page de la telecommande introuvable"
        self.stop_event.clear()
        try:
            self.httpd = _Server((self.host, self.port), _make_handler(self))
        except OSError as exc:
            self.httpd = None
            return False, f"port {self.port} indisponible ({exc.__class__.__name__}) : choisis-en un autre"
        if self.port == 0:
            self.port = self.httpd.server_address[1]
        self.thread = threading.Thread(target=self.httpd.serve_forever, kwargs={"poll_interval": 0.2}, name="remote-http", daemon=True)
        self.thread.start()
        self.log(f"telecommande : serveur ouvert sur {self.host}:{self.port}")
        return True, self.url

    def stop(self) -> None:
        self.stop_event.set()
        httpd, self.httpd = self.httpd, None
        if httpd is not None:
            try:
                httpd.shutdown()
            except Exception:
                pass
            try:
                httpd.server_close()
            except Exception:
                pass
        if self.thread is not None:
            self.thread.join(3.0)
        t_end = time.monotonic() + 2.0
        while self.handlers > 0 and time.monotonic() < t_end:        # les flux SSE se terminent d'eux-memes (stop_event)
            time.sleep(0.02)
        if httpd is not None:
            self.log("telecommande : serveur ferme")
        self.thread = None

    def next_sse(self) -> int:
        with self.sse_lock:
            self.sse_gen += 1
            return self.sse_gen


def _make_handler(remote: RemoteServer):
    class Handler(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"
        timeout = HEADER_TIMEOUT_S
        server_version = "casual-remote"
        sys_version = ""

        def log_message(self, fmt, *args) -> None:          # jamais sur la console (ni d'adresse ni de chemin)
            pass

        # ---- sorties
        def _send(self, status: int, body: bytes, ctype: str, extra=None) -> None:
            self.send_response(status)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header("Content-Security-Policy", CSP)
            self.send_header("Referrer-Policy", "no-referrer")
            for k, v in (extra or {}).items():
                self.send_header(k, v)
            self.end_headers()
            if self.command != "HEAD":
                self.wfile.write(body)

        def _json(self, status: int, payload: dict, extra=None) -> None:
            self._send(status, json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8"), "application/json; charset=utf-8", extra)

        def _error(self, status: int, message: str, extra=None) -> None:
            self._json(status, {"error": message}, extra)

        # ---- controles communs
        def _bearer(self) -> str:
            value = self.headers.get("Authorization", "")
            return value[7:].strip() if value[:7].lower() == "bearer " else ""

        def _gate(self) -> bool:
            """Pair prive, en-tete Host attendu, Origin identique s'il est present. Rend False apres avoir repondu."""
            peer = self.client_address[0]
            if not remote.peer_ok(peer):
                remote.log(f"telecommande : pair refuse {peer}")
                self._error(403, "reseau non autorise")
                return False
            host = (self.headers.get("Host") or "").strip().lower()
            bare = _bare_host(host)
            if bare not in remote.allowed_hosts:
                self._error(403, "adresse non autorisee")
                return False
            origin = self.headers.get("Origin")
            if origin is not None and origin != f"http://{host}":
                self._error(403, "origine non autorisee")
                return False
            return True

        def _body(self):
            """Corps JSON (dict) ou None apres avoir repondu."""
            ctype = (self.headers.get("Content-Type") or "").split(";")[0].strip().lower()
            if ctype != "application/json":
                self._error(415, "Content-Type: application/json attendu")
                return None
            try:
                length = int(self.headers.get("Content-Length", ""))
            except ValueError:
                self._error(411, "longueur requise")
                return None
            if length < 0 or length > remote_auth.MAX_BODY:
                self._error(413, "corps trop gros")
                self.close_connection = True
                return None
            raw = self.rfile.read(length) if length else b""
            try:
                data = json.loads(raw.decode("utf-8")) if raw else {}
            except (ValueError, UnicodeDecodeError):
                self._error(400, "JSON illisible")
                return None
            return data

        def _authorized(self) -> bool:
            bearer = self._bearer()
            if remote.access.authorize(bearer):
                return True
            if remote.access.was_revoked(bearer):
                self._json(401, {"error": "un autre telephone a pris la main", "revoked": True})
            else:
                self._error(401, "connexion requise")
            return False

        # ---- routes
        def do_GET(self) -> None:
            try:
                if not self._gate():
                    return
                path = self.path.split("?", 1)[0]
                page = remote.pages.get(path)
                if page is not None:
                    self._send(200, page[0], page[1])
                elif path == "/api/state":
                    if self._authorized():
                        self._json(200, remote.state_fn())
                elif path == "/events":
                    if self._authorized():
                        self._events()
                elif path == "/preview.jpg":
                    if self._authorized():
                        data = remote.preview_fn() if remote.preview_fn else None
                        if data:
                            self._send(200, data, "image/jpeg")
                        else:
                            self._error(404, "pas d'apercu")
                else:
                    self._error(404, "introuvable")
            except (OSError, ConnectionError):
                self.close_connection = True
            except Exception:
                self._safe_500()

        def do_POST(self) -> None:
            try:
                if not self._gate():
                    return
                path = self.path.split("?", 1)[0]
                if path == "/api/login":
                    self._login()
                    return
                if not path.startswith("/api/"):
                    self._error(404, "introuvable")
                    return
                name = path[5:]
                if name not in remote_auth.COMMANDS:
                    self._error(404, "commande inconnue")
                    return
                if not self._authorized():
                    return
                if not remote.access.allow_command():
                    self._error(429, "trop de commandes", {"Retry-After": "1"})
                    return
                body = self._body()
                if body is None:
                    return
                try:
                    cmd = remote_auth.validate_command(name, body)
                except remote_auth.CommandError as exc:
                    self._error(400, str(exc))
                    return
                problem = remote.command_fn(cmd)
                if problem:
                    self._error(409, str(problem))
                else:
                    self._json(200, {"ok": True})
            except (OSError, ConnectionError):
                self.close_connection = True
            except Exception:
                self._safe_500()

        def do_HEAD(self) -> None:
            self._error(405, "methode non autorisee")

        do_PUT = do_DELETE = do_PATCH = do_OPTIONS = do_HEAD

        def _safe_500(self) -> None:
            remote.log("telecommande : erreur interne dans une requete")
            try:
                self._error(500, "erreur interne")
            except Exception:
                self.close_connection = True

        def _login(self) -> None:
            body = self._body()
            if body is None:
                return
            if not isinstance(body, dict) or not (set(body) <= {"k", "code"}) or len(body) != 1:
                self._error(400, "k ou code attendu")
                return
            token = body.get("k") if "k" in body else None
            code = body.get("code") if "code" in body else None
            if (token is not None and not isinstance(token, str)) or (code is not None and not isinstance(code, str)):
                self._error(400, "k ou code attendu")
                return
            status, session = remote.access.login(self.client_address[0], token=token, code=code)
            if status == 200:
                self._json(200, {"session": session})
            elif status == 429:
                self._error(429, "trop d'essais, patiente 5 minutes", {"Retry-After": "300"})
            elif status == 503:
                self._error(503, "telecommande inactive")
            elif status == 403:
                self._error(403, "reseau non autorise")
            else:
                self._error(401, "code ou lien incorrect")

        def _events(self) -> None:
            """Flux SSE : un evenement `state` a chaque changement (au plus 5 par seconde), un `ping` toutes les 15 s. Un seul abonne a la fois."""
            bearer = self._bearer()
            gen = remote.next_sse()
            with remote.handlers_lock:
                remote.handlers += 1
            try:
                self.connection.settimeout(SSE_WRITE_TIMEOUT_S)
                self.send_response(200)
                self.send_header("Content-Type", "text/event-stream; charset=utf-8")
                self.send_header("Cache-Control", "no-store")
                self.send_header("X-Accel-Buffering", "no")
                self.send_header("X-Content-Type-Options", "nosniff")
                self.send_header("Connection", "close")
                self.end_headers()
                self.close_connection = True
                self.wfile.write(b"retry: 2000\n\n")
                self.wfile.flush()
                throttle = remote_state.Throttle()
                last_ping = time.monotonic()
                while not remote.stop_event.is_set():
                    if gen != remote.sse_gen:
                        break                                            # un autre abonne a pris la place
                    if not remote.access.authorize(bearer):
                        self.wfile.write(b"event: bye\ndata: {\"revoked\":true}\n\n")
                        self.wfile.flush()
                        break
                    now = time.monotonic()
                    out = throttle.next(remote_state.to_json(remote.state_fn()), now)
                    if out is not None:
                        self.wfile.write(b"event: state\ndata: " + out.encode("utf-8") + b"\n\n")
                        self.wfile.flush()
                        last_ping = now
                    elif now - last_ping >= PING_S:
                        self.wfile.write(b"event: ping\ndata: 1\n\n")
                        self.wfile.flush()
                        last_ping = now
                    remote.stop_event.wait(SSE_POLL_S)
            except (OSError, ConnectionError):
                pass
            finally:
                with remote.handlers_lock:
                    remote.handlers -= 1

    return Handler
