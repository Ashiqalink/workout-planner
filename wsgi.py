"""WSGI entry point for the website: ``gunicorn wsgi:application``.

A server started through this file is the hosted site unless FITTRACK_MODE
says otherwise, so a forgotten variable fails closed: hosted mode refuses to
start without SECRET_KEY instead of quietly running as the single-user
download. On PythonAnywhere, set the environment in the WSGI file before
importing this module (see DEPLOY.md).
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
os.environ.setdefault('FITTRACK_MODE', 'hosted')

from app import app as application  # noqa: E402

app = application
