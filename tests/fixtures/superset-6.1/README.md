# Superset 6.1 five-family fixture

`catalyst-dashboard-five-family.zip` is the reviewed, root-wrapped native
Superset fixture for table, KPI, time-series line/area, grouped/stacked bar,
and proportion-bar behavior. It contains two virtual Datasets, seven charts,
one Dashboard, the deterministic read-only local-demo Database asset, and the
extra Catalyst manifest that Superset ignores during native import.

Regenerate it from the deterministic input with:

```bash
catalyst-gateway/.venv/bin/python tests/fixtures/superset-6.1/generate_fixture.py
```

Any byte change requires review of `fixture.json`, the generator revision, the
pinned Superset clean-import result, and the Dashboard Builder contract.
