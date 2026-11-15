#!/usr/bin/env python3
"""
inventory_model.py - the inventory rules of the scan demo window, with no window in them: what state an item is in,
when it counts as expired, how stock is counted against PAR, which rows pass the filters, and the metrics.
scan_dashboard.py and inventory_panel.py only show what comes out of here.

    cat = Catalog(registry, names)      the item numbers (RFID/tag_registry.csv) and the names (item_names.csv)
    state = cat.apply(state)            state = what sync/pc_receiver.py answers on GET /state; after apply() it holds
                                        the demo's inventory only, each tag with its item number and its name
    view = build(state)
    view['rows']      one per item:       no (tag number), name, state ('on' / 'missed' / 'wait'), state_text, shelf, exp, ...
                      (an item is a tag, or an object that carries several tags: see "objects")
    view['products']  one per item name:  total, on, usable, par, below, ...   (tags without a name are in no product)
    view['counts']    the numbers on the tiles
    metrics(state, truth, run)          the numbers of the metrics row

The rules
    tag no.   the number the tag was registered under: "Item 30" in RFID/tag_registry.csv -> 30 (load_registry). A tag
              the registry does not list has no number, unless the database itself calls it "Item N". (The names file
              and the code call it the item number, as the registry does; the window says "Tag number".)
    objects   tags the registry gives the same `object` are one thing with several tags on it, and are ONE item
              (team decisions, 14 Nov). Its number is the object's own ("Item 48" for tags 48, 49, 50); the other
              tags are shown nowhere, but the search box still finds the item by their numbers.
              on shelf: any of its tags was read. missed N scans: none of them was read in the last N complete scans.
              position: the estimate of its tag with the most reads, among the tags placed in this scan (else among
              the older estimates); one real estimate, so its uncertainty region is a real one.
              metrics: count objects, and score that estimate against the tape position of that same tag.
              name, expiry date, PAR: those of the object's own number.
    inventory the tags that have an item number, and no others (team decision, 14 Nov): a tag the database lists but
              that was never registered is left out of every table, tile, count and metric (state['left_out']).
    name      from the names file and nowhere else (load_names): item number -> name. The database's item_name is not
              shown and not needed. An item number the file does not have: no name yet.
    expiry date   the names file's, when its line gives one; else the database's expiration_date.
    state     on shelf: read in the current scan. missed N scans: not read in the last N COMPLETE scans (the receiver
              counts them, see pc_receiver.py; a stopped or failed scan does not count). not seen yet: a scan is going
              and has not read it so far. An item that is read again is on shelf again.
    expiry    expired: the date is before today (as inventory_dashboard.py). soon: today or within SOON_DAYS days.
    stock     per item name. usable = tags of that name that are on shelf and NOT expired. below PAR: the name has a
              PAR and usable is less than it. Names without a PAR are not tracked; tags without a name are not stock
              of anything, and are counted apart (counts['unnamed']). The PAR is kept by the receiver per name
              (state['pars']), so it follows a name that is given to more tags.
    filters   boxes of one group widen (on shelf OR missing), groups narrow each other (state AND expiry AND shelf).
              The search box looks at names and item numbers: every word typed must be in the name or be the number
              ("gown", "30", "#30"; an item without a name also answers to "tag 30").
    within 1 m3   of the items placed in this scan that have a tape-measured position (loc/site/ground_truth_*.csv, items
              only, not the shelf corner tags): the share whose estimate is less than 0.62 m from it in 3D. 0.62 m is
              the radius of a sphere of 1 m3 (0.6204 m), and "< 0.62" is the test the offline study reports
              (loc/offline/mlib.py). An item with no tape position is not in this number at all; neither is an item that
              was read but not placed (too few reads), which the tile says separately.

Standard library only. Check it by itself:   python3 inventory_model.py
"""
import csv
import datetime
import math
import os
import re
import time

WITHIN_M = 0.62
SOON_DAYS = 30
METRICS = (('within', 'WITHIN 1 m³  (< %.2f m)' % WITHIN_M), ('median', 'MEDIAN ERROR'), ('shelf', 'RIGHT SHELF'), ('detected', 'DETECTED'),
           ('placed', 'PLACED'), ('time', 'SCAN TIME'), ('reads', 'READS'))
METRICS_DEFAULT = ('within', 'median', 'shelf', 'detected')


def load_truth(path):
    """tape-measured positions: {rfid_uid: (x, y, z, shelf)} in the room frame, items only (as scene_view.Scene.truth)"""
    out = {}
    try:
        for r in csv.DictReader(open(path)):
            if r.get('category') != 'reference' and r.get('X_room_m'):
                out[r['epc'].strip().upper()] = (float(r['X_room_m']), float(r['Y_room_m']), float(r['Z_room_m']),
                                                 int(r['shelf']) if (r.get('shelf') or '').strip().isdigit() else None)
    except (OSError, KeyError, ValueError):
        pass
    return out


