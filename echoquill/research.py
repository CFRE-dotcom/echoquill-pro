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


# ---------------------------------------------------------------- synthesis
def _extract_for_video(cfg, questions, vid, log):
    """Phase 1: pull, from ONE video, the findings relevant to each question.
    Returns {qidx: [{"t": sec, "point": str}, ...]}."""
    findings = {}
    qlist = "\n".join(f"{i+1}. {q}" for i, q in enumerate(questions))
    sysmsg = (
        "You extract, from a single video transcript, only the passages that "
        "help answer a list of research questions. Use ONLY what the "
        "transcript says. For every point, give the timestamp (the [seconds] "
        "marker nearest where it is said). Ignore questions the transcript "
        "does not address. Reply with STRICT JSON only, shape: "
        '{"1":[{"t":123,"point":"..."}], "2":[...]}  (keys are question '
        "numbers). No prose, no code fences.")
    for win in _windows(vid.get("segs") or []):
        if not win.strip():
            continue
        user = (f"QUESTIONS:\n{qlist}\n\nTRANSCRIPT (of "
                f"\"{vid.get('name','')}\"):\n{win}")
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
                try:
                    t = int(float(it.get("t", 0)))
                except Exception:
                    t = 0
                findings.setdefault(qi, []).append({"t": t, "point": pt})
    return findings


def _answer_question(cfg, question, per_video, log):
    """Phase 2: synthesize one answer from findings across videos.
    per_video: list of (video_index, name, [{"t","point"}...]).
    Returns answer text containing citation tokens like [[V2|754]]."""
    lines, tokens = [], []
    for vk, name, items in per_video:
        for it in items:
            tok = f"[[V{vk}|{it['t']}]]"
            tokens.append(tok)
            lines.append(f"{tok} ({name} @ {mmss(it['t'])}): {it['point']}")
    if not lines:
        return "The videos in this project do not address this question."
    sysmsg = (
        "You write one clear, well-organized answer to a research question, "
        "using ONLY the findings provided. Cite sources inline by copying the "
        "exact bracket tokens shown (e.g. [[V2|754]]). When several findings "
        "support the same point, cite ALL of them together. Do not invent "
        "tokens or facts. Write in plain paragraphs.")
    user = (f"QUESTION: {question}\n\nFINDINGS (each begins with its citation "
            f"token):\n" + "\n".join(lines) +
            "\n\nWrite the answer now, placing the exact tokens after the "
            "claims they support.")
    ok, reply = ai_call.chat(cfg, sysmsg, user, temperature=0.3)
    if not ok:
        log(f"    AI answer failed: {reply[:80]}")
        # fall back to listing the raw findings with their tokens
        return "\n".join(lines)
    valid = set(tokens)
    # drop any hallucinated tokens the model may have invented
    for tok in set(re.findall(r"\[\[V\d+\|\d+\]\]", reply)):
        if tok not in valid:
            reply = reply.replace(tok, "")
    return reply.strip()


def synthesize(cfg, questions, videos, log=lambda s: None,
               cancel=lambda: False, progress=lambda a, b: None):
    """Run both phases. Returns [{"q":..., "answer_html":...}] plus fills a
    citation map. progress(done, total) reports phase-1 video progress."""
    # phase 1 — per video
    extracts = {}      # vk -> {qi: [findings]}
    total = len(videos)
    for vk, vid in enumerate(videos):
        if cancel():
            break
        progress(vk, total)
        log(f"    reading transcript {vk+1}/{total}: {vid.get('name','')}")
        extracts[vk] = _extract_for_video(cfg, questions, vid, log)
    # phase 2 — per question
    results = []
    for qi, q in enumerate(questions):
        if cancel():
            break
        per_video = []
        for vk, vid in enumerate(videos):
            items = (extracts.get(vk) or {}).get(qi) or []
            if items:
                per_video.append((vk, vid.get("name", ""), items))
        log(f"    answering question {qi+1}/{len(questions)}")
        ans = _answer_question(cfg, q, per_video, log)
        results.append({"q": q, "answer": ans})
    return results


# ---------------------------------------------------------------- report
def _nearest_seg(segs, sec):
    """Index of the segment whose start is closest to sec."""
    best, bd = 0, None
    for i, (st, _t) in enumerate(segs):
        d = abs(int(st) - sec)
        if bd is None or d < bd:
            bd, best = d, i
    return best


