# NFC spool reader (ESPHome)

An ESP32 with one PN532 NFC reader per spool holder. When you put a tagged spool on a
holder, the reader assigns that spool to the matching tool:

```
tag on spool ─► PN532 ─► ESP32 ─► Spoolman  POST /api/v1/tag/scan      → spool id
                                └► Moonraker POST /printer/gcode/script → ASSIGN_TOOL_SPOOL TOOL=T0 ID=<id>
                                                    │
                     lanebridge → lane_data → OrcaSlicer / Spooler
```

No Home Assistant is needed. The ESP32 talks to Spoolman and Moonraker directly.

## Hardware

* 1 × ESP32 DevKit (the `esp32dev` board)
* 2 × PN532 modules, switched to **SPI** mode (on the common red boards: switch 1 off, switch 2 on)
* NTAG213/215 stickers on the spools. Bambu and Creality factory tags work too, because Spoolman
  matches a tag by its hardware ID.
* A 5 V supply. Check whether your PN532 boards take 3.3 V or 5 V.

| PN532 | ESP32 |
|---|---|
| SCK | GPIO18 (shared) |
| MISO | GPIO19 (shared) |
| MOSI | GPIO23 (shared) |
| SS | GPIO22 for the T0 reader, GPIO21 for the T1 reader |
| VCC / GND | 3V3 or 5V / GND |

Keep the SPI wires short: about 30–50 cm at most per reader.

## Flash

1. Copy `secrets.yaml.example` to `secrets.yaml` and fill in your Wi-Fi details.
2. If your printer isn't at `10.39.5.187`, change `spoolman_url` and `moonraker_url` at the top
   of `spool-reader.yaml`.
3. Connect the ESP32 by USB and run `esphome run spool-reader.yaml`, or use the ESPHome dashboard
   or the Home Assistant ESPHome add-on.

After the first flash, updates go over Wi-Fi.

## Link tags to spools (once per spool)

1. Under Settings › Tag Scanning in Spoolman, pair your browser with the reader
   `spool-reader-T0`. It appears after its first scan.
2. Tap the new tag on that reader. Spoolman shows "Unknown tag …".
3. Open the spool in Spoolman and use **Link a tag**. Spoolman offers the UID the paired reader
   read last.

If you'd rather not pair: read the UID from "Last tag T0" on `http://spool-reader.local` and
enter it in **Link a tag** by hand. Separators and capitals don't matter.

## What happens

| Event | Result |
|---|---|
| Linked tag placed on the T0 holder | `ASSIGN_TOOL_SPOOL TOOL=T0 ID=<spool>`. The lane updates within about 2 s. |
| Unknown tag | Logged as "unknown tag"; nothing changes. |
| Spoolman or Moonraker unreachable | Logged; nothing changes. |
| Spool taken off the holder | Nothing changes. The tool keeps its spool until another tag is read or you clear it with `ASSIGN_TOOL_SPOOL TOOL=T0`. |

Taking a spool off doesn't clear the tool, on purpose: a tag can briefly drop out of range from
vibration, and clearing a tool mid-print would charge its filament use to no spool.

Moonraker must trust the ESP32's IP address (`[authorization] trusted_clients`). If you narrow
that list, keep the ESP32's address in it.

## More tools

Add another `pn532_spi` entry with its own `cs_pin`, a `last_tag_tN` text sensor, and
`tool: "TN"`.
