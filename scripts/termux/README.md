# Termux phone-recording server (easy-GTFS-RT, TX-1..TX-8)

Turns Michal's Android phone into an always-on GTFS-RT VehiclePositions recorder, feeding the
same `family_a` pipeline used by the GitHub-Actions-only track (FA-*) and the Oracle Cloud VM
track (OR-*). This is the Termux ("TX-") track: phone-side recording + GitHub-side
build/publish/monitoring. Since TX-8, one phone can record **multiple cities in parallel**, each
its own supervised process. Full design rationale lives in
`docs/prd/PR_easy-OTP_termux-migration.md` and the milestone-by-milestone prompts in
`docs/prompts/termux-migration_prompts_for-claude-code.md` (both local-only, see note at the
bottom).

## Multi-city config (TX-8)

Every city being recorded needs two matching entries, kept in sync by hand - **the city id string
must be identical (case-sensitive) in both places**:

1. **On the phone**: `~/easy-gtfs-rt-termux/cities/<city_id>.env`, holding that city's
   `VEHICLE_POSITIONS_URL` and, since 2026-07-16, an optional `TIMEZONE` (an IANA zone name, e.g.
   `Europe/Vilnius`) - see "Recording window timezone" below. `GH_TOKEN` stays shared, in the
   top-level `~/.easy-gtfs-rt-termux.env` (one token covers all cities in the repo).
2. **In `easy-GTFS-RT`**: a `<city_id>` key in `config/cities.json` (`display_name`,
   `static_gtfs_url`, and, since 2026-07-16, an optional `timezone` - same IANA zone name and
   `Europe/Warsaw` fallback as the phone-side `TIMEZONE` above, duplicated here because GitHub
   Actions has no access to the phone's `.env` files) - this is what the build workflow and
   healthcheck use to know which cities to build/check, and, via `timezone`, when each city's own
   local day is actually over.

A city id that exists in one place but not the other fails loudly (an unmapped city in a
`repository_dispatch` payload makes the build workflow's `resolve_targets` job error out
immediately) rather than silently doing nothing - see "Adding a new city" below for the full,
tested-safe order of operations.

### Recording window timezone

`record_supervised.sh`'s 06:00-22:00 recording window (and `record_custom.sh`'s/
`sweep_and_upload.sh`'s date stamping) is evaluated in a city's own `TIMEZONE` (an IANA zone name,
e.g. `Europe/Vilnius`), set in that city's `cities/<city_id>.env`. If a city's `.env` doesn't set
`TIMEZONE`, everything for that city falls back to `Europe/Warsaw` - the original, single-timezone
behavior from before TX-8's multi-city expansion.

**Set this for every city outside CET/CEST** (Prague, Rome, Turin, and Szczecin share Poland's
zone, so `TIMEZONE` is optional for them - only cosmetic if set). For the EU comparison cities:

| city_id | TIMEZONE |
|---|---|
| `szczecin` | `Europe/Warsaw` (optional - already the default) |
| `prague` | `Europe/Prague` (optional - same offset as Warsaw) |
| `rome` | `Europe/Rome` (optional - same offset as Warsaw) |
| `turin` | `Europe/Rome` (optional - same offset as Warsaw) |
| `vilnius` | `Europe/Vilnius` |
| `sofia` | `Europe/Sofia` |
| `bucharest` | `Europe/Bucharest` |
| `lisbon` | `Europe/Lisbon` |
| `boston` | `America/New_York` |
| `brisbane` | `Australia/Brisbane` |

Without this, every city's recording window was silently evaluated in Europe/Warsaw wall-clock
time regardless of the city's real timezone - for cities one hour ahead of Poland (Vilnius, Sofia,
Bucharest), the actual local hours captured were 07:00-23:00, not 06:00-22:00; for Lisbon (one
hour behind), 05:00-21:00 - missing the last hour of evening service. Fixed 2026-07-16; add
`TIMEZONE` to a city's `.env` and restart its `family-a-record-<city_id>` service to pick it up.

### VEHICLE_POSITIONS_URL reference for the EU comparison cities (spike-verified 2026-07-15)

