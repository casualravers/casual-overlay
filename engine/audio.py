"""Analyse audio : extraction des bandes et du kick, tampon circulaire, entrees micro et audio synthetique.

Extrait tel quel de `audio2wave_gl` (qui re-exporte ces noms). Les dependances facultatives viennent de `engine.deps`.
"""
from __future__ import annotations

import math
import sys
import threading
import time

from engine.deps import np, sd


# --- Analyse audio -----------------------------------------------------------------

BANDS = {"bass": (20.0, 150.0), "mid": (150.0, 2000.0), "high": (2000.0, 10000.0)}
# Seuils absolus (amplitude de bin FFT, 1.0 = sinus pleine echelle) sous lesquels une
# bande est consideree comme silence : sans ca l'auto-gain ampliferait le bruit du micro.
BAND_GATE = {"bass": 0.0005, "mid": 0.0005, "high": 0.0005, "rms": 0.003}
PEAK_HALF_LIFE_S = 8.0       # decroissance du maximum glissant de l'auto-gain
ATTACK_TAU_S = 0.02          # lissage: attaque rapide
RELEASE_TAU_S = 0.25         # lissage: relachement lent
KICK_MIN = 0.002             # energie basses minimale pour declarer un kick
# Regles par balayage sur signaux synthetiques (voir CLAUDE.md): a sensibilite 1.0, zero
# faux positif sur bruit blanc constant, kicks francs detectes meme a -30 dB de micro.
KICK_RATIO_EXTRA = 1.2       # seuil: energie basses > base * (1 + extra / sensibilite)
KICK_K = 3.0                 # ... ET ecart a la base > k / sensibilite ecarts-types
KICK_REFRACTORY_S = 0.15
KICK_WARMUP_S = 0.5
BASELINE_RISE_TAU_S = 0.8    # ligne de base glissante (symetrique: une base qui redescend
BASELINE_FALL_TAU_S = 0.8    # plus vite qu'elle ne monte donne des faux positifs sur bruit)
DEV_TAU_S = 1.0              # fenetre de l'ecart-type de l'energie basses
BEAT_DECAY_TAU_S = 0.20      # decroissance de l'enveloppe u_beat
BEAT_IDLE_S = 10.0           # valeur de since_beat tant qu'aucun kick n'est vu


def _coef(dt: float, tau: float) -> float:
    return 1.0 - math.exp(-dt / tau)


class FeatureExtractor:
    """Fonctions pures d'analyse : fenetre de samples -> niveaux de bandes + kick.

    Aucun acces au temps reel ni aux fils : `process()` recoit `dt` (duree couverte par
    les nouveaux samples) et `now` (horloge, secondes), ce qui permet de le tester sur
    des signaux synthetiques (voir check_gl.py).
    """

    def __init__(self, rate: int, win_size: int = 2048, sensitivity: float = 1.0):
        self.rate = rate
        self.win_size = win_size
        self.sensitivity = sensitivity
        self.window = np.hanning(win_size).astype(np.float32)
        self.scale = 2.0 / float(self.window.sum())   # un sinus d'amplitude A donne un bin ~A
        freqs = np.fft.rfftfreq(win_size, 1.0 / rate)
        self.slices: dict[str, slice] = {}
        for name, (lo, hi) in BANDS.items():
            idx = np.nonzero((freqs >= lo) & (freqs < min(hi, rate / 2.0)))[0]
            self.slices[name] = slice(int(idx[0]), int(idx[-1]) + 1)
        self.peak = {k: 0.0 for k in (*BANDS, "rms")}
        self.smooth = {k: 0.0 for k in (*BANDS, "rms")}
        self.baseline: float | None = None
        self.dev2 = 0.0
        self.prev_bass = 0.0
        self.last_beat = -1e9
        self.elapsed = 0.0

    def process(self, samples, dt: float, now: float) -> tuple[dict, bool]:
        """Retourne (niveaux normalises lisses, kick_detecte)."""
        samples = np.asarray(samples, dtype=np.float32)
        mag = np.abs(np.fft.rfft(samples * self.window)) * self.scale
        raw = {name: float(math.sqrt(float(np.mean(mag[sl] ** 2)))) for name, sl in self.slices.items()}
        tail = samples[-512:]
        raw["rms"] = float(math.sqrt(float(np.mean(tail * tail))))

        levels = {}
        for name, e in raw.items():
            gate = BAND_GATE[name]
            self.peak[name] = max(e, self.peak[name] * 0.5 ** (dt / PEAK_HALF_LIFE_S))
            top = max(self.peak[name], gate * 6.0)
            n = min(max((e - gate) / (top - gate), 0.0), 1.0)
            tau = ATTACK_TAU_S if n > self.smooth[name] else RELEASE_TAU_S
            self.smooth[name] += (n - self.smooth[name]) * _coef(dt, tau)
            levels[name] = self.smooth[name]

        beat = self._detect_kick(raw["bass"], dt, now)
        levels["raw_bass"] = raw["bass"]
        return levels, beat

    def _detect_kick(self, b: float, dt: float, now: float) -> bool:
        """Kick = energie basses nettement au-dessus d'une ligne de base glissante :
        b - base > k * ecart-type ET b > base * ratio (seuil adaptatif, donc insensible
        au niveau du micro), avec periode refractaire. `sensitivity` > 1 abaisse les seuils.
        """
        self.elapsed += dt
        s = max(self.sensitivity, 0.05)
        ratio = 1.0 + KICK_RATIO_EXTRA / s
        k = KICK_K / s
        if self.baseline is None:
            self.baseline = b
        base = self.baseline
        dev = b - base
        std = math.sqrt(self.dev2)

        beat = (self.elapsed > KICK_WARMUP_S
                and now - self.last_beat > KICK_REFRACTORY_S
                and b > KICK_MIN
                and b > self.prev_bass
                and b > base * ratio
                and dev > k * std)
        if beat:
            self.last_beat = now

        # Statistiques mises a jour hors des kicks: l'ecart-type decrit le "bruit" ambiant.
        if now - self.last_beat > 0.3:
            self.dev2 += (dev * dev - self.dev2) * _coef(dt, DEV_TAU_S)
        tau = BASELINE_RISE_TAU_S if b > base else BASELINE_FALL_TAU_S
        self.baseline = base + (b - base) * _coef(dt, tau)
        self.prev_bass = b
        return beat


