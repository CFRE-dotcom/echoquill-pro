"""Channel watcher: watched-channel list, per-channel seen-ID dedup, a
persistent queue with auto-retry/backoff, and delete-with-all-data."""

import os
import json
import time
import uuid
import threading

_LOCK = threading.Lock()
_BUSY = False
# serializes read-modify-write of watcher.json so a long-running scan/process
# can't overwrite edits (add/delete/pause) made from the UI meanwhile.
_STORE_LOCK = threading.RLock()


def _mutate(fn):
    """Atomic read-modify-write: reload fresh, apply fn(d), save."""
    with _STORE_LOCK:
        d = load()
        fn(d)
        save(d)


def _update_item(cid, url, updates):
    """Atomically update one queue item's fields (matched by source+url)."""
    def fn(d):
        for q in d["queue"]:
            if q.get("channel_id") == cid and q.get("url") == url:
                q.update(updates)
                return
    _mutate(fn)

# live progress the watcher window polls
ACTIVITY = {"phase": "idle", "i": 0, "n": 0, "title": "", "wait_until": 0, "folder": ""}


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


_LOGFILE = None


def logfile_path():
    """watcher.log lives in your visible EchoQuill output folder (not the hidden
    AppData dir), so it's easy to find and open."""
    global _LOGFILE
    if _LOGFILE:
        return _LOGFILE
    import os
    try:
        from .config import load as _cfgload
        from .media_gui import base_dir
        _LOGFILE = os.path.join(base_dir(_cfgload()), "watcher.log")
    except Exception:
        from .config import app_data_dir
        _LOGFILE = os.path.join(str(app_data_dir()), "watcher.log")
    return _LOGFILE


def _cleanup_old_log():
    """Remove the old AppData watcher.log once (moved to the output folder)."""
    try:
        import os
        from .config import app_data_dir
        old = os.path.join(str(app_data_dir()), "watcher.log")
        if os.path.abspath(old) != os.path.abspath(logfile_path()) \
                and os.path.exists(old):
            os.remove(old)
    except Exception:
        pass


def _to_logfile(msg):
    try:
        import os
        import datetime
        if not globals().get("_LOG_MIGRATED"):
            _cleanup_old_log()
            globals()["_LOG_MIGRATED"] = True
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
    ch.setdefault("id", uuid.uuid4().hex[:8])
    ch.setdefault("seen", [])
    ch.setdefault("enabled", True)
    _mutate(lambda d: d["channels"].append(ch))
    return ch["id"]


def update_channel(cid, fields):
    """Edit an existing watch in place (keeps id + seen list). If `fields`
    includes 'enabled', it wins (lets an edit un-retire a source); otherwise
    the previous enabled state is kept."""
    def fn(d):
        for ch in d["channels"]:
            if ch.get("id") == cid:
                keep_id = ch.get("id")
                seen = ch.get("seen", [])
                en = ch.get("enabled", True)
                ch.clear()
                ch.update(fields)
                ch["id"] = keep_id
                ch["seen"] = seen
                ch.setdefault("enabled", en)
                break
    _mutate(fn)


def set_enabled(cid, on):
    """Pause/resume a source. Resuming also clears an 'expired' flag."""
    def fn(d):
        for ch in d["channels"]:
            if ch.get("id") == cid:
                ch["enabled"] = bool(on)
                if on:
                    ch["expired"] = False
                break
    _mutate(fn)


def clear_source_queue(cid):
    """Remove this source's queued/done items AND its seen list, so it can be
    re-pulled fresh. Keeps the source itself."""
    def fn(d):
        d["queue"] = [q for q in d["queue"] if q.get("channel_id") != cid]
        for ch in d["channels"]:
            if ch.get("id") == cid:
                ch["seen"] = []
    _mutate(fn)


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
    def fn(d):
        d["channels"] = [c for c in d["channels"] if c.get("id") != cid]
        d["queue"] = [q for q in d["queue"] if q.get("channel_id") != cid]
    _mutate(fn)


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
            _emit(log, f"Searching {q}…")
            try:
                items = fetch_search_filtered(
                    q, cfg, types=ch.get("types") or ["Video"],
                    duration=ch.get("duration", "Any"),
                    upload_days=int(ch.get("upload_days", 0) or 0),
                    sort=ch.get("sort", "Relevance"), n=limit,
                    log=lambda m: _emit(log, m))
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

        if ch.get("kind") == "playlist":
            from .auto_batch import fetch_playlist
            _emit(log, f"Scanning playlist {ch.get('url','')}…")
            kw = (ch.get("keyword", "") or "").strip().lower()
            try:
                items = fetch_playlist(ch["url"], limit, cfg)
            except Exception as e:
                _emit(log, f"  playlist failed: {str(e)[:70]}")
                continue
            if kw:
                items = [(u, t) for (u, t) in items if kw in (t or "").lower()]
            for (u, t) in items[:limit]:
                if u in seen:
                    continue
                seen.add(u)
                d["queue"].append(_queue_item(cfg, ch, u, t))
                added += 1
            ch["seen"] = list(seen)
            got = added - before
            _emit(log, f"  {got} new video(s) queued." if got
                  else "  nothing new (all already seen).")
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
                items = [it for it in items if kw in (it[1] or "").lower()]
            items = items[:limit]
            found += len(items)
            for it in items:
                u, t = it[0], it[1]
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

        def _merge(cur):
            existing = {(q.get("channel_id"), q.get("url"))
                        for q in cur["queue"]}
            for q in d["queue"]:
                key = (q.get("channel_id"), q.get("url"))
                if key not in existing:
                    cur["queue"].append(q)
            dmap = {c.get("id"): c for c in d["channels"]}
            for ch in cur["channels"]:
                dc = dmap.get(ch.get("id"))
                if dc:
                    ch["seen"] = list(set(ch.get("seen", []))
                                      | set(dc.get("seen", [])))
                    if dc.get("expired"):
                        ch["expired"] = True
                        ch["enabled"] = False
        _mutate(_merge)
    finally:
        if proxy_on:
            proxy.clear_active_port()
    return added


