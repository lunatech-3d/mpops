import tkinter as tk
import unittest
from unittest.mock import MagicMock

from app.ui.window_utils import (bind_maximize_shortcut, maximize_window, restore_window,
                                 should_start_maximized, toggle_maximized)


class WindowSizingTests(unittest.TestCase):
    def test_large_working_window_starts_maximized(self):
        window = MagicMock()
        window.winfo_width.return_value = 900
        window.winfo_reqwidth.return_value = 850
        window.winfo_height.return_value = 760
        window.winfo_reqheight.return_value = 700
        self.assertTrue(should_start_maximized(window))

    def test_small_dialog_keeps_normal_size(self):
        window = MagicMock()
        window.winfo_width.return_value = 480
        window.winfo_reqwidth.return_value = 500
        window.winfo_height.return_value = 320
        window.winfo_reqheight.return_value = 340
        self.assertFalse(should_start_maximized(window))

    def test_maximize_and_restore_use_window_manager_states(self):
        window = MagicMock()
        self.assertTrue(maximize_window(window))
        window.state.assert_called_with("zoomed")
        self.assertTrue(restore_window(window))
        window.state.assert_called_with("normal")

    def test_maximize_falls_back_to_zoomed_attribute(self):
        window = MagicMock()
        window.state.side_effect = tk.TclError("unsupported")
        self.assertTrue(maximize_window(window))
        window.attributes.assert_called_with("-zoomed", True)

    def test_toggle_restores_a_zoomed_window(self):
        window = MagicMock()
        window.state.side_effect = ["zoomed", None]
        self.assertFalse(toggle_maximized(window))
        window.state.assert_called_with("normal")

    def test_f11_binding_preserves_existing_bindings(self):
        window = MagicMock()
        bind_maximize_shortcut(window)
        args, kwargs = window.bind.call_args
        self.assertEqual(args[0], "<F11>")
        self.assertEqual(kwargs["add"], "+")


if __name__ == "__main__":
    unittest.main()
