"""Reliable in-app notifications.

Windows tray balloons (pystray.notify) silently no-op on many setups, so the
real notification is a small always-on-top toast we draw ourselves. main.py
registers a handler on startup; anything can call notify.send(title, msg).
"""

_HANDLER = None
_BADGE = None
_DONE = None


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


def set_badge_handler(fn):
    global _BADGE
    _BADGE = fn


def badge(on):
    """Turn the 'new results' dot on the pill + tray on or off."""
    fn = _BADGE
    if not fn:
        return
    try:
        fn(bool(on))
    except Exception:
        pass


def set_done_handler(fn):
    global _DONE
    _DONE = fn


def done(on=True):
    """Turn the green 'research complete' dot on the pill on or off."""
    fn = _DONE
    if not fn:
        return
    try:
        fn(bool(on))
    except Exception:
        pass
