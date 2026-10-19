"""Icones de l'interface, dessinees avec Pillow (traits arrondis, sur-echantillonnees puis reduites) : aucun fichier image, aucune police
d'emojis (leur rendu varie d'un PC a l'autre). Une icone = une fonction sur une grille de 24 x 24 ; `image(nom, taille, couleur)` la rend.

Style : traits de 2 unites a bouts ronds, formes pleines pour les actions (lecture, eclair, panique), meme poids partout.
`for_text(texte)` rend l'icone d'un libelle connu (boutons, onglets, titres) : les libelles ne changent jamais, seule l'image s'ajoute.
"""
from __future__ import annotations

import math

from PIL import Image, ImageDraw

import theme as T

SS = 8                      # sur-echantillonnage
GRID = 24.0
_cache: dict = {}


class _Pen:
    """Crayon a bouts ronds sur une grille 24 x 24."""

    def __init__(self, px: int, color: str, stroke: float = 2.0) -> None:
        self.k = px * SS / GRID
        self.img = Image.new("RGBA", (px * SS, px * SS), T.hex_to_rgb(color) + (0,))
        self.d = ImageDraw.Draw(self.img)
        self.rgba = T.hex_to_rgb(color) + (255,)
        self.w = max(1, round(stroke * self.k))

    def p(self, pts):
        return [(x * self.k, y * self.k) for x, y in pts]

    def dot(self, x, y, r=None):
        r = (self.w / 2) if r is None else r * self.k
        self.d.ellipse((x * self.k - r, y * self.k - r, x * self.k + r, y * self.k + r), fill=self.rgba)

    def line(self, pts, closed=False):
        pts = list(pts) + ([pts[0]] if closed else [])
        self.d.line(self.p(pts), fill=self.rgba, width=self.w, joint="curve")
        for x, y in pts:
            self.dot(x, y)

    def poly(self, pts):                       # forme pleine aux coins arrondis
        self.d.polygon(self.p(pts), fill=self.rgba)
        self.line(pts, closed=True)

    def rect(self, x0, y0, x1, y1, r=2.0, fill=False):
        box = (x0 * self.k, y0 * self.k, x1 * self.k, y1 * self.k)
        if fill:
            self.d.rounded_rectangle(box, r * self.k, fill=self.rgba)
        else:
            self.d.rounded_rectangle(box, r * self.k, outline=self.rgba, width=self.w)

    def circle(self, cx, cy, r, fill=False):
        box = ((cx - r) * self.k, (cy - r) * self.k, (cx + r) * self.k, (cy + r) * self.k)
        if fill:
            self.d.ellipse(box, fill=self.rgba)
        else:
            self.d.ellipse(box, outline=self.rgba, width=self.w)

    def arc(self, cx, cy, r, a0, a1):
        box = ((cx - r) * self.k, (cy - r) * self.k, (cx + r) * self.k, (cy + r) * self.k)
        self.d.arc(box, a0, a1, fill=self.rgba, width=self.w)
        for a in (a0, a1):
            self.dot(cx + r * math.cos(math.radians(a)), cy + r * math.sin(math.radians(a)))