NUMBERED = re.compile(r'\s*item\s*#?\s*(\d+)\s*$', re.I)          # "Item 30": a registry number, not a name


def load_registry(path):
    """RFID/tag_registry.csv -> ({rfid_uid: N} for every tag it calls "Item N", {rfid_uid: object} for the tags that share an
    object with another tag; object = what the registry's `object` column says, e.g. "Item 48")"""
    numbers, obj = {}, {}
    try:
        for r in csv.DictReader(open(path)):
            m = NUMBERED.match(r.get('name') or '')
            if m and r.get('epc'):
                uid = r['epc'].strip().upper(); numbers[uid] = int(m.group(1))
                if (r.get('object') or '').strip():
                    obj[uid] = ' '.join(r['object'].split())
    except (OSError, ValueError):
        pass
    n = {}
    for o in obj.values():
        n[o] = n.get(o, 0) + 1
    return numbers, {uid: o for uid, o in obj.items() if n[o] > 1}


def load_numbers(path):
    return load_registry(path)[0]


NAMES_HEAD = '''# item_names.csv - the names the scan demo window shows, by item number (the N of "Item N" in RFID/tag_registry.csv).
# One line: the item numbers, a comma, the name, and if wanted a comma and the expiry date (2027-11-24).
# Numbers: one (30), a range (30-35), or several with spaces (30 31 40-42). A name with a comma in it goes in "double quotes".
# No date on a line: the item keeps the expiry date the database has for it. A number given twice: the later line wins.
# The window reads this file again a few seconds after it is saved; no restart.
item_no,name,expires
'''


def parse_numbers(text):
    """'30', '30-35', '#30 31 40-42', 'Item 30' -> the list of numbers; None when it is not that"""
    out = []
    for tok in re.split(r'[\s;+&]+', re.sub(r'\s*-\s*', '-', re.sub(r'(?i)\bitems?\b|#', ' ', text or '')).strip()):
        m = re.fullmatch(r'(\d+)(?:-(\d+))?', tok)
        if not m:
            return None
        a, b = int(m.group(1)), int(m.group(2) or m.group(1))
        if not a <= b <= a + 2000:
            return None
        out.extend(range(a, b + 1))
    return out


def load_names(path):
    """the names file -> ({item number: name}, {item number: 'YYYY-MM-DD'}, [what could not be read]); see NAMES_HEAD for its layout"""
    names, expires, bad, line_of = {}, {}, [], {}
    try:
        with open(path, newline='', encoding='utf-8-sig') as f:
            for n, row in enumerate(csv.reader(f), 1):
                first = (row[0] if row else '').strip()
                if not any(c.strip() for c in row) or first.startswith('#') or re.sub(r'[^a-z]', '', first.lower()) in ('itemno', 'no', 'number', 'itemnumber'):
                    continue
                nos, name = parse_numbers(first), ' '.join((row[1] if len(row) > 1 else '').split())
                date = (row[2] if len(row) > 2 else '').strip()
                if nos is None:
                    bad.append('line %d: "%s" is not an item number' % (n, first[:30])); continue
                if date and parse_date(date) is None:
                    bad.append('line %d: "%s" is not a date like 2027-11-24 (the name is used, the date is not)' % (n, date[:20])); date = ''
                for no in nos:
                    if name and names.get(no) not in (None, '', name):
                        bad.append('line %d: item %d was "%s" on line %d, now "%s"' % (n, no, names[no], line_of[no], name))
                    names[no] = name; line_of[no] = n; expires[no] = date and parse_date(date).isoformat()
    except OSError as e:
        bad.append('cannot read it: %s' % (e.strerror or e))
    except (csv.Error, UnicodeDecodeError) as e:
        bad.append('not a readable CSV file: %s' % e)
    return {no: name for no, name in names.items() if name}, {no: d for no, d in expires.items() if d and names.get(no)}, bad


def write_names(path, names, expires=None):
    """a names file from {item number: name} and {item number: expiry date}: one line per name and date, its numbers as ranges"""
    by, expires = {}, expires or {}
    for no, name in sorted(names.items()):
        by.setdefault((name, expires.get(no) or ''), []).append(no)
    with open(path, 'w', newline='', encoding='utf-8') as f:
        f.write(NAMES_HEAD); w = csv.writer(f)
        for (name, date), nos in sorted(by.items(), key=lambda kv: (kv[0][0].lower(), kv[1][0])):
            runs = []
            for no in nos:
                if runs and no == runs[-1][1] + 1:
                    runs[-1][1] = no
                else:
                    runs.append([no, no])
            w.writerow([' '.join(str(a) if a == b else '%d-%d' % (a, b) for a, b in runs), name] + ([date] if date else []))


