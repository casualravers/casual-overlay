"""Habillage de la fenetre de reglages : boutons arrondis, radios, cases et listes deroulantes modernes.

On garde les VRAIS widgets Tk (classe, `invoke()`, `state`, variables inchangees : `gui_gates`, `gui_colors`, les tests et le code
d'audio2wave continuent de marcher) et on leur pose des **images arrondies dessinees avec Pillow** (4x puis reduites : bords
lisses). `apply(racine)` parcourt l'arbre APRES la construction, y compris les panneaux d'audio2wave que l'on ne modifie pas.

Un widget habille garde une `_sk` (etat de l'habillage) ; `configure(bg=..., text=..., state=...)` et `cget("bg")` sont
interceptes sur ces widgets (la couleur de fond voulue = la couleur de remplissage de l'image, le fond reel du widget est celui de
son parent pour que les coins soient transparents). Les images sont mises en cache par interpreteur Tk.

Regles d'habillage :
- Bouton : neutre (carte) par defaut ; accent seulement pour les actions principales (`PRIMARY`) ; une couleur de fond propre
  (touche de scene active en ambre...) est respectee. Survol / appui / desactive = variantes de la meme couleur.
- Radio : anneau + point ; Case : carre arrondi + coche, a la couleur de l'accent de la partie (`zone`).
- Liste deroulante (OptionMenu) : champ arrondi avec chevron. Menubutton a fleche dans le texte : simple survol.
"""
from __future__ import annotations

import tkinter as tk
import tkinter.font as tkfont
from tkinter import ttk

from PIL import Image, ImageDraw, ImageTk

import icons
import theme as T

SS = 4                                  # sur-echantillonnage du dessin
PRIMARY = {"Sauvegarder", "Enregistrer", "Demarrer la projection"}      # textes des boutons a l'accent (les autres sont neutres)
BTN_MARGIN = (3, 1)                      # transparent autour d'un bouton : jamais colle a son voisin
GAP = 6                                  # espace image / texte des radios et cases
ICON_PX = 14                             # taille des icones des boutons, des titres, des onglets et des cases
# Boutons dont l'icone suffit : le libelle disparait de l'image (le widget le garde) et devient l'info-bulle.
ICON_ONLY = {"supprimer", "mettre a jour", "restaurer", "actualiser", "sauvegarder", "dupliquer la derniere", "tirer maintenant",
             "rescanner", "centrer", "aucun", "aucune", "plein ecran", "renommer", "retirer"}
ICON_TOGGLES = {"image", "texte", "video", "audio2wave", "fond genere", "halo holographique", "fonte acide", "cellules",
                "recuperer les medias", "automations actives", "phases auto"}      # cases et radios qui recoivent une icone
_pil_cache: dict = {}
_installed = False


# ------------------------------------------------------------------------------- dessin (Pillow)
def _rgb(color: str) -> tuple:
    return T.hex_to_rgb(color)


def _canvas(w: int, h: int, color: str) -> tuple:
    big = Image.new("RGBA", (w * SS, h * SS), _rgb(color) + (0,))      # couleur de frange = celle de la forme
    return big, ImageDraw.Draw(big)


def _finish(big: Image.Image, w: int, h: int) -> Image.Image:
    return big.resize((w, h), Image.LANCZOS)


def round_rect_image(w: int, h: int, fill: str, radius: int = T.RADIUS, outline: str | None = None,
                     margin: tuple = (0, 0)) -> Image.Image:
    """Rectangle arrondi de w x h ; `margin` = (mx, my) pixels transparents autour (l'image fait w + 2 mx x h + 2 my) :
    des boutons voisins ne se touchent jamais, meme dans un conteneur sans espacement."""
    key = ("rr", w, h, fill, radius, outline, margin)
    if key not in _pil_cache:
        mx, my = margin
        big, d = _canvas(w + 2 * mx, h + 2 * my, fill)
        box = (mx * SS, my * SS, (mx + w) * SS - 1, (my + h) * SS - 1)
        if outline:
            d.rounded_rectangle(box, radius * SS, fill=_rgb(outline) + (255,))
            d.rounded_rectangle((box[0] + SS, box[1] + SS, box[2] - SS, box[3] - SS), max(0, (radius - 1) * SS),
                                fill=_rgb(fill) + (255,))
        else:
            d.rounded_rectangle(box, radius * SS, fill=_rgb(fill) + (255,))
        _pil_cache[key] = _finish(big, w + 2 * mx, h + 2 * my)
    return _pil_cache[key]