`config/cities.json` in `easy-GTFS-RT` already has `display_name`/`static_gtfs_url` for these
cities. Their `VEHICLE_POSITIONS_URL` values below are **not** in that file (it's phone-only
config, per the "Multi-city config" note above) - this table exists purely so creating each
city's `cities/<city_id>.env` on the phone (step 2 of "Adding a new city" below) is a copy-paste,
not a re-run of the discovery spike. All verified `auth=none` (no API key needed).

| city_id | VEHICLE_POSITIONS_URL |
|---|---|
| `szczecin` | `https://www.zditm.szczecin.pl/storage/gtfs/gtfs-rt-vehicles.pb` |
| `prague` | `https://api.golemio.cz/v2/vehiclepositions/gtfsrt/vehicle_positions.pb` |
| `rome` | `https://romamobilita.it/sites/default/files/rome_rtgtfs_vehicle_positions_feed.pb` |
| `turin` | `https://percorsieorari.gtt.to.it/das_gtfsrt/vehicle_position.aspx` |
| `vilnius` | `https://www.stops.lt/vilnius/vehicle_positions.pb` |
| `sofia` | `https://gtfs.sofiatraffic.bg/api/v1/vehicle-positions` |
| `bucharest` | `https://gtfs.tpbi.ro/api/gtfs-rt/vehiclePositions` |
| `lisbon` | `https://gateway.carris.pt/gateway/gtfs/api/v2.11/GTFS/realtime/vehiclepositions` |
| `boston` | `https://cdn.mbta.com/realtime/VehiclePositions.pb` |
| `brisbane` | `https://gtfsrt.api.translink.com.au/api/realtime/SEQ/VehiclePositions` |

Notes from the spike:
- **Prague (Golemio)** has historically required a free `X-Access-Token` for some endpoints; this
  one returned HTTP 200 with no key in the 2026-07-15 spike test - re-check if it starts failing.
- **Rome**'s static GTFS is cron-updated on a Drupal site and can go stale; check `Last-Modified`
  before relying on a given day's static feed for matching.
- **Lisbon** is the Carris city-core feed specifically, distinct from Carris Metropolitana (the
  wider metro-area operator) - don't confuse the two if searching for alternates later.
- **Boston (MBTA)** and **Brisbane (TransLink SEQ)** were verified 2026-07-16, both `auth=none`.
  Boston is `America/New_York` (UTC-4 in July), 6h behind Warsaw's summer offset - its recording
  window doesn't close until Warsaw-local ~04:00 the *next* day. Adding it is what exposed a real
  gap: the build workflow's fixed 22:15 Warsaw schedule fallback and the healthcheck's fixed 21:00
  Warsaw check both used to run *before* Boston's window closes, so the healthcheck would have
  sent a false "missing" WhatsApp alert every single night. Fixed 2026-07-16 alongside adding
  these two cities - see "Known gotchas" (`sweep_and_upload.sh`'s dispatch retry marker, and the
  healthcheck's per-city polling) for the redesign; both now correctly wait for each city's own
  local window, using the `timezone` field this file's "Multi-city config" section describes.

## Pipeline overview

Times below (06:00/22:00) are each city's own local clock, per its `TIMEZONE`/`timezone` (see
"Recording window timezone" above) - not simultaneous across cities unless they share a timezone
(e.g. Prague/Rome/Turin/Szczecin all recording in step with Poland). Nothing on the GitHub side
runs on a fixed daily clock anymore either (fixed 2026-07-16, prompted by adding Boston - see the
note under "VEHICLE_POSITIONS_URL reference" above): `sweep_and_upload.sh`, the healthcheck, and
the phone's recording services are each independently self-gated on a given city's own local
time, not a shared trigger - so every "~HH:MM" below means "the first poll tick after this
condition becomes true for this city", not a single simultaneous global event.

