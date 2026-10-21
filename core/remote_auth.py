"""Securite de la telecommande telephone : jetons, pairs prives, limiteurs, validation des commandes, un seul telephone a la fois.

Python pur (stdlib seulement, temps injecte) : se teste partout, sans reseau. Le serveur (`engine/remote.py`) n'a qu'a appeler ces fonctions ; la logique
qui decide « qui a le droit de faire quoi » est ICI et nulle part ailleurs.

Principes (voir docs/PLAN_VJ_REMOTE.md, section 5) :
  * seuls les pairs d'un reseau PRIVE (192.168/16, 10/8, 172.16/12, lien local, boucle locale) sont servis ;
  * un jeton d'ACTIVATION (donne par le QR) ou un code a 6 chiffres s'echange contre un jeton de SESSION ; une seule session vit a la fois
    (une nouvelle connexion valide remplace l'ancienne) ;
  * toutes les comparaisons de secrets sont en temps constant ; les essais ratés sont limites par pair ;
  * une commande n'existe que si elle est dans `COMMANDS` : liste blanche, types et bornes verifies avant d'atteindre le moteur.
"""
from __future__ import annotations

import hmac
import ipaddress
import secrets
import time
from collections import deque

from core import params_schema

PRIVATE_NETS = tuple(ipaddress.ip_network(n) for n in (
    "10.0.0.0/8", "172.16.0.0/12", "192.168.0.0/16", "127.0.0.0/8", "169.254.0.0/16", "::1/128", "fe80::/10", "fc00::/7"))

LOGIN_MAX_FAILS = 5            # essais rates par pair et par fenetre
LOGIN_WINDOW_S = 60.0
LOGIN_BLOCK_S = 300.0          # blocage apres trop d'essais rates
COMMANDS_PER_S = 30            # commandes par seconde et par session
MAX_TEXT = 200                 # longueur du texte du logo
MAX_BODY = 4096                # taille maximale d'un corps de requete
MASTER_KEYS = ("react_master", "fx_master", "overlay_master", "bg_master", "tempo", "hue_master")
AUTO_KEYS = ("auto_phase", "strobe_auto", "bpm_auto")
VJ_ACTIONS = ("toggle", "next", "back")
BUILD_ACTIONS = ("start", "drop")
MEMORY_SLOTS = 4
MAX_SCENE_INDEX = 999


class CommandError(ValueError):
    """Commande refusee : le message est lisible par l'operateur (jamais de trace)."""


# ---- adresses

def is_private(addr) -> bool:
    """Vrai pour une adresse de reseau local (IPv4 ou IPv6). Les adresses mal formees, publiques ou vides sont refusees."""
    try:
        ip = ipaddress.ip_address(str(addr).strip().split("%", 1)[0])
    except ValueError:
        return False
    if isinstance(ip, ipaddress.IPv6Address) and ip.ipv4_mapped is not None:
        ip = ip.ipv4_mapped
    return any(ip in net for net in PRIVATE_NETS if net.version == ip.version)


# ---- secrets

def new_token() -> str:
    return secrets.token_urlsafe(32)


def new_code() -> str:
    return f"{secrets.randbelow(1_000_000):06d}"


def check_token(given, expected) -> bool:
    """Comparaison en temps constant ; un jeton absent ou vide n'est jamais valide."""
    if not given or not expected or not isinstance(given, str) or not isinstance(expected, str):
        return False
    return hmac.compare_digest(given.encode("utf-8", "replace"), expected.encode("utf-8", "replace"))


# ---- limiteurs

class LoginLimiter:
    """5 essais rates par minute et par pair, puis blocage de 5 minutes. Un essai reussi remet le compteur a zero."""

    def __init__(self, now=time.monotonic, max_fails: int = LOGIN_MAX_FAILS, window: float = LOGIN_WINDOW_S, block: float = LOGIN_BLOCK_S) -> None:
        self.now, self.max_fails, self.window, self.block = now, max_fails, window, block
        self.fails: dict[str, deque] = {}
        self.blocked: dict[str, float] = {}

    def allowed(self, peer: str) -> bool:
        until = self.blocked.get(peer)
        if until is not None:
            if self.now() < until:
                return False
            del self.blocked[peer]
            self.fails.pop(peer, None)
        return True

    def fail(self, peer: str) -> None:
        t = self.now()
        q = self.fails.setdefault(peer, deque())
        q.append(t)
        while q and t - q[0] > self.window:
            q.popleft()
        if len(q) >= self.max_fails:
            self.blocked[peer] = t + self.block

    def ok(self, peer: str) -> None:
        self.fails.pop(peer, None)
        self.blocked.pop(peer, None)


class RateLimiter:
    """Au plus `per_second` evenements par seconde et par cle (fenetre glissante d'une seconde)."""

    def __init__(self, now=time.monotonic, per_second: int = COMMANDS_PER_S) -> None:
        self.now, self.per_second = now, per_second
        self.stamps: dict[str, deque] = {}

    def allow(self, key: str) -> bool:
        t = self.now()
        q = self.stamps.setdefault(key, deque())
        while q and t - q[0] >= 1.0:
            q.popleft()
        if len(q) >= self.per_second:
            return False
        q.append(t)
        return True


# ---- commandes

def _int(body: dict, key: str, lo: int, hi: int) -> int:
    value = body.get(key)
    if isinstance(value, bool) or not isinstance(value, int) or not lo <= value <= hi:
        raise CommandError(f"'{key}' doit etre un entier entre {lo} et {hi}")
    return value


def _choice(body: dict, key: str, allowed: tuple) -> str:
    value = body.get(key)
    if not isinstance(value, str) or value not in allowed:
        raise CommandError(f"'{key}' doit etre l'un de : {', '.join(allowed)}")
    return value