class Catalog:
    """which tags are the demo's inventory and what they are called: the registry's item numbers and the names file.
    Both files are read again when they change (tags get registered and names get added during the day)."""

    def __init__(self, registry=None, names=None, numbers=None, named=None, objects=None):
        self.registry, self.names_path = registry, names
        self.numbers, self.names, self.expires, self.problems = dict(numbers or {}), dict(named or {}), {}, []      # given directly: for tests
        self.objects, self._name_problems = dict(objects or {}), []
        self.version, self.names_version, self._at, self._mt = 0, 0, 0.0, (-1, -1)
        self.refresh(force=True)

    def refresh(self, force=False):
        """look at the two files, at most every 2 s; True when something changed"""
        if not force and time.time() - self._at < 2.0:
            return False
        self._at = time.time()

        def mtime(p):                    # time and size: two saves within one tick of the clock still differ
            try:
                st = os.stat(p) if p else None
                return st and (st.st_mtime_ns, st.st_size)
            except OSError:
                return None
        mt = (mtime(self.registry), mtime(self.names_path))
        if mt == self._mt:
            return False
        if self.registry and mt[0] != self._mt[0]:
            self.numbers, self.objects = load_registry(self.registry)
        if self.names_path and mt[1] != self._mt[1]:
            self.names, self.expires, self._name_problems = load_names(self.names_path); self.names_version += 1
        self.problems = self._name_problems + self.clashes()
        self._mt = mt; self.version += 1
        return True

    def clashes(self):
        """objects whose tags were given different names: the object shows the name of its own number"""
        by = {}
        for uid, o in self.objects.items():
            by.setdefault(o, []).append(self.numbers[uid])
        return ['tags %s are one object but have different names: "%s" is used' % (
                    ' '.join(map(str, sorted(nos))), self.names.get(self.object_no(o, nos), ''))
                for o, nos in sorted(by.items(), key=lambda kv: min(kv[1])) if len({self.names.get(n, '') for n in nos}) > 1]

    @staticmethod
    def object_no(o, nos):
        """the number an object goes by: its own ("Item 48" -> 48) when one of its tags has it, else its lowest tag number"""
        m = NUMBERED.match(o)
        return int(m.group(1)) if m and int(m.group(1)) in nos else min(nos)

    def of(self, uid, item_name=None):
        """-> (item number or None, name or '')"""
        no = split_name(uid, item_name, self.numbers)[0]
        return no, self.names.get(no, '') if no is not None else ''

    def apply(self, r):
        """the receiver's state as the demo sees it: only the tags with a number, the tags of one object as one item (see "objects"
        at the top), each item with 'no', 'nos', 'name' and its name's 'par'"""
        pars, items, groups, started = r.get('pars'), [], {}, r.get('scan_started') or 0
        n_tags = 0
        for i in r.get('items') or ():
            no, name = self.of(i['rfid_uid'], i.get('item_name'))
            if no is None:
                continue
            n_tags += 1
            o = self.objects.get(str(i['rfid_uid']).upper())
            if o is not None and o in groups:
                groups[o].append(dict(i, no=no)); continue
            if o is not None:
                groups[o] = [dict(i, no=no)]
            items.append(o if o is not None else dict(i, no=no))        # an object keeps the place of its first tag
        out = []
        for i in items:
            if not isinstance(i, dict):
                i = self.merge(groups[i], self.object_no(i, [t['no'] for t in groups[i]]), started)
            no = i['no']; name = self.names.get(no, '')
            # a receiver from before 'pars': the PAR it joined on the database's own name, good while that is the same name
            par = pars.get(name) if pars is not None else i.get('par') if name == i.get('item_name') else None
            out.append(dict(i, name=name, nos=i.get('nos') or [no], par=par if name else None, expiration_date=self.expires.get(no) or i.get('expiration_date')))
        inside = {t['rfid_uid'] for g in groups.values() for t in g}
        grouped = {o for o in groups}
        other = [dict(i, **dict(zip(('no', 'name'), self.of(i['rfid_uid'], i.get('item_name'))))) for i in r.get('located_other') or ()
                 if i['rfid_uid'] not in inside and self.objects.get(str(i['rfid_uid']).upper()) not in grouped]
        return dict(r, items=out, located_other=other, n_items=len(out), n_on_shelf=sum(1 for i in out if i.get('is_on_shelf')),
                    n_located=sum(1 for i in out if i.get('is_on_shelf') and i.get('x_m') is not None),
                    left_out=len(r.get('items') or ()) - n_tags, merged=n_tags - len(out))

    LOC = ('shelf', 'shelf_prob', 'x_m', 'y_m', 'z_m', 'region90_m', 'method', 'n_reads', 'loc_updated_s')

    def merge(self, tags, no, started):
        """the tags of one object as one item, under the tag that has the object's number (its id is the item's id)"""
        own = next((t for t in tags if t['no'] == no), tags[0])
        placed = [t for t in tags if t.get('x_m') is not None]
        fresh = [t for t in placed if is_fresh(t, started)]
        rep = max(fresh or placed, key=lambda t: (t.get('n_reads') or 0, -t['no'])) if placed else None
        item = dict(own, is_on_shelf=int(any(t.get('is_on_shelf') for t in tags)), missed_scans=min(int(t.get('missed_scans') or 0) for t in tags),
                    last_seen=max((t.get('last_seen') or '' for t in tags)) or None, nos=sorted(t['no'] for t in tags),
                    place_uid=rep['rfid_uid'] if rep else own['rfid_uid'])
        for k in self.LOC:
            item[k] = rep.get(k) if rep else None
        return item


