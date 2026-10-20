"""Rendu : bruit, texte et logo, `Renderer` (passes GL) et garde-fou de charge GPU.

Extrait tel quel de `audio2wave_gl` (qui re-exporte ces noms).
"""
from __future__ import annotations

import math
import os
import random
import time
from pathlib import Path

from core import sessionlog
from core import doodles
from core.automation import FX_EFFECTS
from core.controls import STROBE_COOLDOWN_S, StrobeLimiter, _unit, build_strobe_hz, effective_tempo, spectral_hue
from core.params import DEFAULT_PARAMS
from engine.deps import PIL_Image, PIL_ImageDraw, PIL_ImageFont, moderngl, np
from engine.video import LogoVideo

SHADER_DIR = Path(__file__).resolve().parent.parent / "gl_shaders"


def _hash01(i: int, seed: int) -> float:
    """Hachage entier -> [0, 1) (deterministe, sans etat)."""
    h = (int(i) * 374761393 + (seed + 1) * 668265263) & 0xFFFFFFFF
    h = ((h ^ (h >> 13)) * 1274126177) & 0xFFFFFFFF
    h ^= h >> 16
    return (h & 0xFFFFFF) / float(0xFFFFFF)


def noise_value(x: float, seed: int, smooth: float) -> float:
    """Noise 1D de valeur dans [-1, 1] au point `x` (en valeurs de reseau) : `smooth` = 1 interpole en douceur entre deux
    valeurs au hasard, 0 garde la valeur du reseau jusqu'au suivant (par a-coups)."""
    i = math.floor(x)
    f = x - i
    a = _hash01(i, seed) * 2.0 - 1.0
    b = _hash01(i + 1, seed) * 2.0 - 1.0
    s = f * f * (3.0 - 2.0 * f)
    return a + (b - a) * s * min(max(float(smooth), 0.0), 1.0)


NOISE_SPEED = 3.0          # valeurs tirees par seconde (le noise n'est pas reglable : un seul comportement, prereglé)
NOISE_SMOOTH = 0.8


class NoiseDrive:
    """Le noise unique. Une valeur continue au hasard (`n`, [-1, 1]) dont on tire deux signaux, comme une musique
    imaginaire : `swell()` = houle douce 0..1 (comme les basses) et des COUPS au hasard (`beat()` = enveloppe 1 -> 0
    apres chaque coup, `since` = secondes depuis le dernier, comme le kick). Un coup part quand le noise remonte
    franchement (hysterese + delai minimum : jamais de rafale). La phase est integree image par image."""

    HIT_ON, HIT_OFF, REFRACTORY = 0.30, -0.10, 0.45

    def __init__(self) -> None:
        self.phase = 11.0
        self.n = noise_value(self.phase, 0, NOISE_SMOOTH)
        self.since = 9.0
        self.armed = True

    def step(self, dt: float) -> None:
        self.phase += max(dt, 0.0) * NOISE_SPEED
        self.n = noise_value(self.phase, 0, NOISE_SMOOTH)
        self.since += max(dt, 0.0)
        if self.n < self.HIT_OFF:
            self.armed = True
        if self.armed and self.n > self.HIT_ON and self.since > self.REFRACTORY:
            self.since = 0.0
            self.armed = False

    def swell(self) -> float:
        x = min(max((self.n * 0.5 + 0.5 - 0.35) / 0.6, 0.0), 1.0)
        return x * x * (3.0 - 2.0 * x)

    def beat(self) -> float:
        return math.exp(-self.since / 0.2)


def layer_effects(params: dict) -> tuple[list[float], list[float]]:
    """Intensites effectives (interrupteur x intensite x intensite globale) des quatre effets de
    deformation : (fond, logo). Lie, le logo reprend celles du fond."""
    master = float(params["master"])
    bg = [float(params["fx_on"][i]) * float(params["fx_int"][i]) * master for i in range(FX_EFFECTS)]
    if float(params["fx_link"]) >= 0.5:
        return bg, list(bg)
    return bg, [float(params["fxl_on"][i]) * float(params["fxl_int"][i]) * master for i in range(FX_EFFECTS)]


# --- Rendu ------------------------------------------------------------------------

class ShaderError(Exception):
    pass


def load_logo(path: Path):
    """PNG -> tableau uint8 RGBA premultiplie (premiere ligne = haut), <= 2048 px."""
    return _premultiplied(PIL_Image.open(path).convert("RGBA"))


# Polices proposees dans la GUI (nom affiche -> fichier Windows). Seules celles presentes sur le
# poste sont listees; un chemin vers un .ttf/.otf quelconque est aussi accepte.
TEXT_FONTS = {
    "Bahnschrift": "bahnschrift.ttf", "Arial Black": "ariblk.ttf", "Impact": "impact.ttf",
    "Segoe UI Black": "seguibl.ttf", "Segoe UI Bold": "segoeuib.ttf", "Arial Bold": "arialbd.ttf",
    "Verdana Bold": "verdanab.ttf", "Trebuchet MS Bold": "trebucbd.ttf", "Tahoma Bold": "tahomabd.ttf",
    "Georgia Bold": "georgiab.ttf", "Times New Roman Bold": "timesbd.ttf", "Comic Sans MS Bold": "comicbd.ttf",
    "Consolas Bold": "consolab.ttf", "Courier New Bold": "courbd.ttf",
}
AUTO_FONT_ORDER = ("Bahnschrift", "Arial Black", "Impact", "Segoe UI Black", "Arial Bold")


def font_dirs() -> list[Path]:
    dirs = [Path(os.environ.get("WINDIR", r"C:\Windows")) / "Fonts"]
    local = os.environ.get("LOCALAPPDATA")
    if local:
        dirs.append(Path(local) / "Microsoft" / "Windows" / "Fonts")
    return dirs


