"""Research projects (Pro).

Download + transcribe a set of videos into ONE titled folder, then synthesize
answers to a whole question set across EVERY transcript (+ description), and
write a self-contained, clickable HTML report:
  - each answer cites one or MANY sources (Wikipedia-style)
  - a citation's time link opens that video on YouTube at that second
  - a citation's source-name link jumps to that transcript, embedded in the
    same page, at the cited spot
No internet is needed to READ the report; transcripts are bundled inside it.
"""

import html
import json
import os
import re
import time

from . import ai_call


# ---------------------------------------------------------------- helpers
def yt_id(url):
    m = re.search(r"(?:v=|youtu\.be/|shorts/|embed/)([A-Za-z0-9_-]{11})",
                  url or "")
    return m.group(1) if m else ""


def mmss(sec):
    sec = int(sec or 0)
    h, m, s = sec // 3600, (sec % 3600) // 60, sec % 60
    return f"{h}:{m:02d}:{s:02d}" if h else f"{m}:{s:02d}"


def dur_str(sec):
    """Human duration for a fetched video, or '' when unknown."""
    if not sec:
        return ""
    return mmss(sec)


def _json(text):
    """Best-effort parse of a JSON object from a model reply."""
    if not text:
        return None
    t = text.strip()
    if t.startswith("```"):
        t = re.sub(r"^```[a-zA-Z]*\n?", "", t)
        t = re.sub(r"\n?```$", "", t).strip()
    a, b = t.find("{"), t.rfind("}")
    if a >= 0 and b > a:
        t = t[a:b + 1]
    try:
        return json.loads(t)
    except Exception:
        return None


def _windows(segs, max_chars=6000):
    """Group (sec, text) segments into transcript windows under max_chars,
    each rendered as '[sec] text' lines."""
    out, cur, n = [], [], 0
    for sec, txt in segs:
        line = f"[{int(sec)}] {txt}"
        if n + len(line) > max_chars and cur:
            out.append("\n".join(cur))
            cur, n = [], 0
        cur.append(line)
        n += len(line) + 1
    if cur:
        out.append("\n".join(cur))
    return out or [""]


def _windows_text(text, max_chars=6000):
    """Chunk plain web-page text into windows under max_chars (paragraph-aware)."""
    out, cur, n = [], [], 0
    for para in (text or "").split("\n"):
        para = para.strip()
        if not para:
            continue
        if n + len(para) > max_chars and cur:
            out.append("\n".join(cur))
            cur, n = [], 0
        cur.append(para)
        n += len(para) + 1
    if cur:
        out.append("\n".join(cur))
    return out or [""]


# ---------------------------------------------------------------- search
# YouTube duration filter codes: 1=short (<4 min), 2=long (>20 min),
# 3=medium (4-20 min).
DURATION_CODE = {"any": 0, "any length": 0,
                 "under 4 min": 1, "under 4 minutes": 1, "short": 1,
                 "4-20 min": 3, "4\u201320 min": 3, "medium": 3,
                 "over 20 min": 2, "over 20 minutes": 2, "long": 2}


def _sp(sort, days, duration=0):
    """YouTube 'sp' filter token: sort field + a filters sub-message holding
    the upload-date bucket and/or duration.
    sort: 0 relevance, 1 rating, 2 upload date (newest), 3 view count.
    duration: 0 any, 1 short(<4m), 2 long(>20m), 3 medium(4-20m)."""
    import base64
    field = {"relevance": 0, "rating": 1, "newest": 2, "upload date": 2,
             "most viewed": 3, "view count": 3, "views": 3}.get(
                 (sort or "").strip().lower(), 0)
    buf = b""
    if field:
        buf += bytes([0x08, field])
    d = int(days or 0)
    bucket = 0 if d <= 0 else 2 if d <= 1 else 3 if d <= 7 else 4 if d <= 31 else 5
    sub = b""
    if bucket:
        sub += bytes([0x08, bucket])          # field 1: upload date
    if int(duration or 0):
        sub += bytes([0x18, int(duration)])   # field 3: duration
    if sub:
        buf += bytes([0x12, len(sub)]) + sub
    return base64.urlsafe_b64encode(buf).decode() if buf else ""


