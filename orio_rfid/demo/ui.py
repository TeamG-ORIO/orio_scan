#!/usr/bin/env python3
"""
ui.py - what the parts of the scan demo window share: the two colour themes, the fonts, the icons and the item photos.

Colours    P.CRUST, P.TEXT, P.GREEN ... are the colours of the theme in use; ui.P.use('light') switches all of them, and
           the window is then built again. Dark is Catppuccin Mocha, as in the arms' orio_dashboard.py. Light is its
           counterpart for a projector or a bright room: every text colour has a contrast of at least 4.5 to 1 on the
           panels, and white on every button colour at least 5 to 1.
           P.ON is the text colour on a coloured button, P.FIELD the background of the table, the log and the map.
Fonts      families(root, wanted) picks the text font: the first of SANS (and MONO for the log) this computer has, or the
           one asked for (FONT in the conf file). font_file() finds that font's file for the map, which Pillow draws.
           font_system(root) says how this Python's Tk draws text: 'xft' is needed. A Tk built without it (some conda
           builds) can only use the X server's old bitmap fonts: no smoothing, hardly any symbols; the window says so.
Icons      icon(kind, size, colour): every symbol in the window (start, stop, arrows, turn, close, the boxes of the
           checkboxes and radio buttons) is a small picture drawn here, not a character, so it does not depend on what
           a font happens to contain. The boxes are one small fixed size, whatever the size of the window.
Photos     Photos(folder).image(uid, name, size, no): the item's photo as a square Tk picture. A file is found by the
           tag id, then by the tag number, then by the item name, whatever its capitals, spaces and punctuation
           ("E28011B0A50500726AC86888.jpg", "tag 30.jpg", "item 30.jpg", "30.png", "Surgical Gown XL (Blue).jpg" and
           "surgical_gown_xl_blue.png" all work); jpg, png, webp, gif, bmp. The
           folder is looked at again every few seconds, so a photo dropped in shows up without a restart. No file: a
           drawn placeholder.

Standard library; the photos and the icons need Pillow.
"""
import math
import os
import re
import subprocess
import time

try:
    from PIL import Image, ImageDraw, ImageFont, ImageOps, ImageTk
except ImportError:
    Image = None

PALETTES = {
    'dark': dict(CRUST='#11111b', MANTLE='#181825', BASE='#1e1e2e', SURF0='#313244', SURF1='#45475a', SURF2='#585b70',
                 OVERLAY='#6c7086', SUBTEXT='#a6adc8', TEXT='#cdd6f4', BLUE='#89b4fa', MAUVE='#cba6f7', GREEN='#a6e3a1',
                 YELLOW='#f9e2af', PEACH='#fab387', RED='#f38ba8', TEAL='#94e2d5', SKY='#89dceb', ESTOP='#d20f39', ON='#11111b', FIELD='#11111b'),
    'light': dict(CRUST='#d5dae3', MANTLE='#f6f7fa', BASE='#ffffff', SURF0='#e3e6ee', SURF1='#cdd2de', SURF2='#a3abbd',
                  OVERLAY='#737a91', SUBTEXT='#535a70', TEXT='#1f2333', BLUE='#1d63d8', MAUVE='#7b3fd6', GREEN='#237a36',
                  YELLOW='#9a6200', PEACH='#bd4c0b', RED='#c8102e', TEAL='#0d757c', SKY='#0a74a6', ESTOP='#d20f39', ON='#ffffff', FIELD='#ffffff'),
}


class _Palette:
    """the colours of the theme in use, as attributes"""

    def use(self, name):
        self.name = name if name in PALETTES else 'dark'
        self.__dict__.update(PALETTES[self.name])
        self.DOT = {'ok': self.GREEN, 'wait': self.YELLOW, 'bad': self.RED, 'off': self.SURF2}
        self.PHASE = {'idle': self.SURF2, 'driving': self.GREEN, 'replaying': self.TEAL, 'paused': self.YELLOW, 'done': self.GREEN,
                      'replay finished': self.GREEN, 'failed': self.RED, 'E-STOPPED': self.RED, 'stopped': self.PEACH, 'stopping': self.PEACH}

    def on(self, bg):
        """the text colour that reads on this background"""
        return self.TEXT if bg in (self.SURF0, self.SURF1, self.SURF2, self.MANTLE, self.BASE, self.CRUST, self.FIELD) else self.ON


