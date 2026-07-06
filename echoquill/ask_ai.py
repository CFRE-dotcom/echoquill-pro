"""Pro: Ask AI about a transcript - answers grounded in the video's own words,
with timestamps cited, using whichever AI provider the user configured."""


def _bearer(cfg: dict) -> str:
    if cfg.get("ai_auth_method", "api_key") == "oauth":
        from . import oauth, config as cfgmod
        return oauth.get_access_token(cfg, save_cb=cfgmod.save)
    return (cfg.get("ai_api_key", "") or "").strip()


def ask(question: str, segments, cfg: dict) -> str:
    """segments: list of (seconds, text). Returns the answer or an error note."""
    import requests
    base_url = (cfg.get("ai_base_url", "") or "").rstrip("/")
    if not base_url:
        return "Set up AI Enhancement first (Settings → AI Enhancement)."

    def stamp(sec):
        sec = int(sec or 0)
        h, m, s2 = sec // 3600, (sec % 3600) // 60, sec % 60
        return f"{h}:{m:02d}:{s2:02d}" if h else f"{m:02d}:{s2:02d}"

    lines = [f"[{stamp(sec)}] {text}" for sec, text in segments]
    context = "\n".join(lines)
    if len(context) > 24000:                      # keep within model limits
        context = context[:12000] + "\n[...]\n" + context[-12000:]

    system = (
        "You answer questions about a video using ONLY its transcript below. "
        "Cite the timestamp(s) where the answer appears, like [12:34]. "
        "If the transcript doesn't contain the answer, say exactly: "
        "\"The video doesn't cover that.\" Do not use outside knowledge.\n\n"
        "TRANSCRIPT:\n" + context)
    try:
        r = requests.post(
            f"{base_url}/chat/completions",
            headers={"Authorization": f"Bearer {_bearer(cfg)}",
                     "Content-Type": "application/json"},
            json={"model": __import__("echoquill.config", fromlist=["api_model"]).api_model(cfg.get("ai_model", "gpt-4o-mini")),
                  "messages": [{"role": "system", "content": system},
                               {"role": "user", "content": question}],
                  "temperature": 0.1, "keep_alive": "30m"},
            timeout=45)
        r.raise_for_status()
        return r.json()["choices"][0]["message"]["content"].strip()
    except Exception as e:
        return f"AI request failed: {e}"