def _render_answer(text, videos):
    """Turn citation tokens in an answer into clickable links."""
    esc = html.escape(text)

    def repl(m):
        vk, sec = int(m.group(1)), int(m.group(2))
        if vk < 0 or vk >= len(videos):
            return ""
        vid = videos[vk]
        name = html.escape(vid.get("name", f"Video {vk+1}"))
        vidid = yt_id(vid.get("url", ""))
        seg_i = _nearest_seg(vid.get("segs") or [(0, "")], sec)
        anchor = f"v{vk}s{seg_i}"
        yurl = (f"https://youtu.be/{vidid}?t={sec}" if vidid
                else vid.get("url", "#"))
        return (f'<span class="cite">[<a class="src" href="#{anchor}">'
                f'{name}</a> <a class="ts" href="{yurl}" target="_blank" '
                f'rel="noopener">{mmss(sec)}</a>]</span>')

    # tokens are inside the escaped text as [[V2|754]] (brackets are safe)
    esc = re.sub(r"\[\[V(\d+)\|(\d+)\]\]", repl, esc)
    # paragraphs
    paras = [p.strip() for p in esc.split("\n") if p.strip()]
    return "\n".join(f"<p>{p}</p>" for p in paras)


def _render_transcript(vk, vid):
    segs = vid.get("segs") or []
    vidid = yt_id(vid.get("url", ""))
    rows = []
    for i, (st, txt) in enumerate(segs):
        sec = int(st)
        yurl = (f"https://youtu.be/{vidid}?t={sec}" if vidid
                else vid.get("url", "#"))
        rows.append(
            f'<div class="seg" id="v{vk}s{i}"><a class="ts" href="{yurl}" '
            f'target="_blank" rel="noopener">{mmss(sec)}</a>'
            f'<span>{html.escape(txt)}</span></div>')
    desc = vid.get("desc") or ""
    desc_html = (f'<details class="desc"><summary>Description</summary>'
                 f'<pre>{html.escape(desc)}</pre></details>') if desc else ""
    return (f'<section class="tr" id="v{vk}"><h3>{html.escape(vid.get("name",""))}'
            f'</h3><div class="vlink"><a href="{html.escape(vid.get("url","#"))}"'
            f' target="_blank" rel="noopener">open video</a></div>'
            f'{desc_html}<div class="segs">' + "".join(rows) + "</div></section>")


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
"""


def build_report(project_name, questions_results, videos):
    toc = "".join(
        f'<a href="#q{i}">{i+1}. {html.escape(r["q"])}</a>'
        for i, r in enumerate(questions_results))
    qs = ""
    for i, r in enumerate(questions_results):
        qs += (f'<div class="q" id="q{i}"><h2>{i+1}. '
               f'{html.escape(r["q"])}</h2>'
               f'{_render_answer(r["answer"], videos)}</div>')
    trs = "".join(_render_transcript(vk, v) for vk, v in enumerate(videos))
    when = time.strftime("%B %-d, %Y") if os.name != "nt" else \
        time.strftime("%B %d, %Y")
    return f"""<!doctype html><html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>{html.escape(project_name)} — research report</title>
<style>{_CSS}</style></head><body><div class="wrap">
<h1>{html.escape(project_name)}</h1>
<div class="meta">Research report · {len(videos)} videos · """ \
        f"""{len(questions_results)} questions · {when}</div>
<div class="toc"><strong>Questions</strong>{toc}</div>
{qs}
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


def run(cfg, name, questions, video_items, log=lambda s: None,
        cancel=lambda: False, progress=lambda ph, i, n: None,
        on_done=lambda path: None):
    """Full research project: download+transcribe each video into the project
    folder, then synthesize and write report.html. Returns the report path."""
    from . import pipeline
    folder = project_dir(cfg, name)
    log(f"Research project: {name}")
    log(f"  folder: {folder}")
    collected = []

    total = len(video_items)
    for i, item in enumerate(video_items):
        if cancel():
            break
        it = dict(item)
        it["folder"] = folder
        it["save_desc"] = True
        it["save_comments"] = False
        it["questions"] = []          # no per-video Q&A; we synthesize instead
        progress("transcribe", i + 1, total)
        log(f"→ [{i+1}/{total}] {it.get('title') or it.get('url','')}")
        pipeline.process_video(cfg, it, log, cancel,
                               progress=lambda ph: None,
                               sink=collected.append)

    if cancel():
        log("  cancelled.")
        return ""
    if not collected:
        log("  no transcripts produced — nothing to synthesize.")
        return ""

    log(f"  synthesizing across {len(collected)} transcripts…")
    results = synthesize(
        cfg, questions, collected, log, cancel,
        progress=lambda d, t: progress("synthesize", d, t))

    html_doc = build_report(name, results, collected)
    rpath = os.path.join(folder, "report.html")
    with open(rpath, "w", encoding="utf-8") as f:
        f.write(html_doc)
    # save a manifest for the record
    try:
        man = {"name": name, "created": time.time(), "questions": questions,
               "videos": [{"name": c["name"], "url": c["url"],
                           "path": c.get("path", "")} for c in collected]}
        with open(os.path.join(folder, "project.json"), "w",
                  encoding="utf-8") as f:
            json.dump(man, f, indent=2)
    except Exception:
        pass
    log(f"  report written: {rpath}")
    on_done(rpath)
    return rpath
