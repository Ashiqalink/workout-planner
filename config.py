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

BASE_DIR = os.path.dirname(os.path.abspath(__file__))

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

    g['LM_STUDIO_API_URL'] = (environ.get('LM_STUDIO_API_URL')
                              or 'http://localhost:1234/v1').rstrip('/')
    g['TYPESAFE_API_KEY'] = (environ.get('TYPESAFE_API_KEY') or '').strip()
    # Only the download talks to a local model server. A hosted server has no
    # model beside it, and the address would be one the operator did not vet.
    g['LOCAL_MODEL_ALLOWED'] = IS_LOCAL

    # CLI switch for `flask tag-regions`: re-tag rows that already have tags.
    g['TAG_ALL'] = _flag(environ, 'TAG_ALL')


def validate():
    """Refuse to start on a configuration that is unsafe or unusable."""
    errors = []
    if MODE not in MODES:
        errors.append(f'FITTRACK_MODE must be one of {", ".join(MODES)}, not "{MODE}".')
    if not str(FLASK_PORT).isdigit():
        errors.append(f'FLASK_PORT must be a number, not "{FLASK_PORT}".')
    if IS_HOSTED and not SECRET_KEY:
        errors.append('Hosted mode needs SECRET_KEY: without it every restart '
                      'logs everyone out and session cookies can be forged.')
    if errors:
        raise ConfigError(' '.join(errors))


load()
