from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from jsonschema import Draft202012Validator, FormatChecker
from jsonschema.exceptions import SchemaError, ValidationError


NORMATIVE_SCHEMAS = (
    "catalyst-execute-request-v1.schema.json",
    "catalyst-execution-outcome-v1.schema.json",
    "catalyst-policy-outcome-v1.schema.json",
    "catalyst-preview-v1.schema.json",
    "catalyst-query-completion-v1.schema.json",
    "catalyst-query-request-v1.schema.json",
    "catalyst-query-v1.schema.json",
    "catalyst-question-request-v1.schema.json",
    "catalyst-table-v1.schema.json",
)


class ContractError(ValueError):
    """A normative contract is absent, malformed, or violated."""


class ContractRegistry:
    def __init__(self, schemas: dict[str, dict[str, Any]]) -> None:
        self.schemas = schemas
        self._validators = {
            name: Draft202012Validator(schema, format_checker=FormatChecker())
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