def split_name(uid, item_name, numbers=None):
    """-> (item number or None, name or '') from the database's own item_name: what a state shows that Catalog.apply() has not seen"""
    m = NUMBERED.match(item_name or '')
    no = (numbers or {}).get(str(uid).upper())
    if no is None and m:
        no = int(m.group(1))
    return no, '' if m else str(item_name or '').strip()


def label(uid, no, name):
    """one line that says which item this is (the map, the messages): the name, else "Tag 30", else the end of the tag id"""
    return name if name and no is None else 'No. %d · %s' % (no, name) if name else 'Tag %d' % no if no is not None else str(uid)[-8:]


def natural(text):
    """sort key: "Item 2" before "Item 10" """
    return [int(t) if t.isdigit() else t.lower() for t in re.split(r'(\d+)', text or '')]


def parse_date(text):
    try:
        return datetime.date(*map(int, str(text or '').strip()[:10].split('-')))
    except (ValueError, TypeError):
        return None


def expiry(text, today, soon_days=SOON_DAYS):
    """-> (key, days left): 'expired', 'soon', 'ok' or 'none' (no date, or not a date)"""
    d = parse_date(text)
    if d is None:
        return 'none', None
    days = (d - today).days
    return ('expired' if days < 0 else 'soon' if days <= soon_days else 'ok'), days


def expiry_text(key, days, date, long=False):
    """the table's cell (long=False) or the photo card's line (long=True)"""
    if key == 'none':
        return 'no expiry date' if long else '–'
    n = abs(days); d = '%d day%s' % (n, '' if n == 1 else 's')
    if key == 'expired':
        return 'EXPIRED %s (%s ago)' % (date, d) if long else 'EXPIRED %s' % date
    if key == 'soon':
        when = 'today' if days == 0 else 'in ' + d
        return 'Expires %s (%s)' % (when, date) if long else '%s · %s' % (when, date)
    return 'Expires %s' % date if long else date


def is_fresh(i, started):
    """the item has a position, and it is from the current scan"""
    return bool(started) and i.get('x_m') is not None and (i.get('loc_updated_s') or i.get('updated_s') or 0) >= started - 1


