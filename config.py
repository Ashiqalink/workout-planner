"""Every environment read in FitTrack, and the one switch between its two modes.

``FITTRACK_MODE=local`` (the default) is the download: one person on their own
machine, a local model server allowed. ``FITTRACK_MODE=hosted`` is the website:
many accounts, and the server only ever talks to addresses in this file — never
one a user typed into their settings (that would let any account make the
server fetch any URL it can reach).

Nothing else in the app reads ``os.environ``. Code asks this module, so each
difference between the modes is one ``IS_HOSTED`` check in one place.

Values are read by :func:`load`, which runs at import. Tests call it again
after ``monkeypatch.setenv`` to switch modes.
"""

import os
import secrets

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
# Per-install files that must never be committed (the generated secret key).
INSTANCE_DIR = os.path.join(BASE_DIR, 'instance')

MODES = ('local', 'hosted')


class ConfigError(RuntimeError):
    """Raised by :func:`validate` when the server must not start."""


def _flag(environ, name, default='0'):
    return str(environ.get(name, default)).strip().lower() in ('1', 'true', 'yes', 'on')


def load(environ=None):
    """(Re)read the environment into this module's globals."""
    environ = os.environ if environ is None else environ
    g = globals()
    g['MODE'] = (environ.get('FITTRACK_MODE') or 'local').strip().lower()
    g['IS_LOCAL'] = MODE == 'local'
    g['IS_HOSTED'] = MODE == 'hosted'

    g['SECRET_KEY'] = environ.get('SECRET_KEY') or ''
    g['DATABASE_PATH'] = environ.get('DATABASE_PATH') or os.path.join(BASE_DIR, 'training_app.db')
    g['FLASK_HOST'] = environ.get('FLASK_HOST') or '127.0.0.1'
    g['FLASK_PORT'] = environ.get('FLASK_PORT') or '5000'
    # Hosted never runs the debugger: it executes code typed into a browser.
    g['FLASK_DEBUG'] = _flag(environ, 'FLASK_DEBUG') and not IS_HOSTED
    g['LOG_LEVEL'] = (environ.get('LOG_LEVEL') or ('DEBUG' if FLASK_DEBUG else 'INFO')).upper()
    g['ALLOW_SIGNUP'] = _flag(environ, 'ALLOW_SIGNUP', '1')
    # POSTs to /login and /register per client IP, in Flask-Limiter syntax.
    # Counted in memory, so each worker process keeps its own tally.
    g['LOGIN_RATE_LIMIT'] = environ.get('LOGIN_RATE_LIMIT') or '10 per minute'
    # Reverse proxies in front of the app. Behind one, every request comes from
    # the proxy's address, so the rate limit would be shared by everyone; with
    # 1 the client address is read from X-Forwarded-For. Never set it higher
    # than the real proxy count: the header is otherwise the client's to forge.
    g['TRUSTED_PROXIES'] = environ.get('TRUSTED_PROXIES') or '0'
    # Session cookie only over HTTPS. On by default for the website.
    g['SECURE_COOKIES'] = _flag(environ, 'SECURE_COOKIES', '1' if IS_HOSTED else '0')
    # Where scripts/backup_db.py writes its copies.
    g['BACKUP_DIR'] = environ.get('BACKUP_DIR') or os.path.join(BASE_DIR, 'backups')
    # The download signs its one user in automatically. LOCAL_SINGLE_USER=0
    # brings back accounts; hosted always has them.
    g['LOCAL_SINGLE_USER'] = IS_LOCAL and _flag(environ, 'LOCAL_SINGLE_USER', '1')
    # Set by run.bat / run.sh: open the browser once the server is listening.
    g['OPEN_BROWSER'] = _flag(environ, 'FITTRACK_OPEN_BROWSER')

    g['LM_STUDIO_API_URL'] = (environ.get('LM_STUDIO_API_URL')
                              or 'http://localhost:1234/v1').rstrip('/')
    g['TYPESAFE_API_KEY'] = (environ.get('TYPESAFE_API_KEY') or '').strip()
    # Only the download talks to a local model server. A hosted server has no
    # model beside it, and the address would be one the operator did not vet.
    g['LOCAL_MODEL_ALLOWED'] = IS_LOCAL

    # CLI switch for `flask tag-regions`: re-tag rows that already have tags.
    g['TAG_ALL'] = _flag(environ, 'TAG_ALL')


def secret_key(instance_dir=None):
    """SECRET_KEY if set; otherwise the key saved on first run, made if missing.

    Returns ``(key, source)``. Without a saved key every restart would sign
    everyone out. Hosted mode never gets here: validate() insists on the
    environment variable. If the instance folder cannot be written the key is
    random for this process only, and ``source`` says so.
    """
    if SECRET_KEY:
        return SECRET_KEY, 'env'
    path = os.path.join(instance_dir or INSTANCE_DIR, 'secret_key')
    try:
        with open(path, encoding='utf-8') as f:
            saved = f.read().strip()
        if saved:
            return saved, 'file'
    except FileNotFoundError:
        pass
    key = secrets.token_hex(32)
    try:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        # 0o600: the key signs session cookies, so no other account may read it.
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, 'w', encoding='utf-8') as f:
            f.write(key)
    except OSError:
        return key, 'random'
    return key, 'created'


def validate():
    """Refuse to start on a configuration that is unsafe or unusable."""
    errors = []
    if MODE not in MODES:
        errors.append(f'FITTRACK_MODE must be one of {", ".join(MODES)}, not "{MODE}".')
    if not str(FLASK_PORT).isdigit():
        errors.append(f'FLASK_PORT must be a number, not "{FLASK_PORT}".')
    if not str(TRUSTED_PROXIES).isdigit():
        errors.append(f'TRUSTED_PROXIES must be a number, not "{TRUSTED_PROXIES}".')
    if IS_HOSTED and not SECRET_KEY:
        errors.append('Hosted mode needs SECRET_KEY: without it every restart '
                      'logs everyone out and session cookies can be forged.')
    if errors:
        raise ConfigError(' '.join(errors))


load()