P = _Palette()
P.use('dark')

EXTS = ('.jpg', '.jpeg', '.png', '.webp', '.gif', '.bmp')

# text fonts, best first: plain, even sans-serif faces made for screens; DejaVu last (it is on every Linux, and wide)
SANS = ('Inter', 'Roboto', 'Noto Sans', 'Open Sans', 'Source Sans 3', 'Source Sans Pro', 'IBM Plex Sans', 'Lato', 'Liberation Sans', 'Arial',
        'Helvetica', 'Ubuntu', 'DejaVu Sans')
MONO = ('JetBrains Mono', 'Roboto Mono', 'IBM Plex Mono', 'Ubuntu Mono', 'Liberation Mono', 'DejaVu Sans Mono')
BOX = 13                # the checkbox and radio button box, in pixels, at every window size


def font_system(root):
    """on Linux: 'xft' (smooth fonts, every installed font) or 'x11' (the X server's own old fonts only; also when Tk does not
    say, as abuela's conda build). Elsewhere: the name of the windowing system, where this question does not come up."""
    try:
        ws = str(root.tk.call('tk', 'windowingsystem'))
    except Exception:
        ws = 'x11'
    if ws != 'x11':
        return ws
    try:
        return 'xft' if str(root.tk.call('::tk::pkgconfig', 'get', 'fontsystem')) == 'xft' else 'x11'
    except Exception:
        return 'x11'


def families(root, wanted=None):
    """-> (text font, fixed-width font) from what this computer has; wanted = a family to use if it is there"""
    from tkinter import font as tkfont
    have = {f.lower(): f for f in tkfont.families(root)}
    first = lambda names: next((have[n.lower()] for n in names if n and n.lower() in have), None)
    return first((wanted,) + SANS) or 'TkDefaultFont', first(MONO) or 'TkFixedFont'


_files = {}


def font_file(family, bold=False):
    """the file of that font (for Pillow, which draws the map's text), or None; asks fontconfig"""
    key = (family, bold)
    if key not in _files:
        _files[key] = None
        try:
            out = subprocess.run(['fc-match', '-f', '%{family[0]}|%{file}', '%s:%s' % (family, 'bold' if bold else 'regular')],
                                 capture_output=True, text=True, timeout=3).stdout
            fam, _, path = out.partition('|')
            if fam.strip().lower() == family.lower() and os.path.exists(path) and path.lower().endswith(('.ttf', '.otf', '.ttc')):
                _files[key] = path
        except (OSError, subprocess.SubprocessError):
            pass
    return _files[key]


def pil_font(size, family=None, bold=False):
    """a Pillow font of that pixel size: the window's text font if its file is found, else DejaVu, else Pillow's own"""
    for name in (font_file(family, bold) if family else None, 'DejaVuSans-Bold.ttf' if bold else 'DejaVuSans.ttf',
                 '/usr/share/fonts/truetype/dejavu/DejaVuSans%s.ttf' % ('-Bold' if bold else '')):
        if name:
            try:
                return ImageFont.truetype(name, size)
            except OSError:
                pass
    return ImageFont.load_default()


_icons = {}


