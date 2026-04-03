"""
Privileges Gateway
API module that external government systems (travel, banking, employment, healthcare)
call to verify a citizen's tier and determine access privileges before granting services.
"""

import datetime
import secrets

# ── Tier-based privilege matrix ──────────────────────────────────────────────
# Each tier maps to what each government service should allow.

TIER_PRIVILEGES = {
    "EXEMPLARY": {
        "travel": {"international": True, "domestic": True, "fast_track": True, "border_priority": True},
        "banking": {"max_transaction": 1_000_000, "international_transfers": True, "credit_access": True, "loan_priority": True},
        "employment": {"public_sector": True, "private_sector": True, "security_clearance": "top_secret"},
        "healthcare": {"priority": "immediate", "specialist_access": True, "experimental_treatment": True},
    },
    "TRUSTED": {
        "travel": {"international": True, "domestic": True, "fast_track": False, "border_priority": False},
        "banking": {"max_transaction": 500_000, "international_transfers": True, "credit_access": True, "loan_priority": False},
        "employment": {"public_sector": True, "private_sector": True, "security_clearance": "secret"},
        "healthcare": {"priority": "standard", "specialist_access": True, "experimental_treatment": False},
    },
    "NORMAL": {
        "travel": {"international": True, "domestic": True, "fast_track": False, "border_priority": False},
        "banking": {"max_transaction": 100_000, "international_transfers": False, "credit_access": True, "loan_priority": False},
        "employment": {"public_sector": True, "private_sector": True, "security_clearance": "confidential"},
        "healthcare": {"priority": "standard", "specialist_access": False, "experimental_treatment": False},
    },
    "SUSPICIOUS": {
        "travel": {"international": False, "domestic": True, "fast_track": False, "border_priority": False},
        "banking": {"max_transaction": 25_000, "international_transfers": False, "credit_access": False, "loan_priority": False},
        "employment": {"public_sector": False, "private_sector": True, "security_clearance": "none"},
        "healthcare": {"priority": "queued", "specialist_access": False, "experimental_treatment": False},
    },
    "MONITORED": {
        "travel": {"international": False, "domestic": True, "fast_track": False, "border_priority": False},
        "banking": {"max_transaction": 10_000, "international_transfers": False, "credit_access": False, "loan_priority": False},
        "employment": {"public_sector": False, "private_sector": True, "security_clearance": "none"},
        "healthcare": {"priority": "queued", "specialist_access": False, "experimental_treatment": False},
    },
    "DISSIDENT": {
        "travel": {"international": False, "domestic": False, "fast_track": False, "border_priority": False},
        "banking": {"max_transaction": 1_000, "international_transfers": False, "credit_access": False, "loan_priority": False},
        "employment": {"public_sector": False, "private_sector": False, "security_clearance": "none"},
        "healthcare": {"priority": "emergency_only", "specialist_access": False, "experimental_treatment": False},
    },
}

VALID_SERVICES = set(TIER_PRIVILEGES["EXEMPLARY"].keys())

# ── API key registry ─────────────────────────────────────────────────────────
# Maps API keys to the calling system's identity.

API_KEYS = {
    "gw_travel_authority_a8f3c1": "travel_authority",
    "gw_central_bank_d92e07": "central_bank",
    "gw_employment_bureau_4bc519": "employment_bureau",
    "gw_health_ministry_71fa88": "health_ministry",
    "gw_demo_key": "demo_system",
}

# ── Audit log ────────────────────────────────────────────────────────────────

audit_log = []


def verify_api_key(key):
    """Return the system name if valid, else None."""
    return API_KEYS.get(key)


def generate_api_key(system_name):
    """Issue a new API key for a government system."""
    token = f"gw_{system_name}_{secrets.token_hex(6)}"
    API_KEYS[token] = system_name
    return token


def check_privileges(citizen_record, service, action=None):
    """
    Core gateway logic. Takes a citizen record (from InMemoryDB) and a service name,
    returns the privilege verdict.
    """
    score = citizen_record["civic_score"]
    tier = _score_to_tier(score)

    if service not in VALID_SERVICES:
        return None, f"Unknown service: {service}"

    privileges = TIER_PRIVILEGES[tier][service]

    # Determine overall allowed status
    allowed = True
    if tier == "DISSIDENT":
        allowed = False
    elif action and action in privileges:
        val = privileges[action]
        allowed = bool(val) if isinstance(val, bool) else val > 0

    result = {
        "citizen_id": citizen_record["citizen_id"],
        "civic_score": score,
        "tier": tier,
        "service": service,
        "allowed": allowed,
        "privileges": privileges,
        "checked_at": datetime.datetime.now().isoformat(),
    }

    # Write audit entry
    audit_log.append({
        "timestamp": result["checked_at"],
        "citizen_id": citizen_record["citizen_id"],
        "service": service,
        "action": action,
        "tier": tier,
        "allowed": allowed,
    })

    return result, None


def get_audit_log(citizen_id=None, limit=50):
    logs = audit_log
    if citizen_id:
        logs = [l for l in logs if l["citizen_id"] == citizen_id]
    return logs[-limit:]


def _score_to_tier(score):
    if score >= 800:
        return "EXEMPLARY"
    elif score >= 700:
        return "TRUSTED"
    elif score >= 500:
        return "NORMAL"
    elif score >= 400:
        return "SUSPICIOUS"
    elif score >= 300:
        return "MONITORED"
    else:
        return "DISSIDENT"