WINDOW_DAYS = {"any": 0, "today": 1, "this week": 7, "this month": 31,
               "this year": 365}


def fetch_search_web(query, cfg, sort="Most viewed", window="Any", n=25,
                     log=lambda s: None, duration="Any"):
    """Search YouTube through the real web results URL so EXACT-PHRASE quotes
    are honored (yt-dlp's ytsearch: path ignores them). Returns [(url,title)]."""
    import os
    import yt_dlp
    from urllib.parse import quote
    days = WINDOW_DAYS.get((window or "any").strip().lower(), 0)
    dur = DURATION_CODE.get((duration or "any").strip().lower(), 0)
    sp = _sp(sort, days, dur)
    base = "https://www.youtube.com/results?search_query=" + quote(query or "")
    target = base + ("&sp=" + quote(sp) if sp else "")
    opts = {"quiet": True, "no_warnings": True, "extract_flat": True,
            "skip_download": True, "playlistend": max(int(n), 1)}
    cf = ((cfg or {}).get("yt_cookies_file", "") or "").strip()
    if cf and os.path.exists(cf):
        opts["cookiefile"] = cf
    from .media_gui import _apply_proxy
    _apply_proxy(opts, cfg)
    try:
        with yt_dlp.YoutubeDL(opts) as ydl:
            info = ydl.extract_info(target, download=False)
    except Exception as e:
        log(f"  search error: {str(e)[:120]}")
        return []
    out = []
    for e in (info.get("entries") or []):
        if not e:
            continue
        u = e.get("url") or e.get("webpage_url")
        if not u and e.get("id"):
            u = "https://www.youtube.com/watch?v=" + e["id"]
        t = (e.get("title") or "").strip()
        if u:
            out.append((u, t, e.get("duration")))
        if len(out) >= int(n):
            break
    return out


# ---------------------------------------------------------------- AI: questions
def generate_questions(cfg, goal, existing=None, log=lambda s: None):
    """Ask the AI to produce research questions for a goal. If existing is
    given, ask for MORE, non-duplicate questions (the Expand button)."""
    existing = existing or []
    if existing:
        sysmsg = (
            "You expand a research question set. Given the user's goal and the "
            "questions already written, produce ADDITIONAL questions that fill "
            "gaps and go deeper. Do NOT repeat existing ones. Output one "
            "question per line, no numbering, no preamble.")
        user = ("GOAL:\n" + goal + "\n\nEXISTING QUESTIONS:\n"
                + "\n".join(existing)
                + "\n\nWrite only NEW questions, one per line.")
    else:
        sysmsg = (
            "You generate a comprehensive, exhaustive list of research "
            "questions someone should ask to fully understand their goal. "
            "Cover every important angle. Output one question per line, no "
            "numbering, no preamble, no closing remarks.")
        user = "GOAL:\n" + goal + "\n\nWrite the questions, one per line."
    ok, reply = ai_call.chat(cfg, sysmsg, user, temperature=0.4)
    if not ok:
        log("  question generation failed: " + reply[:100])
        return []
    out = []
    for line in reply.splitlines():
        q = line.strip().lstrip("-*0123456789.) ").strip()
        if q and (("?" in q and len(q) > 3) or len(q) > 8):
            out.append(q)
    seen = set(x.strip().lower() for x in existing)
    uniq = []
    for q in out:
        k = q.lower()
        if k not in seen:
            seen.add(k)
            uniq.append(q)
    return uniq


def suggest_keywords(cfg, unanswered, goal, log=lambda s: None):
    """Ask the AI for ONE better YouTube search query to find videos that
    answer the still-unanswered questions."""
    sysmsg = (
        "You suggest ONE concise YouTube search query (a few words; you may use "
        "double quotes for an exact phrase) most likely to surface videos that "
        "answer the listed unanswered questions. Output ONLY the query text.")
    user = ("GOAL:\n" + goal + "\n\nUNANSWERED QUESTIONS:\n"
            + "\n".join("- " + q for q in unanswered)
            + "\n\nGive one search query.")
    ok, reply = ai_call.chat(cfg, sysmsg, user, temperature=0.3)
    if not ok or not reply:
        return ""
    return reply.strip().splitlines()[0].strip().strip('"').strip()


