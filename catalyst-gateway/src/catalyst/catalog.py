from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass, field as dataclass_field
import json
from pathlib import Path
import re
from typing import Any

from .digest import canonical_sha256


# Catalog-declared SQL identifiers are interpolated into the dataset-browser
# queries, so they are constrained to the unambiguous lowercase form every
# analytics view in this project uses. Anything else is an authoring error, not
# something to quietly quote around.
_IDENTIFIER = re.compile(r"^[a-z_][a-z0-9_]*$")


def _checked_identifier(value: str, *, role: str) -> str:
    for part in value.split("."):
        if not _IDENTIFIER.match(part):
            raise ValueError(
                f"Dataset browser {role} is not a plain lowercase SQL identifier: "
                f"{value!r}"
            )
    return value


@dataclass(frozen=True)
class DatasetBrowserProfile:
    """Which relation and columns the dataset browser reads for one data source.

    The browser renders one generic shape — subject, category, value, unit,
    timestamps — but every source spells those differently (OpenELIS calls the
    category ``test_name``; the OpenMRS HIV source calls it ``concept_name``).
    Declaring the mapping per catalog is what lets one adapter serve both
    without the query text assuming a single source's schema.
    """

    fact_view: str
    identity_column: str
    subject_column: str
    category_column: str
    observed_at_column: str
    value_column: str | None = None
    unit_column: str | None = None
    issued_at_column: str | None = None
    duration_column: str | None = None
    # Display-only fallbacks for sources whose value is coded/text/boolean
    # rather than numeric; the aggregates still use value_column alone.
    value_fallback_columns: tuple[str, ...] = ()

    @classmethod
    def from_payload(
        cls,
        payload: dict[str, Any],
        *,
        view_columns: dict[str, set[str]],
    ) -> DatasetBrowserProfile:
        fact_view = _checked_identifier(str(payload["factView"]), role="factView")
        known = view_columns.get(fact_view)
        if known is None:
            raise ValueError(
                "Dataset browser factView is not an approved catalog view: "
                f"{fact_view!r}"
            )

        def column(key: str, *, required: bool) -> str | None:
            raw = payload.get(key)
            if raw is None:
                if required:
                    raise ValueError(f"Dataset browser profile is missing {key!r}")
                return None
            name = _checked_identifier(str(raw), role=key)
            if name not in known:
                raise ValueError(
                    f"Dataset browser {key} references a column outside "
                    f"{fact_view}: {name!r}"
                )
            return name

        fallbacks = []
        for raw in payload.get("valueFallbackColumns", []):
            name = _checked_identifier(str(raw), role="valueFallbackColumns")
            if name not in known:
                raise ValueError(
                    "Dataset browser valueFallbackColumns references a column "
                    f"outside {fact_view}: {name!r}"
                )
            fallbacks.append(name)

        identity = column("identityColumn", required=True)
        subject = column("subjectColumn", required=True)
        category = column("categoryColumn", required=True)
        observed_at = column("observedAtColumn", required=True)
        assert identity is not None
        assert subject is not None
        assert category is not None
        assert observed_at is not None
        return cls(
            fact_view=fact_view,
            identity_column=identity,
            subject_column=subject,
            category_column=category,
            observed_at_column=observed_at,
            value_column=column("valueColumn", required=False),
            unit_column=column("unitColumn", required=False),
            issued_at_column=column("issuedAtColumn", required=False),
            duration_column=column("durationColumn", required=False),
            value_fallback_columns=tuple(fallbacks),
        )


@dataclass(frozen=True)
class Catalog:
    data_source: str
    catalog_version: str
    schema_version: str
    dialect: str
    context_source_id: str
    views: list[dict[str, Any]]
    freshness: dict[str, Any]
    dataset_browser: DatasetBrowserProfile | None = dataclass_field(default=None)
    # The relations a generated query may reference. ``None`` means every view
    # here is approved, which is true of a catalog straight from its file --
    # ``load`` keeps only ``approved`` views. Runtime discovery re-describes the
    # whole readable schema, so it pins this to the curated set on the way
    # through; without that, every table the role can read silently becomes an
    # approved query surface.
    approved_names: frozenset[str] | None = dataclass_field(default=None)

    @property
    def approved_view_names(self) -> set[str]:
        """Relations a generated query may reference."""
        names = self.relation_names
        if self.approved_names is None:
            return names
        # Intersected, so a curated view that has since been dropped from the
        # database does not stay approved.
        return {name for name in names if name in self.approved_names}

    @property
    def relation_names(self) -> set[str]:
        """Every relation the catalog describes, approved or not."""
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
        """Describe the approved views, and only those.

        This is what the writer sees and what the linter checks references
        against, so an unapproved relation must not appear here: describing a
        table is an invitation to query it.
        """
        approved = self.approved_view_names
        request_views: list[dict[str, Any]] = []
        for view in self.views:
            if view["name"] not in approved:
                continue
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

        # Curation decides what may be queried, so a curated view that the role
        # cannot read leaves nothing to query. Say so here, where the cause is
        # visible, rather than emitting a catalog with no approved views and
        # failing later against the request contract.
        surviving = {
            name
            for name in self.approved_view_names
            if name in {view["name"] for view in views}
        }
        if not surviving:
            missing = ", ".join(sorted(self.approved_view_names)) or "(none declared)"
            raise ValueError(
                "No approved catalog view is readable by the PostgreSQL role. "
                f"Approved: {missing}."
            )

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
            # Runtime discovery re-describes the schema; it does not change
            # which relation the dataset browser was curated to read.
            dataset_browser=self.dataset_browser,
            # ...nor which relations a query may reference. Discovery adds every
            # readable table to ``views`` so the browser and the existence check
            # can see them; approval stays with the curated set.
            approved_names=frozenset(self.approved_view_names),
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
        dataset_browser = None
        if "datasetBrowser" in payload:
            dataset_browser = DatasetBrowserProfile.from_payload(
                payload["datasetBrowser"],
                view_columns={
                    view["name"]: {field["name"] for field in view["fields"]}
                    for view in views
                },
            )
        return cls(
            data_source=payload["dataSource"],
            catalog_version=catalog_version,
            schema_version=payload["schemaVersion"],
            dialect=payload["dialect"],
            context_source_id=f"catalog:{catalog_version}",
            views=views,
            freshness={},
            dataset_browser=dataset_browser,
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
