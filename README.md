# toolchanger-lane-bridge

A small service for Klipper toolchangers (klipper-toolchanger / klipper-toolchanger-easy,
StealthChanger) that publishes the Spoolman spool loaded in each tool to Moonraker's
`lane_data` database namespace.

OrcaSlicer's Moonraker filament sync, and lane-based Orca plugins, read `lane_data` to show
the material and colour per slot. AFC and Happy Hare write it; klipper-toolchanger does not.
This service fills that gap.

```
gcode_macro T0.spool_id ─┐                      ┌─ lane_data/lane1  {"lane": "0", ...}
gcode_macro T1.spool_id ─┼─ lanebridge ─────────┼─ lane_data/lane2  {"lane": "1", ...}
                         │   (Moonraker ws)     │
Spoolman (via /server/spoolman/proxy) ──────────┘
```

* Talks only to Moonraker on localhost: no extra ports, no credentials, no internet access.
* Python standard library plus `websockets`, in its own venv.
* Writes a lane only when its contents change, and logs one line per change.

## Lane entry

Key `lane<n+1>` holds tool `T<n>`:

```json
{"color": "#f0be02", "material": "PLA", "bed_temp": 50, "nozzle_temp": 210,
 "scan_time": "", "td": "", "lane": "1", "spool_id": 6}
```

| field | from Spoolman |
|---|---|
| `color` | `filament.color_hex` with `#` added; for a multi-colour spool, the first of `multi_color_hexes` |
| `material` | `filament.material` |
| `nozzle_temp` / `bed_temp` | `filament.settings_extruder_temp` / `settings_bed_temp`, `null` if unset |
| `spool_id` | spool `id` |
| `lane` | tool number **as a string** (OrcaSlicer skips lanes where this is a number) |

A tool with no spool, a spool that doesn't exist in Spoolman, or an archived spool gets an empty
entry (`material: ""`, `color: ""`, temperatures `null`, `spool_id: null`). If Spoolman is
unreachable, the existing lanes stay as they are until it comes back.

`laneN` keys above the tool count are removed.

## Setting the spool per tool

The bridge reads `spool_id` from `[gcode_macro T<n>]`. This is the Mainsail/Fluidd convention,
so either of these works:

* **Fluidd / Mainsail:** use the per-tool spool selector in the Spoolman panel.
* **G-code:** `ASSIGN_TOOL_SPOOL TOOL=T0 ID=42` sets a spool; `ASSIGN_TOOL_SPOOL TOOL=T0` clears it.

Each tool macro needs `variable_spool_id: None`. To keep the values across restarts, use
`[save_variables]` and a startup `delayed_gcode` that restores `t<n>__spool_id`; see
`printer/spoolman.cfg` on the StealthChanger for an example. The lane updates within about a
second of the variable changing.

## Install

On the printer host, as the printer user (not root):

```bash
git clone <origin> ~/toolchanger-lane-bridge   # or copy the directory there
~/toolchanger-lane-bridge/install.sh
```

`install.sh` can be run more than once. It:

1. creates `~/lanebridge-env` and installs `requirements.txt` into it;
2. copies `lanebridge.conf.example` to `~/printer_data/config/lanebridge.conf` if that file doesn't exist yet;
3. adds `lanebridge` to `~/printer_data/moonraker.asvc` (Moonraker picks this up at its next restart);
4. installs, enables and (re)starts `lanebridge.service`. This is the only step that uses `sudo`.

Before changing anything under `~/printer_data/config`, it copies the affected files to
`backup-lanebridge-<timestamp>/`.

To have Moonraker's update manager track the repo, add this to `moonraker.conf`:

```ini
[update_manager lanebridge]
type: git_repo
path: ~/toolchanger-lane-bridge
origin: <origin>
primary_branch: main
virtualenv: ~/lanebridge-env
requirements: requirements.txt
managed_services: lanebridge
```

## Configure

`~/printer_data/config/lanebridge.conf` (editable from Fluidd/Mainsail). Every option is
optional; see [`lanebridge.conf.example`](lanebridge.conf.example). The most useful ones:

* `[tools] tools`: `auto` (read from `[toolchanger] tool_numbers`) or a list such as `0, 1, 2`.
* `[tools] object` / `variable`: where the spool ID lives (default `gcode_macro T{tool}` / `spool_id`).
* `[spoolman] refresh_interval`: how often spools are re-read, since Moonraker doesn't forward
  Spoolman edit events. Default 300 s.

Restart the service after editing: `sudo systemctl restart lanebridge` (or use the service
controls in Fluidd/Mainsail).

## Run by hand

```bash
cd ~/toolchanger-lane-bridge
~/lanebridge-env/bin/python -m lanebridge --once --dry-run   # print what it would write
~/lanebridge-env/bin/python -m lanebridge -v                 # run in the foreground
curl -s 'http://localhost:7125/server/database/item?namespace=lane_data'
```

Logs go to journald (`journalctl -u lanebridge -f`) and to `~/printer_data/logs/lanebridge.log`.

## Behaviour

* Connects to `ws://localhost:7125/websocket`, identifies itself, waits for Klipper to be
  ready, and subscribes to the tool macros.
* On start and on every reconnect, it reads `lane_data` and rewrites only the lanes that differ.
* When Klipper restarts, it subscribes again and resyncs. When Moonraker goes away, it
  reconnects with backoff (1 s up to 30 s).
* When Spoolman reconnects (`notify_spoolman_status_changed`), it re-reads every spool.

## Uninstall

```bash
~/toolchanger-lane-bridge/uninstall.sh
```

This stops and disables the service, deletes the `laneN` keys from `lane_data`, removes the
unit, the `moonraker.asvc` line and any `[update_manager lanebridge]` block (backing files up
first). The repo, the venv and `lanebridge.conf` are left in place; the script prints the
command to remove them.

## Development

```bash
python3 -m venv .venv && .venv/bin/pip install -r requirements.txt
.venv/bin/python -m unittest discover -s tests -t .
```

The tests use a fake Moonraker websocket server (`tests/fake_moonraker.py`), so no printer is
needed.
