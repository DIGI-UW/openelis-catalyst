"""Catalyst governed-query engine — relocated from med-agent-hub/catalyst_query.py.

The generate -> lint-correct -> review -> repair -> finalize orchestration is
moved here verbatim so the Catalyst gateway owns its own orchestration. The only
changes from the hub original are:

* the model-call seam ``_backend_chat`` calls a Hub-configured named role instead
  of the model router directly, so the Hub controls model, knobs, and prompt;
* a writer-only branch: a profile that declares no ``query_review`` role
  finalizes the writer's lint-passing candidate without an independent review;
* Catalyst retains only its query orchestration and receives the Hub's complete
  profile evidence through discovery.

All deterministic logic (schemas, parsing, lint, semantic grounding, patch
application) is imported from the already-moved query_schemas / query_parse /
query_lint modules — this file is orchestration + evidence only.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import os
import time
import uuid
from copy import deepcopy
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, AsyncIterator, Dict, Mapping, Optional, Tuple

import httpx
import rfc8785

from .query_lint import lint_candidate
from .query_schemas import (
    CANDIDATE_VALIDATOR as _CANDIDATE_VALIDATOR,
    FINAL_VALIDATOR as _FINAL_VALIDATOR,
    GENERATION_FORMAT as _GENERATION_FORMAT,
    REPAIR_FORMAT as _REPAIR_FORMAT,
    REVIEW_FORMAT as _REVIEW_FORMAT,
    STATUS_FIELDS as _STATUS_FIELDS,
    QueryContractError,
    QueryGenerationError,
    QueryPatchError,
    QueryReviewError,
    patch_format as _patch_format,
    validation_error as _validation_error,
)
from .query_parse import (
    _allowed_patch_paths,
    _bind_question_date_literals,
    _candidate_matches_catalog,
    _canonical_target,
    _contract_lint_finding,
    _lint_validation_checks,
    _missing_name_findings,
    _missing_parameter_name_paths,
    _normalize_candidate_draft,
    _normalize_grounded_parameter_names,
    _parse_and_apply_patch,
    _parse_candidate,
    _parse_review_object,
    _patch_lint_finding,
    _semantic_binding_failures,
    _semantic_checks,
    _semantic_lint_findings,
    _related_analyte_values,
    _unknown_result_analyte,
)

logger = logging.getLogger(__name__)

TERMINAL_WRITER_ANSWERS = frozenset({"needs_clarification", "unsupported"})
"""Writer answers that carry no SQL and end the run on one call."""

_PROVIDER_ID = os.getenv("CATALYST_MODEL_PROVIDER_ID", "med-agent-hub")
_HUB_QUERY_PROFILE_URL = os.getenv(
    "CATALYST_HUB_QUERY_PROFILE_URL", "http://med-agent-hub:8080/v1/hub/query-profiles"
)
_HUB_TIMEOUT_SECONDS = float(os.getenv("CATALYST_HUB_TIMEOUT_SECONDS", "1800"))


@dataclass(frozen=True)
class EngineProfile:
    """Hub-discovered query profile required by the local Catalyst engine.

    ``models`` and ``knobs`` are evidence received from the Hub, not caller
    configuration. ``profile_evidence`` is the Hub's complete credential-free
    profile snapshot, including prompt references and digests.
    """

    id: str
    label: str
    models: Mapping[str, str]
    knobs: Mapping[str, Mapping[str, Any]]
    policies: Mapping[str, Any] = field(default_factory=dict)
    profile_evidence: Mapping[str, Any] = field(default_factory=dict)

    @property
    def has_review(self) -> bool:
        return "query_review" in self.models


@dataclass
class EngineRequest:
    """The engine's view of one governed-query request."""

    catalyst_query: Mapping[str, Any]
    messages: list[dict[str, Any]]
    profile: EngineProfile
    max_tokens: Optional[int] = None


def _request_payload(
    request: Any,
    extension: Mapping[str, Any],
    *,
    candidate: Optional[Mapping[str, Any]] = None,
    review_attempt: Optional[int] = None,
    deterministic_findings: Optional[list[dict[str, Any]]] = None,
) -> Dict[str, Any]:
    instruction = str(request.messages[0]["content"])
    payload: Dict[str, Any] = {
        "question": instruction,
        "target": _canonical_target(extension),
        "catalog": extension["catalog"],
        "policy": extension["policy"],
        "requiredOutputContract": extension["requiredOutputContract"],
        "correlation": extension["correlation"],
    }
    if extension.get("contractVersion") == "catalyst.query.request.v2":
        payload["instruction"] = instruction
        payload["revision"] = deepcopy(extension["revision"])
    if candidate is not None:
        payload["candidate"] = candidate
    if review_attempt is not None:
        payload["reviewAttempt"] = review_attempt
    if deterministic_findings:
        payload["deterministicFindings"] = deterministic_findings
    return payload


async def _backend_chat(
    client: httpx.AsyncClient,
    profile_id: str,
    role: str,
    model: str,
    messages: list[dict[str, str]],
    *,
    response_format: Mapping[str, Any],
    temperature: float,
    dry_multiplier: float,
    max_tokens: Optional[int],
) -> tuple[str, Optional[Mapping[str, Any]]]:
    """Call a named Hub query role; caller-provided model settings are ignored."""

    payload: Dict[str, Any] = {"messages": messages}
    if response_format is not None:
        payload["response_format"] = dict(response_format)
    resp = await client.post(
        f"{_HUB_QUERY_PROFILE_URL}/{profile_id}/roles/{role}/generate",
        json=payload,
        timeout=_HUB_TIMEOUT_SECONDS,
    )
    resp.raise_for_status()
    message = resp.json()
    content = message.get("content") if isinstance(message, Mapping) else None
    if not isinstance(content, str) or not content.strip():
        raise QueryContractError("model response did not contain assistant content")
    accounting = (
        message.get("token_accounting") if isinstance(message, Mapping) else None
    )
    return content.strip(), accounting if isinstance(accounting, Mapping) else None


