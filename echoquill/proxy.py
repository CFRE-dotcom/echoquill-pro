"""DataImpulse residential/mobile proxy for yt-dlp.

Per DataImpulse docs:
  - Country targeting is a username parameter:  login__cr.us   (valid, free).
  - Sticky IPs are PORT-based (ports 10000-20000), NOT a username 'sessid'.
So we pin an IP for a job by using a random sticky port (verify + download share
that port = same IP), and rotate by picking a new port. verify-before-execute:
check the exit IP THROUGH the chosen port before any real download uses it.
"""

import uuid
import random

GATEWAY_DEFAULT = "gw.dataimpulse.com:824"   # rotating SOCKS5 port
STICKY_MIN, STICKY_MAX = 10000, 20000

# the sticky port currently pinned for this job; proxy_url()/_apply_proxy use it
# so the verified IP is the one that actually downloads. Runs are serialized.
_ACTIVE_PORT = None


def set_active_port(port):
    global _ACTIVE_PORT
    _ACTIVE_PORT = port


def clear_active_port():
    global _ACTIVE_PORT
    _ACTIVE_PORT = None
    clear_cache()


# back-compat aliases (older call sites)
def clear_active_sessid():
    clear_active_port()


def clear_cache():
    """Remove yt-dlp's on-disk cache so a new IP doesn't reuse old state."""
    try:
        import yt_dlp
        yt_dlp.YoutubeDL({"quiet": True, "no_warnings": True}).cache.remove()
    except Exception:
        pass


def build_username(cfg):
    """base__cr.<country>[;state.<state>][;type.mobile] - real DataImpulse
    params. Country is free 'default targeting'; state costs 2x ('target
    filter'). NO 'sessid' - that's not a DataImpulse param and breaks parsing."""
    base = ((cfg or {}).get("di_base_username", "") or "").strip()
    if not base:
        return ""
    u = f"{base}__cr.{((cfg.get('di_country') or 'us')).strip().lower()}"
    state = (cfg.get("di_state", "") or "").strip().lower().replace(" ", "")
    if state:
        u += f";state.{state}"
    if cfg.get("di_mobile", False):
        u += ";type.mobile"
    return u


def _gw_host_port(cfg):
    gw = ((cfg.get("di_gateway") or GATEWAY_DEFAULT)).strip()
    if ":" in gw:
        h, p = gw.rsplit(":", 1)
        return h, p
    return gw, "824"


def _url(cfg, port=None):
    from urllib.parse import quote
    user = build_username(cfg)
    pw = ((cfg or {}).get("di_password", "") or "").strip()
    if not user or not pw:
        return ""
    host, rot_port = _gw_host_port(cfg)
    use_port = str(port) if port else rot_port
    return f"socks5://{quote(user, safe='')}:{quote(pw, safe='')}@{host}:{use_port}"


def proxy_url(cfg):
    """socks5 URL for yt-dlp, or '' when disabled/unconfigured. Uses the active
    sticky port so downloads exit through the already-verified IP."""
    if not (cfg or {}).get("di_enabled"):
        return ""
    return _url(cfg, _ACTIVE_PORT)


def _verify(cfg, port, timeout=30):
    """Check the exit IP THROUGH this exact sticky port. (ok, ip, geo, org, msg)."""
    url = _url(cfg, port)
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
    port = random.randint(STICKY_MIN, STICKY_MAX)
    ok, ip, geo, org, msg = _verify(cfg, port, timeout)
    if not ok:
        return (False, f"Proxy failed: {msg}")
    mode = "mobile" if cfg.get("di_mobile", False) else "residential"
    return (True, f"Works ✓  {ip}" + (f"  ·  {geo}" if geo else "")
            + f"  ·  {mode}" + (f"  ·  {org}" if org else ""))


def is_block(msg):
    """True if an error looks like a bot-block / rate-limit OR a dropped/unstable
    connection (SSL EOF, reset) - a fresh IP often gets past those. Genuine
    'not available'/'private'/format errors are NOT included (a new IP won't
    help)."""
    low = (msg or "").lower()
    return any(p in low for p in (
        "sign in to confirm", "not a bot", "confirm you", "--cookies",
        "http error 429", " 429", "too many requests", "rate limit",
        "captcha", "http error 403", "forbidden",
        "unexpected_eof", "eof occurred", "ssl", "connection reset",
        "connection aborted", "broken pipe", "tunnel connection failed",
        "read timed out", "connection timed out", "remote end closed"))


def acquire_verified(cfg, tries=3, log=lambda s: None, timeout=30):
    """Clear cache -> pick a fresh sticky port (a new IP) -> verify it; if it
    fails, rotate to a new port and verify again; repeat up to `tries`. On
    success PIN it (set active port) and return (port, ip). On total failure
    return (None, None) so the caller pauses + requeues. Verifies EVERY IP."""
    if not (cfg or {}).get("di_enabled"):
        return (None, None)
    tries = max(1, int(tries or 3))
    clear_active_port()
    mode = "mobile" if (cfg or {}).get("di_mobile", False) else "residential"
    for attempt in range(1, tries + 1):
        log(f"    [IP {attempt}/{tries}] clearing cache before firing a new IP…")
        clear_cache()
        port = random.randint(STICKY_MIN, STICKY_MAX)
        log(f"    [IP {attempt}/{tries}] firing a new {mode} IP…")
        log(f"    [IP {attempt}/{tries}] verifying exit IP…")
        ok, ip, geo, org, msg = _verify(cfg, port, timeout)
        if ok:
            set_active_port(port)
            log(f"    [IP {attempt}/{tries}] verified ✓  {ip}"
                f"{'  ·  ' + geo if geo else ''}  ·  {mode}"
                f"{'  ·  ' + org if org else ''}")
            return (port, ip)
        log(f"    [IP {attempt}/{tries}] NOT verified ({msg}) — starting over")
    log(f"    no live IP after {tries} tries — giving up this pass")
    clear_active_port()
    return (None, None)


def run_download(cfg, log=lambda s: None, download_fn=None, tries=None):
    """Run download_fn() through a VERIFIED proxy IP; on a block/SSL/EOF error
    rotate to a fresh IP and retry - up to di_verify_tries. Holds the IP on
    success (caller clears with clear_active_port() when fully done). Proxy off
    = just run download_fn() once. This is the same verify+rotate procedure the
    Channel watcher and Auto-batch use, shared so EVERY transcribe path behaves
    identically."""
    if not (cfg or {}).get("di_enabled"):
        return download_fn()
    tries = int(tries or (cfg or {}).get("di_verify_tries", 3) or 3)
    for attempt in range(1, tries + 1):
        sid, _ip = acquire_verified(cfg, tries=tries, log=log)
        if not sid:
            raise RuntimeError(f"proxy: no live IP after {tries} tries")
        try:
            return download_fn()
        except Exception as e:
            clear_active_port()
            if is_block(str(e)) and attempt < tries:
                log(f"    unstable IP/block - rotating to a new IP "
                    f"({attempt + 1}/{tries})")
                continue
            raise
    raise RuntimeError("proxy: exhausted retries")