def available_fonts() -> dict[str, Path]:
    """Polices de TEXT_FONTS effectivement installees: nom -> chemin."""
    found = {}
    for name, filename in TEXT_FONTS.items():
        for directory in font_dirs():
            if (directory / filename).is_file():
                found[name] = directory / filename
                break
    return found


def load_font(font: str, size: int):
    """Police PIL pour un nom de TEXT_FONTS, un chemin de fichier, ou "auto"; repli sur la police
    integree de Pillow si rien n'est trouve (jamais d'exception: un texte doit toujours s'afficher)."""
    installed = available_fonts()
    candidates: list[Path] = []
    if font and font != "auto":
        if font in installed:
            candidates.append(installed[font])
        elif Path(font).is_file():
            candidates.append(Path(font))
    if not candidates:
        candidates += [installed[name] for name in AUTO_FONT_ORDER if name in installed]
    for path in candidates:
        try:
            return PIL_ImageFont.truetype(str(path), size)
        except OSError:
            continue
    try:
        return PIL_ImageFont.load_default(size)
    except TypeError:          # Pillow < 10.1: pas de taille pour la police integree
        return PIL_ImageFont.load_default()


def render_text(text: str, font: str = "auto", color: str = "#ffffff", align: str = "center",
                size: int = 256):
    """Texte (plusieurs lignes possibles) -> tableau uint8 RGBA premultiplie, comme load_logo().
    None si le texte est vide. Une marge transparente de 0,18 x la taille entoure le texte, pour
    que le contour lumineux ne soit pas coupe par le bord du rectangle."""
    if not text or not text.strip():
        return None
    fnt = load_font(font, size)
    spacing = int(size * 0.15)
    align = align if align in ("left", "center", "right") else "center"
    draw = PIL_ImageDraw.Draw(PIL_Image.new("L", (8, 8)))
    box = draw.multiline_textbbox((0, 0), text, font=fnt, align=align, spacing=spacing)
    # Pillow renvoie des flottants pour un texte sur plusieurs lignes (et des entiers pour une ligne).
    left, top, right, bottom = math.floor(box[0]), math.floor(box[1]), math.ceil(box[2]), math.ceil(box[3])
    pad = int(size * 0.18)
    img = PIL_Image.new("RGBA", (max(right - left, 1) + 2 * pad, max(bottom - top, 1) + 2 * pad), (0, 0, 0, 0))
    r, g, b = (int(round(c * 255)) for c in hex_to_rgb(color, "#ffffff"))
    PIL_ImageDraw.Draw(img).multiline_text((pad - left, pad - top), text, font=fnt, fill=(r, g, b, 255),
                                           align=align, spacing=spacing)
    return _premultiplied(img)


def _premultiplied(img):
    """Image PIL RGBA -> uint8 RGBA a alpha premultiplie, reduite a 2048 px maximum."""
    if max(img.size) > 2048:
        ratio = 2048 / max(img.size)
        img = img.resize((max(1, round(img.width * ratio)), max(1, round(img.height * ratio))),
                         PIL_Image.LANCZOS)
    arr = np.asarray(img, dtype=np.float32)
    arr[:, :, :3] *= arr[:, :, 3:4] / 255.0
    return np.rint(arr).astype(np.uint8)


def logo_layout(width: int, height: int, aspect: float, scale: float, pos: tuple[float, float],
                pulse: float = 0.0, jitter: tuple[float, float] = (0.0, 0.0), fit: str = "box"):
    """(cx, cy, demi-largeur, demi-hauteur) en uv (origine haut gauche). pulse (0.12 = +12 %) et
    jitter ne le font jamais sortir du cadre.
    fit="box" (images): le logo tient dans un carre de cote scale*hauteur, ratio conserve.
    fit="height" (texte): le bloc a pour hauteur scale*hauteur, sa largeur suit le ratio (un
    carre de cote scale*hauteur ecraserait une ligne de texte); reduit s'il depasse le cadre."""
    if fit == "height":
        h_px = max(min(scale * height, 0.95 * height), 2.0)
        w_px = h_px * aspect
        if w_px > 0.95 * width:
            w_px, h_px = 0.95 * width, 0.95 * width / aspect
    else:
        box = max(min(scale * height, 0.95 * min(width, height)), 2.0)
        w_px, h_px = (box, box / aspect) if aspect >= 1.0 else (box * aspect, box)
        if w_px > 0.95 * width:
            w_px, h_px = 0.95 * width, 0.95 * width / aspect
    hw0, hh0 = w_px / width / 2.0, h_px / height / 2.0
    cx = min(max(pos[0] + jitter[0], hw0), 1.0 - hw0)
    cy = min(max(pos[1] + jitter[1], hh0), 1.0 - hh0)
    limit = max(1.0, min(cx / hw0, (1.0 - cx) / hw0, cy / hh0, (1.0 - cy) / hh0))
    s = min(1.0 + pulse, limit) if pulse > 0 else 1.0
    hw, hh = hw0 * s, hh0 * s
    cx = min(max(cx, hw), 1.0 - hw)
    cy = min(max(cy, hh), 1.0 - hh)
    return cx, cy, hw, hh


HOLO_DOWNSCALE = 4      # le champ du halo holographique est calcule a 1/4 de la resolution de l'ecran


