#!/usr/bin/env python3
"""Génère les illustrations pixel art du README (docs/*.svg), sans dépendance.

Chaque image est dessinée pixel par pixel sur une grille, puis exportée en SVG agrandi
3 fois (un chemin par couleur, rendu net grâce à shape-rendering="crispEdges").
Les animations sont des calques posés sur l'image de base et affichés tour à tour par
des @keyframes CSS, que GitHub joue directement dans le README. Avec « réduire les
animations » activé, seule l'image de base reste affichée.

    python3 docs/make_pixel_art.py            # régénère docs/*.svg
    python3 docs/make_pixel_art.py --png DIR  # écrit aussi chaque étape en PNG (aperçu)
"""
import math
import os
import random
import struct
import sys
import zlib

SCALE = 3
HERE = os.path.dirname(os.path.abspath(__file__))

# ---------- couleurs (thème sombre de YouTube) ----------

BG = "#0f0f0f"
CARD = "#1a1a1a"
CHIP = "#272727"          # pilule YouTube : rgba(255,255,255,.1) sur le fond
CHIP_HOVER = "#3f3f3f"    # survol : rgba(255,255,255,.2)
CHIP_PRESSED = "#575757"
FILL = "#4e4e4e"          # progression dans la pilule : rgba(255,255,255,.18)
LINE = "#3f3f3f"
DIVIDER = "#525252"
DIM = "#717171"
MUTED = "#aaaaaa"
TEXT = "#f1f1f1"
WHITE = "#ffffff"
RED = "#ff0000"
RED_DARK = "#b30000"
RED_LIGHT = "#ff6b6b"
BLUE = "#3ea6ff"          # bouton « Terminé » en mode sombre
GREEN = "#4caf50"         # pastille « Prêt » du popup
TERM_GREEN = "#4ade80"

# ---------- police bitmap 5x7 (+2 lignes de jambage), largeur variable ----------

FONT = {
    "A": ".###. #...# #...# ##### #...# #...# #...#",
    "B": "####. #...# #...# ####. #...# #...# ####.",
    "C": ".###. #...# #.... #.... #.... #...# .###.",
    "D": "####. #...# #...# #...# #...# #...# ####.",
    "E": "##### #.... #.... ####. #.... #.... #####",
    "F": "##### #.... #.... ####. #.... #.... #....",
    "G": ".###. #...# #.... #.### #...# #...# .####",
    "H": "#...# #...# #...# ##### #...# #...# #...#",
    "I": "### .#. .#. .#. .#. .#. ###",
    "J": "..### ...#. ...#. ...#. ...#. #..#. .##..",
    "K": "#...# #..#. #.#.. ##... #.#.. #..#. #...#",
    "L": "#.... #.... #.... #.... #.... #.... #####",
    "M": "#...# ##.## #.#.# #.#.# #...# #...# #...#",
    "N": "#...# #...# ##..# #.#.# #..## #...# #...#",
    "O": ".###. #...# #...# #...# #...# #...# .###.",
    "P": "####. #...# #...# ####. #.... #.... #....",
    "Q": ".###. #...# #...# #...# #.#.# #..#. .##.#",
    "R": "####. #...# #...# ####. #.#.. #..#. #...#",
    "S": ".#### #.... #.... .###. ....# ....# ####.",
    "T": "##### ..#.. ..#.. ..#.. ..#.. ..#.. ..#..",
    "U": "#...# #...# #...# #...# #...# #...# .###.",
    "V": "#...# #...# #...# #...# #...# .#.#. ..#..",
    "W": "#...# #...# #...# #.#.# #.#.# #.#.# .#.#.",
    "X": "#...# #...# .#.#. ..#.. .#.#. #...# #...#",
    "Y": "#...# #...# .#.#. ..#.. ..#.. ..#.. ..#..",
    "Z": "##### ....# ...#. ..#.. .#... #.... #####",
    "a": "..... ..... .###. ....# .#### #...# .####",
    "b": "#.... #.... ####. #...# #...# #...# ####.",
    "c": ".... .... .### #... #... #... .###",
    "d": "....# ....# .#### #...# #...# #...# .####",
    "e": "..... ..... .###. #...# ##### #.... .###.",
    "f": "..## .#.. #### .#.. .#.. .#.. .#..",
    "g": "..... ..... .#### #...# #...# #...# .#### ....# .###.",
    "h": "#.... #.... ####. #...# #...# #...# #...#",
    "i": ".#. ... ##. .#. .#. .#. ###",
    "j": "..# ... .## ..# ..# ..# ..# #.# .#.",
    "k": "#... #... #..# #.#. ##.. #.#. #..#",
    "l": "##. .#. .#. .#. .#. .#. ###",
    "m": "..... ..... ##.#. #.#.# #.#.# #.#.# #.#.#",
    "n": "..... ..... ####. #...# #...# #...# #...#",
    "o": "..... ..... .###. #...# #...# #...# .###.",
    "p": "..... ..... ####. #...# #...# #...# ####. #.... #....",
    "q": "..... ..... .#### #...# #...# #...# .#### ....# ....#",
    "r": ".... .... #.## ##.. #... #... #...",
    "s": ".... .... .### #... .##. ...# ###.",
    "t": ".#.. .#.. #### .#.. .#.. .#.. ..##",
    "u": "..... ..... #...# #...# #...# #...# .####",
    "v": "..... ..... #...# #...# #...# .#.#. ..#..",
    "w": "..... ..... #...# #...# #.#.# #.#.# .#.#.",
    "x": "..... ..... #...# .#.#. ..#.. .#.#. #...#",
    "y": "..... ..... #...# #...# #...# #...# .#### ....# .###.",
    "z": "..... ..... ##### ...#. ..#.. .#... #####",
    "à": ".#... ..#.. .###. ....# .#### #...# .####",
    "é": "...#. ..#.. .###. #...# ##### #.... .###.",
    "è": ".#... ..#.. .###. #...# ##### #.... .###.",
    "ê": "..#.. .#.#. .###. #...# ##### #.... .###.",
    "ô": "..#.. .#.#. .###. #...# #...# #...# .###.",
    "ç": ".... .... .### #... #... #... .### ..#. .##.",
    "0": ".###. #...# #..## #.#.# ##..# #...# .###.",
    "1": "..#.. .##.. ..#.. ..#.. ..#.. ..#.. .###.",
    "2": ".###. #...# ....# ...#. ..#.. .#... #####",
    "3": "##### ...#. ..#.. ...#. ....# #...# .###.",
    "4": "...#. ..##. .#.#. #..#. ##### ...#. ...#.",
    "5": "##### #.... ####. ....# ....# #...# .###.",
    "6": "..##. .#... #.... ####. #...# #...# .###.",
    "7": "##### ....# ...#. ..#.. .#... .#... .#...",
    "8": ".###. #...# #...# .###. #...# #...# .###.",
    "9": ".###. #...# #...# .#### ....# ...#. .##..",
    " ": "... ... ... ... ... ... ...",
    ".": ". . . . . . #",
    ",": ".. .. .. .. .. .# .# #.",
    ":": ". . # . . # .",
    "!": "# # # # # . #",
    "?": ".###. #...# ....# ...#. ..#.. ..... ..#..",
    "%": "##..# ##.#. ...#. ..#.. .#... .#.## #..##",
    "-": ".... .... .... #### .... .... ....",
    "_": "..... ..... ..... ..... ..... ..... ..... #####",
    "/": "....# ...#. ...#. ..#.. .#... .#... #....",
    "~": "..... ..... .##.# #..#. ..... ..... .....",
    "[": "### #.. #.. #.. #.. #.. ###",
    "]": "### ..# ..# ..# ..# ..# ###",
    "(": "..# .#. #.. #.. #.. .#. ..#",
    ")": "#.. .#. ..# ..# ..# .#. #..",
    "'": "# # . . . . .",
    "=": ".... .... #### .... #### .... ....",
    "<": "...# ..#. .#.. #... .#.. ..#. ...#",
    ">": "#... .#.. ..#. ...# ..#. .#.. #...",
    "+": "..... ..#.. ..#.. ##### ..#.. ..#.. .....",
    "…": "..... ..... ..... ..... ..... ..... #.#.#",
    "·": ". . . # . . .",
}
GLYPHS = {ch: spec.split() for ch, spec in FONT.items()}


