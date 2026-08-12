"""DataImpulse residential/mobile proxy for yt-dlp.

verify-before-execute: pin an exit IP with a sessid, verify that exact IP works
THROUGH the proxy, and only then let a real download use the SAME IP. Rotate by
choosing a new sessid; verify EVERY new IP before use; after N failed tries give
up so the caller can pause + requeue (never download on an unverified IP).
The password lives in Windows Credential Manager (config._SECRET_KEYS).
"""

import uuid

GATEWAY_DEFAULT = "gw.dataimpulse.com:824"

# sessid currently pinned for this run; proxy_url()/_apply_proxy use it so the
# verified IP is the one that actually downloads. Runs are single-threaded.
_ACTIVE_SESSID = None


def set_active_sessid(sid):
    global _ACTIVE_SESSID
    _ACTIVE_SESSID = sid


def clear_active_sessid():
    global _ACTIVE_SESSID
    _ACTIVE_SESSID = None


def build_username(cfg, sessid=None):
    """cr.<country>;state.<state>;type.mobile[;sessid.<id>] - per the skill.
    The sessid pins the exit IP (same sessid = same IP; new sessid = new IP)."""
    base = ((cfg or {}).get("di_base_username", "") or "").strip()
    if not base:
        return ""
    u = f"{base}__cr.{((cfg.get('di_country') or 'us')).strip().lower()}"
    state = (cfg.get("di_state", "") or "").strip().lower().replace(" ", "")
    if state:
        u += f";state.{state}"
    if cfg.get("di_mobile", True):
        u += ";type.mobile"
    if sessid:
        u += f";sessid.{sessid}"
    return u


def _url(cfg, sessid=None):
    from urllib.parse import quote
    user = build_username(cfg, sessid)
    pw = ((cfg or {}).get("di_password", "") or "").strip()
    if not user or not pw:
        return ""
    gw = ((cfg.get("di_gateway") or GATEWAY_DEFAULT)).strip()
    return f"socks5://{quote(user, safe='')}:{quote(pw, safe='')}@{gw}"


def proxy_url(cfg):
    """socks5 URL for yt-dlp, or '' when disabled/unconfigured. Uses the active
    pinned sessid so downloads exit through the already-verified IP."""
    if not (cfg or {}).get("di_enabled"):
        return ""
    return _url(cfg, _ACTIVE_SESSID)


def _verify(cfg, sessid, timeout=30):
    """Check the exit IP THROUGH this exact sessid. Returns (ok, ip, geo, msg).
    Rejects on auth/timeout/empty/block per the skill's good-IP criteria."""
    url = _url(cfg, sessid)
    if not url:
        return (False, "", "", "enter your DataImpulse username and password first")
    import yt_dlp
    ydl = yt_dlp.YoutubeDL({"proxy": url, "quiet": True, "no_warnings": True,
                            "socket_timeout": timeout})
    try:
        ip = ydl.urlopen("https://api.ipify.org").read().decode(
            "utf-8", "ignore").strip()
    except Exception as e:
        return (False, "", "", f"{str(e)[:110]}")
    if not ip or " " in ip or len(ip) > 45:
        return (False, "", "", "no exit IP returned")
    geo = ""
    try:
        import json
        j = json.loads(ydl.urlopen("https://ipinfo.io/json").read().decode(
            "utf-8", "ignore"))
        geo = ", ".join(x for x in (j.get("city"), j.get("region"),
                                    j.get("country")) if x)
    except Exception:
        pass
    return (True, ip, geo, "ok")


def test(cfg, timeout=45):
    """Manual 'Test' button: fire ONE fresh IP and verify it."""
    if not (((cfg or {}).get("di_base_username")) and (cfg or {}).get("di_password")):
        return (False, "Enter your DataImpulse username and password first.")
    ok, ip, geo, msg = _verify(cfg, uuid.uuid4().hex[:12], timeout)
    if not ok:
        return (False, f"Proxy failed: {msg}")
    mode = "mobile" if cfg.get("di_mobile", True) else "residential"
    return (True, f"Works ✓  {ip}" + (f"  ·  {geo}" if geo else "")
            + f"  ·  {mode}")


def acquire_verified(cfg, tries=3, log=lambda s: None, timeout=30):
    """Fire a fresh IP and verify it; if it fails, rotate to a NEW IP and verify
    again; repeat up to `tries`. On success PIN it and return (sessid, ip). On
    total failure return (None, None) so the caller pauses + requeues instead of
    downloading on a bad IP. Verifies EVERY new IP before use."""
    if not (cfg or {}).get("di_enabled"):
        return (None, None)
    tries = max(1, int(tries or 3))
    clear_active_sessid()
    for attempt in range(1, tries + 1):
        sid = uuid.uuid4().hex[:12]
        ok, ip, geo, msg = _verify(cfg, sid, timeout)
        if ok:
            set_active_sessid(sid)
            log(f"    proxy IP verified: {ip}"
                f"{'  ·  ' + geo if geo else ''}  (attempt {attempt})")
            return (sid, ip)
        log(f"    proxy attempt {attempt}/{tries} failed ({msg}) - rotating IP")
    clear_active_sessid()
    return (None, None)