class Renderer:
    """Deux passes : scene (video + logo) dans un FBO, puis post-traitement vers `target`."""

    def __init__(self, ctx, video_size: tuple[int, int], logo_rgba=None, logo_path: str = "",
                 shader_dir: Path = SHADER_DIR):
        self.ctx = ctx
        self.shader_dir = shader_dir
        self.video_size = video_size
        self.hud = False
        self.rng = np.random.default_rng(3)
        # Phases d'animation du motif de fond, integrees image par image (un changement de
        # vitesse ne fait ainsi jamais sauter le motif, contrairement a speed * temps).
        self._last_t: float | None = None
        self._bg_scroll = 0.0
        self._bg_flip_t = 0.0
        self._bg_clock = 0.0
        self.noise = NoiseDrive()
        self._strobe_last, self._strobe_beats, self._strobe_seen = -10.0, 0, 0     # strobe : dernier eclair, kicks vus, appuis vus
        self._strobe_kicks = 0                                                      # strobe leger : kicks comptes depuis l'armement
        self.strobe_limiter = StrobeLimiter()
        self._breath_t = self._hue_phase = self._comp_t = self._comp_rot = 0.0        # phases integrees (respiration, teinte, composition)
        self._hue_spec = 0.0                                                          # teinte tiree du spectre, lissee
        self._prev_ok = False
        self.fb_tex: list = []                  # trainee : deux images finales hors ecran (on lit l'une, on ecrit l'autre, puis on echange)
        self.fb_fbo: list = []
        self._fb_i = 0
        self.flash_t = -10.0              # instant (time.monotonic) du dernier flash de transition du VJ
        self.noise_out = (0.0, 0.0)      # (houle, coup) x force, pour l'indicateur de la GUI
        self._melt_t = 0.0          # cycles de fonte ecoules (partie entiere = cycles complets)
        self._melt_clock = 0.0      # secondes (derive du bruit)
        self._cell_t = 0.0          # temps integre de la derive des cellules

        self.video_tex = ctx.texture(video_size, 3, data=bytes(video_size[0] * video_size[1] * 3))
        self.video_tex.filter = (moderngl.LINEAR, moderngl.LINEAR)
        self.video_tex.repeat_x = self.video_tex.repeat_y = False
        self.mode_tex = None               # texture d'un mode Snap / Ridge dessine en GL (engine.native_modes) : remplace le flux video

        self.logo_tex = None
        self.logo_aspect = 1.0
        self.has_logo = False
        self.logo_kind = "image"
        self.logo_path = ""
        self.logo_video: LogoVideo | None = None
        self._holo_t = 0.0                 # phase d'animation du halo holographique, integree image par image
        # Dernier chemin DEMANDE (charge ou non): evite de retenter un fichier invalide a chaque image.
        self.logo_requested = logo_path
        self.set_logo(logo_rgba, logo_path)

        self.wall = doodles.Wall()                 # doodles du site visibles (par-dessus tout, apres le post-traitement)
        self.doodle_tex: dict = {}                 # identifiant -> texture
        self._doodle_id = 0
        self.screen_size = (1, 1)                  # taille du dernier rendu (pour le rapport d'aspect des doodles)
        self._doodle_rng = random.Random()
        self.vbo = ctx.buffer(np.array([-1, -1, 1, -1, -1, 1, 1, 1], dtype="f4").tobytes())
        self.scene_prog = self.post_prog = self.scene_vao = self.post_vao = None
        self.scene_tex = self.scene_fbo = self.layer_tex = self.layer_fbo = self.holo_tex = self.holo_fbo = None
        self.reload_shaders()

    def set_logo(self, logo_rgba, path: str = "", kind: str = "image") -> None:
        """Remplace la texture du logo (None = aucun). `kind` = "image" ou "text" (mise en page
        differente, voir logo_layout). A appeler depuis le fil GL."""
        self.release_logo_video()
        self.logo_kind = kind
        old = self.logo_tex
        if logo_rgba is not None:
            h, w = logo_rgba.shape[:2]
            tex = self.ctx.texture((w, h), 4, data=logo_rgba.tobytes())
            tex.build_mipmaps()
            tex.filter = (moderngl.LINEAR_MIPMAP_LINEAR, moderngl.LINEAR)
            tex.anisotropy = 8.0
            self.logo_aspect = w / h
        else:
            tex = self.ctx.texture((1, 1), 4, data=bytes(4))
            self.logo_aspect = 1.0
        tex.repeat_x = tex.repeat_y = False
        self.logo_tex = tex
        self.has_logo = logo_rgba is not None
        self.logo_path = path if logo_rgba is not None else ""
        if old is not None:
            old.release()

    def set_logo_video(self, video: LogoVideo, path: str = "") -> None:
        """Logo anime: la texture (rgba, alpha droit) est reecrite par `video.reader` a chaque
        nouvelle image (voir draw). Le Renderer possede `video` et l'arrete quand il est remplace."""
        self.set_logo(None, "")                 # libere l'ancien logo / l'ancienne video
        old = self.logo_tex
        w, h = video.size
        tex = self.ctx.texture((w, h), 4, data=bytes(w * h * 4))
        tex.filter = (moderngl.LINEAR, moderngl.LINEAR)
        tex.repeat_x = tex.repeat_y = False
        self.logo_tex = tex
        self.logo_aspect = w / h
        self.logo_kind = "video"
        self.logo_video = video
        self.has_logo = True
        self.logo_path = path
        old.release()

    def release_logo_video(self) -> None:
        if self.logo_video is not None:
            self.logo_video.stop()
            self.logo_video = None

    def _read(self, name: str) -> str:
        return (self.shader_dir / name).read_text(encoding="utf-8")

    def reload_shaders(self) -> None:
        """Recompile les GLSL depuis le disque. En cas d'erreur, l'ancien programme reste
        actif et ShaderError porte le message du compilateur."""
        try:
            vert = self._read("quad.vert")
            scene = self.ctx.program(vertex_shader=vert, fragment_shader=self._read("scene.frag"))
            post = self.ctx.program(vertex_shader=vert, fragment_shader=self._read("post.frag"))
            holo = self.ctx.program(vertex_shader=vert, fragment_shader=self._read("holo.frag"))
        except Exception as exc:
            raise ShaderError(str(exc)) from exc
        self.scene_prog, self.post_prog, self.holo_prog = scene, post, holo
        self.scene_vao = self.ctx.vertex_array(scene, [(self.vbo, "2f", "in_pos")])
        self.post_vao = self.ctx.vertex_array(post, [(self.vbo, "2f", "in_pos")])
        self.holo_vao = self.ctx.vertex_array(holo, [(self.vbo, "2f", "in_pos")])

    def _ensure_scene(self, size: tuple[int, int]) -> None:
        if self.scene_tex is not None and self.scene_tex.size == size:
            return
        for old in (self.scene_tex, self.layer_tex, self.holo_tex, *self.fb_tex):
            if old is not None:
                old.release()
        self._prev_ok = False
        # scene_* = couche FOND (opaque), layer_* = couche LOGO (rgba premultiplie, transparente)
        self.scene_tex = self.ctx.texture(size, 4)
        self.layer_tex = self.ctx.texture(size, 4)
        for tex in (self.scene_tex, self.layer_tex):
            tex.filter = (moderngl.LINEAR, moderngl.LINEAR)
            tex.repeat_x = tex.repeat_y = False
        self.scene_fbo = self.ctx.framebuffer(color_attachments=[self.scene_tex])
        self.layer_fbo = self.ctx.framebuffer(color_attachments=[self.layer_tex])
        # Champ du halo holographique: basse resolution (1/HOLO_DOWNSCALE), flottants demi-precision.
        self.holo_tex = self.ctx.texture((max(size[0] // HOLO_DOWNSCALE, 1), max(size[1] // HOLO_DOWNSCALE, 1)), 4,
                                         dtype="f2")
        self.holo_tex.filter = (moderngl.LINEAR, moderngl.LINEAR)
        self.holo_tex.repeat_x = self.holo_tex.repeat_y = False
        self.holo_fbo = self.ctx.framebuffer(color_attachments=[self.holo_tex])
        self.fb_tex = [self.ctx.texture(size, 4) for _ in range(2)]
        for tex in self.fb_tex:
            tex.filter = (moderngl.LINEAR, moderngl.LINEAR)
            tex.repeat_x = tex.repeat_y = False
        self.fb_fbo = [self.ctx.framebuffer(color_attachments=[tex]) for tex in self.fb_tex]

    def _strobe(self, p: dict, state: dict) -> float:
        """Eclair blanc court du strobe : 1 -> 0 en 70 ms. Declenche a chaque appui, puis (arme / maintenu) en cadence fixe ou
        a chaque kick selon `strobe_mode`. Hors ecran noir : il blanchit l'image comme le flash du VJ, sans bandes de glitch."""
        now = time.monotonic()
        beats = int(state.get("beats", 0))
        new_kicks, self._strobe_beats = max(beats - self._strobe_beats, 0), beats
        kicked = new_kicks > 0
        hits = int(p["strobe_hits"])
        fire = hits != self._strobe_seen
        self._strobe_seen = hits
        if float(p["strobe_on"]) >= 0.5:
            if p["strobe_mode"] == "kick":
                fire = fire or kicked
            elif p["strobe_mode"] == "light":                             # leger : un kick sur N (le compteur repart a zero a chaque armement)
                div = max(int(p.get("strobe_div", 4)), 1)
                idx = state.get("beat_index")
                if idx is not None:                                       # grille du temps donnee (« 1 ») : l'eclair tombe sur le 1er temps du groupe
                    if kicked and int(idx) % div == 0:
                        fire = True
                else:
                    self._strobe_kicks += new_kicks
                    if self._strobe_kicks >= div:
                        self._strobe_kicks = 0
                        fire = True
        else:
            self._strobe_kicks = 0
        build = _unit(p.get("build", 0.0))
        timed = True
        if build > 0.0 and not fire:                                  # montee : eclairs automatiques de 0,5 a 16 Hz (lents au debut), le premier tout de suite
            fire = now - self._strobe_last >= 1.0 / build_strobe_hz(build)
            timed = not fire                                          # eclairs de la seule montee : jamais coupes par la duree max (drop ou bouton)
        if fire and self.strobe_limiter.allow(now, float(p["strobe_max_hz"]), float(p["strobe_max_s"]), timed):
            self._strobe_last = now
        elif fire and self.strobe_limiter.blocked(now) and self.strobe_limiter.trips != getattr(self, "_trips_logged", 0):
            self._trips_logged = self.strobe_limiter.trips
            sessionlog.event(f"Strobe limite : rafale de plus de {float(p['strobe_max_s']):.0f} s, pause de {STROBE_COOLDOWN_S:.0f} s")
        return max(0.0, 1.0 - (now - self._strobe_last) / 0.07)

    # ---- Doodles du site : par-dessus tout, apres la trainee et le post-traitement (ni symetrie, ni teinte, ni PANIQUE n'y touchent)
    def add_doodle(self, rgba, now: float) -> None:
        """Pose un doodle (tableau uint8 RGBA premultiplie, premiere ligne = haut) a un endroit tire au hasard, entierement dans le cadre.
        A appeler depuis le fil GL. Au-dela de 5 medias le plus ancien part."""
        h, w = rgba.shape[:2]
        tex = self.ctx.texture((w, h), 4, data=rgba.tobytes())
        tex.build_mipmaps()
        tex.filter = (moderngl.LINEAR_MIPMAP_LINEAR, moderngl.LINEAR)
        tex.repeat_x = tex.repeat_y = False
        self._doodle_id += 1
        ident = self._doodle_id
        self.doodle_tex[ident] = tex
        sw, sh = self.screen_size
        rect = doodles.place(self._doodle_rng, w / h, sw / max(sh, 1))
        for gone in self.wall.add(ident, rect, now):
            self._release_doodle(gone)

    def _release_doodle(self, ident) -> None:
        tex = self.doodle_tex.pop(ident, None)
        if tex is not None:
            tex.release()

    def _draw_doodles(self, target, size: tuple[int, int], now: float, presence: float = 1.0) -> bool:
        """Pose les doodles visibles sur la couche du logo (`target`) en relancant la passe LOGO de `scene.frag` pour chacun : texture et
        rectangle du doodle a la place de ceux du logo, donc fonte acide, cellules, contour lumineux et opacite du logo s'y appliquent
        comme au logo. Rend True si au moins un est dessine."""
        self.screen_size = size
        if not self.wall.items:                     # liste vide : aucun cout
            return False
        for gone in self.wall.step(now):
            self._release_doodle(gone)
        visible = self.wall.posed(now)
        if not visible:
            return False
        sp = self.scene_prog
        target.viewport = (0, 0, size[0], size[1])
        target.use()
        self.ctx.enable(moderngl.BLEND)
        self.ctx.blend_func = moderngl.ONE, moderngl.ONE_MINUS_SRC_ALPHA        # couche premultipliee
        self._set(sp, "u_pass", 1.0)
        self._set(sp, "u_logo_on", 1.0)
        self._set(sp, "u_logo_straight", 0.0)
        try:
            for ident, rect, a, scale, rot in visible:
                x, y, w, h = (float(v) for v in rect)
                self.doodle_tex[ident].use(1)
                # apparition / disparition animees : echelle autour du centre et rotation (voir core.doodles.pose)
                self._set(sp, "u_logo_rect", (x + w / 2, y + h / 2, w / 2 * scale, h / 2 * scale))
                self._set(sp, "u_logo_cs", (math.cos(rot), math.sin(rot)))
                self._set(sp, "u_logo_opacity", float(a) * presence)
                self.scene_vao.render(moderngl.TRIANGLE_STRIP)
        finally:
            self._set(sp, "u_logo_cs", (1.0, 0.0))                      # le logo, lui, reste droit
            self.ctx.disable(moderngl.BLEND)
        return True

    @staticmethod
    def _set(prog, name: str, value) -> None:
        if name in prog:   # un uniform inutilise est elimine par le compilateur
            prog[name].value = value

    def draw(self, target, size: tuple[int, int], state: dict, params: dict, t: float) -> None:
        width, height = size
        p = {**DEFAULT_PARAMS, **params}       # un dict partiel (tests) reste valide
        fm = _unit(p["fx_master"], 0.0, 2.0)   # master des effets : x l'intensite de tous les effets (copie : `params` n'est pas touche)
        if fm != 1.0:
            for key in ("master", "melt_depth", "cell_amount", "noise_amount", "holo_intensity", "holo_warp", "holo_dust"):
                p[key] = float(p[key]) * fm
        fx_on, fx_int, master = p["fx_on"], p["fx_int"], p["master"]
        # Masters du Mode VJ : reactivite = tout ce qui vient de l'audio est multiplie (0 = le visuel ne reagit plus au son) ;
        # tempo = les animations (halo, fonte, cellules, fond, noise) avancent plus ou moins vite.
        build = _unit(p["build"])                                      # montee en cours : plus de reactivite, strobe, teinte qui derive
        react_m = _unit(float(p["react_master"]) * (1.0 + build), 0.0, 3.0)
        tempo = effective_tempo(p)                                      # master, ralenti par le mode silence
        idle = _unit(p["idle"])
        if react_m != 1.0:
            state = {**state, **{k: float(state[k]) * react_m for k in ("bass", "mid", "high", "rms", "beat") if k in state}}
        bass, high, beat = state["bass"], state["high"], state["beat"]
        dt = 0.0 if self._last_t is None else min(max(t - self._last_t, 0.0), 0.1)
        self._last_t = t
        raw_dt = dt
        dt *= tempo
        # Respiration (overlay et fond en opposition de phase), cycle de teinte, phases de la composition : integres image par image.
        self._breath_t += dt / max(float(p["breath_period"]), 1.0)
        wave = 0.5 + 0.5 * math.sin(2.0 * math.pi * self._breath_t)
        ov = _unit(p["overlay_master"]) * (1.0 - idle) * (1.0 - _unit(p["breath_overlay"]) * wave)      # presence de l'overlay (0 = efface)
        bg_level = _unit(p["bg_master"]) * (1.0 - _unit(p["breath_bg"]) * (1.0 - wave))
        self._hue_phase = (self._hue_phase + dt * float(p["hue_cycle"])) % 360.0
        spec = _unit(p["hue_spectrum"])
        if spec > 0.0:                                                  # graves = chaud, aigus = froid, suivi lisse (0,5 s) pour ne pas clignoter
            self._hue_spec += (spectral_hue(bass, high) - self._hue_spec) * (1.0 - math.exp(-raw_dt / 0.5))
        hue_deg = (float(p["hue_master"]) + self._hue_phase + build * 120.0 + spec * self._hue_spec) % 360.0
        self._comp_t += dt
        self._comp_rot += dt * math.radians(float(p["comp_rot"]))
        # Noise: houle et coups au hasard (x force) qui s'ajoutent a l'audio reel pour les effets coches.
        self.noise.step(dt)
        n_amount = min(max(float(p["noise_amount"]), 0.0), 1.0)
        n_fx = p["noise_fx"]
        nz, nzb = self.noise.swell() * n_amount, self.noise.beat() * n_amount
        self.noise_out = (nz, nzb)
        eff = [float(fx_on[i]) * float(fx_int[i]) * master for i in range(5)]
        if int(n_fx[4]):                                   # reaction du logo (pulsation, tremblement, contour)
            beat_l, high_l, bass_l = max(beat, nzb), max(high, nz), max(bass, nz)
        else:
            beat_l, high_l, bass_l = beat, high, bass

        self._ensure_scene(size)

        # Passe 1 : video + logo (pulse, jitter, contour lumineux). L'interrupteur/intensite
        # "logo" (effet 5) module les trois reactions a l'audio; position, taille et
        # opacite restent celles des reglages meme effet coupe.
        logo_fx = eff[4]
        pulse = p["logo_pulse"] * beat_l * logo_fx
        jit = p["logo_jitter"] * high_l * logo_fx
        jitter = (float(self.rng.uniform(-1, 1)) * jit, float(self.rng.uniform(-1, 1)) * jit)
        is_text = self.logo_kind == "text"
        rect = logo_layout(width, height, self.logo_aspect, p["text_scale"] if is_text else p["logo_scale"],
                           (p["logo_x"], p["logo_y"]), pulse, jitter, "height" if is_text else "box")
        sp = self.scene_prog
        self._set(sp, "u_video", 0)
        self._set(sp, "u_logo", 1)
        self._set(sp, "u_res", (float(width), float(height)))
        self._set(sp, "u_logo_rect", rect)
        self._set(sp, "u_logo_on", 1.0 if self.has_logo else 0.0)
        self._set(sp, "u_logo_straight", 1.0 if self.logo_video is not None else 0.0)
        if self.logo_video is not None:
            self.logo_video.reader.upload_to(self.logo_tex)
        self._set(sp, "u_logo_opacity", min(max(float(p["logo_opacity"]), 0.0), 1.0) * ov)
        self._set(sp, "u_bass", float(bass_l))
        self._set(sp, "u_glow", min(float(p["logo_glow"]) * logo_fx, 3.0) * ov)
        self._set(sp, "u_glow_radius", float(p["logo_glow_radius"]))
        self._set(sp, "u_glow_color", hex_to_rgb(p["logo_glow_color"]))
        self._set(sp, "u_beat", float(beat))

        # Fond: video ffmpeg ou motif genere. Les phases avancent meme quand le motif est cache,
        # pour qu'un retour au motif ne reparte pas d'un etat fige.
        # Halo holographique: phase integree (changer la vitesse ne fait pas sauter les reflets). Sans
        # holo_react (defaut), ni l'intensite ni la vitesse ne dependent de l'audio.
        h_react = max(float(p["holo_react"]), 0.0)
        self._holo_t += dt * float(p["holo_speed"]) * (1.0 + h_react * float(bass) * 3.0)
        holo_on = float(p["holo_on"]) >= 0.5 and self.has_logo
        # Fonte acide: phase integree (changer la periode ne fait pas sauter l'animation). Triangle 0 -> 1 -> 0 avec
        # un palier intact et un palier dissous, adouci: le logo reste lisible un moment, puis fond, puis se reforme.
        m_react = max(float(p["melt_react"]), 0.0)
        self._melt_t += dt / max(float(p["melt_period"]), 1.0) * (1.0 + m_react * float(bass) * 3.0)
        self._melt_clock += dt
        tri = 1.0 - abs(2.0 * (self._melt_t % 1.0) - 1.0)
        s_ = min(max((tri - 0.15) / 0.7, 0.0), 1.0)
        melt = s_ * s_ * (3.0 - 2.0 * s_) * min(max(float(p["melt_depth"]), 0.0), 1.0)
        if float(p["melt_on"]) < 0.5:
            melt = 0.0
        self._set(sp, "u_melt", melt)
        self._set(sp, "u_melt_scale", max(float(p["melt_scale"]), 0.1))
        self._set(sp, "u_melt_edge", max(float(p["melt_edge"]), 0.0))
        self._set(sp, "u_melt_color", hex_to_rgb(p["melt_color"], DEFAULT_PARAMS["melt_color"]))
        self._set(sp, "u_melt_t", self._melt_clock)
        # Cellules du logo: derive integree (changer la vitesse ne fait pas sauter les gouttes).
        c_react = max(float(p["cell_react"]), 0.0)
        self._cell_t += dt * float(p["cell_speed"]) * (1.0 + c_react * float(bass) * 3.0)
        cell_on = float(p["cell_on"]) >= 0.5
        self._set(sp, "u_cell", min(max(float(p["cell_amount"]), 0.0), 1.0) if cell_on else 0.0)
        self._set(sp, "u_cell_scale", max(float(p["cell_scale"]), 0.1))
        self._set(sp, "u_cell_fusion", max(float(p["cell_fusion"]), 0.05) * (1.0 + c_react * float(beat) * 0.6))
        self._set(sp, "u_cell_flat", min(max(float(p["cell_flat"]), 0.0), 1.0))
        self._set(sp, "u_cell_ink", hex_to_rgb(p["cell_ink"], DEFAULT_PARAMS["cell_ink"]))
        self._set(sp, "u_cell_t", self._cell_t)
        react = max(float(p["bg_react"]), 0.0)
        self._bg_scroll += dt * float(p["bg_speed"]) * (1.0 + react * float(bass) * 1.5)
        self._bg_flip_t += dt * float(p["bg_flip"])
        flips = self._bg_flip_t + (float(state.get("beats", 0)) if react > 0 and react_m > 0 else 0.0)
        self._set(sp, "u_bg_mode", 1.0 if p["bg_mode"] == "pattern" else 0.0)
        self._bg_clock += dt                       # horloge reelle: la palette "test" tourne a 30 images/s fixes
        self._set(sp, "u_bg_palette", {"duo": 1.0, "test": 2.0}.get(p["bg_palette"], 0.0))
        self._set(sp, "u_bg_frame", float(int(self._bg_clock * 30.0)))
        self._set(sp, "u_bg_c1", hex_to_rgb(p["bg_color1"], DEFAULT_PARAMS["bg_color1"]))
        self._set(sp, "u_bg_c2", hex_to_rgb(p["bg_color2"], DEFAULT_PARAMS["bg_color2"]))
        self._set(sp, "u_bg_phase", (self._bg_scroll, flips))
        self._set(sp, "u_bg_tile", float(p["bg_tile"]))
        self._set(sp, "u_bg_checker", float(p["bg_checker"]))
        self._set(sp, "u_bg_react", react)
        self._set(sp, "u_bg_hue", float(p["bg_hue"]))
        self._set(sp, "u_bg_angle", float(p["bg_angle"]))
        (self.mode_tex or self.video_tex).use(0)
        self.logo_tex.use(1)
        # Passe 1a: couche fond. 1b: couche logo seule (videe si pas de logo: le post-traitement
        # ne la lit alors pas, mais elle reste coherente).
        self._set(sp, "u_pass", 0.0)
        self.scene_fbo.viewport = (0, 0, width, height)
        self.scene_fbo.use()
        self.scene_vao.render(moderngl.TRIANGLE_STRIP)
        self.layer_fbo.viewport = (0, 0, width, height)
        self.layer_fbo.use()
        if self.has_logo:
            self._set(sp, "u_pass", 1.0)
            self.scene_vao.render(moderngl.TRIANGLE_STRIP)
        else:
            self.layer_fbo.clear(0.0, 0.0, 0.0, 0.0)
        # Les doodles du site passent par la MEME passe que le logo (fonte, cellules, contour, opacite), posee sur sa couche : ils
        # subissent ensuite ses effets par couche, le halo holographique, la trainee et la composition.
        doodles_on = self._draw_doodles(self.layer_fbo, size, time.monotonic(), ov)
        (self.mode_tex or self.video_tex).use(0)
        self.logo_tex.use(1)
        self._set(sp, "u_logo_rect", rect)
        self._set(sp, "u_logo_on", 1.0 if self.has_logo else 0.0)
        self._set(sp, "u_logo_straight", 1.0 if self.logo_video is not None else 0.0)
        holo_on = float(p["holo_on"]) >= 0.5 and (self.has_logo or doodles_on)
        if holo_on:
            # Le halo holographique lit l'alpha de cette couche, flou a de nombreuses echelles (mipmaps
            # a la taille de l'ecran: la portee ne depend ni du PNG ni de la marge de sa texture); le
            # champ est calcule a basse resolution (holo.frag) puis lu par le post-traitement.
            self.layer_tex.build_mipmaps()
            self.layer_tex.filter = (moderngl.LINEAR_MIPMAP_LINEAR, moderngl.LINEAR)
            hp = self.holo_prog
            self._set(hp, "u_logo", 0)
            self._set(hp, "u_res", (float(width), float(height)))
            self._set(hp, "u_holo_reach", max(float(p["holo_reach"]), 0.05))
            self._set(hp, "u_holo_lod0", math.log2(max(height, 1) / 720.0))
            self._set(hp, "u_holo_center", (float(rect[0]), float(1.0 - rect[1])))
            self.layer_tex.use(0)
            self.holo_fbo.viewport = (0, 0) + tuple(self.holo_tex.size)
            self.holo_fbo.use()
            self.holo_vao.render(moderngl.TRIANGLE_STRIP)
        if not holo_on:
            # Sans mipmaps construites, le filtre a mipmaps rendrait la texture incomplete (noire).
            self.layer_tex.filter = (moderngl.LINEAR, moderngl.LINEAR)

        # Passe 2 : post-traitement de chaque couche avec ses effets, logo par-dessus, vers la cible.
        fx_bg, fx_logo = layer_effects(p)
        linked = float(p["fx_link"]) >= 0.5
        # Decorreles, les ondes du logo partent de son centre (origine GL = bas gauche) et son glitch
        # tire d'autres bandes; lies, tout est identique au fond (comme un seul post-traitement).
        center = (0.5, 0.5) if linked else (rect[0], 1.0 - rect[1])
        pp = self.post_prog
        self._set(pp, "u_scene", 0)
        self._set(pp, "u_logo", 1)
        self._set(pp, "u_logo_on", 1.0 if self.has_logo else 0.0)
        self._set(pp, "u_layer_on", 1.0 if (self.has_logo or doodles_on) else 0.0)
        self._set(pp, "u_fx_bg", tuple(fx_bg))
        self._set(pp, "u_fx_logo", tuple(fx_logo))
        self._set(pp, "u_center_logo", (float(center[0]), float(center[1])))
        self._set(pp, "u_salt_logo", 0.0 if linked else 13.0)
        self._set(pp, "u_res", (float(width), float(height)))
        self._set(pp, "u_time", float(t))
        for name in ("bass", "mid", "high", "rms"):
            self._set(pp, f"u_{name}", float(state[name]))
        self._set(pp, "u_beat", float(beat))
        flash = max(0.0, 1.0 - (time.monotonic() - self.flash_t) / 0.55)
        self._set(pp, "u_flash", flash * flash)
        self._set(pp, "u_strobe", self._strobe(p, state))
        self._set(pp, "u_strobe_dark", float(p.get("strobe_dark", 1.0)))
        self._set(pp, "u_hue", math.radians(hue_deg))
        self._set(pp, "u_bg_level", bg_level)
        # Composition : symetrie / kaleidoscope, pixelisation, deformation globale (UV) et trainee (image precedente)
        self._set(pp, "u_csym", float(int(round(float(p["comp_sym"])))))
        self._set(pp, "u_cseg", float(max(2, min(16, int(round(float(p["comp_seg"])))))))
        self._set(pp, "u_crot", self._comp_rot)
        self._set(pp, "u_cpix", _unit(p["comp_pix"]))
        self._set(pp, "u_cwarp", _unit(p["comp_warp"]))
        self._set(pp, "u_ctime", self._comp_t)
        trail = _unit(p["comp_trail"], 0.0, 0.97)
        self._set(pp, "u_trail", trail)
        self._set(pp, "u_prev", 3)
        self._set(pp, "u_nz", float(nz))
        self._set(pp, "u_nz_beat", float(nzb))
        self._set(pp, "u_nz_since", float(self.noise.since))
        self._set(pp, "u_nz_amount", float(n_amount))
        self._set(pp, "u_nz_fx", tuple(float(int(n_fx[i])) for i in range(4)))
        self._set(pp, "u_since_beat", float(state["since_beat"]))
        self._set(pp, "u_holo", (1.0 + h_react * float(beat) * 1.5) * ov if holo_on else 0.0)
        self._set(pp, "u_holo_light", float(p["holo_intensity"]) * ov)
        self._set(pp, "u_holo_reach", max(float(p["holo_reach"]), 0.05))
        self._set(pp, "u_holo_warp", float(p["holo_warp"]))
        self._set(pp, "u_holo_dust", float(p["holo_dust"]))
        self._set(pp, "u_holo_phase", self._holo_t)
        self._set(pp, "u_holo_tex", 2)
        self._set(pp, "u_holo_center", (float(rect[0]), float(1.0 - rect[1])))    # toujours le centre du logo
        self._set(pp, "u_hud", 1.0 if self.hud else 0.0)
        self._set(pp, "u_fx_state", tuple(float(v) for v in fx_on))
        self.scene_tex.use(0)
        self.layer_tex.use(1)
        self.holo_tex.use(2)
        dest = target
        if trail > 0.001:
            # Trainee : on ecrit l'image dans l'une de deux textures hors ecran (l'ecran est multi-echantillonne : on ne peut pas le lire
            # en texture), on lit l'autre (l'image d'avant), puis on recopie l'image finale sur la cible et on echange.
            read_i = 1 - self._fb_i
            if not self._prev_ok:
                self.fb_fbo[read_i].clear(0.0, 0.0, 0.0, 1.0)            # la trainee repart d'une image noire
                self._prev_ok = True
            self.fb_tex[read_i].use(3)
            dest = self.fb_fbo[self._fb_i]
        else:
            self._prev_ok = False
            self.fb_tex[0].use(3)
        dest.viewport = (0, 0, width, height)
        dest.use()
        self.post_vao.render(moderngl.TRIANGLE_STRIP)
        if dest is not target:
            self.ctx.copy_framebuffer(target, dest)
            self._fb_i = 1 - self._fb_i


def hex_to_rgb(text: str, fallback: str = DEFAULT_PARAMS["logo_glow_color"]) -> tuple[float, float, float]:
    """'#rrggbb' -> (r, g, b) en 0..1 ; valeur invalide -> couleur de repli."""
    for candidate in (text, fallback):
        c = candidate.strip().lstrip("#")
        if len(c) == 6:
            try:
                return tuple(int(c[i:i + 2], 16) / 255.0 for i in (0, 2, 4))
            except ValueError:
                pass
    return (0.37, 0.83, 0.78)


class PerfGuard:
    """Garde-fou de charge : previent quand le GPU ne tient plus l'image. Le budget est une image a 60 Hz au plus (un
    projecteur a 59 Hz ne demande pas plus, un ecran a 144 Hz ne rend pas le budget impossible). Moyenne lissee des temps
    GPU mesures ; alerte apres `HOLD_S` secondes au-dessus de `HIGH` x budget, levee sous `LOW` x budget (hysterese :
    pas de message qui clignote). `update()` renvoie le texte a afficher, ou None quand tout va bien. Pure (testable)."""

    HIGH, LOW, HOLD_S, ALPHA = 0.90, 0.80, 2.0, 0.2
    # (cle de reglage, libelle, touche), du plus cher au moins cher (mesure : cellules > halo > fonte)
    COSTLY = (("cell_on", "cellules (V)"), ("holo_on", "halo (C)"), ("melt_on", "fonte (M)"))

    def __init__(self, refresh_hz: float) -> None:
        self.budget_ms = 1000.0 / max(min(float(refresh_hz or 60.0), 60.0), 20.0)
        self.avg = 0.0
        self.over_since: float | None = None
        self.active = False

    def update(self, gpu_ms: float, now: float, params: dict) -> str | None:
        self.avg = gpu_ms if self.avg == 0.0 else self.avg + self.ALPHA * (gpu_ms - self.avg)
        if self.avg > self.HIGH * self.budget_ms:
            if self.over_since is None:
                self.over_since = now
            if now - self.over_since >= self.HOLD_S:
                self.active = True
        else:
            self.over_since = None
            if self.avg < self.LOW * self.budget_ms:
                self.active = False
        if not self.active:
            return None
        on = [label for key, label in self.COSTLY if float(params.get(key, 0.0)) >= 0.5]
        advice = ("coupe " + " ou ".join(on)) if on else "baisse la taille du rendu (--render-size)"
        return f"GPU trop charge: {self.avg:.0f} ms par image pour {self.budget_ms:.0f} ms disponibles - {advice}"
