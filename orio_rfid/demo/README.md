# Scan demo: one command, one window

The robot drives the corridor by itself and reads the RFID tags; a PC shows the run, marks every item present or
missing in the inventory, and places each item on a shelf with one of the localisation methods F1 to F4.

```
PC (abuela today)                                       robot (stretch-re1-1076)
  demo/run_scan_demo.sh                                   orio_stretch/scripts/demo_agent.py     <- started over ssh
    sync/pc_receiver.py   <-- sightings, posed reads ---    scripts/rfid_nav_run.sh              (the run itself)
    loc/live/engine.py    --> item_locations table          RFID/rfid_pose_live.py               (posed reads, live)
    demo/scan_dashboard.py --- commands, status -------->   sync/robot_sync.py --follow          (sends them)
```

## Run it

On the PC:

```bash
demo/run_scan_demo.sh              # receiver + robot agent + item localiser + the window
demo/run_scan_demo.sh --stop       # stop everything, including the robot's agent
```

A PC without a checkout of orio-main gets the files from the robot with `demo/push_to_pc.sh <user@pc>` (it copies
`orio_rfid/{demo,sync,loc}` to `~/orio_scan/orio_rfid`), then runs `~/orio_scan/orio_rfid/demo/run_scan_demo.sh`.

Rehearsal without moving the robot: in the window choose **Replay an old run** and press START. The robot's agent
plays the reads of that run back at real speed; everything after the reader behaves as in a real run.
`demo/run_scan_demo.sh --local` runs both ends on one machine (for example on the robot itself).

The map (`demo/scene_view.py`) is the scene of the run-2 replay page drawn with numpy and Pillow instead of a browser: same room
frame, shelf colours and point cloud (`loc/site/scene_v2.json`, built by `loc/site/make_scene.py`). Without numpy or Pillow on the
PC the window still works and draws the route only.

## Settings: `demo/demo.conf`

`demo.conf` is not in git, because it holds this site's addresses. On a fresh checkout copy `demo.conf.example` to
`demo.conf` and fill in `ROBOT_HOST` and `PC_HOST`.

| Key | Meaning |
| --- | --- |
| `ROBOT_HOST`, `ROBOT_USER` | the robot as the PC reaches it (ssh and port `AGENT_PORT`, default 8123) |
| `PC_HOST` | this PC as the **robot** reaches it (the robot posts to port `RECEIVER_PORT`, default 8000) |
| `PYTHON` | Python on the PC: tkinter, and torch / numpy / pandas / scipy when `ENGINE=pc` |
| `DB` | SQLite inventory. If the file is missing a test copy is built (every item tag of `RFID/tag_registry.csv`, nothing else). The window shows the tags that have an item number, and no others |
| `NAMES` | the names file **on the PC**: item number -> name (default: `item_names.csv` next to `DB`; the window makes it). See "Item numbers and names" |
| `ENGINE` | `pc`: the robot sends its reads and this PC computes. `robot`: the robot computes. `off` |
| `METHOD` | default method; the window's F1..F4 buttons choose it per run |
| `CELLS` | `all` (default): the whole room grid, exactly as the offline method. `shelf`: only the cells in or near the shelf boxes, for F3 / F4; each item then gets a new position every 4 s instead of every 11 s, and 1 item in 130 moved by 2 cm on one of three recordings (`../loc/README.md`, "Speed and delay") |
| `MAP`, `RUN_DIR`, `WAYPOINTS`, `LAPS`, `START_AT` | the site, as paths **on the robot**. For the corridor since 14 Nov: `hospital_corridor_v2_cap` and `.../orbslam/ir-rgbd_map_clean_140_cap` |
| `FEATURE_BAND` | `LOW HIGH` in metres, e.g. `-1 1.143`: ORB-SLAM3 only uses features in that height band (here: from the floor up to 1.143 m, so people behind the 1.14 m walls do not disturb it). The agent passes it to `rfid_nav_run.sh --feature-band`, which uses the patched ORB-SLAM3 in `~/orbslam_ws_band`. It belongs with a map built the same way (`..._cap`): both or neither; the START dialog says so when they do not match. Left out: features at any height |
| `GUI_PYTHON` | the Python for the window, if it must differ from `PYTHON` (see "Fonts and symbols"). Left out: chosen by the launcher |
| `FONT` | the window's text font, if you want a particular installed one (see "Fonts and symbols") |
| `LIFT_SPEED` | speed of the lift's up-and-down wave during a run, m/s (0.10). The agent turns it into the run script's `--period` for the wave's range, so a wider range no longer means a faster lift. Left out: the run script's own period |
| `PHOTOS` | folder **on the PC** with the item photos (default: `item_photos` next to `DB`; the window makes it). See "Item photos" |
| `EXPIRY_SOON_DAYS` | an item counts as "expiring" this many days before its date (default 30) |

