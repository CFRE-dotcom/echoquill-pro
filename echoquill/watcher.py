"""Channel watcher: watched-channel list, per-channel seen-ID dedup, a
persistent queue with auto-retry/backoff, and delete-with-all-data."""

import os
import json
import time
import uuid
import threading

_LOCK = threading.Lock()
_BUSY = False

# live progress the watcher window polls
ACTIVITY = {"phase": "idle", "i": 0, "n": 0, "title": "", "wait_until": 0}


def get_activity():
    return dict(ACTIVITY)


def _set_activity(**kw):
    ACTIVITY.update(kw)


# any open watcher window registers its log here so it shows output from EVERY
# run, including the background timer (not just its own "Check now").
_LOG_LISTENERS = []


def add_log_listener(fn):
    if fn not in _LOG_LISTENERS:
        _LOG_LISTENERS.append(fn)


def remove_log_listener(fn):
    try:
        _LOG_LISTENERS.remove(fn)
    except ValueError:
        pass


def logfile_path():
    from .config import app_data_dir
    import os
    return os.path.join(str(app_data_dir()), "watcher.log")


def _to_logfile(msg):
    try:
        import os
        import datetime
        path = logfile_path()
        if os.path.exists(path) and os.path.getsize(path) > 2_000_000:
            try:
                os.replace(path, path + ".old")
            except Exception:
                pass
        with open(path, "a", encoding="utf-8") as f:
            f.write(f"{datetime.datetime.now():%Y-%m-%d %H:%M:%S}  {msg}\n")
    except Exception:
        pass


def _emit(log, msg):
    _to_logfile(msg)
    try:
        log(msg)
    except Exception:
        pass
    for fn in list(_LOG_LISTENERS):
        try:
            fn(msg)
        except Exception:
            pass

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


def update_channel(cid, fields):
    """Edit an existing watch in place (keeps id, seen list, enabled)."""
    d = load()
    for ch in d["channels"]:
        if ch.get("id") == cid:
            keep_id = ch.get("id")
            seen = ch.get("seen", [])
            en = ch.get("enabled", True)
            ch.clear()
            ch.update(fields)
            ch["id"] = keep_id
            ch["seen"] = seen
            ch["enabled"] = en
            break
    save(d)


def stats(cid):
    """Per-channel counts: done total, last finished time, last-7-days done."""
    d = load()
    now = time.time()
    total = last = last7 = pending = 0
    for q in d["queue"]:
        if q.get("channel_id") != cid:
            continue
        if q.get("status") == "done":
            total += 1
            da = q.get("done_at", 0) or 0
            if da > last:
                last = da
            if da and now - da <= 7 * 86400:
                last7 += 1
        elif q.get("status") not in ("unavailable",):
            pending += 1
    return {"done": total, "last": last, "last7": last7, "pending": pending}


def delete_channel(cid):
    """Remove a watch AND everything stored for it (seen list + queued items)."""
    d = load()
    d["channels"] = [c for c in d["channels"] if c.get("id") != cid]
    d["queue"] = [q for q in d["queue"] if q.get("channel_id") != cid]
    save(d)


def is_expired(ch):
    """True if a search source has passed its lifespan."""
    days = int(ch.get("lifespan_days", 0) or 0)
    if days <= 0:
        return False
    return (ch.get("created_at", 0) or 0) + days * 86400 < time.time()


def expiry_info(ch):
    """('none'|'expired'|seconds_left) for the GUI."""
    days = int(ch.get("lifespan_days", 0) or 0)
    if days <= 0:
        return "none"
    left = (ch.get("created_at", 0) or 0) + days * 86400 - time.time()
    return "expired" if left <= 0 else int(left)


def _queue_item(cfg, ch, u, t):
    from . import prompts as _pr
    return {
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
    }


def _scan_sources(cfg, d, log):
    """The actual per-source scan (channels + searches). Returns count queued."""
    from .auto_batch import fetch_channel
    added = 0
    for ch in d["channels"]:
        if not ch.get("enabled", True):
            continue
        if ch.get("kind") == "search" and is_expired(ch):
            ch["enabled"] = False
            ch["expired"] = True
            _emit(log, f"Search \"{ch.get('query','')}\" reached its lifespan "
                  "— retired.")
            continue
        seen = set(ch.get("seen", []))
        limit = int(ch.get("count") or 15)
        before = added

        if ch.get("kind") == "search":
            from .auto_batch import fetch_search_filtered
            q = ch.get("query", "")
            _emit(log, f"Searching \"{q}\"…")
            try:
                items = fetch_search_filtered(
                    q, cfg, types=ch.get("types") or ["Video"],
                    duration=ch.get("duration", "Any"),
                    upload_days=int(ch.get("upload_days", 0) or 0),
                    sort=ch.get("sort", "Relevance"), n=limit)
            except Exception as e:
                _emit(log, f"  search failed: {str(e)[:70]}")
                continue
            for (u, t) in items:
                if u in seen:
                    continue
                seen.add(u)
                d["queue"].append(_queue_item(cfg, ch, u, t))
                added += 1
            ch["seen"] = list(seen)
            got = added - before
            if not items:
                _emit(log, "  0 results — search returned nothing (blocked, or "
                      "nothing matched your Type / Duration / Upload-window).")
            else:
                _emit(log, f"  found {len(items)} result(s), {got} new queued.")
            continue

        kinds = ch.get("kinds") or ["Videos"]
        _emit(log, f"Scanning {ch.get('url','')} ({', '.join(kinds)})…")
        kw = (ch.get("keyword", "") or "").strip().lower()
        found = 0
        for kind in kinds:
            try:
                fetch_n = min(limit * 8, 300) if kw else limit
                items = fetch_channel(ch["url"], kind, fetch_n, cfg)
            except Exception:
                continue
            if kw:
                items = [(u, t) for (u, t) in items if kw in (t or "").lower()]
            items = items[:limit]
            found += len(items)
            for (u, t) in items:
                if u in seen:
                    continue
                seen.add(u)
                d["queue"].append(_queue_item(cfg, ch, u, t))
                added += 1
        ch["seen"] = list(seen)
        got = added - before
        if not found:
            _emit(log, "  0 results returned (possibly blocked).")
        else:
            _emit(log, f"  {got} new video(s) queued." if got
                  else "  nothing new (all already seen).")
    return added