```
Phone (Termux)                                   GitHub (GISBoost/easy-GTFS-RT)
---------------                                   -------------------------------
06:00  each family-a-record-<city> service
       wakes up, starts recording that city
       (self-healing: runit restarts it if
       Android kills the process)
22:00  recording window ends (this city's own
       local clock)
~22:10 sweep_and_upload.sh (cron, every 15 min,
       all day) reaches the first tick after
       this city's local window has closed:
       zips its positions_<city>_<date>_* dirs,
       uploads to a "positions-raw-<city>-<date>"
       pre-release, then fires a
       repository_dispatch event carrying
       that city's id           ----->
                                                   (seconds later, per city) family_a_build_and
                                                          _notify_from_phone.yml downloads that
                                                          city's raw data, builds corrected
                                                          GTFS, publishes
                                                          "<city>-realized-<date>-phone" release
                                                   (nothing announces success - see "Notifications"
                                                          below; a failed run does send GitHub's
                                                          own email, a day that silently produces
                                                          nothing sends nothing at all)
       (if the dispatch call above itself
       failed - e.g. a network hiccup - the
       next sweep tick retries just that call,
       every 15 min, until it succeeds; no
       GitHub-side fallback needed for this
       anymore)
reboot (any time) start-services.sh (Termux:Boot)
       brings every city's recording service
       back up automatically, no manual
       app-open needed

record_custom.sh <city> ... (run manually, any
       time) - ad-hoc recording of arbitrary
       duration/interval for one city,
       independent of the 06:00-22:00 window
```

Milestone numbering (`TX-1`..`TX-8`) and full acceptance criteria: PRD section 5. TX-4, the
healthcheck, and TX-7/TX-8's workflow half live in the `easy-GTFS-RT` repo
(`.github/workflows/`, `config/cities.json`), not here.

## File map

| File | Milestone | Runs | Purpose |
|---|---|---|---|
| `termux_provision.sh` | TX-1 | once, manually | Installs packages, builds numpy/pandas from source, clones `easy-OTP`, sets up the venv |
| `record_supervised.sh <city>` | TX-2 (+TX-8) | continuously, one instance per city, supervised by `termux-services` | Self-healing loop: sleeps until 06:00, records the *remaining* window for that city, restarts if killed |
| `service/family-a-record-lodz/run` | TX-2 (+TX-8) | invoked by `runit` | The `termux-services` entry point that execs `record_supervised.sh lodz` - one such service dir per city |
| `boot/start-services.sh` | TX-2 (boot-survival addendum) | invoked by Termux:Boot after every reboot | Starts `runsvdir` so every city's recording resumes without opening the Termux app manually |
| `sweep_and_upload.sh` | TX-3 (+TX-7, +TX-8) | every 15 min, all day, via `cronie` | Per configured city: uploads unsent recordings as a raw GitHub pre-release once that city's own local window has closed, then fires (and retries, if needed) a `repository_dispatch` event (carrying that city's id) to start its build immediately |
| `record_custom.sh <city>` | TX-5 (+TX-8) | manually, on demand | `record_custom.sh <city_id> <duration_min> <interval_sec> <suffix>` - one-off recording outside the normal window |

## One-time phone setup (how this was built)