Today both addresses are Tailscale addresses. On the team router (`TeamG_ORIO_5G`, no internet) only `ROBOT_HOST`
and `PC_HOST` change. The PC needs an ssh key on the robot (`ssh-copy-id hello-robot@<robot>`), and on the PC port
8000 must be open to the robot (`sudo ufw allow 8000/tcp` if ufw is on).

## The window

One window, two screens: **INVENTORY** (map, item table, metrics: the one to show) and **OPERATE** (setup, checks, log,
manual control). The two buttons in the top bar, or F2, switch between them. The top bar (START, PAUSE, STOP, E-STOP)
and the four link lights are on both, and Esc is E-STOP on both, wherever the cursor is.

Everything grows with the window: text, buttons, table rows and the map's marks have their plain size at 1280 x 800 and
scale with the smaller of width and height (from 0.8 to 3.2 times). Ctrl + / Ctrl - make everything larger or smaller for the
same window, Ctrl 0 puts that back. **LIGHT / DARK** in the top bar switches the colours; light is for a projector or a
bright room. Theme, screen, window size, zoom and the metrics shown are remembered in `~/.orio_scan_window.json` on the PC.

| Part | What it does |
| --- | --- |
| START | real run: asks once, stops the robot's teleop server, runs `rfid_nav_run.sh`. Replay: plays the chosen old run |
| PAUSE / RESUME | the base holds at the spot (the goal is cancelled and sent again on resume); lift and reader keep going |
| STOP | ends the run cleanly: everything is stopped and the reads so far are still posed |
| E-STOP (or Esc) | `/runstop` of the driver: all motion stops and stays stopped, then the run is stopped. Release with the button on the robot's head (hold 2 s) or RELEASE RUNSTOP while a driver is running |
| PREFLIGHT, HOME, CLEAN UP | `rfid_preflight.sh`, `home_payload.py` (asks first: the lift goes to the top), `ros_clean.sh` |
| Checks | one line per thing that must work: agent, receiver, teleop server, preflight, navigation, reader, antenna pose, reads at the PC, route, presence, localiser, locations. **Localiser** is the localiser program itself: it reports in every round, the line shows what it is doing, and turns red (`NOT RUNNING`, or the reason it stopped) when it has not been heard for 20 s. **Locations** turns red when reads are flowing but no position has arrived for 30 s |
| Map | the room in **2D** (from above) or **3D**, switched with the buttons above it: shelf boxes, the map's point cloud, waypoints, the driven path, the robot, and every item where the localiser has placed it so far. Drawn as on the run-2 replay page: a ball in the tag's own colour (its shelf's hue, its own shade) where it was placed in this scan, a diamond in the same colour at its tape-measured position, and a line between the two. Grey ring = last known place from an earlier scan, red cross = missing. Drag to move (3D: orbit, right-drag to move), wheel to zoom, double-click to reset. Point at an item for its name, shelf, uncertainty and read count; click it, or its row in the inventory, to mark it in both. Switches: point cloud, uncertainty (a translucent circle of the 90 % region's size), tape positions (on at the start; the 9 Nov measurement; with it on, the corner shows the median 3D error and an item's label says how far it is from its tape position) |
| Item card | click an item on the map, or its row: the map shows its uncertainty region as before, and a small card in the map's bottom right corner shows its photo, tag number, name and expiry date. The ✕ on the card, or a click on empty map, puts it away |
| Inventory tiles | ON SHELF, MISSING, EXPIRED (on the shelf and past its date), EXPIRING (on the shelf, within `EXPIRY_SOON_DAYS`), BELOW PAR (item names under their PAR). A click on a tile shows just those rows; a second click shows all again |
| Search and filters | the search box looks at names and tag numbers: every word typed must be in the name or be the number (`gown`, `30`, `#30`, `gown 17`; an item without a name also answers to `tag 30`). An object with several tags is also found by the numbers of its other tags. Filter boxes: state, expiry, below PAR, shelf. Boxes on one line widen (on shelf OR missing), lines narrow each other (on shelf AND expired). What does not pass is drawn faint on the map. No pop-up for expired items: they are a tile, a filter and a column |
| ITEMS table | one row per item: its **tag no.** and its **name** (see "Item numbers and names"), then `on shelf`, `missed N scans`, `not seen yet` (a scan is going), its shelf from the localiser, its expiry date (`EXPIRED ...`, `in 12 days ...`) and when it was last read. A click on a column heading sorts |
| STOCK table | one row per item name: how many usable ones are on the shelf out of how many tags have that name, the PAR, and `BELOW PAR by N`. **Expired items do not count as stock.** Double-click a row to type its PAR (empty = not tracked; most names have none). Tags without a name are not in this table; the line over it says how many those are |
| Metrics | the METRICS button over the map (INVENTORY screen) ticks which numbers show under the map: within 1 m³, median error, right shelf, detected, placed, scan time, reads. See "The metrics" |
| Head camera | in manual mode, with no run going, the CAMERA button of the map panel shows the head camera (about 6 pictures a second, upright). `cam_stream.py` on the robot opens the camera once the driver is up and is stopped before a run, preflight or homing needs it. Not shown during a run |
| Manual control | MANUAL ON asks once, stops the teleop server, starts only the driver and then, by itself, goes to the run pose: lift up to the saved height first (with the payload effort from homing), then arm in, wrist turned in to 75 degrees short of stowed (2.09 rad; a run stows it fully, 3.40 rad, at its own start), head to its usual pan / tilt. POSE AGAIN repeats it. Everything on the pad is hold-to-move: the robot moves only while a button or key is held, with the pointer over the pad for keys, and stops 0.5 s after the last command at the latest. Base: the six pad buttons or W A S D / Q E, also while a run is paused. Lift: ▲ ▼ or R / F, up to 0.10 m/s, only with the arm in, from 0.17 to 1.05 m (the range a run waves over since the robot was recalibrated on 14 Nov; the window sends no lift range of its own for a run). If the lift meets something it stops and waits until the button is let go. Camera: the four arrows or I J K L, up to 0.5 rad/s; the middle button puts it back. The speed slider scales all three. MANUAL OFF puts the head back to its saved pan / tilt **before** the driver stops, so the camera looks the same way at the start of every run (a run also sets it at its start) |
| Head camera | in manual mode, with no run going, the CAMERA button of the map panel shows the head camera (about 6 pictures a second, upright); the view switches to it when manual mode is ready. `cam_stream.py` on the robot opens the camera once the driver is up and is stopped before a run, preflight or homing needs it. Not shown during a run |

The robot's agent accepts commands only with the token in `~/.orio_demo_token` on the PC (made on first start).

## Missed scans

A scan is one run from START to its end. When a run is over the robot's agent tells the PC (`POST /scan`) whether it
drove its route to the end by itself. Only then does every item the scan did not read get one more missed scan; an item that
is read again goes back to `on shelf` at once. A run that was stopped, E-stopped or failed is recorded but counts for
nothing: it says nothing about the shelves it never reached, and the window shows `not seen (scan not finished)` for
what it did not read. The line at the top of the inventory says which scan the numbers are from, and warns when a
finished scan did not reach all its waypoints (its missed counts can then be too high).
With `LAPS=2` both laps are one scan: an item taken away between the laps was still read in that scan.

## The metrics

All of them are about the current scan. The three accuracy numbers use the tape-measured positions of
`loc/site/ground_truth_v2.csv` (153 items, measured 9 Nov; the shelf corner tags are not items) and only the items the
localiser has placed in this scan:

| Metric | What it is |
| --- | --- |
| Within 1 m³ | the share of those items whose estimate is less than 0.62 m from the tape position in 3D. 0.62 m is the radius of a sphere of 1 m³ (0.6204 m), and `< 0.62` is the test the offline study reports (`loc/offline/mlib.py`). An item without a tape position is not in the number; an item that was read but not placed is not either, and the tile says how many those are |
| Median error | the median of the same 3D distances (also on the map, top left) |
| Right shelf | of the same items, the share placed on the shelf the tape sheet has them on (F3 / F4; F1 and F2 give no shelf) |
| Detected | items of the database read in this scan |
| Placed | items read that have a position from this scan |
| Scan time, Reads | of the run on the robot |

An item that was moved since 9 Nov still has its old tape position, and counts as wrong where it is now.

## Item numbers and names

**Tag no.** (the registry and the names file say item number) is the number the tag was registered under: `Item 30` in
`RFID/tag_registry.csv` is tag no. 30. The window reads the registry copy on the PC (`push_to_pc.sh` brings it) and
reads it again when the file changes.

**An object with several tags is one item** (team decisions, 14 Nov). The registry's `object` column says which tags
are stuck on the same thing: 27 objects, 58 tags, all between 22 and 135 (13 water bottles, 5 staplers, ...). The window
shows one row, one map mark and one unit of stock for such an object, under the object's own number (48 for the tags
48, 49, 50); the other tags are shown nowhere, though the search box still finds the row by their numbers.

