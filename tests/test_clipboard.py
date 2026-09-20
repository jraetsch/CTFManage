"""ctf index (and any other manual-step prompt) tries to copy the browser
snippet to the clipboard. This must be best-effort: no clipboard tool, a
headless session, or a failing tool must never turn into a crash or a
mangled snippet — the printed instructions are what actually matters.
"""

import subprocess
import unittest
from unittest.mock import patch

from ctf.cli import _copy_to_clipboard


class TestCopyToClipboard(unittest.TestCase):
    def test_returns_false_when_no_tool_is_on_path(self):
        with patch("shutil.which", return_value=None):
            self.assertFalse(_copy_to_clipboard("some snippet"))

    def test_uses_first_available_tool(self):
        def fake_which(name):
            return f"/usr/bin/{name}" if name == "xclip" else None

        with patch("shutil.which", side_effect=fake_which), \
             patch("subprocess.run") as run:
            run.return_value = subprocess.CompletedProcess([], 0)
            self.assertTrue(_copy_to_clipboard("some snippet"))

        run.assert_called_once()
        args, kwargs = run.call_args
        self.assertEqual(args[0], ["xclip", "-selection", "clipboard"])
        self.assertEqual(kwargs["input"], "some snippet")

    def test_falls_back_to_next_tool_on_failure(self):
        with patch("shutil.which", return_value="/usr/bin/tool"), \
             patch("subprocess.run") as run:
            run.side_effect = [
                subprocess.CalledProcessError(1, "wl-copy"),
                subprocess.CompletedProcess([], 0),
            ]
            self.assertTrue(_copy_to_clipboard("some snippet"))

        self.assertEqual(run.call_count, 2)

    def test_never_raises_when_every_tool_fails(self):
        with patch("shutil.which", return_value="/usr/bin/tool"), \
             patch("subprocess.run", side_effect=OSError("no display")):
            self.assertFalse(_copy_to_clipboard("some snippet"))


if __name__ == "__main__":
    unittest.main()