def text_width(s, scale=1):
    if not s:
        return 0
    return (sum(len(GLYPHS[ch][0]) for ch in s) + len(s) - 1) * scale


def fit(s, width):
    """Coupe le texte avec « … » pour qu'il tienne dans `width` pixels."""
    if text_width(s) <= width:
        return s
    while s and text_width(s + "…") > width:
        s = s[:-1]
    return s.rstrip() + "…"


# ---------- couleurs ----------

def rgb(color):
    return tuple(int(color[i:i + 2], 16) for i in (1, 3, 5))


def mix(a, b, t):
    ra, rb = rgb(a), rgb(b)
    return "#%02x%02x%02x" % tuple(round(x + (y - x) * t) for x, y in zip(ra, rb))


def rounded_rect_sdf(x, y, w, h, r):
    qx, qy = abs(x - w / 2) - (w / 2 - r), abs(y - h / 2) - (h / 2 - r)
    return math.hypot(max(qx, 0), max(qy, 0)) + min(max(qx, qy), 0) - r


# ---------- toile ----------

class Canvas:
    def __init__(self, w, h):
        self.w, self.h = w, h
        self.px = [[None] * w for _ in range(h)]

    def copy(self):
        c = Canvas(0, 0)
        c.w, c.h, c.px = self.w, self.h, [row[:] for row in self.px]
        return c

    def get(self, x, y):
        return self.px[y][x] if 0 <= x < self.w and 0 <= y < self.h else None

    def set(self, x, y, color):
        if 0 <= x < self.w and 0 <= y < self.h and color:
            self.px[y][x] = color

    def rect(self, x, y, w, h, color):
        for yy in range(y, y + h):
            for xx in range(x, x + w):
                self.set(xx, yy, color)

    def hline(self, x, y, w, color):
        self.rect(x, y, w, 1, color)

    def vline(self, x, y, h, color):
        self.rect(x, y, 1, h, color)

    def round_rect(self, x, y, w, h, r, color, x_from=None, x_to=None):
        for py in range(h):
            for px in range(w):
                if x_from is not None and x + px < x_from or x_to is not None and x + px >= x_to:
                    continue
                if r and rounded_rect_sdf(px + 0.5, py + 0.5, w, h, r) > 0:
                    continue
                self.set(x + px, y + py, color)

    def framed(self, x, y, w, h, r, border, fill):
        self.round_rect(x, y, w, h, r, border)
        self.round_rect(x + 1, y + 1, w - 2, h - 2, max(0, r - 1), fill)

    def circle(self, cx, cy, r, color):
        for yy in range(math.floor(cy - r), math.ceil(cy + r) + 1):
            for xx in range(math.floor(cx - r), math.ceil(cx + r) + 1):
                if (xx + 0.5 - cx) ** 2 + (yy + 0.5 - cy) ** 2 <= r * r:
                    self.set(xx, yy, color)

    def sprite(self, x, y, rows, palette):
        for dy, row in enumerate(rows):
            for dx, ch in enumerate(row):
                if ch in palette:
                    self.set(x + dx, y + dy, palette[ch])

    def text(self, x, y, s, color, scale=1):
        for ch in s:
            glyph = GLYPHS[ch]
            for gy, row in enumerate(glyph):
                for gx, bit in enumerate(row):
                    if bit == "#":
                        self.rect(x + gx * scale, y + gy * scale, scale, scale, color)
            x += (len(glyph[0]) + 1) * scale
        return text_width(s, scale)

    def ctext(self, cx, y, s, color):
        self.text(cx - text_width(s) // 2, y, s, color)

    def shade(self, x, y, w, h, factor):
        for yy in range(y, y + h):
            for xx in range(x, x + w):
                color = self.get(xx, yy)
                if color:
                    self.set(xx, yy, mix("#000000", color, factor))


# ---------- sprites ----------

ICON_DOWNLOAD = ["..###..", "..###..", "..###..", "#######", ".#####.", "..###..", "...#...", ".......", "#######"]
ICON_CHECK = ["........#", ".......##", "#.....##.", "##...##..", ".##.##...", "..###....", "...#....."]
ICON_LIKE = [
    "....#....",
    "...##....",
    "...##....",
    "##.######",
    "##.######",
    "##.#####.",
    "##.######",
    "##.#####.",
    "##..####.",
]
ICON_DISLIKE = ICON_LIKE[::-1]
ICON_SHARE = [
    "......#...",
    "......##..",
    "..#######.",
    ".#########",
    "##....##..",
    "#.....#...",
    "#.........",
    "#.........",
]
ICON_PAUSE = ["##.##"] * 7
ICON_NEXT = ["#...#", "##..#", "###.#", "#####", "###.#", "##..#", "#...#"]
ICON_VOLUME = ["...#....", "..##..#.", "####.#.#", "####.#.#", "####.#.#", "..##..#.", "...#...."]
ICON_PLAY = ["#....", "##...", "###..", "####.", "###..", "##...", "#...."]
ICON_PLAY_SMALL = ["#..", "##.", "###", "##.", "#.."]
ICON_SEARCH = [".##...", "#..#..", "#..#..", ".##...", "....#.", ".....#"]
ICON_CARET = ["#####", ".###.", "..#.."]
CURSOR = [
    "k.........",
    "kk........",
    "kwk.......",
    "kwwk......",
    "kwwwk.....",
    "kwwwwk....",
    "kwwwwwk...",
    "kwwwwwwk..",
    "kwwwwwwwk.",
    "kwwwwwkkkk",
    "kwwkwwk...",
    "kwk.kwwk..",
    "kk..kwwk..",
    ".....kwwk.",
    ".....kwwk.",
    "......kk..",
]
CURSOR_COLORS = {"k": "#000000", "w": WHITE}
SPINNER = ["..kkk..", ".kwwwk.", "kw.k.dk", "kwk.kdk", "kd...dk", ".kdddk.", "..kkk.."]
SPINNER_COLORS = {"k": "#000000", "w": WHITE, "d": "#8a8a8a"}
PYTHON = [
    "......bbbbbbbbb.......",
    ".....bbwwbbbbbbb......",
    ".....bbwwbbbbbbb......",
    ".....bbbbbbbbbbb......",
    "..........bbbbbb......",
    ".bbbbbbbbbbbbbbb.yyyy.",
    "bbbbbbbbbbbbbbbb.yyyyy",
    "bbbbbbbbbbbbbbbb.yyyyy",
    "bbbbbbbbbbbbbbbb.yyyyy",
    "bbbbbbbbbbbbbbb..yyyyy",
    "bbbbbb..........yyyyyy",
    "bbbbbb.........yyyyyyy",
    "bbbbbbb..........yyyyy",
    "bbbbb.yyyyyyyyyyyyyyyy",
    "bbbbb.yyyyyyyyyyyyyyyy",
    "bbbbb.yyyyyyyyyyyyyyyy",
    "bbbbb.yyyyyyyyyyyyyyy.",
    ".bbbb.yyyyyy..........",
    "......yyyyyyyyyyy.....",
    "......yyyyyyyywwy.....",
    "......yyyyyyyywwy.....",
    ".......yyyyyyyyy......",
]
PYTHON_COLORS = {"b": "#3776ab", "y": "#ffd43b", "w": WHITE}
MUG = [
    "...ww..w.ww.........",
    "..wwwwwwwwwww.......",
    ".wwwwwwwwwwwww......",
    ".wcwwwwwwwwwcw......",
    ".yyyyyyyyyyyyy......",
    ".yhyyyyyhyyyYy.ggg..",
    ".yhyyyyyyyyyYygg.gg.",
    ".yhyyhyyyyyyYyg...g.",
    ".yhyyyyyyyhyYyg...g.",
    ".yhyyyyyyyyyYyg...g.",
    ".yhyyyyhyyyyYyg...g.",
    ".yhyyyyyyyyyYygg.gg.",
    ".yyyyyyyyyyyYy.ggg..",
    ".yyyyyyyyyyyYy......",
    ".yyyyyyyyyyyYy......",
    "..YYYYYYYYYYY.......",
]
MUG_COLORS = {"w": "#f5f5f5", "c": "#cfcfcf", "y": "#f6a800", "Y": "#c47f00", "h": "#ffd86b", "g": "#c8c8c8"}


def draw_cursor(c, x, y, waiting=False):
    c.sprite(x, y, CURSOR, CURSOR_COLORS)
    if waiting:  # curseur « progress » : le bouton est désactivé pendant le téléchargement
        c.sprite(x + 8, y + 10, SPINNER, SPINNER_COLORS)


def draw_click(c, x, y):
    """Petits traits autour de la pointe du curseur (en haut et à droite, loin de l'icône)."""
    for dx, dy, w, h in ((0, -6, 1, 3), (3, -4, 1, 1), (4, -5, 1, 1), (3, 0, 3, 1)):
        c.rect(x + dx, y + dy, w, h, TEXT)


def draw_synthwave(c, x, y, w, h):
    """La « vidéo » : un coucher de soleil synthwave."""
    sky = ["#140a2a", "#1f0d3d", "#2e1052", "#441566", "#5f1a72", "#7f2177", "#a32a78", "#c93a73", "#ea5a6a"]
    horizon = y + round(h * 0.62)
    sky_h = horizon - y
    for yy in range(y, horizon):
        f = (yy - y) / sky_h * len(sky)
        i = min(int(f), len(sky) - 1)
        for xx in range(x, x + w):
            band = i + 1 if f - int(f) > 0.7 and i + 1 < len(sky) and (xx + yy) % 2 == 0 else i
            c.set(xx, yy, sky[band])
    rnd = random.Random(w * 31 + h)
    for _ in range(w * sky_h // 110):
        c.set(x + rnd.randrange(w), y + rnd.randrange(max(1, sky_h // 2)), rnd.choice([WHITE, "#c9b8ff", "#8a74c0"]))
    # soleil rayé
    cx, r = x + w / 2, h * 0.3
    gaps, pos, thick, space = set(), 1, 3, 2
    while r >= 6 and pos < r * 0.65:
        gaps.update(range(pos, pos + thick))
        pos, space, thick = pos + thick + space, space + 1, max(1, thick - 1)
    for yy in range(math.floor(horizon - r), horizon):
        if horizon - yy in gaps:
            continue
        for xx in range(math.floor(cx - r), math.ceil(cx + r) + 1):
            if (xx + 0.5 - cx) ** 2 + (yy + 0.5 - horizon) ** 2 <= r * r:
                c.set(xx, yy, mix("#ffe45c", "#ff2e88", (yy - (horizon - r)) / r))
    # montagnes sur les côtés
    for xx in range(x, x + w):
        u = (xx - x) / w
        edge = max(0.0, (abs(u - 0.5) * 2 - 0.32) / 0.68)
        mh = round(edge * h * 0.3 * (0.55 + 0.45 * abs(math.sin(u * 23.0))))
        for yy in range(horizon - mh, horizon):
            c.set(xx, yy, "#2a0f45")
        if mh:
            c.set(xx, horizon - mh, "#b03a9c")
    # sol quadrillé en perspective
    floor_h = y + h - horizon
    for yy in range(horizon, y + h):
        c.hline(x, yy, w, "#160624")
    c.hline(x, horizon, w, "#ff7ad9")
    d, step = 2, 1
    while d < floor_h:
        c.hline(x, horizon + d, w, mix("#5a1050", "#e0259c", d / floor_h))
        d, step = d + step + 1, step + 1
    spacing = max(6, w / 9)  # écart des lignes de fuite au bas de l'image
    for k in range(-12, 13):
        for dd in range(3, floor_h):
            xa = cx + k * spacing * dd / floor_h
            xb = cx + k * spacing * (dd + 1) / floor_h
            for xx in range(round(min(xa, xb)), max(round(min(xa, xb)) + 1, round(max(xa, xb)))):
                if x <= xx < x + w:
                    c.set(xx, horizon + dd, mix("#5a1050", "#e0259c", dd / floor_h))


def draw_icon_tile(c, x, y, size, arrow_offset=0):
    """Icône de l'extension : carré rouge arrondi, flèche blanche et bac."""
    u = size / 40
    c.round_rect(x, y + round(3 * u), size, size, 9 * u, RED_DARK)
    c.round_rect(x, y, size, size, 9 * u, RED)
    if size >= 30:
        c.hline(x + 7, y + 2, 9, RED_LIGHT)
        c.vline(x + 2, y + 7, 9, RED_LIGHT)
    o = arrow_offset
    for color, dy in ((RED_DARK, round(2 * u)), (WHITE, 0)):
        shaft_w = max(2, round(6 * u))
        c.rect(x + size // 2 - shaft_w // 2, y + round(6 * u) + o + dy, shaft_w, round(13 * u), color)
        head = round(12 * u)
        for i in range(head):
            hw = head - i
            c.hline(x + size // 2 - hw, y + round(18 * u) + o + dy + i, 2 * hw, color)
    c.rect(x + round(8 * u), y + round(34 * u) + 1, size - 2 * round(8 * u), max(1, round(3 * u)), RED_DARK)
    c.rect(x + round(8 * u), y + round(34 * u), size - 2 * round(8 * u), max(1, round(3 * u)), WHITE)


def draw_pill(c, x, y, icon, label, bg, fg, fill=None):
    """Bouton pilule de YouTube (icône + libellé) ; renvoie sa largeur."""
    icon_w = len(icon[0])
    w = 6 + icon_w + 4 + text_width(label) + 8
    c.round_rect(x, y, w, 15, 7.5, bg)
    if fill is not None:
        c.round_rect(x, y, w, 15, 7.5, FILL, x_to=x + round(w * fill / 100))
    c.sprite(x + 6, y + 3 + (9 - len(icon)) // 2, icon, {"#": fg})
    c.text(x + 6 + icon_w + 4, y + 4, label, fg)
    return w


def draw_window(c, x, y, w, h, content):
    """Fenêtre macOS miniature : barre de titre et ses trois pastilles."""
    c.round_rect(x, y, w, h, 2, "#4a4a4a")
    c.rect(x + 1, y + 4, w - 2, h - 5, content)
    for i, color in enumerate(("#ff5f57", "#febc2e", "#28c840")):
        c.rect(x + 3 + i * 3, y + 1, 2, 2, color)


def draw_chrome_logo(c, cx, cy, r):
    for yy in range(math.floor(cy - r), math.ceil(cy + r)):
        for xx in range(math.floor(cx - r), math.ceil(cx + r)):
            dx, dy = xx + 0.5 - cx, yy + 0.5 - cy
            d = math.hypot(dx, dy)
            if d > r:
                continue
            a = math.degrees(math.atan2(dy, dx))
            if d <= r * 0.36:
                color = "#4285f4"
            elif d <= r * 0.48:
                color = WHITE
            elif -150 <= a < -30:
                color = "#ea4335"
            elif -30 <= a < 90:
                color = "#fbbc05"
            else:
                color = "#34a853"
            c.set(xx, yy, color)


def draw_folder(c, x, y, with_file=False):
    """Dossier bleu façon Finder, avec une vidéo MP4 qui dépasse quand `with_file`."""
    c.round_rect(x + 1, y + 2, 11, 5, 1.5, "#2f78c4")
    c.round_rect(x + 1, y + 4, 28, 19, 2, "#2f78c4")
    if with_file:
        c.rect(x + 8, y - 4, 14, 18, "#e8e8e8")
        c.rect(x + 9, y - 3, 12, 16, WHITE)
        c.round_rect(x + 11, y + 1, 8, 6, 1.5, RED)
        c.sprite(x + 14, y + 2, ["#.", "##", "#."], {"#": WHITE})
    c.round_rect(x + 1, y + 8, 28, 15, 2, "#58aef0")
    c.hline(x + 2, y + 8, 26, "#8fcbff")


def draw_terminal(c, x, y, pct=None):
    draw_window(c, x, y, 30, 24, "#0a0a0a")
    c.sprite(x + 3, y + 6, ["#..", ".#.", "..#", ".#.", "#.."], {"#": TERM_GREEN})
    c.hline(x + 8, y + 8, 12, "#2f8f55")
    c.hline(x + 3, y + 13, 18, "#555555")
    c.hline(x + 3, y + 15, 13, "#555555")
    c.rect(x + 3, y + 18, 24, 3, "#173d26")
    if pct:
        c.rect(x + 3, y + 18, round(24 * pct / 100), 3, TERM_GREEN)


def draw_puzzle(c, x, y, color):
    c.rect(x, y + 4, 14, 14, color)
    c.circle(x + 7, y + 3.5, 3.5, color)
    c.circle(x + 17.5, y + 11, 3.5, color)


# ---------- scènes ----------

class Anim:
    def __init__(self, durations, draw):
        self.durations, self.draw = durations, draw


def scene_banner():
    W, H = 264, 80
    tile_x, tile_y, tile = 14, 12, 40
    tx, ty = 66, 13
    line1 = "Un bouton Télécharger sur YouTube"
    line2 = "en local, avec yt-dlp et ffmpeg"
    title_w = text_width("DownloaderYtb", 2)
    assert tx + title_w <= W - 8 and tx + text_width(line1) <= W - 8, "bannière trop étroite"
    bar_x0, bar_x1, bar_y = 14, 250, 62
    boxes = [(tile_x, tile_y, tile, tile + 3), (tx, ty, title_w + 2, 16), (tx, 36, text_width(line1), 9),
             (tx, 46, text_width(line2), 9), (0, 57, W, H)]
    rnd = random.Random(11)
    stars = []
    while len(stars) < 40:
        sx, sy = rnd.randrange(5, W - 5), rnd.randrange(4, 56)
        if not any(bx - 3 <= sx < bx + bw + 3 and by - 3 <= sy < by + bh + 3 for bx, by, bw, bh in boxes):
            stars.append((sx, sy))

    def draw(c):
        c.round_rect(0, 0, W, H, 6, BG)
        for i, (sx, sy) in enumerate(stars):
            c.set(sx, sy, ("#262626", "#3a3a3a", "#5a5a5a")[i % 3])
        x = tx
        for part, color in (("Downloader", WHITE), ("Ytb", RED)):
            c.text(x + 2, ty + 2, part, "#4a0b0b", 2)
            c.text(x, ty, part, color, 2)
            x += text_width(part, 2) + 2
        c.text(tx, 36, line1, MUTED)
        c.text(tx, 46, line2, DIM)
        # commandes du lecteur
        c.sprite(14, 68, ICON_PAUSE, {"#": TEXT})
        c.sprite(25, 68, ICON_NEXT, {"#": TEXT})
        c.sprite(36, 68, ICON_VOLUME, {"#": TEXT})
        c.text(W - 14 - text_width("MP4"), 68, "MP4", TEXT)
        c.sprite(W - 14 - text_width("MP4") - 11, 67, ICON_DOWNLOAD, {"#": RED})

    def arrow(c, k):
        draw_icon_tile(c, tile_x, tile_y, tile, (0, 1, 2, 1)[k])

    pcts = [35, 41, 47, 53, 59, 65, 71, 77, 83, 89, 95, 100]

    def player(c, k):
        span = bar_x1 - bar_x0
        end = bar_x0 + round(span * pcts[k] / 100)
        c.rect(bar_x0, bar_y, span, 2, "#3f3f3f")
        c.rect(bar_x0, bar_y, min(bar_x1, end + span // 8) - bar_x0, 2, DIM)
        c.rect(bar_x0, bar_y, end - bar_x0, 2, RED)
        c.circle(end, bar_y + 1, 3, RED)
        secs = round(212 * pcts[k] / 100)
        c.text(50, 68, f"{secs // 60}:{secs % 60:02d} / 3:32", MUTED)

    twinkle = stars[::6]

    def sparkle(c, k):
        for i, (sx, sy) in enumerate(twinkle):
            if (i + k) % 3 == 0:
                for dx, dy in ((-1, 0), (1, 0), (0, -1), (0, 1)):
                    c.set(sx + dx, sy + dy, "#4a4a4a")
                c.set(sx, sy, TEXT)

    return ("banner", "DownloaderYtb : un bouton Télécharger sur YouTube", W, H, draw,
            [Anim([0.16] * 4, arrow), Anim([0.5] * 12, player), Anim([0.8] * 3, sparkle)])


def scene_button():
    W, H = 264, 184
    vx, vy, vw, vh = 8, 22, 248, 72
    row_y = 131
    video_title = "Synthwave en pixel art"
    description = "Dessiné pixel par pixel, à écouter en boucle."
    file_id = "x7Kq2pLm9Va"
    assert text_width(description) <= 236, "description trop longue"

    def draw(c):
        c.round_rect(0, 0, W, H, 6, BG)
        # barre du haut : logo, recherche, avatar
        c.round_rect(8, 7, 13, 9, 2.5, RED)
        c.sprite(13, 9, ICON_PLAY_SMALL, {"#": WHITE})
        c.text(24, 8, "YouTube", TEXT)
        c.round_rect(92, 5, 116, 13, 6.5, LINE)
        c.round_rect(93, 6, 114, 11, 5.5, "#121212")
        c.round_rect(93, 6, 114, 11, 5.5, "#222222", x_from=190)
        c.vline(189, 6, 11, LINE)
        c.sprite(195, 8, ICON_SEARCH, {"#": TEXT})
        c.text(100, 8, "Rechercher", DIM)
        c.circle(250, 11.5, 5.5, "#7e57c2")
        # la vidéo et ses commandes
        draw_synthwave(c, vx, vy, vw, vh)
        c.shade(vx, vy + vh - 13, vw, 13, 0.45)
        bar = vx + 6, vx + vw - 6
        end = bar[0] + round((bar[1] - bar[0]) * 0.32)
        c.rect(bar[0], vy + vh - 12, bar[1] - bar[0], 2, "#5a5a5a")
        c.rect(bar[0], vy + vh - 12, end - bar[0], 2, RED)
        c.circle(end, vy + vh - 11, 3, RED)
        c.sprite(vx + 6, vy + vh - 8, ICON_PLAY, {"#": WHITE})
        c.sprite(vx + 16, vy + vh - 8, ICON_NEXT, {"#": WHITE})
        c.sprite(vx + 27, vy + vh - 8, ICON_VOLUME, {"#": WHITE})
        c.text(vx + 41, vy + vh - 8, "1:07 / 3:32", WHITE)
        # titre et chaîne
        c.text(8, 99, video_title, TEXT)
        c.circle(14, 117, 6, "#c93a73")  # avatar : un petit coucher de soleil
        c.circle(14, 119, 3.5, "#ffd23f")
        c.rect(8, 119, 12, 5, "#2e1052")
        for yy in range(111, 124):
            for xx in range(8, 21):
                if (xx + 0.5 - 14) ** 2 + (yy + 0.5 - 117) ** 2 > 36:
                    c.set(xx, yy, BG)
        c.text(25, 110, "Pixel TV", TEXT)
        c.text(25, 119, "12 k abonnés", MUTED)
        sx = 25 + text_width("12 k abonnés") + 10
        c.round_rect(sx, 110, text_width("S'abonner") + 16, 15, 7.5, TEXT)
        c.text(sx + 8, 114, "S'abonner", BG)
        # J'aime / Je n'aime pas, Partager
        x = 8
        w = 6 + 9 + 4 + text_width("1,2 k") + 6 + 1 + 6 + 9 + 7
        c.round_rect(x, row_y, w, 15, 7.5, CHIP)
        c.sprite(x + 6, row_y + 3, ICON_LIKE, {"#": TEXT})
        c.text(x + 19, row_y + 4, "1,2 k", TEXT)
        div = x + 19 + text_width("1,2 k") + 6
        c.vline(div, row_y + 3, 9, DIVIDER)
        c.sprite(div + 7, row_y + 3, ICON_DISLIKE, {"#": TEXT})
        x += w + 6
        x += draw_pill(c, x, row_y, ICON_SHARE, "Partager", CHIP, TEXT) + 6
        # description
        c.round_rect(8, 152, 248, 26, 5, CHIP)
        c.text(14, 156, "12 k vues · il y a 2 jours", TEXT)
        c.text(14, 167, description, MUTED)

    share_end = 8 + (6 + 9 + 4 + text_width("1,2 k") + 6 + 1 + 6 + 9 + 7) + 6
    share_end += 6 + len(ICON_SHARE[0]) + 4 + text_width("Partager") + 8 + 6
    dl_x = share_end
    rest = (6 + 7 + 4 + text_width("Télécharger") + 8 + 1, 3)   # curseur à droite du bouton
    on = (13, 11)                                               # curseur sur l'icône du bouton
    steps = [
        # libellé, fond, progression, position du curseur
        ("Télécharger", CHIP, None, rest),
        ("Télécharger", CHIP_HOVER, None, on),
        ("Télécharger", CHIP_PRESSED, None, on),
        ("Préparation…", CHIP, 0, on),
        ("12 %", CHIP, 12, on),
        ("37 %", CHIP, 37, on),
        ("64 %", CHIP, 64, on),
        ("91 %", CHIP, 91, on),
        ("Finalisation…", CHIP, 100, on),
        ("Terminé", CHIP, None, rest),
    ]
    assert dl_x + 6 + 7 + 4 + max(text_width(s[0]) for s in steps) + 8 <= W - 8, "bouton trop large"
    assert dl_x + rest[0] + len(CURSOR[0]) <= W - 3, "curseur hors cadre"

    def button(c, k):
        label, bg, fill, (cdx, cdy) = steps[k]
        tip_x, tip_y = dl_x + cdx, row_y + cdy
        if label == "Terminé":
            draw_pill(c, dl_x, row_y, ICON_CHECK, label, bg, BLUE)
            toast = [f"Téléchargé : {video_title}", f"[{file_id}].mp4 dans ~/Downloads/YouTube"]
            tw = max(text_width(t) for t in toast) + 16
            c.round_rect(8, 150, tw, 27, 4, TEXT)
            c.text(16, 154, toast[0], BG)
            c.text(16, 165, toast[1], BG)
        else:
            draw_pill(c, dl_x, row_y, ICON_DOWNLOAD, label, bg, TEXT, fill)
        if bg == CHIP_PRESSED:
            draw_click(c, tip_x, tip_y)
        draw_cursor(c, tip_x, tip_y, waiting=fill is not None)

    durations = [1.4, 0.6, 0.2, 0.5, 0.45, 0.45, 0.45, 0.45, 0.6, 2.6]
    return ("bouton", "Le bouton Télécharger, à côté de J'aime et Partager, pendant un téléchargement",
            W, H, draw, [Anim(durations, button)])


def scene_how():
    W, H = 264, 70
    centers = [24, 78, 132, 186, 240]
    names = ["YouTube", "Chrome", "Python", "yt-dlp", "Dossier"]
    hops = ["clic", "json", "url", "mp4"]
    iy = 8
    ay = iy + 12
    lane_y = 58

    def draw(c):
        c.round_rect(0, 0, W, H, 6, BG)
        for i, (cx, name) in enumerate(zip(centers, names)):
            c.ctext(cx, iy + 29, name, TEXT)
            if i < 4:
                x0, x1 = cx + 17, centers[i + 1] - 17
                for xx in range(x0, x1 - 3, 2):
                    c.set(xx, ay, DIM)
                c.sprite(x1 - 3, ay - 3, ["#...", "##..", "###.", "####", "###.", "##..", "#..."], {"#": DIM})
                c.ctext((x0 + x1) // 2, iy, hops[i], MUTED)
        draw_chrome_logo(c, centers[1], iy + 12, 11)
        c.sprite(centers[2] - 11, iy + 1, PYTHON, PYTHON_COLORS)
        # retour de la progression : yt-dlp -> bouton
        for yy in range(iy + 40, lane_y, 2):
            c.set(centers[3], yy, DIM)
            c.set(centers[0], yy, DIM)
        for xx in range(centers[0], centers[3] + 1, 2):
            c.set(xx, lane_y, DIM)
        c.sprite(centers[0] - 3, iy + 38, ["...#...", "..###..", ".#####.", "#######"], {"#": DIM})

    # étape : (paquet aller, n° de flèche / position), (paquet retour), progression, état final
    frames = [
        (None, None, None),
        ((0, 1 / 3), None, None),
        ((0, 2 / 3), None, None),
        ((1, 1 / 3), None, None),
        ((1, 2 / 3), None, None),
        ((2, 1 / 3), None, None),
        ((2, 2 / 3), None, None),
        (None, 0.15, 12),
        (None, 0.5, 47),
        (None, 0.85, 86),
        ((3, 1 / 3), None, 100),
        ((3, 2 / 3), None, 100),
        (None, None, "done"),
    ]

    def flow(c, k):
        packet, back, progress = frames[k]
        done = progress == "done"
        pct = None if done or progress is None else progress
        # icône YouTube : fenêtre, vidéo et petit bouton
        x = centers[0] - 15
        draw_window(c, x, iy, 30, 24, BG)
        draw_synthwave(c, x + 2, iy + 5, 26, 11)
        pressed = packet is not None and packet[0] == 0
        c.round_rect(x + 2, iy + 17, 16, 5, 2.5, CHIP_PRESSED if pressed else CHIP)
        if pct is not None:
            c.round_rect(x + 2, iy + 17, 16, 5, 2.5, "#6a6a6a", x_to=x + 2 + round(16 * pct / 100))
        c.sprite(x + 4, iy + 18, ["###", ".#."] if not done else ["..#", "##."], {"#": BLUE if done else TEXT})
        draw_terminal(c, centers[3] - 15, iy, pct if pct is not None else (100 if done else None))
        draw_folder(c, centers[4] - 15, iy, with_file=done)
        if packet:
            i, t = packet
            x0, x1 = centers[i] + 17, centers[i + 1] - 17
            px = round(x0 + (x1 - x0) * t) - 2
            c.rect(px, ay - 2, 4, 4, RED)
            c.set(px, ay - 2, RED_LIGHT)
        if back is not None:
            px = round(centers[3] - (centers[3] - centers[0]) * back)
            c.rect(px - 1, lane_y - 1, 3, 3, BLUE)
        label = "Terminé" if done else ("progression" if pct is None else f"progression : {pct} %")
        lw = text_width(label) + (13 if done else 0)
        mid = (centers[0] + centers[3]) // 2
        c.rect(mid - 60, lane_y - 4, 120, 9, BG)
        if done:
            c.sprite(mid - lw // 2, lane_y - 3, ICON_CHECK, {"#": BLUE})
            c.text(mid - lw // 2 + 13, lane_y - 3, label, BLUE)
        else:
            c.text(mid - lw // 2, lane_y - 3, label, MUTED if pct is None else TEXT)

    durations = [1.0, 0.22, 0.22, 0.22, 0.22, 0.22, 0.22, 0.4, 0.4, 0.4, 0.22, 0.22, 2.2]
    return ("fonctionnement", "Clic sur YouTube, Chrome lance le programme Python, qui appelle yt-dlp ; "
            "la vidéo arrive dans le dossier et la progression remonte jusqu'au bouton", W, H, draw,
            [Anim(durations, flow)])


def scene_popup():
    W = 264
    pw = 188
    px, py = W - 8 - pw, 24
    x0, iw = px + 8, pw - 16
    rows = [("Synthwave en pixel art", "Terminé", GREEN, True), ("Lofi pixel beats - 2 h", "47 %", MUTED, False)]
    H = py + 178

    def select(c, x, y, w, label):
        c.framed(x, y, w, 13, 3, LINE, BG)
        c.text(x + 5, y + 3, label, TEXT)
        c.sprite(x + w - 10, y + 5, ICON_CARET, {"#": TEXT})

    def draw(c):
        c.round_rect(0, 0, W, H, 6, "#181818")
        # la page YouTube, en retrait derrière le popup
        for i in range(4):
            y = 26 + i * 40
            c.round_rect(8, y, 56, 30, 3, "#262626")
            c.hline(8, y + 33, 50, "#262626")
            c.hline(8, y + 36, 34, "#222222")
        # barre d'outils de Chrome
        c.round_rect(0, 0, W, 18, 6, "#35363a")
        c.rect(0, 12, W, 6, "#35363a")
        c.text(6, 5, "<", MUTED)
        c.text(14, 5, ">", DIM)
        c.round_rect(26, 3, 180, 12, 6, "#202124")
        c.text(34, 5, fit("youtube.com/watch?v=" + "x7Kq2pLm9Va", 164), MUTED)
        c.circle(px + pw - 33.5, 9, 7, "#4a4c50")
        draw_icon_tile(c, px + pw - 39, 4, 11)
        c.circle(W - 13, 9, 4.5, "#7e57c2")
        # le popup
        c.rect(px + 2, py + 2, pw, H - py - 8, "#000000")
        c.framed(px, py, pw, H - py - 8, 4, "#5f6368", BG)
        y = py + 8
        c.text(x0, y, "YouTube Download Button", TEXT)
        y += 14
        c.circle(x0 + 2.5, y + 3.5, 2.5, GREEN)
        c.text(x0 + 9, y, "Prêt (yt-dlp 2026.10.18)", TEXT)
        y += 14
        c.text(x0, y, "Format", TEXT)
        select(c, x0, y + 10, iw, "Vidéo (MP4)")
        y += 28
        c.text(x0, y, "Qualité maximale", TEXT)
        select(c, x0, y + 10, iw, "1080p max")
        y += 28
        c.text(x0, y, "Dossier de destination", TEXT)
        bw = text_width("Choisir…") + 10
        c.framed(x0, y + 10, iw - bw - 4, 13, 3, LINE, BG)
        c.text(x0 + 5, y + 13, "~/Downloads/YouTube", TEXT)
        c.round_rect(x0 + iw - bw, y + 10, bw, 13, 3, CHIP)
        c.text(x0 + iw - bw + 5, y + 13, "Choisir…", TEXT)
        y += 29
        c.text(x0, y, "Téléchargements de cette session", MUTED)
        y += 11
        for name, state, color, finder in rows:
            c.hline(x0, y, iw, LINE)
            right = x0 + iw
            if finder:
                fw = text_width("Finder") + 8
                c.round_rect(right - fw, y + 2, fw, 11, 3, CHIP)
                c.text(right - fw + 4, y + 4, "Finder", TEXT)
                right -= fw + 5
            c.text(right - text_width(state), y + 4, state, color)
            right -= text_width(state) + 6
            c.text(x0, y + 4, fit(name, right - x0), TEXT)
            y += 15
        assert y + 6 <= H - 8, "popup trop court"

    return ("popup", "Le popup de l'extension : état de yt-dlp, format, qualité, dossier et téléchargements",
            W, H, draw, [])


def scene_install():
    W, H = 264, 78
    cards = [("Homebrew", "+ yt-dlp"), ("installer", ".command"), ("extension", "chrome://"), ("YouTube", "1 clic")]
    cw, gap, x_start = 57, 8, 6

    def draw(c):
        c.round_rect(0, 0, W, H, 6, BG)
        for i, (label, sub) in enumerate(cards):
            x = x_start + i * (cw + gap)
            cx = x + cw // 2
            assert text_width(label) <= cw - 6 and text_width(sub) <= cw - 6, label
            c.framed(x, 6, cw, 66, 4, "#303030", CARD)
            c.circle(x + 8.5, 14.5, 5.5, RED)
            c.text(x + 7, 11, str(i + 1), WHITE)
            c.ctext(cx, 49, label, TEXT)
            c.ctext(cx, 59, sub, DIM)
            if i < 3:
                c.sprite(x + cw + 3, 36, ["#..", ".#.", "..#", ".#.", "#.."], {"#": RED})
            if i == 0:
                c.sprite(cx - 9, 20, MUG, MUG_COLORS)
            elif i == 1:
                c.rect(cx - 9, 17, 16, 21, "#d9d9d9")
                c.rect(cx - 8, 18, 14, 19, WHITE)
                c.rect(cx + 3, 17, 4, 4, CARD)
                c.sprite(cx + 3, 17, ["#...", "##..", "###.", "####"], {"#": "#bdbdbd"})
                c.rect(cx - 6, 23, 10, 9, "#0a0a0a")
                c.sprite(cx - 5, 25, ["#..", ".#.", "#.."], {"#": TERM_GREEN})
                c.hline(cx - 1, 27, 3, TERM_GREEN)
                draw_cursor(c, cx + 3, 30)
            elif i == 2:
                draw_puzzle(c, cx - 12, 17, "#9aa0a6")
                c.round_rect(cx + 2, 33, 13, 7, 3.5, "#3c6ea8")
                c.circle(cx + 11.5, 36.5, 3.5, "#8ab4f8")
            else:
                c.round_rect(cx - 10, 15, 20, 13, 4, RED)
                c.sprite(cx - 2, 18, ICON_PLAY, {"#": WHITE})
                c.round_rect(cx - 10, 30, 20, 9, 4.5, CHIP)
                c.sprite(cx - 5, 31, ["..#..", "..#..", "#####", ".###.", "..#..", ".....", "#####"],
                         {"#": TEXT})
                draw_cursor(c, cx + 3, 33)

    return ("installation", "Installation : Homebrew, installer.command, l'extension dans Chrome, puis un clic sur YouTube",
            W, H, draw, [])


# ---------- export ----------

def grid_paths(grid):
    """{couleur: chemin SVG} : pixels voisins de même couleur fusionnés en rectangles."""
    rects, previous = {}, {}
    for y, row in enumerate(grid):
        current, x = {}, 0
        while x < len(row):
            color = row[x]
            if color is None:
                x += 1
                continue
            start = x
            while x < len(row) and row[x] == color:
                x += 1
            key = (start, x - start, color)
            r = previous.get(key)
            if r:
                r[3] += 1
            else:
                r = [start, y, x - start, 1]
                rects.setdefault(color, []).append(r)
            current[key] = r
        previous = current
    return {color: "".join(f"M{x} {y}h{w}v{h}h-{w}z" for x, y, w, h in rs) for color, rs in rects.items()}


def pct(t):
    return f"{t * 100:.2f}".rstrip("0").rstrip(".") + "%"


def esc(s):
    return s.replace("&", "&amp;").replace("<", "&lt;").replace('"', "&quot;")


def render(scene):
    name, title, w, h, draw, anims = scene
    background = Canvas(w, h)
    draw(background)

    def frame(anim_index, k):
        c = background.copy()
        for j, anim in enumerate(anims):
            anim.draw(c, k if j == anim_index else 0)
        return c

    base = frame(None, 0)
    layers = []
    for ai, anim in enumerate(anims):
        total = sum(anim.durations)
        t = anim.durations[0]
        for k in range(1, len(anim.durations)):
            img = frame(ai, k)
            diff = [[p if p != b else None for p, b in zip(row, brow)] for row, brow in zip(img.px, base.px)]
            layers.append((f"a{ai}f{k}", diff, t / total, (t + anim.durations[k]) / total, total, img))
            t += anim.durations[k]
    return name, title, base, layers


def to_svg(title, base, layers):
    out = [f'<svg xmlns="http://www.w3.org/2000/svg" width="{base.w * SCALE}" height="{base.h * SCALE}" '
           f'viewBox="0 0 {base.w} {base.h}" shape-rendering="crispEdges" role="img" aria-label="{esc(title)}">',
           f"<title>{esc(title)}</title>"]
    if layers:
        css = [".f{opacity:0;animation-timing-function:step-end;animation-iteration-count:infinite}"]
        for name, _, start, end, total, _ in layers:
            keys = f"0%{{opacity:0}}{pct(start)}{{opacity:1}}" + (f"{pct(end)}{{opacity:0}}" if end < 0.99999 else "")
            css.append(f".{name}{{animation-name:{name};animation-duration:{total:g}s}}@keyframes {name}{{{keys}}}")
        css.append("@media (prefers-reduced-motion:reduce){.f{animation:none}}")
        out.append("<style>" + "".join(css) + "</style>")
    out += [f'<path fill="{color}" d="{d}"/>' for color, d in grid_paths(base.px).items()]
    for name, grid, *_ in layers:
        paths = "".join(f'<path fill="{color}" d="{d}"/>' for color, d in grid_paths(grid).items())
        out.append(f'<g class="f {name}">{paths}</g>')
    out.append("</svg>")
    return "\n".join(out) + "\n"


def to_png(canvas, scale):
    rows = []
    for row in canvas.px:
        line = b"".join((bytes(rgb(p)) + b"\xff" if p else b"\0\0\0\0") * scale for p in row)
        rows += [b"\0" + line] * scale

    def chunk(tag, data):
        return struct.pack(">I", len(data)) + tag + data + struct.pack(">I", zlib.crc32(tag + data) & 0xFFFFFFFF)

    header = struct.pack(">IIBBBBB", canvas.w * scale, canvas.h * scale, 8, 6, 0, 0, 0)
    return (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", header) + chunk(b"IDAT", zlib.compress(b"".join(rows), 9))
            + chunk(b"IEND", b""))


def main():
    png_dir = sys.argv[sys.argv.index("--png") + 1] if "--png" in sys.argv else None
    for scene in (scene_banner(), scene_button(), scene_how(), scene_popup(), scene_install()):
        name, title, base, layers = render(scene)
        path = os.path.join(HERE, f"{name}.svg")
        with open(path, "w", encoding="utf-8") as f:
            f.write(to_svg(title, base, layers))
        print(f"{os.path.relpath(path)} ({os.path.getsize(path) // 1024} Ko, {len(layers)} calques)")
        if png_dir:
            os.makedirs(png_dir, exist_ok=True)
            with open(os.path.join(png_dir, f"{name}.png"), "wb") as f:
                f.write(to_png(base, SCALE))
            for layer_name, *_, img in layers:
                with open(os.path.join(png_dir, f"{name}-{layer_name}.png"), "wb") as f:
                    f.write(to_png(img, SCALE))


if __name__ == "__main__":
    main()
