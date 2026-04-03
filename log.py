"""Structured logging setup."""

import json
import logging
import sys
from datetime import datetime, timezone

from config import cfg


class JSONFormatter(logging.Formatter):
    def format(self, record):
        entry = {
            "ts": datetime.now(timezone.utc).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "msg": record.getMessage(),
        }
        if record.exc_info and record.exc_info[0]:
            entry["exception"] = self.formatException(record.exc_info)
        return json.dumps(entry)


def setup_logging():
    root = logging.getLogger()
    root.setLevel(getattr(logging, cfg.LOG_LEVEL.upper(), logging.INFO))

    # Clear existing handlers
    root.handlers.clear()

    if cfg.LOG_FORMAT == "json":
        formatter = JSONFormatter()
    else:
        formatter = logging.Formatter(
            "%(asctime)s %(levelname)-8s [%(name)s] %(message)s",
            datefmt="%Y-%m-%d %H:%M:%S",
        )

    # Stdout handler
    stdout = logging.StreamHandler(sys.stdout)
    stdout.setFormatter(formatter)
    root.addHandler(stdout)

    # File handler (if configured)
    if cfg.LOG_FILE:
        fh = logging.FileHandler(cfg.LOG_FILE)
        fh.setFormatter(formatter)
        root.addHandler(fh)

    # Silence noisy libraries
    logging.getLogger("werkzeug").setLevel(logging.WARNING)
    logging.getLogger("urllib3").setLevel(logging.WARNING)
