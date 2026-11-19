"""The two server setups of the demo, expressed as benchmark variants.

Same verified flags as the benchmark: ``--tools ""``, ``--strict-mcp-config`` and
``ENABLE_TOOL_SEARCH=false``, so every tool definition is sent up front in both setups.
"""

from __future__ import annotations

from bench.harness.variants import VARIANTS, BenchVariant

# per-endpoint without --tool-count: every operation becomes a tool (102 in this API).
PER_ENDPOINT = BenchVariant("per-endpoint", "per-endpoint", 102)
SEARCH_CALL = VARIANTS["search-call"]
SETUPS: dict[str, BenchVariant] = {"per-endpoint": PER_ENDPOINT, "search-call": SEARCH_CALL}
