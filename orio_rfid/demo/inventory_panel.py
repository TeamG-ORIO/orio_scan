#!/usr/bin/env python3
"""
inventory_panel.py - the inventory side of the scan demo window: the count tiles, the search box, the filter boxes and
the table. The rules are in inventory_model.py; this file only draws them and takes the clicks.

Tiles      ON SHELF / MISSING / EXPIRED / EXPIRING / BELOW PAR. A click on a tile shows just those rows; a second click
           shows everything again.
Search     looks at the names and the item numbers; every word typed must be in the name or be the number.
Filters    boxes of one line widen (on shelf OR missing), the lines narrow each other (state AND expiry AND shelf).
           What does not pass is also drawn faint on the map.
ITEMS      one row per item (a tag, or an object with several tags on it, which counts once): tag number (the number it
           was registered under; an object goes by its own), name (from the names file; – until it has
           one), state (on shelf /
           missed N scans / not seen yet), shelf, expiry, when it was last read (the time today, else the day).
STOCK      one row per item name: how many are on the shelf and usable (expired ones do not count), the PAR and
           whether the name is below it. Double-click a row to set its PAR (empty = not tracked). Tags without a name
           are not in it; the line over the table says how many those are.
A click on a column heading sorts by it (again: the other way round; a third time: back to "what needs a person first").

The panel object lives as long as the window (it keeps the search text, the boxes, the sort order); build() makes its
widgets, and is called again whenever the window is rebuilt for another theme, screen or size.
"""
import time
import tkinter as tk
from tkinter import ttk

import inventory_model as M
import ui
from ui import P

TILES = (('on', 'ON SHELF'), ('missed', 'MISSING'), ('expired', 'EXPIRED'), ('soon', 'EXPIRING'), ('below', 'BELOW PAR'))
PRESET = {'on': dict(state={'on'}), 'missed': dict(state={'missed'}), 'expired': dict(state={'on'}, exp={'expired'}),
          'soon': dict(state={'on'}, exp={'soon'}), 'below': dict(below=True)}
COLS = {'items': (('no', 'Tag no.', 0.11, 'center'), ('name', 'Name', 0.29, 'w'), ('state', 'State', 0.18, 'w'), ('shelf', 'Shelf', 0.08, 'center'),
                  ('exp', 'Expires', 0.24, 'w'), ('seen', 'Seen', 0.10, 'e')),
        'stock': (('name', 'Name', 0.32, 'w'), ('stock', 'On shelf', 0.19, 'w'), ('par', 'PAR', 0.07, 'center'), ('status', 'Status', 0.18, 'w'),
                  ('next', 'Next expiry', 0.24, 'w'))}
SORT = {'items': {'no': lambda x: (x['no'] is None, x['no'] or 0), 'name': lambda x: (not x['name'], M.natural(x['name'])), 'state': lambda x: ({'missed': 0, 'on': 1, 'wait': 2}[x['state']], -x['missed']),
                  'shelf': lambda x: (x['shelf'] is None, x['shelf'] or 0), 'exp': lambda x: (x['days'] is None, x['days'] or 0),
                  'seen': lambda x: (not x['seen'], x['seen'])},
        'stock': {'name': lambda p: (), 'stock': lambda p: (p['usable'],), 'par': lambda p: (p['par'] is None, p['par'] or 0),
                  'status': lambda p: (not p['below'], p['par'] is None), 'next': lambda p: (p['next_days'] is None, p['next_days'] or 0)}}


