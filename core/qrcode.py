"""Generateur de QR code (mode octets, correction d'erreur M, versions 1 a 10) en Python pur : pas de dependance.

Sert a afficher l'adresse de la telecommande (`http://192.168.x.x:8765/#k=<jeton>`) dans la fenetre de reglages : le telephone la scanne et se connecte
sans rien taper. 74 octets tiennent dans la version 5 (37 x 37 modules) ; la version 10 en accepte 213.

`encode(texte)` rend la matrice (liste de lignes de booleens, vrai = module noir, SANS zone de silence : le dessin ajoute 4 modules blancs autour).
Verifie contre un decodeur de reference (OpenCV) a la mise au point, et par un decodeur de relecture dans `check_gl.py` (`check_qrcode`).
"""
from __future__ import annotations

# version -> (octets de donnees par bloc : liste de (nombre de blocs, octets), octets de correction par bloc), niveau M
BLOCKS_M = {
    1: ([(1, 16)], 10), 2: ([(1, 28)], 16), 3: ([(1, 44)], 26), 4: ([(2, 32)], 18), 5: ([(2, 43)], 24),
    6: ([(4, 27)], 16), 7: ([(4, 31)], 18), 8: ([(2, 38), (2, 39)], 22), 9: ([(3, 36), (2, 37)], 22), 10: ([(4, 43), (1, 44)], 26),
}
MAX_VERSION = 10
EC_FORMAT_BITS_M = 0           # M = 00
MODE_BYTES = 0b0100


def capacity(version: int) -> int:
    """Octets de texte que la version peut contenir (mode octets, niveau M)."""
    blocks, _ = BLOCKS_M[version]
    total = sum(n * size for n, size in blocks)
    head = 4 + (8 if version <= 9 else 16)
    return (total * 8 - head) // 8


def _mul(x: int, y: int) -> int:
    z = 0
    for i in reversed(range(8)):
        z = (z << 1) ^ ((z >> 7) * 0x11D)
        z ^= ((y >> i) & 1) * x
    return z


def _generator(degree: int) -> list[int]:
    result = [0] * (degree - 1) + [1]
    root = 1
    for _ in range(degree):
        for j in range(degree):
            result[j] = _mul(result[j], root)
            if j + 1 < degree:
                result[j] ^= result[j + 1]
        root = _mul(root, 2)
    return result


def _remainder(data: list[int], divisor: list[int]) -> list[int]:
    result = [0] * len(divisor)
    for b in data:
        factor = b ^ result.pop(0)
        result.append(0)
        for i, coef in enumerate(divisor):
            result[i] ^= _mul(coef, factor)
    return result


def _data_codewords(payload: bytes, version: int) -> list[int]:
    capacity_bits = sum(n * size for n, size in BLOCKS_M[version][0]) * 8
    bits: list[int] = []

    def put(value: int, count: int) -> None:
        bits.extend((value >> i) & 1 for i in reversed(range(count)))
    put(MODE_BYTES, 4)
    put(len(payload), 8 if version <= 9 else 16)
    for b in payload:
        put(b, 8)
    put(0, min(4, capacity_bits - len(bits)))                       # terminateur
    put(0, -len(bits) % 8)
    out = [int("".join(map(str, bits[i:i + 8])), 2) for i in range(0, len(bits), 8)]
    pad = 0xEC
    while len(out) * 8 < capacity_bits:
        out.append(pad)
        pad ^= 0xEC ^ 0x11
    return out


def _interleave(data: list[int], version: int) -> list[int]:
    layout, ec_len = BLOCKS_M[version]
    sizes = [size for n, size in layout for _ in range(n)]
    divisor = _generator(ec_len)
    blocks, ecs, pos = [], [], 0
    for size in sizes:
        chunk = data[pos:pos + size]
        pos += size
        blocks.append(chunk)
        ecs.append(_remainder(chunk, divisor))
    out = []
    for i in range(max(sizes)):
        for blk in blocks:
            if i < len(blk):
                out.append(blk[i])
    for i in range(ec_len):
        for ec in ecs:
            out.append(ec[i])
    return out


def _alignment_positions(version: int) -> list[int]:
    if version == 1:
        return []
    num = version // 7 + 2
    step = (version * 4 + num * 2 + 1) // (num * 2 - 2) * 2
    size = version * 4 + 17
    result = [6]
    pos = size - 7
    while len(result) < num:
        result.insert(1, pos)
        pos -= step
    return result


def _format_bits(mask: int) -> int:
    data = EC_FORMAT_BITS_M << 3 | mask
    rem = data
    for _ in range(10):
        rem = (rem << 1) ^ ((rem >> 9) * 0x537)
    return (data << 10 | rem) ^ 0x5412


def _version_bits(version: int) -> int:
    rem = version
    for _ in range(12):
        rem = (rem << 1) ^ ((rem >> 11) * 0x1F25)
    return version << 12 | rem


