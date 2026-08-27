"""Test-wide setup.

Registering the fixture dialect adapter here is what lets a test point a source
at a grammar the production build does not ship, so the connection and
execution code is proven to be driven by configuration rather than by a
built-in engine.
"""

from src.catalyst.dialects import register_dialect_adapter

from tests.fixture_dialect import FIXTURE

register_dialect_adapter(FIXTURE)
