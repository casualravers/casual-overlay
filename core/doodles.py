"""Doodles du site Casual Ravers : logique pure de la recuperation (configuration, liste du bucket, fichiers vus, validation, position,
cycle de vie, mur de medias). Stdlib seulement : aucun reseau, aucune image, aucun GL ici (le fil, Pillow et le rendu sont dans engine/).
"""
from __future__ import annotations

import json
from collections.abc import Callable
from pathlib import Path

BUCKET = "doodles"
POLL_S = 5.0                  # intervalle d'interrogation
REQUEST_TIMEOUT_S = 6.0       # delai d'une requete reseau
LIST_LIMIT = 100              # fichiers demandes a chaque interrogation (les plus recents d'abord)
MAX_BYTES = 5 * 1024 * 1024   # taille maximale d'un fichier
MAX_SIDE = 4096               # dimension maximale (px)
MAX_VISIBLE = 5               # medias visibles en meme temps
LIFE_S = 15.0                 # duree d'affichage (fondus compris)
FADE_IN_S = 1.5
FADE_OUT_S = 2.0
MAX_HEIGHT_FRAC = 0.30        # hauteur maximale d'un media (fraction de la hauteur de l'ecran)
CONFIG_NAME = "depot.json"

PNG_MAGIC = b"\x89PNG\r\n\x1a\n"
JPEG_MAGIC = b"\xff\xd8\xff"


def config_path() -> Path:
    return Path.home() / ".audio2wave" / CONFIG_NAME


def parse_config(data) -> tuple[dict | None, str]:
    """Valide le contenu de depot.json (`{"url": ..., "anon_key": ...}`). Rend (config, "") ou (None, message)."""
    if not isinstance(data, dict):
        return None, "Configuration du depot illisible (depot.json)"
    url = str(data.get("url", "")).strip().rstrip("/")
    key = str(data.get("anon_key", "")).strip()
    if not url or not key:
        missing = " et ".join(n for n, v in (("url", url), ("anon_key", key)) if not v)
        return None, f"Configuration du depot incomplete : il manque {missing} dans {CONFIG_NAME}"
    if not url.lower().startswith(("http://", "https://")):
        return None, f"Configuration du depot invalide : l'url doit commencer par https:// ({CONFIG_NAME})"
    return {"url": url, "anon_key": key}, ""


def load_config(path: Path | None = None) -> tuple[dict | None, str]:
    """Lit ~/.audio2wave/depot.json. Fichier absent = message clair, jamais d'exception."""
    path = path or config_path()
    try:
        raw = path.read_text(encoding="utf-8-sig")
    except FileNotFoundError:
        return None, f"Configuration du depot absente : cree {path}"
    except OSError as exc:
        return None, f"Configuration du depot illisible ({exc})"
    try:
        data = json.loads(raw)
    except ValueError:
        return None, f"Configuration du depot illisible : {CONFIG_NAME} n'est pas du JSON valide"
    return parse_config(data)


def list_request(config: dict) -> tuple[str, bytes, dict]:
    """(url, corps, en-tetes) du POST de liste : les LIST_LIMIT fichiers les plus recents."""
    body = json.dumps({"prefix": "", "limit": LIST_LIMIT, "sortBy": {"column": "created_at", "order": "desc"}}).encode("utf-8")
    headers = {"apikey": config["anon_key"], "Authorization": f"Bearer {config['anon_key']}", "Content-Type": "application/json"}
    return f"{config['url']}/storage/v1/object/list/{BUCKET}", body, headers


def download_url(config: dict, name: str) -> str:
    from urllib.parse import quote
    return f"{config['url']}/storage/v1/object/public/{BUCKET}/{quote(name)}"


def parse_listing(raw) -> list[dict]:
    """Reponse de liste (octets, texte ou deja decodee) -> [{"name", "created_at"}], les entrees sans nom (dossiers) ecartees."""
    try:
        data = json.loads(raw) if isinstance(raw, (bytes, bytearray, str)) else raw
    except ValueError:
        raise ValueError("reponse de liste illisible") from None
    if not isinstance(data, list):
        raise ValueError("reponse de liste inattendue")
    out = []
    for item in data:
        if not isinstance(item, dict):
            continue
        name = item.get("name")
        if not isinstance(name, str) or not name or name.startswith(".") or "/" in name:
            continue            # dossier, marqueur (.emptyFolderPlaceholder) ou chemin imbrique
        out.append({"name": name, "created_at": str(item.get("created_at") or "")})
    return out


class Seen:
    """Fichiers deja vus. `arm` memorise l'existant a l'activation (sans rien afficher) ; `fresh` rend les nouveaux, du plus ancien
    au plus recent ; `mark` les range une fois traites (afficher, ou ecarter un fichier invalide : un fichier n'est traite qu'une fois)."""

    def __init__(self) -> None:
        self.names: set[str] = set()

    def arm(self, listing: list[dict]) -> None:
        self.names = {e["name"] for e in listing}

    def fresh(self, listing: list[dict]) -> list[str]:
        new = [e for e in listing if e["name"] not in self.names]
        new.sort(key=lambda e: (e["created_at"], e["name"]))
        return [e["name"] for e in new]

    def mark(self, name: str) -> None:
        self.names.add(name)