| | Rule |
| --- | --- |
| On shelf | any of its tags was read. `missed N scans`: none of them was read in the last N complete scans |
| Position | the estimate of its tag with the most reads, among the tags placed in this scan (else among older estimates). One real estimate, so the uncertainty region shown on a click is a real one |
| Metrics | count objects. The estimate is scored against the tape position of that same tag |
| Name, expiry, PAR | those of the object's own number. Tags of one object with different names in the names file: the window says so |

Only the window groups them: the database, the receiver and the robot keep one row per tag. Checked on the 21:19 run of
14 Nov: 153 tags become 122 items; the 4 water bottles with one unread tag are on shelf; within 1 m³ 91 % (107 of 118)
against 92 % (121 of 132) per tag, median 22 cm against 20 cm. Other position rules (average of the tags, always the
same tag) scored within a centimetre of this one on that run.

**The inventory of the demo is the tags that have an item number**, and no others (team decision, 14 Nov). A tag the
database lists but that was never registered is left out of every table, tile, count and metric; its row in the
database is not touched. The PC RECEIVER check on the OPERATE screen says how many are left out (24 in abuela's test
database: tags of the old seed inventory, none of them in the room).

**Names come from one file, `item_names.csv`**, next to the database on the PC (`NAMES`), and from nowhere else: not
from the database's `item_name`, not from the seed inventory. Add to it with any editor:

    item_no,name,expires
    1-13 154-186,PI Surgical Gloves,2027-12-31
    14 16-17 20,Surgical Gown XL (Blue),2028-08-24
    128,Asahi Peripheral Guide Wire
    151,"Gauze, sterile 4x4",2027-03-01

