"""DataForSEO client (Pro): Google search (SERP) + page-text reading.

Used by Research projects in Web/Both mode. Each research question becomes a
Google search; the top results are read for their text via the On-Page
content-parsing endpoint. Auth is HTTP Basic (login + password); the password
is stored in Windows Credential Manager (never logged).
"""

import base64
import json

SERP_URL = "https://api.dataforseo.com/v3/serp/google/organic/live/advanced"

# Domains we can't get useful text from (login-walled or not article text) —
# skip them entirely so we don't waste time or credits. Reddit + Quora are NOT
# here: Reddit has a .json reader; Quora works with JS rendering.
SKIP_DOMAINS = ("facebook.com", "fb.com", "instagram.com", "linkedin.com",
                "twitter.com", "x.com", "tiktok.com", "pinterest.com",
                "youtube.com", "youtu.be", "m.facebook.com")


def unscrapable(url):
    """True if this URL is a known login-walled / thin-text page to skip."""
    low = (url or "").lower()
    return any(("//" + d in low) or ("." + d in low) or ("/" + d in low)
               for d in SKIP_DOMAINS)
PARSE_URL = "https://api.dataforseo.com/v3/on_page/content_parsing/live"


def _auth(cfg):
    login = (cfg.get("dataforseo_login", "") or "").strip()
    pw = (cfg.get("dataforseo_password", "") or "").strip()
    if not login or not pw:
        return ""
    return base64.b64encode(f"{login}:{pw}".encode()).decode()


def configured(cfg) -> bool:
    return bool(_auth(cfg))


def _post(cfg, url, payload, timeout=60):
    """POST to DataForSEO. Returns (ok, data_or_error)."""
    import requests
    tok = _auth(cfg)
    if not tok:
        return (False, "DataForSEO login/password not set (Settings).")
    try:
        r = requests.post(url, headers={"Authorization": "Basic " + tok,
                                        "Content-Type": "application/json"},
                          data=json.dumps(payload), timeout=timeout)
        r.raise_for_status()
        data = r.json()
    except Exception as e:
        return (False, f"DataForSEO request failed: {str(e)[:120]}")
    if str(data.get("status_code")) != "20000":
        return (False, "DataForSEO: " + str(data.get("status_message"))[:120])
    return (True, data)


def _collect_text(node, out):
    """Recursively pull every 'text' string out of a content node, skipping the
    'urls' link-metadata arrays."""
    if isinstance(node, dict):
        t = node.get("text")
        if isinstance(t, str) and t.strip():
            out.append(t.strip())
        for key, v in node.items():
            if key == "urls":          # link anchors/metadata, not body text
                continue
            _collect_text(v, out)
    elif isinstance(node, list):
        for v in node:
            _collect_text(v, out)


def search(cfg, query, n=10, location="United States", language="English",
           log=lambda s: None):
    """Google organic results for a query. Returns [(url, title), ...]."""
    payload = [{"keyword": query, "location_name": location,
                "language_name": language, "depth": max(int(n), 1)}]
    ok, data = _post(cfg, SERP_URL, payload)
    if not ok:
        log("  " + str(data))
        return []
    out = []
    try:
        for task in data.get("tasks") or []:
            for res in task.get("result") or []:
                for it in res.get("items") or []:
                    if it.get("type") != "organic":
                        continue
                    u = it.get("url")
                    t = (it.get("title") or "").strip()
                    if u:
                        out.append((u, t))
                    if len(out) >= int(n):
                        break
    except Exception as e:
        log(f"  SERP parse error: {str(e)[:80]}")
    return out[:int(n)]


def _content_parse(cfg, url, render=False, log=lambda s: None):
    """Parse one page via DataForSEO. render=True asks for JavaScript rendering
    (catches SPAs like Quora / new Reddit UI; costs more per page)."""
    item = {"url": url}
    if render:
        item["enable_javascript"] = True
    ok, data = _post(cfg, PARSE_URL, [item], timeout=120)
    if not ok:
        log("  " + str(data))
        return ("", "")
    title, parts = "", []
    try:
        for task in data.get("tasks") or []:
            for res in task.get("result") or []:
                for it in res.get("items") or []:
                    meta = it.get("meta") or {}
                    if not title:
                        title = (meta.get("title") or "").strip()
                    _collect_text(it.get("page_content"), parts)
    except Exception as e:
        log(f"  parse error: {str(e)[:80]}")
    return (title, _dedupe("\n\n".join(parts)))


def _dedupe(text):
    seen, clean = set(), []
    for p in (text or "").split("\n\n"):
        p = p.strip()
        if not p:
            continue
        k = p[:120]
        if k in seen:
            continue
        seen.add(k)
        clean.append(p)
    return "\n\n".join(clean)


def _read_reddit(cfg, url, log=lambda s: None):
    """Read a Reddit thread deep via its public .json endpoint — post body plus
    the full nested comment tree. No login. Routed through the proxy if on."""
    import requests
    base = url.split("#")[0].split("?")[0].rstrip("/")
    jurl = base + "/.json?limit=500&raw_json=1"
    headers = {
        "User-Agent": ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                       "AppleWebKit/537.36 (KHTML, like Gecko) "
                       "Chrome/125.0.0.0 Safari/537.36"),
        "Accept": "application/json, text/plain, */*",
        "Accept-Language": "en-US,en;q=0.9",
    }
    proxies = None
    try:
        from . import proxy as _px
        pu = _px.proxy_url(cfg)
        if pu:
            proxies = {"http": pu, "https": pu}
    except Exception:
        proxies = None
    data, last = None, ""
    for use in ([proxies, None] if proxies else [None]):
        try:
            r = requests.get(jurl, headers=headers, proxies=use, timeout=45)
            r.raise_for_status()
            data = r.json()
            break
        except Exception as e:
            last = str(e)
    if not isinstance(data, list) or len(data) < 2:
        if last:
            log("  reddit fetch failed: " + last[:80])
        return ("", "")
    title, parts = "", []
    try:
        post = data[0]["data"]["children"][0]["data"]
        title = post.get("title", "")
        if post.get("selftext"):
            parts.append(post["selftext"])

        def walk(children):
            for c in children or []:
                d = c.get("data") or {}
                if d.get("body"):
                    parts.append(d["body"])
                reps = d.get("replies")
                if isinstance(reps, dict):
                    walk(((reps.get("data") or {}).get("children")) or [])
        walk((data[1].get("data") or {}).get("children") or [])
    except Exception as e:
        log("  reddit parse: " + str(e)[:60])
    return (title, "\n\n".join(p for p in parts if p and p.strip()))


def read_page(cfg, url, log=lambda s: None):
    """Fetch a page's readable text, with fallbacks:
    Reddit -> its .json comment tree; empty parse -> retry with JS rendering.
    Returns (title, text) or ('', '')."""
    low = (url or "").lower()
    if "reddit.com" in low:
        t, x = _read_reddit(cfg, url, log)
        if x.strip():
            return (t, x)
    title, text = _content_parse(cfg, url, render=False, log=log)
    if not text.strip():
        log("    little/no text — retrying with JS rendering")
        t2, x2 = _content_parse(cfg, url, render=True, log=log)
        if x2.strip():
            return (t2 or title, x2)
    return (title, text)


def test(cfg):
    """Quick auth/connectivity check. Returns (ok, message)."""
    ok, data = _post(cfg, SERP_URL,
                     [{"keyword": "test", "location_name": "United States",
                       "language_name": "English", "depth": 1}])
    if ok:
        return (True, "Works ✓")
    return (False, str(data)[:160])
