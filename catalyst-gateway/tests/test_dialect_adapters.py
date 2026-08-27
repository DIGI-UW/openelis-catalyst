"""The dialect adapter seam.

Configuration names an adapter and the Gateway asks it; these tests hold that
line from both sides -- the production adapter must agree with the contracts
its output has to satisfy, and a source pointed at a different adapter must
reach the same code.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from src.catalyst.dialects import (
    SPARK,
    UnknownDialectAdapter,
    resolve_dialect_adapter,
)
from tests.fixture_dialect import FIXTURE


CONTRACTS = Path(__file__).resolve().parents[2] / "docs" / "contracts"


def _editor_catalog_logical_types() -> set[str]:
    schema = json.loads(
        (CONTRACTS / "catalyst-workbench-editor-catalog-v1.schema.json").read_text()
    )
    return set(schema["$defs"]["column"]["properties"]["logicalType"]["enum"])


# Every Spark type a ViewDefinition export actually produces, plus the complex
# ones the flat views are full of.
SPARK_TYPES = [
    "BOOLEAN", "TINYINT", "SMALLINT", "INT", "BIGINT",
    "FLOAT", "DOUBLE", "DECIMAL(10,2)", "STRING", "VARCHAR(64)", "CHAR(3)",
    "DATE", "TIMESTAMP", "TIMESTAMP_NTZ", "BINARY", "INTERVAL",
    "ARRAY<STRING>", "STRUCT<code:STRING>", "MAP<STRING,STRING>",
    "some_type_spark_adds_later",
]


@pytest.mark.parametrize("database_type", SPARK_TYPES)
def test_every_spark_logical_type_is_one_the_editor_contract_accepts(database_type):
    """A type the contract rejects makes the whole catalog unavailable.

    This is not hypothetical: mapping Spark's DOUBLE to "number" produced a
    503 on the real stack, because the editor catalog enum has no "number".
    The adapter and the contract have to agree, so assert it directly.
    """
    assert SPARK.logical_type(database_type) in _editor_catalog_logical_types()


def test_a_source_can_be_served_by_an_adapter_this_build_does_not_ship():
    """Configuration selects the adapter; the Gateway never names an engine."""
    assert resolve_dialect_adapter("spark") is SPARK
    assert resolve_dialect_adapter("fixture") is FIXTURE
    # A different grammar, not Spark's behavior under another name.
    assert FIXTURE.sqlglot_dialect != SPARK.sqlglot_dialect
    assert FIXTURE.quote_identifier("x") != SPARK.quote_identifier("x")


def test_an_unknown_adapter_fails_loudly_with_what_this_build_has():
    with pytest.raises(UnknownDialectAdapter) as caught:
        resolve_dialect_adapter("postgresql")
    assert "spark" in str(caught.value)


def test_spark_records_the_bound_it_cannot_enforce():
    """Where Spark cannot honor a guarantee, it is surfaced, not emulated."""
    assert SPARK.row_bound.enforced
    assert SPARK.read_only.enforced
    # Spark's thriftserver has no server-side statement timeout to set.
    assert not SPARK.time_limit.enforced
    assert any("time limit" in line for line in SPARK.limitations())


def test_identifier_quoting_escapes_the_quote_character():
    assert SPARK.quote_identifier("observation_flat") == "`observation_flat`"
    assert SPARK.quote_identifier("odd`name") == "`odd``name`"