def build(r, today=None, soon_days=SOON_DAYS):
    today = today or datetime.date.today()
    scan = r.get('scan') or {}
    running = bool(scan) and scan.get('ended') is None
    cut_short = bool(scan) and scan.get('ended') is not None and not scan.get('complete')
    day = today.isoformat()
    rows, by_name = [], {}
    for i in r.get('items') or ():
        on, missed = bool(i.get('is_on_shelf')), int(i.get('missed_scans') or 0)
        no, name = (i['no'], i['name']) if 'no' in i else split_name(i['rfid_uid'], i.get('item_name'))
        if on:
            state, text = 'on', 'on shelf'
        elif missed:
            state, text = 'missed', 'missed %d scan%s' % (missed, '' if missed == 1 else 's')
        elif running:
            state, text = 'wait', 'not seen yet'
        else:
            state, text = 'wait', 'not seen (scan not finished)' if cut_short else '–'
        ekey, days = expiry(i.get('expiration_date'), today, soon_days)
        date = str(i.get('expiration_date') or '').strip()[:10] if ekey != 'none' else ''
        seen = i.get('last_seen') or ''
        row = dict(uid=i['rfid_uid'], no=no, nos=i.get('nos') or ([no] if no is not None else []), no_text='–' if no is None else str(no), name=name,
                   name_text=name or '–', label=label(i['rfid_uid'], no, name), words=(name or ('tag %d' % no if no is not None else '')).lower(), state=state, state_text=text, missed=missed, on=on,
                   shelf=i.get('shelf'), shelf_text=('S%d' % i['shelf']) if i.get('shelf') is not None else ('·' if i.get('x_m') is not None else ''),
                   located=i.get('x_m') is not None, exp=ekey, days=days, date=date, exp_text=expiry_text(ekey, days, date),
                   seen=seen, seen_text=seen[11:16] if seen[:10] == day else seen[5:10], par=i.get('par'))
        # what needs a person first: missing, expired on the shelf, expiring on the shelf, the rest, not seen
        row['rank'] = 0 if state == 'missed' else 4 if state == 'wait' else 1 if ekey == 'expired' else 2 if ekey == 'soon' else 3
        row['tag'] = ('miss', 'exp', 'soon', 'on', 'wait')[row['rank']]
        rows.append(row)
        if name:
            by_name.setdefault(name, []).append(row)
    products = []
    for name, tags in by_name.items():
        on = [t for t in tags if t['on']]
        expired_on = sum(1 for t in on if t['exp'] == 'expired'); usable = len(on) - expired_on
        par = next((t['par'] for t in tags if t['par'] is not None), None)
        below = par is not None and usable < par
        nxt = min((t for t in on if t['exp'] in ('soon', 'ok')), key=lambda t: t['days'], default=None)
        p = dict(name=name, words=name.lower(), tags=tags, total=len(tags), on=len(on), usable=usable, expired_on=expired_on, par=par, below=below,
                 missed=sum(1 for t in tags if t['state'] == 'missed'), soon_on=sum(1 for t in on if t['exp'] == 'soon'),
                 stock_text='%d of %d%s' % (usable, len(tags), ' (+%d expired)' % expired_on if expired_on else ''),
                 par_text='–' if par is None else str(par),
                 status_text=('BELOW PAR by %d' % (par - usable)) if below else 'ok' if par is not None else '',
                 next_days=nxt['days'] if nxt else None, next_text=nxt['exp_text'] if nxt else ('all expired' if on and expired_on == len(on) else '–'))
        p['rank'] = 0 if below else 1 if expired_on else 2 if p['soon_on'] else 3 if on else 4
        p['tag'] = ('miss', 'exp', 'soon', 'on', 'wait')[p['rank']]
        products.append(p)
    counts = dict(items=len(rows), products=len(products), unnamed=sum(1 for x in rows if not x['name']), named=sum(1 for x in rows if x['name']), on=sum(1 for x in rows if x['on']), missed=sum(1 for x in rows if x['state'] == 'missed'),
                  wait=sum(1 for x in rows if x['state'] == 'wait'), expired=sum(1 for x in rows if x['on'] and x['exp'] == 'expired'),
                  soon=sum(1 for x in rows if x['on'] and x['exp'] == 'soon'), below=sum(1 for p in products if p['below']),
                  tracked=sum(1 for p in products if p['par'] is not None), located=sum(1 for x in rows if x['on'] and x['located']))
    return dict(rows=rows, products=products, counts=counts, scan=scan, running=running, cut_short=cut_short, scans_done=r.get('scans_done') or 0,
                started=r.get('scan_started') or 0, cleared=bool(r.get('cleared')),
                below_names={p['name'] for p in products if p['below']})


def empty_facets():
    return dict(state=set(), exp=set(), shelf=set(), below=False)


def any_filter(words, facets):
    return bool(words or facets['state'] or facets['exp'] or facets['shelf'] or facets['below'])


def row_passes(row, words, facets, below_names):
    return (all(w in row['words'] or (w.lstrip('#').isdigit() and int(w.lstrip('#')) in row['nos']) for w in words) and (not facets['state'] or row['state'] in facets['state'])
            and (not facets['exp'] or row['exp'] in facets['exp']) and (not facets['shelf'] or row['shelf'] in facets['shelf'])
            and (not facets['below'] or row['name'] in below_names))


def product_passes(p, words, facets, below_names):
    """an item name passes when one of its tags does"""
    return any(row_passes(t, words, facets, below_names) for t in p['tags'])