1. Install from **F-Droid** (not Google Play - stale there): Termux, Termux:API, Termux:Boot.
2. Android Settings -> Apps -> Termux -> Battery -> **Unrestricted**. Do the same for the
   **Termux:Boot** app separately (it's a different entry). Also check brand-specific autostart
   toggles (Xiaomi "Autostart", Huawei "Protected apps", Samsung, etc.) for both apps.
3. **Open the Termux:Boot app icon manually at least once** after installing it. Android 12+
   withholds the boot-completed broadcast from apps that have never been launched by the user -
   without this one tap, `boot/start-services.sh` below will never fire.
4. Keep the phone on charger for the whole 06:00-22:00 window.
5. Copy `termux_provision.sh` to the phone (e.g. via `~/storage/shared/documents/`) and run it:
   ```
   bash termux_provision.sh
   ```
   This is the slow step - numpy/pandas have no prebuilt wheels for Android's Bionic libc and
   must compile from source. It also creates the empty `~/easy-gtfs-rt-termux/cities/` directory.
6. Verify: `source ~/easy-gtfs-rt-termux/venv/bin/activate && cd ~/easy-OTP/tools/family_a_reconstruction && python -m family_a.cli --help` must run without an import error.
7. Create a fine-grained GitHub PAT (Contents: Read and write, scoped only to
   `GISBoost/easy-GTFS-RT`), then create the secrets file **by hand** (never via the agent):
   ```
   nano ~/.easy-gtfs-rt-termux.env
   chmod 600 ~/.easy-gtfs-rt-termux.env
   ```
   ```
   export GH_TOKEN="github_pat_..."
   ```
8. For each city to record, create its own config file (per-city, not shared):
   ```
   nano ~/easy-gtfs-rt-termux/cities/lodz.env
   chmod 600 ~/easy-gtfs-rt-termux/cities/lodz.env
   ```
   ```
   export VEHICLE_POSITIONS_URL="https://otwarte.miasto.lodz.pl/wp-content/uploads/2025/06/vehicle_positions.bin"
   ```
   This must also exist as a `lodz` key in `easy-GTFS-RT`'s `config/cities.json` (already shipped)
   - see "Adding a new city" below for adding any city beyond the first.
9. Set up the `termux-services` entry, one per city: copy `service/family-a-record-lodz/run` to
   `$PREFIX/var/service/family-a-record-lodz/run` on the phone, `chmod +x`, then
   `sv-enable family-a-record-lodz`. Confirm: `sv status family-a-record-lodz` shows `run:`.
10. Copy `boot/start-services.sh` to `~/.termux/boot/start-services.sh`, `chmod +x`. Test with an
    actual phone reboot (not Force Stop - that doesn't trigger Termux:Boot at all), then check
    `cat ~/easy-gtfs-rt-termux/logs/boot.log` without opening Termux first. This step covers every
    city's service automatically (it just starts `runsvdir`, which supervises whatever's
    registered) - no per-city boot-script change needed.
11. Enable `cronie`'s daemon and add the TX-3 sweep schedule - **do not skip this even though
    `termux_provision.sh` installs the `cronie` package**; installing the package does not enable
    the service or create the crontab entry:
    ```
    sv-enable crond
    (crontab -l 2>/dev/null; echo "*/15 * * * * /data/data/com.termux/files/usr/bin/bash /data/data/com.termux/files/home/easy-gtfs-rt-termux/sweep_and_upload.sh >> /data/data/com.termux/files/home/easy-gtfs-rt-termux/logs/sweep.log 2>&1") | crontab -
    ```
    Confirm: `sv status crond` shows `run:` and `crontab -l` shows the line above. Once enabled,
    `crond` is picked up automatically by `boot/start-services.sh` on future reboots too, same as
    the recording services - no separate boot-script change needed for it. This one crontab entry
    covers every city - `sweep_and_upload.sh` loops all of them itself, every 15 minutes, all day
    (not once at a fixed time) - see "Recording window timezone" above and the script's own header
    comment for why: with cities in different timezones, no single daily trigger time is safe for
    all of them, so the script itself decides per city, per tick, whether it's actually time.

Nothing needs configuring on the GitHub side: as of 2026-07-29 `GISBoost/easy-GTFS-RT` requires
no repository secrets at all (the `CALLMEBOT_*` pair went away with the notification steps).
Per-city static GTFS URLs live in that repo's `config/cities.json` (versioned, not a Settings
variable). `GH_TOKEN` here is the phone's own token and is still required - it is what authorises
the raw-release upload and the `repository_dispatch` above.

### Notifications

There are none any more, in either direction:

- **Success** was announced over WhatsApp (CallMeBot) until 2026-07-29. Removed - the free API's
  quota ran out, and the message only restated what the published Release already said.
- **Failure** of a build is covered by GitHub's own email for a failed Actions run, which is why
  the WhatsApp failure message was dropped as duplication rather than replaced.
- **A phone that silently stops recording is still not detected.** The healthcheck workflow that
  used to alert on this (`family_a_phone_healthcheck.yml`) was deleted 2026-07-17 for
  false positives, and nothing replaced it. No upload means no `repository_dispatch`, which means
  no workflow run, which means no failure email either - the only symptom is a missing Release,
  and you have to go looking for it.

## Adding a new city

