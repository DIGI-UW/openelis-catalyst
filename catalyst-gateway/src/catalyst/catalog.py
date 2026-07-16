from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass
import json
from pathlib import Path
from typing import Any


@dataclass(frozen=True)
class Catalog:
    data_source: str
    catalog_version: str
    dialect: str
    context_source_id: str
    views: list[dict[str, Any]]
    freshness: dict[str, Any]

    @property
    def approved_view_names(self) -> set[str]:
        return {view["name"] for view in self.views}

    def request_target(self) -> dict[str, str]:
        return {
            "dataSource": self.data_source,
            "catalogVersion": self.catalog_version,
            "dialect": self.dialect,
        }

    def request_catalog(self) -> dict[str, Any]:
        return {
            "contextSourceId": self.context_source_id,
            "views": deepcopy(self.views),
        }

    @classmethod
    def load(cls, path: str | Path) -> Catalog:
        catalog_path = Path(path)
        payload = json.loads(catalog_path.read_text(encoding="utf-8"))
        if payload.get("contractVersion") != "catalyst.analytics.catalog.v1":
            raise ValueError(f"Unsupported analytics catalog: {catalog_path}")

        views = []
        for source_view in payload.get("views", []):
            if source_view.get("approved") is not True:
                continue
            fields = []
            for column in source_view.get("columns", []):
                logical_type = column["logicalType"]
                field = {
                    "name": column["name"],
                    "type": (
                        "date-time" if logical_type == "timestamp" else logical_type
                    ),
                    "description": column["description"],
                }
                if "unit" in column:
                    field["unit"] = column["unit"]
                fields.append(field)
            views.append(
                {
                    "name": source_view["name"],
                    "version": source_view["version"],
                    "grain": source_view["grain"],
                    "fields": fields,
                }
            )
        if not views:
            raise ValueError(f"Analytics catalog has no approved views: {catalog_path}")

        catalog_version = payload["catalogVersion"]
        return cls(
            data_source=payload["dataSource"],
            catalog_version=catalog_version,
            dialect=payload["dialect"],
            context_source_id=f"catalog:{catalog_version}",
            views=views,
            freshness={},
        )

    @classmethod
    def demo(cls) -> Catalog:
        return cls(
            data_source="openelis-demo-analytics",
            catalog_version="2026.07",
            dialect="postgresql",
            context_source_id="catalog:openelis-demo-analytics:2026.07",
            views=[
                {
                    "name": "analytics.lab_results",
                    "version": "1",
                    "grain": "one row per laboratory result",
                    "fields": [
                        {
                            "name": "test_name",
                            "type": "string",
                            "description": "Laboratory test display name",
                        },
                        {
                            "name": "result_value",
                            "type": "decimal",
                            "description": "Numeric result value when available",
                        },
                        {
                            "name": "result_date",
                            "type": "date",
                            "description": "Result effective date",
                        },
                    ],
                }
            ],
            freshness={
                "sourceWatermark": "1970-01-01T00:00:00Z",
                "pipelineRunId": "demo-unconfigured",
                "completionState": "partial",
                "observedLagSeconds": 0,
            },
        )