# ------------------------------------------------------------------------------- les icones
def _play(p): p.poly([(8, 5), (19, 12), (8, 19)])
def _pause(p): p.rect(6, 5, 10, 19, 1.4, True); p.rect(14, 5, 18, 19, 1.4, True)
def _stop(p): p.rect(6, 6, 18, 18, 2.2, True)
def _next(p): p.poly([(5, 5), (15, 12), (5, 19)]); p.line([(19, 5), (19, 19)])
def _prev(p): p.poly([(19, 5), (9, 12), (19, 19)]); p.line([(5, 5), (5, 19)])
def _bolt(p): p.poly([(13.5, 2.5), (5, 13.5), (11, 13.5), (10, 21.5), (19, 9.5), (13, 9.5)])
def _alert(p): p.line([(12, 3.5), (21.5, 20), (2.5, 20)], closed=True); p.line([(12, 9.5), (12, 14)]); p.dot(12, 17.2, 1.2)
def _lock(p): p.rect(5, 11, 19, 21, 2.5); p.arc(12, 8.5, 4.5, 180, 360); p.line([(7.5, 8.5), (7.5, 11)]); p.line([(16.5, 8.5), (16.5, 11)])
def _unlock(p): p.rect(5, 11, 19, 21, 2.5); p.arc(12, 8.5, 4.5, 180, 360); p.line([(7.5, 8.5), (7.5, 11)])
def _tap(p): p.circle(12, 12, 9); p.circle(12, 12, 4.2); p.dot(12, 12, 1.5)
def _save(p): p.line([(5, 4), (16, 4), (20, 8), (20, 20), (5, 20)], closed=True); p.line([(8, 4), (8, 9), (15, 9), (15, 4)]); p.line([(8, 20), (8, 14), (17, 14), (17, 20)])
def _trash(p): p.line([(4, 7), (20, 7)]); p.line([(9, 7), (9, 4), (15, 4), (15, 7)]); p.line([(6, 7), (7, 20.5), (17, 20.5), (18, 7)]); p.line([(10, 11), (10, 17)]); p.line([(14, 11), (14, 17)])
def _plus(p): p.line([(12, 5), (12, 19)]); p.line([(5, 12), (19, 12)])
def _close(p): p.line([(6, 6), (18, 18)]); p.line([(18, 6), (6, 18)])
def _edit(p): p.line([(4, 20), (5, 15), (16, 4), (20, 8), (9, 19), (4, 20)]); p.line([(13, 7), (17, 11)])
def _folder(p): p.line([(3, 6), (9, 6), (11.5, 8.5), (21, 8.5), (21, 19), (3, 19)], closed=True)
def _image(p): p.rect(3, 4, 21, 20, 3); p.circle(9, 10, 2); p.line([(3.5, 18), (9, 13.5), (13, 17), (16, 14), (21, 18.5)])
def _text(p): p.line([(5, 6), (19, 6)]); p.line([(12, 6), (12, 19)]); p.line([(9, 19), (15, 19)])
def _video(p): p.rect(3, 5, 21, 19, 3); p.poly([(10, 9), (15.5, 12), (10, 15)])
def _wave(p):
    for x, h in ((5, 3.5), (9, 7), (13, 10), (17, 5), (21, 2)):
        p.line([(x - 1, 12 - h), (x - 1, 12 + h)])
def _sliders(p):
    for y, x in ((6, 8), (12, 16), (18, 11)):
        p.line([(4, y), (20, y)]); p.circle(x, y, 2.4, True)
def _monitor(p): p.rect(3, 4, 21, 16, 2.5); p.line([(12, 16), (12, 20)]); p.line([(8, 20), (16, 20)])
def _refresh(p): p.arc(12, 12, 7.5, 40, 330); p.poly([(18.5, 3.5), (19.8, 9.2), (14.2, 8.3)])
def _undo(p): p.arc(12.5, 13, 7, 200, 80 + 360); p.poly([(3.5, 8), (4.5, 14), (10, 11.5)])
def _up(p): p.line([(6, 15), (12, 8.5), (18, 15)])
def _down(p): p.line([(6, 9), (12, 15.5), (18, 9)])
def _right(p): p.line([(9, 6), (15.5, 12), (9, 18)])
def _dice(p): p.rect(4, 4, 20, 20, 4); [p.dot(x, y, 1.4) for x, y in ((9, 9), (15, 9), (12, 12), (9, 15), (15, 15))]
def _sparkle(p): p.poly([(11, 3), (13, 9.5), (19.5, 11.5), (13, 13.5), (11, 20), (9, 13.5), (2.5, 11.5), (9, 9.5)]); p.poly([(19, 3), (19.8, 5.2), (22, 6), (19.8, 6.8), (19, 9), (18.2, 6.8), (16, 6), (18.2, 5.2)])
def _layers(p): p.line([(12, 3.5), (21, 8.5), (12, 13.5), (3, 8.5)], closed=True); p.line([(3, 12.5), (12, 17.5), (21, 12.5)]); p.line([(3, 16.5), (12, 21.5), (21, 16.5)])
def _eye(p): p.line([(2.5, 12), (6, 7.5), (12, 5.5), (18, 7.5), (21.5, 12), (18, 16.5), (12, 18.5), (6, 16.5)], closed=True); p.circle(12, 12, 3)
def _download(p): p.line([(12, 4), (12, 15)]); p.line([(7, 10.5), (12, 15.5), (17, 10.5)]); p.line([(4, 19.5), (20, 19.5)])
def _check(p): p.circle(12, 12, 9.2); p.line([(7.8, 12.3), (10.8, 15.3), (16.5, 9)])
def _grid(p): p.rect(4, 4, 10.5, 10.5, 1.8); p.rect(13.5, 4, 20, 10.5, 1.8); p.rect(4, 13.5, 10.5, 20, 1.8); p.rect(13.5, 13.5, 20, 20, 1.8)
def _drop(p): p.line([(12, 3), (18, 11.5), (18, 14), (15.5, 19), (12, 20.5), (8.5, 19), (6, 14), (6, 11.5)], closed=True)
def _sun(p):
    p.circle(12, 12, 4.2)
    for a in range(0, 360, 45):
        c, s = math.cos(math.radians(a)), math.sin(math.radians(a))
        p.line([(12 + 7.5 * c, 12 + 7.5 * s), (12 + 10 * c, 12 + 10 * s)])
