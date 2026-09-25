# Deploying FitTrack as a website

This runs FitTrack in hosted mode: many accounts, sign-up and login, and
workouts built by the rules engine (plus the TypeSafe coaching judge if you
give it a key). It never talks to a local model server.

The steps are for a free [PythonAnywhere](https://www.pythonanywhere.com)
account. Replace `yourname` with your PythonAnywhere username throughout.
Any other host that runs a WSGI app works the same way; see
[Other hosts](#other-hosts) at the end.

## 1. Clone and make the virtualenv

Open a **Bash console** from the Dashboard and run:

```bash
git clone https://github.com/Ashiqalink/workout-planner.git
cd workout-planner
python3.10 -m venv venv
venv/bin/pip install -r requirements.txt
```

Then make a secret key and copy the line it prints. It signs everyone's
login cookie, so keep it private and never commit it:

```bash
python3.10 -c "import secrets; print(secrets.token_hex(32))"
```

## 2. Create the web app

On the **Web** tab: *Add a new web app* → *Manual configuration* (not
"Flask") → *Python 3.10*.

On the same page, under **Virtualenv**, enter:

```
/home/yourname/workout-planner/venv
```

Under **Static files**, add one mapping:

| URL | Directory |
| --- | --- |
| `/static/` | `/home/yourname/workout-planner/static` |

Under **Security**, turn on **Force HTTPS**. Hosted mode sends the login
cookie over HTTPS only, so plain `http://` would never stay signed in.

## 3. The WSGI file

Still on the Web tab, open the **WSGI configuration file** link
(`/var/www/yourname_pythonanywhere_com_wsgi.py`), delete everything in it and
paste:

```python
import os
import sys

PROJECT = '/home/yourname/workout-planner'
sys.path.insert(0, PROJECT)

# FitTrack reads its settings from the environment. Set them before the import.
os.environ['FITTRACK_MODE'] = 'hosted'
os.environ['SECRET_KEY'] = 'paste-the-key-from-step-1'
# PythonAnywhere sits behind one proxy. Without this every visitor shares
# one address and the login rate limit locks everyone out together.
os.environ['TRUSTED_PROXIES'] = '1'
# Uncomment to stop new sign-ups (existing accounts can still log in).
# os.environ['ALLOW_SIGNUP'] = '0'
# Uncomment to turn on the coaching judge. See "Outbound requests" below.
# os.environ['TYPESAFE_API_KEY'] = 'your-key'

from wsgi import application  # noqa: E402,F401
```

Save, then press the green **Reload** button at the top of the Web tab. Open
`https://yourname.pythonanywhere.com`, register an account and build a
workout.

The database is created on first start at
`/home/yourname/workout-planner/training_app.db`. Set `DATABASE_PATH` in the
WSGI file to put it elsewhere, and use the same value in step 4.

## 4. Nightly backup

On the **Tasks** tab, add a daily scheduled task (pick a quiet hour) with
this command:

```bash
/home/yourname/workout-planner/venv/bin/python /home/yourname/workout-planner/scripts/backup_db.py
```

Each run writes `backups/fittrack-YYYYmmdd-HHMMSS.db` beside the app and
deletes all but the newest 7. Add `--keep 14` to keep two weeks instead. It
uses SQLite's backup API, so it is safe while people are using the site.
If you set `DATABASE_PATH` or `BACKUP_DIR` in the WSGI file, put them in
front of the command too, because scheduled tasks do not read that file:

```bash
DATABASE_PATH=/home/yourname/data/fittrack.db /home/yourname/workout-planner/venv/bin/python /home/yourname/workout-planner/scripts/backup_db.py
```

To restore, stop the site (Web tab → *Disable*), copy a backup over the
database file, and enable it again.

## 5. Keep the site running

A free web app is switched off if nobody logs in to PythonAnywhere for three
months. Log in before then and press **Run until 3 months from today** on
the Web tab. Put a reminder in your calendar now, every 11 weeks.

## Outbound requests on the free tier

Free accounts can only make web requests to sites on PythonAnywhere's
[allowlist](https://www.pythonanywhere.com/whitelist/). FitTrack itself needs
none: the rules engine runs entirely on the server. The TypeSafe judge calls
`api.typesafe.ai`; if that is not on the list, the judge times out and every
workout is served without a confidence score. Either ask PythonAnywhere to
add it (from the *Send feedback* link) or use a paid account, which has no
allowlist.

## Updating

```bash
cd ~/workout-planner
git pull
venv/bin/pip install -r requirements.txt
```

Then **Reload** on the Web tab. New tables and columns are added on start;
nothing is dropped.

## Settings

| Variable | Hosted default | Notes |
| --- | --- | --- |
| `FITTRACK_MODE` | `hosted` via `wsgi.py` | `wsgi.py` assumes hosted, so a missing value fails closed |
| `SECRET_KEY` | none | Required. The server will not start without it |
| `TRUSTED_PROXIES` | `0` | `1` on PythonAnywhere. Never more than the real proxy count |
| `ALLOW_SIGNUP` | `1` | `0` closes registration |
| `LOGIN_RATE_LIMIT` | `10 per minute` | POSTs to login and register per client address |
| `SECURE_COOKIES` | `1` | Login cookie over HTTPS only |
| `TYPESAFE_API_KEY` | unset | Optional coaching judge |
| `DATABASE_PATH` | `training_app.db` beside the app | |
| `BACKUP_DIR` | `backups/` beside the app | Used by `scripts/backup_db.py` |

The rate limit is counted in memory by each worker process. PythonAnywhere's
free tier runs one worker, so the limit is exact there. With N workers a
client can make up to N times the limit, and a reload resets the counts.

Debug mode is always off in hosted mode, whatever `FLASK_DEBUG` says.

## Other hosts

`wsgi.py` exposes `application` for any WSGI server. With gunicorn, behind
one reverse proxy that terminates HTTPS:

```bash
pip install gunicorn
export SECRET_KEY=... TRUSTED_PROXIES=1
gunicorn --workers 2 --bind 127.0.0.1:8000 wsgi:application
```

Serve `static/` from the proxy if you can, and run `scripts/backup_db.py`
from cron.