def _is_due(q, now):
    if q.get("status") in ("done", "unavailable"):
        return False
    if q.get("next_try", 0) > now:
        return False
    return True


def _roundrobin(items):
    """One item per source in turn (fair) - preserves each source's own order."""
    from collections import OrderedDict
    groups = OrderedDict()
    for q in items:
        groups.setdefault(q.get("channel_id"), []).append(q)
    out = []
    lists = list(groups.values())
    while any(lists):
        for lst in lists:
            if lst:
                out.append(lst.pop(0))
    return out


def _due_order(cfg, d):
    """Due queue items in the order to process them: focused sources first, then
    the rest by the chosen mode (fair round-robin / in-order / random)."""
    import random as _rnd
    now = time.time()
    focus_ids = {c.get("id") for c in d["channels"] if c.get("focus")}
    due = [q for q in d["queue"] if _is_due(q, now)]
    focused = [q for q in due if q.get("channel_id") in focus_ids]
    rest = [q for q in due if q.get("channel_id") not in focus_ids]
    mode = (cfg or {}).get("watch_order", "fair")

    def arrange(items):
        if mode == "random":
            items = list(items)
            _rnd.shuffle(items)
            return items
        if mode == "order":
            return items
        return _roundrobin(items)          # 'fair' (default)

    return arrange(focused) + arrange(rest)


def set_focus(cid, on):
    """Mark/unmark a source as focused (its videos jump the queue)."""
    def fn(d):
        for ch in d["channels"]:
            if ch.get("id") == cid:
                ch["focus"] = bool(on)
                break
    _mutate(fn)


def randomize_queue():
    """Shuffle the order of pending items in the stored queue."""
    import random as _rnd

    def fn(d):
        q = d["queue"]
        _rnd.shuffle(q)
        d["queue"] = q
    _mutate(fn)


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
        ordered = _due_order(cfg, d)       # focus-first, then fair/order/random
        _now = time.time()
        retry_cands = [x for x in d["queue"]
                       if x.get("save_video") and x.get("video_needed")
                       and x.get("status") == "done"
                       and x.get("video_next_try", 0) <= _now]
        n_total = len(ordered) + len(retry_cands)
        if per_cycle:
            n_total = min(n_total, per_cycle)
        for item in ordered:
            if cancel():
                break
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
            attempts = item.get("attempts", 0) + 1
            if status == "done":
                upd = {"status": "done", "last_error": "",
                       "done_at": time.time(), "attempts": attempts,
                       "video_needed": bool(item.get("video_needed"))}
                done += 1
                _emit(log, "    done ✓")
            elif status == "unavailable":
                upd = {"status": "unavailable", "last_error": msg,
                       "attempts": attempts}
                _emit(log, "    unavailable — skipping")
            else:
                mins = int((cfg or {}).get("watch_retry_minutes", 30) or 30)
                upd = {"status": "failed", "last_error": msg,
                       "attempts": attempts,
                       "next_try": time.time() + max(1, mins) * 60}
                _emit(log, f"    failed (will retry): {msg[:80]}")
            item.update(upd)   # keep the in-memory snapshot consistent
            _update_item(item.get("channel_id"), item.get("url"), upd)

        # retry videos whose transcript is done but whose file never saved
        for item in d["queue"]:
            if cancel():
                break
            if per_cycle and processed >= per_cycle:
                break
            if not (item.get("save_video") and item.get("video_needed")
                    and item.get("status") == "done"):
                continue
            if item.get("video_next_try", 0) > time.time():
                continue
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
            _set_activity(phase="Retrying video", i=processed, n=n_total,
                          title=title, wait_until=0)
            _emit(log, f"↻ retrying video that failed earlier: {title}")

            def _prog2(ph, _t=title):
                _set_activity(phase=ph)
                _emit(log, f"    {ph}…")

            cid = item.get("channel_id")
            url = item.get("url")
            ok = pipeline.retry_video_only(
                cfg, item, lambda m: _emit(log, m), cancel, _prog2)
            if ok:
                _emit(log, "    video saved ✓")
                _update_item(cid, url, {"video_needed": False,
                                        "video_next_try": 0})
            else:
                mins = int((cfg or {}).get("watch_retry_minutes", 30) or 30)
                _update_item(cid, url,
                             {"video_next_try": time.time() + max(1, mins) * 60})
                _emit(log, "    still couldn't save the video — will retry "
                      "next cycle")

        if done:
            _mutate(lambda d: d.__setitem__(
                "new_ready", d.get("new_ready", 0) + done))
    finally:
        _set_activity(phase="idle", i=0, n=0, title="", wait_until=0, folder="")
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
    """Remove only items WAITING to run (pending + failed/retrying). Keeps the
    finished 'done' history (so counts stay), unavailable items, channels, and
    seen lists."""
    def fn(d):
        d["queue"] = [q for q in d["queue"]
                      if q.get("status") not in ("pending", "failed")]
        d["new_ready"] = 0
    _mutate(fn)


def clear_new_ready():
    _mutate(lambda d: d.__setitem__("new_ready", 0))


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
