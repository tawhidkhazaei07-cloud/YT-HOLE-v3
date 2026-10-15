"""WSGI entry point for external servers.

    waitress-serve --host 127.0.0.1 --port 5000 backend.wsgi:application
    gunicorn -w 1 --threads 16 -b 127.0.0.1:5000 backend.wsgi:application

Use ONE worker process: download jobs and rate limits live in the process.
Scale with threads (--threads), not workers.
"""

from backend.app import Application
from backend.config import load_settings
from backend.core.logging_setup import setup_logging

_settings = load_settings()
setup_logging(_settings.log_dir, _settings.log_level)
application = Application(_settings)
