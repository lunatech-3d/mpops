"""Shared display formatting for structured job service addresses."""

from __future__ import annotations

import re
from collections.abc import Mapping
from typing import Any


_EMPTY_ADDRESS_VALUES = {"", "none", "null"}
_OPERATIONAL_METADATA = re.compile(
    r"^(?:capture|property|service)\s*type\s*:", re.IGNORECASE
)


def _address_component(value: Any) -> str:
    """Return a display-safe component without changing its stored value."""
    text = re.sub(r"\s+", " ", str(value or "")).strip()
    return "" if text.casefold() in _EMPTY_ADDRESS_VALUES else text


def _raw_address(value: Any) -> str:
    """Clean separators in a preserved combined address for display."""
    parts = [_address_component(part) for part in str(value or "").split(",")]
    return ", ".join(part for part in parts if part and not _is_operational_metadata(part))


def _is_operational_metadata(value: str) -> bool:
    """Identify labeled job metadata that was imported into an address field."""
    return bool(_OPERATIONAL_METADATA.match(value))


def format_service_address(job: Mapping[str, Any]) -> str:
    """Format a job's service address, preferring complete structured fields.

    A preserved combined address is used when any ordinary required US address
    component is absent. If neither source is complete, the available structured
    components are still returned rather than inventing missing information.
    """
    address_1 = _address_component(job.get("address_1"))
    address_2 = _address_component(job.get("address_2"))
    if _is_operational_metadata(address_2):
        # Preserve the contaminated stored value for later data cleanup, but do
        # not present it as a suite/unit in customer-facing output.
        address_2 = ""
    city = _address_component(job.get("city"))
    state = _address_component(job.get("state"))
    postal_code = _address_component(job.get("postal_code"))

    # Once an operator has corrected the normalized street address, it is the
    # operational source of truth even when the imported source omitted a ZIP code.
    # Falling back to the raw value merely because one component is absent would
    # make a protected correction appear to have been ignored.
    if address_1:
        locality = " ".join(part for part in (state, postal_code) if part)
        return ", ".join(part for part in (address_1, address_2, city, locality) if part)

    raw = _raw_address(job.get("capture_address_raw"))
    if raw:
        return raw

    locality = " ".join(part for part in (state, postal_code) if part)
    return ", ".join(part for part in (address_1, address_2, city, locality) if part)
