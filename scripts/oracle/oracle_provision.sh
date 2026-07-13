#!/usr/bin/env bash
set -euo pipefail

# Idempotent provisioning for the easy-GTFS-RT Oracle Cloud fallback (OR-2).
# Target: Oracle Linux 9.x, user "opc", VM.Standard.E2.1.Micro (1 GB RAM) —
# confirmed on Michal's live instance 2026-07-13. Safe to re-run.
# Does NOT create secrets or authenticate gh — see
# docs/handoffs/oracle-migration_plan-for-michal.md, step OR-2.

# Swap file — mitigation for the 1 GB RAM shape (PRD section 4), created BEFORE
# any dnf operation. dnf's own dependency resolution / transaction check on a
# full `update` can itself need more memory than 1 GB provides on a fresh VM
# (observed directly: `dnf update -y` OOM-killed on Michal's instance when swap
# didn't exist yet) — so this mitigation has to be in place before the first
# dnf call, not just before `family_a build` later. 2 GB is a starting guess,
# not a verified-sufficient size — see OR-4's human verification step, which
# checks real memory behavior on the actual build run.
SWAP_FILE="/swapfile"
if [ ! -f "$SWAP_FILE" ]; then
  sudo fallocate -l 2G "$SWAP_FILE" || sudo dd if=/dev/zero of="$SWAP_FILE" bs=1M count=2048
  sudo chmod 600 "$SWAP_FILE"
  sudo mkswap "$SWAP_FILE"
  sudo swapon "$SWAP_FILE"
  if ! grep -q "^$SWAP_FILE" /etc/fstab; then
    echo "$SWAP_FILE none swap sw 0 0" | sudo tee -a /etc/fstab > /dev/null
  fi
fi

sudo dnf update -y
sudo dnf install -y python3 python3-pip git unzip curl jq

# GitHub CLI (official RPM repo, RHEL/Fedora family)
if ! command -v gh >/dev/null 2>&1; then
  sudo dnf install -y 'dnf-command(config-manager)'
  sudo dnf config-manager --add-repo https://cli.github.com/packages/rpm/gh-cli.repo
  sudo dnf install -y gh
fi

# Read-only clone of easy-OTP — never pushed to, re-pulled on each run to stay
# on main. Mirrors the actions/checkout discipline FA-7/8/9 already use.
REPO_DIR="$HOME/easy-OTP"
if [ -d "$REPO_DIR/.git" ]; then
  git -C "$REPO_DIR" pull --ff-only
else
  git clone --depth 1 https://github.com/GISBoost/easy-OTP.git "$REPO_DIR"
fi

TOOL_DIR="$REPO_DIR/tools/family_a_reconstruction"
VENV_DIR="$HOME/easy-gtfs-rt-oracle/venv"
mkdir -p "$HOME/easy-gtfs-rt-oracle"
if [ ! -d "$VENV_DIR" ]; then
  python3 -m venv "$VENV_DIR"
fi
"$VENV_DIR/bin/pip" install --upgrade pip
"$VENV_DIR/bin/pip" install -r "$TOOL_DIR/requirements.txt"

# Working directory for recordings/builds/logs lives outside the cloned repo,
# so git pull never conflicts with data written here.
mkdir -p "$HOME/easy-gtfs-rt-oracle/logs"

echo "Provisioning done. Swap: $(swapon --show || echo none). Activate venv with: source $VENV_DIR/bin/activate"
echo "Next: create ~/.easy-gtfs-rt-oracle.env by hand (see the plan doc, step OR-2)."
