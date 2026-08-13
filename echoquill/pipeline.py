"""One-video pipeline shared by the channel watcher (mirrors the Auto-batch
per-video work): captions/audio -> optional video/audio save -> transcript
(+description) -> comments -> question set -> Q&A. Returns a status."""


def _already_have(dest, name):
    """True if a main transcript for <name> already exists in dest, ignoring the
    date prefix (YYYY-MM-DD-) so a re-run doesn't re-download."""
    import os
    import re
    from .media_gui import safe_filename
    try:
        if not name or not os.path.isdir(dest):
            return False
        target = safe_filename(name)[:-4].strip().lower()
        datepat = re.compile(r"^\d{4}-\d{1,2}-\d{1,2}-")
        for fn in os.listdir(dest):
            if not fn.lower().endswith(".txt"):
                continue
            stem = datepat.sub("", fn[:-4]).strip().lower()
            if stem == target:
                return True
    except Exception:
        return False
    return False


def process_video(cfg, item, log=lambda s: None, cancel=lambda: False,
                  progress=lambda ph: None):
    """Run one video end-to-end. When the proxy is on and YouTube bot-blocks a
    download, rotate to a fresh verified IP and retry - up to di_verify_tries
    IPs - before giving up. Returns (status, message)."""
    from . import proxy
    from .auto_batch import resolve_folder, normalize_name
    dest = resolve_folder(cfg, item.get("folder", ""))
    ttl = item.get("title", "")
    early = normalize_name(ttl) if ttl else ""
    if early and _already_have(dest, early):
        log("    already in this folder — skipping download.")
        return ("done", early)

    proxy_on = bool(cfg.get("di_enabled"))
    tries = int(cfg.get("di_verify_tries", 3) or 3) if proxy_on else 1
    last = ""
    for attempt in range(1, tries + 1):
        if proxy_on:
            progress("Verifying proxy IP")
            sid, ip = proxy.acquire_verified(
                cfg, tries=int(cfg.get("di_verify_tries", 3) or 3), log=log)
            if not sid:
                return ("failed", f"proxy: no live IP after {tries} tries - "
                        "paused, will retry next cycle")
        try:
            status, msg = _do_video(cfg, item, dest, log, cancel, progress)
        finally:
            if proxy_on:
                proxy.clear_active_sessid()
        if status != "failed" or not proxy_on or not proxy.is_block(msg):
            return (status, msg)
        last = msg
        if attempt < tries:
            log(f"    YouTube blocked this IP (attempt {attempt}/{tries}) - "
                "rotating to a NEW IP and retrying")
    return ("failed", f"bot-blocked on {tries} different IPs - likely needs "
            f"fresh YouTube cookies. Last: {last[:80]}")


def _do_video(cfg, item, dest, log=lambda s: None, cancel=lambda: False,
              progress=lambda ph: None):
    """One download+transcribe pass on the CURRENTLY pinned proxy IP (if any).
    Returns (status, message)."""
    import os
    import shutil
    import gc
    from .media_gui import (download_video, fetch_audio_info, safe_filename,
                            _safe_stem, _unique_path, _video_description,
                            _video_comments, fetch_captions, _dated)
    from .transcriber import Transcriber
    from . import ask_ai
    from .auto_batch import normalize_name

    url = item["url"]
    ttl = item.get("title", "")
    questions = item.get("questions") or []
    tmpdir = None
    try:
        use_caps = str(item.get("transcript_mode", "")).lower().startswith(
            "youtube")
        cap_segs = cap_text = None
        cap_title = ""
        if use_caps:
            progress("Fetching captions")
            cap_segs, cap_text, cap_title = fetch_captions(url, cfg)
        got_caps = bool(cap_text)

        apath, atitle = None, ""
        if (not got_caps) or item.get("save_audio"):
            progress("Downloading audio")
            apath, atitle = fetch_audio_info(url, log, cfg)
            tmpdir = os.path.dirname(apath)
        name = normalize_name(ttl or cap_title or atitle or "video") or "video"
        dname = _dated(name)

        if item.get("save_video"):
            progress("Saving video")
            try:
                download_video(url, cfg, dest, log, name=dname)
            except Exception as e:
                log(f"    video save failed: {e}")
                try:
                    from .media_gui import cleanup_parts
                    cleanup_parts(dest, dname)
                except Exception:
                    pass
        if item.get("save_audio") and apath and os.path.exists(apath):
            try:
                ext = os.path.splitext(apath)[1] or ".m4a"
                adst = _unique_path(os.path.join(dest, _safe_stem(dname) + ext))
                shutil.copy2(apath, adst)
            except Exception as e:
                log(f"    audio save failed: {e}")

        if got_caps:
            segs, text = cap_segs, cap_text
        else:
            progress("Transcribing")
            eng = Transcriber(cfg.get("model", "base"))
            model = eng.load()
            lang = cfg.get("language", "auto")
            lang = None if lang in ("", "auto") else lang
            segs, parts = [], []
            with eng._lock:
                segments, _i = model.transcribe(apath, language=lang,
                                                vad_filter=True)
                for seg in segments:
                    if cancel():
                        break
                    t = seg.text.strip()
                    segs.append((seg.start, t))
                    parts.append(t)
            text = " ".join(parts).strip()
            eng = None

        body = text
        if item.get("save_desc"):
            desc = _video_description(url, cfg)
            body = ("========== VIDEO DESCRIPTION ==========\n" + desc +
                    "\n\n========== TRANSCRIPTION ==========\n" + text)
        if _already_have(dest, name):
            log("    already in this folder — skipping.")
            return ("done", name)
        progress("Writing transcript")
        tpath = _unique_path(os.path.join(dest, safe_filename(dname)))
        with open(tpath, "w", encoding="utf-8") as f:
            f.write(f"{name}\n{url}\n\n{body}")

        if item.get("save_comments"):
            progress("Saving comments")
            try:
                cmts = _video_comments(url, cfg)
                cpath = _unique_path(os.path.join(
                    dest, safe_filename(dname + " - Comments")))
                with open(cpath, "w", encoding="utf-8") as fc:
                    fc.write(f"{name}\n{url}\n\n{cmts}")
            except Exception:
                pass

        if questions:
            progress("Asking AI")
            qa = []
            for q in questions:
                if cancel():
                    break
                ans = ask_ai.ask(q, segs, cfg, title=name, url=url)
                qa.append(f"{'*' * 50}\n{'*' * 50}\nQ: {q}\n\nA: {ans}\n")
            if qa:
                qpath = _unique_path(os.path.join(
                    dest, safe_filename(dname + " - Q&A")))
                with open(qpath, "w", encoding="utf-8") as f:
                    f.write(f"{name}\n{url}\n\n" + "\n".join(qa))
        return ("done", name)
    except Exception as e:
        msg = str(e)
        low = msg.lower()
        permanent = (("not available" in low and "format" not in low)
                     or "private" in low or "removed" in low
                     or "does not exist" in low or "no longer available" in low)
        return ("unavailable" if permanent else "failed", msg)
    finally:
        if tmpdir:
            shutil.rmtree(tmpdir, ignore_errors=True)
        gc.collect()
