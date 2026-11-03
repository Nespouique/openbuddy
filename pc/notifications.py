"""Non-blocking Windows sounds, once on entry to a notification state."""

import sys
import time

SOUND_ALIASES = {
    "permission": "SystemExclamation",
    "question": "SystemQuestion",
    "success": "SystemAsterisk",
    "error": "SystemHand",
}


def windows_play_sound(alias):
    import winreg
    import winsound

    if alias == "SystemQuestion":
        # Windows often leaves this event silent even in its default sound scheme.
        path = r"AppEvents\Schemes\Apps\.Default\SystemQuestion\.Current"
        try:
            with winreg.OpenKey(winreg.HKEY_CURRENT_USER, path) as key:
                configured, _ = winreg.QueryValueEx(key, "")
            if not configured:
                alias = "SystemExclamation"
        except OSError:
            alias = "SystemExclamation"
    winsound.PlaySound(alias, winsound.SND_ALIAS | winsound.SND_ASYNC)


class SoundNotifier:
    def __init__(self, muted=False, player=None, clock=time.monotonic):
        self.previous = None
        self.last_played = {}
        self.clock = clock
        self.warned = False
        self.player = player
        if self.player is None and not muted and sys.platform == "win32":
            self.player = windows_play_sound
        self.enabled = not muted and self.player is not None

    def observe(self, state):
        if state == self.previous:
            return False
        self.previous = state
        if not self.enabled or state not in SOUND_ALIASES:
            return False
        now = self.clock()
        if now - self.last_played.get(state, -float("inf")) < 2:
            return False
        self.last_played[state] = now
        try:
            self.player(SOUND_ALIASES[state])
            return True
        except (RuntimeError, OSError):
            if not self.warned:
                print("Windows sound unavailable; display and BLE remain active", flush=True)
                self.warned = True
            return False
