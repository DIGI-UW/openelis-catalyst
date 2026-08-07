# Catalyst Gateway

The Gateway owns Catalyst's governed-query orchestration, read-only execution,
lineage, and Dashboard Builder APIs. It invokes role models only through a
named med-agent-hub query profile; it does not expose a generic chat-completion
relay or call a model router directly.

See the repository
[product specification](../docs/specification.md),
[roadmap](../docs/roadmap.md), and
[hub client contract](../docs/med-agent-hub.md).

Use `uv sync` to set up dependencies.