def icon(kind, size, colour, fill=None, margin=(0, 0)):
    """a size x size Tk picture of a symbol in that colour on nothing (the widget's own background shows through).
    kinds: play stop up down left right turn_l turn_r home x caret sort_up sort_down box box_on radio radio_on
    fill: the inside of a box that is on (box_on, radio_on); colour is then its mark.
    margin: empty pixels added left and right of it. None without Pillow."""
    if Image is None:
        return None
    key = (kind, size, colour, fill, tuple(margin))
    if key in _icons:
        return _icons[key]
    k = 8; n = size * k                       # drawn 8 times as large, then made small: smooth edges
    im = Image.new('RGBA', (n, n), (0, 0, 0, 0)); d = ImageDraw.Draw(im)
    ink = _rgb(colour) + (255,); pt = lambda *xy: [(x * n, y * n) for x, y in zip(xy[::2], xy[1::2])]
    w = max(k, int(round(n * 0.11)))            # line width
    if kind == 'play':
        d.polygon(pt(0.18, 0.10, 0.90, 0.50, 0.18, 0.90), fill=ink)
    elif kind == 'stop':
        d.rounded_rectangle([0.14 * n, 0.14 * n, 0.86 * n, 0.86 * n], radius=0.08 * n, fill=ink)
    elif kind in ('up', 'sort_up'):
        d.polygon(pt(0.50, 0.16, 0.90, 0.80, 0.10, 0.80), fill=ink)
    elif kind in ('down', 'sort_down', 'caret'):
        d.polygon(pt(0.10, 0.22, 0.90, 0.22, 0.50, 0.84), fill=ink)
    elif kind == 'left':
        d.polygon(pt(0.14, 0.50, 0.80, 0.10, 0.80, 0.90), fill=ink)
    elif kind == 'right':
        d.polygon(pt(0.86, 0.50, 0.20, 0.10, 0.20, 0.90), fill=ink)
    elif kind in ('turn_l', 'turn_r'):          # most of a circle with an arrow head at its end. Drawn clockwise (turn right, seen
        cx, cy, r = 0.50 * n, 0.54 * n, 0.30 * n     # from above); turn left is its mirror image
        a0, a1 = -40, 205                         # degrees, clockwise from 3 o'clock (the picture's y points down)
        d.arc([cx - r, cy - r, cx + r, cy + r], start=a0, end=a1, fill=ink, width=w)
        t = math.radians(a1 + 8); x, y = cx + r * math.cos(t), cy + r * math.sin(t)
        tx, ty, nx, ny, h = -math.sin(t), math.cos(t), math.cos(t), math.sin(t), 0.20 * n      # along the arc, across it
        d.polygon([(x + tx * h, y + ty * h), (x - tx * 0.35 * h + nx * 0.85 * h, y - ty * 0.35 * h + ny * 0.85 * h),
                   (x - tx * 0.35 * h - nx * 0.85 * h, y - ty * 0.35 * h - ny * 0.85 * h)], fill=ink)
        if kind == 'turn_l':
            im = ImageOps.mirror(im)
    elif kind == 'home':
        d.polygon(pt(0.50, 0.10, 0.94, 0.52, 0.06, 0.52), fill=ink); d.rectangle([0.20 * n, 0.50 * n, 0.80 * n, 0.88 * n], fill=ink)
        d.rectangle([0.42 * n, 0.62 * n, 0.58 * n, 0.88 * n], fill=(0, 0, 0, 0))
    elif kind == 'x':
        d.line(pt(0.20, 0.20, 0.80, 0.80), fill=ink, width=w); d.line(pt(0.80, 0.20, 0.20, 0.80), fill=ink, width=w)
    elif kind in ('box', 'box_on'):
        on = kind == 'box_on'; e = 0.5 * k
        d.rounded_rectangle([e, e, n - 1 - e, n - 1 - e], radius=0.17 * n, fill=(_rgb(fill) + (255,)) if on and fill else None,
                            outline=(_rgb(fill) + (255,)) if on and fill else ink, width=k)
        if on:
            d.line(pt(0.24, 0.52, 0.43, 0.71, 0.77, 0.30), fill=ink, width=int(1.6 * k), joint='curve')
    elif kind in ('radio', 'radio_on'):
        on = kind == 'radio_on'; e = 0.5 * k
        d.ellipse([e, e, n - 1 - e, n - 1 - e], outline=(_rgb(fill) + (255,)) if on and fill else ink, width=k)
        if on:
            d.ellipse([0.29 * n, 0.29 * n, 0.71 * n - 1, 0.71 * n - 1], fill=(_rgb(fill) + (255,)) if fill else ink)
    else:
        raise ValueError(kind)
    im = im.resize((size, size), Image.LANCZOS)
    if any(margin):
        wide = Image.new('RGBA', (size + margin[0] + margin[1], size), (0, 0, 0, 0)); wide.paste(im, (margin[0], 0)); im = wide
    _icons[key] = ImageTk.PhotoImage(im)
    return _icons[key]


