"""Instantane d'etat envoye au telephone (evenement SSE `state`) : contrat JSON de la telecommande.

Python pur : `build_state` assemble le dict a partir de valeurs DEJA lues par la GUI (aucun acces a la fenetre, au moteur ni a Tk ici), le nettoie
(chaines tronquees, pas de caracteres de controle) et le rend stable (memes entrees -> meme JSON) pour que le serveur n'emette que les changements.
Aucune donnee sensible : ni jeton, ni code, ni chemin de fichier.
"""
from __future__ import annotations

import json

from core import remote_auth

VERSION = 1
MAX_NAME = 60
MAX_LINE = 160
MAX_SCENES = 200
MAX_BYTES = 24576
MIN_INTERVAL_S = 0.2               # au plus 5 evenements par seconde


def clean(text, limit: int = MAX_LINE) -> str:
    """Texte affichable : sans caracteres de controle (sauf le retour a la ligne), tronque."""
    if text is None:
        return ""
    return "".join(ch for ch in str(text) if ch == "\n" or ch >= " ")[:limit]


def _num(value, digits: int = 3) -> float:
    try:
        v = float(value)
    except (TypeError, ValueError):
        return 0.0
    if v != v or v in (float("inf"), float("-inf")):
        return 0.0
    return round(v, digits)


def build_state(*, set_name="", scenes=(), active=None, vj_running=False, vj_remaining="", locked=False, phase=None, bpm=None,
                voyants=None, masters=None, autos=None, text="", projection_open=False, msg="", build=0.0, preview_on=False,
                preview_note="") -> dict:
    """Etat complet. `scenes` : suite de (rang, nom) ; `phase` : dict du `PhaseTracker.snapshot()` ou None ; `voyants` : textes deja formes
    (`phase_why`, `strobe`, `sources`) ; `masters` : cle -> valeur ; `autos` : cle -> bool."""
    rows = [{"i": int(i), "n": clean(n, MAX_NAME)} for i, n in list(scenes)[:MAX_SCENES]]
    ph = None
    if phase:
        ph = {"name": clean(phase.get("name"), 20), "label": clean(phase.get("label"), 20)}
    v = voyants or {}
    return {
        "v": VERSION,
        "set": {"name": clean(set_name, MAX_NAME), "scenes": rows, "active": None if active is None else int(active)},
        "vj": {"running": bool(vj_running), "remaining": clean(vj_remaining, 30), "locked": bool(locked)},
        "phase": ph,
        "bpm": None if not bpm else round(float(bpm)),
        "voyants": {"phase": clean(v.get("phase")), "strobe": clean(v.get("strobe")), "sources": clean(v.get("sources"))},
        "masters": {k: _num(val) for k, val in sorted((masters or {}).items()) if k in remote_auth.MASTER_KEYS},
        "autos": {k: bool(val) for k, val in sorted((autos or {}).items()) if k in remote_auth.AUTO_KEYS},
        "text": clean(text, remote_auth.MAX_TEXT),
        "projection": bool(projection_open),
        "msg": clean(msg),
        "build": _num(build, 2),
        "preview": {"on": bool(preview_on), "note": clean(preview_note, 60)},
    }


def to_json(state: dict) -> str:
    """JSON canonique (cles triees, compact, UTF-8 non echappe) : deux etats egaux donnent le meme texte."""
    return json.dumps(state, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


class Throttle:
    """Decide quand emettre : seulement si l'etat a change ET qu'au moins `MIN_INTERVAL_S` se sont ecoulees depuis le dernier envoi (le dernier
    etat est toujours envoye, mais plus tard : jamais perdu)."""

    def __init__(self, interval: float = MIN_INTERVAL_S) -> None:
        self.interval = interval
        self.last_json = ""
        self.last_at = -1e9

    def next(self, state_json: str, now: float):
        """Le texte JSON a envoyer maintenant, ou None."""
        if state_json == self.last_json or now - self.last_at < self.interval:
            return None
        self.last_json, self.last_at = state_json, now
        return state_json

    def force(self) -> None:
        """Le prochain etat sera envoye meme s'il n'a pas change (nouvel abonne)."""
        self.last_json, self.last_at = "", -1e9

