"""Text-to-speech - turn text (or a document) into spoken audio.

Pro feature. Uses YOUR ElevenLabs account (bring your own API key). The key is
kept in Windows Credential Manager, same as your other secrets. Long text is
split at sentence boundaries and the parts are stitched back together with the
ffmpeg that's already bundled for the Meeting recorder.
"""

import os
import re
import subprocess
import sys
import tempfile

ELEVEN_BASE = "https://api.elevenlabs.io/v1"
DEFAULT_VOICE = "21m00Tcm4TlvDq8ikWAM"   # "Rachel" - a stock ElevenLabs voice
DEFAULT_MODEL = "eleven_multilingual_v2"
CHUNK_LIMIT = 2400                        # characters per request


def _no_window():
    return getattr(subprocess, "CREATE_NO_WINDOW", 0) if sys.platform == "win32" else 0


def _ffmpeg():
    import imageio_ffmpeg
    return imageio_ffmpeg.get_ffmpeg_exe()


def _key(cfg):
    return (cfg.get("elevenlabs_api_key") or "").strip()


def list_voices(cfg):
    """[(name, voice_id), ...] from the user's ElevenLabs account (or [])."""
    import requests
    key = _key(cfg)
    if not key:
        return []
    r = requests.get(f"{ELEVEN_BASE}/voices",
                     headers={"xi-api-key": key}, timeout=30)
    if r.status_code != 200:
        raise RuntimeError(_friendly(r))
    data = r.json()
    return [(v.get("name", ""), v.get("voice_id", ""))
            for v in data.get("voices", []) if v.get("voice_id")]


def _chunks(text, limit=CHUNK_LIMIT):
    text = (text or "").strip()
    if len(text) <= limit:
        return [text] if text else []
    parts, cur = [], ""
    for sent in re.split(r'(?<=[.!?])\s+', text):
        if len(sent) > limit:                      # hard-split a huge sentence
            if cur:
                parts.append(cur.strip()); cur = ""
            for i in range(0, len(sent), limit):
                parts.append(sent[i:i + limit])
            continue
        if len(cur) + len(sent) + 1 > limit and cur:
            parts.append(cur.strip()); cur = ""
        cur += (" " if cur else "") + sent
    if cur.strip():
        parts.append(cur.strip())
    return parts


def _synth_chunk(text, cfg, voice_id, out_path):
    import requests
    model = cfg.get("tts_model_id") or DEFAULT_MODEL
    body = {
        "text": text,
        "model_id": model,
        "voice_settings": {
            "stability": float(cfg.get("tts_stability", 0.5) or 0.5),
            "similarity_boost": float(cfg.get("tts_similarity", 0.75) or 0.75),
        },
    }
    url = f"{ELEVEN_BASE}/text-to-speech/{voice_id}?output_format=mp3_44100_128"
    r = requests.post(url, headers={"xi-api-key": _key(cfg),
                                    "accept": "audio/mpeg",
                                    "content-type": "application/json"},
                      json=body, timeout=180)
    if r.status_code != 200:
        raise RuntimeError(_friendly(r))
    with open(out_path, "wb") as f:
        f.write(r.content)


def synthesize_to_mp3(text, cfg, voice_id, out_mp3, status_cb=None):
    """Turn text into one mp3 file at out_mp3 (chunking + concat as needed)."""
    if not _key(cfg):
        raise RuntimeError("Add your ElevenLabs API key first.")
    voice_id = voice_id or cfg.get("tts_voice_id") or DEFAULT_VOICE
    parts = _chunks(text)
    if not parts:
        raise RuntimeError("Nothing to read - the text is empty.")
    tmpd = tempfile.mkdtemp(prefix="eq_tts_")
    files = []
    try:
        for i, p in enumerate(parts):
            if status_cb and len(parts) > 1:
                status_cb(f"Generating audio… ({i + 1}/{len(parts)})")
            fp = os.path.join(tmpd, f"part{i:03d}.mp3")
            _synth_chunk(p, cfg, voice_id, fp)
            files.append(fp)
        if len(files) == 1:
            import shutil
            shutil.copy2(files[0], out_mp3)
        else:
            _concat(files, out_mp3)
    finally:
        import shutil
        shutil.rmtree(tmpd, ignore_errors=True)
    return out_mp3


def _concat(mp3_files, out_mp3):
    listf = out_mp3 + ".list.txt"
    with open(listf, "w", encoding="utf-8") as f:
        for p in mp3_files:
            f.write("file '" + p.replace("'", "'\\''") + "'\n")
    subprocess.run([_ffmpeg(), "-y", "-f", "concat", "-safe", "0",
                    "-i", listf, "-c", "copy", out_mp3],
                   check=True, capture_output=True, creationflags=_no_window())
    try:
        os.remove(listf)
    except Exception:
        pass


def mp3_to_wav(mp3_path):
    """Convert an mp3 to a temp wav so winsound can play it. Returns wav path."""
    wav = mp3_path + ".play.wav"
    subprocess.run([_ffmpeg(), "-y", "-i", mp3_path, wav],
                   check=True, capture_output=True, creationflags=_no_window())
    return wav


def read_document(path):
    """Pull plain text out of .txt/.md/.docx/.pdf for reading aloud."""
    ext = os.path.splitext(path)[1].lower()
    if ext in (".txt", ".md", ".text", ""):
        with open(path, "r", encoding="utf-8", errors="replace") as f:
            return f.read()
    if ext == ".docx":
        try:
            import docx
        except Exception:
            raise RuntimeError("Reading .docx files needs the python-docx library.")
        d = docx.Document(path)
        return "\n".join(p.text for p in d.paragraphs)
    if ext == ".pdf":
        reader = None
        try:
            from pypdf import PdfReader
            reader = PdfReader(path)
        except Exception:
            try:
                from PyPDF2 import PdfReader
                reader = PdfReader(path)
            except Exception:
                raise RuntimeError("Reading .pdf files needs the pypdf library.")
        return "\n".join((pg.extract_text() or "") for pg in reader.pages)
    raise RuntimeError(f"Unsupported file type: {ext or 'unknown'}")


def _friendly(r):
    try:
        j = r.json()
        detail = j.get("detail")
        if isinstance(detail, dict):
            msg = detail.get("message") or detail.get("status") or str(detail)
        else:
            msg = str(detail)
    except Exception:
        msg = (r.text or "")[:200]
    if r.status_code in (401, 403):
        return "ElevenLabs rejected the API key - check it and try again."
    if r.status_code == 422:
        return f"ElevenLabs couldn't process that: {msg}"
    if r.status_code == 429:
        return "ElevenLabs quota/rate limit hit - check your plan usage."
    return f"ElevenLabs error {r.status_code}: {msg}"
