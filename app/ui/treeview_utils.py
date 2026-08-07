"""Shared, widget-independent helpers for sortable Treeviews."""

import re


BLANK_DISPLAY_VALUES = (None, "", "—")


def is_blank_tree_value(value):
    """Return whether *value* represents an unavailable Treeview cell."""
    return value in BLANK_DISPLAY_VALUES


def natural_sort_key(value):
    """Sort mixed text and numeric identifiers naturally and case-insensitively."""
    return tuple(
        int(part) if part.isdigit() else part.casefold()
        for part in re.split(r"(\d+)", str(value or ""))
    )


def ordered_tree_items(items, value_for, descending=False):
    """Order populated items while consistently leaving unavailable values last.

    ``value_for`` returns ``(raw_value, sort_key)`` so callers can use typed keys
    without losing the common handling for blank cells and em-dash placeholders.
    """
    populated = []
    blank = []
    for item in items:
        raw_value, sort_key = value_for(item)
        (blank if is_blank_tree_value(raw_value) else populated).append(
            (sort_key, item)
        )
    populated.sort(key=lambda pair: pair[0], reverse=descending)
    return [item for _key, item in populated] + [item for _key, item in blank]
