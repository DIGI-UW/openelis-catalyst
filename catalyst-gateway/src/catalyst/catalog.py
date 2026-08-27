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
        """Relations exposed through the legacy ``approvedViews`` field."""
        return self.relation_names

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
        """Describe every relation available to the writer."""
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
            # Runtime discovery re-describes the schema; it does not change
            # which relation the dataset browser was curated to read.
        )

    @classmethod
    def for_source(cls, *, data_source: str, dialect: str) -> Catalog:
        """An empty catalog for a configured source.

        Live discovery is authoritative, so a source starts with no relations
        and is filled by ``with_discovered_relations``. Nothing is read from a
        generated catalog file: what the connection exposes is the catalog.
        """
        return cls(
            data_source=data_source,
            catalog_version="live",
            schema_version="live",
            dialect=dialect,
            context_source_id=data_source,
            views=[],
            freshness={},
        )
