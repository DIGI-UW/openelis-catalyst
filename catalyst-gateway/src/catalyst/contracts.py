from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from jsonschema import Draft202012Validator, FormatChecker
from jsonschema.exceptions import SchemaError, ValidationError
from referencing import Registry, Resource


NORMATIVE_SCHEMAS = (
    "catalyst-execute-request-v1.schema.json",
    "catalyst-execution-outcome-v1.schema.json",
    "catalyst-policy-outcome-v1.schema.json",
    "catalyst-preview-v1.schema.json",
    "catalyst-query-completion-v1.schema.json",
    "catalyst-query-request-v1.schema.json",
    "catalyst-query-request-v2.schema.json",
    "catalyst-query-revision-context-v1.schema.json",
    "catalyst-query-v1.schema.json",
    "catalyst-question-request-v1.schema.json",
    "catalyst-superset-bundle-v1.schema.json",
    "catalyst-superset-import-latest-v1.schema.json",
    "catalyst-superset-import-receipt-v1.schema.json",
    "catalyst-superset-last-verified-v1.schema.json",
    "catalyst-superset-outbox-current-v1.schema.json",
    "catalyst-data-sources-v1.schema.json",
    "catalyst-table-v1.schema.json",
    "catalyst-workbench-editor-catalog-v1.schema.json",
    "catalyst-workbench-editor-snapshot-v1.schema.json",
    "catalyst-workbench-editor-snapshot-record-v1.schema.json",
    "catalyst-workbench-execute-request-v1.schema.json",
    "catalyst-workbench-finding-v1.schema.json",
    "catalyst-workbench-session-request-v1.schema.json",
    "catalyst-workbench-session-v1.schema.json",
    "catalyst-workbench-turn-request-v1.schema.json",
    "catalyst-workbench-turn-v1.schema.json",
    "catalyst-workbench-turn-timeline-v1.schema.json",
    "catalyst-workbench-generation-evidence-v1.schema.json",
    "catalyst-workbench-version-request-v1.schema.json",
)


class ContractError(ValueError):
    """A normative contract is absent, malformed, or violated."""


class ContractRegistry:
    def __init__(self, schemas: dict[str, dict[str, Any]]) -> None:
        self.schemas = schemas
        registry = Registry()
        for name, schema in schemas.items():
            identifier = schema.get("$id")
            if not isinstance(identifier, str) or not identifier:
                raise ContractError(f"Normative schema {name} has no $id.")
            try:
                registry = registry.with_resource(
                    identifier,
                    Resource.from_contents(schema),
                )
            except Exception as error:
                raise ContractError(
                    f"Cannot register normative schema {name}: {error}"
                ) from error
        self._registry = registry
        self._validators = {
            name: Draft202012Validator(
                schema,
                format_checker=FormatChecker(),
                registry=registry,
            )
            for name, schema in schemas.items()
        }

    @classmethod
    def load(cls, directory: str | Path) -> ContractRegistry:
        root = Path(directory)
        schemas: dict[str, dict[str, Any]] = {}
        for name in NORMATIVE_SCHEMAS:
            path = root / name
            try:
                schema = json.loads(path.read_text(encoding="utf-8"))
                Draft202012Validator.check_schema(schema)
            except (OSError, json.JSONDecodeError, SchemaError) as error:
                raise ContractError(
                    f"Cannot load normative schema {name}: {error}"
                ) from error
            schemas[name] = schema
        return cls(schemas)

    @classmethod
    def default(cls) -> ContractRegistry:
        workspace = Path(__file__).resolve().parents[3]
        return cls.load(workspace / "docs" / "contracts")

    def validate(self, name: str, instance: Any) -> None:
        try:
            validator = self._validators[name]
        except KeyError as error:
            raise ContractError(f"Unknown normative schema: {name}") from error
        try:
            validator.validate(instance)
        except ValidationError as error:
            location = ".".join(str(part) for part in error.absolute_path)
            prefix = f"{location}: " if location else ""
            raise ContractError(f"{name}: {prefix}{error.message}") from error
