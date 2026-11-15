#!/usr/bin/env python3
"""
make_dummy_db.py - make a throwaway master database for testing pc_receiver.py.

    python3 make_dummy_db.py dummy_master.db [--registry ../RFID/tag_registry.csv] [--no-seed]

Copies seed_medical_inventory.db and rebuilds its index (the seed's index is missing one row, so that
item cannot be found by rfid_uid until it is rebuilt). The seed itself is not touched.

--registry adds every item tag of the robot's tag registry that the seed does not have (named as in the registry,
e.g. "Item 12"; the shelf corner tags, category "reference", are left out), so a run in the corridor has a row
for every tagged item. Without it only the seed's 72 items exist and the other tags come back as unknown.

--no-seed starts from an empty table instead of the seed: with --registry the database then holds the registered item
tags and nothing else. The scan demo builds its test database this way (demo/run_scan_demo.sh): its inventory is the
registry, and its names come from the names file, so it needs nothing of the seed. Such a database has no expiry dates.
"""
import csv
import os
import shutil
import sqlite3
import sys

SEED = os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', 'seed_medical_inventory.db')

REG = sys.argv[sys.argv.index('--registry') + 1] if '--registry' in sys.argv[:-1] else None
NO_SEED = '--no-seed' in sys.argv
ARGS = [a for a in sys.argv[1:] if a not in ('--registry', REG, '--no-seed')]
if len(ARGS) != 1:
    sys.exit(__doc__)
out = ARGS[0]
if os.path.exists(out):
    sys.exit('%s already exists - delete it first or pick another name' % out)
if NO_SEED:
    conn = sqlite3.connect(out)          # the seed's layout, empty
    conn.execute('CREATE TABLE medical_supplies (rfid_uid TEXT PRIMARY KEY, item_name TEXT, expiration_date TEXT, '
                 'date_labeled TEXT, last_seen TEXT, is_on_shelf INTEGER DEFAULT 0)')
else:
    shutil.copy(SEED, out)
    conn = sqlite3.connect(out)
    conn.execute('REINDEX')
    conn.execute('UPDATE medical_supplies SET last_seen = NULL, is_on_shelf = 0')
added = 0
if REG:
    for r in csv.DictReader(open(REG)):
        if r.get('category') == 'reference' or not r.get('epc'):
            continue
        added += conn.execute('INSERT OR IGNORE INTO medical_supplies (rfid_uid, item_name, is_on_shelf) VALUES (?, ?, 0)',
                              (r['epc'].strip().upper(), r.get('name') or r['epc'])).rowcount
    print('added %d item tags from %s' % (added, REG))
conn.commit()
print('%s: %d items, check: %s' % (out, conn.execute('SELECT COUNT(*) FROM medical_supplies').fetchone()[0],
                                 conn.execute('PRAGMA integrity_check').fetchone()[0]))