# ---------------------------------------------------------------- synthesis
def _extract_source(cfg, questions, src, log):
    """Phase 1: pull, from ONE source (video transcript OR web page), the
    findings relevant to each question. Returns {qidx: [{"t","point"}, ...]}.
    Web sources have t=None (no timestamps)."""
    findings = {}
    qlist = "\n".join(f"{i+1}. {q}" for i, q in enumerate(questions))
    is_web = src.get("kind") == "web" or not src.get("segs")
    if is_web:
        sysmsg = (
            "You extract, from a web page's text, only the passages that help "
            "answer a list of research questions. Use ONLY what the text says. "
            "Ignore questions the text does not address. Reply with STRICT JSON "
            'only, shape: {"1":[{"point":"..."}], "2":[...]}  (keys are '
            "question numbers). No prose, no code fences.")
        wins = [w for w in _windows_text(src.get("text", "")) if w.strip()]
        label = "WEB PAGE"
    else:
        sysmsg = (
            "You extract, from a single video transcript, only the passages "
            "that help answer a list of research questions. Use ONLY what the "
            "transcript says. For every point, give the timestamp (the "
            "[seconds] marker nearest where it is said). Ignore questions the "
            "transcript does not address. Reply with STRICT JSON only, shape: "
            '{"1":[{"t":123,"point":"..."}], "2":[...]}  (keys are question '
            "numbers). No prose, no code fences.")
        wins = [w for w in _windows(src.get("segs") or []) if w.strip()]
        label = "TRANSCRIPT"
    for wi, win in enumerate(wins):
        if len(wins) > 1:
            log(f"      reading part {wi+1}/{len(wins)}\u2026")
        user = (f"QUESTIONS:\n{qlist}\n\n{label} (of "
                f"\"{src.get('name','')}\"):\n{win}")
        ok, reply = ai_call.chat(cfg, sysmsg, user, temperature=0.1)
        if not ok:
            log(f"    AI extract failed: {reply[:80]}")
            continue
        data = _json(reply) or {}
        for k, arr in data.items():
            try:
                qi = int(str(k).strip()) - 1
            except Exception:
                continue
            if qi < 0 or qi >= len(questions) or not isinstance(arr, list):
                continue
            for it in arr:
                if not isinstance(it, dict):
                    continue
                pt = (it.get("point") or "").strip()
                if not pt:
                    continue
                if is_web:
                    t = None
                else:
                    try:
                        t = int(float(it.get("t", 0)))
                    except Exception:
                        t = 0
                findings.setdefault(qi, []).append({"t": t, "point": pt})
    return findings


def _answer_question(cfg, question, per_video, log):
    """Phase 2: synthesize one answer from findings across sources (videos and
    web pages). Video points carry a timestamp; web points don't.
    Returns answer text with citation tokens like [[S2|754]] or [[S2]]."""
    lines, tokens = [], []
    for k, name, items in per_video:
        for it in items:
            if it.get("t") is not None:
                tok = f"[[S{k}|{it['t']}]]"
                where = f"{name} @ {mmss(it['t'])}"
            else:
                tok = f"[[S{k}]]"
                where = name
            tokens.append(tok)
            lines.append(f"{tok} ({where}): {it['point']}")
    if not lines:
        return "The sources in this project do not address this question."
    sysmsg = (
        "You answer a research question using ONLY the findings provided (from "
        "video transcripts and web pages). Absolute rules: use no outside "
        "knowledge; invent nothing; no hyperbole, no editorializing, no filler. "
        "State only what the findings support. Cite sources inline by copying "
        "the exact bracket tokens shown (e.g. [[S2|754]] or [[S2]]); when "
        "several findings support a point, cite ALL of them together. If the "
        "findings do not actually answer the question, reply with exactly: "
        "NOT_COVERED. Write in plain paragraphs, nothing else.")
    user = (f"QUESTION: {question}\n\nFINDINGS (each begins with its citation "
            f"token):\n" + "\n".join(lines) +
            "\n\nWrite the answer now, placing the exact tokens after the "
            "claims they support.")
    ok, reply = ai_call.chat(cfg, sysmsg, user, temperature=0.3)
    if not ok:
        log(f"    AI answer failed: {reply[:80]}")
        return "\n".join(lines)
    valid = set(tokens)
    for tok in set(re.findall(r"\[\[S\d+(?:\|\d+)?\]\]", reply)):
        if tok not in valid:
            reply = reply.replace(tok, "")
    return reply.strip()