def sniff(data: bytes) -> str | None:
    """'png' / 'jpeg' d'apres les premiers octets (le suffixe du nom ne prouve rien), sinon None."""
    if data.startswith(PNG_MAGIC):
        return "png"
    if data.startswith(JPEG_MAGIC):
        return "jpeg"
    return None


def check_download(data: bytes) -> tuple[str | None, str]:
    """Controles avant le decodage : (type, "") si le fichier est acceptable, sinon (None, raison)."""
    if not data:
        return None, "fichier vide"
    if len(data) > MAX_BYTES:
        return None, f"fichier trop gros ({len(data) / 1048576:.1f} Mo, maximum {MAX_BYTES // 1048576} Mo)"
    kind = sniff(data)
    if kind is None:
        return None, "ni PNG ni JPEG"
    return kind, ""


def check_size(width: int, height: int) -> str:
    """"" si les dimensions sont raisonnables, sinon la raison."""
    if width < 1 or height < 1:
        return "dimensions nulles"
    if width > MAX_SIDE or height > MAX_SIDE:
        return f"image trop grande ({width}x{height}, maximum {MAX_SIDE} px)"
    return ""


def place(rng, aspect: float, screen_aspect: float, max_height: float = MAX_HEIGHT_FRAC) -> tuple[float, float, float, float]:
    """Rectangle (x, y, largeur, hauteur) du media en fractions de l'ecran, origine en haut a gauche, TOUJOURS entierement dans le cadre.
    La hauteur vaut `max_height` de l'ecran (ratio de l'image garde) ; une image tres large est reduite pour ne pas depasser en largeur.
    `rng` : objet avec `random()` (random.Random)."""
    aspect = max(float(aspect), 1e-3)
    screen_aspect = max(float(screen_aspect), 1e-3)
    h = max(0.01, min(float(max_height), 1.0))
    w = h * aspect / screen_aspect
    if w > 1.0:
        h, w = h / w, 1.0
    return rng.random() * (1.0 - w), rng.random() * (1.0 - h), w, h


def alpha(age: float, life: float = LIFE_S, fade_in: float = FADE_IN_S, fade_out: float = FADE_OUT_S) -> float:
    """Opacite 0..1 d'un media d'age `age` secondes : fondu d'entree, palier, fondu de sortie ; 0 hors de [0, life]."""
    if age < 0.0 or age >= life:
        return 0.0
    a = 1.0
    if fade_in > 0.0:
        a = min(a, age / fade_in)
    if fade_out > 0.0:
        a = min(a, (life - age) / fade_out)
    return max(0.0, min(1.0, a))


class Wall:
    """Medias visibles. `add` en ajoute un (au-dela de `max_visible` le plus ancien part) ; `step(now)` retire les expires et rend
    les identifiants retires (pour liberer leurs textures) ; `visible(now)` rend (id, rect, opacite). Le temps est injecte."""

    def __init__(self, max_visible: int = MAX_VISIBLE, life: float = LIFE_S,
                 fade_in: float = FADE_IN_S, fade_out: float = FADE_OUT_S) -> None:
        self.max_visible, self.life, self.fade_in, self.fade_out = max(1, int(max_visible)), life, fade_in, fade_out
        self.items: list[dict] = []

    def add(self, ident, rect: tuple[float, float, float, float], now: float) -> list:
        """Ajoute un media ; rend les identifiants evinces par la limite."""
        self.items.append({"id": ident, "rect": rect, "t0": float(now)})
        dropped = []
        while len(self.items) > self.max_visible:
            dropped.append(self.items.pop(0)["id"])
        return dropped

    def step(self, now: float) -> list:
        gone = [it["id"] for it in self.items if now - it["t0"] >= self.life]
        if gone:
            self.items = [it for it in self.items if now - it["t0"] < self.life]
        return gone

    def visible(self, now: float) -> list[tuple]:
        out = []
        for it in self.items:
            a = alpha(now - it["t0"], self.life, self.fade_in, self.fade_out)
            if a > 0.0:
                out.append((it["id"], it["rect"], a))
        return out

    def clear(self) -> list:
        gone = [it["id"] for it in self.items]
        self.items = []
        return gone

    def __len__(self) -> int:
        return len(self.items)


def poll_once(config: dict, seen: Seen, http_post: Callable, http_get: Callable) -> tuple[list[tuple[str, bytes]], list[str], str]:
    """Un tour d'interrogation, sans fil ni Pillow : `http_post(url, body, headers) -> octets`, `http_get(url) -> octets` (injectes ;
    ils levent une exception en cas d'echec reseau, qui remonte a l'appelant). Rend ([(nom, octets valides)], [fichiers ecartes
    avec leur raison], erreur). Un fichier ecarte ou affiche est marque vu ; un echec de telechargement ne le marque pas : le tour
    s'arrete la, `erreur` le dit, et les fichiers deja recuperes sont rendus quand meme (reessai au tour suivant). Un echec de la
    liste leve l'exception."""
    url, body, headers = list_request(config)
    listing = parse_listing(http_post(url, body, headers))
    good, rejected, error = [], [], ""
    for name in seen.fresh(listing):
        try:
            data = http_get(download_url(config, name))
        except Exception as exc:
            error = f"telechargement de {name} impossible ({exc})"
            break
        kind, why = check_download(data)
        seen.mark(name)
        if kind is None:
            rejected.append(f"{name} : {why}")
        else:
            good.append((name, data))
    return good, rejected, error
