"""Channel watcher: watched-channel list, per-channel seen-ID dedup, a
persistent queue with auto-retry/backoff, and delete-with-all-data."""

import os
import json
import time
import uuid
import threading

_LOCK = threading.Lock()
_BUSY = False

# retry backoff after N failed attempts: 30m, 2h, 6h, then daily
BACKOFF = [0, 1800, 7200, 21600, 86400]


def _path():
    from .config import app_data_dir
    return os.path.join(str(app_data_dir()), "watcher.json")


def load():
    try:
        with open(_path(), encoding="utf-8") as f:
            d = json.load(f)
        if isinstance(d, dict):
            d.setdefault("channels", [])
            d.setdefault("queue", [])
            d.setdefault("new_ready", 0)
            return d
    except Exception:
        pass
    return {"channels": [], "queue": [], "new_ready": 0}


def save(d):
    try:
        with open(_path(), "w", encoding="utf-8") as f:
            json.dump(d, f, indent=2)
    except Exception:
        pass


def add_channel(ch):
    d = load()
    ch.setdefault("id", uuid.uuid4().hex[:8])
    ch.setdefault("seen", [])
    ch.setdefault("enabled", True)
    d["channels"].append(ch)
    save(d)
    return ch["id"]


def delete_channel(cid):
    """Remove a watch AND everything stored for it (seen list + queued items)."""
    d = load()
    d["channels"] = [c for c in d["channels"] if c.get("id") != cid]
    d["queue"] = [q for q in d["queue"] if q.get("channel_id") != cid]
    save(d)


def check_new(cfg):
    """Queue any new uploads from each enabled channel. Returns count queued."""
    from .auto_batch import fetch_channel
    from . import prompts as _pr
    d = load()
    added = 0
    for ch in d["channels"]:
        if not ch.get("enabled", True):
            continue
        seen = set(ch.get("seen", []))
        kinds = ch.get("kinds") or ["Videos"]
        kw = (ch.get("keyword", "") or "").strip().lower()
        limit = int(ch.get("count") or 15)
        for kind in kinds:
            try:
                fetch_n = min(limit * 8, 300) if kw else limit
                items = fetch_channel(ch["url"], kind, fetch_n, cfg)
            except Exception:
                continue
            if kw:
                items = [(u, t) for (u, t) in items if kw in (t or "").lower()]
            items = items[:limit]
            for (u, t) in items:
                if u in seen:
                    continue
                seen.add(u)
                d["queue"].append({
                    "channel_id": ch["id"], "url": u, "title": t,
                    "folder": ch.get("folder", ""),
                    "set_name": ch.get("set_name", ""),
                    "questions": (_pr.get_set(cfg, ch["set_name"])
                                  if ch.get("set_name") else []),
                    "save_video": ch.get("save_video", False),
                    "save_audio": ch.get("save_audio", False),
                    "save_desc": ch.get("save_desc", False),
                    "save_comments": ch.get("save_comments", False),
                    "transcript_mode": ch.get("transcript_mode", "Whisper"),
                    "status": "pending", "attempts": 0,
                    "last_error": "", "next_try": 0,
                })
                added += 1
        ch["seen"] = list(seen)
    save(d)
    return added


def process_pending(cfg, log=lambda s: None, cancel=lambda: False):
    """Process every due pending/failed item once. Returns count newly done."""
    global _BUSY
    if _BUSY:
        return 0
    with _LOCK:
        _BUSY = True
    done = 0
    try:
        from . import pipeline
        d = load()
        for item in d["queue"]:
            if cancel():
                break
            if item.get("status") in ("done", "unavailable"):
                continue
            if item.get("next_try", 0) > time.time():
                continue
            log(f"→ {item.get('title') or item['url']}")
            status, msg = pipeline.process_video(cfg, item, log, cancel)
            item["attempts"] = item.get("attempts", 0) + 1
            if status == "done":
                item["status"] = "done"
                item["last_error"] = ""
                done += 1
                log("    done ✓")
            elif status == "unavailable":
                item["status"] = "unavailable"
                item["last_error"] = msg
                log("    unavailable — skipping")
            else:
                item["status"] = "failed"
                item["last_error"] = msg
                mins = int((cfg or {}).get("watch_retry_minutes", 30) or 30)
                item["next_try"] = time.time() + max(1, mins) * 60
                log(f"    failed (will retry): {msg[:80]}")
            save(d)
        if done:
            fresh = load()
            fresh["new_ready"] = fresh.get("new_ready", 0) + done
            save(fresh)
    finally:
        _BUSY = False
    return done


def run_once(cfg, log=lambda s: None, cancel=lambda: False):
    check_new(cfg)
    return process_pending(cfg, log, cancel)


def clear_new_ready():
    d = load()
    d["new_ready"] = 0
    save(d)


def counts():
    d = load()
    q = d["queue"]
    return {
        "channels": len(d["channels"]),
        "done": sum(1 for x in q if x.get("status") == "done"),
        "pending": sum(1 for x in q if x.get("status") == "pending"),
        "failed": sum(1 for x in q if x.get("status") == "failed"),
        "unavailable": sum(1 for x in q if x.get("status") == "unavailable"),
        "new_ready": d.get("new_ready", 0),
    }
