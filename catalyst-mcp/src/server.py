import os
from typing import Optional

from mcp.server.fastmcp import FastMCP

from .tools import fhir_tools, schema_tools

host = os.getenv("MCP_HOST", "0.0.0.0")
port = int(os.getenv("MCP_PORT", "9102"))

mcp = FastMCP(
    "Catalyst Schema Server",
    host=host,
    port=port,
    json_response=True,
)


@mcp.tool()
def get_query_context(user_query: str) -> dict[str, object]:
    """
    Get query context (schema bundle) for allowed tables only.

    Provides the LLM with schema information (columns, primary keys, foreign keys)
    for tables in the allowlist, enabling accurate SQL generation within safe boundaries.
    """
    return schema_tools.get_query_context(user_query)


@mcp.tool()
def validate_sql(sql: str) -> dict[str, object]:
    """
    Validate SQL against guardrails and allowlist.

    Ensures generated SQL:
    - Uses only SELECT/WITH (no DDL/DML)
    - References only allowlisted tables
    - Provides warnings for potential issues (missing LIMIT, SELECT *)
    """
    return schema_tools.validate_sql(sql)


@mcp.tool()
def search_patient(query: str) -> dict[str, object]:
    """Search for patients by name or identifier. Returns a FHIR searchset Bundle."""
    return fhir_tools.search_patient(query)


@mcp.tool()
def get_patient_context(patient_id: str) -> dict[str, object]:
    """Demographic + identifier summary for a single patient (FHIR Patient resource)."""
    return fhir_tools.get_patient_context(patient_id)


@mcp.tool()
def get_service_requests(
    patient_id: str,
    date_start: Optional[str] = None,
    date_end: Optional[str] = None,
) -> dict[str, object]:
    """Lab orders (ServiceRequest) for a patient; optional date range (YYYY-MM-DD)."""
    return fhir_tools.get_service_requests(patient_id, date_start, date_end)


@mcp.tool()
def get_observations(patient_id: str, test_code: Optional[str] = None) -> dict[str, object]:
    """Lab result Observation resources for a patient; optional test-code filter."""
    return fhir_tools.get_observations(patient_id, test_code)


@mcp.tool()
def get_diagnostic_reports(patient_id: str) -> dict[str, object]:
    """DiagnosticReport resources for a patient."""
    return fhir_tools.get_diagnostic_reports(patient_id)


@mcp.tool()
def get_resource_by_reference(reference: str) -> dict[str, object]:
    """Resolve an arbitrary FHIR reference, e.g. 'Observation/12345'."""
    return fhir_tools.get_resource_by_reference(reference)


@mcp.tool()
def build_patient_lab_timeline(patient_id: str) -> dict[str, object]:
    """Chronological merge of Observation + DiagnosticReport events for a patient."""
    return fhir_tools.build_patient_lab_timeline(patient_id)


if __name__ == "__main__":
    mcp.run(transport="streamable-http")
