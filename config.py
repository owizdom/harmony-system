"""Centralized configuration — all settings loaded from env vars with sane defaults."""

import os


def _bool(val: str) -> bool:
    return val.lower() in ("1", "true", "yes")


class Config:
    # --- App ---
    SECRET_KEY: str = os.environ.get("SECRET_KEY", "change-me-in-production")
    DEBUG: bool = _bool(os.environ.get("DEBUG", "false"))
    HOST: str = os.environ.get("HOST", "0.0.0.0")
    PORT: int = int(os.environ.get("PORT", "5050"))

    # --- Auth ---
    ADMIN_USERNAME: str = os.environ.get("ADMIN_USERNAME", "admin")
    ADMIN_PASSWORD: str = os.environ.get("ADMIN_PASSWORD", "admin")
    AUTH_ENABLED: bool = _bool(os.environ.get("AUTH_ENABLED", "false"))
    SESSION_LIFETIME_HOURS: int = int(os.environ.get("SESSION_LIFETIME_HOURS", "24"))

    # --- Database ---
    DATABASE_URL: str = os.environ.get("DATABASE_URL", "sqlite:///citizens.db")
    # Legacy compat: if DB_PATH is set, use SQLite with that path
    DB_PATH: str = os.environ.get("DB_PATH", "citizens.db")

    # --- Anthropic ---
    ANTHROPIC_API_KEY: str = os.environ.get("ANTHROPIC_API_KEY", "")
    LLM_MODEL: str = os.environ.get("LLM_MODEL", "claude-sonnet-4-6-20250514")
    LLM_MAX_TOKENS: int = int(os.environ.get("LLM_MAX_TOKENS", "512"))
    LLM_TIMEOUT: int = int(os.environ.get("LLM_TIMEOUT", "30"))
    LLM_MAX_RETRIES: int = int(os.environ.get("LLM_MAX_RETRIES", "3"))

    # --- Rate Limiting ---
    RATE_LIMIT_CLASSIFY: str = os.environ.get("RATE_LIMIT_CLASSIFY", "30/minute")
    RATE_LIMIT_IMPORT: str = os.environ.get("RATE_LIMIT_IMPORT", "5/minute")
    RATE_LIMIT_DEFAULT: str = os.environ.get("RATE_LIMIT_DEFAULT", "120/minute")

    # --- Ingestion ---
    TWITTER_BEARER_TOKEN: str = os.environ.get("TWITTER_BEARER_TOKEN", "")
    TWITTER_TRACK_USERS: str = os.environ.get("TWITTER_TRACK_USERS", "")
    TWITTER_RULES: str = os.environ.get("TWITTER_RULES", "")
    META_ACCESS_TOKEN: str = os.environ.get("META_ACCESS_TOKEN", "")
    META_APP_SECRET: str = os.environ.get("META_APP_SECRET", "")
    META_PAGE_IDS: str = os.environ.get("META_PAGE_IDS", "")
    META_IG_USER_IDS: str = os.environ.get("META_IG_USER_IDS", "")

    # --- Logging ---
    LOG_LEVEL: str = os.environ.get("LOG_LEVEL", "INFO")
    LOG_FILE: str = os.environ.get("LOG_FILE", "")  # empty = stdout only
    LOG_FORMAT: str = os.environ.get("LOG_FORMAT", "json")  # "json" or "text"

    # --- Pagination ---
    DEFAULT_PAGE_SIZE: int = int(os.environ.get("DEFAULT_PAGE_SIZE", "50"))
    MAX_PAGE_SIZE: int = int(os.environ.get("MAX_PAGE_SIZE", "500"))

    # --- Bulk Import ---
    MAX_IMPORT_ROWS: int = int(os.environ.get("MAX_IMPORT_ROWS", "500"))
    MAX_BATCH_SIZE: int = int(os.environ.get("MAX_BATCH_SIZE", "50"))

    # --- Activity Log ---
    ACTIVITY_LOG_TTL_DAYS: int = int(os.environ.get("ACTIVITY_LOG_TTL_DAYS", "90"))

    # --- Classification ---
    ESCALATION_THRESHOLD: float = float(os.environ.get("ESCALATION_THRESHOLD", "0.65"))
    HIGH_CONFIDENCE_THRESHOLD: float = float(os.environ.get("HIGH_CONFIDENCE_THRESHOLD", "0.8"))
    HIGH_CONFIDENCE_MULTIPLIER: float = float(os.environ.get("HIGH_CONFIDENCE_MULTIPLIER", "1.5"))


cfg = Config()