def _flag(body: dict, key: str) -> bool:
    value = body.get(key)
    if isinstance(value, bool):
        return value
    if isinstance(value, int) and value in (0, 1):
        return bool(value)
    raise CommandError(f"'{key}' doit etre vrai ou faux")


def _scene(body):
    return {"index": _int(body, "index", 0, MAX_SCENE_INDEX)}


def _vj(body):
    return {"action": _choice(body, "action", VJ_ACTIONS)}


def _strobe(body):
    return {"down": _flag(body, "down")}


def _build(body):
    return {"action": _choice(body, "action", BUILD_ACTIONS)}


def _master(body):
    key = _choice(body, "key", MASTER_KEYS)
    value = body.get("value")
    if isinstance(value, bool) or not isinstance(value, (int, float)) or value != value or value in (float("inf"), float("-inf")):
        raise CommandError("'value' doit etre un nombre")
    p = params_schema.BY_KEY[key]
    return {"key": key, "value": min(max(float(value), float(p.lo)), float(p.hi))}


def _memory(body):
    return {"slot": _int(body, "slot", 1, MEMORY_SLOTS)}


def _text(body):
    value = body.get("text")
    if not isinstance(value, str):
        raise CommandError("'text' doit etre du texte")
    value = "".join(ch for ch in value if ch == "\n" or ch >= " ")[:MAX_TEXT]          # pas de caracteres de controle
    return {"text": value}


def _auto(body):
    return {"key": _choice(body, "key", AUTO_KEYS), "value": _flag(body, "value")}


def _preview(body):
    return {"on": _flag(body, "on")}


def _none(body):
    return {}


# nom -> validateur. C'est LA liste blanche : le serveur refuse tout autre nom (404) et le pont n'execute que ces commandes.
COMMANDS = {
    "scene": _scene, "vj": _vj, "lock": _none, "strobe": _strobe, "build": _build, "master": _master, "memory": _memory,
    "one": _none, "text": _text, "auto": _auto, "preview": _preview,
}


def validate_command(name: str, body) -> dict:
    """Commande normalisee `{"cmd": nom, ...}` ou `CommandError`. Une cle inattendue dans le corps est refusee (rien ne passe en douce)."""
    check = COMMANDS.get(name)
    if check is None:
        raise CommandError("commande inconnue")
    if not isinstance(body, dict):
        raise CommandError("le corps doit etre un objet JSON")
    cmd = check(body)
    extra = set(body) - set(cmd)
    if extra:
        raise CommandError("champ inattendu : " + ", ".join(sorted(str(k)[:20] for k in extra)))
    cmd["cmd"] = name
    return cmd


# ---- acces : activation, connexion, un seul telephone

class Access:
    """Etat de securite de la telecommande : jeton d'activation (QR), code court, session unique.

    `activate()` genere jeton + code et vide la session ; `login(peer, token=, code=)` echange l'un des deux contre un jeton de session (les essais
    ratés sont limites) ; `authorize(session_token)` verifie une requete ; une nouvelle connexion remplace la precedente ; `disconnect()` revoque.
    """

    def __init__(self, now=time.monotonic) -> None:
        self.now = now
        self.limiter = LoginLimiter(now)
        self.rate = RateLimiter(now)
        self.active = False
        self.token = ""
        self.code = ""
        self.session = ""
        self.peer = ""
        self.since = 0.0
        self.revoked_sessions: set[str] = set()
        self.events: list[str] = []            # evenements lisibles (journal) a recuperer avec `pop_events()`

    def _log(self, text: str) -> None:
        self.events.append(text)
        del self.events[:-50]

    def pop_events(self) -> list[str]:
        out, self.events = self.events, []
        return out

    def activate(self) -> None:
        self.active = True
        self.token, self.code = new_token(), new_code()
        self.session, self.peer, self.since = "", "", 0.0
        self.revoked_sessions.clear()
        self.limiter = LoginLimiter(self.now)
        self._log("telecommande activee")

    def deactivate(self) -> None:
        if self.active:
            self._log("telecommande coupee")
        self.active = False
        self.token = self.code = self.session = self.peer = ""

    def login(self, peer: str, token=None, code=None):
        """(statut, jeton de session ou None). Statuts : 200 ok, 401 mauvais secret, 403 pair non prive, 429 trop d'essais, 503 inactive."""
        if not self.active:
            return 503, None
        if not is_private(peer):
            self._log(f"pair refuse (reseau non prive) : {peer}")
            return 403, None
        if not self.limiter.allowed(peer):
            return 429, None
        good = check_token(token, self.token) if token is not None else (
            code is not None and isinstance(code, str) and check_token(code.strip(), self.code))
        if not good:
            self.limiter.fail(peer)
            self._log(f"essai de connexion rate depuis {peer}")
            return 401, None
        self.limiter.ok(peer)
        if self.session:
            self.revoked_sessions.add(self.session)
            self._log(f"telephone {self.peer} remplace par {peer}")
        self.session, self.peer, self.since = new_token(), peer, self.now()
        self._log(f"telephone connecte : {peer}")
        return 200, self.session

    def authorize(self, bearer) -> bool:
        return self.active and bool(self.session) and check_token(bearer, self.session)

    def was_revoked(self, bearer) -> bool:
        return isinstance(bearer, str) and bearer in self.revoked_sessions

    def allow_command(self) -> bool:
        return self.rate.allow(self.session or "-")

    def disconnect(self) -> None:
        if self.session:
            self.revoked_sessions.add(self.session)
            self._log(f"telephone deconnecte : {self.peer}")
        self.session, self.peer = "", ""

    def connected(self) -> bool:
        return self.active and bool(self.session)
