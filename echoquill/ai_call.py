"""One place that actually talks to the AI provider.

Handles BOTH shapes:
- OpenAI-compatible  -> POST {base}/chat/completions   (OpenAI, Groq, DeepSeek,
  local Ollama at :11434/v1, etc.)
- Ollama Cloud native -> POST https://ollama.com/api/chat   (ollama.com does NOT
  serve /v1, so the OpenAI path 404s — this is the fix for that.)
"""


def _bearer(cfg: dict) -> str:
    if cfg.get("ai_auth_method", "api_key") == "oauth":
        from . import oauth, config as cfgmod
        return oauth.get_access_token(cfg, save_cb=cfgmod.save)
    return (cfg.get("ai_api_key", "") or "").strip()


def chat(cfg: dict, system: str, user: str, temperature: float = 0.3,
         timeout: int = 180):
    """Returns (ok, text). text is the reply on success, else an error note."""
    from .config import AI_PROVIDERS, api_model as _am
    _prov = AI_PROVIDERS.get(cfg.get("ai_provider", ""), {})
    if _prov.get("backend") == "codex_cli":
        from . import codex_cli
        return codex_cli.chat(system, user,
                              model=_am(cfg.get("ai_model", "gpt-5.5")) or "gpt-5.5",
                              timeout=max(timeout, 180))
    # Use Python's BUILT-IN HTTP (urllib), NOT requests. requests rides on
    # urllib3, which yt-dlp monkey-patches ('Urllib3PercentREOverride'); once
    # yt-dlp runs in-process that patch breaks every requests call, which was
    # failing the AI Q&A with "object has no attribute 'sub'". Stdlib urllib is
    # independent of urllib3, so the AI calls can't be broken by the video
    # engine. This is the redundancy/isolation.
    import json as _json
    import urllib.request
    import urllib.error
    base = (cfg.get("ai_base_url", "") or "").rstrip("/")
    if not base:
        return (False, "Set up AI Enhancement first (Settings > AI Enhancement).")
    from .config import api_model
    model = api_model(cfg.get("ai_model", "gpt-4o-mini"))
    key = _bearer(cfg)
    headers = {"Authorization": f"Bearer {key}", "Content-Type": "application/json"}
    messages = [{"role": "system", "content": system},
                {"role": "user", "content": user}]
    native = base.endswith("/api") or (("ollama.com" in base) and not base.endswith("/v1"))

    def _post(url, payload):
        data = _json.dumps(payload).encode("utf-8")
        req = urllib.request.Request(url, data=data, method="POST")
        for k, v in headers.items():
            req.add_header(k, v)
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            raw = resp.read().decode("utf-8", "replace")
        return _json.loads(raw) if raw else {}

    try:
        if native:
            root = base[:-4] if base.endswith("/api") else base
            # Ollama defaults num_ctx to 4096 tokens and silently TRUNCATES
            # anything longer. Set it explicitly so batched prompts aren't cut.
            try:
                _nctx = int(cfg.get("ai_num_ctx", 16384))
            except Exception:
                _nctx = 16384
            j = _post(root + "/api/chat",
                      {"model": model, "messages": messages, "stream": False,
                       "options": {"temperature": temperature,
                                   "num_ctx": max(2048, _nctx)}})
            out = ((j or {}).get("message") or {}).get("content", "").strip()
        else:
            j = _post(base + "/chat/completions",
                      {"model": model, "messages": messages,
                       "temperature": temperature, "keep_alive": "30m"})
            out = j["choices"][0]["message"]["content"].strip()
        return (True, out) if out else (False, "AI returned nothing.")
    except urllib.error.HTTPError as e:
        try:
            body = e.read().decode("utf-8", "replace")[:200]
        except Exception:
            body = ""
        return (False, f"AI request failed: HTTP {e.code} {body}".strip())
    except Exception as e:
        return (False, f"AI request failed: {e}")
