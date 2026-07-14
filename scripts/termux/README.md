# Termux phone-recording server (easy-GTFS-RT, TX-1..TX-7)

Turns Michal's Android phone into an always-on GTFS-RT VehiclePositions recorder for the Lodz
feed, feeding the same `family_a` pipeline used by the GitHub-Actions-only track (FA-*) and the
Oracle Cloud VM track (OR-*). This is the Termux ("TX-") track: phone-side recording +
GitHub-side build/publish/monitoring. Full design rationale lives in
`docs/prd/PR_easy-OTP_termux-migration.md` and the milestone-by-milestone prompts in
`docs/prompts/termux-migration_prompts_for-claude-code.md` (both local-only, see note at the
bottom).

## Pipeline overview

```
Phone (Termux)                                   GitHub (GISBoost/easy-GTFS-RT)
---------------                                   -------------------------------
06:00  record_supervised.sh wakes up, starts
       recording (self-healing: runit restarts
       it if Android kills the process)
                                                   21:00  TX-6 healthcheck: alerts via
                                                          WhatsApp if today's raw release
                                                          is still missing
22:00  recording window ends
22:10  sweep_and_upload.sh (cron) zips today's
       positions_<date>_* dirs, uploads to a
       "positions-raw-<date>" pre-release, then
       fires a repository_dispatch event  ----->
                                                   (seconds later) family_a_build_and_notify_from
                                                          _phone.yml downloads raw data, builds
                                                          corrected GTFS, publishes
                                                          "lodz-realized-<date>-phone" release,
                                                          WhatsApp notify (TX-7)
                                                   22:15  schedule fallback (only does real work
                                                          if the dispatch above never fired or
                                                          failed - otherwise a fast no-op)
reboot (any time) start-services.sh (Termux:Boot)
       brings the recording service back up
       automatically, no manual app-open needed

record_custom.sh (run manually, any time) - ad-hoc
       recording of arbitrary duration/interval,
       independent of the 06:00-22:00 window
```

Milestone numbering (`TX-1`..`TX-7`) and full acceptance criteria: PRD section 5. TX-4, TX-6, and
TX-7's workflow half live in the `easy-GTFS-RT` repo (`.github/workflows/`), not here.

## File map

| File | Milestone | Runs | Purpose |
|---|---|---|---|
| `termux_provision.sh` | TX-1 | once, manually | Installs packages, builds numpy/pandas from source, clones `easy-OTP`, sets up the venv |
| `record_supervised.sh` | TX-2 | continuously, supervised by `termux-services` | Self-healing loop: sleeps until 06:00, records the *remaining* window, restarts if killed |
| `service/family-a-record/run` | TX-2 | invoked by `runit` | The `termux-services` entry point that execs `record_supervised.sh` |
| `boot/start-services.sh` | TX-2 (boot-survival addendum) | invoked by Termux:Boot after every reboot | Starts `runsvdir` so recording resumes without opening the Termux app manually |
| `sweep_and_upload.sh` | TX-3 (+TX-7) | daily at 22:10, via `cronie` | Uploads unsent recordings as a raw GitHub pre-release, then fires a `repository_dispatch` event to start the build immediately (TX-7) |
| `record_custom.sh` | TX-5 | manually, on demand | `record_custom.sh <duration_min> <interval_sec> <suffix>` - one-off recording outside the normal window |

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
   must compile from source.
6. Verify: `source ~/easy-gtfs-rt-termux/venv/bin/activate && cd ~/easy-OTP/tools/family_a_reconstruction && python -m family_a.cli --help` must run without an import error.
7. Create a fine-grained GitHub PAT (Contents: Read and write, scoped only to
   `GISBoost/easy-GTFS-RT`), then create the secrets file **by hand** (never via the agent):
   ```
   nano ~/.easy-gtfs-rt-termux.env
   chmod 600 ~/.easy-gtfs-rt-termux.env
   ```
   ```
   export GH_TOKEN="github_pat_..."
   export LODZ_VEHICLE_POSITIONS_URL="https://otwarte.miasto.lodz.pl/wp-content/uploads/2025/06/vehicle_positions.bin"
   ```
8. Set up the `termux-services` entry: copy `service/family-a-record/run` to
   `$PREFIX/var/service/family-a-record/run` on the phone, `chmod +x`, then `sv-enable family-a-record`.
   Confirm: `sv status family-a-record` shows `run:`.