def _zigzag(p): p.line([(3, 14), (6.5, 7), (10, 17), (13.5, 6), (17, 16), (21, 9)])
def _clock(p): p.circle(12, 12, 9.2); p.line([(12, 6.5), (12, 12), (16, 14.5)])
def _bookmark(p): p.line([(7, 3.5), (17, 3.5), (17, 20.5), (12, 16.5), (7, 20.5)], closed=True)
def _crosshair(p): p.circle(12, 12, 6.5); p.line([(12, 2), (12, 6)]); p.line([(12, 18), (12, 22)]); p.line([(2, 12), (6, 12)]); p.line([(18, 12), (22, 12)])
def _fullscreen(p):
    for sx, sy in ((1, 1), (-1, 1), (1, -1), (-1, -1)):
        cx, cy = 12 + sx * 8.5, 12 + sy * 8.5
        p.line([(cx - sx * 5, cy), (cx, cy), (cx, cy - sy * 5)])
def _bars(p):
    for x, h in ((6, 6), (12, 12), (18, 9)):
        p.line([(x, 20), (x, 20 - h)])
def _code(p): p.line([(8.5, 6.5), (3, 12), (8.5, 17.5)]); p.line([(15.5, 6.5), (21, 12), (15.5, 17.5)]); p.line([(13.5, 4.5), (10.5, 19.5)])
def _copy(p): p.rect(8, 8, 20, 20, 2.5); p.line([(16, 8), (16, 5.5), (13.5, 4), (5.5, 4), (4, 5.5), (4, 13.5), (5.5, 16), (8, 16)])
def _list(p):
    for y in (6.5, 12, 17.5):
        p.line([(8, y), (20, y)]); p.dot(4.5, y, 1.2)
def _menu(p): p.line([(4, 7), (20, 7)]); p.line([(4, 12), (20, 12)]); p.line([(4, 17), (20, 17)])
def _mic(p): p.rect(9, 3, 15, 14, 3); p.arc(12, 12, 7, 20, 160); p.line([(12, 19), (12, 22)])
def _palette(p): p.circle(12, 12, 9.2); [p.dot(x, y, 1.4) for x, y in ((8, 9), (13, 7.5), (17, 11), (8, 14.5))]
def _star(p): p.poly([(12, 3), (14.6, 9.2), (21, 9.7), (16, 14), (17.7, 20.5), (12, 17), (6.3, 20.5), (8, 14), (3, 9.7), (9.4, 9.2)])
def _minus(p): p.circle(12, 12, 9.2); p.line([(7.5, 12), (16.5, 12)])
def _mute(p): p.poly([(4, 9.5), (8, 9.5), (13, 5), (13, 19), (8, 14.5), (4, 14.5)]); p.line([(17, 9.5), (21.5, 14.5)]); p.line([(21.5, 9.5), (17, 14.5)])
def _shuffle(p): p.line([(3, 17), (8, 17), (16, 7), (20, 7)]); p.line([(3, 7), (8, 7), (10.5, 10.5)]); p.line([(13.5, 13.5), (16, 17), (20, 17)]); p.line([(17.5, 4.5), (20.5, 7), (17.5, 9.5)]); p.line([(17.5, 14.5), (20.5, 17), (17.5, 19.5)])
def _cube(p): p.line([(12, 3), (20.5, 7.5), (20.5, 16.5), (12, 21), (3.5, 16.5), (3.5, 7.5)], closed=True); p.line([(3.5, 7.5), (12, 12), (20.5, 7.5)]); p.line([(12, 12), (12, 21)])