class RingBuffer:
    """Tampon circulaire mono float32, ecriture par le callback audio, lecture par l'analyse."""

    def __init__(self, size: int = 1 << 16):
        self.size = size
        self.buf = np.zeros(size, dtype=np.float32)
        self.pos = 0
        self.total = 0
        self.lock = threading.Lock()
        self.event = threading.Event()

    def write(self, x) -> None:
        x = np.asarray(x, dtype=np.float32)
        n = len(x)
        if n >= self.size:
            x = x[-self.size:]
            n = self.size
        with self.lock:
            end = self.pos + n
            if end <= self.size:
                self.buf[self.pos:end] = x
            else:
                k = self.size - self.pos
                self.buf[self.pos:] = x[:k]
                self.buf[:n - k] = x[k:]
            self.pos = end % self.size
            self.total += n
        self.event.set()

    def latest(self, n: int):
        out = np.zeros(n, dtype=np.float32)
        with self.lock:
            valid = min(n, self.total, self.size)
            if valid:
                idx = (self.pos - valid + np.arange(valid)) % self.size
                out[n - valid:] = self.buf[idx]
        return out


def synth_audio_block(start: int, n: int, rate: int, bpm: float = 120.0, rng=None):
    """Kick 60 Hz a `bpm` + tic aigu a contretemps + leger bruit de fond."""
    rng = rng or np.random.default_rng(0)
    t = (start + np.arange(n)) / rate
    period = 60.0 / bpm
    ph = t % period
    kick = 0.6 * np.sin(2 * np.pi * 60.0 * ph) * np.exp(-ph / 0.07)
    hp = ph - period / 2
    hat = np.where(hp > 0, rng.standard_normal(n) * 0.12 * np.exp(-np.clip(hp, 0, None) / 0.02), 0.0)
    return (kick + hat + rng.standard_normal(n) * 0.002).astype(np.float32)


class SyntheticAudio:
    """Source audio de test : ecrit des blocs synthetiques dans le ring, au rythme reel."""

    def __init__(self, ring: RingBuffer, rate: int = 48000, block: int = 512, bpm: float = 120.0):
        self.ring, self.rate, self.block, self.bpm = ring, rate, block, bpm
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._run, daemon=True)

    def start(self):
        self._thread.start()

    def stop(self):
        self._stop.set()

    def _run(self):
        rng = np.random.default_rng(1)
        idx = 0
        t_next = time.monotonic()
        while not self._stop.is_set():
            self.ring.write(synth_audio_block(idx, self.block, self.rate, self.bpm, rng))
            idx += self.block
            t_next += self.block / self.rate
            delay = t_next - time.monotonic()
            if delay > 0:
                time.sleep(delay)


def find_input_device(query: str | None, dshow_name: str | None = None):
    """Index sounddevice d'une entree, ou None pour le defaut. Correspondance par
    sous-chaine (insensible a la casse) ; WASAPI prefere. Un numero est pris tel quel."""
    devices = sd.query_devices()
    hostapis = sd.query_hostapis()
    inputs = [(i, d) for i, d in enumerate(devices) if d["max_input_channels"] > 0]

    def rank(item):
        return 0 if "WASAPI" in hostapis[item[1]["hostapi"]]["name"] else 1

    def match(name: str):
        q = name.lower()
        found = sorted((c for c in inputs if q in c[1]["name"].lower()), key=rank)
        return found[0][0] if found else None

    if query:
        if query.isdigit():
            return int(query)
        idx = match(query)
        if idx is None:
            print(f"Aucune entree sounddevice ne contient '{query}'. Disponibles:", file=sys.stderr)
            print_audio_devices(file=sys.stderr)
            sys.exit(2)
        return idx
    if dshow_name:
        idx = match(dshow_name)
        if idx is not None:
            return idx
        print(f"Note: '{dshow_name}' introuvable cote sounddevice, entree par defaut "
              "utilisee (voir --audio-device).", file=sys.stderr)
    for api in hostapis:
        if "WASAPI" in api["name"] and api["default_input_device"] >= 0:
            return api["default_input_device"]
    return None


