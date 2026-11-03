import unittest
from unittest.mock import patch
from xml.etree import ElementTree

from pc.toasts import DesktopNotifier, MESSAGES, toast_xml


class ToastTests(unittest.TestCase):
    def setUp(self):
        self.messages = []
        self.now = 0
        self.notifier = DesktopNotifier(poster=lambda title, body: self.messages.append((title, body)), clock=lambda: self.now)

    def test_waiting_notification_is_not_repeated_by_heartbeats(self):
        self.assertTrue(self.notifier.observe("question"))
        self.now = 10
        self.assertFalse(self.notifier.observe("question"))
        self.assertEqual(self.messages, [MESSAGES["question"]])

    def test_only_notification_states_are_displayed(self):
        for state in (None, "idle", "working", "thinking", "retry"):
            self.notifier.observe(state)
        self.assertEqual(self.messages, [])
        self.notifier.observe("permission")
        self.notifier.observe("error")
        self.assertEqual(len(self.messages), 2)

    def test_notifications_can_be_disabled_independently_of_audio(self):
        notifier = DesktopNotifier(disabled=True, poster=lambda *args: self.fail("No toast expected"))
        self.assertFalse(notifier.observe("question"))

    def test_quick_state_flapping_is_throttled(self):
        self.notifier.observe("question")
        self.notifier.observe("working")
        self.now = 1
        self.assertFalse(self.notifier.observe("question"))
        self.notifier.observe("working")
        self.now = 3
        self.assertTrue(self.notifier.observe("question"))

    def test_toast_xml_is_escaped_silent_and_has_no_actions(self):
        root = ElementTree.fromstring(toast_xml("Title <&>", "Body <&>"))
        self.assertEqual(root.find("./visual/binding/text").text, "Title <&>")
        self.assertEqual(root.find("audio").attrib["silent"], "true")
        self.assertIsNone(root.find("actions"))

    def test_notification_failure_does_not_break_other_channels(self):
        def broken(*args):
            raise OSError("notification platform unavailable")

        notifier = DesktopNotifier(poster=broken)
        with patch("builtins.print"):
            self.assertFalse(notifier.observe("question"))
        self.assertEqual(notifier.error, "OSError")
