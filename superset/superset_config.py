"""Pinned local-demo Superset configuration for Catalyst.

The Superset metadata database is deliberately separate from the read-only
analytics Database asset imported from Catalyst's native bundle.
"""

from __future__ import annotations

import os


SECRET_KEY = os.environ["SUPERSET_SECRET_KEY"]
SQLALCHEMY_DATABASE_URI = os.environ["CATALYST_SUPERSET_METADATA_DSN"]

WTF_CSRF_ENABLED = True
TALISMAN_ENABLED = False
SESSION_COOKIE_HTTPONLY = True
SESSION_COOKIE_SAMESITE = "Lax"

# Keep the local renderer bounded. Catalyst's virtual datasets remain the
# configuration authority and the analytics database role enforces read-only.
ROW_LIMIT = 5000
SUPERSET_WEBSERVER_TIMEOUT = 120