class InventoryPanel:
    def __init__(self, app, shelves=()):
        self.app, self.shelves = app, list(shelves)
        self.mode, self.sort = 'items', {'items': None, 'stock': None}       # sort: (column, reversed) or None
        self.v_search = tk.StringVar()
        self.v = {k: tk.BooleanVar(value=False) for k in ('on', 'missed', 'wait', 'expired', 'soon', 'below')}
        self.v_shelf = {n: tk.BooleanVar(value=False) for n in self.shelves}
        self.view = self.visible = self.sig = self.tree = None       # view: the model; visible: tags that pass, None = no filter
        self.v_search.trace_add('write', lambda *a: self.changed())

    # ------------------------------------------------------------------ what is asked for
    def facets(self):
        v = self.v
        return dict(state={k for k in ('on', 'missed', 'wait') if v[k].get()}, exp={k for k in ('expired', 'soon') if v[k].get()},
                    shelf={n for n, b in self.v_shelf.items() if b.get()}, below=bool(v['below'].get()))

    def set_facets(self, f):
        for k in ('on', 'missed', 'wait'):
            self.v[k].set(k in f['state'])
        for k in ('expired', 'soon'):
            self.v[k].set(k in f['exp'])
        for n, b in self.v_shelf.items():
            b.set(n in f['shelf'])
        self.v['below'].set(bool(f['below']))
        self.changed()

    def words(self):
        return self.v_search.get().lower().split()

    def clear(self):
        self.v_search.set(''); self.set_facets(M.empty_facets())

    def tile(self, key):
        want = dict(M.empty_facets(), **PRESET[key])
        if self.facets() == want:
            return self.set_facets(M.empty_facets())
        if key == 'below' and self.mode != 'stock':
            self.set_mode('stock')
        self.set_facets(want)

    def set_mode(self, mode):
        self.mode = mode
        if self.tree is not None:
            self._columns()
        self.changed()

    def sort_by(self, col):
        cur = self.sort[self.mode]
        self.sort[self.mode] = (col, False) if not cur or cur[0] != col else (col, True) if not cur[1] else None
        self._columns(); self.changed()

    def changed(self):
        """a box, the search text, the mode or the sort order changed: draw now, not at the next tick"""
        self.app._paint_boxes(); self.sig = None
        self.draw(self.app.state() if self.app.net.pc_up() else None)
        self.app.map_sig = None

    # ------------------------------------------------------------------ widgets
    def build(self, parent):
        app, px, f = self.app, self.app.px, self.app.f
        p = tk.Frame(parent, bg=P.MANTLE, highlightbackground=P.SURF0, highlightthickness=1); p.pack(fill='both', expand=True)
        head = tk.Frame(p, bg=P.MANTLE); head.pack(fill='x', padx=px(10), pady=(px(8), px(4)))
        tk.Label(head, text='INVENTORY', font=f['h'], bg=P.MANTLE, fg=P.SUBTEXT).pack(side='left')
        self.l_scan = tk.Label(head, text='', font=f['small'], bg=P.MANTLE, fg=P.SUBTEXT, anchor='e'); self.l_scan.pack(side='right')
        # ---- tiles: a number, what it counts, a colour strip that lights up when the number wants attention
        tiles = tk.Frame(p, bg=P.MANTLE); tiles.pack(fill='x', padx=px(10))
        self.tiles = {}
        for n, (key, name) in enumerate(TILES):
            tiles.columnconfigure(n, weight=1, uniform='tile')
            fr = tk.Frame(tiles, bg=P.SURF0, cursor='hand2'); fr.grid(row=0, column=n, sticky='nsew', padx=(0 if n == 0 else px(6), 0))
            strip = tk.Frame(fr, bg=P.SURF2, width=px(4)); strip.pack(side='left', fill='y')
            num = tk.Label(fr, text='–', font=f['big'], bg=P.SURF0, fg=P.TEXT, anchor='w'); num.pack(fill='x', padx=px(8), pady=(px(4), 0))
            lab = tk.Label(fr, text=name, font=f['small'], bg=P.SURF0, fg=P.SUBTEXT, anchor='w'); lab.pack(fill='x', padx=px(8), pady=(0, px(5)))
            for w in (fr, num, lab, strip):
                w.bind('<Button-1>', lambda e, k=key: self.tile(k))
            self.tiles[key] = (fr, strip, num, lab)
        # ---- search, and the two tables
        row = tk.Frame(p, bg=P.MANTLE); row.pack(fill='x', padx=px(10), pady=(px(10), px(4)))
        tk.Label(row, text='Search', font=f['small'], bg=P.MANTLE, fg=P.SUBTEXT).pack(side='left')
        seg = tk.Frame(row, bg=P.SURF0); seg.pack(side='right')
        self.b_mode = {}
        for key, text in (('items', 'ITEMS'), ('stock', 'STOCK')):
            self.b_mode[key] = app._btn(seg, text, lambda k=key: self.set_mode(k), P.SURF0, fg=P.TEXT, font='small', padx=8, pady=2)
            self.b_mode[key].pack(side='left')
        x = app.close_x(row, P.SUBTEXT); x.configure(padx=px(4)); x.pack(side='right', padx=(0, px(6)))
        x.bind('<Button-1>', lambda e: self.v_search.set(''))
        ent = tk.Entry(row, textvariable=self.v_search, font=f['b'], bg=P.SURF0, fg=P.TEXT, insertbackground=P.TEXT, relief='flat',
                       highlightthickness=1, highlightbackground=P.SURF1, highlightcolor=P.BLUE)
        ent.pack(side='left', fill='x', expand=True, padx=(px(6), 0), ipady=px(2))
        # ---- filter boxes
        g = tk.Frame(p, bg=P.MANTLE); g.pack(fill='x', padx=px(10))
        lab = dict(font=f['small'], bg=P.MANTLE, fg=P.OVERLAY, anchor='w')
        lines = [('State', [('on', 'on shelf'), ('missed', 'missing'), ('wait', 'not seen')]),
                 ('Expiry', [('expired', 'expired'), ('soon', 'expiring in %d days' % app.soon_days)]), ('Stock', [('below', 'below PAR')])]
        for r, (name, boxes) in enumerate(lines):
            tk.Label(g, text=name, **lab).grid(row=r, column=0, sticky='w', padx=(0, px(6)))
            fr = tk.Frame(g, bg=P.MANTLE); fr.grid(row=r, column=1, sticky='w')
            for key, text in boxes:
                app.cbox(fr, text, self.v[key], self.changed).pack(side='left')
        if self.shelves:
            tk.Label(g, text='Shelf', **lab).grid(row=len(lines), column=0, sticky='w', padx=(0, px(6)))
            fr = tk.Frame(g, bg=P.MANTLE); fr.grid(row=len(lines), column=1, sticky='w')
            for n in self.shelves:
                app.cbox(fr, str(n), self.v_shelf[n], self.changed, pad=2).pack(side='left')
        row = tk.Frame(p, bg=P.MANTLE); row.pack(fill='x', padx=px(10), pady=(px(4), 0))
        self.l_count = tk.Label(row, text='', font=f['small'], bg=P.MANTLE, fg=P.SUBTEXT, anchor='w'); self.l_count.pack(side='left')
        self.b_clear = app._btn(row, 'show all', self.clear, P.SURF0, fg=P.TEXT, font='small', padx=8, pady=1); self.b_clear.pack(side='right')
        # ---- the table
        self.l_hint = tk.Label(p, text='', font=f['small'], bg=P.MANTLE, fg=P.OVERLAY, anchor='w'); self.l_hint.pack(side='bottom', fill='x', padx=px(10), pady=(0, px(6)))
        tf = tk.Frame(p, bg=P.MANTLE); tf.pack(fill='both', expand=True, padx=px(10), pady=(px(4), px(4)))
        self.tree = ttk.Treeview(tf, show='headings', style='Inv.Treeview', selectmode='browse', height=6)
        sb = ttk.Scrollbar(tf, orient='vertical', command=self.tree.yview); self.tree.configure(yscrollcommand=sb.set)
        sb.pack(side='right', fill='y'); self.tree.pack(side='left', fill='both', expand=True)
        for tag, col in (('on', P.TEXT), ('wait', P.OVERLAY), ('miss', P.RED), ('exp', P.PEACH), ('soon', P.YELLOW)):
            self.tree.tag_configure(tag, foreground=col)
        self.tree.bind('<<TreeviewSelect>>', self._on_select)
        self.tree.bind('<Double-Button-1>', self._on_double)
        self.tree.bind('<Configure>', lambda e: self._widths())
        self._columns(); self.sig = None

    def forget(self):
        """the window is being rebuilt: the widgets are gone, what was asked for stays"""
        self.tree = None

    def _columns(self):
        cols = COLS[self.mode]; cur = self.sort[self.mode]
        self.tree.configure(columns=[c[0] for c in cols])
        for col, name, _, anchor in cols:
            arrow = ui.icon('sort_down' if cur[1] else 'sort_up', max(6, self.app.px(7)), P.SUBTEXT) if cur and cur[0] == col else None
            self.tree.heading(col, text=name, image=arrow or '', anchor=anchor, command=lambda c=col: self.sort_by(c))      # the arrow is drawn right of the name
            self.tree.column(col, anchor=anchor, stretch=False)
        self._widths()
        for key, b in self.b_mode.items():
            b.configure(bg=P.BLUE if key == self.mode else P.SURF0, fg=P.ON if key == self.mode else P.TEXT)
        self.l_hint.configure(text='Double-click a name to set its PAR (empty = not tracked). Expired items do not count as stock.' if self.mode == 'stock' else
                              'Click a row to see it on the map. STOCK counts each item name against its PAR.')

    def _widths(self):
        if self.tree is None:
            return
        total = max(self.tree.winfo_width() - self.app.px(2), self.app.px(200))
        for col, _, frac, _ in COLS[self.mode]:
            self.tree.column(col, width=int(frac * total), minwidth=self.app.px(30))

    # ------------------------------------------------------------------ drawing
    def draw(self, r):
        """r = the receiver's /state after the catalog (App.state(); None: not reachable). Always refreshes the model; the widgets only if they are there."""
        if r is None:
            return
        v = self.view = M.build(r, soon_days=self.app.soon_days)
        words, fac = self.words(), self.facets()
        active = M.any_filter(words, fac)
        passing = [x for x in v['rows'] if M.row_passes(x, words, fac, v['below_names'])]
        self.visible = {x['uid'] for x in passing} if active else None
        if self.tree is None:
            return
        c = v['counts']
        for key, (fr, strip, num, lab) in self.tiles.items():
            n = c[key]; tracked = key != 'below' or c['tracked']
            hot = {'on': P.GREEN, 'missed': P.RED, 'expired': P.RED, 'soon': P.YELLOW, 'below': P.RED}[key]
            on = fac == dict(M.empty_facets(), **PRESET[key])
            bg = P.SURF1 if on else P.SURF0
            strip.configure(bg=hot if n else P.SURF2)
            num.configure(text=str(n) if tracked else '–', fg=P.TEXT if n else P.OVERLAY, bg=bg)
            lab.configure(bg=bg, fg=P.TEXT if on else P.SUBTEXT); fr.configure(bg=bg)
        self._scan_line(v, c)
        phrase = ' '.join(words)           # with a search and no column picked: the number or names that hold the words as typed come first

        def near(x):
            return (0 if x['words'] == phrase or x.get('no_text') == phrase.lstrip('#') else 1 if x['words'].startswith(phrase)
                    else 2 if phrase in x['words'] else 3) if phrase else 0
        if self.mode == 'items':
            cur = self.sort['items']; key = SORT['items'][cur[0]] if cur else (lambda x: (near(x), x['rank']))
            rows = sorted(passing, key=lambda x: (key(x), not x['name'], M.natural(x['name']), x['no'] is None, x['no'] or 0, x['uid']),
                          reverse=bool(cur and cur[1]))          # ties: by name, the ones without a name after them by number
            out = [(x['uid'], (x['no_text'], x['name_text'], x['state_text'], x['shelf_text'], x['exp_text'], x['seen_text']), x['tag']) for x in rows]
            self.l_count.configure(text=('%d of %d items' % (len(out), c['items']) if active else '%d items' % c['items'])
                                   + ' · %d with a name, %d to name' % (c['named'], c['unnamed']))
        else:
            cur = self.sort['stock']; key = SORT['stock'][cur[0]] if cur else (lambda p: (near(p), p['rank']))
            prods = [p for p in v['products'] if M.product_passes(p, words, fac, v['below_names'])]
            prods.sort(key=lambda p: (key(p), M.natural(p['name'])), reverse=bool(cur and cur[1]))
            out = [('p:' + p['name'], (p['name'], p['stock_text'], p['par_text'], p['status_text'], p['next_text']), p['tag']) for p in prods]
            self.l_count.configure(text=('%d of %d item names' % (len(out), c['products']) if active else '%d item names' % c['products'])
                                   + (', %d with a PAR' % c['tracked'] if c['tracked'] else ', none with a PAR yet')
                                   + (' · %d item%s without a name not listed' % (c['unnamed'], '' if c['unnamed'] == 1 else 's') if c['unnamed'] else ''))
        self.b_clear.configure(state='normal' if active else 'disabled')
        sig = (self.mode, tuple(out))
        if sig == self.sig:
            return
        self.sig = sig
        top = self.tree.yview()[0]
        self.tree.delete(*self.tree.get_children())
        for iid, values, tag in out:
            self.tree.insert('', 'end', iid=iid, values=values, tags=(tag,))
        self.tree.yview_moveto(top)
        self.show_selection(see=False)

    def _scan_line(self, v, c):
        scan, n = v['scan'], v['scans_done']
        hhmm = lambda t: time.strftime('%H:%M', time.localtime(t))
        fg = P.SUBTEXT
        if not scan:           # a scan from before the receiver kept a record of scans has a start time and nothing else
            text = 'earlier runs cleared: no scan yet' if v.get('cleared') else 'the last scan was not recorded as finished' if v['started'] else 'no scan yet'
        elif v['running']:
            text = 'scan %d going: %d of %d read' % (n + 1, c['on'], c['items'])
        elif scan.get('complete'):
            text = 'scan %d finished %s' % (n, hhmm(scan['ended']))
            if scan.get('wp_n') and scan.get('wp_ok') is not None and scan['wp_ok'] < scan['wp_n']:
                text += ' · only %d of %d waypoints reached' % (scan['wp_ok'], scan['wp_n']); fg = P.YELLOW
        else:
            text, fg = 'last scan %s at %s: not counted as a missed scan' % (scan.get('phase') or 'not finished', hhmm(scan['ended'])), P.YELLOW
        self.l_scan.configure(text=text, fg=fg)

    # ------------------------------------------------------------------ selection
    def name_of(self, uid):
        return next((x['name'] for x in (self.view or {}).get('rows', ()) if x['uid'] == uid), None)

    def product(self, name):
        return next((p for p in (self.view or {}).get('products', ()) if p['name'] == name), None)

    def row(self, uid):
        return next((x for x in (self.view or {}).get('rows', ()) if x['uid'] == uid), None)

    def show_selection(self, see=True):
        """mark the window's selection (a tag or an item name) in the table"""
        if self.tree is None:
            return
        app = self.app
        iid = app.sel_uid if self.mode == 'items' else ('p:' + (app.sel_name or self.name_of(app.sel_uid) or '')) if (app.sel_name or app.sel_uid) else None
        if iid and self.tree.exists(iid):
            if self.tree.selection() != (iid,):
                self.tree.selection_set(iid)
            if see:
                self.tree.see(iid)
        elif self.tree.selection():
            self.tree.selection_remove(*self.tree.selection())

    def _on_select(self, _e):
        sel, app = self.tree.selection(), self.app
        if not sel:
            return
        iid = sel[0]
        if iid.startswith('p:'):
            name = iid[2:]
            if app.sel_name != name and self.name_of(app.sel_uid) != name:
                app.select(name=name)
        elif iid != app.sel_uid:
            app.select(uid=iid)

    def _on_double(self, e):
        iid = self.tree.identify_row(e.y)
        if self.mode != 'stock' or not iid.startswith('p:'):
            return
        box, app, name = self.tree.bbox(iid, 'par'), self.app, iid[2:]
        if not box:
            return
        p = self.product(name)
        ent = tk.Entry(self.tree, font=app.f['small'], justify='center', bg=P.BASE, fg=P.TEXT, insertbackground=P.TEXT, relief='flat',
                       highlightthickness=1, highlightbackground=P.BLUE, highlightcolor=P.BLUE)
        ent.insert(0, '' if not p or p['par'] is None else str(p['par'])); ent.select_range(0, 'end')
        ent.place(x=box[0], y=box[1], width=box[2], height=box[3]); ent.focus_set()

        def save(_e=None):
            text = ent.get().strip(); ent.destroy()
            if text and not text.isdigit():
                return app.say('PAR is a whole number (empty = not tracked)', True)
            app.net.pc_post('/par', {'item_name': name, 'par': int(text) if text else None},
                            lambda rep: app.root.after(0, app.say, ('PAR of %s: %s' % (name, text or 'not tracked')) if rep.get('ok') else
                                                       'PAR not saved: %s' % rep.get('error', rep), not rep.get('ok')))
        ent.bind('<Return>', save); ent.bind('<KP_Enter>', save); ent.bind('<FocusOut>', lambda _e: ent.destroy())
