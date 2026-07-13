"""One-click updates. Checks the GitHub Releases page, and if a newer
version exists, downloads the installer and launches it."""

import os
import re
import tempfile

# Pro edition: version check against the public repo (same version numbers),
# but downloads come from your Lemon Squeezy library, not public GitHub.
REPO = "CFRE-dotcom/echoquill"
PRO_EDITION = True
API_LATEST = f"https://api.github.com/repos/{REPO}/releases/latest"


def _ver_tuple(v: str):
    nums = re.findall(r"\d+", v or "")
    return tuple(int(n) for n in nums[:3]) or (0,)


MANIFEST_URL = "https://echo-quill.com/pro-version.json"
RELEASES_PAGE = "https://github.com/CFRE-dotcom/echoquill-pro/releases/latest"


def check():
    """Version comes from the public manifest (no login needed). Returns
    (latest_version, releases_page) if newer, else None."""
    from . import __version__
    import requests
    r = requests.get(MANIFEST_URL, timeout=15)
    r.raise_for_status()
    latest = (r.json() or {}).get("version", "")
    if _ver_tuple(latest) <= _ver_tuple(__version__):
        return None
    return (latest.lstrip("v"), RELEASES_PAGE)
    url = None
    for a in data.get("assets", []):
        if a.get("name", "").endswith("Setup.exe"):
            url = a.get("browser_download_url")
            break
    if not url:  # fall back to portable exe
        for a in data.get("assets", []):
            if a.get("name", "").endswith(".exe"):
                url = a.get("browser_download_url")
                break
    return (latest.lstrip("v"), url) if url else None


def download_and_run(url: str, status_cb=lambda s: None) -> bool:
    """Download the installer to temp and launch it. Returns True on launch."""
    import requests
    status_cb("Downloading update…")
    path = os.path.join(tempfile.gettempdir(), os.path.basename(url))
    with requests.get(url, stream=True, timeout=60) as r:
        r.raise_for_status()
        with open(path, "wb") as f:
            for chunk in r.iter_content(chunk_size=1 << 16):
                f.write(chunk)
    status_cb("Updating…")
    import subprocess
    import sys
    args = [path, "/VERYSILENT", "/NORESTART", "/FORCECLOSEAPPLICATIONS"]
    # per-user installs must not silently stall on an elevation prompt
    if "appdata" in sys.executable.lower():
        args.append("/CURRENTUSER")
    try:
        subprocess.Popen(args)
    except Exception:
        os.startfile(path)   # fall back to the visible wizard
        return True
    # belt & suspenders: a detached watchdog restarts EchoQuill in ~75s
    # if the installer's own relaunch didn't (only if it's not running).
    try:
        exe = sys.executable
        cmd = ('ping -n 75 127.0.0.1 >nul & '
               'tasklist /FI "IMAGENAME eq EchoQuill.exe" 2>nul '
               '| find /I "EchoQuill.exe" >nul '
               f'|| start "" "{exe}"')
        subprocess.Popen(["cmd", "/c", cmd],
                         creationflags=0x08000008)  # no window, detached
    except Exception:
        pass
    return True
