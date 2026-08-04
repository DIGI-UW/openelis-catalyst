#!/usr/bin/env python3
"""Normalize a known OpenELIS 3.2.1.x legacy Specimen export limitation.

Directly seeded legacy samples preserve collection time, but the OEToFhir
transform stamps Specimen.receivedTime at export time. For the synthetic
Catalyst fixture only (CAT* accessions), this transform preserves the intended
OpenELIS receipt-to-release interval by subtracting each source analysis interval
from its actual FHIR Observation.issued instant. Resources are updated through
the real HAPI FHIR transaction API before Data Pipes runs.
"""

from __future__ import annotations

import argparse
import json
import ssl
import sys
import urllib.parse
import urllib.request
from datetime import datetime, timedelta
from typing import Any


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--fhir-url", required=True)
    parser.add_argument("--client-cert", required=True)
    parser.add_argument("--expected", type=int, default=1152)
    parser.add_argument("--turnaround-map", required=True)
    parser.add_argument("--insecure", action="store_true")
    return parser.parse_args()


def request_json(
    url: str,
    context: ssl.SSLContext,
    *,
    method: str = "GET",
    payload: dict[str, Any] | None = None,
) -> dict[str, Any]:
    body = None if payload is None else json.dumps(payload).encode("utf-8")
    request = urllib.request.Request(
        url,
        data=body,
        method=method,
        headers={"Accept": "application/fhir+json", "Content-Type": "application/fhir+json"},
    )
    with urllib.request.urlopen(request, context=context, timeout=120) as response:
        value = json.load(response)
    if not isinstance(value, dict):
        raise RuntimeError(f"FHIR response from {url} was not an object")
    return value


def next_link(bundle: dict[str, Any]) -> str | None:
    for link in bundle.get("link", []):
        if link.get("relation") == "next" and isinstance(link.get("url"), str):
            return str(link["url"])
    return None


def catalyst_accession(resource: dict[str, Any]) -> str | None:
    value = (resource.get("accessionIdentifier") or {}).get("value")
    if isinstance(value, str) and value.startswith("CAT"):
        return value
    return None


def load_turnaround_map(path: str) -> dict[str, int]:
    stream = sys.stdin if path == "-" else open(path, encoding="utf-8")
    try:
        mapping: dict[str, int] = {}
        for line in stream:
            accession, minutes = line.rstrip("\n").split("\t", 1)
            mapping[accession] = int(minutes)
        return mapping
    finally:
        if stream is not sys.stdin:
            stream.close()


def fixture_accession(fhir_accession: str, mapping: dict[str, int]) -> str:
    if fhir_accession in mapping:
        return fhir_accession
    if fhir_accession.endswith("-1") and fhir_accession[:-2] in mapping:
        return fhir_accession[:-2]
    raise RuntimeError(f"No OpenELIS turnaround mapping for {fhir_accession}")


def transaction(resources: list[dict[str, Any]]) -> dict[str, Any]:
    return {
        "resourceType": "Bundle",
        "type": "transaction",
        "entry": [
            {
                "resource": resource,
                "request": {"method": "PUT", "url": f"Specimen/{resource['id']}"},
            }
            for resource in resources
        ],
    }


def resources(
    base_url: str,
    resource_type: str,
    context: ssl.SSLContext,
) -> list[dict[str, Any]]:
    base_origin = urllib.parse.urlsplit(base_url)
    url: str | None = (
        f"{base_url}/{resource_type}?{urllib.parse.urlencode({'_count': 200})}"
    )
    found: list[dict[str, Any]] = []
    while url:
        bundle = request_json(url, context)
        found.extend(
            resource
            for entry in bundle.get("entry", [])
            if isinstance((resource := entry.get("resource")), dict)
        )
        internal_next = next_link(bundle)
        if internal_next:
            parsed_next = urllib.parse.urlsplit(internal_next)
            url = urllib.parse.urlunsplit(
                (
                    base_origin.scheme,
                    base_origin.netloc,
                    parsed_next.path,
                    parsed_next.query,
                    "",
                )
            )
        else:
            url = None
    return found


def main() -> None:
    args = parse_args()
    base_url = args.fhir_url.rstrip("/")
    turnaround_by_accession = load_turnaround_map(args.turnaround_map)
    context = ssl.create_default_context()
    context.load_cert_chain(args.client_cert)
    if args.insecure:
        context.check_hostname = False
        context.verify_mode = ssl.CERT_NONE

    issued_by_specimen: dict[str, datetime] = {}
    for observation in resources(base_url, "Observation", context):
        reference = (observation.get("specimen") or {}).get("reference")
        issued = observation.get("issued")
        if isinstance(reference, str) and isinstance(issued, str):
            issued_by_specimen[reference.rsplit("/", 1)[-1]] = datetime.fromisoformat(
                issued.replace("Z", "+00:00")
            )

    specimens: list[dict[str, Any]] = []
    for resource in resources(base_url, "Specimen", context):
        fhir_accession = catalyst_accession(resource)
        if not fhir_accession:
            continue
        accession = fixture_accession(fhir_accession, turnaround_by_accession)
        specimen_id = str(resource.get("id"))
        issued = issued_by_specimen.get(specimen_id)
        if issued is None:
            raise RuntimeError(f"No issued Observation for Specimen {specimen_id}")
        resource["receivedTime"] = (
            issued - timedelta(minutes=turnaround_by_accession[accession])
        ).isoformat(timespec="seconds")
        specimens.append(resource)

    if len(specimens) != args.expected:
        raise SystemExit(
            f"expected {args.expected} Catalyst Specimens, found {len(specimens)}"
        )

    for start in range(0, len(specimens), 100):
        batch = specimens[start : start + 100]
        response = request_json(
            base_url,
            context,
            method="POST",
            payload=transaction(batch),
        )
        if response.get("resourceType") != "Bundle":
            raise RuntimeError("FHIR transaction did not return a Bundle")

    print(f"Normalized {len(specimens)} Catalyst Specimen receipt timestamps")


if __name__ == "__main__":
    main()
