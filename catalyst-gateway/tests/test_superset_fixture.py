from __future__ import annotations

import hashlib
import importlib.util
import json
import zipfile
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
FIXTURE_ROOT = ROOT / "tests/fixtures/superset-6.1"
FIXTURE_ZIP = FIXTURE_ROOT / "catalyst-dashboard-five-family.zip"
FIXTURE_METADATA = FIXTURE_ROOT / "fixture.json"


def _assets(archive: zipfile.ZipFile, segment: str) -> list[dict[str, object]]:
    return [
        json.loads(archive.read(name))
        for name in archive.namelist()
        if f"/{segment}/" in name
    ]


def test_canonical_superset_fixture_covers_every_supported_family() -> None:
    metadata = json.loads(FIXTURE_METADATA.read_text(encoding="utf-8"))
    bundle_bytes = FIXTURE_ZIP.read_bytes()

    assert metadata["schemaVersion"] == "catalyst.superset.fixture.v1"
    assert metadata["bundleDigest"] == hashlib.sha256(bundle_bytes).hexdigest()
    assert metadata["bytes"] == len(bundle_bytes)

    with zipfile.ZipFile(FIXTURE_ZIP) as archive:
        names = archive.namelist()
        roots = {name.split("/", 1)[0] for name in names}
        assert roots == {metadata["bundleRoot"]}
        assert all(not name.endswith("/") for name in names)
        assert names == sorted(names)

        databases = _assets(archive, "databases")
        datasets = _assets(archive, "datasets")
        charts = _assets(archive, "charts")
        dashboards = _assets(archive, "dashboards")
        manifest_name = next(
            name for name in names if name.endswith("/catalyst/manifest.json")
        )
        manifest = json.loads(archive.read(manifest_name))

    assert len(databases) == 1
    assert len(datasets) == 2
    assert len(charts) == 7
    assert len(dashboards) == 1
    database = databases[0]
    assert database["sqlalchemy_uri"] == (
        "postgresql+psycopg2://catalyst_readonly:demo-readonly-change-me@"
        "analytics-db:5432/catalyst_analytics"
    )
    assert database["allow_dml"] is False
    assert database["allow_ctas"] is False
    assert database["allow_cvas"] is False
    assert database["expose_in_sqllab"] is False

    assert {dataset["sql"] for dataset in datasets} == {
        "SELECT patient_id, result_value, result_unit, observed_at FROM "
        "analytics.lab_result_fact_v1 ORDER BY observed_at DESC, patient_id ASC "
        "LIMIT 10",
        "SELECT COUNT(*)::bigint AS result_count FROM analytics.lab_result_fact_v1",
    }

    by_title = {str(chart["slice_name"]): chart for chart in charts}
    assert by_title["Latest laboratory results"]["viz_type"] == "table"
    assert by_title["Laboratory result count"]["viz_type"] == "big_number_total"
    assert by_title["Laboratory result count"]["params"]["metric"]["aggregate"] == (
        "MAX"
    )
    assert by_title["Laboratory result count"]["params"]["metric"]["column"] == {
        "column_name": "result_count"
    }
    assert by_title["Laboratory values over time"]["viz_type"] == (
        "echarts_timeseries_line"
    )
    assert by_title["Laboratory value area"]["viz_type"] == "echarts_area"
    for title in (
        "Laboratory values over time",
        "Laboratory value area",
        "Values by patient",
        "Stacked laboratory values",
        "Result composition",
    ):
        assert by_title[title]["params"]["metrics"][0]["aggregate"] == "MAX"
        assert by_title[title]["params"]["metrics"][0]["column"] == {
            "column_name": "result_value"
        }

    grouped = by_title["Values by patient"]["params"]
    stacked = by_title["Stacked laboratory values"]["params"]
    proportion = by_title["Result composition"]["params"]
    assert grouped["viz_type"] == "echarts_timeseries_bar"
    assert "stack" not in grouped
    assert stacked["stack"] == "Stack"
    assert "contributionMode" not in stacked
    assert proportion["stack"] == "Stack"
    assert proportion["contributionMode"] == "row"

    assert manifest["targetSupersetVersion"] == "6.1.0"
    assert manifest["credentialPolicy"] == "local_demo_read_only"
    assert manifest["manifestContainsCredentials"] is False
    assert manifest["containsResultRows"] is False
    assert [widget["presentationKind"] for widget in manifest["widgets"]] == [
        "table",
        "big_number",
        "time_series_line",
        "time_series_area",
        "grouped_bar",
        "stacked_bar",
        "proportion_bar",
    ]
    assert len(manifest["assetMembers"]) == 12
    assert all(
        not member["path"].startswith("catalyst/")
        for member in manifest["assetMembers"]
    )


def test_canonical_superset_fixture_records_exact_member_digests() -> None:
    metadata = json.loads(FIXTURE_METADATA.read_text(encoding="utf-8"))
    with zipfile.ZipFile(FIXTURE_ZIP) as archive:
        actual = [
            {
                "path": name.split("/", 1)[1],
                "bytes": len(archive.read(name)),
                "sha256": hashlib.sha256(archive.read(name)).hexdigest(),
            }
            for name in archive.namelist()
        ]
    assert metadata["members"] == actual


def test_canonical_superset_fixture_regenerates_byte_for_byte(tmp_path: Path) -> None:
    generator_path = FIXTURE_ROOT / "generate_fixture.py"
    spec = importlib.util.spec_from_file_location(
        "superset_fixture_generator", generator_path
    )
    assert spec is not None and spec.loader is not None
    generator = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(generator)

    generator._build(tmp_path)

    assert (tmp_path / FIXTURE_ZIP.name).read_bytes() == FIXTURE_ZIP.read_bytes()
    assert (tmp_path / FIXTURE_METADATA.name).read_bytes() == (
        FIXTURE_METADATA.read_bytes()
    )