NOT_ANSWERED = "Not addressed by the videos in this project."


def extract_all(cfg, questions, videos, start=0, log=lambda s: None,
                cancel=lambda: False, progress=lambda a, b: None):
    """Phase 1 for videos[start:]. Returns list aligned with videos: each entry
    is {qi: [findings]}. Videos before `start` are skipped (already done)."""
    out = []
    total = len(videos)
    for vk in range(start, total):
        if cancel():
            break
        progress(vk, total)
        _kind = "page" if videos[vk].get("kind") == "web" else "transcript"
        log("    reading " + _kind + " " + str(vk + 1) + "/" + str(total)
            + ": " + videos[vk].get("name", ""))
        out.append(_extract_source(cfg, questions, videos[vk], log))
    return out


def answer_all(cfg, questions, videos, extracts, log=lambda s: None,
               cancel=lambda: False):
    """Phase 2. extracts aligned with videos. Returns list of
    {q, answer, answered}."""
    results = []
    for qi, q in enumerate(questions):
        if cancel():
            break
        per_video = []
        for vk, vid in enumerate(videos):
            items = (extracts[vk] if vk < len(extracts) else {}).get(qi) or []
            if items:
                per_video.append((vk, vid.get("name", ""), items))
        log("    answering question " + str(qi + 1) + "/" + str(len(questions)))
        answered = bool(per_video)
        ans = _answer_question(cfg, q, per_video, log)
        if ans.strip() == "NOT_COVERED" or not per_video:
            ans = NOT_ANSWERED
            answered = False
        results.append({"q": q, "answer": ans, "answered": answered})
    return results


def synthesize(cfg, questions, videos, log=lambda s: None,
               cancel=lambda: False, progress=lambda a, b: None):
    """Convenience: full phase-1 + phase-2 over all videos."""
    extracts = extract_all(cfg, questions, videos, 0, log, cancel, progress)
    return answer_all(cfg, questions, videos, extracts, log, cancel)


# ---------------------------------------------------------------- report
def _nearest_seg(segs, sec):
    """Index of the segment whose start is closest to sec."""
    best, bd = 0, None
    for i, (st, _t) in enumerate(segs):
        d = abs(int(st) - sec)
        if bd is None or d < bd:
            bd, best = d, i
    return best


def _render_answer(text, sources):
    """Turn citation tokens in an answer into clickable links (video or web)."""
    esc = html.escape(text)

    def repl(m):
        k = int(m.group(1))
        sec = m.group(2)
        if k < 0 or k >= len(sources):
            return ""
        src = sources[k]
        name = html.escape(src.get("name", f"Source {k+1}"))
        is_web = src.get("kind") == "web" or not src.get("segs")
        if is_web:
            url = html.escape(src.get("url", "#"))
            return (f'<span class="cite">[<a class="src" href="#src{k}">'
                    f'{name}</a> <a class="ts" href="{url}" target="_blank" '
                    f'rel="noopener">link</a>]</span>')
        secn = int(sec) if sec else 0
        vidid = yt_id(src.get("url", ""))
        seg_i = _nearest_seg(src.get("segs") or [(0, "")], secn)
        yurl = (f"https://youtu.be/{vidid}?t={secn}" if vidid
                else src.get("url", "#"))
        return (f'<span class="cite">[<a class="src" href="#src{k}s{seg_i}">'
                f'{name}</a> <a class="ts" href="{yurl}" target="_blank" '
                f'rel="noopener">{mmss(secn)}</a>]</span>')

    esc = re.sub(r"\[\[S(\d+)(?:\|(\d+))?\]\]", repl, esc)
    paras = [p.strip() for p in esc.split("\n") if p.strip()]
    return "\n".join(f"<p>{p}</p>" for p in paras)


