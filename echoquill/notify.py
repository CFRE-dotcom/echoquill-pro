"""Reliable in-app notifications.

Windows tray balloons (pystray.notify) silently no-op on many setups, so the
real notification is a small always-on-top toast we draw ourselves. main.py
registers a handler on startup; anything can call notify.send(title, msg).
"""

_HANDLER = None


def set_handler(fn):
    global _HANDLER
    _HANDLER = fn


def send(title, msg):
    fn = _HANDLER
    if not fn:
        return
    try:
        fn(title, msg)
    except Exception:
        pass
