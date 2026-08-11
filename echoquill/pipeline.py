"""One-video pipeline shared by the channel watcher (mirrors the Auto-batch
per-video work): captions/audio -> optional video/audio save -> transcript
(+description) -> comments -> question set -> Q&A. Returns a status."""


def process_video(cfg, item, log=lambda s: None, cancel=lambda: False):
    """Run one video end-to-end. Returns (status, message) where status is
    'done', 'failed' (retryable) or 'unavailable' (permanent)."""
    import os
    import shutil
    import gc
    from .media_gui import (download_video, fetch_audio_info, safe_filename,
                            _safe_stem, _unique_path, _video_description,
                            _video_comments, fetch_captions, _dated)
    from .transcriber import Transcriber
    from . import ask_ai
    from .auto_batch import resolve_folder, normalize_name

    url = item["url"]
    ttl = item.get("title", "")
    questions = item.get("questions") or []
    tmpdir = None
    try:
        dest = resolve_folder(cfg, item.get("folder", ""))

        use_caps = str(item.get("transcript_mode", "")).lower().startswith(
            "youtube")
        cap_segs = cap_text = None
        cap_title = ""
        if use_caps:
            cap_segs, cap_text, cap_title = fetch_captions(url, cfg)
        got_caps = bool(cap_text)

        apath, atitle = None, ""
        if (not got_caps) or item.get("save_audio"):
            apath, atitle = fetch_audio_info(url, log, cfg)
            tmpdir = os.path.dirname(apath)
        name = normalize_name(ttl or cap_title or atitle or "video") or "video"
        dname = _dated(name)

        if item.get("save_video"):
            try:
                download_video(url, cfg, dest, log, name=dname)
            except Exception as e:
                log(f"    video save failed: {e}")
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
        tpath = _unique_path(os.path.join(dest, safe_filename(dname)))
        with open(tpath, "w", encoding="utf-8") as f:
            f.write(f"{name}\n{url}\n\n{body}")

        if item.get("save_comments"):
            try:
                cmts = _video_comments(url, cfg)
                cpath = _unique_path(os.path.join(
                    dest, safe_filename(dname + " - Comments")))
                with open(cpath, "w", encoding="utf-8") as fc:
                    fc.write(f"{name}\n{url}\n\n{cmts}")
            except Exception:
                pass

        if questions:
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
