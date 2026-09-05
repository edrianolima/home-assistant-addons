# 🕹️ RomM

[![Version](https://img.shields.io/badge/version-1.1.0-blue.svg)](config.yaml)
![aarch64](https://img.shields.io/badge/aarch64-yes-green.svg)
![amd64](https://img.shields.io/badge/amd64-yes-green.svg)
[![License](https://img.shields.io/badge/license-MIT-blue.svg)](../LICENSE)

Your retro game collection, organised and playable in the browser.
[RomM](https://github.com/rommapp/romm) scans your ROMs, finds the box art and
details for each one, and lets you play them without installing an emulator.

This add-on packages the official image, tuned so a Raspberry Pi can run it next
to Home Assistant.

## What it does

**Finds everything about your games.** Point it at a folder of ROMs and it comes
back with covers, screenshots, descriptions and manuals.

**Plays them in the browser.** Click a game and it runs, on a laptop, a tablet
or a phone. Nothing to install on the device.

**Works for the whole family.** Each person gets an account and their own saves.

> [!IMPORTANT]
> **The emulator runs in the browser, not on the Pi.** RomM sends the emulator
> (about 1.5 MB) and the ROM to whoever is playing, and the game runs on their
> machine. The Pi only hands over two files. Five people playing costs it five
> downloads, not five emulations.
>
> So the speed of a game depends on the device playing it, and a Raspberry Pi is
> perfectly capable of hosting this.

## Before you install

Two things to set up first. The first one is not optional.

### 1. MariaDB

**RomM does not work with SQLite.** Its own database migrations refuse to run on
anything but MariaDB, MySQL or PostgreSQL, so install the official **MariaDB**
add-on first and give it a database and a user:

```yaml
databases:
  - romm
logins:
  - username: romm
    password: <pick a strong one>
rights:
  - username: romm
    database: romm
```

Keep that password: you enter the same one in this add-on's configuration.
`db_host` stays `core-mariadb`.

### 2. Somewhere to put the ROMs

Create this structure, all under **one** disk or share:

```
/media/romm/
├── library/roms/<platform>/   ← put your ROMs here
├── resources/                 ← covers and screenshots land here
├── assets/                    ← saves and save states
└── config/                    ← optional config.yml
```

> [!WARNING]
> `base_path` must be a **single mount point**. RomM creates hardlinks between
> those folders, and a hardlink across two different disks fails.

Platform folders use RomM's own names, which are not always the obvious ones.
`genesis` (not `megadrive`) and `sms` (not `mastersystem`) are the two that catch
people out. A wrong folder name shows up as *Unidentified* after a scan.

### 3. At least one metadata provider

Optional, but without one a scan finds no artwork at all. All are free:

| Provider | Gives you |
|---|---|
| **ScreenScraper** | Box art, screenshots, manuals. The broadest coverage for retro. |
| **SteamGridDB** | High-quality covers |
| **RetroAchievements** | Achievement data |
| **Hasheous** | Identification by file hash, no account needed |

## Installing

1. Install and configure **MariaDB** as above
2. Install this add-on and open its **Configuration** tab
3. Fill in `db_password`, and any metadata providers you signed up for
4. **Start** it, then open the web interface on port **8080**
5. Create your admin account, then run the first scan

## ⚠️ Copying ROMs from a Mac? Read this first

Copying to a non-HFS volume from macOS leaves an **AppleDouble** file beside
every real one, named `._Game.zip`. They are 4 KB of metadata and they are
invisible in Finder.

RomM treats them as ROMs. It indexes them, requests metadata for each one, and
lists them in your library as `._Game`. On a library of 6462 games that meant
6480 extra entries: the scan took twice as long, two 24-hour runs died on
timeout, and the artwork downloaded for those phantom entries filled **11 GB** of
disk.

Delete them before scanning:

```bash
find /media/romm/library/roms -name "._*" -delete
find /media/romm/library/roms -name ".DS_Store" -delete
```

And stop them coming back by telling RomM to ignore them in
`/media/romm/config/config.yml`:

```yaml
exclude:
  roms:
    single_file:
      names:
        - "._*"
        - ".DS_Store"
```

## Performance on a Raspberry Pi

The defaults assume Home Assistant is the priority tenant on the machine.

| Option | Default | Why |
|---|---|---|
| `scan_workers` | 3 | A scan waits on metadata providers, not on disk: measured disk utilisation stays near 1% throughout. Workers run those lookups in parallel, so this sets the pace. Going from 1 to 3 took a Pi 4 from 1.65 to 3.55 roms/min, with load under 1.0. Three leaves a core for Home Assistant. |
| `web_server_concurrency` | 2 | Upstream suggests 2×CPU+1 (nine here). Nine gunicorn workers on a shared Pi is not a trade worth making. |
| `scan_timeout` | 86400 | Upstream's four hours is far too short for a first scan of a large library. A single provider outage drags the rate from 10/min to 2/min while lookups burn their timeouts, and the job dies with the work half done. |

**Run the first scan overnight.** Steady-state serving is a read burst per game
session and is invisible. The initial import is not.

**Watch free space.** If the library shares a disk with Home Assistant, a full
disk breaks Home Assistant for real: the recorder stops and backups fail.

## Notes

- **No Ingress.** RomM's nginx serves from the root and cannot be mounted under
  an Ingress sub-path, so the interface is on port 8080 directly.
- **Authentication is always on.** You can create accounts for family members
  from the admin interface.
- **`ROMM_AUTH_SECRET_KEY` is generated once** into `/data/.auth_secret`. Without
  a stable key every restart invalidates existing sessions.
- **The scan progress bar can end short of 100%.** Its denominator is fixed when
  the job starts, so files removed mid-scan still count toward it. The scan
  finishes correctly.

## Reaching RomM from Home Assistant

RomM publishes no entities, so there is no card to build from it. What is worth
adding is a way in.

### A sidebar entry

Home Assistant → **Settings → Dashboards → Add dashboard → Webpage**, pointing at
`http://<your-ha-ip>:8080`. RomM then sits in the sidebar next to everything
else.

### Or a button on an existing dashboard

```yaml
type: button
name: Game Library
icon: mdi:controller-classic
tap_action:
  action: url
  url_path: http://192.168.1.10:8080
```

An `iframe` card also works, but RomM's own interface expects the full window,
and the in-browser player is unusable in a small frame.

## Family accounts

Everyone who plays needs an account, created from RomM's admin interface. A few
things worth knowing before you set them up:

- **An email address is required** for each account.
- Saves and save states are **per user**, kept under `assets/`, so nobody
  overwrites anyone else's progress.
- The library itself is shared. There is no per-user filtering of what is
  visible.

## Backups

Two things have to be backed up together, and neither is enough alone:

| What | Where | Why |
|---|---|---|
| The MariaDB database | the MariaDB add-on | Every scan result, all metadata, users and saves metadata |
| `resources/` and `assets/` | `base_path` | Downloaded artwork and the actual save files |

Losing the database means rescanning the whole library from scratch, which on a
large collection is hours of provider lookups, not minutes of disk work.

## Troubleshooting

**The add-on will not start, and the log mentions the database**
Check that MariaDB is running, that the `romm` database and user exist, and that
`db_password` here matches the one in MariaDB's `logins`.

**Everything scanned as *Unidentified***
The platform folder name is not one RomM recognises. See
[Somewhere to put the ROMs](#2-somewhere-to-put-the-roms).

**The scan found no artwork**
No metadata provider is configured, or the credentials are wrong. The add-on log
names the provider that failed.

**The scan stopped part-way through**
Look for `JobTimeoutException` in the log. `scan_timeout` defaults to 24 hours
here, which is enough for a large library; if you hit it, something else is
slowing the providers down.

**A game appears twice, once with a `._` in front**
See [Copying ROMs from a Mac](#️-copying-roms-from-a-mac-read-this-first).

## Upgrading

The RomM version is pinned in `build.yaml`. Read upstream's release notes before
moving it, especially across a major, then rebuild the add-on.
