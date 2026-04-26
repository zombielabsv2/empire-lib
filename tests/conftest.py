"""Test config — eat our own dog food by activating the live-API guard."""
from __future__ import annotations

# Activates autouse session-scope fixture that blocks live API hosts.
from empire.test.guards import block_live_api_hosts  # noqa: F401
