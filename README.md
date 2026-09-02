# EchoQuill Pro — PRIVATE (never make this repo public)

<p align="center"><img src="assets/hero.png" alt="EchoQuill" width="820"></p>

**EchoQuill — capture, transcribe & summarize anything you hear or see.**

The paid edition. Everything in the free app, plus the power features below.

## Pro-only features
- **Meeting / Record** — record what you HEAR on your PC (calls, webinars, any playing video), optionally your mic, and **capture the full screen as an MP4** — then transcribe & summarize locally. Right-click the pill → Meeting / Record.
- **Unlimited video transcription** (free is capped at 5)
- **Drag-and-drop upload** — drop any audio/video file onto the transcriber and it auto-transcribes
- **Ask AI about any video** — ask a question, get an answer grounded only in that video's transcript, with timestamps; save the Q&A alongside the transcript
- **Unlimited clip library** with paging (50 per page) and a **★ Favorites** tab
- **Priority support**

## Also unique to EchoQuill (free & pro)
**Skool support** (paste the lesson video link or a signed .m3u8 — auto Referer — or sign in via browser for member-only). **Keep audio/video** downloads, **name any transcript**, and everything saves under an organized `Documents/EchoQuill/` folder (Transcriptions, Meetings, …).

Click a clip to paste it straight at your cursor · edit the text of any past transcription and save it · full history search, multi-select and delete. No other dictation app does these.

## Everything from the free edition
Live dictation into any app · voice commands · rewrite-by-voice · batch URL transcription with Stop · timestamped transcript search · clips tray with drag-and-drop paste · learning dictionary (add/edit/remove) · multi-provider AI cleanup & formatting (Claude, OpenAI, Groq, Ollama, Ollama Cloud, DeepSeek, Qwen, Z.AI) · light/dark/system theme · optional audio history with budget + ZIP export · daily/weekly/monthly stats · administrator mode · self-update · first-run tour. Keys stored in Windows Credential Manager. 100% local unless you enable a cloud AI provider.

## Release flow
1. Bump the version in `echoquill/__init__.py` and `installer.iss`.
2. `git tag vX.Y.Z && git push origin main vX.Y.Z`
3. GitHub Actions builds `EchoQuill-Pro-Setup.exe` (private release).
4. Update `pro-version.json` in the `echo-quill-site` repo so the in-app update check reports the new version.
5. Upload the installer as the file on the Lemon Squeezy product → buyers get their key + download automatically.

Public repo (free edition): https://github.com/CFRE-dotcom/echoquill
Website: https://echo-quill.com
