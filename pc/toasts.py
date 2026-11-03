"""Private-content-free native Windows toasts; sound is handled separately."""

import sys
import time
from xml.sax.saxutils import escape

APP_ID = "OpenBuddy.OpenCode"
MESSAGES = {
    "permission": ("OpenBuddy — Autorisation requise", "OpenCode attend votre validation. Revenez dans OpenCode pour vérifier la demande."),
    "question": ("OpenBuddy — Réponse attendue", "OpenCode vous pose une question. Revenez dans OpenCode pour répondre."),
    "success": ("OpenBuddy — Réponse prête", "OpenCode a terminé son exécution."),
    "error": ("OpenBuddy — Erreur", "L'exécution OpenCode a échoué. Consultez la session pour les détails."),
}


def toast_xml(title, body):
    return ('<toast duration="short"><visual><binding template="ToastGeneric">'
            '<text>%s</text><text>%s</text></binding></visual><audio silent="true"/></toast>'
            % (escape(title), escape(body)))


def register_identity():
    """Register only our per-user app identity; never change notification policy."""
    import winreg

    path = r"Software\Classes\AppUserModelId" + "\\" + APP_ID
    with winreg.CreateKeyEx(winreg.HKEY_CURRENT_USER, path, 0, winreg.KEY_READ | winreg.KEY_WRITE) as key:
        for name, value, kind in (("DisplayName", "OpenBuddy", winreg.REG_SZ),
                                  ("ShowInSettings", 1, winreg.REG_DWORD)):
            try:
                winreg.QueryValueEx(key, name)
            except FileNotFoundError:
                winreg.SetValueEx(key, name, 0, kind, value)


class WindowsToastPoster:
    def __init__(self):
        from winrt.windows.ui.notifications import ToastNotificationManager

        register_identity()
        self.notifier = ToastNotificationManager.create_toast_notifier_with_id(APP_ID)

    def __call__(self, title, body):
        from winrt.windows.data.xml.dom import XmlDocument
        from winrt.windows.ui.notifications import ToastNotification

        # Windows can reject .setting for a newly registered unpackaged identity.
        # show() still applies the user's notification and focus-assist policy.
        document = XmlDocument()
        document.load_xml(toast_xml(title, body))
        toast = ToastNotification(document)
        # Replace our previous item instead of filling the notification center.
        toast.tag = "state"
        toast.group = "OpenBuddy"
        self.notifier.show(toast)


class DesktopNotifier:
    def __init__(self, disabled=False, poster=None, clock=time.monotonic):
        self.enabled = not disabled and (poster is not None or sys.platform == "win32")
        self.poster = poster
        self.clock = clock
        self.previous = None
        self.last_posted = {}
        self.error = None
        self.warned = False

    def post(self, title, body):
        if not self.enabled:
            return False
        try:
            if self.poster is None:
                self.poster = WindowsToastPoster()
            self.poster(title, body)
            self.error = None
            return True
        except (ImportError, OSError, RuntimeError, ValueError) as error:
            self.error = type(error).__name__
            if not self.warned:
                print("Windows notification unavailable; check notification settings. Sound and BLE remain active.", flush=True)
                self.warned = True
            return False

    def observe(self, state):
        if state == self.previous:
            return False
        self.previous = state
        if not self.enabled or state not in MESSAGES:
            return False
        now = self.clock()
        if now - self.last_posted.get(state, -float("inf")) < 2:
            return False
        self.last_posted[state] = now
        return self.post(*MESSAGES[state])