def _render_source(k, src):
    """Render one source (video transcript with timestamps, or web page text)
    into the report appendix, with anchors the citations jump to."""
    name = html.escape(src.get("name", ""))
    url = html.escape(src.get("url", "#"))
    is_web = src.get("kind") == "web" or not src.get("segs")
    if is_web:
        body = "".join("<p>" + html.escape(p.strip()) + "</p>"
                       for p in (src.get("text", "") or "").split("\n")
                       if p.strip())
        return (f'<section class="tr" id="src{k}"><h3>{name}</h3>'
                f'<div class="vlink"><a href="{url}" target="_blank" '
                f'rel="noopener">open page</a></div>'
                f'<div class="webtext">{body}</div></section>')
    vidid = yt_id(src.get("url", ""))
    rows = []
    for i, (st, txt) in enumerate(src.get("segs") or []):
        sec = int(st)
        yurl = (f"https://youtu.be/{vidid}?t={sec}" if vidid else url)
        rows.append(
            f'<div class="seg" id="src{k}s{i}"><a class="ts" href="{yurl}" '
            f'target="_blank" rel="noopener">{mmss(sec)}</a>'
            f'<span>{html.escape(txt)}</span></div>')
    desc = src.get("desc") or ""
    desc_html = (f'<details class="desc"><summary>Description</summary>'
                 f'<pre>{html.escape(desc)}</pre></details>') if desc else ""
    return (f'<section class="tr" id="src{k}"><h3>{name}</h3>'
            f'<div class="vlink"><a href="{url}" target="_blank" '
            f'rel="noopener">open video</a></div>{desc_html}'
            f'<div class="segs">' + "".join(rows) + "</div></section>")


_CSS = """
:root{--fg:#1a1a1a;--mut:#6b6b6b;--acc:#0b57d0;--bd:#e3e3e0;--bg:#faf9f7;
--card:#ffffff;--cite:#0f6e56}
*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--fg);
font:16px/1.7 -apple-system,Segoe UI,Roboto,Arial,sans-serif}
.wrap{max-width:900px;margin:0 auto;padding:28px 20px 80px}
h1{font-size:26px;font-weight:600;margin:0 0 4px}
.meta{color:var(--mut);font-size:14px;margin-bottom:22px}
.toc{background:var(--card);border:1px solid var(--bd);border-radius:12px;
padding:14px 18px;margin-bottom:26px}
.toc a{color:var(--acc);text-decoration:none;display:block;padding:3px 0}
.toc a:hover{text-decoration:underline}
.q{background:var(--card);border:1px solid var(--bd);border-radius:12px;
padding:18px 22px;margin:0 0 18px}
.q h2{font-size:18px;font-weight:600;margin:0 0 10px}
.q p{margin:0 0 10px}
.cite{white-space:nowrap;font-size:13px}
.cite a{text-decoration:none}.src{color:var(--cite)}.ts{color:var(--acc)}
.cite a:hover{text-decoration:underline}
h1.appx{font-size:20px;margin:40px 0 10px;border-top:1px solid var(--bd);
padding-top:24px}
.tr{background:var(--card);border:1px solid var(--bd);border-radius:12px;
padding:14px 18px;margin:0 0 14px}
.tr h3{font-size:16px;margin:0 0 2px}
.vlink a{color:var(--acc);text-decoration:none;font-size:13px}
.desc{margin:8px 0}.desc pre{white-space:pre-wrap;font:13px/1.5 inherit;
color:var(--mut);background:var(--bg);padding:8px;border-radius:8px}
.segs{margin-top:8px}
.seg{display:flex;gap:10px;padding:2px 0;font-size:14px}
.seg .ts{flex:none;min-width:56px;color:var(--acc);text-decoration:none}
.seg:target{background:#fff3cd;border-radius:6px}
.webtext p{margin:6px 0;font-size:14px}
.tr:target{outline:2px solid #f0d98a}
.gap{background:#fff8e6;border:1px solid #f0d98a;border-radius:12px;padding:14px 20px;margin:0 0 18px}
.gap h2{font-size:16px;margin:0 0 8px}.gap li{margin:2px 0}
.gap code{background:#fff;padding:2px 6px;border-radius:6px}
"""


