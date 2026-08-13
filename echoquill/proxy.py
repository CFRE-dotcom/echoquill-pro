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
    clear_cache()          # wipe session state AFTER releasing an IP


def clear_cache():
    """Remove yt-dlp's on-disk cache (nsig/player tokens, etc.) so a new IP does
    NOT reuse state minted under the previous IP - the no-browser equivalent of
    a fresh context. Called after releasing an IP and before firing a new one."""
    try:
        import yt_dlp
        yt_dlp.YoutubeDL({"quiet": True, "no_warnings": True}).cache.remove()
    except Exception:
        pass


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
    if cfg.get("di_mobile", False):
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
        return (False, "", "", "", "enter your DataImpulse username and password first")
    import yt_dlp
    ydl = yt_dlp.YoutubeDL({"proxy": url, "quiet": True, "no_warnings": True,
                            "socket_timeout": timeout})
    try:
        ip = ydl.urlopen("https://api.ipify.org").read().decode(
            "utf-8", "ignore").strip()
    except Exception as e:
        return (False, "", "", "", f"{str(e)[:110]}")
    if not ip or " " in ip or len(ip) > 45:
        return (False, "", "", "", "no exit IP returned")
    geo = org = ""
    try:
        import json
        j = json.loads(ydl.urlopen("https://ipinfo.io/json").read().decode(
            "utf-8", "ignore"))
        geo = ", ".join(x for x in (j.get("city"), j.get("region"),
                                    j.get("country")) if x)
        org = (j.get("org") or "").strip()
    except Exception:
        pass
    return (True, ip, geo, org, "ok")


def test(cfg, timeout=45):
    """Manual 'Test' button: fire ONE fresh IP and verify it."""
    if not (((cfg or {}).get("di_base_username")) and (cfg or {}).get("di_password")):
        return (False, "Enter your DataImpulse username and password first.")
    ok, ip, geo, org, msg = _verify(cfg, uuid.uuid4().hex[:12], timeout)
    if not ok:
        return (False, f"Proxy failed: {msg}")
    mode = "mobile" if cfg.get("di_mobile", False) else "residential"
    return (True, f"Works ✓  {ip}" + (f"  ·  {geo}" if geo else "")
            + f"  ·  {mode}" + (f"  ·  {org}" if org else ""))


def is_block(msg):
    """True if an error looks like a YouTube bot-block / rate-limit that a
    different IP might get past (vs. a permanent 'video unavailable')."""
    low = (msg or "").lower()
    return any(p in low for p in (
        "sign in to confirm", "not a bot", "confirm you", "--cookies",
        "http error 429", " 429", "too many requests", "rate limit",
        "captcha", "http error 403", "forbidden"))


def acquire_verified(cfg, tries=3, log=lambda s: None, timeout=30):
    """Fire a fresh IP and verify it; if it fails, rotate to a NEW IP and verify
    again; repeat up to `tries`. On success PIN it and return (sessid, ip). On
    total failure return (None, None) so the caller pauses + requeues instead of
    downloading on a bad IP. Verifies EVERY new IP before use."""
    if not (cfg or {}).get("di_enabled"):
        return (None, None)
    tries = max(1, int(tries or 3))
    clear_active_sessid()
    mode = "mobile" if (cfg or {}).get("di_mobile", False) else "residential"
    for attempt in range(1, tries + 1):
        log(f"    [IP {attempt}/{tries}] clearing cache before firing a new IP…")
        clear_cache()          # fresh state BEFORE firing a new IP
        sid = uuid.uuid4().hex[:12]
        log(f"    [IP {attempt}/{tries}] firing a new {mode} IP…")
        log(f"    [IP {attempt}/{tries}] verifying exit IP…")
        ok, ip, geo, org, msg = _verify(cfg, sid, timeout)
        if ok:
            set_active_sessid(sid)
            log(f"    [IP {attempt}/{tries}] verified ✓  {ip}"
                f"{'  ·  ' + geo if geo else ''}  ·  {mode}"
                f"{'  ·  ' + org if org else ''}")
            return (sid, ip)
        log(f"    [IP {attempt}/{tries}] NOT verified ({msg}) — starting over")
    log(f"    no live IP after {tries} tries — giving up this pass")
    clear_active_sessid()
    return (None, None)
