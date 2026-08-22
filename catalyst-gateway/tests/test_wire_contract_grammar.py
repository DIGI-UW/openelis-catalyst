"""The wire contracts must stay inside the subset llama.cpp compiles.

`response_format` is not a promise the backend keeps for free. llama.cpp turns
the schema into a GBNF grammar, and its converter does not implement `not` or
branch-local `required` inside a `oneOf`. A schema using them is accepted, a
grammar is produced, and the grammar is weaker than the schema -- so `strict:
True` reads as enforced while the model can still emit a forbidden shape.

That happened: a reviewer returned `candidate: {"status": "ready"}` with no SQL,
which REVIEW_SCHEMA forbids via `not`, and every follow-up turn died on the
resulting contract failure. These tests pin the shape of what we send.
"""

from __future__ import annotations

from typing import Any, Mapping

from src.catalyst.query_schemas import (
    GENERATION_FORMAT,
    REPAIR_FORMAT,
    REVIEW_FORMAT,
    REVIEW_VALIDATOR,
)

WIRE_FORMATS = {
    "generation": GENERATION_FORMAT,
    "review": REVIEW_FORMAT,
    "repair": REPAIR_FORMAT,
}

# Keywords llama.cpp's converter does not honour. `oneOf` is allowed, but only
# as a union of self-contained closed objects -- which the tests below check.
UNSUPPORTED_KEYWORDS = ("not", "if", "then", "else", "allOf", "anyOf")


def _walk(node: Any, path: str = ""):
    if isinstance(node, Mapping):
        yield path, node
        for key, value in node.items():
            yield from _walk(value, f"{path}/{key}")
    elif isinstance(node, list):
        for index, value in enumerate(node):
            yield from _walk(value, f"{path}/{index}")


def test_wire_formats_avoid_keywords_the_grammar_drops():
    for name, wire in WIRE_FORMATS.items():
        schema = wire["json_schema"]["schema"]
        for path, node in _walk(schema):
            for keyword in UNSUPPORTED_KEYWORDS:
                assert keyword not in node, (
                    f"{name} format uses {keyword!r} at {path or '/'}; llama.cpp "
                    "ignores it, so the grammar would be weaker than the schema"
                )


def test_every_oneof_branch_is_a_closed_self_describing_object():
    """A branch must not lean on sibling context to be correct.

    If a branch omits `type`/`additionalProperties`, or declares required fields
    it does not also list in `properties`, the compiled grammar cannot express
    the constraint even without `not`.
    """
    for name, wire in WIRE_FORMATS.items():
        schema = wire["json_schema"]["schema"]
        for path, node in _walk(schema):
            branches = node.get("oneOf")
            if not isinstance(branches, list):
                continue
            for index, branch in enumerate(branches):
                where = f"{name}{path}/oneOf/{index}"
                assert branch.get("type") == "object", f"{where} has no object type"
                assert (
                    branch.get("additionalProperties") is False
                ), f"{where} does not close additionalProperties"
                properties = branch.get("properties") or {}
                missing = [
                    field
                    for field in branch.get("required", [])
                    if field not in properties
                ]
                assert not missing, f"{where} requires undeclared {missing}"


def test_review_wire_forces_a_repair_to_carry_its_query():
    """The exact shape that broke production must be unrepresentable."""
    repair = next(
        branch
        for branch in REVIEW_FORMAT["json_schema"]["schema"]["oneOf"]
        if branch["properties"]["decision"]["const"] == "repair"
    )
    candidate = repair["properties"]["candidate"]

    assert "candidate" in repair["required"]
    for field in ("status", "target", "sql", "parameters", "expectedColumns"):
        assert field in candidate["required"], f"repair candidate may omit {field}"

    # And the validator still rejects the stub, so defence in depth holds.
    stub = {
        "decision": "repair",
        "checks": [{"name": "field-grounding", "status": "failed"}],
        "candidate": {"status": "ready"},
    }
    assert list(REVIEW_VALIDATOR.iter_errors(stub)), "validator accepts the stub"


def test_review_wire_keeps_approve_and_reject_usable():
    branches = {
        branch["properties"]["decision"]["const"]: branch
        for branch in REVIEW_FORMAT["json_schema"]["schema"]["oneOf"]
    }
    assert set(branches) == {"approve", "reject", "repair"}
    # A rejection is only useful if it says why.
    assert "message" in branches["reject"]["required"]
    # An approval carries no candidate to apply.
    assert "candidate" not in branches["approve"]["properties"]