def median(v):
    v = sorted(v); n = len(v)
    return None if not n else v[n // 2] if n % 2 else (v[n // 2 - 1] + v[n // 2]) / 2


def score(r, truth):
    """the estimates of the current scan against the tape-measured positions (see "within 1 m3" at the top)"""
    started = r.get('scan_started') or 0
    errs, shelf_ok, shelf_n, unplaced = [], 0, 0, 0
    for i in list(r.get('items') or ()) + list(r.get('located_other') or ()):
        t = truth.get(i.get('place_uid') or i['rfid_uid'])       # an object: the tape position of the tag whose estimate stands for it
        if t is None:
            continue
        if is_fresh(i, started):
            errs.append(math.dist((i['x_m'], i['y_m'], i['z_m']), t[:3]))
            if i.get('shelf') is not None and t[3] is not None:
                shelf_n += 1; shelf_ok += int(i['shelf']) == t[3]
        elif i.get('is_on_shelf'):
            unplaced += 1                # read in this scan, has a tape position, but the localiser has not placed it
    return dict(n=len(errs), within=sum(1 for e in errs if e < WITHIN_M), median_m=median(errs), shelf_ok=shelf_ok, shelf_n=shelf_n, unplaced=unplaced)


def metrics(r, truth, run=None):
    """{key: (value, what it is counted over)} for the metrics row; '–' while there is nothing to count"""
    s, n_items = score(r, truth), r.get('n_items') or 0
    n_on = r.get('n_on_shelf') or 0
    started = r.get('scan_started') or 0
    placed = sum(1 for i in r.get('items') or () if i.get('is_on_shelf') and is_fresh(i, started))
    pct = lambda a, b: '%.0f %%' % (100.0 * a / b) if b else '–'
    none = 'no placed item with a tape position yet' if truth else 'no tape positions for this site'
    out = {
        'within': (pct(s['within'], s['n']), ('%d of %d placed items with a tape position' % (s['within'], s['n'])
                                            + (' · %d more read, not placed' % s['unplaced'] if s['unplaced'] else '')) if s['n'] else none),
        'median': ('%.0f cm' % (100 * s['median_m']) if s['n'] else '–', '3D, %d placed items with a tape position' % s['n'] if s['n'] else none),
        'shelf': (pct(s['shelf_ok'], s['shelf_n']), '%d of %d on the tape sheet\'s shelf' % (s['shelf_ok'], s['shelf_n']) if s['shelf_n']
                  else 'this method gives no shelf' if s['n'] else none),
        'detected': (pct(n_on, n_items), '%d of %d items in the inventory' % (n_on, n_items)),
        'placed': (pct(placed, n_on), '%d of %d items read' % (placed, n_on)),
    }
    if run:
        out['time'] = ('%d:%02d' % divmod(int(run.get('elapsed_s') or 0), 60), 'this run so far' if run.get('alive') else 'the last run')
        rd = run.get('reads') or {}
        n = rd.get('rows') or 0             # short, so that it fits a narrow tile: 126.7 k
        out['reads'] = ('%.1f k' % (n / 1000.0) if n >= 10000 else str(n), '%d a second now, %d tags' % (rd.get('rate') or 0, rd.get('tags') or 0) if run.get('alive')
                        else '%d reads, %d tags' % (n, rd.get('tags') or 0))
    else:
        out['time'] = out['reads'] = ('–', 'no run on the robot')
    return out


if __name__ == '__main__':      # the rules on a made-up inventory
    T = datetime.date(2026, 11, 14)
    it = lambda uid, name, on, missed=0, exp=None, par=None, **kw: dict(rfid_uid=uid, item_name=name, is_on_shelf=int(on), missed_scans=missed,
                                                                     expiration_date=exp, par=par, last_seen='2026-11-14 12:00:00', **kw)
    st = {'scan_started': 100.0, 'scan': {'ended': 200.0, 'complete': 1}, 'scans_done': 2, 'n_items': 7, 'n_on_shelf': 5, 'items': [
        it('A1', 'Gown', True, exp='2028-01-01', par=3, x_m=0.0, y_m=0.0, z_m=0.0, shelf=1, loc_updated_s=150.0),
        it('A2', 'Gown', True, exp='2026-11-13', par=3, x_m=0.5, y_m=0.0, z_m=0.0, shelf=1, loc_updated_s=150.0),      # expired yesterday
        it('A3', 'Gown', True, exp='2026-11-14', par=3, x_m=0.7, y_m=0.0, z_m=0.0, shelf=2, loc_updated_s=150.0),      # expires today: soon
        it('A4', 'Gown', False, missed=2, exp='2028-01-01', par=3, x_m=0.0, y_m=0.0, z_m=0.0, shelf=1, loc_updated_s=50.0),
        it('B1', 'Item 10', True), it('B2', 'Item 2', True, exp='2026-12-14'), it('B3', 'Item 3', False)]}
    raw = st; v = build(st, T)
    by = {x['uid']: x for x in v['rows']}; gown = next(p for p in v['products'] if p['name'] == 'Gown')
    assert [by[u]['state_text'] for u in ('A1', 'A4', 'B3')] == ['on shelf', 'missed 2 scans', '–'], [by[u]['state_text'] for u in by]
    assert [by[u]['exp'] for u in ('A1', 'A2', 'A3', 'B1', 'B2')] == ['ok', 'expired', 'soon', 'none', 'soon']
    assert (gown['on'], gown['usable'], gown['below'], gown['status_text']) == (3, 2, True, 'BELOW PAR by 1'), gown      # the expired one does not count
    assert [(by[u]['no'], by[u]['name']) for u in ('A1', 'B1', 'B3')] == [(None, 'Gown'), (10, ''), (3, '')]
    assert [by[u]['label'] for u in ('A1', 'B1')] == ['Gown', 'Tag 10'] and label('x', 30, 'Gown') == 'No. 30 · Gown'
    assert [p['name'] for p in v['products']] == ['Gown']           # a tag without a name is stock of nothing
    assert v['counts'] == dict(items=7, products=1, unnamed=3, named=4, on=5, missed=1, wait=1, expired=1, soon=2, below=1, tracked=1, located=3), v['counts']
    assert natural('Item 2') < natural('Item 10') and (by['A1']['seen_text'], build(st, T + datetime.timedelta(1))['rows'][0]['seen_text']) == ('12:00', '11-14')
    f = empty_facets(); f['state'] = {'on'}; f['exp'] = {'expired', 'soon'}
    assert [x['uid'] for x in v['rows'] if row_passes(x, [], f, v['below_names'])] == ['A2', 'A3', 'B2']
    find = lambda *w: [x['uid'] for x in v['rows'] if row_passes(x, list(w), empty_facets(), set())]
    assert (find('tag', '1'), find('#3'), find('gown', '3'), find('go')) == (['B1'], ['B3'], [], ['A1', 'A2', 'A3', 'A4'])
    assert split_name('x', ' item #12 ') == (12, '') and split_name('x', 'Items 12') == (None, 'Items 12') and split_name('x', None) == (None, '')
    truth = {'A1': (0.0, 0.0, 0.0, 1), 'A2': (0.5, 0.0, 0.61, 1), 'A3': (0.7, 0.0, 0.62, 1), 'A4': (0.0, 0.0, 0.0, 1), 'B1': (1.0, 1.0, 1.0, 3)}
    s = score(st, truth)        # A1 0 m, A2 0.61 m (inside), A3 0.62 m (not inside: the test is <); A4 is from an older scan; B1 read, not placed
    assert (s['n'], s['within'], s['shelf_ok'], s['shelf_n'], s['unplaced']) == (3, 2, 2, 3, 1), s
    m = metrics(st, truth)
    assert m['within'][0] == '67 %' and m['detected'][0] == '71 %', m
    st['scan'] = {'ended': None}; assert build(st, T)['rows'][6]['state_text'] == 'not seen yet'
    st['scan'] = {'ended': 5.0, 'complete': 0}; assert build(st, T)['rows'][6]['state_text'] == 'not seen (scan not finished)'
    # the catalog: registry numbers and the names file decide what the inventory is and what it is called
    assert parse_numbers('30') == [30] and parse_numbers(' #30 31; 40 - 42') == [30, 31, 40, 41, 42] and parse_numbers('Item 7') == [7]
    assert parse_numbers('3o') is None and parse_numbers('9-3') is None and parse_numbers('') is None
    import tempfile
    with tempfile.TemporaryDirectory() as d:
        reg, nf = os.path.join(d, 'reg.csv'), os.path.join(d, 'names.csv')
        open(reg, 'w').write('epc,name,category\na2,Item 30,dry\nB3,Item 7,dry\nA1,Item 31,dry\nR1,Ref S1 TL,reference\n')
        open(nf, 'w').write(NAMES_HEAD + '30-31,"Gown, blue",2027-01-05\n7 , Stapler, 1 Jan\nseven,Oops\n31,Gown XL\n\n# a note\n8,\n')
        cat = Catalog(reg, nf)
        assert cat.numbers == {'A2': 30, 'B3': 7, 'A1': 31} and cat.names == {30: 'Gown, blue', 31: 'Gown XL', 7: 'Stapler'}, (cat.numbers, cat.names)
        assert cat.expires == {30: '2027-01-05'}, cat.expires           # 31 was named again without a date; "1 Jan" is not a date
        assert len(cat.problems) == 3 and '1 Jan' in cat.problems[0] and 'seven' in cat.problems[1] and 'item 31 was' in cat.problems[2], cat.problems
        write_names(nf, {30: 'Gown, blue', 31: 'Gown, blue', 33: 'Gown, blue', 7: 'Stapler'}, {31: '2027-01-05', 33: '2027-01-05'}); cat._at = 0
        assert cat.refresh() and cat.names == {30: 'Gown, blue', 31: 'Gown, blue', 33: 'Gown, blue', 7: 'Stapler'} and not cat.problems
        assert cat.expires == {31: '2027-01-05', 33: '2027-01-05'} and open(nf).read().endswith('30,"Gown, blue"\n31 33,"Gown, blue",2027-01-05\n7,Stapler\n')
    cat = Catalog(numbers={'A2': 30, 'B3': 7}, named={30: 'Gown', 3: 'not B3: the registry says 7'}); cat.expires = {7: '2030-02-02'}
    raw['pars'] = {'Gown': 2, 'Item 2': 9}
    ap = cat.apply(raw)             # A2 by the registry; B1, B2 by the database's "Item N"; B3 is 7 (registry wins); the rest have no number: out
    assert [(i['rfid_uid'], i['no'], i['name'], i['par']) for i in ap['items']] == [('A2', 30, 'Gown', 2), ('B1', 10, '', None), ('B2', 2, '', None), ('B3', 7, '', None)]
    assert (ap['n_items'], ap['n_on_shelf'], ap['left_out'], len(raw['items'])) == (4, 3, 3, 7)
    assert [i['expiration_date'] for i in ap['items']] == ['2026-11-13', None, '2026-12-14', '2030-02-02']       # the file's date, else the database's
    v2 = build(ap, T)
    assert v2['counts']['items'] == 4 and [(p['name'], p['total'], p['par'], p['below']) for p in v2['products']] == [('Gown', 1, 2, True)]
    assert metrics(ap, {})['detected'] == ('75 %', '3 of 4 items in the inventory')
    # objects with several tags: one item, on shelf when any tag was read, placed where its most-read tag is, scored against that tag
    tg = lambda uid, on, missed=0, seen=None, **kw: dict(rfid_uid=uid, item_name=None, is_on_shelf=int(on), missed_scans=missed, last_seen=seen, expiration_date=None, **kw)
    with tempfile.TemporaryDirectory() as d:
        reg = os.path.join(d, 'reg.csv')
        open(reg, 'w').write('epc,name,category,object\nT48,Item 48,liquid,Item 48\nT49,Item 49,liquid,Item 48\nT50,Item 50,liquid,Item 48\n'
                             'T60,Item 60,dry,\nT61,Item 61,dry,Item 61\nT70,Item 70,metal,Item 9\nT71,Item 71,metal,Item 9\n')
        nb, ob = load_registry(reg)
        assert ob == {'T48': 'Item 48', 'T49': 'Item 48', 'T50': 'Item 48', 'T70': 'Item 9', 'T71': 'Item 9'}, ob      # T61 is alone on its object: a plain tag
    cat = Catalog(numbers=nb, objects=ob, named={48: 'Gown', 49: 'Gown', 50: 'Gown', 60: 'Kit', 70: 'Clip', 71: 'Clamp'})
    assert cat.clashes() == ['tags 70 71 are one object but have different names: "Clip" is used'], cat.clashes()
    so = {'scan_started': 100.0, 'scan': {'ended': 200.0, 'complete': 1}, 'pars': {'Gown': 2}, 'items': [
        tg('T49', True, seen='2026-11-14 12:05:00', x_m=1.0, y_m=0.0, z_m=0.0, shelf=3, n_reads=700, region90_m=0.2, loc_updated_s=150.0),
        tg('T60', True, seen='2026-11-14 12:00:00', x_m=5.0, y_m=0.0, z_m=0.0, shelf=1, n_reads=10, loc_updated_s=150.0),
        tg('T48', False, missed=3, x_m=9.0, y_m=9.0, z_m=0.0, shelf=7, n_reads=5000, loc_updated_s=50.0),          # many reads, but from an older scan
        tg('T50', True, missed=0, seen='2026-11-14 12:01:00', x_m=1.3, y_m=0.0, z_m=0.0, shelf=3, n_reads=200, loc_updated_s=150.0),
        tg('T70', False, missed=2), tg('T71', False, missed=5)]}
    ao = cat.apply(so); g = ao['items'][0]
    assert [(i['rfid_uid'], i['no'], i['nos'], i['name']) for i in ao['items']] == [('T48', 48, [48, 49, 50], 'Gown'), ('T60', 60, [60], 'Kit'), ('T70', 70, [70, 71], 'Clip')]
    assert (g['is_on_shelf'], g['missed_scans'], g['last_seen'], g['x_m'], g['shelf'], g['n_reads'], g['place_uid'], g['par']) == (1, 0, '2026-11-14 12:05:00', 1.0, 3, 700, 'T49', 2)
    assert (ao['items'][2]['is_on_shelf'], ao['items'][2]['missed_scans'], ao['items'][2]['x_m']) == (0, 2, None)      # no tag read for 2 scans
    assert (ao['n_items'], ao['n_on_shelf'], ao['merged'], ao['left_out']) == (3, 2, 3, 0)
    vo = build(ao, T); fo = lambda *w: [x['no'] for x in vo['rows'] if row_passes(x, list(w), empty_facets(), set())]
    assert [(x['no'], x['state_text']) for x in vo['rows']] == [(48, 'on shelf'), (60, 'on shelf'), (70, 'missed 2 scans')]
    assert (fo('50'), fo('#71'), fo('gown', '49'), fo('5')) == ([48], [70], [48], []) and [(p['name'], p['total'], p['usable']) for p in vo['products']][0] == ('Gown', 1, 1)
    s2 = score(ao, {'T48': (9.0, 9.0, 0.0, 7), 'T49': (1.0, 0.0, 0.1, 3), 'T50': (4.0, 0.0, 0.0, 3), 'T60': (5.0, 0.0, 0.0, 1)})       # the gown is scored against T49's tape position
    assert (s2['n'], s2['within'], s2['shelf_ok'], round(s2['median_m'], 2)) == (2, 2, 2, 0.05), s2
    assert metrics(ao, {})['detected'] == ('67 %', '2 of 3 items in the inventory')
    print('inventory_model: all checks passed')
    for k, (val, sub) in m.items():
        print('  %-9s %-6s %s' % (k, val, sub))