_NAMES = ("play pause stop next prev bolt alert lock unlock tap save trash plus close edit folder image text video wave sliders monitor refresh undo "
          "up down right dice sparkle layers eye download check grid drop sun zigzag clock bookmark crosshair fullscreen bars code copy list menu "
          "mic palette star cube minus mute shuffle").split()
ICONS = {n: globals()["_" + n] for n in _NAMES}


def image(name: str, size: int = 16, color: str = T.FG) -> Image.Image:
    """Icone `name` en `size` x `size` px (fond transparent) a la couleur `color`."""
    key = (name, size, color)
    if key not in _cache:
        pen = _Pen(size, color)
        ICONS[name](pen)
        _cache[key] = pen.img.resize((size, size), Image.LANCZOS)
    return _cache[key]


# ------------------------------------------------------------------------------- libelle -> icone
# Les libelles ne changent jamais (les tests et le code les retrouvent par leur texte) : seul l'image s'ajoute.
TEXT_ICONS = {
    # actions
    "sauvegarder": "save", "sauvegarder sous": "save", "enregistrer": "save", "sauver": "save", "sauver reglages": "save", "garder ce tirage...": "save",
    "mettre a jour": "refresh", "actualiser": "refresh", "rescanner": "refresh", "recharger shaders": "code",
    "supprimer": "trash", "restaurer": "undo", "annuler": "close", "aucun": "close", "aucune": "close", "fermer": "close",
    "centrer": "crosshair", "ambiance par defaut": "sparkle", "diagnostic": "check", "plein ecran": "fullscreen", "barres debug": "bars",
    "demarrer la projection": "monitor", "arreter la projection": "monitor", "panique": "alert", "dupliquer la derniere": "copy",
    "tirer maintenant": "dice", "+ dossier": "folder", "tap tempo": "tap", "strobe": "bolt", "drop": "down", "montee": "up", "renommer": "edit", "retirer": "minus", "mode silence": "mute", "charger": "download",
    "verrou": "lock", "retour": "prev", "snap": "image", "ridge": "wave",
    # onglets, titres de partie et de section
    "fond": "layers", "effets": "sparkle", "logo": "image", "aura du logo": "sun", "fonte du logo": "drop", "cellules": "grid", "noise": "zigzag",
    "masters": "sliders", "live": "bolt", "audio": "wave", "compo": "cube", "auto": "refresh", "memoires": "bookmark", "historique": "clock",
    "affichage": "monitor", "overlay": "layers", "scenes": "play", "presets live": "star", "presets overlay": "star", "presets fond": "star",
    "source": "mic", "spectre": "wave", "couleurs": "palette", "sortie": "monitor", "effets sur le logo": "image", "motif genere": "palette",
    "image": "image", "texte": "text", "video": "video", "audio2wave": "wave", "fond genere": "palette",
}
_PREFIX = (("verrou", "lock"), ("strobe", "bolt"), ("montee", "up"), ("set ", "list"), ("reglages vj", "sliders"), ("duree", "clock"),
           ("ordre", "shuffle"), ("recuperer les medias", "download"))


def for_text(text: str) -> str | None:
    """Nom de l'icone d'un libelle connu (casse et espaces ignores ; un libelle a glyphe deja iconique n'en recoit pas), sinon None."""
    t = " ".join(str(text).split()).lower()
    if not t or t[0] in "▶▴▾■◀✕✓":
        return None
    if t in TEXT_ICONS:
        return TEXT_ICONS[t]
    for head, name in _PREFIX:
        if t.startswith(head):
            return name
    return None