9. Copy `boot/start-services.sh` to `~/.termux/boot/start-services.sh`, `chmod +x`. Test with an
   actual phone reboot (not Force Stop - that doesn't trigger Termux:Boot at all), then check
   `cat ~/easy-gtfs-rt-termux/logs/boot.log` without opening Termux first.

`GH_TOKEN`/`LODZ_VEHICLE_POSITIONS_URL`/`LODZ_STATIC_GTFS_URL` GitHub-side equivalents
(`CALLMEBOT_PHONE`, `CALLMEBOT_APIKEY`, repo vars) already exist in `GISBoost/easy-GTFS-RT` from
the FA-7/8/9 track - nothing new to configure there for TX-4/TX-6.

## Day-to-day commands (via SSH)

Connect: `ssh -p 8022 u0_a98@<phone-ip>` (port 8022 is Termux's sshd default; `u0_a98` is the
Android user id, stable across sessions but may change after a Termux reinstall). `sshd` itself
only starts once you open the Termux app - it is not (yet) wrapped as a boot-persistent service.

```
sv status family-a-record                                   # run: = recording up, down: = not
ls ~/easy-gtfs-rt-termux/positions_$(date +%F)_*/ | wc -l    # snapshot count, should grow ~1/min
tail -f ~/easy-gtfs-rt-termux/logs/record_$(date +%F).log    # live log (Ctrl+C to detach, doesn't kill it)
grep -c "failed" ~/easy-gtfs-rt-termux/logs/record_$(date +%F).log   # failed polls today
cat ~/easy-gtfs-rt-termux/logs/boot.log                      # did Termux:Boot fire on last reboot?
```

Ad-hoc recording outside the 06:00-22:00 window:
```
bash record_custom.sh 60 60 test    # 60 min, 60s interval, output dir suffix "test"
```

After copying an updated script from the repo to the phone (e.g. via `~/storage/shared/documents/`):
```
sv down family-a-record
cp ~/storage/shared/documents/record_supervised.sh ~/easy-gtfs-rt-termux/record_supervised.sh
chmod +x ~/easy-gtfs-rt-termux/record_supervised.sh
sv up family-a-record
```
(same pattern for any other script - `sv down`/`up` only needed for files the `family-a-record`
service actually runs; `record_custom.sh`/`sweep_and_upload.sh` just need re-copying + `chmod +x`.)

## Known gotchas

- **`record_supervised.sh`'s `date +%H`/`+%M`** must go through `10#` (e.g. `10#$(date +%H)`) -
  otherwise bash parses zero-padded values like `08`/`09` as invalid octal and the script aborts.
  Already fixed in the current version; don't reintroduce a plain `$(( $(date +%H) * 60 ))`.
- **Termux:Boot's execution context does not inherit Termux env vars** (`$PREFIX` in particular).
  `boot/start-services.sh` sets them explicitly - don't remove those exports.
- **`sv-enable` cannot do anything at a cold boot** - it needs `runit`'s `runsvdir` already
  supervising the service directory, which nothing has started yet immediately after a reboot.
  `boot/start-services.sh` starts `runsvdir` directly instead of calling `sv-enable`.
- **Force Stop does not simulate a reboot.** It doesn't trigger Termux:Boot's broadcast receiver -
  only an actual device restart does. Don't use it to test boot behavior.
- **GitHub Actions `schedule:` cron has no DST awareness.** TX-4's build workflow
  (`easy-GTFS-RT/.github/workflows/family_a_build_and_notify_from_phone.yml`) needs its cron value
  manually flipped between summer (CEST, UTC+2) and winter (CET, UTC+1) - currently
  `"15 20 * * *"` (22:15 CEST). Since TX-7, this only affects the **fallback** path - the primary
  trigger (`repository_dispatch`, fired by `sweep_and_upload.sh`) doesn't depend on this cron at
  all - but still needs flipping so the fallback stays correct for the rare case it's actually
  needed. TX-6's healthcheck (`family_a_phone_healthcheck.yml`) has the same caveat for its 21:00
  check, independent of this.
- **The `repository_dispatch` `event_type` string (`"phone-sweep-complete"`) is an exact,
  case-sensitive contract** between `sweep_and_upload.sh` and the workflow's
  `on.repository_dispatch.types` list - a typo on either side means the event is silently dropped
  by GitHub with no error anywhere, not even in the Actions log (the build just quietly falls back
  to the 22:15 schedule instead). Keep both in sync if either ever changes.
- **`family_a/cli.py` always imports `numpy`/`pandas`**, even for the plain `record` subcommand -
  the phone needs the full `requirements.txt`, not just recorder-only dependencies. This is a
  deliberate decision (not touching shared `cli.py` for one track) - see PRD section 3.
- **`GH_TOKEN` (the fine-grained PAT in `~/.easy-gtfs-rt-termux.env`) expires** - fine-grained
  PATs have a mandatory expiry (commonly set to 90 days at creation). Only `sweep_and_upload.sh`
  uses it - since TX-7, that includes both the upload calls **and** the `repository_dispatch`
  call, so an expired token silently disables the fast-build path too, not just uploads. Failures
  are logged as a `WARNING:` line (not a crash - the script has no `set -e`), so there's no direct
  alert. The first visible sign is TX-6's healthcheck starting to send a WhatsApp "no raw
  recording found" alert every evening, since the raw release never gets created. Renew at
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
