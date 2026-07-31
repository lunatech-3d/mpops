"""Nonvisual tests for shared scrollable-form behavior."""

import unittest
from unittest.mock import patch

from app.ui.scrollable_frame import mousewheel_units, should_scroll_outer


class DummyCanvas:
    def __init__(self, scrollregion=""):
        self.scrollregion = scrollregion

    def cget(self, option):
        if option != "scrollregion":
            raise AssertionError(f"unexpected option: {option}")
        return self.scrollregion


class DummyText:
    pass


class DummyTreeview:
    pass


class DummyListbox:
    pass


class DummyControl:
    pass


class ScrollableFrameTests(unittest.TestCase):
    def test_mousewheel_units_supports_windows_deltas(self):
        self.assertEqual(mousewheel_units(120, platform="win32"), -1)
        self.assertEqual(mousewheel_units(-240, platform="win32"), 2)
        self.assertEqual(mousewheel_units(0, platform="win32"), 0)

    def test_mousewheel_units_preserves_small_macos_deltas(self):
        self.assertEqual(mousewheel_units(1, platform="darwin"), -1)
        self.assertEqual(mousewheel_units(-1, platform="darwin"), 1)
        self.assertEqual(mousewheel_units(0.25, platform="darwin"), -0.25)
        self.assertEqual(mousewheel_units(-0.5, platform="darwin"), 0.5)

    def test_mousewheel_units_supports_x11_buttons(self):
        self.assertEqual(mousewheel_units(0, 4, platform="linux"), -1)
        self.assertEqual(mousewheel_units(0, 5, platform="linux"), 1)

    def test_outer_canvas_and_regular_controls_scroll_outer_form(self):
        own_canvas = DummyCanvas("0 0 100 500")
        with patch("app.ui.scrollable_frame.tk.Canvas", DummyCanvas), patch(
            "app.ui.scrollable_frame.tk.Text", DummyText
        ), patch("app.ui.scrollable_frame.ttk.Treeview", DummyTreeview), patch(
            "app.ui.scrollable_frame.tk.Listbox", DummyListbox
        ):
            self.assertTrue(should_scroll_outer(own_canvas, own_canvas))
            self.assertTrue(should_scroll_outer(DummyControl(), own_canvas))
            self.assertTrue(should_scroll_outer(DummyCanvas(), own_canvas))

    def test_nested_scrollable_controls_keep_their_wheel_events(self):
        own_canvas = DummyCanvas("0 0 100 500")
        with patch("app.ui.scrollable_frame.tk.Canvas", DummyCanvas), patch(
            "app.ui.scrollable_frame.tk.Text", DummyText
        ), patch("app.ui.scrollable_frame.ttk.Treeview", DummyTreeview), patch(
            "app.ui.scrollable_frame.tk.Listbox", DummyListbox
        ):
            self.assertFalse(should_scroll_outer(DummyText(), own_canvas))
            self.assertFalse(should_scroll_outer(DummyTreeview(), own_canvas))
            self.assertFalse(should_scroll_outer(DummyListbox(), own_canvas))
            self.assertFalse(
                should_scroll_outer(DummyCanvas("0 0 100 500"), own_canvas)
            )


if __name__ == "__main__":
    unittest.main()
