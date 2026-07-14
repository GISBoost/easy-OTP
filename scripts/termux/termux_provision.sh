#!/usr/bin/env bash
set -euo pipefail

# Idempotent provisioning for the easy-GTFS-RT Termux phone-recording experiment (TX-1).
# Safe to re-run. Does NOT create secrets or generate the PAT — see
# docs/handoffs/termux-migration_plan-for-michal.md, step TX-1.

pkg update -y
pkg install -y python build-essential cmake ninja libopenblas patchelf binutils git curl jq zip \
  termux-services termux-api cronie

REPO_DIR="$HOME/easy-OTP"
if [ -d "$REPO_DIR/.git" ]; then
  git -C "$REPO_DIR" pull --ff-only
else
  git clone --depth 1 https://github.com/GISBoost/easy-OTP.git "$REPO_DIR"
fi

TOOL_DIR="$REPO_DIR/tools/family_a_reconstruction"
VENV_DIR="$HOME/easy-gtfs-rt-termux/venv"
mkdir -p "$HOME/easy-gtfs-rt-termux"
if [ ! -d "$VENV_DIR" ]; then
  python -m venv "$VENV_DIR"
fi

# Activate rather than invoking "$VENV_DIR/bin/pip" directly: numpy's meson build
# spawns cython as a subprocess and finds it via PATH, not via pip's own install
# location, so the venv's bin/ must be on PATH for the build step below.
source "$VENV_DIR/bin/activate"
pip install --upgrade pip

# numpy/pandas: PyPI wheels target glibc Linux, not Android's Bionic libc, so these
# must be built from source here. This is the step most likely to fail — if it does,
# this whole track is blocked (PRD section 3/4), report the exact error back rather
# than trying workarounds not documented in the PRD.
# numpy/pandas build with meson-python, not classic setuptools. --no-build-isolation
# means pip won't auto-fetch build tools, so the meson-python backend must already be
# in the venv before the build — otherwise pip fails at the metadata step with
# "Cannot import 'mesonpy'".
pip install --no-cache-dir meson meson-python "Cython>=3.0.6" pybind11 "versioneer[toml]"

PYVER="$(python3 -c "import sysconfig; print(sysconfig.get_python_version())")"
LDFLAGS="-lpython${PYVER}" pip install --no-build-isolation --no-cache-dir numpy
LDFLAGS="-lpython${PYVER}" pip install --no-build-isolation --no-cache-dir pandas
pip install -r "$TOOL_DIR/requirements.txt"

mkdir -p "$HOME/easy-gtfs-rt-termux/logs"
mkdir -p "$HOME/easy-gtfs-rt-termux/cities"

echo "Provisioning done. Detected Python version: ${PYVER}. Activate with: source $VENV_DIR/bin/activate"
echo "Next: create ~/.easy-gtfs-rt-termux.env by hand (see the plan doc, step TX-1)."
echo "Then create one ~/easy-gtfs-rt-termux/cities/<city_id>.env per city to record (TX-8, see scripts/termux/README.md)."
echo "Then verify: cd $TOOL_DIR && python -m family_a.cli --help"
