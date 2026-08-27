"""Gateway boot config: the data-source registry env contract."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from src.config import load_config


def test_registry_file_registers_extra_sources(monkeypatch, tmp_path: Path) -> None:
    registry = tmp_path / "data-sources.json"
    registry.write_text(
        json.dumps(
            {
                "dataSources": [
                    {
                        "id": "openmrs-hiv",
                        "label": "OpenMRS HIV/ART program",
                        "connectionUri": "hive2://u:p@spark-thriftserver:10000/hiv",
                        "dialect": "spark",
                    }
                ]
            }
        )
    )
    monkeypatch.setenv("CATALYST_DATA_SOURCES_PATH", str(registry))
    config = load_config()
    assert [s.source_id for s in config.data_sources] == ["openelis", "openmrs-hiv"]
    extra = config.data_sources[1]
    assert extra.label == "OpenMRS HIV/ART program"
    assert extra.connection_uri == "hive2://u:p@spark-thriftserver:10000/hiv"
    assert extra.dialect == "spark"
    # A source that names no adapter uses the one implementing its dialect.
    assert extra.dialect_adapter == "spark"


def test_unset_registry_path_yields_default_source_only(monkeypatch) -> None:
    monkeypatch.delenv("CATALYST_DATA_SOURCES_PATH", raising=False)
    config = load_config()
    assert [s.source_id for s in config.data_sources] == ["openelis"]
    assert config.default_data_source_id == "openelis"


def test_query_profile_default_is_configurable(monkeypatch) -> None:
    monkeypatch.setenv(
        "CATALYST_QUERY_PROFILE_ID",
        "catalyst-query-gemma-4-12b-qwen2.5-14b-checked",
    )

    config = load_config()

    assert (
        config.default_query_profile_id
        == "catalyst-query-gemma-4-12b-qwen2.5-14b-checked"
    )


def test_set_but_missing_registry_path_fails_boot(monkeypatch, tmp_path: Path) -> None:
    """A configured-but-absent registry is an operator error, not a silent
    fallback to single-source mode (which would hide a broken deployment)."""
    monkeypatch.setenv(
        "CATALYST_DATA_SOURCES_PATH", str(tmp_path / "does-not-exist.json")
    )
    with pytest.raises(FileNotFoundError, match="CATALYST_DATA_SOURCES_PATH"):
        load_config()
