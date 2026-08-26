#!/usr/bin/env node
/*
 * Prepare Superset for the full-scenario demo, and print the permalink key
 * the spec needs.
 *
 * Two things the shipped stack does not provide:
 *
 * 1. `superset-init.sh` provisions only the OpenELIS analytics connection, so
 *    the HIV database the workbench queries (catalyst_analytics_hiv) is not
 *    reachable from Superset at all. This adds it, read-only.
 * 2. Selecting a database and schema in SQL Lab is a three-interaction
 *    popover. A SQL Lab permalink encodes both plus the tab name in one URL,
 *    which is steadier to drive and reads better on camera.
 *
 * Idempotent: re-running reuses the existing connection and mints a fresh
 * permalink. Usage:
 *   node scripts/superset-demo-fixture.mjs            # prints the key
 *   SUPERSET_URL=... SUPERSET_ADMIN_PASSWORD=... node scripts/...
 */

const SUPERSET_URL = process.env.SUPERSET_URL ?? "http://127.0.0.1:18088";
const USER = process.env.SUPERSET_ADMIN_USERNAME ?? "admin";
const PASSWORD = process.env.SUPERSET_ADMIN_PASSWORD ?? "admin";
const DB_NAME = "Catalyst OpenMRS HIV analytics";
const ANALYTICS_URI =
  process.env.CATALYST_SUPERSET_HIV_URI ??
  "postgresql+psycopg2://catalyst_readonly:demo-readonly-change-me@analytics-db:5432/catalyst_analytics_hiv";

const login = async () => {
  const res = await fetch(`${SUPERSET_URL}/api/v1/security/login`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      username: USER,
      password: PASSWORD,
      provider: "db",
      refresh: false,
    }),
  });
  if (!res.ok) throw new Error(`superset login failed: ${res.status}`);
  return (await res.json()).access_token;
};

/* Superset's API is CSRF-protected even for token auth: the token comes from
 * /security/csrf_token/ and must travel with that endpoint's session cookie. */
const csrf = async (token) => {
  const res = await fetch(`${SUPERSET_URL}/api/v1/security/csrf_token/`, {
    headers: { Authorization: `Bearer ${token}` },
  });
  const cookie = res.headers.get("set-cookie") ?? "";
  return { csrfToken: (await res.json()).result, cookie: cookie.split(";")[0] };
};

const api = async (path, { token, csrfToken, cookie, method = "GET", body }) => {
  const res = await fetch(`${SUPERSET_URL}${path}`, {
    method,
    headers: {
      Authorization: `Bearer ${token}`,
      "Content-Type": "application/json",
      "X-CSRFToken": csrfToken,
      Referer: SUPERSET_URL,
      ...(cookie ? { Cookie: cookie } : {}),
    },
    ...(body ? { body: JSON.stringify(body) } : {}),
  });
  const text = await res.text();
  if (!res.ok) throw new Error(`${method} ${path} -> ${res.status}: ${text}`);
  return text ? JSON.parse(text) : {};
};

const token = await login();
const { csrfToken, cookie } = await csrf(token);
const auth = { token, csrfToken, cookie };

const existing = await api(
  `/api/v1/database/?q=(filters:!((col:database_name,opr:eq,value:'${encodeURIComponent(DB_NAME)}')))`,
  auth,
);
let dbId = existing.result?.[0]?.id;
if (!dbId) {
  const created = await api("/api/v1/database/", {
    ...auth,
    method: "POST",
    body: {
      database_name: DB_NAME,
      sqlalchemy_uri: ANALYTICS_URI,
      expose_in_sqllab: true,
      allow_ctas: false,
      allow_cvas: false,
      allow_dml: false,
    },
  });
  dbId = created.id;
  console.error(`created database connection ${dbId} (${DB_NAME})`);
} else {
  console.error(`reusing database connection ${dbId} (${DB_NAME})`);
}

const permalink = await api("/api/v1/sqllab/permalink", {
  ...auth,
  method: "POST",
  body: {
    dbId,
    name: "HIV program snapshot",
    schema: "analytics",
    sql: "",
  },
});

console.error(`permalink: ${permalink.url}`);
process.stdout.write(`${permalink.key}\n`);