def slug(text):
    return re.sub(r'[^a-z0-9]+', '', str(text or '').lower())


def _rgb(hexcol):
    h = {'white': '#ffffff', 'black': '#000000'}.get(hexcol, hexcol).lstrip('#')
    return tuple(int(h[i:i + 2], 16) for i in (0, 2, 4))


def mix(a, b, t):
    """the colour a part t of the way from a to b"""
    return '#%02x%02x%02x' % tuple(int(round(x + (y - x) * t)) for x, y in zip(_rgb(a), _rgb(b)))


class Photos:
    def __init__(self, folder):
        self.folder, self.index, self.at, self.cache = folder, {}, 0.0, {}

    def _look(self):
        if time.time() - self.at < 3.0:
            return
        self.at, idx = time.time(), {}
        try:
            for n in sorted(os.listdir(self.folder)):
                stem, ext = os.path.splitext(n)
                if ext.lower() in EXTS and slug(stem):
                    idx.setdefault(slug(stem), os.path.join(self.folder, n))
        except OSError:
            pass
        self.index = idx

    def path(self, uid, name, no=None):
        """the photo file of this tag (by its id first, then by its item number, then by its item name), or None"""
        self._look()
        for key in (slug(uid),) + (('tag%d' % no, 'item%d' % no, str(no)) if no is not None else ()) + (slug(name),):
            if key and key in self.index:
                return self.index[key]
        return None

    def image(self, uid, name, size, no=None):
        """a size x size Tk picture: the photo, whole, on the theme's background, or the placeholder. None without Pillow."""
        if Image is None:
            return None
        path = self.path(uid, name, no)
        try:
            key = (path, os.path.getmtime(path) if path else None, size, P.name)
        except OSError:
            path, key = None, (None, None, size, P.name)
        if key not in self.cache:
            if len(self.cache) > 200:
                self.cache.clear()
            im = None
            if path:
                try:
                    src = ImageOps.exif_transpose(Image.open(path)).convert('RGB')        # a phone photo taken sideways
                    src.thumbnail((size, size))
                    im = Image.new('RGB', (size, size), _rgb(P.SURF0)); im.paste(src, ((size - src.width) // 2, (size - src.height) // 2))
                except Exception:               # not a picture after all: the placeholder
                    im = None
            self.cache[key] = ImageTk.PhotoImage(im or placeholder(size))
        return self.cache[key]


def placeholder(size):
    """a drawn box with "no photo" under it, in the theme's colours"""
    im = Image.new('RGB', (size, size), _rgb(P.SURF0)); d = ImageDraw.Draw(im)
    ink, w = _rgb(P.OVERLAY), max(1, size // 40)
    x0, x1, y0, y1 = 0.30 * size, 0.70 * size, 0.24 * size, 0.60 * size; lid = y0 + 0.30 * (y1 - y0); mid = (x0 + x1) / 2
    d.rectangle([x0, y0, x1, y1], outline=ink, width=w)
    d.line([(x0, lid), (x1, lid)], fill=ink, width=w)
    d.line([(mid, y0), (mid, lid + 0.16 * (y1 - y0))], fill=ink, width=w)
    font = pil_font(max(8, size // 8), FAMILY)
    b = d.textbbox((0, 0), 'no photo', font=font)
    d.text(((size - (b[2] - b[0])) / 2, 0.70 * size), 'no photo', fill=ink, font=font)
    return im


FAMILY = None            # the window's text font, once it has picked one (the placeholder and the map use its file)