No code changes needed - this is a config + phone-setup only. **Do this in order**, and validate
step 1 before touching the phone (a `repository_dispatch` naming a city not yet in
`config/cities.json` fails loudly, by design):

1. In `easy-GTFS-RT`: add a `<city_id>` key to `config/cities.json` (`display_name`,
   `static_gtfs_url`, and, if the city isn't in Poland's timezone, `timezone` too - see "Multi-city
   config" above), PR + merge to `main`. Required first - triggers only evaluate the workflow file
   (and the `config/cities.json` it reads) from the default branch.
2. On the phone: create `~/easy-gtfs-rt-termux/cities/<city_id>.env` with that city's
   `VEHICLE_POSITIONS_URL` (step 8 above) and, if the city isn't in Poland's timezone, its
   `TIMEZONE` too (see "Recording window timezone" above), `chmod 600`.
3. Copy `service/family-a-record-lodz/` to `service/family-a-record-<city_id>/` (in this repo, or
   directly on the phone under `$PREFIX/var/service/`), edit the one `exec ... record_supervised.sh
   <city_id>` line to the new city id, `chmod +x run`, `sv-enable family-a-record-<city_id>`.
   Confirm `sv status family-a-record-<city_id>` shows `run:`.
4. Nothing else - `sweep_and_upload.sh` picks up the new `cities/<city_id>.env` automatically on
   its next run, and the build workflow already handles any city in `config/cities.json`.

## Day-to-day commands (via SSH)

Connect: `ssh -p 8022 u0_a98@<phone-ip>` (port 8022 is Termux's sshd default; `u0_a98` is the
Android user id, stable across sessions but may change after a Termux reinstall). `sshd` itself
only starts once you open the Termux app - it is not (yet) wrapped as a boot-persistent service.

```
sv status family-a-record-lodz                                      # run: = recording up, down: = not
ls ~/easy-gtfs-rt-termux/positions_lodz_$(date +%F)_*/ | wc -l       # snapshot count, should grow ~1/min
tail -f ~/easy-gtfs-rt-termux/logs/record_lodz_$(date +%F).log       # live log (Ctrl+C to detach, doesn't kill it)
grep -c "failed" ~/easy-gtfs-rt-termux/logs/record_lodz_$(date +%F).log   # failed polls today
cat ~/easy-gtfs-rt-termux/logs/boot.log                              # did Termux:Boot fire on last reboot?
```
(swap `lodz` for any other configured city id.)

Ad-hoc recording outside the 06:00-22:00 window:
```
bash record_custom.sh lodz 60 60 test    # city "lodz", 60 min, 60s interval, output dir suffix "test"
```

After copying an updated script from the repo to the phone (e.g. via `~/storage/shared/documents/`):
```
sv down family-a-record-lodz
cp ~/storage/shared/documents/record_supervised.sh ~/easy-gtfs-rt-termux/record_supervised.sh
chmod +x ~/easy-gtfs-rt-termux/record_supervised.sh
sv up family-a-record-lodz
```
(`record_supervised.sh` is shared code across all cities' services - updating it once affects
every `family-a-record-<city>` service, since each just calls it with a different argument. `sv
down`/`up` only needed for files a `family-a-record-<city>` service actually runs;
`record_custom.sh`/`sweep_and_upload.sh` just need re-copying + `chmod +x`.)

## Known gotchas

- **`record_supervised.sh`'s `date +%H`/`+%M`** must go through `10#` (e.g. `10#$(date +%H)`) -
  otherwise bash parses zero-padded values like `08`/`09` as invalid octal and the script aborts.
  Already fixed in the current version; don't reintroduce a plain `$(( $(date +%H) * 60 ))`.
- **A city's recording window and its `family_a.cli record` subprocess must use the same `TZ`.**
  `record_supervised.sh`/`record_custom.sh` derive `ZONE` once from `cities/<city_id>.env`'s
  `TIMEZONE` (default `Europe/Warsaw`) and pass it both to their own `date` calls AND as a
  `TZ="$ZONE"` prefix on the `python -m family_a.cli record` invocation itself - the Python
  process's internal `datetime.now()` calls (recording.json, snapshot filenames) otherwise follow
  the phone's actual system clock (Europe/Warsaw), not the shell's temporary `TZ` override, which
  would silently reintroduce a Warsaw/city clock mismatch even after the window itself is fixed.
  See "Recording window timezone" above.
- **Termux:Boot's execution context does not inherit Termux env vars** (`$PREFIX` in particular).
  `boot/start-services.sh` sets them explicitly - don't remove those exports.
- **`sv-enable` cannot do anything at a cold boot** - it needs `runit`'s `runsvdir` already
  supervising the service directory, which nothing has started yet immediately after a reboot.
  `boot/start-services.sh` starts `runsvdir` directly instead of calling `sv-enable`.
- **Force Stop does not simulate a reboot.** It doesn't trigger Termux:Boot's broadcast receiver -
  only an actual device restart does. Don't use it to test boot behavior.
- **Installing the `cronie` package does not enable or schedule anything.** Discovered on-device
  2026-07-13: `crond` sat `down` and `crontab -l` was empty despite `termux_provision.sh` having
  installed `cronie` - the sweep had never actually run automatically, ever, only whenever someone
  ran `sweep_and_upload.sh` by hand. `sv-enable crond` + a real crontab entry (setup step 11 above)
  are both required separately.
- **`sweep_and_upload.sh` runs every 15 minutes, all day, on purpose** (fixed 2026-07-16) - it used
  to be a single daily trigger at a fixed Europe/Warsaw time, which was only safe because every
  city shared Poland's offset. Once `record_supervised.sh`'s window became per-city, a single
  fixed trigger could no longer safely cover every city (a city behind Warsaw could still be
  mid-recording when it fired, truncating that day's session - the `.uploaded` marker never lets a
  swept directory be reconsidered). The script now gates itself instead: Gate 1 skips a city until
  its own local window has closed (+ a safety margin), Gate 2 skips a city with nothing new to
  upload (avoids hitting the GitHub API on every 15-minute tick once a city is done for the day).
  Both gates fail silently - don't expect a log line for a normal skip, only for real events
  (upload, dispatch, error). Bonus: unlike the GitHub Actions crons in this pipeline, this script
  needs no manual DST-flip twice a year - `TZ="$ZONE" date` already knows each city's own rules.
- **All cities' `record_supervised.sh` processes share one `~/easy-OTP` checkout** (TX-8) - each
  does its own `git pull --ff-only` at startup, so several services restarting at once (e.g. every
  service coming up together at boot) can race for git's lock. The existing warning-and-continue
  fallback (falls back to whatever's already checked out) already covers this; not otherwise
  handled specially, and not expected to matter in practice since a stale-by-a-few-seconds
  checkout is harmless here.
- **The city id string is an exact, case-sensitive contract** (TX-8) across three places: the
  phone's `cities/<city_id>.env` filename, its `family-a-record-<city_id>` service dir suffix, and
  the `config/cities.json` key in `easy-GTFS-RT`. A mismatch means that city's
  `repository_dispatch` payload names a key the build workflow can't find - it fails loudly in the
  `resolve_targets` job (by design), it does not silently skip the city.
- **The build workflow's `schedule:` fallback was removed 2026-07-16** (not just DST-flipped -
  deleted). It used to be a fixed 22:15 Europe/Warsaw cron covering a dispatch call that fails
  after its upload already succeeded. Two problems: a single fixed Warsaw time can't safely cover
  a city far from Warsaw's offset (Boston's window doesn't close until Warsaw-local ~04:00 the
  *next* day - the fallback could only ever catch it a full day late), and this project already
  hit this exact failure mode once before - `family_a_build_and_notify.yml` (FA-8, commit
  `e7dba56`, 2026-07-13) dropped an almost identical fallback after GitHub's schedule queue
  delayed a run past local midnight, its date resolved to the wrong day, and it sent a spurious
  failure notification for a day that had already built successfully minutes earlier. Replaced by
  fixing the actual gap at the source instead: see the next bullet. Manual recovery is still
  `workflow_dispatch` (with its `date`/`city` inputs), same as before.