def check_new(cfg, log=lambda s: None):
    """Scan every enabled source. When the proxy is on, verify a live IP FIRST
    so scans aren't run on a blocked IP. Returns count queued."""
    from . import proxy
    d = load()
    proxy_on = bool(cfg.get("di_enabled"))
    if proxy_on:
        _emit(log, "Verifying a proxy IP for this scan…")
        sid, _ip = proxy.acquire_verified(
            cfg, tries=int(cfg.get("di_verify_tries", 3) or 3),
            log=lambda m: _emit(log, m))
        if not sid:
            _emit(log, "  proxy: no live IP after tries — skipping this scan.")
            return 0
    try:
        added = _scan_sources(cfg, d, log)
        save(d)
    finally:
        if proxy_on:
            proxy.clear_active_sessid()
    return added


def process_pending(cfg, log=lambda s: None, cancel=lambda: False):
    """Process every due pending/failed item once. Returns count newly done.
    Serialized by run_once (do not call directly from two threads)."""
    done = 0
    try:
        from . import pipeline
        gap = int((cfg or {}).get("watch_gap_seconds", 600) or 0)
        per_cycle = int((cfg or {}).get("watch_per_cycle", 5) or 0)
        d = load()
        processed = 0
        _now = time.time()
        due = 0
        for _it in d["queue"]:
            if _it.get("status") in ("done", "unavailable"):
                continue
            if _it.get("next_try", 0) > _now:
                continue
            due += 1
        n_total = min(due, per_cycle) if per_cycle else due
        for item in d["queue"]:
            if cancel():
                break
            if item.get("status") in ("done", "unavailable"):
                continue
            if item.get("next_try", 0) > time.time():
                continue
            if per_cycle and processed >= per_cycle:
                break
            if processed > 0 and gap > 0:
                _set_activity(phase="waiting", i=processed + 1, n=n_total,
                              title=item.get("title") or item.get("url", ""),
                              wait_until=time.time() + gap)
                _emit(log, f"    waiting {gap}s before next video…")
                for _ in range(gap):
                    if cancel():
                        break
                    time.sleep(1)
                if cancel():
                    break
            processed += 1
            title = item.get("title") or item.get("url", "")
            _set_activity(phase="Starting", i=processed, n=n_total,
                          title=title, wait_until=0)
            _emit(log, f"→ [{processed}/{n_total}] {title}")

            def _prog(ph, _t=title):
                _set_activity(phase=ph)
                _emit(log, f"    {ph}…")

            status, msg = pipeline.process_video(
                cfg, item, lambda m: _emit(log, m), cancel, progress=_prog)
            item["attempts"] = item.get("attempts", 0) + 1
            if status == "done":
                item["status"] = "done"
                item["last_error"] = ""
                item["done_at"] = time.time()
                done += 1
                _emit(log, "    done ✓")
            elif status == "unavailable":
                item["status"] = "unavailable"
                item["last_error"] = msg
                _emit(log, "    unavailable — skipping")
            else:
                item["status"] = "failed"
                item["last_error"] = msg
                mins = int((cfg or {}).get("watch_retry_minutes", 30) or 30)
                item["next_try"] = time.time() + max(1, mins) * 60
                _emit(log, f"    failed (will retry): {msg[:80]}")
            save(d)
        if done:
            fresh = load()
            fresh["new_ready"] = fresh.get("new_ready", 0) + done
            save(fresh)
    finally:
        _set_activity(phase="idle", i=0, n=0, title="", wait_until=0)
    return done


def run_once(cfg, log=lambda s: None, cancel=lambda: False):
    """Scan + process as ONE serialized cycle. If a cycle is already running
    (e.g. the timer fires during a manual Check now), this one is skipped so two
    scans never race on the queue file or the shared proxy IP."""
    global _BUSY
    with _LOCK:
        if _BUSY:
            return 0
        _BUSY = True
    try:
        check_new(cfg, log)
        return process_pending(cfg, log, cancel)
    finally:
        with _LOCK:
            _BUSY = False


def clear_queue():
    """Wipe every queued item (keeps channels + their seen lists)."""
    d = load()
    d["queue"] = []
    d["new_ready"] = 0
    save(d)


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