def field_image(w: int, h: int, fill: str, border: str, chevron: str) -> Image.Image:
    """Champ arrondi avec un chevron a droite (liste deroulante)."""
    key = ("field", w, h, fill, border, chevron)
    if key not in _pil_cache:
        img = round_rect_image(w, h, fill, T.RADIUS, border).copy()
        big = img.resize((w * SS, h * SS), Image.LANCZOS)
        d = ImageDraw.Draw(big)
        cx, cy, a = (w - 13) * SS, (h // 2) * SS, 3.2 * SS
        d.line([(cx - a, cy - a * 0.55), (cx, cy + a * 0.55), (cx + a, cy - a * 0.55)], fill=_rgb(chevron) + (255,),
               width=int(1.6 * SS), joint="curve")
        _pil_cache[key] = _finish(big, w, h)
    return _pil_cache[key]


def radio_image(on: bool, accent: str, disabled: bool = False, size: int = 18) -> Image.Image:
    key = ("radio", on, accent, disabled, size)
    if key not in _pil_cache:
        ring = T.FAINT if disabled else (accent if on else T.MUTED)
        w = size + GAP
        big, d = _canvas(w, size, ring)
        r = size * SS
        d.ellipse((SS, SS, r - SS, r - SS), fill=_rgb(T.PANEL) + (255,), outline=_rgb(ring) + (255,), width=int(1.6 * SS))
        if on:
            m = 5 * SS
            d.ellipse((m, m, r - m, r - m), fill=_rgb(T.FAINT if disabled else accent) + (255,))
        _pil_cache[key] = _finish(big, w, size)
    return _pil_cache[key]


def check_image(on: bool, accent: str, disabled: bool = False, size: int = 17) -> Image.Image:
    key = ("check", on, accent, disabled, size)
    if key not in _pil_cache:
        edge = T.FAINT if disabled else T.MUTED
        fill = (T.FAINT if disabled else accent) if on else T.PANEL
        w = size + GAP
        big, d = _canvas(w, size, fill)
        r = size * SS
        if on:
            d.rounded_rectangle((0, 0, r - 1, r - 1), T.CHECK_RADIUS * SS, fill=_rgb(fill) + (255,))
            pts = [(r * 0.25, r * 0.53), (r * 0.43, r * 0.71), (r * 0.76, r * 0.31)]
            d.line(pts, fill=_rgb(T.ON_ACCENT) + (255,), width=int(2.2 * SS), joint="curve")
        else:
            d.rounded_rectangle((SS, SS, r - SS, r - SS), T.CHECK_RADIUS * SS, fill=_rgb(fill) + (255,),
                                outline=_rgb(edge) + (255,), width=int(1.5 * SS))
        _pil_cache[key] = _finish(big, w, size)
    return _pil_cache[key]


def with_icon(base: Image.Image, icon: str, color: str) -> Image.Image:
    """`base` (anneau ou case + espace) suivie d'une icone et d'un espace."""
    key = ("withicon", id(base), icon, color)
    if key not in _pil_cache:
        out = Image.new("RGBA", (base.width + ICON_PX + GAP, base.height), (0, 0, 0, 0))
        out.paste(base, (0, 0), base)
        ico = icons.image(icon, ICON_PX, color)
        out.paste(ico, (base.width, (base.height - ICON_PX) // 2), ico)
        _pil_cache[key] = out
    return _pil_cache[key]

def tick_image(color: str, height: int, width: int = 4, gap: int = 8) -> Image.Image:
    """Petit repere vertical arrondi a l'accent, a gauche d'un titre (marge transparente a droite)."""
    key = ("tick", color, height, width, gap)
    if key not in _pil_cache:
        w = width + gap
        big, d = _canvas(w, height, color)
        d.rounded_rectangle((0, 0, width * SS - 1, height * SS - 1), (width // 2) * SS, fill=_rgb(color) + (255,))
        _pil_cache[key] = _finish(big, w, height)
    return _pil_cache[key]


def tick_icon_image(color: str, height: int, icon: str, size: int = ICON_PX) -> Image.Image:
    """Repere vertical + icone a l'accent (titre de section), suivis d'un espace transparent."""
    key = ("tickicon", color, height, icon, size)
    if key not in _pil_cache:
        tick = tick_image(color, height, 4, 7)
        ico = icons.image(icon, size, color)
        out = Image.new("RGBA", (tick.width + size + 6, max(height, size)), (0, 0, 0, 0))
        out.paste(tick, (0, (out.height - tick.height) // 2), tick)
        out.paste(ico, (tick.width, (out.height - size) // 2), ico)
        _pil_cache[key] = out
    return _pil_cache[key]


def title(label: tk.Label, color: str, kicker: bool = True) -> None:
    """Titre de section : repere d'accent (+ icone si le titre en a une) + texte a l'accent, sans bande de fond (kicker = petit titre, sinon titre de partie)."""
    try:
        bg = str(label.master.cget("bg"))
    except tk.TclError:
        bg = T.BG
    height = 12 if kicker else 20
    icon = icons.for_text(str(label.cget("text")))
    img = tick_icon_image(color, height, icon, ICON_PX if kicker else 18) if icon else tick_image(color, height)
    label.config(bg=bg, fg=color, font=T.FONT_KICKER if kicker else T.FONT_TITLE, anchor="w", compound="left",
                 image=photo(label, img), padx=2 if kicker else 6, pady=3 if kicker else 6)


def photo(w: tk.Misc, img: Image.Image) -> ImageTk.PhotoImage:
    """PhotoImage de `img`, une seule fois par interpreteur Tk (la racine garde le cache)."""
    root = w._root()
    cache = root.__dict__.setdefault("_skin_photos", {})
    key = id(img)
    if key not in cache:
        cache[key] = (img, ImageTk.PhotoImage(img, master=root))      # `img` gardee : son id ne sera pas reutilise
    return cache[key][1]


# ------------------------------------------------------------------------------- habillage d'un widget
def accent_for(w: tk.Misc) -> str:
    """Accent de la partie ou se trouve le widget : le premier ancetre marque par `zone()`, sinon turquoise (LIVE)."""
    node = w
    while node is not None:
        color = getattr(node, "_skin_accent", None)
        if color:
            return color
        node = getattr(node, "master", None)
    return T.LIVE


SCROLL_STYLE = "Casual.Vertical.TScrollbar"
SCROLL_WIDTH = 10
SCROLL_STYLE_H = "Casual.Horizontal.TScrollbar"
FIELD_MAX_TEXT = 360                                  # px : un nom de preset tres long n'elargit pas les listes a l'infini


def scrollbar(parent: tk.Misc, command=None, orient: str = "vertical") -> ttk.Scrollbar:
    """Ascenseur vertical aux couleurs de la GUI. Le `tk.Scrollbar` de Windows ignore les couleurs (rail gris clair, fleches : un
    bloc clair dans une fenetre sombre) : on prend un `ttk.Scrollbar` (theme `clam`, le seul dont les couleurs se reglent) sans
    fleches, fin, poignee discrete qui s'eclaircit au survol et passe a l'accent de la partie LIVE quand on la tient. Meme
    interface (`set`, `command`, pack / grid) : a brancher a la place de `tk.Scrollbar`."""
    root = parent._root()
    style = ttk.Style(root)
    style.theme_use("clam")
    if not getattr(root, "_skin_scroll_style", False):          # une fois par racine Tk (le style est a l'interprete)
        root._skin_scroll_style = True
        thumb = T.mix(T.CARD, T.MUTED, 0.35)
        style.layout(SCROLL_STYLE, [("Vertical.Scrollbar.trough", {"sticky": "ns", "children": [
            ("Vertical.Scrollbar.thumb", {"expand": "1", "sticky": "nswe"})]})])
        style.configure(SCROLL_STYLE, troughcolor=T.BG, background=thumb, bordercolor=T.BG, lightcolor=thumb, darkcolor=thumb,
                        relief="flat", borderwidth=0, arrowsize=SCROLL_WIDTH, gripcount=0)
        style.map(SCROLL_STYLE, background=[("pressed", T.LIVE), ("active", T.mix(thumb, T.MUTED, 0.5))],
                  lightcolor=[("pressed", T.LIVE), ("active", T.mix(thumb, T.MUTED, 0.5))],
                  darkcolor=[("pressed", T.LIVE), ("active", T.mix(thumb, T.MUTED, 0.5))])
        style.layout(SCROLL_STYLE_H, [("Horizontal.Scrollbar.trough", {"sticky": "ew", "children": [
            ("Horizontal.Scrollbar.thumb", {"expand": "1", "sticky": "nswe"})]})])
        style.configure(SCROLL_STYLE_H, **style.configure(SCROLL_STYLE))
        style.map(SCROLL_STYLE_H, **{k: style.map(SCROLL_STYLE, k) for k in ("background", "lightcolor", "darkcolor")})
    horizontal = orient == "horizontal"
    return ttk.Scrollbar(parent, orient=orient, style=SCROLL_STYLE_H if horizontal else SCROLL_STYLE, command=command)


def zone(widget: tk.Misc, accent: str) -> None:
    """Marque un conteneur : ses radios, cases et boutons d'accent prennent cette couleur."""
    widget._skin_accent = accent


_orig = {}      # classe -> (configure, cget)


def _norm(w: tk.Misc, color: str) -> str:
    """Couleur en #rrggbb (un nom de couleur Tk ou systeme, comme SystemButtonFace, est converti)."""
    color = str(color)
    if len(color) == 7 and color.startswith("#"):
        return color.lower()
    r, g, b = w.winfo_rgb(color)
    return "#%02x%02x%02x" % (r >> 8, g >> 8, b >> 8)


def _font_of(w: tk.Misc) -> tkfont.Font:
    return tkfont.Font(root=w._root(), font=w.cget("font"))


def _state(w: tk.Misc) -> str:
    sk = w._sk
    if str(_orig_cget(w, "state")) == "disabled":
        return "disabled"
    if sk.get("down"):
        return "down"
    return "hover" if sk.get("hover") else "normal"


def _base(w):
    return next((c for c in _orig if isinstance(w, c)), None)


def _orig_cget(w, key):
    return _orig[_base(w)][1](w, key)


def _orig_configure(w, **kw):
    return _orig[_base(w)][0](w, **kw)


def _button_colors(w: tk.Misc, state: str) -> tuple:
    sk = w._sk
    fill = sk["fill"]
    parent = sk["parent"]
    ghost = fill.lower() == parent.lower()
    if state == "disabled":
        return (parent if ghost else T.mix(fill, parent, 0.55)), T.FAINT
    if state == "down":
        return (T.CARD if ghost else T.darken(fill, 0.12)), sk["fg"]
    if state == "hover":
        return (T.CARD if ghost else (T.lighten(fill, 0.16) if fill != T.CARD else T.HOVER)), sk["fg"]
    return fill, sk["fg"]


def _pil_font(font: tkfont.Font, root) -> "ImageFont.FreeTypeFont | None":
    """Police PIL equivalente a une police Tk (meme famille, taille, graisse), ou None si le fichier est introuvable."""
    from PIL import ImageFont
    key = ("pilfont", font.actual("family"), font.actual("size"), font.actual("weight"))
    if key not in _pil_cache:
        family = font.actual("family").lower().replace(" ", "")
        bold = font.actual("weight") == "bold"
        size = font.actual("size")
        px = abs(size) if size < 0 else round(size * root.winfo_fpixels("1i") / 72.0)
        names = {"segoeui": ("segoeuib.ttf" if bold else "segoeui.ttf")}.get(family, family + (".ttf"))
        pf = None
        for candidate in (names, "segoeuib.ttf" if bold else "segoeui.ttf", "arialbd.ttf" if bold else "arial.ttf"):
            try:
                pf = ImageFont.truetype(candidate, px)
                break
            except OSError:
                continue
        _pil_cache[key] = pf
    return _pil_cache[key]


def _icon_button_image(w: tk.Misc, width: int, height: int, fill: str, fg: str, icon: str, text: str, font: tkfont.Font):
    """Bouton a icone : forme arrondie + icone + libelle, dessines ensemble (le libelle du widget reste le meme : `cget("text")`)."""
    pf = _pil_font(font, w)
    if pf is None:
        return None
    key = ("iconbtn", width, height, fill, fg, icon, text, id(pf))
    if key not in _pil_cache:
        base = round_rect_image(width, height, fill, T.RADIUS, margin=BTN_MARGIN).copy()
        d = ImageDraw.Draw(base)
        text_w = d.textlength(text, font=pf) if text else 0
        content = ICON_PX + (GAP + text_w if text else 0)
        x0 = BTN_MARGIN[0] + (width - content) / 2
        cy = BTN_MARGIN[1] + height / 2
        ico = icons.image(icon, ICON_PX, fg)
        base.paste(ico, (round(x0), round(cy - ICON_PX / 2)), ico)
        if text:
            d.text((x0 + ICON_PX + GAP, cy), text, font=pf, fill=_rgb(fg) + (255,), anchor="lm")
        _pil_cache[key] = base
    return _pil_cache[key]


def _tip_place(w: tk.Misc, tip: tk.Toplevel) -> None:
    """Place l'info-bulle sous le widget, gardee dans la fenetre (meme regle que celle d'audio2wave)."""
    tip.update_idletasks()
    tw, th = tip.winfo_reqwidth(), tip.winfo_reqheight()
    win = w.winfo_toplevel()
    left, top = win.winfo_rootx(), win.winfo_rooty()
    right, bottom = left + win.winfo_width(), top + win.winfo_height()
    gap = 4
    x = max(left + gap, min(w.winfo_rootx() + gap, right - tw - gap))
    y = w.winfo_rooty() + w.winfo_height() + gap
    if y + th > bottom - gap:
        above = w.winfo_rooty() - th - gap
        y = above if above >= top + gap else bottom - th - gap
    y = max(top + gap, min(y, bottom - th - gap))
    tip.wm_geometry(f"+{x}+{y}")


def _tip_enter(w: tk.Misc, _e=None) -> None:
    """Bouton a icone seule : le libelle s'affiche en info-bulle. S'il en a deja une (explication), le libelle en devient la premiere ligne."""
    sk = w._sk
    if not sk.get("icon_only"):
        return
    label = str(_orig_cget(w, "text"))
    try:
        theirs = next((c for c in w.winfo_children() if isinstance(c, tk.Toplevel)), None)
        if theirs is not None:                       # info-bulle existante (posee juste avant par le code de la GUI)
            lab = next((c for c in theirs.winfo_children() if isinstance(c, tk.Label)), None)
            if lab is not None and not str(lab.cget("text")).startswith(label + "\n"):
                lab.configure(text=f"{label}\n{lab.cget('text')}")
                _tip_place(w, theirs)
            return
        if sk.get("tip") is not None:
            return
        tip = tk.Toplevel(w)
        tip.wm_overrideredirect(True)
        tip.withdraw()
        tk.Label(tip, text=label, bg=T.PANEL, fg=T.FG, font=T.FONT_SMALL, relief="solid", borderwidth=1, padx=6, pady=3).pack()
        _tip_place(w, tip)
        tip.deiconify()
        sk["tip"] = tip
    except tk.TclError:
        sk["tip"] = None


def _tip_leave(w: tk.Misc, _e=None) -> None:
    tip = w._sk.get("tip")
    w._sk["tip"] = None
    if tip is not None:
        try:
            tip.destroy()
        except tk.TclError:
            pass

def _render_button(w: tk.Misc) -> None:
    sk = w._sk
    text = str(_orig_cget(w, "text"))
    font = sk["font_obj"]
    icon = sk.get("icon") or icons.for_text(text)
    if icon and "\n" in text:
        icon = None
    icon_only = bool(icon) and " ".join(text.split()).lower() in ICON_ONLY
    sk["icon_only"] = icon_only
    extra = (ICON_PX + GAP) if icon else 0
    if icon_only:
        text_w = ICON_PX
    elif sk["chars"]:
        text_w = font.measure("0") * sk["chars"]
    else:
        text_w = max((font.measure(line) for line in text.split("\n")), default=0) + extra
    lines = max(1, text.count("\n") + 1)
    width = text_w + 2 * sk["padx"]
    height = max(21, font.metrics("linespace") * lines + 2 * sk["pady"])
    natural = (width + 2 * BTN_MARGIN[0], height + 2 * BTN_MARGIN[1])        # l'image fait la forme + la marge transparente
    stretch = bool(getattr(w, "_skin_stretch", False))
    if stretch:                               # bouton qui remplit sa case : l'image prend la taille allouee (mise a jour a chaque <Configure>)
        alloc_w, alloc_h = sk.get("alloc", (0, 0))
        width = max(width, alloc_w - 2 * BTN_MARGIN[0])
        height = max(height, alloc_h - 2 * BTN_MARGIN[1])
    state = _state(w)
    fill, fg = _button_colors(w, state)
    key = (width, height, fill, fg, state, icon, text if icon else "", icon_only)
    if sk.get("key") == key:
        return
    sk["key"] = key
    composite = _icon_button_image(w, width, height, fill, fg, icon, "" if icon_only else text, font) if icon else None
    if composite is not None:
        img = photo(w, composite)
        compound = "none"                     # le libelle est dans l'image ; le widget le garde (cget, tests, info-bulles)
    else:
        img = photo(w, round_rect_image(width, height, fill, T.RADIUS, margin=BTN_MARGIN))
        compound = "center"
    sk["img"] = img
    # Etire : la taille VOULUE du widget reste la taille naturelle (en pixels, le bouton a une image), sinon il ne pourrait plus
    # retrecir quand la fenetre diminue ; c'est l'allocation du gestionnaire de mise en page qui donne la taille de l'image.
    _orig_configure(w, image=img, compound=compound, fg=fg, activeforeground=fg, activebackground=sk["parent"],
                    width=natural[0] if stretch else 0, height=natural[1] if stretch else 0)


def _render_toggle(w: tk.Misc) -> None:
    sk = w._sk
    dis = str(_orig_cget(w, "state")) == "disabled"
    make = radio_image if sk["kind"] == "radio" else check_image
    accent = accent_for(w)
    text = str(_orig_cget(w, "text"))
    icon = icons.for_text(text) if " ".join(text.split()).lower() in ICON_TOGGLES else None
    key = (dis, accent, icon)
    if sk.get("key") == key:
        return
    sk["key"] = key
    img_off, img_on = make(False, accent, dis), make(True, accent, dis)
    if icon:                                  # icone apres l'anneau / la case : [o] (icone) libelle
        img_off = with_icon(img_off, icon, T.FAINT if dis else T.MUTED)
        img_on = with_icon(img_on, icon, T.FAINT if dis else accent)
    off, on = photo(w, img_off), photo(w, img_on)
    sk["imgs"] = (off, on)
    _orig_configure(w, image=off, selectimage=on, disabledforeground=T.FAINT, activeforeground=accent if not dis else T.FAINT)


def _render_field(w: tk.Misc) -> None:
    sk = w._sk
    dis = str(_orig_cget(w, "state")) == "disabled"
    font = sk["font_obj"]
    menu = w.nametowidget(str(_orig_cget(w, "menu")))
    labels = [str(w.tk.call(menu._w, "entrycget", i, "-label")) for i in range(int(menu.index("end") or -1) + 1)
              if str(w.tk.call(menu._w, "type", i)) != "separator"]
    labels.append(str(_orig_cget(w, "text")))
    width = min(max([font.measure(t) for t in labels] + [30]), FIELD_MAX_TEXT) + 12 + 28
    height = max(24, font.metrics("linespace") + 8)
    border = T.FAINT if dis else (sk["accent"] if sk.get("hover") else T.LINE)
    key = (width, height, dis, border)
    if sk.get("key") == key:
        return
    sk["key"] = key
    img = photo(w, field_image(width, height, T.PANEL, border, T.FAINT if dis else T.MUTED))
    sk["img"] = img
    _orig_configure(w, image=img, compound="center", indicatoron=0, borderwidth=0, highlightthickness=0, relief="flat",
                    padx=0, pady=0, anchor="w", width=0, height=0)


def _render_scale(w: tk.Misc) -> None:
    """Curseur : poignee a l'accent (le `bg` d'un Scale est la couleur de sa poignee), piste sombre fine ; grise = poignee eteinte."""
    sk = w._sk
    dis = str(_orig_cget(w, "state")) == "disabled"
    _orig_configure(w, **_scale_options(w, dis))


def _scale_options(w: tk.Misc, dis: bool) -> dict:
    accent = accent_for(w)
    if int(str(_orig_cget(w, "showvalue")) or 0):        # la valeur s'affiche au-dessus (audio2wave) : `bg` en est aussi le fond
        return {"bg": w._sk["parent"], "activebackground": T.FAINT if dis else accent, "troughcolor": T.TROUGH,
                "highlightthickness": 0, "bd": 1, "sliderrelief": "raised", "relief": "flat"}
    return {"bg": T.FAINT if dis else accent, "activebackground": T.FAINT if dis else T.lighten(accent, 0.25),
            "troughcolor": T.TROUGH, "highlightthickness": 0, "bd": 0, "sliderrelief": "flat", "relief": "flat"}


_RENDER = {"scale": _render_scale, "button": _render_button, "radio": _render_toggle, "check": _render_toggle, "field": _render_field}


def _on(w, hover=None, down=None):
    sk = getattr(w, "_sk", None)
    if sk is None:
        return
    if hover is not None:
        sk["hover"] = hover
    if down is not None:
        sk["down"] = down
    _RENDER[sk["kind"]](w)


def _on_alloc(w, e) -> None:
    """Un bouton etire (`w._skin_stretch = True`) : son image suit la taille que la mise en page lui donne."""
    sk = getattr(w, "_sk", None)
    if sk is None or sk.get("alloc") == (e.width, e.height):
        return
    sk["alloc"] = (e.width, e.height)
    _render_button(w)


def retint(w: tk.Misc, parent_bg: str) -> None:
    """Le fond du conteneur d'un widget habille change (ligne active d'un panneau) : on le repeint (coins transparents compris)
    sans le reconstruire. Un widget non habille prend simplement le fond."""
    sk = getattr(w, "_sk", None)
    if sk is None:
        try:
            w.configure(bg=parent_bg)
        except tk.TclError:
            pass
        return
    if "parent" not in sk:
        return
    sk["parent"] = parent_bg.lower()
    if sk["kind"] == "button":
        _orig_configure(w, bg=parent_bg, activebackground=parent_bg)
        sk["key"] = None
        _render_button(w)
    elif sk["kind"] in ("radio", "check"):
        _orig_configure(w, bg=parent_bg, activebackground=parent_bg, selectcolor=parent_bg)
    else:
        _orig_configure(w, bg=parent_bg)


def _make_configure(cls):
    base = _orig[cls][0]

    def configure(self, cnf=None, **kw):
        sk = getattr(self, "_sk", None)
        if sk is None or isinstance(cnf, str) or (cnf is None and not kw):
            return base(self, cnf, **kw)
        opts = dict(cnf) if isinstance(cnf, dict) else {}
        opts.update(kw)
        redo = False
        for name in ("bg", "background"):
            if name in opts and sk["kind"] == "button":
                sk["fill"] = opts.pop(name)
                redo = True
        if sk["kind"] == "button":
            if "fg" in opts or "foreground" in opts:
                sk["fg"] = opts.pop("fg", None) or opts.pop("foreground")
                redo = True
            if "width" in opts:
                sk["chars"] = int(opts.pop("width") or 0)
                redo = True
            for name in ("padx", "pady"):
                if name in opts:
                    sk[name] = self.winfo_pixels(opts.pop(name))
                    redo = True
            redo = redo or "text" in opts or "font" in opts
            if "font" in opts:
                base(self, font=opts.pop("font"))
                sk["font_obj"] = _font_of(self)
        elif sk["kind"] == "scale":
            if "state" in opts:
                opts.update(_scale_options(self, str(opts["state"]) == "disabled"))     # un seul appel : voir gui_gates (valeur perimee)
            return base(self, **opts) if opts else None
        else:
            redo = "state" in opts or "text" in opts or "font" in opts
        redo = redo or "state" in opts
        result = base(self, **opts) if opts else None
        if redo:
            sk["key"] = None
            _RENDER[sk["kind"]](self)
        return result

    return configure


def _make_cget(cls):
    base = _orig[cls][1]

    def cget(self, key):
        sk = getattr(self, "_sk", None)
        if sk is not None and sk["kind"] == "button":
            if key in ("bg", "background"):
                return sk["fill"]
            if key in ("fg", "foreground"):
                return sk["fg"]
        return base(self, key)

    return cget


def install() -> None:
    """Intercepte configure / cget des quatre classes de widgets habilles (sans effet sur les widgets non habilles)."""
    global _installed
    if _installed:
        return
    for cls in (tk.Button, tk.Radiobutton, tk.Checkbutton, tk.Menubutton, tk.Scale):
        _orig[cls] = (cls.configure, cls.cget)
        cfg, get = _make_configure(cls), _make_cget(cls)
        cls.configure = cls.config = cfg
        cls.cget = get
        cls.__getitem__ = get
    _installed = True


def skin_widget(w: tk.Misc) -> bool:
    """Habille un widget (idempotent). Rend True s'il l'a fait."""
    cls = w.winfo_class()
    if getattr(w, "_sk", None) is not None or getattr(w, "_noskin", False):
        return False
    if cls == "Entry":
        accent = accent_for(w)
        w.configure(bg=T.PANEL, fg=T.FG, readonlybackground=T.PANEL, disabledbackground=T.BG, disabledforeground=T.FAINT,
                    insertbackground=T.FG, relief="flat", bd=1, highlightthickness=1, highlightbackground=T.LINE,
                    highlightcolor=accent, selectbackground=accent, selectforeground=T.ON_ACCENT)
        w._sk = {"kind": "entry"}
        return True
    if _base(w) is None:
        return False
    if cls != "Scale" and str(_orig_cget(w, "image")):
        return False                              # deja une image (vignette, icone) : pas touche
    parent = _norm(w, _orig_cget(w, "bg"))
    if cls == "Scale":
        w._sk = {"kind": "scale", "parent": parent}
        _orig_configure(w, **_scale_options(w, str(_orig_cget(w, "state")) == "disabled"))
        return True
    ghost_accent = cls == "Menubutton" and "\u25be" in str(_orig_cget(w, "text")) and parent.lower() == T.LIVE.lower()
    if cls == "Button" or ghost_accent:
        fill = parent
        original = fill.lower()
        text = str(_orig_cget(w, "text"))
        # Les fonds d'accent d'audio2wave (turquoise plein) deviennent des boutons neutres, sauf les actions principales.
        if original == T.LIVE.lower():
            fill = accent_for(w) if text in PRIMARY else T.CARD
        font = _font_of(w)
        fg = _norm(w, _orig_cget(w, "fg"))
        if fill == T.CARD or original == T.LIVE.lower() and text not in PRIMARY:
            fg = T.FG
        elif original == T.LIVE.lower():
            fg = T.ON_ACCENT
        # le fond reel du widget : celui de son parent (coins transparents)
        try:
            host_bg = _norm(w, w.master.cget("bg"))
        except tk.TclError:
            host_bg = T.BG
        w._sk = {"kind": "button", "fill": fill, "fg": fg, "parent": host_bg, "font_obj": font,
                 "chars": int(_orig_cget(w, "width") or 0), "padx": 12 if w.winfo_pixels(_orig_cget(w, "padx")) < 4 else
                 w.winfo_pixels(_orig_cget(w, "padx")), "pady": 3}
        extra = {"overrelief": "flat"} if cls == "Button" else {}
        _orig_configure(w, bg=host_bg, borderwidth=0, highlightthickness=0, relief="flat", padx=0, pady=0, compound="center",
                        cursor="hand2", disabledforeground=T.FAINT, **extra)
        w.bind("<Enter>", lambda e, w=w: _on(w, hover=True), add="+")
        w.bind("<Leave>", lambda e, w=w: _on(w, hover=False, down=False), add="+")
        w.bind("<ButtonPress-1>", lambda e, w=w: _on(w, down=True), add="+")
        w.bind("<ButtonRelease-1>", lambda e, w=w: _on(w, down=False), add="+")
        w.bind("<Enter>", lambda e, w=w: _tip_enter(w), add="+")
        w.bind("<Leave>", lambda e, w=w: _tip_leave(w), add="+")
        w.bind("<ButtonPress-1>", lambda e, w=w: _tip_leave(w), add="+")
        if getattr(w, "_skin_stretch", False):
            w.bind("<Configure>", lambda e, w=w: _on_alloc(w, e), add="+")
        _render_button(w)
        return True
    if cls in ("Radiobutton", "Checkbutton"):
        kind = "radio" if cls == "Radiobutton" else "check"
        w._sk = {"kind": kind, "parent": parent}
        _orig_configure(w, indicatoron=0, compound="left", borderwidth=0, highlightthickness=0, relief="flat",
                        overrelief="flat", offrelief="flat", selectcolor=parent, activebackground=parent, padx=4, pady=0,
                        anchor="w", cursor="hand2")
        _render_toggle(w)
        return True
    if cls == "Menubutton":
        text = str(_orig_cget(w, "text"))
        is_option = str(_orig_cget(w, "indicatoron")) in ("1", "true") and "▾" not in text and str(_orig_cget(w, "menu"))
        if is_option:
            w._sk = {"kind": "field", "parent": parent, "font_obj": _font_of(w), "accent": accent_for(w)}
            w.bind("<Enter>", lambda e, w=w: _on(w, hover=True), add="+")
            w.bind("<Leave>", lambda e, w=w: _on(w, hover=False), add="+")
            _render_field(w)
            return True
        # Menubutton "ghost" (la fleche est dans le texte) : pas d'image, un survol discret
        _orig_configure(w, relief="flat", borderwidth=0, highlightthickness=0, cursor="hand2", activebackground=T.CARD)
        icon = icons.for_text(text)
        if icon:                                  # menu deroulant a fleche dans le texte : icone a gauche (Tk la pose, le texte reste le meme)
            fg = _norm(w, _orig_cget(w, "fg"))
            w._sk_icon = photo(w, icons.image(icon, ICON_PX, fg))
            _orig_configure(w, image=w._sk_icon, compound="left", padx=6)
        w.bind("<Enter>", lambda e, w=w: _orig_configure(w, bg=T.CARD), add="+")
        w.bind("<Leave>", lambda e, w=w, b=parent: _orig_configure(w, bg=b), add="+")
        return True
    return False


def skin_notebook(nb: ttk.Notebook) -> None:
    """Icones des onglets (gris, l'onglet choisi a l'accent) : les libelles ne changent pas."""
    if getattr(nb, "_sk_nb", False):
        return
    nb._sk_nb = True
    accent = accent_for(nb)
    tabs = [(i, icons.for_text(nb.tab(i, "text"))) for i in range(nb.index("end"))]
    tabs = [(i, ic) for i, ic in tabs if ic]
    if not tabs:
        return

    def paint(_e=None) -> None:
        try:
            current = nb.index("current")
        except tk.TclError:
            current = -1
        for i, ic in tabs:
            try:
                nb.tab(i, image=photo(nb, icons.image(ic, ICON_PX, accent if i == current else T.MUTED)), compound="left")
            except tk.TclError:
                return
    paint()
    nb.bind("<<NotebookTabChanged>>", paint, add="+")

def apply(root: tk.Misc) -> int:
    """Habille `root` et tous ses descendants. Rend le nombre de widgets habilles."""
    install()
    count = 0
    stack = [root]
    while stack:
        node = stack.pop()
        try:
            children = node.winfo_children()
        except tk.TclError:
            continue
        if node.winfo_class() == "TNotebook":
            try:
                skin_notebook(node)
            except tk.TclError:
                pass
        if node is not root or node.winfo_class() in ("Button", "Radiobutton", "Checkbutton", "Menubutton", "Scale", "Entry"):
            try:
                count += bool(skin_widget(node))
            except tk.TclError:
                pass
        stack.extend(children)
    return count
