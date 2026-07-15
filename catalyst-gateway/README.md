# Catalyst Gateway

The Gateway is Catalyst's OpenAI-compatible HTTP boundary.

The current implementation forwards chat completions to the legacy
RouterAgent. The target architecture keeps the public boundary but delegates
inference to med-agent-hub through the Catalyst integration layer.

See the repository
[product specification](../docs/specification.md),
[roadmap](../docs/roadmap.md), and
[hub client contract](../docs/med-agent-hub.md).

Use `uv sync` to set up dependencies.