class _Grid:
    def __init__(self, version: int) -> None:
        self.version = version
        self.size = version * 4 + 17
        self.mod = [[False] * self.size for _ in range(self.size)]
        self.func = [[False] * self.size for _ in range(self.size)]

    def set(self, x: int, y: int, dark: bool) -> None:
        self.mod[y][x] = bool(dark)
        self.func[y][x] = True

    def draw_function_patterns(self) -> None:
        size = self.size
        for i in range(size):
            self.set(6, i, i % 2 == 0)
            self.set(i, 6, i % 2 == 0)
        for cx, cy in ((3, 3), (size - 4, 3), (3, size - 4)):
            for dy in range(-4, 5):
                for dx in range(-4, 5):
                    x, y = cx + dx, cy + dy
                    if 0 <= x < size and 0 <= y < size:
                        self.set(x, y, max(abs(dx), abs(dy)) not in (2, 4))
        pos = _alignment_positions(self.version)
        last = len(pos) - 1
        for i, cx in enumerate(pos):
            for j, cy in enumerate(pos):
                if (i == 0 and j == 0) or (i == 0 and j == last) or (i == last and j == 0):
                    continue
                for dy in range(-2, 3):
                    for dx in range(-2, 3):
                        self.set(cx + dx, cy + dy, max(abs(dx), abs(dy)) != 1)
        self.draw_format(0)                                              # reserve les zones (ecrites pour de bon apres le masque)
        if self.version >= 7:
            bits = _version_bits(self.version)
            for i in range(18):
                bit = (bits >> i) & 1
                a, b = size - 11 + i % 3, i // 3
                self.set(a, b, bit)
                self.set(b, a, bit)

    def draw_format(self, mask: int) -> None:
        bits = _format_bits(mask)
        size = self.size

        def b(i: int) -> bool:
            return bool((bits >> i) & 1)
        for i in range(0, 6):
            self.set(8, i, b(i))
        self.set(8, 7, b(6))
        self.set(8, 8, b(7))
        self.set(7, 8, b(8))
        for i in range(9, 15):
            self.set(14 - i, 8, b(i))
        for i in range(0, 8):
            self.set(size - 1 - i, 8, b(i))
        for i in range(8, 15):
            self.set(8, size - 15 + i, b(i))
        self.set(8, size - 8, True)                                      # module sombre fixe

    def draw_codewords(self, data: list[int]) -> None:
        size, i = self.size, 0
        right = size - 1
        while right >= 1:                                                # deux colonnes a la fois, de droite a gauche, en zigzag
            if right == 6:
                right = 5                                                # la colonne du timing (6) est sautee
            for vert in range(size):
                for j in range(2):
                    x = right - j
                    upward = ((right + 1) & 2) == 0
                    y = size - 1 - vert if upward else vert
                    if not self.func[y][x] and i < len(data) * 8:
                        self.mod[y][x] = bool((data[i >> 3] >> (7 - (i & 7))) & 1)
                        i += 1
            right -= 2

    def apply_mask(self, mask: int) -> None:
        for y in range(self.size):
            for x in range(self.size):
                if self.func[y][x]:
                    continue
                if mask == 0:
                    inv = (x + y) % 2 == 0
                elif mask == 1:
                    inv = y % 2 == 0
                elif mask == 2:
                    inv = x % 3 == 0
                elif mask == 3:
                    inv = (x + y) % 3 == 0
                elif mask == 4:
                    inv = (x // 3 + y // 2) % 2 == 0
                elif mask == 5:
                    inv = x * y % 2 + x * y % 3 == 0
                elif mask == 6:
                    inv = (x * y % 2 + x * y % 3) % 2 == 0
                else:
                    inv = ((x + y) % 2 + x * y % 3) % 2 == 0
                self.mod[y][x] ^= inv

    def penalty(self) -> int:
        size, mod = self.size, self.mod
        score = 0
        lines = [row for row in mod] + [[mod[y][x] for y in range(size)] for x in range(size)]
        for line in lines:                                               # N1 : suites de 5 modules ou plus ; N3 : motifs de repere
            run, prev = 0, None
            for v in line:
                if v == prev:
                    run += 1
                else:
                    if run >= 5:
                        score += 3 + run - 5
                    run, prev = 1, v
            if run >= 5:
                score += 3 + run - 5
            text = "".join("1" if v else "0" for v in line)
            for pattern in ("10111010000", "00001011101"):
                start = 0
                while True:
                    k = text.find(pattern, start)
                    if k < 0:
                        break
                    score += 40
                    start = k + 1
        for y in range(size - 1):                                        # N2 : blocs 2 x 2
            for x in range(size - 1):
                if mod[y][x] == mod[y][x + 1] == mod[y + 1][x] == mod[y + 1][x + 1]:
                    score += 3
        dark = sum(sum(row) for row in mod)
        total = size * size
        score += ((abs(dark * 20 - total * 10) + total - 1) // total - 1) * 10      # N4 : equilibre noir / blanc
        return score


def encode(text: str, mask: int | None = None) -> list[list[bool]]:
    """Matrice du QR code de `text` (UTF-8). `mask` impose un masque (0 a 7, pour les tests) ; sinon le meilleur. ValueError si trop long (213 octets)."""
    payload = text.encode("utf-8")
    version = next((v for v in range(1, MAX_VERSION + 1) if len(payload) <= capacity(v)), None)
    if version is None:
        raise ValueError("texte trop long pour un QR code (213 octets au plus)")
    codewords = _interleave(_data_codewords(payload, version), version)
    best, best_score = None, None
    for m in ([mask] if mask is not None else range(8)):
        g = _Grid(version)
        g.draw_function_patterns()
        g.draw_codewords(codewords)
        g.apply_mask(m)
        g.draw_format(m)
        score = g.penalty()
        if best is None or score < best_score:
            best, best_score = g, score
    return best.mod


def to_text(matrix: list[list[bool]], quiet: int = 4) -> str:
    """Dessin en caracteres (deux caracteres par module) : pratique pour verifier a l'oeil ou dans un terminal."""
    n = len(matrix)
    rows = []
    for y in range(-quiet, n + quiet):
        rows.append("".join(("##" if 0 <= x < n and 0 <= y < n and matrix[y][x] else "  ") for x in range(-quiet, n + quiet)))
    return "\n".join(rows)