def build_report(project_name, questions_results, videos, unanswered=None,
                 suggestion=""):
    toc = "".join(
        f'<a href="#q{i}">{i+1}. {html.escape(r["q"])}</a>'
        for i, r in enumerate(questions_results))
    qs = ""
    for i, r in enumerate(questions_results):
        qs += (f'<div class="q" id="q{i}"><h2>{i+1}. '
               f'{html.escape(r["q"])}</h2>'
               f'{_render_answer(r["answer"], videos)}</div>')
    gap = ""
    unanswered = unanswered or []
    if unanswered:
        items = "".join("<li>" + html.escape(q) + "</li>" for q in unanswered)
        sug = ("<p>Suggested next search: <code>" + html.escape(suggestion)
               + "</code></p>") if suggestion else ""
        gap = ('<div class="gap"><h2>Not answered by these sources</h2>'
               '<ul>' + items + '</ul>' + sug + '</div>')
    trs = "".join(_render_source(vk, v) for vk, v in enumerate(videos))
    when = time.strftime("%B %-d, %Y") if os.name != "nt" else \
        time.strftime("%B %d, %Y")
    return f"""<!doctype html><html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>{html.escape(project_name)} — research report</title>
<style>{_CSS}</style></head><body><div class="wrap">
<h1>{html.escape(project_name)}</h1>
<div class="meta">Research report · {sum(1 for v in videos if v.get("kind")!="web")} videos · {sum(1 for v in videos if v.get("kind")=="web")} web pages · """ \
        f"""{len(questions_results)} questions · {when}</div>
<div class="toc"><strong>Questions</strong>{toc}</div>
{qs}
{gap}
<h1 class="appx">Sources &amp; transcripts</h1>
{trs}
</div></body></html>"""


# ---------------------------------------------------------------- run
def project_dir(cfg, name):
    from .media_gui import transcripts_dir
    from .auto_batch import normalize_name
    base = transcripts_dir(cfg)
    folder = os.path.join(base, "Research", normalize_name(name) or "project")
    os.makedirs(folder, exist_ok=True)
    return folder


def _norm_url(u):
    """Normalize a URL for de-duplication (drop scheme, www, fragment, common
    tracking params, trailing slash)."""
    import re as _re
    u = (u or "").strip()
    u = _re.sub(r"^https?://", "", u, flags=_re.I)
    u = _re.sub(r"^www\.", "", u, flags=_re.I)
    u = u.split("#")[0]
    u = _re.sub(r"[?&](utm_[^=&]+|fbclid|gclid)=[^&]*", "", u)
    return u.rstrip("/?").lower()


