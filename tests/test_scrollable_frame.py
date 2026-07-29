"""Nonvisual tests for shared scrollable-form behavior."""

import unittest

from app.ui.scrollable_frame import mousewheel_units


class ScrollableFrameTests(unittest.TestCase):
    def test_mousewheel_units_supports_windows_and_macos(self):
        self.assertEqual(mousewheel_units(120), -1)
        self.assertEqual(mousewheel_units(-240), 2)
        self.assertEqual(mousewheel_units(1), -1)
        self.assertEqual(mousewheel_units(-1), 1)
        self.assertEqual(mousewheel_units(0), 0)

    def test_mousewheel_units_supports_x11_buttons(self):
        self.assertEqual(mousewheel_units(0, 4), -1)
        self.assertEqual(mousewheel_units(0, 5), 1)


if __name__ == "__main__":
    unittest.main()
