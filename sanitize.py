"""Input sanitization to prevent XSS and injection."""

import bleach
import re


def sanitize_text(text: str) -> str:
    """Strip all HTML tags and dangerous content from user input."""
    if not text:
        return ""
    # Strip HTML tags
    cleaned = bleach.clean(text, tags=[], attributes={}, strip=True)
    # Remove null bytes
    cleaned = cleaned.replace("\x00", "")
    return cleaned.strip()


def sanitize_citizen_id(citizen_id: str) -> str:
    """Validate and sanitize citizen IDs — alphanumeric, hyphens, underscores only."""
    if not citizen_id:
        return ""
    cleaned = re.sub(r"[^a-zA-Z0-9\-_]", "", citizen_id)
    return cleaned[:100]  # Max length


def sanitize_platform(platform: str) -> str:
    """Sanitize platform names."""
    if not platform:
        return "unknown"
    cleaned = re.sub(r"[^a-zA-Z0-9\-_.]", "", platform)
    return cleaned[:50] or "unknown"