def run(cfg, name, questions, video_items, log=lambda s: None,
        cancel=lambda: False, progress=lambda ph, i, n: None,
        on_done=lambda path: None, folder=None, goal="",
        auto_rounds=0, refetch=None, mode="videos", web_per_q=10,
        location="United States", language="English"):
    """Research project across videos and/or web pages.
    mode: 'videos' | 'web' | 'both'. For web, each question is a Google search
    (via DataForSEO); the top web_per_q results are read for their text and
    de-duplicated by URL. Everything is pooled and every question is answered
    across all sources with citations. auto_rounds>0 keeps searching for more
    sources to fill unanswered questions.
    Returns {"report":path, "unanswered":[...], "suggestion":str}."""
    from . import pipeline
    if not folder:
        folder = project_dir(cfg, name)
    else:
        os.makedirs(folder, exist_ok=True)
    log("Research project: " + name + "  (" + mode + ")")
    log("  folder: " + folder)
    collected = []
    seen = set()        # video urls
    web_seen = set()    # normalized web urls
    do_videos = mode in ("videos", "both")
    do_web = mode in ("web", "both")

    def transcribe(items):
        n = len(items)
        for i, item in enumerate(items):
            if cancel():
                break
            u = item.get("url", "")
            if not u or u in seen:
                continue
            seen.add(u)
            it = dict(item)
            it["folder"] = folder
            it["save_desc"] = True
            it["save_comments"] = False
            it["questions"] = []
            progress("transcribe", i + 1, n)
            log("→ " + (it.get("title") or u))
            pipeline.process_video(cfg, it, log, cancel,
                                   progress=lambda ph: None,
                                   sink=collected.append)

    def gather_web(qs):
        from . import dataforseo
        total = len(qs)
        for qi, q in enumerate(qs):
            if cancel():
                break
            progress("web-search", qi + 1, total)
            log("  Google: " + q)
            for (u, t) in dataforseo.search(cfg, q, web_per_q, location,
                                            language, log=log):
                if cancel():
                    break
                nu = _norm_url(u)
                if not nu or nu in web_seen:
                    continue
                web_seen.add(nu)
                log("    reading " + u)
                title, text = dataforseo.read_page(cfg, u, log=log)
                if not (text or "").strip():
                    continue
                collected.append({"kind": "web",
                                  "name": (title or t or u), "url": u,
                                  "text": text})

    if do_videos:
        transcribe(video_items)
    if do_web and not cancel():
        gather_web(questions)

    if cancel():
        log("  cancelled.")
        return {"report": "", "unanswered": [], "suggestion": ""}
    if not collected:
        log("  nothing gathered — nothing to synthesize.")
        return {"report": "", "unanswered": [], "suggestion": ""}

    import gc
    gc.collect()
    log("  synthesizing across " + str(len(collected)) + " sources…")
    log("  (AI reading every source — can take a while on a slow/local model; "
        "it is working, not stuck)")
    extracts = extract_all(cfg, questions, collected, 0, log, cancel,
                           lambda d, t: progress("synthesize", d, t))
    results = answer_all(cfg, questions, collected, extracts, log, cancel)

    rounds = 0
    while auto_rounds and rounds < auto_rounds and not cancel():
        unanswered = [r["q"] for r in results if not r.get("answered")]
        if not unanswered:
            break
        log("  " + str(len(unanswered)) + " unanswered — auto-search round "
            + str(rounds + 1) + "/" + str(auto_rounds))
        kw = suggest_keywords(cfg, unanswered, goal, log)
        if not kw:
            break
        before = len(collected)
        if do_videos and refetch:
            log("  more videos: " + kw)
            more = [it for it in (refetch(kw) or [])
                    if it.get("url") not in seen]
            if more:
                transcribe(more)
        if do_web and not cancel():
            log("  more web: " + kw)
            gather_web([kw])
        if len(collected) == before:
            break
        new_ex = extract_all(cfg, questions, collected, before, log, cancel,
                             lambda d, t: progress("synthesize", d, t))
        extracts += new_ex
        results = answer_all(cfg, questions, collected, extracts, log, cancel)
        rounds += 1

    unanswered_final = [r["q"] for r in results if not r.get("answered")]
    suggestion = ""
    if unanswered_final and not cancel():
        suggestion = suggest_keywords(cfg, unanswered_final, goal, log)

    html_doc = build_report(name, results, collected, unanswered_final,
                            suggestion)
    rpath = os.path.join(folder, "report.html")
    with open(rpath, "w", encoding="utf-8") as f:
        f.write(html_doc)
    try:
        man = {"name": name, "created": time.time(), "goal": goal,
               "mode": mode, "questions": questions,
               "unanswered": unanswered_final,
               "sources": [{"kind": c.get("kind", "video"),
                            "name": c["name"], "url": c["url"]}
                           for c in collected]}
        with open(os.path.join(folder, "project.json"), "w",
                  encoding="utf-8") as f:
            json.dump(man, f, indent=2)
    except Exception:
        pass
    log("  report written: " + rpath)
    on_done(rpath)
    return {"report": rpath, "unanswered": unanswered_final,
            "suggestion": suggestion}

