"""Gunicorn production config."""

import os

bind = f"{os.environ.get('HOST', '0.0.0.0')}:{os.environ.get('PORT', '5050')}"
workers = int(os.environ.get("GUNICORN_WORKERS", "4"))
worker_class = "sync"
timeout = 120
keepalive = 5
accesslog = "-"
errorlog = "-"
loglevel = os.environ.get("LOG_LEVEL", "info").lower()
preload_app = True
