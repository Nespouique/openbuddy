import unittest
from types import SimpleNamespace
from unittest.mock import MagicMock, Mock, patch

from pc.notifications import SoundNotifier, windows_play_sound


class NotificationTests(unittest.TestCase):
    def setUp(self):
        self.played = []
        self.now = 0
        self.notifier = SoundNotifier(player=self.played.append, clock=lambda: self.now)

    def test_waiting_sound_is_not_repeated_by_heartbeats(self):
        self.assertTrue(self.notifier.observe("permission"))
        for _ in range(10):
            self.now += 3
            self.assertFalse(self.notifier.observe("permission"))
        self.assertEqual(self.played, ["SystemExclamation"])

    def test_activity_and_offline_are_silent(self):
        for state in (None, "idle", "thinking", "working", "retry", None):
            self.notifier.observe(state)
        self.assertEqual(self.played, [])

    def test_success_and_error_have_distinct_aliases(self):
        self.notifier.observe("success")
        self.notifier.observe("error")
        self.assertEqual(self.played, ["SystemAsterisk", "SystemHand"])

    def test_short_state_flapping_is_throttled(self):
        self.notifier.observe("question")
        self.notifier.observe("thinking")
        self.now = 1
        self.assertFalse(self.notifier.observe("question"))
        self.notifier.observe("thinking")
        self.now = 3
        self.assertTrue(self.notifier.observe("question"))
        self.assertEqual(len(self.played), 2)

    def test_muted_notifications_never_play(self):
        notifier = SoundNotifier(muted=True, player=self.played.append)
        for state in ("question", "permission", "success", "error"):
            notifier.observe(state)
        self.assertEqual(self.played, [])

    def test_audio_failure_does_not_break_the_bridge(self):
        def broken(alias):
            raise RuntimeError("no sound device")

        notifier = SoundNotifier(player=broken)
        with patch("builtins.print"):
            self.assertFalse(notifier.observe("question"))
            self.assertFalse(notifier.observe("error"))
        self.assertTrue(notifier.warned)

    def test_silent_windows_question_uses_exclamation_without_registry_write(self):
        registry = SimpleNamespace(HKEY_CURRENT_USER=1, OpenKey=MagicMock(), QueryValueEx=Mock(return_value=("", 1)))
        sound = SimpleNamespace(PlaySound=Mock(), SND_ALIAS=1, SND_ASYNC=2)
        with patch.dict("sys.modules", {"winreg": registry, "winsound": sound}):
            windows_play_sound("SystemQuestion")
        sound.PlaySound.assert_called_once_with("SystemExclamation", 3)

    def test_configured_windows_question_keeps_its_custom_sound(self):
        registry = SimpleNamespace(HKEY_CURRENT_USER=1, OpenKey=MagicMock(), QueryValueEx=Mock(return_value=("custom.wav", 1)))
        sound = SimpleNamespace(PlaySound=Mock(), SND_ALIAS=1, SND_ASYNC=2)
        with patch.dict("sys.modules", {"winreg": registry, "winsound": sound}):
            windows_play_sound("SystemQuestion")
        sound.PlaySound.assert_called_once_with("SystemQuestion", 3)
