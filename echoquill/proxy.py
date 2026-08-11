"""DataImpulse residential/mobile proxy for yt-dlp.

Follows the verify-before-execute rule: build a geo-specific username, then
check the exit IP (and geo) THROUGH the proxy before any real download uses it.
The password lives in Windows Credential Manager (config._SECRET_KEYS).
"""

GATEWAY_DEFAULT = "gw.dataimpulse.com:824"


def build_username(cfg):
    """cr.<country>;state.<state>;type.mobile  — per the DataImpulse skill."""
    base = ((cfg or {}).get("di_base_username", "") or "").strip()
    if not base:
        return ""
    u = f"{base}__cr.{((cfg.get('di_country') or 'us')).strip().lower()}"
    state = (cfg.get("di_state", "") or "").strip().lower().replace(" ", "")
    if state:
        u += f";state.{state}"
    if cfg.get("di_mobile", True):
        u += ";type.mobile"
    return u


def _url(cfg):
    from urllib.parse import quote
    user = build_username(cfg)
    pw = ((cfg or {}).get("di_password", "") or "").strip()
    if not user or not pw:
        return ""
    gw = ((cfg.get("di_gateway") or GATEWAY_DEFAULT)).strip()
    return f"socks5://{quote(user, safe='')}:{quote(pw, safe='')}@{gw}"


def proxy_url(cfg):
    """The socks5 URL to hand yt-dlp, or '' when disabled/unconfigured."""
    if not (cfg or {}).get("di_enabled"):
        return ""
    return _url(cfg)


def test(cfg, timeout=45):
    """Verify the proxy through yt-dlp's own networking (same path downloads
    use). Returns (ok, message)."""
    url = _url(cfg)
    if not url:
        return (False, "Enter your DataImpulse username and password first.")
    import yt_dlp
    ydl = yt_dlp.YoutubeDL({"proxy": url, "quiet": True, "no_warnings": True,
                            "socket_timeout": timeout})
    try:
        ip = ydl.urlopen("https://api.ipify.org").read().decode(
            "utf-8", "ignore").strip()
    except Exception as e:
        return (False, f"Proxy failed: {str(e)[:130]}")
    if not ip or " " in ip or len(ip) > 45:
        return (False, "No exit IP returned — check the credentials/geography.")
    geo = ""
    try:
        import json
        j = json.loads(ydl.urlopen("https://ipinfo.io/json").read().decode(
            "utf-8", "ignore"))
        geo = ", ".join(x for x in (j.get("city"), j.get("region"),
                                    j.get("country")) if x)
    except Exception:
        pass
    mode = "mobile" if cfg.get("di_mobile", True) else "residential"
    return (True, f"Works ✓  {ip}" + (f"  ·  {geo}" if geo else "")
            + f"  ·  {mode}")
