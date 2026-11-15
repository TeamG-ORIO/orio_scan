#!/usr/bin/env python3
"""
scan_dashboard.py - the window for the autonomous RFID scan demo: start and stop the robot, watch the run, see the
inventory fill in. Runs on the PC (run_scan_demo.sh starts it); the robot is reached through scripts/demo_agent.py
in orio_stretch, the inventory through sync/pc_receiver.py on this PC.

    python3 scan_dashboard.py [--conf demo.conf] [--agent http://robot:8123] [--receiver http://127.0.0.1:8000] [--token T]
                              [--screen inventory|operate] [--theme dark|light] [--geometry 1600x1000]

One window, two screens. The INVENTORY / OPERATE buttons in the top bar (or the F2 key) switch between them; the top
bar and the four link lights stay.

Top bar      START / PAUSE / STOP and E-STOP (also the Esc key, on both screens, wherever the cursor is). E-STOP stops
             all motion and latches: release it with the button on the robot's head (hold 2 s) or RELEASE RUNSTOP on
             the OPERATE screen. LIGHT / DARK switches the colours (light: for a projector or a bright room).
Size         everything grows and shrinks with the window: text, buttons, table rows, the map's marks. Ctrl + and
             Ctrl - make it all larger or smaller for the same window, Ctrl 0 puts that back.
Map          on both screens. The room in 2D (from above) or 3D: shelves, the map's point cloud, waypoints, the driven
             path, the robot and every item where the localiser has placed it so far (filled dot = placed in this scan,
             grey ring = last known place, red cross = missed a scan). Buttons 2D / 3D / CAMERA; drag, wheel and
             double-click move the view. Point at an item for its name; click it (or its row in the inventory) and the
             map shows its uncertainty region, and a card in the bottom right corner its photo, tag number, name and expiry date.
             Needs numpy and Pillow; without them only the route is drawn.
             In manual mode (no run going) CAMERA shows the head camera, about 6 pictures a second.

INVENTORY screen (inventory_panel.py, rules in inventory_model.py)
             Tiles: on shelf, missing, expired, expiring, below PAR; a click on one shows just those. Search box (names
             and item numbers) and filter boxes; what does not pass is drawn faint on the map. ITEMS: one row per tag
             (an object that carries several tags counts once, see inventory_model.py "objects") with its tag number
             (the number it has in RFID/tag_registry.csv), its name (from the names file, NAMES in
             the conf file; – until it has one), its state
             (on shelf / missed N scans / not seen yet), shelf, expiry and last sighting. STOCK: one row per item name
             with how many usable ones are on the shelf against its PAR (double-click to set it; expired items do not
             count; tags without a name are not in it).
             METRICS (button over the map): pick which numbers show under the map. "Within 1 m3" is the share of the
             placed items with a tape-measured position whose estimate is less than 0.62 m from it.
OPERATE screen
  Setup      real run or the replay of an old run (no robot motion), laps, start waypoint, localisation method. A real run
             is started with MAP, RUN_DIR, WAYPOINTS and, if set, FEATURE_BAND from the conf file (ORB-SLAM3 then only uses
             features in that height band; it goes with a map built the same way, e.g. hospital_corridor_v2_cap) and
             LIFT_SPEED (the lift wave's speed, m/s). The Antenna pose check turns red when the lift stops waving.
             "keep what earlier runs found" (ticked when the window opens): untick it before START and the PC first
             forgets the earlier runs (last known places, missed-scan counts, last-seen times), so nothing carries over.
  Checks     one line per thing that has to work, green when it does: the list to look at when something is wrong.
  Manual     MANUAL ON starts the driver and then, by itself, raises the lift, retracts the arm, turns the wrist in to
             75 degrees short of stowed and points the head as a run does (it asks first). Everything on the pad is
             hold-to-move: base (W A S D / Q E), lift (R / F; 0.17 to 1.05 m, the range a run waves over since the
             recalibration of 14 Nov) and camera (I J K L). The camera's middle button puts it back. MANUAL OFF puts
             the head back to its usual pose before the driver stops, so every run starts with the camera looking the
             same way. POSE AGAIN repeats the pose move. Hold a pad button, or the keys with the pointer over the pad.
             The robot moves only while a button is held (it stops by itself 0.5 s after the last command).

Names        one file on this PC (NAMES in the conf file, default: item_names.csv next to the database): item number,
             name. The team adds to it; it is read again when it is saved. The inventory shown is the tags that have
             an item number in RFID/tag_registry.csv; other rows of the database are left out (inventory_model.Catalog).
Photos       one folder on this PC (PHOTOS in the conf file, default: item_photos next to the database). A file named
             after the item ("Surgical Gown XL (Blue).jpg") or after the tag id; see ui.py. No file: a placeholder.
Fonts        the text font is the first good one this PC has (ui.SANS), or FONT in the conf file. Symbols and checkbox boxes
             are drawn pictures (ui.icon), not characters. This needs a Python whose Tk has Xft: run_scan_demo.sh picks one
             (GUI_PYTHON in the conf file); see README.md "Fonts and symbols".
Remembered   theme, screen, window size, zoom and the metrics shown, in ~/.orio_scan_window.json.

Standard library only (tkinter); the map, the camera picture and the photos need numpy / Pillow.
"""
import argparse
import json
import math
import os
import sys
import threading
import time
import tkinter as tk
import traceback
import urllib.error
import urllib.request
from tkinter import font as tkfont, messagebox, ttk

try:
    import io
    from PIL import Image, ImageTk
except ImportError:      # the camera picture then shows a note instead
    Image = ImageTk = None
try:
    import scene_view    # numpy + Pillow: the 2D / 3D map
except ImportError:
    scene_view = None
import inventory_model as M
import ui
from inventory_panel import InventoryPanel
from ui import P

HERE = os.path.dirname(os.path.abspath(__file__))
PREFS = os.environ.get('ORIO_SCAN_PREFS') or os.path.expanduser('~/.orio_scan_window.json')      # what the window remembers
BASE_W, BASE_H = 1280, 800           # the window size at which everything has its plain size (scale 1)
FONTS = (('title', 17, 'bold'), ('h', 10, 'bold'), ('b', 10, 'normal'), ('small', 9, 'normal'), ('btn', 12, 'bold'), ('big', 20, 'bold'), ('mono', 9, 'normal'))
V_STEP, W_STEP = 0.10, 0.35          # manual speed at full scale (the robot clamps to 0.15 m/s, 0.5 rad/s)
DRIVE_KEYS, JOG_KEYS = 'wasdqe', 'rfijkl'     # held keys / buttons: base, then lift up / down and camera up / left / down / right


def load_prefs():
    try:
        d = json.load(open(PREFS))
        return d if isinstance(d, dict) else {}
    except (OSError, ValueError):
        return {}


def read_conf(path):
    conf = {}
    for line in open(path):
        line = line.strip()
        if line and not line.startswith('#') and '=' in line:
            k, v = line.split('=', 1)
            conf[k.strip()] = v.strip()
    return conf


def http(url, payload=None, token='', timeout=2.5):
    data = None if payload is None else json.dumps(payload).encode()
    req = urllib.request.Request(url, data=data, headers={'Content-Type': 'application/json', 'X-Orio-Token': token})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read())


def ago(t):
    if not t:
        return 'never'
    s = max(0.0, time.time() - t)
    return '%.0f s ago' % s if s < 90 else '%.0f min ago' % (s / 60) if s < 5400 else '%.1f h ago' % (s / 3600)


class IconButton(tk.Button):
    """A button with a drawn symbol next to its text. Tk draws the picture of a switched-off button through a dot pattern, which
    looks broken; so for Tk this button is never switched off: state='disabled' fades its text and symbol and stops its command."""

    def __init__(self, parent, command, images, colours, **kw):
        self._off, self._cmd, self._images, self._colours = False, command, images, colours      # images, colours: (on, off)
        super().__init__(parent, command=self._press, image=images[0], fg=colours[0], activeforeground=colours[0], **kw)

    def _press(self):
        if not self._off and self._cmd:
            self._cmd()

    def configure(self, cnf=None, **kw):
        kw = dict(cnf or {}, **kw)
        if 'state' in kw:
            off = kw.pop('state') == 'disabled'
            if off is not self._off:
                self._off = off; k = int(off)
                kw.update(image=self._images[k], fg=self._colours[k], activeforeground=self._colours[k], cursor='arrow' if off else 'hand2')
        return super().configure(**kw) if kw else None
    config = configure


class Net:
    """polls the robot's agent and the PC's receiver in the background; the window only reads the newest answers"""

    def __init__(self, agent, receiver, token):
        self.agent, self.receiver, self.token = agent, receiver, token
        self.a, self.a_at, self.a_err, self.a_ms = None, 0.0, 'not tried yet', 0
        self.r, self.r_at, self.r_err = None, 0.0, 'not tried yet'
        threading.Thread(target=self._poll, args=('a', agent + '/status', 0.5), daemon=True).start()
        threading.Thread(target=self._poll, args=('r', receiver + '/state', 0.5), daemon=True).start()
        self.want_cam, self.cam, self.cam_n = False, None, 0       # head-camera frames, fetched only while the window asks
        threading.Thread(target=self._poll_cam, daemon=True).start()

    def _poll_cam(self):
        while True:
            if not self.want_cam:
                self.cam = None; time.sleep(0.4)
                continue
            t0 = time.time()
            try:
                req = urllib.request.Request(self.agent + '/camera.jpg', headers={'X-Orio-Token': self.token})
                with urllib.request.urlopen(req, timeout=2.0) as r:
                    self.cam = r.read(); self.cam_n += 1
            except Exception:
                self.cam = None
            time.sleep(max(0.02, 0.16 - (time.time() - t0)))

    def _poll(self, key, url, period):
        while True:
            t0 = time.time()
            try:
                d = http(url, token=self.token)
                setattr(self, key, d); setattr(self, key + '_at', time.time()); setattr(self, key + '_err', None)
                if key == 'a':
                    self.a_ms = int((time.time() - t0) * 1000)
            except urllib.error.HTTPError as e:
                setattr(self, key + '_err', 'refused (%d): wrong token?' % e.code)
            except Exception as e:
                setattr(self, key + '_err', getattr(e, 'reason', None) and str(e.reason) or str(e))
            time.sleep(max(0.05, period - (time.time() - t0)))

    def robot_up(self):
        return time.time() - self.a_at < 3.0

    def pc_up(self):
        return time.time() - self.r_at < 4.0

    def cmd(self, c, done=None, timeout=20.0):
        def run():
            try:
                rep = http(self.agent + '/cmd', c, self.token, timeout)
            except Exception as e:
                rep = {'ok': False, 'message': 'robot not reachable: %s' % e}
            if done:
                done(c, rep)
        threading.Thread(target=run, daemon=True).start()

    def pc_post(self, path, payload, done=None):
        """something for the PC's receiver (a PAR, for one)"""
        def run():
            try:
                rep = http(self.receiver + path, payload, self.token, 4.0)
            except Exception as e:
                rep = {'ok': False, 'error': str(e)}
            if done:
                done(rep)
        threading.Thread(target=run, daemon=True).start()