def _evidence_digest(value: Any) -> str:
    if isinstance(value, str):
        encoded = value.encode("utf-8")
    else:
        encoded = json.dumps(
            value,
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=False,
        ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _canonical_evidence_digest(value: Any) -> str:
    return hashlib.sha256(rfc8785.dumps(value)).hexdigest()


def query_profile_evidence(
    profile: Any, *, max_tokens: Optional[int] = None
) -> dict[str, Any]:
    """Return the exact credential-free Hub-owned profile snapshot."""
    if profile.profile_evidence:
        return deepcopy(dict(profile.profile_evidence))

    # Unit tests may construct a minimal in-memory EngineProfile. Production
    # profiles always arrive from Hub discovery and take the branch above.
    model_classes = profile.policies.get("model_classes") or {}

    def role_evidence(public_role: str, configured_role: str) -> dict[str, Any]:
        config = dict(profile.knobs.get(configured_role) or {})
        if max_tokens is not None:
            config["maxTokens"] = max_tokens
        return {
            "role": public_role,
            "providerId": _PROVIDER_ID,
            "modelClass": str(
                model_classes.get(configured_role)
                or profile.models[configured_role].split("-", 1)[0]
            ),
            "modelId": profile.models[configured_role],
            "config": config,
            "systemPrompt": {
                "promptId": "test-only",
                "version": "1",
                "promptRef": "test-only",
                "promptDigest": _evidence_digest("test-only"),
                "text": "test-only",
            },
        }

    evidence: dict[str, Any] = {
        "profileId": profile.id,
        "profileName": profile.label,
        "writer": role_evidence("writer", "query_generate"),
    }
    # Writer-only profiles declare no reviewer role; their evidence has no
    # reviewer leg. Reviewed profiles are unchanged (reviewer evidence retained).
    if "query_review" in profile.models:
        evidence["reviewer"] = role_evidence("reviewer", "query_review")
    compact = deepcopy(evidence)
    compact["writer"]["systemPrompt"].pop("text")
    if "reviewer" in compact:
        compact["reviewer"]["systemPrompt"].pop("text")
    return {**evidence, "profileDigest": _canonical_evidence_digest(compact)}


def _profile_evidence(request: Any) -> dict[str, Any]:
    return query_profile_evidence(
        request.profile,
        max_tokens=request.max_tokens,
    )


def _role_max_tokens(request: Any, configured_role: str) -> Optional[int]:
    """Use the selected role's Hub-discovered cap; retain test-only fallback."""
    value = (request.profile.knobs.get(configured_role) or {}).get("maxTokens")
    return request.max_tokens if value is None else int(value)


def _role_provider_id(request: Any, public_role: str) -> str:
    role = _profile_evidence(request).get(public_role) or {}
    provider_id = role.get("providerId")
    if not isinstance(provider_id, str) or not provider_id:
        raise QueryContractError(f"profile evidence omitted {public_role} providerId")
    return provider_id


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _finish_invocation(
    invocation: dict[str, Any],
    *,
    outcome: str,
    failure: Any = None,
) -> None:
    invocation["outcome"] = outcome
    invocation["failureDigest"] = (
        None if outcome == "succeeded" else _evidence_digest(failure or outcome)
    )


def _mark_model_validation(
    invocation: dict[str, Any], findings: list[dict[str, Any]]
) -> None:
    if not findings:
        return
    outcome = (
        "contract_failed"
        if any(str(item.get("code", "")).startswith("contract.") for item in findings)
        else "validation_failed"
    )
    _finish_invocation(
        invocation,
        outcome=outcome,
        failure={"findingCodes": [item.get("code") for item in findings]},
    )


async def _invoke_backend(
    client: httpx.AsyncClient,
    profile_id: str,
    profile_role: str,
    model: str,
    messages: list[dict[str, str]],
    *,
    provider_id: str,
    response_format: Mapping[str, Any],
    temperature: float,
    dry_multiplier: float,
    max_tokens: Optional[int],
    invocations: list[dict[str, Any]],
    role: str,
    stage: str,
    attempt: int,
) -> str:
    """Record every physical model call, including transport failures."""
    started_at = _utc_now()
    started = time.monotonic()
    configuration = {
        "temperature": temperature,
        "dryMultiplier": dry_multiplier,
        "maxTokens": max_tokens,
        "responseFormat": (
            (response_format.get("json_schema") or {}).get("name")
            if isinstance(response_format, Mapping)
            else None
        ),
    }
    request_payload = {
        "model": model,
        "messages": messages,
        "response_format": response_format,
        "configuration": configuration,
    }
    invocation: dict[str, Any] = {
        "invocationId": str(uuid.uuid4()),
        "role": role,
        "stage": stage,
        "attempt": attempt,
        "providerId": provider_id,
        "modelId": model,
        "configuration": configuration,
        "startedAt": started_at,
        "endedAt": None,
        "durationMs": None,
        "requestDigest": _evidence_digest(request_payload),
        "responseDigest": None,
        "failureDigest": None,
        "outcome": "in_progress",
    }
    invocations.append(invocation)
    try:
        answered = await _backend_chat(
            client,
            profile_id,
            profile_role,
            model,
            messages,
            response_format=response_format,
            temperature=temperature,
            dry_multiplier=dry_multiplier,
            max_tokens=max_tokens,
        )
        # Test doubles may still answer with a bare string; production answers
        # (content, accounting). Either way each invocation keeps its own count.
        content, accounting = (
            answered if isinstance(answered, tuple) else (answered, None)
        )
        invocation["tokenAccounting"] = (
            dict(accounting) if isinstance(accounting, Mapping) else None
        )
    except asyncio.CancelledError as exc:
        _finish_invocation(invocation, outcome="cancelled", failure=repr(exc))
        raise
    except (TimeoutError, httpx.TimeoutException) as exc:
        _finish_invocation(invocation, outcome="timed_out", failure=repr(exc))
        raise
    except QueryContractError as exc:
        invocation["responseDigest"] = _evidence_digest("")
        _finish_invocation(invocation, outcome="contract_failed", failure=str(exc))
        raise
    except Exception as exc:
        _finish_invocation(
            invocation,
            outcome="transport_failed",
            failure={"type": type(exc).__name__, "message": str(exc)},
        )
        raise
    else:
        invocation["responseDigest"] = _evidence_digest(content)
        _finish_invocation(invocation, outcome="succeeded")
        return content
    finally:
        invocation["endedAt"] = _utc_now()
        invocation["durationMs"] = max(0, int((time.monotonic() - started) * 1000))


async def _generate(
    client: httpx.AsyncClient,
    request: Any,
    extension: Mapping[str, Any],
    invocations: list[dict[str, Any]],
) -> tuple[Dict[str, Any], int, bool, list[dict[str, Any]]]:
    profile = request.profile
    messages = [
        {
            "role": "user",
            "content": json.dumps(
                _request_payload(request, extension),
                separators=(",", ":"),
            ),
        },
    ]
    max_attempts = int(profile.policies.get("generation_attempts", 2))
    question = request.messages[0]["content"]
    seen_outputs: set[str] = set()
    history: list[dict[str, Any]] = []
    binding_normalized = False
    last_candidate: Optional[Dict[str, Any]] = None
    last_output: Optional[str] = None
    correction_base: Optional[Dict[str, Any]] = None
    correction_findings: list[dict[str, Any]] = []
    allowed_patch_paths: list[str] = []
    required_patch_paths: Optional[set[str]] = None

    for attempt in range(1, max_attempts + 1):
        using_patch = correction_base is not None
        response_format = (
            _patch_format(
                allowed_patch_paths,
                [str(finding["code"]) for finding in correction_findings],
                add_only_paths=required_patch_paths,
            )
            if using_patch
            else _GENERATION_FORMAT
        )
        content = await _invoke_backend(
            client,
            profile.id,
            "query_generate",
            profile.models["query_generate"],
            messages,
            provider_id=_role_provider_id(request, "writer"),
            response_format=response_format,
            temperature=float(profile.knobs["query_generate"]["temperature"]),
            dry_multiplier=float(profile.knobs["query_generate"]["dry"]),
            max_tokens=_role_max_tokens(request, "query_generate"),
            invocations=invocations,
            role="writer",
            stage=(
                "followup_generation"
                if extension.get("contractVersion") == "catalyst.query.request.v2"
                else "initial_generation"
            ),
            attempt=attempt,
        )
        last_output = content
        if content in seen_outputs:
            finding = {
                "code": "generation.unchanged_candidate",
                "stage": "query_correct",
                "severity": "error",
                "path": "$",
                "message": "The model repeated an unchanged candidate after feedback.",
                "evidence": "candidate output matched an earlier attempt",
                "suggestedAction": "Stop retrying and reject this generation run.",
            }
            history.append(
                {
                    "attempt": attempt,
                    "status": "failed",
                    "finding_codes": [finding["code"]],
                    "findings": [finding],
                }
            )
            _mark_model_validation(invocations[-1], [finding])
            raise QueryGenerationError(
                finding["message"],
                history,
                candidate=last_candidate,
                raw_output=last_output,
            )
        seen_outputs.add(content)

        parsed: Optional[Dict[str, Any]] = None
        normalized_this_attempt = False
        patch_rejected: Optional[list[dict[str, Any]]] = None
        partial_base = False
        if using_patch:
            assert correction_base is not None
            try:
                parsed = _parse_and_apply_patch(
                    content,
                    correction_base,
                    correction_findings,
                    allowed_patch_paths,
                    required_paths=required_patch_paths,
                )
                grounded = _normalize_grounded_parameter_names(
                    parsed, question, extension
                )
                normalized_this_attempt = grounded != parsed
                parsed = grounded
                candidate_error = _validation_error(
                    _CANDIDATE_VALIDATOR,
                    parsed,
                    f"query generation patch attempt {attempt}",
                )
                if str(candidate_error):
                    missing_paths = _missing_parameter_name_paths(parsed, extension)
                    if not missing_paths:
                        raise QueryPatchError(
                            "contract.invalid_patch", str(candidate_error)
                        )
                    correction_base = deepcopy(parsed)
                    correction_findings = _missing_name_findings(missing_paths)
                    allowed_patch_paths = missing_paths
                    required_patch_paths = set(missing_paths)
                    findings = correction_findings
                    partial_base = True
                    parsed = None
                else:
                    if not _candidate_matches_catalog(
                        parsed, _canonical_target(extension)
                    ):
                        raise QueryPatchError(
                            "generation.patch_out_of_scope",
                            "Patch reconstruction changed or retained a non-canonical target.",
                        )
                    last_candidate = deepcopy(parsed)
                    findings = [
                        *lint_candidate(parsed, extension, instruction=question),
                        *_semantic_lint_findings(parsed, question, extension),
                    ]
            except QueryPatchError as error:
                logger.warning("Catalyst query correction patch failed: %s", error)
                findings = [_patch_lint_finding(error)]
                patch_rejected = findings
        else:
            try:
                parsed, normalized_this_attempt = _parse_candidate(
                    content,
                    question,
                    extension,
                    label=f"query generation attempt {attempt}",
                )
                if parsed.get("status") in TERMINAL_WRITER_ANSWERS:
                    # Asking or declining is an answer, not a draft. There is
                    # no SQL to lint, repair, or review, so the run ends on
                    # this one call with the writer's own words intact.
                    return parsed, attempt, binding_normalized, history
                bound = _bind_question_date_literals(parsed, question)
                normalized_this_attempt = normalized_this_attempt or bound != parsed
                parsed = bound
                last_candidate = deepcopy(parsed)
                findings = [
                    *lint_candidate(parsed, extension, instruction=question),
                    *_semantic_lint_findings(parsed, question, extension),
                ]
            except QueryContractError as error:
                logger.warning(
                    "Catalyst query candidate failed deterministic validation: %s",
                    error,
                )
                findings = [_contract_lint_finding(error)]
                try:
                    draft, normalized_this_attempt = _normalize_candidate_draft(
                        content,
                        question,
                        extension,
                        label=f"query generation attempt {attempt}",
                    )
                    missing_paths = _missing_parameter_name_paths(draft, extension)
                except QueryContractError:
                    draft = None
                    missing_paths = []
                if draft is not None and missing_paths:
                    correction_base = deepcopy(draft)
                    correction_findings = _missing_name_findings(missing_paths)
                    allowed_patch_paths = missing_paths
                    required_patch_paths = set(missing_paths)
                    findings = correction_findings
                    partial_base = True

        binding_normalized = binding_normalized or normalized_this_attempt
        _mark_model_validation(invocations[-1], findings)
        history.append(
            {
                "attempt": attempt,
                "status": "passed" if not findings else "failed",
                "finding_codes": [finding["code"] for finding in findings],
                "findings": findings,
            }
        )
        collaborative_initial = (
            profile.policies.get("collaborative_review") is True
            and extension.get("contractVersion") != "catalyst.query.request.v2"
        )
        if collaborative_initial and parsed is not None:
            # Initial collaborative turns hand lint findings to the reviewer as
            # repair context. Revision turns are writer-only, so they use the
            # deterministic correction loop below like any solo profile.
            return parsed, attempt, binding_normalized, history
        if not findings and parsed is not None:
            return parsed, attempt, binding_normalized, history
        if attempt == max_attempts:
            codes = ", ".join(finding["code"] for finding in findings)
            raise QueryGenerationError(
                f"query generation exhausted deterministic correction budget: {codes}",
                history,
                candidate=last_candidate,
                raw_output=last_output,
            )

        if using_patch:
            if patch_rejected is None and parsed is not None:
                correction_base = deepcopy(parsed)
                correction_findings = findings
                allowed_patch_paths = _allowed_patch_paths(parsed, findings)
                required_patch_paths = None
            if not allowed_patch_paths:
                raise QueryGenerationError(
                    "query generation findings could not be localized to patch paths",
                    history,
                    candidate=last_candidate,
                    raw_output=last_output,
                )
        elif parsed is not None:
            correction_base = deepcopy(parsed)
            correction_findings = findings
            allowed_patch_paths = _allowed_patch_paths(parsed, findings)
            required_patch_paths = None
        elif not partial_base:
            correction_base = None

        if correction_base is not None:
            if not allowed_patch_paths:
                raise QueryGenerationError(
                    "query generation findings could not be localized to patch paths",
                    history,
                    candidate=last_candidate,
                    raw_output=last_output,
                )
            correction_request: Dict[str, Any] = {
                "attempt": attempt + 1,
                "instruction": (
                    "Return only typed patch operations for the permitted paths. "
                    "Do not return the full candidate. Preserve every unaffected "
                    "field and return JSON only."
                ),
                "baseCandidate": correction_base,
                "allowedPatchPaths": allowed_patch_paths,
                "findings": correction_findings,
            }
            if patch_rejected is not None:
                correction_request["lastPatchRejection"] = patch_rejected
        else:
            correction_request = {
                "attempt": attempt + 1,
                "instruction": (
                    "The prior response was not a structurally parseable candidate. "
                    "Return one complete candidate matching the supplied schema, "
                    "without changing the question, target, catalog, or policy. "
                    "Return JSON only."
                ),
                "findings": findings,
            }
        feedback = {"correctionRequest": correction_request}
        messages = [
            *messages,
            {"role": "assistant", "content": content},
            {
                "role": "user",
                "content": json.dumps(feedback, separators=(",", ":")),
            },
        ]

    raise AssertionError("generation attempt loop terminated unexpectedly")


async def _review(
    client: httpx.AsyncClient,
    request: Any,
    extension: Mapping[str, Any],
    candidate: Mapping[str, Any],
    *,
    attempt: int,
    invocations: list[dict[str, Any]],
    deterministic_findings: Optional[list[dict[str, Any]]] = None,
) -> tuple[Dict[str, Any], int]:
    profile = request.profile
    messages = [
        {
            "role": "user",
            "content": json.dumps(
                _request_payload(
                    request,
                    extension,
                    candidate=candidate,
                    review_attempt=attempt,
                    deterministic_findings=deterministic_findings,
                ),
                separators=(",", ":"),
            ),
        },
    ]
    response_format = _REPAIR_FORMAT if deterministic_findings else _REVIEW_FORMAT
    content = await _invoke_backend(
        client,
        profile.id,
        "query_review",
        profile.models["query_review"],
        messages,
        provider_id=_role_provider_id(request, "reviewer"),
        response_format=response_format,
        temperature=float(profile.knobs["query_review"]["temperature"]),
        dry_multiplier=float(profile.knobs["query_review"]["dry"]),
        max_tokens=_role_max_tokens(request, "query_review"),
        invocations=invocations,
        role="reviewer",
        stage="review",
        attempt=attempt,
    )
    try:
        return (
            _parse_review_object(
                content,
                label="query review",
                flat_repair=bool(deterministic_findings),
                question=request.messages[0]["content"],
                extension=extension,
            ),
            1,
        )
    except QueryContractError as error:
        _finish_invocation(
            invocations[-1], outcome="contract_failed", failure=str(error)
        )
        # No early exit here. The corrective re-ask below, and the instruction
        # written for this very case, were unreachable while a review with no
        # deterministic findings raised immediately -- so a reviewer that
        # diagnosed the query correctly and then bungled the shape of its repair
        # took the turn down without ever being asked to fix the shape.
        if deterministic_findings:
            correction_instruction = (
                "Your repair JSON failed the strict output contract: "
                f"{error}. Return one corrected JSON object only. The "
                "top-level repair fields must be complete, including "
                "status, exact target, full SQL, all parameters, and "
                "expected columns."
            )
        else:
            correction_instruction = (
                "Your review JSON failed the strict output contract: "
                f"{error}. Return one corrected JSON object only with "
                "decision and checks, plus one complete candidate only when "
                "decision is repair."
            )
        corrected = await _invoke_backend(
            client,
            profile.id,
            "query_review",
            profile.models["query_review"],
            [
                *messages,
                {"role": "assistant", "content": content},
                {
                    "role": "user",
                    "content": correction_instruction,
                },
            ],
            provider_id=_role_provider_id(request, "reviewer"),
            response_format=response_format,
            temperature=float(profile.knobs["query_review"]["temperature"]),
            dry_multiplier=float(profile.knobs["query_review"]["dry"]),
            max_tokens=_role_max_tokens(request, "query_review"),
            invocations=invocations,
            role="reviewer",
            stage="review",
            attempt=attempt + 1,
        )
        try:
            parsed = _parse_review_object(
                corrected,
                label="query review correction",
                flat_repair=bool(deterministic_findings),
                question=request.messages[0]["content"],
                extension=extension,
            )
        except QueryContractError as correction_error:
            _finish_invocation(
                invocations[-1],
                outcome="contract_failed",
                failure=str(correction_error),
            )
            # The contract detail stays, because it is what a developer needs,
            # but it is no longer the whole message: a bare jsonschema string is
            # not something to show a person who asked a question about data.
            raise QueryReviewError(
                "The reviewer did not return a usable review. It was asked once "
                "to correct the shape of its output and did not. Its raw output "
                f"is retained as evidence. Contract detail: {correction_error}",
                raw_output=corrected,
            ) from correction_error
        return parsed, 2


def _validation_for(
    candidate: Mapping[str, Any], checks: list[dict[str, Any]]
) -> Dict[str, Any]:
    status = candidate["status"]
    statuses = {check["status"] for check in checks}
    if status == "ready":
        if "failed" in statuses:
            raise QueryContractError("review approved a ready query with failed checks")
        validation_status = "warned" if "warned" in statuses else "passed"
    elif status == "needs_clarification":
        validation_status = "warned"
        if "warned" not in statuses:
            checks = [
                *checks,
                {
                    "name": "query_status",
                    "status": "warned",
                    "message": "The query requires clarification.",
                },
            ]
    else:
        validation_status = "rejected"
        if "failed" not in statuses:
            checks = [
                *checks,
                {
                    "name": "query_status",
                    "status": "failed",
                    "message": "The query is not executable.",
                },
            ]
    return {"status": validation_status, "checks": checks}


def _provenance(profile_id: str, extension: Mapping[str, Any]) -> Dict[str, Any]:
    return {
        "profileId": profile_id,
        "traceId": extension["correlation"]["traceId"],
        "contextSourceIds": [extension["catalog"]["contextSourceId"]],
    }


def _collaboration_for_response(
    collaboration: Mapping[str, Any], extension: Mapping[str, Any]
) -> dict[str, Any]:
    value = deepcopy(dict(collaboration))
    if extension.get("contractVersion") == "catalyst.query.request.v2":
        revision = extension["revision"]
        value["base"] = {
            "baseClassification": revision["baseClassification"],
            "observedBase": deepcopy(revision["observedBase"]),
            "effectiveBaseVersion": deepcopy(revision["effectiveBaseVersion"]),
            "editorDigest": (
                revision["editorSnapshot"]["editorDigest"]
                if revision["editorSnapshot"] is not None
                else None
            ),
        }
    return value


def _finalize(
    question: str,
    extension: Mapping[str, Any],
    candidate: Mapping[str, Any],
    checks: list[dict[str, Any]],
    *,
    profile_id: str,
    model_collaboration: Optional[Mapping[str, Any]] = None,
) -> Dict[str, Any]:
    status = str(candidate["status"])
    result: Dict[str, Any] = {
        "contractVersion": "catalyst.query.v1",
        "deploymentMode": "demo",
        "status": status,
        "question": question,
    }
    if status == "ready":
        result["target"] = _canonical_target(extension)
        for field in ("sql", "parameters", "expectedColumns"):
            result[field] = deepcopy(candidate[field])
    else:
        for field in _STATUS_FIELDS[status]:
            result[field] = candidate[field]
    result["validation"] = _validation_for(candidate, checks)
    result["provenance"] = _provenance(profile_id, extension)
    if model_collaboration is not None:
        result["modelCollaboration"] = _collaboration_for_response(
            model_collaboration, extension
        )
    error = _validation_error(_FINAL_VALIDATOR, result, "final query contract")
    if str(error):
        raise error
    return result


def _rejected(
    question: str,
    extension: Mapping[str, Any],
    *,
    message: str,
    check_name: str,
    checks: Optional[list[dict[str, Any]]] = None,
    diagnostic_candidate: Optional[Mapping[str, Any]] = None,
    profile_id: str = "catalyst-query-checked",
    model_collaboration: Optional[Mapping[str, Any]] = None,
) -> Dict[str, Any]:
    final_checks = deepcopy(checks or [])
    if not any(check.get("status") == "failed" for check in final_checks):
        final_checks.append(
            {
                "name": check_name,
                "status": "failed",
                "message": message,
            }
        )
    result = {
        "contractVersion": "catalyst.query.v1",
        "deploymentMode": "demo",
        "status": "rejected",
        "question": question,
        "message": message,
        "validation": {
            "status": "rejected",
            "checks": final_checks,
        },
        "provenance": _provenance(profile_id, extension),
    }
    if diagnostic_candidate is not None:
        result["diagnosticCandidate"] = deepcopy(diagnostic_candidate)
    if model_collaboration is not None:
        result["modelCollaboration"] = _collaboration_for_response(
            model_collaboration, extension
        )
    error = _validation_error(_FINAL_VALIDATOR, result, "rejected query contract")
    if str(error):  # pragma: no cover - fixed fields are covered by contract tests
        raise error
    return result


def _write_trace(
    request: Any,
    extension: Mapping[str, Any],
    result: Mapping[str, Any],
    steps: list[dict[str, Any]],
) -> None:
    """Append query correlation metadata without using clinical trace fields."""
    try:
        trace_dir = Path(os.getenv("TEAM_TRACE_DIR", "/app/trace"))
        entry = {
            "ts": datetime.now(timezone.utc).isoformat(),
            "level_id": request.profile.id,
            "trace_id": extension["correlation"]["traceId"],
            "request_id": extension["correlation"]["requestId"],
            "question": str(request.messages[0]["content"])[:2000],
            "context_source_ids": [extension["catalog"]["contextSourceId"]],
            "models": {
                "generator": request.profile.models["query_generate"],
                "reviewer": request.profile.models.get("query_review"),
            },
            "sampling": {
                "generator_temperature": request.profile.knobs["query_generate"][
                    "temperature"
                ],
                "reviewer_temperature": (
                    request.profile.knobs.get("query_review") or {}
                ).get("temperature"),
            },
            "status": result["status"],
            "steps": steps,
            "model_invocations": deepcopy(
                (result.get("_hubEvidence") or {}).get("modelInvocations", [])
            ),
        }
        trace_dir.mkdir(parents=True, exist_ok=True)
        with (trace_dir / "trace.jsonl").open("a", encoding="utf-8") as file:
            file.write(json.dumps(entry, separators=(",", ":")) + "\n")
    except Exception as exc:  # pragma: no cover - tracing is best effort
        logger.warning("query trace write failed (non-fatal): %s", exc)


def _attach_model_evidence(
    result: Dict[str, Any], request: Any, invocations: list[dict[str, Any]]
) -> None:
    result["_hubEvidence"] = {
        "profileEvidence": _profile_evidence(request),
        "modelInvocations": deepcopy(invocations),
        "totalModelInvocationDurationMs": sum(
            int(item["durationMs"]) for item in invocations
        ),
        # The writer's request is the one whose budget the turn lives or dies
        # by; its count is the turn's token evidence.
        "tokenAccounting": deepcopy(
            next(
                (
                    item.get("tokenAccounting")
                    for item in invocations
                    if item.get("role") == "writer" and item.get("tokenAccounting")
                ),
                None,
            )
        ),
    }


async def execute_query_profile(
    request: Any,
) -> AsyncIterator[Tuple[str, str]]:
    """Execute generation, independent review, one repair, and final validation."""
    extension = request.catalyst_query
    question = request.messages[0].get("content", "") if request.messages else ""
    if not isinstance(extension, Mapping):
        raise QueryContractError(
            "query profile execution requires a validated catalystQuery context"
        )
    is_revision = extension.get("contractVersion") == "catalyst.query.request.v2"
    steps: list[dict[str, Any]] = [
        {
            "role": "context",
            "context_source_ids": [extension["catalog"]["contextSourceId"]],
        }
    ]
    invocations: list[dict[str, Any]] = []
    result: Dict[str, Any]
    canonical_target = _canonical_target(extension)
    approved_views = canonical_target["approvedViews"]
    if len(approved_views) != len(set(approved_views)):
        result = _rejected(
            question,
            extension,
            message="The request catalog contains duplicate relation names.",
            check_name="catalog_context",
            profile_id=request.profile.id,
        )
        _attach_model_evidence(result, request, invocations)
        _write_trace(request, extension, result, steps)
        yield "result", json.dumps(result, separators=(",", ":"))
        return

    unknown_analyte = _unknown_result_analyte(question, extension)
    if unknown_analyte:
        # No value is *called* this. Whether the data therefore lacks it is a
        # different question: a subject that several values are about is a
        # category to disambiguate, not an absence to report. Only a subject
        # nothing resembles is genuinely outside the catalog.
        related = _related_analyte_values(unknown_analyte, extension)
        if related:
            offered = ", ".join(related)
            message = (
                f"{unknown_analyte!r} is not one of the recorded result names. "
                f"Did you mean {offered}, or something else?"
            )
            answer = {"status": "needs_clarification", "clarification": message}
            check_status = "warned"
        else:
            message = (
                "The readable request catalog does not contain a grounded analyte "
                f"matching {unknown_analyte!r}."
            )
            answer = {"status": "unsupported", "message": message}
            check_status = "failed"
        result = _finalize(
            question,
            extension,
            answer,
            [{"name": "catalog_scope", "status": check_status, "message": message}],
            profile_id=request.profile.id,
        )
        steps.append(
            {
                "role": "catalog_scope",
                "status": answer["status"],
                "subject": unknown_analyte,
            }
        )
        steps.append({"role": "query_finalize", "status": result["status"]})
        _attach_model_evidence(result, request, invocations)
        _write_trace(request, extension, result, steps)
        yield "result", json.dumps(result, separators=(",", ":"))
        return

    async with httpx.AsyncClient() as client:
        try:
            (
                candidate,
                generation_attempts,
                binding_normalized,
                lint_history,
            ) = await _generate(client, request, extension, invocations)
            steps.append(
                {
                    "role": "query_generate",
                    "status": candidate["status"],
                    "attempts": generation_attempts,
                    "binding_normalized": binding_normalized,
                }
            )
            steps.extend(
                {
                    "role": "query_lint",
                    "attempt": lint_attempt["attempt"],
                    "status": lint_attempt["status"],
                    "finding_codes": lint_attempt["finding_codes"],
                    "findings": lint_attempt["findings"],
                }
                for lint_attempt in lint_history
            )
        except asyncio.CancelledError as exc:
            result = _rejected(
                question,
                extension,
                message="Query generation was cancelled.",
                check_name="query_generate",
                profile_id=request.profile.id,
            )
            steps.append(
                {"role": "query_generate", "status": "cancelled", "message": str(exc)}
            )
            _attach_model_evidence(result, request, invocations)
            _write_trace(request, extension, result, steps)
            raise
        except Exception as exc:
            logger.warning("Catalyst query generation failed: %s", exc)
            if isinstance(exc, (TimeoutError, httpx.TimeoutException)):
                failure_message = (
                    "The model backend timed out while generating the query."
                )
            elif isinstance(exc, httpx.HTTPStatusError):
                failure_message = (
                    "The model backend rejected the query-generation request "
                    f"(HTTP {exc.response.status_code})."
                )
            elif isinstance(exc, httpx.HTTPError):
                failure_message = (
                    "The model backend request failed while generating the query."
                )
            else:
                failure_message = (
                    "Query generation failed its structured-output contract."
                )
            diagnostic_candidate = None
            if isinstance(exc, QueryGenerationError):
                diagnostic_candidate = {
                    "executable": False,
                    "attempts": exc.history,
                }
                if exc.candidate is not None:
                    diagnostic_candidate["candidate"] = exc.candidate
                if exc.raw_output is not None:
                    diagnostic_candidate["rawOutput"] = exc.raw_output
            result = _rejected(
                question,
                extension,
                message=failure_message,
                check_name="query_generate",
                diagnostic_candidate=diagnostic_candidate,
                profile_id=request.profile.id,
            )
            steps.append(
                {
                    "role": "query_generate",
                    "status": "failed",
                    "message": str(exc),
                }
            )
            if isinstance(exc, QueryGenerationError):
                steps.extend(
                    {
                        "role": "query_lint",
                        "attempt": lint_attempt["attempt"],
                        "status": lint_attempt["status"],
                        "finding_codes": lint_attempt["finding_codes"],
                        "findings": lint_attempt["findings"],
                    }
                    for lint_attempt in exc.history
                )
        else:
            if candidate.get("status") in TERMINAL_WRITER_ANSWERS:
                # The writer asked or declined. There is no query to echo a
                # catalog target, to review, or to run: finalize its own words.
                result = _finalize(
                    question,
                    extension,
                    candidate,
                    [],
                    profile_id=request.profile.id,
                )
                steps.append({"role": "query_generate", "status": candidate["status"]})
            elif not _candidate_matches_catalog(candidate, canonical_target):
                result = _rejected(
                    question,
                    extension,
                    message=(
                        "Generated query did not echo the request catalog target."
                    ),
                    check_name="catalog_target",
                    diagnostic_candidate={
                        "executable": False,
                        "candidate": candidate,
                    },
                    profile_id=request.profile.id,
                )
                steps.append({"role": "catalog_target", "status": "failed"})
            elif not request.profile.has_review:
                # Writer-only profile: no independent reviewer. The candidate
                # already passed deterministic lint inside _generate, so finalize
                # it directly, recording the lint attempts as the validation
                # checks (plus any semantic-grounding check).
                result = _finalize(
                    question,
                    extension,
                    candidate,
                    _semantic_checks(
                        _lint_validation_checks(lint_history, []),
                        question,
                        extension,
                    ),
                    profile_id=request.profile.id,
                )
                steps.append({"role": "query_review", "status": "skipped"})
            else:
                try:
                    collaborative_review = (
                        request.profile.policies.get("collaborative_review") is True
                    )
                    if collaborative_review:
                        model_classes = request.profile.policies.get("model_classes")
                        if not isinstance(model_classes, Mapping) or model_classes.get(
                            "query_generate"
                        ) == model_classes.get("query_review"):
                            raise QueryContractError(
                                "collaborative query roles require different model "
                                "classes"
                            )
                    model_collaboration: Optional[Dict[str, Any]] = None
                    writer_findings = (
                        deepcopy(lint_history[-1]["findings"])
                        if collaborative_review and lint_history
                        else []
                    )
                    semantic_failures = (
                        []
                        if collaborative_review
                        else _semantic_binding_failures(candidate, question, extension)
                    )
                    if not collaborative_review and semantic_failures:
                        raise QueryContractError(
                            "candidate reached review with deterministic semantic "
                            "failures"
                        )
                    review, review_model_attempts = await _review(
                        client,
                        request,
                        extension,
                        candidate,
                        attempt=1,
                        invocations=invocations,
                        deterministic_findings=writer_findings or None,
                    )
                    steps.append(
                        {
                            "role": "query_review",
                            "attempt": 1,
                            "model_attempts": review_model_attempts,
                            "decision": review["decision"],
                            "deterministic_findings": len(writer_findings),
                        }
                    )
                    if writer_findings and review["decision"] == "approve":
                        raise QueryContractError(
                            "review approved a candidate with deterministic findings"
                        )
                    if review["decision"] == "reject":
                        model_collaboration = (
                            {
                                "writer": {
                                    "model": request.profile.models["query_generate"],
                                    "candidate": deepcopy(candidate),
                                    "lintFindings": writer_findings,
                                    **(
                                        {"disposition": "retained_unselected"}
                                        if is_revision
                                        else {}
                                    ),
                                },
                                "reviewer": {
                                    "model": request.profile.models["query_review"],
                                    "decision": "reject",
                                    "checks": deepcopy(review["checks"]),
                                    **(
                                        {"disposition": "diagnostic_only"}
                                        if is_revision
                                        else {}
                                    ),
                                },
                                "finalLintFindings": writer_findings,
                            }
                            if collaborative_review
                            else None
                        )
                        result = _rejected(
                            question,
                            extension,
                            message=review["message"],
                            check_name="query_review",
                            checks=list(review["checks"]),
                            diagnostic_candidate={
                                "executable": False,
                                "candidate": candidate,
                            },
                            profile_id=request.profile.id,
                            model_collaboration=model_collaboration,
                        )
                    elif review["decision"] == "repair":
                        repaired = _bind_question_date_literals(
                            review["candidate"], question
                        )
                        candidate_error = _validation_error(
                            _CANDIDATE_VALIDATOR,
                            repaired,
                            "query repair",
                        )
                        if str(candidate_error):
                            raise candidate_error
                        if not _candidate_matches_catalog(repaired, canonical_target):
                            raise QueryContractError(
                                "query repair changed the request catalog target"
                            )
                        repaired_findings = [
                            *lint_candidate(repaired, extension, instruction=question),
                            *_semantic_lint_findings(repaired, question, extension),
                        ]
                        model_collaboration = (
                            {
                                "writer": {
                                    "model": request.profile.models["query_generate"],
                                    "candidate": deepcopy(candidate),
                                    "lintFindings": writer_findings,
                                    **(
                                        {"disposition": "superseded"}
                                        if is_revision
                                        else {}
                                    ),
                                },
                                "reviewer": {
                                    "model": request.profile.models["query_review"],
                                    "decision": "repair",
                                    "candidate": deepcopy(repaired),
                                    "checks": deepcopy(review["checks"]),
                                    **(
                                        {"disposition": "selected"}
                                        if is_revision
                                        else {}
                                    ),
                                },
                                "finalLintFindings": deepcopy(repaired_findings),
                            }
                            if collaborative_review
                            else None
                        )
                        steps.append(
                            {
                                "role": "query_lint",
                                "attempt": "review_repair",
                                "status": ("failed" if repaired_findings else "passed"),
                                "finding_codes": [
                                    finding["code"] for finding in repaired_findings
                                ],
                                "findings": repaired_findings,
                            }
                        )
                        if repaired_findings:
                            _finish_invocation(
                                invocations[-1],
                                outcome="validation_failed",
                                failure={
                                    "findingCodes": [
                                        finding["code"] for finding in repaired_findings
                                    ]
                                },
                            )
                            logger.warning(
                                "Catalyst repaired candidate retained deterministic "
                                "lint failures: %s",
                                "; ".join(
                                    finding["message"] for finding in repaired_findings
                                ),
                            )
                            raise QueryContractError(
                                "review repair failed deterministic lint"
                            )
                        if collaborative_review:
                            final_checks = [
                                {
                                    "name": "reviewer_correction_lint",
                                    "status": "passed",
                                    "message": (
                                        "The reviewer's complete corrected query "
                                        "passed the deterministic contract and SQL "
                                        "lint."
                                    ),
                                }
                            ]
                        else:
                            second_review, second_review_model_attempts = await _review(
                                client,
                                request,
                                extension,
                                repaired,
                                attempt=2,
                                invocations=invocations,
                            )
                            steps.append(
                                {
                                    "role": "query_review",
                                    "attempt": 2,
                                    "model_attempts": second_review_model_attempts,
                                    "decision": second_review["decision"],
                                    "deterministic_findings": 0,
                                }
                            )
                            if second_review["decision"] != "approve":
                                raise QueryContractError(
                                    "repaired query did not pass independent re-review"
                                )
                            final_checks = list(second_review["checks"])
                        result = _finalize(
                            question,
                            extension,
                            repaired,
                            _semantic_checks(
                                _lint_validation_checks(lint_history, final_checks),
                                question,
                                extension,
                            ),
                            profile_id=request.profile.id,
                            model_collaboration=model_collaboration,
                        )
                    else:
                        model_collaboration = (
                            {
                                "writer": {
                                    "model": request.profile.models["query_generate"],
                                    "candidate": deepcopy(candidate),
                                    "lintFindings": writer_findings,
                                    **(
                                        {"disposition": "selected"}
                                        if is_revision
                                        else {}
                                    ),
                                },
                                "reviewer": {
                                    "model": request.profile.models["query_review"],
                                    "decision": "approve",
                                    "checks": deepcopy(review["checks"]),
                                    **(
                                        {"disposition": "selected"}
                                        if is_revision
                                        else {}
                                    ),
                                },
                                "finalLintFindings": [],
                            }
                            if collaborative_review
                            else None
                        )
                        result = _finalize(
                            question,
                            extension,
                            candidate,
                            _semantic_checks(
                                _lint_validation_checks(
                                    lint_history, list(review["checks"])
                                ),
                                question,
                                extension,
                            ),
                            profile_id=request.profile.id,
                            model_collaboration=model_collaboration,
                        )
                except asyncio.CancelledError as exc:
                    result = _rejected(
                        question,
                        extension,
                        message="Query review was cancelled.",
                        check_name="query_review",
                        diagnostic_candidate={
                            "executable": False,
                            "candidate": candidate,
                        },
                        profile_id=request.profile.id,
                    )
                    steps.append(
                        {
                            "role": "query_review",
                            "status": "cancelled",
                            "message": str(exc),
                        }
                    )
                    _attach_model_evidence(result, request, invocations)
                    _write_trace(request, extension, result, steps)
                    raise
                except Exception as exc:
                    logger.warning("Catalyst query review failed: %s", exc)
                    if is_revision and collaborative_review:
                        if model_collaboration is not None:
                            model_collaboration = deepcopy(model_collaboration)
                            model_collaboration["writer"]["disposition"] = (
                                "retained_unselected"
                            )
                            model_collaboration["reviewer"]["disposition"] = (
                                "diagnostic_only"
                            )
                            if not model_collaboration[
                                "finalLintFindings"
                            ] and isinstance(exc, QueryContractError):
                                model_collaboration["finalLintFindings"] = [
                                    _contract_lint_finding(exc)
                                ]
                        else:
                            repaired_candidate = locals().get("repaired")
                            review_result = locals().get("review")
                            if (
                                isinstance(repaired_candidate, Mapping)
                                and isinstance(review_result, Mapping)
                                and review_result.get("decision") == "repair"
                            ):
                                repair_findings = locals().get("repaired_findings")
                                if not isinstance(repair_findings, list):
                                    repair_findings = (
                                        [_contract_lint_finding(exc)]
                                        if isinstance(exc, QueryContractError)
                                        else []
                                    )
                                model_collaboration = {
                                    "writer": {
                                        "model": request.profile.models[
                                            "query_generate"
                                        ],
                                        "candidate": deepcopy(candidate),
                                        "lintFindings": deepcopy(writer_findings),
                                        "disposition": "retained_unselected",
                                    },
                                    "reviewer": {
                                        "model": request.profile.models["query_review"],
                                        "decision": "repair",
                                        "candidate": deepcopy(repaired_candidate),
                                        "checks": deepcopy(
                                            review_result.get("checks", [])
                                        ),
                                        "disposition": "diagnostic_only",
                                    },
                                    "finalLintFindings": deepcopy(repair_findings),
                                }
                            else:
                                model_collaboration = {
                                    "writer": {
                                        "model": request.profile.models[
                                            "query_generate"
                                        ],
                                        "candidate": deepcopy(candidate),
                                        "lintFindings": deepcopy(writer_findings),
                                        "disposition": "retained_unselected",
                                    },
                                    "reviewer": {
                                        "model": request.profile.models["query_review"],
                                        "decision": "failed",
                                        "checks": [],
                                        "disposition": "diagnostic_only",
                                    },
                                    "finalLintFindings": deepcopy(writer_findings),
                                }
                    diagnostic_candidate = {
                        "executable": False,
                        "candidate": locals().get("repaired", candidate),
                    }
                    if isinstance(exc, QueryReviewError):
                        diagnostic_candidate["rawOutput"] = exc.raw_output
                    result = _rejected(
                        question,
                        extension,
                        message=f"Query review failed: {exc}",
                        check_name="query_review",
                        diagnostic_candidate=diagnostic_candidate,
                        profile_id=request.profile.id,
                        model_collaboration=model_collaboration,
                    )
                    steps.append({"role": "query_review", "status": "failed"})

    steps.append({"role": "query_finalize", "status": result["status"]})
    _attach_model_evidence(result, request, invocations)
    _write_trace(request, extension, result, steps)
    yield "result", json.dumps(result, separators=(",", ":"))
