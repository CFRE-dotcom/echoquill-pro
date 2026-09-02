"""DataForSEO client (Pro): Google search (SERP) + page-text reading.

Used by Research projects in Web/Both mode. Each research question becomes a
Google search; the top results are read for their text via the On-Page
content-parsing endpoint. Auth is HTTP Basic (login + password); the password
is stored in Windows Credential Manager (never logged).
"""

import base64
import json

SERP_URL = "https://api.dataforseo.com/v3/serp/google/organic/live/advanced"
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
    """Recursively pull every 'text' string out of a content node."""
    if isinstance(node, dict):
        t = node.get("text")
        if isinstance(t, str) and t.strip():
            out.append(t.strip())
        for v in node.values():
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


def read_page(cfg, url, log=lambda s: None):
    """Fetch a page's readable text. Returns (title, text) or ('', '')."""
    payload = [{"url": url}]
    ok, data = _post(cfg, PARSE_URL, payload, timeout=90)
    if not ok:
        log("  " + str(data))
        return ("", "")
    title, parts = "", []
    try:
        for task in data.get("tasks") or []:
            for res in task.get("result") or []:
                for it in res.get("items") or []:
                    pc = it.get("page_content") or {}
                    meta = it.get("meta") or {}
                    if not title:
                        title = (meta.get("title") or "").strip()
                    _collect_text(pc.get("primary_content"), parts)
                    _collect_text(pc.get("secondary_content"), parts)
    except Exception as e:
        log(f"  parse error: {str(e)[:80]}")
    # de-dupe consecutive blocks, join
    seen, clean = set(), []
    for p in parts:
        k = p[:120]
        if k in seen:
            continue
        seen.add(k)
        clean.append(p)
    return (title, "\n\n".join(clean))


def test(cfg):
    """Quick auth/connectivity check. Returns (ok, message)."""
    ok, data = _post(cfg, SERP_URL,
                     [{"keyword": "test", "location_name": "United States",
                       "language_name": "English", "depth": 1}])
    if ok:
        return (True, "Works ✓")
    return (False, str(data)[:160])
