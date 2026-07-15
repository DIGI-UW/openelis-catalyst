# Catalyst MCP

MCP server for deterministic Catalyst schema/context and query-policy tools.

MCP does not execute user queries and does not own LLM orchestration. The
current implementation serves mock approved schema context and SQL allowlist
checks; the target architecture applies those boundaries to approved analytics
views independently of med-agent-hub profile review.

See the repository
[product specification](../docs/specification.md),
[roadmap](../docs/roadmap.md), and
[hub client contract](../docs/med-agent-hub.md).

Use `uv sync` to set up dependencies.
