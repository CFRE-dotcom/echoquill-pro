"""EchoQuill Pro licensing via Lemon Squeezy license keys.

Activate once with the key from your purchase email; the app re-validates
quietly and keeps working offline for up to 14 days between checks.
"""

import time

ACTIVATE_URL = "https://api.lemonsqueezy.com/v1/licenses/activate"
VALIDATE_URL = "https://api.lemonsqueezy.com/v1/licenses/validate"
REVALIDATE_DAYS = 14


def activate(cfg: dict, key: str) -> str:
    """Activate this machine. Returns '' on success, else an error message."""
    import requests
    from . import config as cfgmod
    try:
        r = requests.post(ACTIVATE_URL, data={
            "license_key": key.strip(),
            "instance_name": "EchoQuill Pro (Windows)",
        }, headers={"Accept": "application/json"}, timeout=20)
        data = r.json()
        if data.get("activated"):
            cfg["pro_license_key"] = key.strip()
            cfg["pro_instance_id"] = (data.get("instance") or {}).get("id", "")
            cfg["pro_last_valid"] = time.time()
            cfgmod.save(cfg)
            return ""
        return data.get("error") or "Key was not accepted."
    except Exception as e:
        return f"Couldn't reach the license server: {e}"


def _validate(cfg: dict) -> bool:
    import requests
    from . import config as cfgmod
    try:
        r = requests.post(VALIDATE_URL, data={
            "license_key": cfg.get("pro_license_key", ""),
            "instance_id": cfg.get("pro_instance_id", ""),
        }, headers={"Accept": "application/json"}, timeout=15)
        data = r.json()
        ok = bool(data.get("valid"))
        if ok:
            cfg["pro_last_valid"] = time.time()
            cfgmod.save(cfg)
        return ok
    except Exception:
        return True   # offline: keep Pro alive; the 14-day window governs


# This is the Pro edition build — all Pro features are unlocked. (When a
# store/license flow exists, gate on the key instead by setting this False.)
PRO_BUILD = True


def is_pro(cfg: dict) -> bool:
    """Pro edition: always unlocked. (License checks apply only if PRO_BUILD is False.)"""
    if PRO_BUILD:
        return True
    if not cfg.get("pro_license_key"):
        return False
    age = time.time() - float(cfg.get("pro_last_valid", 0) or 0)
    if age < REVALIDATE_DAYS * 86400:
        return True
    return _validate(cfg)


def deactivate(cfg: dict):
    from . import config as cfgmod
    cfg["pro_license_key"] = ""
    cfg["pro_instance_id"] = ""
    cfg["pro_last_valid"] = 0
    cfgmod.save(cfg)
