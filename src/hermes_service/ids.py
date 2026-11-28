"""Server-generated identifiers.

Formats are fixed by CHECK constraints in migration 0001:
``tkt_<32 hex>`` / ``run_<32 hex>`` (128 bits from the OS CSPRNG via ``secrets``).
Identifiers are never client-supplied for creation.
"""

from __future__ import annotations

import re
import secrets

_TICKET_RE = re.compile(r"tkt_[0-9a-f]{32}")
_RUN_RE = re.compile(r"run_[0-9a-f]{32}")


def new_ticket_id() -> str:
    return "tkt_" + secrets.token_hex(16)


def new_run_id() -> str:
    return "run_" + secrets.token_hex(16)


def is_ticket_id(value: object) -> bool:
    """True only for a well-formed ticket id (``fullmatch``: no trailing-newline slack)."""
    return isinstance(value, str) and _TICKET_RE.fullmatch(value) is not None


def is_run_id(value: object) -> bool:
    return isinstance(value, str) and _RUN_RE.fullmatch(value) is not None
