from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass
import json
from pathlib import Path
from typing import Any

from .digest import canonical_sha256


@dataclass(frozen=True)
class Catalog:
    data_source: str
    catalog_version: str
    schema_version: str
    dialect: str
    context_source_id: str
    views: list[dict[str, Any]]
    freshness: dict[str, Any]

    @property
    def approved_view_names(self) -> set[str]:
        return {view["name"] for view in self.views}

    @property
    def relation_names(self) -> set[str]:
        return {view["name"] for view in self.views}

    @property
    def available_relation_names(self) -> set[str]:
        names = self.relation_names
        names.update(
            view["name"].rsplit(".", 1)[-1]
            for view in self.views
            if view.get("unqualifiedVisible") is True
        )
        return names

    def request_target(self) -> dict[str, str]:
        return {
            "dataSource": self.data_source,
            "catalogVersion": self.catalog_version,
            "dialect": self.dialect,
        }

    def request_catalog(self) -> dict[str, Any]:
        request_views: list[dict[str, Any]] = []
        for view in self.views:
            request_view = {
                key: deepcopy(view[key]) for key in ("name", "version", "grain")
            }
            request_view["fields"] = [
                {
                    key: deepcopy(field[key])
                    for key in ("name", "type", "description", "unit")
                    if key in field
                }
                for field in view["fields"]
            ]
            for key in ("relationships", "semanticDimensions"):
                if key in view:
                    request_view[key] = deepcopy(view[key])
            request_views.append(request_view)
        return {
            "contextSourceId": self.context_source_id,
            "views": request_views,
        }

    def with_discovered_relations(
        self,
        relations: list[dict[str, Any]],
    ) -> Catalog:
        """Return a deterministic catalog for every relation readable by PostgreSQL."""

        curated_by_name = {view["name"]: view for view in self.views}
        views: list[dict[str, Any]] = []
        for relation in sorted(relations, key=lambda item: item["name"]):
            name = relation["name"]
            curated = curated_by_name.get(name, {})
            curated_fields = {
                field["name"]: field for field in curated.get("fields", [])
            }
            fields: list[dict[str, Any]] = []
            for discovered_field in relation["fields"]:
                field = deepcopy(discovered_field)
                curated_field = curated_fields.get(field["name"])
                if curated_field is not None:
                    for key in ("description", "unit", "unitColumn"):
                        if key in curated_field:
                            field[key] = deepcopy(curated_field[key])
                fields.append(field)

            field_names = {field["name"] for field in fields}
            view: dict[str, Any] = {
                "name": name,
                "version": "database-schema",
                "grain": curated.get("grain") or relation["grain"],
                "relationType": relation.get("relationType", "relation"),
                "unqualifiedVisible": relation.get("unqualifiedVisible") is True,
                "fields": fields,
            }
            for key in ("relationships", "semanticDimensions"):
                value = curated.get(key)
                if key == "semanticDimensions" and value:
                    value = [
                        dimension
                        for dimension in value
                        if dimension.get("field") in field_names
                    ]
                if value:
                    view[key] = deepcopy(value)
            view["version"] = "schema-" + canonical_sha256(view)[:16]
            views.append(view)

        if not views:
            raise ValueError("PostgreSQL role has no readable relations.")

        schema_digest = canonical_sha256(
            {
                "dataSource": self.data_source,
                "dialect": self.dialect,
                "views": views,
            }
        )
        base_version = self.catalog_version.split("+schema.", 1)[0]
        catalog_version = f"{base_version}+schema.{schema_digest[:16]}"
        return Catalog(
            data_source=self.data_source,
            catalog_version=catalog_version,
            schema_version=self.schema_version,
            dialect=self.dialect,
            context_source_id=f"catalog:{catalog_version}",
            views=views,
            freshness=deepcopy(self.freshness),
        )

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
                nullable = column.get("nullable")
                if not isinstance(nullable, bool):
                    raise ValueError(
                        "Catalog columns must declare boolean nullability: "
                        f"{source_view.get('name')}.{column.get('name')}"
                    )
                field = {
                    "name": column["name"],
                    "type": (
                        "date-time" if logical_type == "timestamp" else logical_type
                    ),
                    "description": column["description"],
                    "nullable": nullable,
                }
                if "unit" in column:
                    field["unit"] = column["unit"]
                if "unitColumn" in column:
                    field["unitColumn"] = column["unitColumn"]
                fields.append(field)
            semantic_dimensions = deepcopy(source_view.get("semanticDimensions", []))
            field_names = {field["name"] for field in fields}
            for field in fields:
                unit_column = field.get("unitColumn")
                if unit_column is not None and unit_column not in field_names:
                    raise ValueError(
                        "Catalog unit column references a field outside its view: "
                        f"{source_view.get('name')}.{unit_column}"
                    )
            for dimension in semantic_dimensions:
                if dimension.get("field") not in field_names:
                    raise ValueError(
                        "Semantic dimension references a field outside its view: "
                        f"{dimension.get('field')}"
                    )
                canonical_values = [
                    value.get("canonical") for value in dimension.get("values", [])
                ]
                if len(canonical_values) != len(set(canonical_values)):
                    raise ValueError(
                        "Semantic dimension canonical values must be unique: "
                        f"{dimension.get('field')}"
                    )
            views.append(
                {
                    "name": source_view["name"],
                    "version": source_view["version"],
                    "grain": source_view["grain"],
                    "fields": fields,
                    **(
                        {
                            "semanticDimensions": deepcopy(
                                source_view["semanticDimensions"]
                            )
                        }
                        if semantic_dimensions
                        else {}
                    ),
                }
            )
        if not views:
            raise ValueError(f"Analytics catalog has no approved views: {catalog_path}")

        catalog_version = payload["catalogVersion"]
        return cls(
            data_source=payload["dataSource"],
            catalog_version=catalog_version,
            schema_version=payload["schemaVersion"],
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
            schema_version="analytics-v1",
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