# Entrees DirectShow qui ne sont pas un micro (boucle de sortie, cables virtuels...) : jamais choisies d'office.
NOT_A_MIC = ("stereo mix", "mixage", "loopback", "what u hear", "virtual", "cable", "voicemeeter", "vb-audio", "obs",
             "nvidia broadcast", "steam streaming")
MIC_WORDS = ("microphone", "micro", "mic", "array", "headset", "casque")


def pick_default_device(dshow_names: list[str], system_default: str | None = None) -> str | None:
    """Entree DirectShow a utiliser quand l'utilisateur n'en a pas choisi : le micro du PC. Ordre : celle qui correspond
    a l'entree par defaut de Windows (noms tronques a 31 caracteres cote DirectShow), puis un nom de micro evident, puis
    la premiere qui n'est pas une boucle ou un cable virtuel, puis la premiere. Pure (testable sans materiel)."""
    if not dshow_names:
        return None
    real = [n for n in dshow_names if not any(w in n.lower() for w in NOT_A_MIC)] or list(dshow_names)
    if system_default:
        sysname = system_default.lower().strip()
        for n in real:
            low = n.lower().strip()
            if low == sysname or sysname.startswith(low) or low.startswith(sysname):
                return n
    for n in real:
        if any(w in n.lower() for w in MIC_WORDS):
            return n
    return real[0]


def system_default_input_name() -> str | None:
    """Nom de l'entree audio par defaut de Windows (sounddevice), ou None."""
    try:
        index = sd.default.device[0]
        if index is None or index < 0:
            index = sd.query_hostapis(sd.default.hostapi)["default_input_device"]
        return str(sd.query_devices(index)["name"]) if index is not None and index >= 0 else None
    except Exception:
        return None


def print_audio_devices(file=sys.stdout) -> None:
    hostapis = sd.query_hostapis()
    print("Entrees sounddevice (utilise --audio-device <sous-chaine ou numero>):", file=file)
    for i, d in enumerate(sd.query_devices()):
        if d["max_input_channels"] > 0:
            api = hostapis[d["hostapi"]]["name"]
            print(f"  {i:3d}  [{api}] {d['name']}  ({d['default_samplerate']:.0f} Hz)", file=file)


class SoundDeviceInput:
    """Capture sounddevice en callback (mono, blocs courts) vers un RingBuffer."""

    def __init__(self, device, ring: RingBuffer, blocksize: int = 512):
        info = sd.query_devices(device, "input")
        self.rate = int(info["default_samplerate"])
        self.name = info["name"]
        self.channels = max(1, min(2, int(info["max_input_channels"])))
        self.ring = ring
        self.stream = sd.InputStream(
            device=device, channels=self.channels, samplerate=self.rate, blocksize=blocksize,
            dtype="float32", latency="low", callback=self._callback)

    def _callback(self, indata, frames, time_info, status):
        if status:
            self.dropped = getattr(self, "dropped", 0) + 1
        self.ring.write(indata[:, 0] if self.channels == 1 else indata.mean(axis=1))

    def start(self):
        self.stream.start()

    def stop(self):
        self.stream.stop()
        self.stream.close()

    @property
    def latency_ms(self) -> float:
        return float(self.stream.latency) * 1000.0


class AudioAnalyzer:
    """Fil d'analyse : ring -> FeatureExtractor -> dernier etat lu par le fil GL."""

    def __init__(self, ring: RingBuffer, extractor: FeatureExtractor):
        self.ring = ring
        self.extractor = extractor
        self.lock = threading.Lock()
        self.levels = {"bass": 0.0, "mid": 0.0, "high": 0.0, "rms": 0.0, "raw_bass": 0.0}
        self.t_beat: float | None = None
        self.beats = 0
        self._last_total = 0
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._run, daemon=True)

    def start(self):
        self._thread.start()

    def stop(self):
        self._stop.set()
        self.ring.event.set()

    def _run(self):
        while not self._stop.is_set():
            if not self.ring.event.wait(0.1):
                continue
            rate = self.extractor.rate    # relu: l'entree audio peut changer en cours de route
            self.ring.event.clear()
            total = self.ring.total
            new = total - self._last_total
            if new <= 0:
                continue
            self._last_total = total
            dt = min(new / rate, 0.1)
            window = self.ring.latest(self.extractor.win_size)
            now = time.monotonic()
            levels, beat = self.extractor.process(window, dt, now)
            with self.lock:
                self.levels = levels
                if beat:
                    self.t_beat = now
                    self.beats += 1

    def latest(self) -> dict:
        with self.lock:
            state = dict(self.levels)
            t_beat = self.t_beat
            state["beats"] = self.beats
        since = BEAT_IDLE_S if t_beat is None else min(time.monotonic() - t_beat, BEAT_IDLE_S)
        state["since_beat"] = since
        state["beat"] = math.exp(-since / BEAT_DECAY_TAU_S) if t_beat is not None else 0.0
        return state
