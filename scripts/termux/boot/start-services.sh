#!/data/data/com.termux/files/usr/bin/sh
set -uo pipefail   # not -e: a failed pgrep/wake-lock shouldn't stop the rest of boot recovery

# Termux:Boot entry point for the easy-GTFS-RT phone-recording experiment. Termux:Boot runs every
# executable script under ~/.termux/boot/ once after Android finishes booting, even if the Termux
# app itself is never opened. Without this, record_supervised.sh's termux-services-supervised loop
# (TX-2) only resumes after Michal manually opens Termux, since opening a session is normally
# what starts the runit supervisor tree - so a phone restart (system update, dead battery, manual
# reboot) would otherwise silently end recording for the rest of the day.
#
# Termux:Boot invokes this via Android's RunCommandService, not a normal login shell, so
# Termux-specific environment variables (most importantly $PREFIX) are not inherited the way
# they are in an interactive session - set explicitly below.
export PREFIX=/data/data/com.termux/files/usr
export HOME=/data/data/com.termux/files/home
export PATH="$PREFIX/bin:$PATH"
export LD_LIBRARY_PATH="$PREFIX/lib"

LOG="$HOME/easy-gtfs-rt-termux/logs/boot.log"
mkdir -p "$HOME/easy-gtfs-rt-termux/logs"
{
  echo "$(TZ=Europe/Warsaw date '+%F %T') start-services.sh invoked by Termux:Boot"
  termux-wake-lock

  # sv-enable/sv need runit's runsvdir already actively supervising $PREFIX/var/service before
  # they can do anything - at a cold boot nothing has started it yet, so sv-enable alone fails
  # with "unable to change to service directory: file does not exist" (confirmed on-device
  # 2026-07-13). Opening Termux normally works only because that's what starts runsvdir in the
  # first place (via termux-services' own shell-profile hook, which may pass different arguments
  # than the invocation below). Starting it directly here removes that dependency: runsvdir scans
  # the service directory itself and starts every service that isn't marked "down" (family-a-record
  # already isn't, from earlier setup), no sv-enable call needed. Matched on the bare process name
  # (not the exact command line) so this correctly detects an already-running runsvdir regardless
  # of which of the two invocations started it first - runit's own supervise/lock prevents a second
  # runsvdir from actually double-supervising the same directory even if this check ever raced.
  if ! pgrep -x runsvdir >/dev/null 2>&1; then
    nohup runsvdir "$PREFIX/var/service" >/dev/null 2>&1 &
    echo "$(TZ=Europe/Warsaw date '+%F %T') started runsvdir (pid $!)"
  else
    echo "$(TZ=Europe/Warsaw date '+%F %T') runsvdir already running"
  fi
} >> "$LOG" 2>&1