One line is the item numbers, a comma, the name, and if wanted a comma and the expiry date (`2027-11-24`). Numbers: one
(`30`), a range (`30-35`), or several with spaces (`30 31 40-42`). A name with a comma in it goes in double quotes. A
line with a date sets the expiry date of those items; a line without one leaves them the date the database has. For an
object with several tags, its own number is the one that counts. Lines starting with `#` are notes. A number given
twice: the later line wins, and the window says so. The window reads the file again a few seconds after it is saved
(no restart) and says in its status line how many numbers have a name, or which line it could not read. The line over
the ITEMS table counts how many items still have no name. An item without a name shows `–` and is not in STOCK.

The file starts with what the team entered in its "RFID Item Labeler" page by the evening of 14 Nov: all 286 item
numbers, 38 names, an expiry date for 249 of them (`demo/item_names.start.csv`; the window copies it to `NAMES` when
that file does not exist yet, and never again). The PAR is kept per name, so it holds for every tag that is given that
name later. The labeler's 36 chosen photos went into `item_photos/` on abuela, each named after its item; they are not
in this repository.

## Fonts and symbols

**The window needs a Python whose Tk draws text with Xft.** A Tk without it (abuela's conda Python has one) can only use
the X server's old bitmap fonts: rough letters, and every symbol an empty box. That is what the window looked like on
abuela until 14 Nov. The launcher now gives the window its own Python: `GUI_PYTHON` if set, else `PYTHON` if its Tk is
good, else `/usr/bin/python3` (which needs `python3-tk`, `python3-pil.imagetk`, `python3-numpy`; abuela has them). It
says which one it took. The receiver and the localiser stay on `PYTHON`. Started by hand with a wrong Python, the
window says so in red in its status line.

**Font:** the first of Inter, Roboto, Noto Sans, Open Sans, Source Sans, IBM Plex Sans, Lato, Liberation Sans, Arial,
Ubuntu, DejaVu Sans that the PC has (Lato on abuela and on the robot), or `FONT` from the conf file. The map's text
uses the same font.

**Symbols** (start, stop, arrows, turn, close, sort arrow, the boxes of checkboxes and radio buttons) are small
pictures drawn by `ui.py`, not characters, so they do not depend on what a font contains. The checkbox and radio boxes
are 13 pixels at every window size.

## Item photos

One folder on the PC (`PHOTOS`, default `~/orio_scan/item_photos`). A photo belongs to an item when the file is named
after the tag id, the item number or the item name; capitals, spaces and punctuation do not matter:
`E28011B0A50500726AC86888.jpg`, `item 30.jpg`, `30.png`, `Surgical Gown XL (Blue).jpg` and `surgical_gown_xl_blue.png`
all work (jpg, png, webp, gif, bmp). The tag id wins over the number, the number over the name, so one photo named
after the item serves all its tags unless a tag has its own. The folder is looked at again every few seconds, so a
photo dropped in shows up without a restart. No file: a drawn placeholder.

## Starting a run from nothing

By default a run builds on the ones before it: an item that is not read keeps its last known place (a grey ring on the
map), its count of missed scans goes up, and its last-seen time stays. **Untick "keep what earlier runs found" in SETUP
before START** (real run or replay) and none of that carries over. The window first asks the PC to forget (`POST /fresh`)
and sends the start to the robot only when it has; the START dialog says which of the two will happen.

| Forgotten | Kept |
| --- | --- |
| every item's position (`item_locations`), so no grey rings | the item list, names, expiry dates, PAR |
| the missed-scan counts | the `scans` table and every run's files under `runs/` (reads, `locations.json`) |
| on shelf (set to no for all, as each new scan does anyway) | `last_seen` in the table: other tools use that column, so it is only hidden from before that moment |
| in the window: last-seen times and the scan line from before | |

The box is ticked again whenever the window is opened. If the PC cannot clear (receiver not running, or an older one),
the run is **not** started and the status line says why.

## What is stored

| Where | What |
| --- | --- |
| `<DB folder>/item_names.csv` | the names, by item number: kept by the team, read by the window, never written by the demo once it exists |
| `<DB folder>/item_photos/` | the photos |
| `DB`, table `medical_supplies` | unchanged layout; `is_on_shelf` and `last_seen` are set by the sightings. A new run sets every item to not on shelf first |
| `DB`, tables `scans`, `item_status`, `products` | new, next to the others: one row per scan (start, end, complete or not, waypoints reached, items read), `missed_scans` per tag, `par` per item name. `medical_supplies` keeps the layout the arms' tools use |
| `DB`, table `item_locations` | one row per tag: `shelf`, `shelf_prob`, `x_m y_m z_m` (room frame), `region90_m`, `method`, `n_reads`, `run`, `updated`. Not cleared by a new run: for a missing item it is where it was last |
| `<DB folder>/runs/<label>/` | `reads_posed_live.csv` (every read with the antenna pose), `run.json`, `locations.json` |
| `<DB folder>/logs/` | `pc_receiver.log`, `engine.log` |
| robot `~/orio/datasets/<label>/` | the run folder of `rfid_nav_run.sh`, plus `reads_posed_live.csv`, `nav_events.jsonl`, `robot_sync.out` |
| robot `~/orio/maps/logs/` | `demo_agent.log`, `demo_<label>.log` |

## Tested, and not

Tested 2026-11-13, with run 2 (7 Nov) replayed at real speed on the robot and abuela as the PC, over Tailscale:

- all 127,446 reads reached abuela and its copy of the read log is identical to the robot's, also after the
  receiver was killed and restarted mid-run
- presence: 149 of the 177 items of the test database marked on shelf
- F4 on abuela's GPU kept 2 to 4 s behind the reads for the whole run; result as good as abuela's offline one
  (see `../loc/README.md`)
- pause / resume in `nav_waypoints.py` against a pretend `move_base` on a private ROS master
- the window, on the robot's own screen at 1024 x 740, against a replay; the 2D and 3D map with the 138 item positions of the
  22:36 run (replayed), and the renderer alone on abuela (10 ms a picture). The mouse controls were exercised in code, not by hand

Tested 2026-11-14 (the two screens, scaling, themes, inventory rules), all on the robot with a test database:

- the rules by themselves (`python3 demo/inventory_model.py`): states, expiry, stock against PAR without expired items, filters, the 0.62 m test
- missed scans end to end with the real agent replaying run 2 at 20 times speed: a finished replay closed the scan and counted
  28 items as missed; a replay stopped part-way was recorded as `stopped` and changed no count
- "within 1 m³" against a second calculation straight from the files (pandas): the same 119 of 140
- the window against a stand-in agent at 1000 x 700, 1280 x 800 and 2560 x 1440, both screens, both themes: PAR typed in, refused
  and cleared; sorting; search; the tiles; the metrics menu; resizing; the zoom keys; typing W A S D in the search box sends
  nothing to the robot; the pad's hold buttons still send what they did, and a button held while the window is rebuilt is let go
- item no. and name as two columns (same day, later), at 1280 x 800 and 2560 x 1440: numbers from the registry, `–` for a
  name not given yet, sorting by either column, search by number (`30`, `#22`, `gown 17`, `item 14`), the card's "ITEM NO."
  line, a photo found by its item number, STOCK without the unnamed tags
- the names file (same evening): the window made it from the starting copy; lines added with an editor while the window was
  open showed up in the tables within seconds (a range, a renamed item, a line it could not read was named in the status
  line); a PAR typed for a newly named item was kept and counted; the 24 unnumbered tags of the test database were left out
  (153 items instead of 177, MISSING 4 instead of 28); a test database built with `--no-seed` held the 286 registered tags
- the labeler's names, dates and photos (same evening): all 153 items of the test database named, the file's dates in the
  Expires column and the EXPIRED tile, the photo of the item's name on the card
- starting from nothing (14 Nov, late), on a scratch copy of the database: `POST /fresh` took 137 positions and all missed-scan
  counts away and left `last_seen`, the scans and the PARs in the tables; a new scan after it counted from 1; in the window,
  START with the box unticked posted `/fresh` first and the start 0.14 s later, with the box ticked only the start
- lift wave (14 Nov, late): in the first run on the new settings (`rfid_auto_20261114_223304`) the lift's contact stop ended
  the wave after 7 s and the antenna stayed at 0.13 m for both laps: range 0.17 to 1.05 m at the old 11 s period is 0.15 m/s,
  and the upward effort reached the 60 % threshold (47 % at the 0.08 m/s of earlier runs). Since then: `LIFT_SPEED=0.10`,
  the threshold written by `home_payload.py` is at least 75 % (user), and the window's Antenna pose check turns red when
  the lift stops waving. Checked without the robot: the start command, and the check line on that run's own lift events.
  **Not driven yet with these values.**
- the new navigation settings (14 Nov, late): the agent's start command checked without the robot (below); the old and the
  "cap" map compared on the mapping run's 4360 robot poses: 1.0 cm apart at the median, 1.9 cm at most, so the item
  localiser's map-to-room fit still holds. **Not driven from the window yet with these settings.**
- fonts and symbols (same evening): the window drawn by abuela's own `/usr/bin/python3` with abuela's fonts (into a test
  display on the robot), both screens, light and dark; and on the robot at 1000 x 700, 1280 x 800 and 2560 x 1440. Looked at
  in the pictures: every button symbol, the drive pad, checkboxes and radio buttons, the sort arrow, START switched off during
  a run. The same window drawn by abuela's conda Python showed the rough bitmap fonts
- objects with several tags as one item (same evening), on a snapshot of abuela's database after its 21:19 run: 122 rows,
  search by a hidden tag's number, stock per object (13 water bottles, not 26), metrics per object as in "Item numbers and names"

Not tested: any of this on abuela's screen, with a real run, or with real photos.

First try on the hardware (2026-11-13, 21:20): PREFLIGHT and HOME failed at once, because an agent started over
ssh had no ROS and no `HELLO_FLEET_PATH` (`~/.bashrc` sets them only in a terminal). Fixed in
`orio_stretch/scripts/orio_env.sh` and `demo_agent.py`, and checked in an empty environment; the two buttons have
not been pressed again since.

Not tested, because it needs the robot to move or its hardware to be free:

- START of a real run from the window, STOP and E-STOP during one
- PREFLIGHT, HOME and CLEAN UP from the window (the scripts themselves are the ones used on 7 Nov)
- manual mode on the real robot: the automatic pose move after MANUAL ON, driving, the lift and camera buttons, the head going back on
  MANUAL OFF, RELEASE RUNSTOP. (Tested 2026-11-14 only against a pretend driver on a private ROS master, and the order of the
  moves with stand-in processes)
- the window on abuela's screen, and anything on the team router
- `ENGINE=robot` during a real run (see the CPU numbers in `../loc/README.md`)

## When the localiser stops

At start the localiser waits (`ready, waiting for a run`). A run that was already over is not gone through again; its
positions stay in the database and on the map as last known places. `run_scan_demo.sh` runs the localiser in a loop: if it stops by itself it is started again after 5 s, picks the run up from
its beginning and catches up; the reason is in `logs/engine.log` and in the window's Localiser line. Closing the window (or
`--stop`) stops the loop, then the localiser, and waits until it is gone before anything new may start; a localiser left
over from an earlier session is stopped the same way at the next start. On 2026-11-13 this was missing: a new localiser
started while the old one was still finishing, the two did not fit on abuela's 8 GB GPU, the new one died, and nothing in
the window said so. Since then F3 and F4 also use 16 times less GPU memory (shelf cells only).