- **`sweep_and_upload.sh` retries a failed `repository_dispatch` call** (added 2026-07-16,
  alongside removing the fallback above). Upload and dispatch are two separate `curl` calls, so a
  transient failure of just the second one used to mean that city's build never started that day,
  with no future sweep tick ever retrying (once every directory was `.uploaded`, Gate 2 skipped
  the city entirely, dispatch included). A per-city-per-date
  `.dispatched_<city>_<date>` marker (independent of the per-directory `.uploaded` markers) now
  keeps a city eligible for a dispatch-only retry, every 15 minutes, until it actually succeeds -
  same reliability class the upload retries already had.
- **The healthcheck now polls every 15 minutes, per city, instead of once daily at a fixed Warsaw
  time** (fixed 2026-07-16, same motivation and shape as `sweep_and_upload.sh`'s own gates below).
  `config/cities.json` gained a `timezone` field (mirrors the phone's `TIMEZONE`, same
  `Europe/Warsaw` fallback) so the healthcheck can gate each city on its own local window+margin
  before judging it missing. A still-missing city doesn't re-alert every tick all night: the first
  detection creates a throwaway `healthcheck-alert-<city>-<date>` marker release (same
  try-then-treat-as-already-done idiom used elsewhere in this codebase), and later ticks for an
  already-alerted city stay silent.
- **The `repository_dispatch` `event_type` string (`"phone-sweep-complete"`) is an exact,
  case-sensitive contract** between `sweep_and_upload.sh` and the workflow's
  `on.repository_dispatch.types` list - a typo on either side means the event is silently dropped
  by GitHub with no error anywhere, not even in the Actions log. Since the schedule fallback was
  removed, there's no other automatic path that would catch a mismatch here - a typo would show up
  as `sweep_and_upload.sh` logging "Dispatched build..." forever while no build ever runs (and the
  healthcheck eventually alerting, since the raw release itself did upload but nothing consumed
  it). Keep both in sync if either ever changes.
- **`family_a/cli.py` always imports `numpy`/`pandas`**, even for the plain `record` subcommand -
  the phone needs the full `requirements.txt`, not just recorder-only dependencies. This is a
  deliberate decision (not touching shared `cli.py` for one track) - see PRD section 3.
- **`GH_TOKEN` (the fine-grained PAT in `~/.easy-gtfs-rt-termux.env`) expires** - fine-grained
  PATs have a mandatory expiry (commonly set to 90 days at creation). Only `sweep_and_upload.sh`
  uses it - since TX-7, that includes both the upload calls **and** the `repository_dispatch`
  call, for every city, so an expired token silently disables the fast-build path for all of them
  at once, not just uploads. Failures are logged as a `WARNING:` line (not a crash - the script
  has no `set -e`), so there's no direct alert - and since 2026-07-29 there is no indirect one
  either. The healthcheck workflow that used to catch this by messaging "no raw recording found"
  was deleted 2026-07-17, and the WhatsApp channel it used is gone too. **Nothing will tell you.**
  An expired token produces no upload, therefore no `repository_dispatch`, therefore no workflow
  run that could fail and email you - just a quiet absence of new Releases. Check the token's
  expiry date against your calendar rather than waiting for a symptom. Renew at
  [github.com/settings/personal-access-tokens](https://github.com/settings/personal-access-tokens),
  same scope (`Contents: Read and write` on `GISBoost/easy-GTFS-RT` only), then update the
  `GH_TOKEN` line in `~/.easy-gtfs-rt-termux.env` on the phone.

## Note on documentation location

The detailed PRD (`docs/prd/PR_easy-OTP_termux-migration.md`), the step-by-step prompts
(`docs/prompts/termux-migration_prompts_for-claude-code.md`), and the SSH cheatsheet
(`docs/handoffs/termux-ssh_cheatsheet-for-michal.md`) all currently exist only on disk, **not in
git** - `.gitignore` has a blanket `docs/` rule that was never narrowed, so nothing under `docs/`
is tracked despite `CLAUDE.md` saying it should be. This README lives under `scripts/termux/`
specifically so it survives in version control even if that's never fixed.
