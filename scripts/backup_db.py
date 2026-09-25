"""Copy the live database into BACKUP_DIR and keep only the newest copies.

Uses SQLite's online backup API, so it is safe while the app is serving
requests: a plain file copy taken mid-write can be a corrupt database.
Meant for a daily PythonAnywhere scheduled task (see DEPLOY.md):

    python /home/<you>/workout-planner/scripts/backup_db.py

Reads DATABASE_PATH and BACKUP_DIR through config, like the app.
"""
import argparse
import glob
import os
import sqlite3
import sys
from datetime import datetime

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import config  # noqa: E402

PREFIX = 'fittrack-'


def backup(src_path, dest_dir, keep=7):
    """Write one timestamped copy, delete all but the newest ``keep``; return its path."""
    if not os.path.exists(src_path):
        raise FileNotFoundError(f'No database at {src_path}')
    os.makedirs(dest_dir, exist_ok=True)
    dest_path = os.path.join(dest_dir, f'{PREFIX}{datetime.now():%Y%m%d-%H%M%S}.db')
    src = sqlite3.connect(src_path)
    dest = sqlite3.connect(dest_path)
    try:
        with dest:
            src.backup(dest)
    finally:
        dest.close()
        src.close()
    # Names sort by time, so the oldest come first.
    copies = sorted(glob.glob(os.path.join(dest_dir, f'{PREFIX}*.db')))
    for old in copies[:-keep] if keep > 0 else []:
        os.remove(old)
    return dest_path


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument('--keep', type=int, default=7, help='copies to keep (default 7)')
    args = parser.parse_args(argv)
    path = backup(config.DATABASE_PATH, config.BACKUP_DIR, args.keep)
    print(f'Backed up {config.DATABASE_PATH} to {path}')


if __name__ == '__main__':
    main()
