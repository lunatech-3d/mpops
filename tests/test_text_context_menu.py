"""Unit coverage for the application-wide Tk clipboard context menu."""

import tkinter as tk
from tkinter import ttk
import unittest
from unittest import mock

from app.ui.text_context_menu import TextContextMenu, _is_text_widget


class _WidgetMixin:
    widget_class = ""

    def winfo_class(self):
        return self.widget_class


class _CustomEntry(_WidgetMixin, ttk.Entry):
    widget_class = "TEntry"


class _CustomText(_WidgetMixin, tk.Text):
    widget_class = "Text"


class _CustomCombobox(_WidgetMixin, ttk.Combobox):
    widget_class = "TCombobox"


class _CustomSpinbox(_WidgetMixin, ttk.Spinbox):
    widget_class = "TSpinbox"


class TextWidgetDetectionTests(unittest.TestCase):
    """Detection must include subclasses without creating a display-backed Tk."""

    def test_all_supported_widget_subclasses_are_detected(self):
        for widget_type in (_CustomEntry, _CustomText, _CustomCombobox, _CustomSpinbox):
            with self.subTest(widget_type=widget_type.__name__):
                self.assertTrue(_is_text_widget(widget_type.__new__(widget_type)))

    def test_tk_class_fallback_covers_wrapped_entry_and_spinbox(self):
        class WrappedWidget:
            def __init__(self, widget_class):
                self.widget_class = widget_class

            def winfo_class(self):
                return self.widget_class

        self.assertTrue(_is_text_widget(WrappedWidget("Entry")))
        self.assertTrue(_is_text_widget(WrappedWidget("Spinbox")))
        self.assertFalse(_is_text_widget(WrappedWidget("Treeview")))

    def test_editability_distinguishes_normal_readonly_and_disabled_ttk_states(self):
        class StatefulWidget:
            def __init__(self, state, active_states=()):
                self.state = state
                self.active_states = active_states

            def cget(self, option):
                self.assert_state_option = option
                return self.state

            def instate(self, states):
                return any(state in self.active_states for state in states)

        with mock.patch.object(ttk, "Widget", StatefulWidget):
            self.assertTrue(TextContextMenu._editable(StatefulWidget("normal")))
            self.assertFalse(TextContextMenu._editable(StatefulWidget("readonly", ("readonly",))))
            self.assertFalse(TextContextMenu._editable(StatefulWidget("disabled", ("disabled",))))


if __name__ == "__main__":
    unittest.main()