class App:
    def __init__(self, root, conf, net, view='2d', prefs=None, size=(BASE_W, BASE_H)):
        self.root, self.conf, self.net, self.start_view = root, conf, net, view
        self.prefs = prefs if prefs is not None else load_prefs()
        P.use(self.prefs.get('theme', 'dark'))
        self.screen = self.prefs.get('screen') if self.prefs.get('screen') in ('operate', 'inventory') else 'operate'
        try:
            self.zoom = min(max(float(self.prefs.get('zoom', 1.0)), 0.6), 1.8)
        except (TypeError, ValueError):
            self.zoom = 1.0
        self.preflight = None            # the last preflight result (the agent only remembers its newest job)
        self.held, self.msgs = {}, []
        self.jog_last, self.jog_msg = {}, None
        self.band = ' '.join(str(conf.get('FEATURE_BAND') or '').replace('"', ' ').replace("'", ' ').split())      # "LO HI" m, or ''
        lo, _, hi = self.band.partition(' ')
        try:                                 # the lift wave's speed for a run, m/s; 0 = not sent, the run script's own
            self.lift_speed = float(conf.get('LIFT_SPEED') or 0)
        except ValueError:
            self.lift_speed = 0.0
        self.band_text = ('up to %s' % hi) if lo.startswith('-') else ('%s to %s' % (lo, hi))       # a low end under the floor: no low end
        self.family, self.mono = ui.families(root, conf.get('FONT'))     # before the map is made: it draws its text with the same font
        ui.FAMILY, self.font_sys = self.family, ui.font_system(root)
        self.view = self.scene_data = self.map_sig = self.sel_uid = self.sel_name = self.hover_uid = self.mdrag = self._kick_id = self._settle_id = None
        self.cam_was_ok, self.map_view = False, self.start_view
        self.shell = self.pad = self.card = self._resize_id = self._card_sig = None
        self.checks, self.metric_w, self._boxes, self._err = {}, {}, [], None
        site = os.path.join(HERE, '..', 'loc', 'site')
        if scene_view is not None:
            try:
                self.view = scene_view.SceneView(scene_view.Scene(site))
            except Exception as e:           # no scene files for this site: the plain route map is used
                print('map: %r - drawing the route only' % e)
        try:                                 # tape-measured positions, for the metrics (the same file the map's diamonds come from)
            self.truth = M.load_truth(os.path.join(site, json.load(open(os.path.join(site, 'live_site_v2.json')))['ground_truth']))
        except (OSError, ValueError, KeyError):
            self.truth = {}
        db = os.path.expanduser(conf.get('DB') or '~/orio_scan/master.db')
        # which tags are the inventory (the registry's item numbers) and what they are called (the names file, next to the database)
        self.names_file = os.path.expanduser(conf.get('NAMES') or os.path.join(os.path.dirname(db), 'item_names.csv'))
        if not os.path.exists(self.names_file):
            try:                                 # a first one: the names known on 14 Nov if this checkout has them, else only the heading
                start = os.path.join(HERE, 'item_names.start.csv')
                open(self.names_file, 'w', encoding='utf-8').write(open(start, encoding='utf-8').read() if os.path.exists(start) else M.NAMES_HEAD)
            except OSError:
                pass
        self.cat = M.Catalog(os.path.join(HERE, '..', 'RFID', 'tag_registry.csv'), self.names_file)
        self._st = self._st_r = None; self._st_ver = self._names_said = -1
        self._lift_said = None
        self.photo_dir = os.path.expanduser(conf.get('PHOTOS') or os.path.join(os.path.dirname(db), 'item_photos'))
        try:
            os.makedirs(self.photo_dir, exist_ok=True)
        except OSError:
            pass
        self.photos = ui.Photos(self.photo_dir)
        try:
            self.soon_days = int(conf.get('EXPIRY_SOON_DAYS') or M.SOON_DAYS)
        except ValueError:
            self.soon_days = M.SOON_DAYS
        self.f = {n: tkfont.Font(family=self.mono if n == 'mono' else self.family, size=s, weight=w) for n, s, w in FONTS}
        self.tk_fonts = {}                   # Tk's own fonts (its dialogs and menus use them) and their plain sizes
        for n in ('TkDefaultFont', 'TkTextFont', 'TkMenuFont', 'TkCaptionFont', 'TkHeadingFont'):
            try:
                self.tk_fonts[n] = tkfont.nametofont(n).cget('size')
                if self.family != 'TkDefaultFont':
                    tkfont.nametofont(n).configure(family=self.family)
            except tk.TclError:
                pass
        # the variables live as long as the window; the widgets are built again when the theme, the screen or the size changes
        c = conf
        self.v_src, self.v_replay = tk.StringVar(value='robot'), tk.StringVar()
        self.v_laps, self.v_at = tk.StringVar(value=c.get('LAPS', '1')), tk.StringVar(value=c.get('START_AT', '1'))
        self.v_keep = tk.BooleanVar(value=True)       # unticked before START: the run starts from nothing (see start())
        self.v_method, self.v_view, self.v_speed = tk.StringVar(value=c.get('METHOD', 'F4')), tk.StringVar(value=self.start_view), tk.DoubleVar(value=0.6)
        self.v_cloud, self.v_regions, self.v_truth = tk.BooleanVar(value=True), tk.BooleanVar(value=False), tk.BooleanVar(value=True)
        shown = self.prefs.get('metrics') if isinstance(self.prefs.get('metrics'), list) else M.METRICS_DEFAULT
        self.v_metric = {k: tk.BooleanVar(value=k in shown) for k, _ in M.METRICS}
        self.inv = InventoryPanel(self, sorted(self.view.s.shelves) if self.view is not None else ())
        root.title('ORIO scan'); root.minsize(1000, 700)
        self.s = self._scale_for(*size)
        self._fonts(); self._rebuild()
        root.bind('<Escape>', lambda e: self.estop())
        root.bind('<F2>', lambda e: self.set_screen('operate' if self.screen == 'inventory' else 'inventory'))
        for seq, k in (('<Control-plus>', 1.1), ('<Control-equal>', 1.1), ('<Control-KP_Add>', 1.1), ('<Control-minus>', 1 / 1.1),
                       ('<Control-KP_Subtract>', 1 / 1.1), ('<Control-Key-0>', 0)):
            root.bind(seq, lambda e, k=k: self.set_zoom(self.zoom * k if k else 1.0))
        for k in DRIVE_KEYS + JOG_KEYS:
            root.bind('<KeyPress-%s>' % k, lambda e, k=k: self._key(k, True)); root.bind('<KeyRelease-%s>' % k, lambda e, k=k: self._key(k, False))
        root.bind('<Configure>', self._on_configure)
        root.protocol('WM_DELETE_WINDOW', self.close)
        self._tick(); self._drive_tick()

    # ------------------------------------------------------------------ size, theme, screen
    def px(self, n):
        """a length in pixels at the window's scale"""
        return int(round(n * self.s)) if n else 0

    def _scale_for(self, w, h):
        s = min(w / BASE_W, h / BASE_H) * self.zoom
        return round(min(max(s, 0.8), 3.2) * 20) / 20.0

    def _fonts(self):
        for n, size, _ in FONTS:
            self.f[n].configure(size=max(6, int(round(size * self.s))))
        for n, size in self.tk_fonts.items():           # negative = pixels: the sign stays
            tkfont.nametofont(n).configure(size=int(round(size * self.s)) or size)

    def _on_configure(self, e):
        if e.widget is not self.root:
            return
        self._size = (e.width, e.height)
        if self._resize_id is not None:
            self.root.after_cancel(self._resize_id)
        self._resize_id = self.root.after(220, self._resized)       # once the dragging has stopped

    def _resized(self):
        self._resize_id = None
        s = self._scale_for(*self._size)
        if abs(s - self.s) > 0.02:
            self.s = s; self._fonts(); self._rebuild()

    def set_zoom(self, zoom):
        self.zoom = min(max(zoom, 0.6), 1.8); self._save_prefs()
        self._size = (self.root.winfo_width(), self.root.winfo_height()); self.s = 0.0; self._resized()

    def set_screen(self, key):
        if key != self.screen:
            self.screen = key; self._save_prefs(); self._rebuild()

    def toggle_theme(self):
        P.use('light' if P.name == 'dark' else 'dark'); self._save_prefs(); self._rebuild()

    def _save_prefs(self, size=False):
        self.prefs.update(theme=P.name, screen=self.screen, zoom=round(self.zoom, 3), metrics=[k for k, _ in M.METRICS if self.v_metric[k].get()])
        if size and self.root.winfo_width() > 200:
            self.prefs['size'] = '%dx%d' % (self.root.winfo_width(), self.root.winfo_height())
        try:
            json.dump(self.prefs, open(PREFS, 'w'), indent=1)
        except OSError:
            pass

    def _rebuild(self):
        """build every widget again, for this theme, screen and scale. What the window knows is kept; nothing is held down after it."""
        if self.held:
            self.held.clear(); self._jog()
        self.inv.forget()
        if self.shell is not None:
            self.shell.destroy()
        self.pad = self.card = self._card_sig = None
        self.checks, self.metric_w, self._boxes = {}, {}, []
        self.root.configure(bg=P.CRUST)
        self.shell = tk.Frame(self.root, bg=P.CRUST); self.shell.pack(fill='both', expand=True)
        self._style(); self._build()
        self.map_sig = self._log_text = self.cam_shown = None
        if self.view is not None:
            self.view.set_style(P.name, self.s)
        self._paint_boxes()
        try:
            self._draw()
        except Exception:
            pass                             # the next tick reports it

    def _style(self):
        st, px = ttk.Style(), self.px
        st.theme_use('clam')
        st.configure('Inv.Treeview', background=P.FIELD, fieldbackground=P.FIELD, foreground=P.TEXT, font=self.f['small'], rowheight=px(22), borderwidth=0)
        st.configure('Inv.Treeview.Heading', background=P.SURF0, foreground=P.SUBTEXT, font=self.f['small'], relief='flat', padding=(px(4), px(2)))
        st.map('Inv.Treeview', background=[('selected', P.SURF1)], foreground=[('selected', P.TEXT)])
        st.map('Inv.Treeview.Heading', background=[('active', P.SURF1)])
        st.configure('TCombobox', fieldbackground=P.SURF0, background=P.SURF1, foreground=P.TEXT, arrowcolor=P.TEXT, bordercolor=P.SURF1,
                     lightcolor=P.SURF0, darkcolor=P.SURF0, selectbackground=P.SURF0, selectforeground=P.TEXT, arrowsize=px(12))
        st.map('TCombobox', fieldbackground=[('readonly', P.SURF0)], foreground=[('readonly', P.TEXT)], selectbackground=[('readonly', P.SURF0)],
               selectforeground=[('readonly', P.TEXT)])
        st.configure('Vertical.TScrollbar', background=P.SURF1, troughcolor=P.MANTLE, bordercolor=P.MANTLE, lightcolor=P.SURF1, darkcolor=P.SURF1,
                     arrowcolor=P.TEXT, arrowsize=px(12))
        st.map('Vertical.TScrollbar', background=[('active', P.SURF2)])
        for k, v in (('background', P.SURF0), ('foreground', P.TEXT), ('selectBackground', P.SURF2), ('selectForeground', P.TEXT), ('font', self.f['small'])):
            self.root.option_add('*TCombobox*Listbox.' + k, v)

    # ------------------------------------------------------------------ widgets
    def _btn(self, parent, text, cmd, bg, fg=None, font='btn', icon=None, **kw):
        fg = fg or P.ON
        if icon:                             # a drawn symbol left of the text, as high as the text's capitals
            # Tk puts padx between the picture and the text as well as outside them. So padx becomes the small gap wanted
            # between the two, and the rest of the outer padding is empty margin in the picture and spaces after the text.
            pad, gap = self.px(kw.pop('padx', 12)), self.px(7)
            size, faded = max(8, int(round(self.f[font].metrics('ascent') * 0.78))), ui.mix(bg, fg, 0.42)
            imgs = [ui.icon(icon, size, c, margin=(max(0, pad - gap), 0)) for c in (fg, faded)]
            if imgs[0] is not None:
                text += ' ' * int(round(max(0, pad - gap) / max(1, self.f[font].measure(' '))))
                return IconButton(parent, cmd, imgs, (fg, faded), text=text, compound='left', font=self.f[font], relief='flat', bd=0, bg=bg, activebackground=bg,
                                  cursor='hand2', padx=gap, pady=self.px(kw.pop('pady', 6)), highlightthickness=0, **kw)
        # a button that is switched off keeps its colour; its text fades into it (grey text on a coloured button is unreadable)
        return tk.Button(parent, text=text, command=cmd, font=self.f[font], relief='flat', bd=0, bg=bg, fg=fg, activebackground=bg,
                         activeforeground=fg, disabledforeground=ui.mix(bg, fg, 0.42), cursor='hand2', padx=self.px(kw.pop('padx', 12)), pady=self.px(kw.pop('pady', 6)),
                         highlightthickness=0, **kw)

    def close_x(self, parent, colour):
        """a small drawn cross to click (a word where Pillow is missing)"""
        img = ui.icon('x', max(8, self.px(9)), colour)
        return tk.Label(parent, bg=P.MANTLE, cursor='hand2', **(dict(image=img) if img is not None else dict(text='close', font=self.f['small'], fg=colour)))

    def _panel(self, parent, title):
        fr = tk.Frame(parent, bg=P.MANTLE, highlightbackground=P.SURF0, highlightthickness=1)
        tk.Label(fr, text=title, font=self.f['h'], bg=P.MANTLE, fg=P.SUBTEXT, anchor='w').pack(fill='x', padx=self.px(10), pady=(self.px(8), self.px(4)))
        return fr

    def cbox(self, parent, text, var, command=None, font='small', pad=5):
        """a checkbox: a small drawn box of one fixed size (ui.BOX pixels at every window size) and its text"""
        lab = tk.Label(parent, font=self.f[font], bg=P.MANTLE, cursor='hand2', padx=self.px(pad), compound='left')

        def click(_e):
            var.set(not var.get()); self._paint_boxes()
            if command:
                command()
        lab.bind('<Button-1>', click)
        self._boxes.append((lab, lambda: bool(var.get()), text, 'box_on', 'box'))
        return lab

    def rbox(self, parent, text, var, value, font='b'):
        """the same for a radio button"""
        lab = tk.Label(parent, font=self.f[font], bg=P.MANTLE, cursor='hand2', padx=self.px(2), anchor='w', compound='left')
        lab.bind('<Button-1>', lambda e: (var.set(value), self._paint_boxes()))
        self._boxes.append((lab, lambda: var.get() == value, text, 'radio_on', 'radio'))
        return lab

    def _paint_boxes(self):
        for lab, is_on, text, yes, no in self._boxes:
            on = is_on()
            if getattr(lab, '_on', None) is not on:
                lab._on = on
                img = ui.icon(yes, ui.BOX, P.ON, P.BLUE) if on else ui.icon(no, ui.BOX, P.OVERLAY)
                if img is not None:
                    lab.configure(image=img, text=' ' + text, fg=P.TEXT if on else P.SUBTEXT)
                else:                        # no Pillow: words instead of a box
                    lab.configure(text=('[x] ' if on else '[  ] ') + text, fg=P.TEXT if on else P.SUBTEXT)

    def _build(self):
        px, f, sh = self.px, self.f, self.shell
        # ---- top bar: the same on both screens
        top = tk.Frame(sh, bg=P.BASE); top.pack(fill='x')
        tk.Label(top, text='ORIO  scan', font=f['title'], bg=P.BASE, fg=P.TEXT).pack(side='left', padx=(px(14), px(12)), pady=px(10))
        seg = tk.Frame(top, bg=P.SURF0); seg.pack(side='left', padx=(0, px(14)))
        for key, text in (('inventory', 'INVENTORY'), ('operate', 'OPERATE')):
            on = self.screen == key
            self._btn(seg, text, lambda k=key: self.set_screen(k), P.BLUE if on else P.SURF0, fg=P.ON if on else P.TEXT, font='h', padx=12, pady=6).pack(side='left')
        self.phase = tk.Label(top, text='IDLE', font=f['btn'], bg=P.SURF2, fg=P.TEXT, padx=px(12), pady=px(4)); self.phase.pack(side='left')
        self.sub = tk.Label(top, text='', font=f['small'], bg=P.BASE, fg=P.SUBTEXT); self.sub.pack(side='left', padx=px(10))
        self.b_estop = self._btn(top, 'E-STOP', self.estop, P.ESTOP, fg='white', icon='stop', padx=20, pady=10); self.b_estop.pack(side='right', padx=(px(6), px(14)), pady=px(8))
        self.b_stop = self._btn(top, 'STOP', lambda: self.send({'cmd': 'stop'}), P.PEACH); self.b_stop.pack(side='right', padx=px(4))
        self.b_pause = self._btn(top, 'PAUSE', self.pause, P.YELLOW); self.b_pause.pack(side='right', padx=px(4))
        self.b_start = self._btn(top, 'START', self.start, P.GREEN, icon='play'); self.b_start.pack(side='right', padx=px(4))
        self._btn(top, 'LIGHT' if P.name == 'dark' else 'DARK', self.toggle_theme, P.SURF0, fg=P.TEXT, font='small', padx=8, pady=4).pack(side='right', padx=(px(4), px(14)))
        links = tk.Frame(sh, bg=P.CRUST); links.pack(fill='x', padx=px(10), pady=(px(6), 0))
        self.links = {}
        for key, name in (('robot', 'ROBOT'), ('pc', 'PC RECEIVER'), ('reads', 'READS TO PC'), ('loc', 'LOCALISER')):
            self.links[key] = tk.Label(links, text=name, font=f['small'], bg=P.SURF0, fg=P.TEXT, padx=px(10), pady=px(3))
            self.links[key].pack(side='left', padx=(0, px(6)))
        self.status = tk.Label(links, text='', font=f['small'], bg=P.CRUST, fg=P.SUBTEXT, anchor='e'); self.status.pack(side='right', fill='x', expand=True)
        if self.font_sys == 'x11':           # stays until the first message: rough fonts are this, not the window
            self.status.configure(text='this Python\'s Tk has no Xft: rough fonts. Start the window with run_scan_demo.sh, or with /usr/bin/python3', fg=P.RED)
        body = tk.Frame(sh, bg=P.CRUST); body.pack(fill='both', expand=True, padx=px(10), pady=px(8))
        body.rowconfigure(0, weight=1)
        # The parts of the screen that is not showing are built all the same, into a frame that is never put on the screen:
        # the drawing code then has every widget it sets, whichever screen is up, and the pad's keys cannot fire unseen.
        unseen = tk.Frame(sh, bg=P.CRUST)
        if self.screen == 'operate':
            body.columnconfigure(1, weight=1)
            left = tk.Frame(body, bg=P.CRUST); left.grid(row=0, column=0, sticky='ns')
            mid = tk.Frame(body, bg=P.CRUST); mid.grid(row=0, column=1, sticky='nsew', padx=(px(8), 0))
            self._build_setup(left); self._build_checks(left); self._build_log(left)
            self._build_manual(mid); self._build_map(mid, False)
        else:
            body.columnconfigure(0, weight=3, uniform='screen'); body.columnconfigure(1, weight=2, uniform='screen')
            mid = tk.Frame(body, bg=P.CRUST); mid.grid(row=0, column=0, sticky='nsew', padx=(0, px(8)))
            right = tk.Frame(body, bg=P.CRUST); right.grid(row=0, column=1, sticky='nsew')
            self._build_setup(unseen); self._build_checks(unseen); self._build_log(unseen); self._build_manual(unseen)
            self._build_map(mid, True); self.inv.build(right)

    def _build_setup(self, left):
        px, c = self.px, self.conf
        p = self._panel(left, 'SETUP'); p.pack(fill='x')
        g = tk.Frame(p, bg=P.MANTLE); g.pack(fill='x', padx=px(10), pady=(0, px(8)))
        self.rbox(g, 'Robot drives (real run)', self.v_src, 'robot').grid(row=0, column=0, columnspan=4, sticky='w')
        self.rbox(g, 'Replay an old run (no motion)', self.v_src, 'replay').grid(row=1, column=0, columnspan=4, sticky='w')
        self.cb_replay = ttk.Combobox(g, textvariable=self.v_replay, state='readonly', width=30, font=self.f['small'])
        self.cb_replay.grid(row=2, column=0, columnspan=4, sticky='we', pady=(px(2), px(6)))
        lab = dict(bg=P.MANTLE, fg=P.SUBTEXT, font=self.f['small'])
        tk.Label(g, text='Map', **lab).grid(row=3, column=0, sticky='w')
        tk.Label(g, text=c.get('MAP', '?'), bg=P.MANTLE, fg=P.TEXT, font=self.f['small']).grid(row=3, column=1, columnspan=3, sticky='w')
        self.cbox(g, 'keep what earlier runs found', self.v_keep, pad=0).grid(row=8, column=0, columnspan=4, sticky='w', pady=(px(4), 0))
        tk.Label(g, text='Lift wave', **lab).grid(row=7, column=0, sticky='w', padx=(0, px(6)))
        tk.Label(g, text=('%.2f m/s up and down' % self.lift_speed) if self.lift_speed else 'the run script\'s own speed', bg=P.MANTLE, fg=P.TEXT,
                 font=self.f['small']).grid(row=7, column=1, columnspan=3, sticky='w')
        tk.Label(g, text='Features', **lab).grid(row=6, column=0, sticky='w', padx=(0, px(6)))       # what ORB-SLAM3 localises on
        tk.Label(g, text=('%s m above the floor' % self.band_text) if self.band else 'at any height', bg=P.MANTLE, fg=P.TEXT,
                 font=self.f['small']).grid(row=6, column=1, columnspan=3, sticky='w')
        tk.Label(g, text='Laps', **lab).grid(row=4, column=0, sticky='w', pady=px(2))
        sp = dict(width=4, font=self.f['b'], bg=P.SURF0, fg=P.TEXT, buttonbackground=P.SURF1, relief='flat', insertbackground=P.TEXT, highlightthickness=0)
        tk.Spinbox(g, from_=1, to=9, textvariable=self.v_laps, **sp).grid(row=4, column=1, sticky='w')
        tk.Label(g, text='Start on', **lab).grid(row=4, column=2, sticky='e', padx=(px(8), px(4)))
        tk.Spinbox(g, from_=1, to=20, textvariable=self.v_at, **sp).grid(row=4, column=3, sticky='w')
        tk.Label(g, text='Method', **lab).grid(row=5, column=0, sticky='w')
        mf = tk.Frame(g, bg=P.MANTLE); mf.grid(row=5, column=1, columnspan=3, sticky='w')
        for m in ('F1', 'F2', 'F3', 'F4'):
            self.rbox(mf, m, self.v_method, m, font='small').pack(side='left')
        bf = tk.Frame(p, bg=P.MANTLE); bf.pack(fill='x', padx=px(10), pady=(0, px(10)))
        self.b_pre = self._btn(bf, 'PREFLIGHT', lambda: self.send({'cmd': 'preflight'}), P.BLUE, font='h', padx=8); self.b_pre.pack(side='left')
        self.b_home = self._btn(bf, 'HOME', self.home, P.MAUVE, font='h', padx=8); self.b_home.pack(side='left', padx=px(6))
        self.b_clean = self._btn(bf, 'CLEAN UP', lambda: self.send({'cmd': 'clean'}), P.SURF1, fg=P.TEXT, font='h', padx=8); self.b_clean.pack(side='left')

    def _build_checks(self, left):
        px = self.px
        p = self._panel(left, 'CHECKS'); p.pack(fill='x', pady=px(8))
        for key, name in (('agent', 'Robot agent'), ('pc', 'PC receiver'), ('teleop', 'Teleop server'), ('pre', 'Preflight'),
                          ('nav', 'Navigation'), ('reader', 'RFID reader'), ('pose', 'Antenna pose'), ('stream', 'Reads at the PC'),
                          ('route', 'Route'), ('presence', 'Presence'), ('engine', 'Localiser'), ('loc', 'Locations')):
            row = tk.Frame(p, bg=P.MANTLE); row.pack(fill='x', padx=px(10), pady=px(1))
            d = px(12)
            dot = tk.Canvas(row, width=d, height=d, bg=P.MANTLE, highlightthickness=0); dot.pack(side='left')
            oid = dot.create_oval(px(2), px(2), d - 1, d - 1, fill=P.SURF2, outline='')
            tk.Label(row, text=name, font=self.f['small'], bg=P.MANTLE, fg=P.TEXT, width=13, anchor='w').pack(side='left', padx=(px(4), 0))
            val = tk.Label(row, text='', font=self.f['small'], bg=P.MANTLE, fg=P.SUBTEXT, anchor='w', width=30); val.pack(side='left')
            self.checks[key] = (dot, oid, val)
        tk.Frame(p, bg=P.MANTLE, height=px(6)).pack()

    def _build_log(self, left):
        p = self._panel(left, 'LOG'); p.pack(fill='both', expand=True)
        self.log = tk.Text(p, height=5, width=20, bg=P.FIELD, fg=P.SUBTEXT, font=self.f['mono'], relief='flat', wrap='none', state='disabled', highlightthickness=0)
        self.log.pack(fill='both', expand=True, padx=self.px(10), pady=(0, self.px(10)))
        self.log.tag_configure('msg', foreground=P.YELLOW); self.log.tag_configure('err', foreground=P.RED)

    def _build_manual(self, mid):
        px = self.px
        p = self._panel(mid, 'MANUAL CONTROL'); p.pack(side='bottom', fill='x', pady=(px(8), 0))
        mrow = tk.Frame(p, bg=P.MANTLE); mrow.pack(fill='x', padx=px(10))
        self.b_manual = self._btn(mrow, 'MANUAL ON', self.manual, P.SURF1, fg=P.TEXT, font='h', padx=8); self.b_manual.pack(side='left')
        self.b_rel = self._btn(mrow, 'RELEASE RUNSTOP', lambda: self.send({'cmd': 'runstop', 'on': False}), P.SURF1, fg=P.TEXT, font='h', padx=8)
        self.b_rel.pack(side='left', padx=px(6))
        self.b_pose = self._btn(mrow, 'POSE AGAIN', self.pose, P.SURF1, fg=P.TEXT, font='h', padx=8)
        self.b_pose.pack(side='left')
        pad = tk.Frame(p, bg=P.MANTLE); pad.pack(padx=px(10), pady=px(6), anchor='w')
        self.pad = pad
        lab = dict(bg=P.MANTLE, fg=P.SUBTEXT, font=self.f['small'])
        def sym(parent, kind, size, text, font='btn', **kw):       # a button face: the drawn symbol, or a word where Pillow is missing
            img = ui.icon(kind, px(size), P.TEXT)
            if img is None:
                return tk.Label(parent, text=text, font=self.f['small'], bg=P.SURF0, fg=P.TEXT, cursor='hand2')
            return tk.Label(parent, image=img, bg=P.SURF0, cursor='hand2', **kw)
        for kind, key, r, col, word in (('turn_l', 'q', 0, 0, 'turn L'), ('up', 'w', 0, 1, 'fwd'), ('turn_r', 'e', 0, 2, 'turn R'), ('left', 'a', 1, 0, 'left'),
                                        ('down', 's', 1, 1, 'back'), ('right', 'd', 1, 2, 'right')):
            b = sym(pad, kind, 15 if kind.startswith('turn') else 12, word, width=px(38), height=px(28))
            b.grid(row=r, column=col, padx=px(2), pady=px(2))
            b.bind('<ButtonPress-1>', lambda e, k=key: self._hold(k, True)); b.bind('<ButtonRelease-1>', lambda e, k=key: self._hold(k, False))

        def hold_btn(parent, kind, key, size=10, **kw):          # moves only while pressed, like the base buttons
            b = sym(parent, kind, size, kind, **kw)
            b.bind('<ButtonPress-1>', lambda e: self._hold(key, True)); b.bind('<ButtonRelease-1>', lambda e: self._hold(key, False))
            return b
        lf = tk.Frame(pad, bg=P.MANTLE); lf.grid(row=0, column=3, rowspan=2, padx=(px(14), 0))
        tk.Label(lf, text='Lift', **lab).pack()
        hold_btn(lf, 'up', 'r', width=px(32), height=px(20)).pack(pady=px(1))
        self.l_lift = tk.Label(lf, text='– m', font=self.f['small'], bg=P.MANTLE, fg=P.TEXT); self.l_lift.pack()
        hold_btn(lf, 'down', 'f', width=px(32), height=px(20)).pack(pady=px(1))
        sf = tk.Frame(pad, bg=P.MANTLE); sf.grid(row=0, column=4, rowspan=2, padx=(px(14), 0))
        tk.Label(sf, text='Speed', **lab).pack()
        tk.Scale(sf, from_=1.0, to=0.2, resolution=0.1, variable=self.v_speed, orient='vertical', length=px(70), showvalue=False, bg=P.MANTLE, fg=P.TEXT,
                 troughcolor=P.SURF0, highlightthickness=0, bd=0, sliderlength=px(16), width=px(10)).pack()
        cf = tk.Frame(pad, bg=P.MANTLE); cf.grid(row=0, column=5, rowspan=2, padx=(px(14), 0))          # head camera: turn, tilt, back to usual
        tk.Label(cf, text='Camera', **lab).grid(row=0, column=0, columnspan=3)
        for kind, r, col, key in (('up', 1, 1, 'i'), ('left', 2, 0, 'j'), ('right', 2, 2, 'l'), ('down', 3, 1, 'k')):
            hold_btn(cf, kind, key, size=8, width=px(22), height=px(16)).grid(row=r, column=col, padx=1, pady=1)
        hb = sym(cf, 'home', 10, 'home', width=px(22), height=px(16)); hb.grid(row=2, column=1, padx=1, pady=1)
        hb.bind('<Button-1>', lambda e: self.send({'cmd': 'head', 'home': True}))
        self.l_robot = tk.Label(p, text='', font=self.f['small'], bg=P.MANTLE, fg=P.SUBTEXT, anchor='w', justify='left'); self.l_robot.pack(fill='x', padx=px(10), pady=(0, px(8)))

    def _build_map(self, mid, metrics):
        """the 2D / 3D room with the route and the items, or the head camera in manual mode; on the inventory screen with the metrics row"""
        px, f = self.px, self.f
        p = tk.Frame(mid, bg=P.MANTLE, highlightbackground=P.SURF0, highlightthickness=1); p.pack(fill='both', expand=True)
        head = tk.Frame(p, bg=P.MANTLE); head.pack(fill='x', padx=px(10), pady=(px(8), px(4)))
        tk.Label(head, text='MAP', font=f['h'], bg=P.MANTLE, fg=P.SUBTEXT).pack(side='left', padx=(0, px(8)))
        self.view_btn = {}
        for key, text in (('2d', '2D'), ('3d', '3D'), ('cam', 'CAMERA')):
            b = self._btn(head, text, lambda k=key: self._set_view(k), P.SURF0, fg=P.TEXT, font='small', padx=8, pady=1)
            b.pack(side='left', padx=(0, px(3))); self.view_btn[key] = b
        if metrics:
            caret = ui.icon('caret', max(6, px(7)), P.TEXT)
            mb = tk.Menubutton(head, text='METRICS ' if caret is not None else 'METRICS', **(dict(image=caret, compound='right') if caret is not None else {}), font=f['small'], bg=P.SURF0, fg=P.TEXT, activebackground=P.SURF1, activeforeground=P.TEXT, relief='flat',
                               bd=0, padx=px(8), pady=px(1), cursor='hand2', highlightthickness=0)
            menu = tk.Menu(mb, tearoff=0, bg=P.MANTLE, fg=P.TEXT, activebackground=P.SURF1, activeforeground=P.TEXT, selectcolor=P.TEXT, font=f['small'], bd=1)
            for k, name in M.METRICS:
                menu.add_checkbutton(label=name.capitalize() if k != 'within' else 'Within 1 m³  (< %.2f m, tape positions)' % M.WITHIN_M,
                                     variable=self.v_metric[k], command=self._metrics_changed)
            menu.add_separator()
            menu.add_command(label='Show all', command=lambda: self._metrics_changed(True)); menu.add_command(label='Hide all', command=lambda: self._metrics_changed(False))
            mb['menu'] = menu; mb.pack(side='right')
        opts = tk.Frame(p, bg=P.MANTLE); opts.pack(fill='x', padx=px(6))
        for var, text in ((self.v_cloud, 'point cloud'), (self.v_regions, 'uncertainty'), (self.v_truth, 'tape positions')):
            self.cbox(opts, text, var, self._render_map).pack(side='left')
        if metrics:
            self._build_metrics(p)
        self.bar = tk.Canvas(p, bg=P.SURF0, highlightthickness=0, height=px(8)); self.bar.pack(side='bottom', fill='x', padx=px(10), pady=(px(4), px(10)))
        self.l_run = tk.Label(p, text='', font=f['b'], bg=P.MANTLE, fg=P.TEXT, anchor='w'); self.l_run.pack(side='bottom', fill='x', padx=px(10), pady=(px(6), 0))
        self.map = tk.Canvas(p, bg=P.FIELD, highlightthickness=0, height=px(200)); self.map.pack(fill='both', expand=True, padx=px(10))
        m = self.map
        m.bind('<ButtonPress-1>', lambda e: self._m_down(e, 1)); m.bind('<ButtonPress-3>', lambda e: self._m_down(e, 3))
        m.bind('<B1-Motion>', self._m_move); m.bind('<B3-Motion>', self._m_move)
        m.bind('<ButtonRelease-1>', self._m_up); m.bind('<ButtonRelease-3>', self._m_up)
        m.bind('<Double-Button-1>', self._m_reset); m.bind('<Motion>', self._m_hover); m.bind('<Leave>', lambda e: self._m_hover(None))
        m.bind('<MouseWheel>', lambda e: self._m_wheel(e, 1 if e.delta > 0 else -1))                    # Windows / macOS
        m.bind('<Button-4>', lambda e: self._m_wheel(e, 1)); m.bind('<Button-5>', lambda e: self._m_wheel(e, -1))   # X11

    def _build_metrics(self, p):
        """the metrics that are switched on, one tile each, under the map"""
        px, f = self.px, self.f
        shown = [(k, name) for k, name in M.METRICS if self.v_metric[k].get()]
        if not shown:
            return
        row = tk.Frame(p, bg=P.MANTLE); row.pack(side='bottom', fill='x', padx=px(10), pady=(0, px(10)))
        for n, (k, name) in enumerate(shown):
            row.columnconfigure(n, weight=1, uniform='metric')
            fr = tk.Frame(row, bg=P.SURF0); fr.grid(row=0, column=n, sticky='nsew', padx=(0 if n == 0 else px(6), 0))
            val = tk.Label(fr, text='–', font=f['big'], bg=P.SURF0, fg=P.TEXT, anchor='w'); val.pack(fill='x', padx=px(10), pady=(px(5), 0))
            lab = tk.Label(fr, text=name, font=f['small'], bg=P.SURF0, fg=P.TEXT, anchor='w', justify='left'); lab.pack(fill='x', padx=px(10))
            sub = tk.Label(fr, text='', font=f['small'], bg=P.SURF0, fg=P.SUBTEXT, anchor='nw', justify='left'); sub.pack(fill='x', padx=px(10), pady=(0, px(6)))
            self.metric_w[k] = (val, sub, lab)

        def wrap(e, n=len(shown)):
            # the line under each number wraps inside its tile. Taken from the ROW's width (which the window sets), not from
            # the tile's own: a tile whose text sets its width and whose width sets its text never comes to rest.
            w = max((e.width - (n - 1) * px(6)) // n - 2 * px(10) - 4, 40)
            for _, sub, lab in self.metric_w.values():
                if abs(int(str(sub.cget('wraplength'))) - w) > 2:
                    sub.configure(wraplength=w); lab.configure(wraplength=w)
        row.bind('<Configure>', wrap)

    def _metrics_changed(self, every=None):
        if every is not None:
            for v in self.v_metric.values():
                v.set(every)
        self._save_prefs(); self.root.after(1, self._rebuild)

    # ------------------------------------------------------------------ commands
    def say(self, text, err=False):
        self.msgs.append((time.strftime('%H:%M:%S'), text, err)); del self.msgs[:-6]
        self.status.configure(text=text, fg=P.RED if err else P.SUBTEXT)

    def send(self, c, **kw):
        self.net.cmd(c, lambda c, rep: self.root.after(0, self.say, '%s: %s' % (c['cmd'], rep.get('message', rep)), not rep.get('ok')), **kw)

    def run(self):
        return (self.net.a or {}).get('run') if self.net.robot_up() else None

    def _begin(self, cmd):
        """send the start command; with "keep what earlier runs found" unticked, first have the PC forget it (POST /fresh), and
        start only when it has: the run then begins with no last known places, no missed-scan counts, no old last-seen times"""
        if self.v_keep.get():
            return self.send(cmd)

        def cleared(rep):
            if rep.get('ok'):
                self.map_sig = None
                self.say('earlier runs cleared (%s positions, %s missed-scan counts); starting' % (rep.get('locations', '?'), rep.get('missed', '?')))
                self.send(cmd)
            else:
                self.say('NOT started: the PC could not clear the earlier runs (%s)' % str(rep.get('error', rep))[:80], True)
        self.net.pc_post('/fresh', {}, lambda rep: self.root.after(0, cleared, rep))

    def start(self):
        c, replay = self.conf, self.v_src.get() == 'replay'
        if replay:
            if not self.v_replay.get():
                return self.say('pick an old run to replay', True)
            return self._begin({'cmd': 'start', 'replay_from': self.v_replay.get(), 'speed': 1, 'method': self.v_method.get()})
        odd = bool(self.band) != str(c['MAP']).endswith(('_cap', '_band'))       # the band and a map built with it belong together
        if not messagebox.askyesno('Start the run', 'The robot will drive by itself.\n\n- parked on waypoint %s, facing as when it was marked\n'
                                   '- route clear, nobody next to the robot\n- preflight passed and homed this boot\n\n'
                                   'Map %s%s.\nLift wave: %s.\n%s.\n%s'
                                   'The teleop server is stopped automatically.\n\nStart?' % (
                                       self.v_at.get(), c['MAP'], ', features %s m above the floor only' % self.band_text if self.band else ', features at any height',
                                       ('%.2f m/s' % self.lift_speed) if self.lift_speed else 'the run script\'s own speed',
                                       'Earlier runs: kept (last known places, missed-scan counts)' if self.v_keep.get() else
                                       'Earlier runs: CLEARED when you press Yes (last known places, missed-scan counts, last-seen times)',
                                       'CHECK demo.conf first: FEATURE_BAND and a map built with it (..._cap) go together, both or neither.\n' if odd else ''),
                                   icon='warning'):
            return
        cmd = {'cmd': 'start', 'map': c['MAP'], 'run_dir': c['RUN_DIR'], 'waypoints': c['WAYPOINTS'], 'laps': int(self.v_laps.get()),
               'start_at': int(self.v_at.get()), 'method': self.v_method.get()}
        if self.band:                        # the lift range is not sent: the run script's own (0.17 .. 1.05 m) is the one to use
            cmd['feature_band'] = self.band
        if self.lift_speed:                  # the agent turns it into the wave's period for that range
            cmd['lift_speed'] = self.lift_speed
        self._begin(cmd)

    def pause(self):
        r = self.run()
        self.send({'cmd': 'resume' if r and r['nav']['paused'] else 'pause'})

    def estop(self):
        self.held.clear()
        self.send({'cmd': 'estop'}, timeout=5.0)

    def home(self):
        if messagebox.askyesno('Home with the payload', 'The lift goes to the very top of the mast and the wrist swings in.\n'
                               'Nothing above the arm, nobody near the robot.\n\nHome now?', icon='warning'):
            self.send({'cmd': 'home'})

    def manual(self):
        m = (self.net.a or {}).get('manual')
        if m and m['on']:
            return self.send({'cmd': 'manual', 'on': False}, timeout=40.0)       # the head goes back to its usual pose first
        if messagebox.askyesno('Manual mode', 'The driver starts (about 20 s). Then, by itself, the lift goes UP to the saved height,\n'
                               'the arm retracts, the wrist swings in (it stops 75 degrees short of stowed) and the head\n'
                               'goes to its usual pose.\n\n'
                               'Nothing above the arm, nobody near it. The teleop server is stopped.\n\nSwitch manual mode on?', icon='warning'):
            self.send({'cmd': 'manual', 'on': True}, timeout=30.0)

    def pose(self):
        if messagebox.askyesno('Pose again', 'The lift goes UP to the saved height first, then the arm retracts, the wrist swings in\n'
                               '(it stops 75 degrees short of stowed) and the head tilts down. Nothing above the arm, nobody near it.\n\nMove now?', icon='warning'):
            self.send({'cmd': 'pose'})

    def _hold(self, key, down):
        if down:
            self.held[key] = time.time()
        else:
            self.held.pop(key, None)
        self._jog()                      # the lift / camera start and stop at once, not at the next tick

    def _key(self, key, down):
        """keys move the robot only while the pointer is over the pad (so typing elsewhere never moves it). X11 repeats a
        held key as release + press pairs: a release only counts if no press follows within 60 ms."""
        x, y = self.root.winfo_pointerxy(); w = self.root.winfo_containing(x, y)
        over = self.pad is not None and w is not None and (w is self.pad or str(w).startswith(str(self.pad)))
        if down and over:
            self.held[key] = time.time()
        elif not down and key in self.held:
            t = self.held[key]
            self.root.after(60, lambda: (self.held.pop(key, None), self._jog()) if self.held.get(key) == t else None)

    def _jog(self):
        """lift (R / F) and camera (I J K L) buttons that are held, to the robot; once more with zeros when they are let go"""
        h = self.held
        j = {'lift': ('r' in h) - ('f' in h), 'pan': ('j' in h) - ('l' in h), 'tilt': ('i' in h) - ('k' in h)}
        if (any(j.values()) or any(self.jog_last.values())) and ((self.net.a or {}).get('manual') or {}).get('ready'):
            self.net.cmd(dict(j, cmd='jog', speed=self.v_speed.get()), self._jog_reply, timeout=1.0)
        self.jog_last = j

    def _jog_reply(self, c, rep):
        msg = None if rep.get('ok') else rep.get('message', str(rep))
        if msg != self.jog_msg:                               # five replies a second: only a new refusal is shown
            self.jog_msg = msg
            if msg:
                self.root.after(0, self.say, msg, True)

    def _drive_tick(self):
        h = self.held
        if any(k in h for k in DRIVE_KEYS) and (self.net.a or {}).get('can_drive'):
            s = self.v_speed.get()
            v = (V_STEP if 'w' in h else 0.0) - (V_STEP if 's' in h else 0.0)
            w = (W_STEP if 'a' in h or 'q' in h else 0.0) - (W_STEP if 'd' in h or 'e' in h else 0.0)
            self.net.cmd({'cmd': 'drive', 'v': round(v * s, 3), 'w': round(w * s, 3)}, timeout=1.0)
        self._jog()
        self.root.after(200, self._drive_tick)

    def close(self):
        r = self.run()
        if r and r['alive'] and not messagebox.askyesno('Close the window', 'A run is still going. It keeps going on the robot without this window.\n\nClose anyway?'):
            return
        self._save_prefs(size=True)
        self.root.destroy()


    # ------------------------------------------------------------------ drawing
    def _check(self, key, state, text):
        if key not in self.checks:
            return
        dot, oid, val = self.checks[key]
        dot.itemconfigure(oid, fill=P.DOT[state]); val.configure(text=text, fg=P.RED if state == 'bad' else P.SUBTEXT)

    def _tick(self):
        try:
            self._draw()
        except Exception as e:           # a drawing error must not stop the buttons from working
            self.status.configure(text='display error: %r' % e, fg=P.RED)
            if repr(e) != self._err:         # once per kind, where the launcher's terminal shows it
                self._err = repr(e); traceback.print_exc(file=sys.stderr)
        self.root.after(250, self._tick)

    def _draw(self):
        net, a, r = self.net, self.net.a or {}, self.state() or {}
        up, pc = net.robot_up(), net.pc_up()
        run = a.get('run') if up else None
        live = r.get('live') or {}
        alive = bool(run and run['alive'])
        phase = run['phase'] if run else 'idle'
        job = a.get('job') if up else None
        if job and job['name'] == 'preflight':
            self.preflight = job
        # top bar
        chip = P.PHASE.get(phase, P.BLUE) if up else P.RED
        self.phase.configure(text=phase.upper() if up else 'NO ROBOT', bg=chip, fg=P.on(chip))
        self.sub.configure(text=(run['label'] + ('   %d:%02d' % divmod(run['elapsed_s'], 60))) if run else '')
        busy = alive or bool(job and job['running'])
        self.b_start.configure(state='disabled' if busy or not up else 'normal')
        self.b_pause.configure(state='normal' if alive and not run['replay'] else 'disabled', text='RESUME' if run and run['nav']['paused'] else 'PAUSE')
        self.b_stop.configure(state='normal' if alive else 'disabled')
        for b in (self.b_pre, self.b_home, self.b_clean):
            b.configure(state='disabled' if busy or not up else 'normal')
        man = a.get('manual') or {}
        self.b_manual.configure(text='MANUAL OFF' if man.get('on') else 'MANUAL ON', bg=P.YELLOW if man.get('on') else P.SURF1, fg=P.ON if man.get('on') else P.TEXT,
                                state='disabled' if alive or not up else 'normal')
        rep = a.get('replayable') or []
        if tuple(self.cb_replay['values']) != tuple(rep):
            self.cb_replay['values'] = rep
            if rep and not self.v_replay.get():
                self.v_replay.set(rep[-1])
        # link pills
        reads_ok = pc and alive and live.get('reads_run') == run['label'] and live.get('reads_at') and time.time() - live['reads_at'] < 5
        loc_ok = pc and live.get('loc_at') and time.time() - live['loc_at'] < 30
        # the localiser reports to the receiver every round (about 2 s): heard within 20 s = it is running
        eng_age = (r.get('now', 0) - live['engine_at']) if pc and live.get('engine_at') else None
        eng_up = eng_age is not None and eng_age < 20 and not live.get('engine_error')
        eng_wanted = a.get('engine') == 'pc' or (a.get('engine') == 'robot' and alive)
        for key, ok, off in (('robot', up, False), ('pc', pc, False), ('reads', reads_ok, not alive or a.get('engine') != 'pc'),
                             ('loc', eng_up, not (up and pc and eng_wanted))):
            self.links[key].configure(bg=P.SURF0 if off and not ok else P.GREEN if ok else P.RED, fg=P.TEXT if off and not ok else P.ON)
        # checks
        C = self._check
        C('agent', 'ok' if up else 'bad', '%s, %d ms' % (a.get('host', '?'), net.a_ms) if up else str(net.a_err)[:34])
        C('pc', 'ok' if pc else 'bad', ('%d items' % r.get('n_items', 0) + (', %d unnumbered left out' % r['left_out'] if r.get('left_out') else '')) if pc
          else str(net.r_err)[:34])
        n_t = a.get('teleop_server', 0)
        C('teleop', 'off' if not up else 'wait' if n_t else 'ok', '' if not up else 'running (START stops it)' if n_t else 'stopped')
        pf = self.preflight
        if not pf:
            C('pre', 'off', 'not run from here yet')
        elif pf['running']:
            C('pre', 'wait', 'running, %d s' % pf['elapsed_s'])
        elif pf['rc'] == 0:
            C('pre', 'ok', 'passed')
        else:
            fix = [l.strip()[4:].strip() for l in pf['lines'] if l.strip().startswith('FIX')]
            C('pre', 'bad', '%d to fix: %s' % (len(fix), fix[0][:60] if fix else 'see the log'))
        rob = a.get('robot') or {}
        if not run:
            C('nav', 'off', ''); C('reader', 'off', ''); C('pose', 'off', ''); C('stream', 'off', ''); C('route', 'off', '')
        else:
            nav, rd, pose = run['nav'], run['reads'], run['pose']
            if run['replay']:
                C('nav', 'off', 'replay: no navigation')
            elif rob.get('localized'):
                C('nav', 'ok', 'localised in the map')
            elif rob.get('seeded'):
                C('nav', 'ok', 'on odometry from the start pose')
            else:
                C('nav', 'wait' if alive else 'off', phase if alive else '')
            started = rd['rows'] > 0
            C('reader', ('ok' if rd['rate'] > 100 else 'bad' if started else 'wait') if alive else 'off',
              '%d reads/s, %d tags' % (rd['rate'], rd['tags']) if alive else '%d reads, %d tags' % (rd['rows'], rd['tags']))
            lf = run.get('lift') or {}       # the lift's own account of its wave: it gives up after three strokes cut short by the contact stop
            if lf.get('state') == 'gave up':
                C('pose', 'bad', 'LIFT STOPPED WAVING at %s m' % ('%.2f' % lf['at_m'] if lf.get('at_m') is not None else '?'))
                if self._lift_said != run['label']:          # once per run, with the reason, where the log keeps it
                    self._lift_said = run['label']
                    self.say('the lift stopped waving: %s (%d strokes cut short). The antenna stays where it is; this run\'s positions will be poor' % (
                        (lf.get('why') or 'contact').rstrip('.'), lf.get('short') or 0), True)
            else:
                C('pose', ('ok' if pose and pose['age_s'] < 3 else 'bad' if started else 'wait') if alive else 'off',
                  ('antenna %.2f m up, %.1f s old' % (pose['ant_z'], pose['age_s']) + (', %d stroke%s cut short' % (lf['short'], '' if lf['short'] == 1 else 's') if lf.get('short') else ''))
                  if pose and alive else '')
            if a.get('engine') != 'pc':
                C('stream', 'off', 'not sent (localiser: %s)' % a.get('engine'))
            elif live.get('reads_run') == run['label']:
                lag = rd['rows'] - live.get('reads_rows', 0)
                C('stream', 'ok' if lag < 3000 else 'wait', '%d rows, %d behind' % (live.get('reads_rows', 0), max(lag, 0)))
            else:
                C('stream', 'wait' if alive and not started else 'bad', 'nothing received for this run')
            C('route', 'bad' if nav['fail'] else 'ok' if nav['done'] or nav['i'] else 'wait',
              '%d of %d reached%s%s' % (nav['ok'], nav['n'], ', %d FAILED' % nav['fail'] if nav['fail'] else '', ', %d retries' % nav['retries'] if nav['retries'] else ''))
        if pc:
            n_on, n_all = r.get('n_on_shelf', 0), r.get('n_items', 0)
            C('presence', 'ok' if n_on else 'wait' if alive else 'off', '%d of %d on shelf, update %s' % (n_on, n_all, ago(live.get('sightings_at'))))
            if not up or not eng_wanted:
                C('engine', 'off', 'off' if up and a.get('engine') == 'off' else 'starts with a run, on the robot' if up and a.get('engine') == 'robot' else '')
            elif eng_up:
                C('engine', 'ok', (live.get('engine_note') or 'running')[:60])
            elif eng_age is None:
                C('engine', 'bad', 'NOT RUNNING (never heard from it): logs/engine.log')
            else:
                C('engine', 'bad', ('%s' if live.get('engine_error') else 'NOT RUNNING, last heard %s: %%s' % ago(live['engine_at'])) % (live.get('engine_note') or '')[:70])
            # reads are flowing but no positions have come for half a minute: the localiser is down (or far behind)
            silent = alive and a.get('engine') != 'off' and run['reads']['rows'] > 5000 and not loc_ok
            C('loc', 'bad' if silent else 'ok' if loc_ok else 'wait' if alive and a.get('engine') != 'off' else 'off',
              'NO UPDATE for 30 s: see logs/engine.log' if silent else
              '%d placed (%s), %s' % (r.get('n_located', 0), live.get('loc_method') or '–', ago(live.get('loc_at'))) if live.get('loc_at') else 'localiser has not reported')
        else:
            C('presence', 'off', ''); C('loc', 'off', ''); C('engine', 'off', '')
        # manual + robot line
        self.l_lift.configure(text='%.2f m' % rob['lift_m'] if rob.get('lift_m') is not None else '– m')
        bits = []
        if man.get('on'):
            bits.append('manual: ' + ('moving ...' if man.get('moving') else 'ready, hold a pad button' if man.get('ready') else 'driver starting ...'))
        if man.get('on') and rob.get('arm_m') is not None and rob['arm_m'] > 0.05:
            bits.append('arm out %.2f m: the lift buttons wait for the pose move' % rob['arm_m'])
        for j, why in (rob.get('jog_blocked') or {}).items() if man.get('on') else ():
            bits.append('%s stopped: %s' % ('lift' if j == 'lift' else 'camera', why))
        if man.get('on') and rob.get('head_pan') is not None:
            bits.append('camera pan %+.2f  tilt %+.2f rad' % (rob['head_pan'], rob['head_tilt']))
        if rob:
            bits.append('battery %s V   mode %s%s' % (rob.get('battery_v', '?'), rob.get('mode', '?'), '   RUNSTOP ON' if rob.get('runstop') else ''))
        self.l_robot.configure(text='\n'.join(bits), fg=P.RED if rob.get('runstop') else P.SUBTEXT)
        self.b_rel.configure(state='normal' if rob.get('runstop') else 'disabled')
        self.b_pose.configure(state='normal' if man.get('ready') and not man.get('moving') else 'disabled')
        cam_ok = bool(man.get('ready')) and not alive
        if cam_ok and not self.cam_was_ok:
            self.v_view.set('cam')                       # manual mode just became ready: show the camera
        elif not cam_ok and self.v_view.get() == 'cam':
            self.v_view.set(self.map_view)
        self.cam_was_ok = cam_ok
        self.net.want_cam = cam_ok and self.v_view.get() == 'cam'
        for key, b in self.view_btn.items():
            on = self.v_view.get() == key
            b.configure(bg=P.BLUE if on else P.SURF0, fg=P.ON if on else P.TEXT, activebackground=P.BLUE if on else P.SURF0, activeforeground=P.ON if on else P.TEXT, state='normal' if (cam_ok if key == 'cam' else self.view is not None) else 'disabled')
        self.inv.draw(r if pc else None)                 # first: the map draws what the filters let through
        self._draw_map(run, r if pc else None, alive)
        self._draw_metrics(r if pc else None, run)
        self._draw_card()
        self._draw_log(a if up else {}, run, job)
        self._paint_boxes()

    def _draw_camera(self, c, W, H):
        """manual mode: the head camera in the route panel"""
        self.l_run.configure(text='head camera (manual mode)', fg=P.TEXT); self.bar.delete('all')
        self.map_sig = None                              # the map is redrawn when the view goes back to it
        if Image is None:
            c.delete('all'); c.create_text(W / 2, H / 2, text='The camera picture needs Pillow (pip install pillow).', fill=P.OVERLAY, font=self.f['b'])
            return
        if self.net.cam is None:
            c.delete('all'); self.cam_shown = None
            c.create_text(W / 2, H / 2, text='Waiting for the camera ...', fill=P.OVERLAY, font=self.f['b'])
            return
        if getattr(self, 'cam_shown', None) == (self.net.cam_n, W, H):
            return                                     # same frame, same size: leave the picture as it is
        try:
            im = Image.open(io.BytesIO(self.net.cam)); k = min(W / im.width, H / im.height)
            self.cam_img = ImageTk.PhotoImage(im.resize((max(1, int(im.width * k)), max(1, int(im.height * k)))))
        except Exception:
            return
        c.delete('all'); c.create_image(W / 2, H / 2, image=self.cam_img); self.cam_shown = (self.net.cam_n, W, H)

    def _run_line(self, run):
        """the line and the progress bar under the map"""
        self.bar.delete('all')
        if not run:
            return self.l_run.configure(text='')
        nav = run['nav']; goal = ('at %s' % nav['name']) if nav['name'] else ''
        self.l_run.configure(text='waypoint %d of %d  %s      %d reads, %d tags%s' % (
            nav['i'], nav['n'], goal, run['reads']['rows'], run['reads']['tags'], '      ' + run['error'][:70] if run.get('error') else ''),
            fg=P.RED if run.get('error') else P.TEXT)
        if nav['n']:
            self.bar.create_rectangle(0, 0, max(self.bar.winfo_width(), 10) * (nav['ok'] + nav['fail']) / nav['n'], self.px(8), fill=P.RED if nav['fail'] else P.GREEN, outline='')

    def _draw_map(self, run, r, alive):
        c = self.map
        W, H = max(c.winfo_width(), 50), max(c.winfo_height(), 50)
        self._run_line(run)
        if self.net.want_cam:
            return self._draw_camera(c, W, H)
        self.cam_shown = None
        if self.view is None:
            return self._draw_route_plain(c, W, H, run)
        data = self._scene_data(run, r, alive)
        sig = (W, H, self.v_view.get(), tuple((i['uid'], i['x'], i['y'], i['z'], i['state'], i['dim']) for i in data['items']),
               data['robot'] and tuple(round(v, 2) for v in data['robot'][:4]) + (data['robot'][4],), 0 if data['path'] is None else len(data['path']),
               tuple(w[3] for w in data['waypoints']), tuple(data['hud']))
        self.scene_data = data
        if sig != self.map_sig:
            self.map_sig = sig; self._render_map()

    def _scene_data(self, run, r, alive):
        """what the map shows, in the room frame: items from the PC's database, robot / path / waypoints from the robot (map frame -> room)"""
        sc, items, started = self.view.s, [], (r or {}).get('scan_started') or 0
        shown = self.inv.visible if self.screen == 'inventory' else None       # the tags that pass the inventory's filters (None: no filter)

        def add(i, listed):
            if i.get('x_m') is None:
                return
            fresh, missed = M.is_fresh(i, started), int(i.get('missed_scans') or 0)
            state = 'new' if fresh else 'missing' if listed and missed and not i.get('is_on_shelf') else 'old'
            note = '%s · ±%.2f m · %s reads · %s' % ('S%d' % i['shelf'] if i.get('shelf') is not None else 'no shelf', i.get('region90_m') or 0, i.get('n_reads'), i.get('method'))
            items.append(dict(uid=i['rfid_uid'], name=M.label(i['rfid_uid'], i.get('no'), i.get('name') or ''), x=i['x_m'], y=i['y_m'], z=i['z_m'], shelf=i.get('shelf'),
                              r90=i.get('region90_m'), state=state, dim=shown is not None and i['rfid_uid'] not in shown,
                              note=note if state == 'new' else ('missed %d scan%s · last known place' % (missed, '' if missed == 1 else 's') if state == 'missing'
                                                                else 'last known place, from an earlier scan')))
        for i in (r or {}).get('items') or ():
            add(i, True)
        for i in (r or {}).get('located_other') or ():
            add(i, False)
        robot = path = None; wps = []
        if run:
            p, nav = run['pose'], run['nav']
            if p:
                x, y = sc.room_xy([[p['base_x'], p['base_y']]])[0]; robot = (float(x), float(y), p['base_yaw'] + sc.yaw0, p['ant_z'], bool(run['alive']))
            if len(run['path']) > 1:
                path = sc.room_xy(run['path'])
            if run['waypoints']:
                seen = set()
                for k, ((x, y), w) in enumerate(zip(sc.room_xy([[w['x'], w['y']] for w in run['waypoints']]), run['waypoints'])):
                    res = next((q for q in nav['results'] if q['i'] == k + 1), None)
                    st = ('ok' if res['ok'] else 'fail') if res else 'goal' if nav['i'] == k + 1 and run['alive'] else ''
                    key = (round(w['x'], 1), round(w['y'], 1)); name = '' if key in seen else w['name'].split('_')[0]; seen.add(key)
                    wps.append((float(x), float(y), name, st))
        n_new = sum(1 for i in items if i['state'] == 'new'); n_old = len(items) - n_new
        hud = ['%s · %d items placed in this scan%s' % (((r or {}).get('live') or {}).get('loc_method') or 'no method yet', n_new,
                                                        ' · %d at their last known place' % n_old if n_old else '')]
        return dict(items=items, robot=robot, path=path, waypoints=wps, hud=hud)

    def _render_map(self, fast=False):
        if self.view is None or self.scene_data is None or self.net.want_cam or ImageTk is None:
            return
        c = self.map; W, H = max(c.winfo_width(), 50), max(c.winfo_height(), 50)
        self.view.mode = '3d' if self.v_view.get() == '3d' else '2d'
        data = dict(self.scene_data, selected=self.marked(), hover=self.hover_uid, cloud=self.v_cloud.get(), regions=self.v_regions.get(),
                    truth=self.v_truth.get(), fast=fast)
        if data['truth']:                # as on the replay page: how far each estimate is from its tape position, and the median
            tr, errs = self.view.s.truth, []
            items = []
            for i in data['items']:
                if i['state'] == 'new' and i['uid'] in tr:
                    e = math.dist((i['x'], i['y'], i['z']), tr[i['uid']]); errs.append(e); i = dict(i, note='%s · %.0f cm from the tape position' % (i['note'], 100 * e))
                items.append(i)
            data['items'] = items
            if errs:
                errs.sort(); n = len(errs); med = errs[n // 2] if n % 2 else (errs[n // 2 - 1] + errs[n // 2]) / 2
                data['hud'] = data['hud'] + ['median 3D error now %.0f cm (%d items with a tape position)' % (100 * med, n)]
        try:
            im = self.view.render(W, H, data)
        except Exception as e:
            return self.status.configure(text='map error: %r' % e, fg=P.RED)
        self.map_img = ImageTk.PhotoImage(im); c.delete('all'); c.create_image(0, 0, anchor='nw', image=self.map_img)

    def _kick(self, fast=False):
        """redraw the map soon (at most about 60 times a second); after a fast (thinned) picture a full one follows"""
        if self._kick_id is None:
            def go():
                self._kick_id = None; self._render_map(fast)
            self._kick_id = self.root.after(15, go)
        if fast:
            if self._settle_id is not None:
                self.root.after_cancel(self._settle_id)

            def settle():
                self._settle_id = None; self._render_map()
            self._settle_id = self.root.after(350, settle)

    def _set_view(self, key):
        self.v_view.set(key)
        if key != 'cam':
            self.map_view = key
        self.map_sig = None                              # the next tick draws it; the buttons recolour there too
        self._kick()

    def _map_live(self):
        return self.view is not None and not self.net.want_cam

    def _m_down(self, e, button):
        self.mdrag = [e.x, e.y, 0, button]

    def _m_move(self, e):
        if not self.mdrag or not self._map_live():
            return
        dx, dy = e.x - self.mdrag[0], e.y - self.mdrag[1]; self.mdrag[0], self.mdrag[1] = e.x, e.y; self.mdrag[2] += abs(dx) + abs(dy)
        self.view.drag(dx, dy, pan=self.mdrag[3] == 3 or bool(e.state & 0x0001)); self._kick(fast=True)

    def _m_up(self, e):
        d, self.mdrag = self.mdrag, None
        if d and d[2] < 4 and d[3] == 1 and self._map_live():     # a click, not a drag: pick the item under it
            self.select(uid=self.view.pick(e.x, e.y))

    def _m_wheel(self, e, steps):
        if self._map_live():
            self.view.zoom(steps, e.x, e.y); self._kick(fast=True)

    def _m_reset(self, e):
        if self._map_live():
            self.view.reset(); self._kick()

    def _m_hover(self, e):
        uid = self.view.pick(e.x, e.y) if e is not None and self._map_live() and not self.mdrag else None
        if uid != self.hover_uid:
            self.hover_uid = uid; self._kick()

    def _draw_route_plain(self, c, W, H, run):
        """without numpy / Pillow or without scene files: the route only, in the robot's map frame"""
        c.delete('all')
        if not run or not run['waypoints']:
            c.create_text(W / 2, H / 2, text='The route appears here when a run starts.', fill=P.OVERLAY, font=self.f['b'])
            return
        pts = [(w['x'], w['y']) for w in run['waypoints']] + [tuple(p) for p in run['path']]
        x0, x1 = min(p[0] for p in pts) - 0.8, max(p[0] for p in pts) + 0.8
        y0, y1 = min(p[1] for p in pts) - 0.8, max(p[1] for p in pts) + 0.8
        s = min(W / (x1 - x0), H / (y1 - y0))
        ox, oy = (W - s * (x1 - x0)) / 2, (H - s * (y1 - y0)) / 2

        def P(x, y):                      # map x right, map y up
            return ox + (x - x0) * s, H - oy - (y - y0) * s
        for gx in range(math.floor(x0), math.ceil(x1) + 1):
            c.create_line(*P(gx, y0), *P(gx, y1), fill=P.MANTLE)
        for gy in range(math.floor(y0), math.ceil(y1) + 1):
            c.create_line(*P(x0, gy), *P(x1, gy), fill=P.MANTLE)
        nav = run['nav']
        if len(run['path']) > 1:
            c.create_line(*[v for p in run['path'] for v in P(*p)], fill=P.SKY, width=2)
        seen = {}
        for i, w in enumerate(run['waypoints']):
            res = next((x for x in nav['results'] if x['i'] == i + 1), None)
            col = (P.GREEN if res['ok'] else P.RED) if res else P.YELLOW if nav['i'] == i + 1 and run['alive'] else P.SURF2
            x, y = P(w['x'], w['y']); k = (round(w['x'], 1), round(w['y'], 1)); seen[k] = seen.get(k, 0) + 1
            if seen[k] == 1:
                c.create_text(x, y - 16, text=w['name'].split('_')[0], fill=P.SUBTEXT, font=self.f['small'])
            rad = 7 if seen[k] == 1 else 4
            c.create_oval(x - rad, y - rad, x + rad, y + rad, fill=col if res or col == P.YELLOW else '', outline=col, width=2)
        p = run['pose']
        if p:
            x, y = P(p['base_x'], p['base_y']); th = p['base_yaw']
            tri = [(x + 13 * math.cos(th + d), y - 13 * math.sin(th + d)) for d in (0, 2.5, -2.5)]
            c.create_polygon(*[v for q in tri for v in q], fill=P.BLUE if run['alive'] else P.SURF2, outline=P.CRUST)
        c.create_text(8, H - 8, anchor='sw', text='1 m grid · route only (the 2D / 3D map needs numpy and Pillow)', fill=P.OVERLAY, font=self.f['small'])

    def _draw_log(self, a, run, job):
        lines = []
        if job and (job['running'] or not run or not run['alive']):
            lines += [('', l) for l in job['lines'][-7:]]
        elif run:
            lines += [('', l) for l in run['log'][-7:]]
        man = a.get('manual')
        if man and man['on'] and not man['ready']:
            lines += [('', l) for l in man['lines'][-3:]]
        lines += [('err' if e else 'msg', '%s  %s' % (t, m)) for t, m, e in self.msgs[-3:]]
        text = '\n'.join(l for _, l in lines)
        if text == getattr(self, '_log_text', None):
            return
        self._log_text = text
        self.log.configure(state='normal'); self.log.delete('1.0', 'end')
        for tag, l in lines[-9:]:
            self.log.insert('end', l[:160] + '\n', tag)
        self.log.configure(state='disabled')

    # ------------------------------------------------------------------ selection, the photo card, the metrics
    def state(self):
        """the receiver's newest answer as the demo sees it (M.Catalog.apply): the numbered tags only, with their names; None: no answer yet"""
        r = self.net.r
        if r is None:
            return None
        self.cat.refresh()
        if self._names_said != self.cat.names_version:       # the names file was read (at the start, and whenever it is saved): say what came of it
            self._names_said = self.cat.names_version
            self.say('%s: %s' % (os.path.basename(self.names_file), self.cat.problems[0] + (' (and %d more)' % (len(self.cat.problems) - 1) if len(self.cat.problems) > 1 else ''))
                     if self.cat.problems else '%s read: %d item numbers have a name' % (os.path.basename(self.names_file), len(self.cat.names)), bool(self.cat.problems))
        if self._st_r is not r or self._st_ver != self.cat.version:
            self._st_r, self._st_ver, self._st = r, self.cat.version, self.cat.apply(r)
        return self._st

    def select(self, uid=None, name=None):
        """one tag (a click on the map or on an ITEMS row), or every tag of an item name (a STOCK row); nothing: no selection"""
        self.sel_uid, self.sel_name = uid, (name if not uid else None)
        self.inv.show_selection()
        self._draw_card(); self._kick()

    def marked(self):
        """what the map rings: the selected tag, or the tags of the selected item name"""
        if self.sel_uid:
            return self.sel_uid
        p = self.inv.product(self.sel_name) if self.sel_name else None
        return [t['uid'] for t in p['tags']] if p else None

    def _card_info(self):
        """(tag id, title, line under it, its colour, item number, small line over the title, name for the photo) of what is selected"""
        over = lambda no: '' if no is None else 'TAG NO. %d' % no
        if self.sel_uid:
            row = self.inv.row(self.sel_uid)
            if row:
                return (row['uid'], row['name'] or 'no name yet', M.expiry_text(row['exp'], row['days'], row['date'], long=True),
                        P.RED if row['exp'] == 'expired' else P.YELLOW if row['exp'] == 'soon' else P.SUBTEXT, row['no'], over(row['no']), row['name'])
            no, name = self.cat.of(self.sel_uid)
            return (self.sel_uid, name or ('no name yet' if no is not None else self.sel_uid[-8:]), 'not in the inventory database', P.SUBTEXT, no, over(no), name)
        p = self.inv.product(self.sel_name) if self.sel_name else None
        if p:
            line = ('%d on the shelf EXPIRED' % p['expired_on']) if p['expired_on'] else ('Next to expire: ' + p['next_text']) if p['next_days'] is not None else 'no expiry date'
            return ('', p['name'], line, P.RED if p['expired_on'] else P.YELLOW if p['soon_on'] else P.SUBTEXT, None,
                    '%d ITEM%s' % (p['total'], '' if p['total'] == 1 else 'S'), p['name'])
        return None

    def _draw_card(self):
        """the small card in the map's bottom right corner: photo, name and expiry of the selected item"""
        info = None if self.net.want_cam else self._card_info()
        if info is None:
            if self.card is not None:
                self.card.destroy(); self.card = self._card_sig = None
            return
        px, size = self.px, self.px(84)
        img = self.photos.image(info[0], info[6], size, info[4])
        sig = info + (id(img),)
        if sig == self._card_sig:
            return
        if self.card is None:
            self.card = tk.Frame(self.map, bg=P.MANTLE, highlightbackground=P.SURF1, highlightthickness=1)
            self.card_photo = tk.Label(self.card, bg=P.SURF0, bd=0); self.card_photo.pack(side='left', padx=px(6), pady=px(6))
            x = self.close_x(self.card, P.OVERLAY); x.pack(side='right', anchor='n', padx=px(4), pady=px(4))
            x.bind('<Button-1>', lambda e: self.select())
            txt = tk.Frame(self.card, bg=P.MANTLE); txt.pack(side='left', fill='y', padx=(0, px(4)), pady=px(6))
            self.card_no = tk.Label(txt, font=self.f['small'], bg=P.MANTLE, fg=P.SUBTEXT, anchor='w'); self.card_no.pack(anchor='w')
            self.card_name = tk.Label(txt, font=self.f['h'], bg=P.MANTLE, fg=P.TEXT, anchor='w', justify='left', wraplength=px(190)); self.card_name.pack(anchor='w')
            self.card_exp = tk.Label(txt, font=self.f['small'], bg=P.MANTLE, anchor='w', justify='left', wraplength=px(190)); self.card_exp.pack(anchor='w', pady=(px(4), 0))
            self.card.place(relx=1.0, rely=1.0, x=-px(8), y=-px(8), anchor='se')       # bottom right: clear of the map's own text
        self._card_sig = sig
        if img is not None:
            self.card_photo.configure(image=img, width=size, height=size); self.card_photo.image = img
        else:
            self.card_photo.configure(text='photos need\nPillow', font=self.f['small'], fg=P.OVERLAY)
        self.card_no.configure(text=info[5])
        self.card_no.pack(anchor='w', before=self.card_name) if info[5] else self.card_no.pack_forget()
        self.card_name.configure(text=info[1]); self.card_exp.configure(text=info[2], fg=info[3])

    def _draw_metrics(self, r, run):
        if not self.metric_w or r is None:
            return
        m = M.metrics(r, self.truth, run)
        for k, (val, sub, _) in self.metric_w.items():
            val.configure(text=m[k][0], fg=P.TEXT if m[k][0] != '–' else P.OVERLAY); sub.configure(text=m[k][1])


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('--conf', default=os.path.join(HERE, 'demo.conf'))
    ap.add_argument('--agent', default=None, help='robot agent address (default: from the conf file)')
    ap.add_argument('--receiver', default=None, help='pc_receiver.py address (default: this PC, port from the conf file)')
    ap.add_argument('--token', default=os.environ.get('ORIO_DEMO_TOKEN', ''))
    ap.add_argument('--geometry', default=None, help='window size, e.g. 1600x1000 (default: as it was last time, else 1280x800)')
    ap.add_argument('--view', default='2d', choices=['2d', '3d'], help='the map view to open with')
    ap.add_argument('--screen', default=None, choices=['inventory', 'operate'], help='the screen to open with (default: as it was last time)')
    ap.add_argument('--theme', default=None, choices=['dark', 'light'], help='default: as it was last time')
    args = ap.parse_args()
    conf = read_conf(args.conf)
    agent = (args.agent or 'http://%s:%s' % (conf['ROBOT_HOST'], conf.get('AGENT_PORT', '8123'))).rstrip('/')
    receiver = (args.receiver or 'http://127.0.0.1:%s' % conf.get('RECEIVER_PORT', '8000')).rstrip('/')
    prefs = load_prefs()
    if args.screen:
        prefs['screen'] = args.screen
    if args.theme:
        prefs['theme'] = args.theme
    geo = args.geometry or prefs.get('size') or '%dx%d' % (BASE_W, BASE_H)
    try:
        size = tuple(int(v) for v in geo.split('+')[0].split('x'))[:2]
        assert len(size) == 2
    except (ValueError, AssertionError):
        geo, size = '%dx%d' % (BASE_W, BASE_H), (BASE_W, BASE_H)
    root = tk.Tk(); root.geometry(geo)
    App(root, conf, Net(agent, receiver, args.token), args.view, prefs, size)
    root.mainloop()


if __name__ == '__main__':
    main()
