#!/usr/bin/env sh
# FitTrack launcher for macOS and Linux: makes the virtualenv on first run,
# installs requirements when they change, starts the app and opens the browser.
set -e
cd "$(dirname "$0")"

if [ ! -x venv/bin/python ]; then
    if ! command -v python3 >/dev/null 2>&1; then
        echo "Python 3.10 or newer is needed: https://www.python.org/downloads/"
        exit 1
    fi
    echo "Creating virtual environment..."
    python3 -m venv venv
fi

# Reinstall only when requirements.txt differs from the copy saved last time.
if ! cmp -s requirements.txt venv/requirements.installed; then
    echo "Installing requirements..."
    venv/bin/python -m pip install --disable-pip-version-check -q -r requirements.txt
    cp requirements.txt venv/requirements.installed
fi

export FITTRACK_MODE="${FITTRACK_MODE:-local}"
export FITTRACK_OPEN_BROWSER=1
exec venv/bin/python app.py
