import { execFileSync } from "node:child_process";
import { resolve } from "node:path";

/*
 * Run the pinned Superset importer against the live stack — the seam between
 * "Superset bundle ready" and "Imported".
 *
 * The MVP deliberately has no Superset REST publication: Catalyst writes a
 * native bundle to the outbox and an operator runs the pinned CLI. This
 * helper is that operator, so the e2e run exercises the seam the product
 * actually has, and the recording shows the status flip honestly.
 *
 * Environment it must match (learned the hard way, see the demo issue log):
 * - COMPOSE_PROJECT_NAME must be the RUNNING stack's project, or compose
 *   creates a second network and the importer can't see Superset.
 * - SUPERSET_PORT must be the published port (18088 on the isolated stack) —
 *   the importer stamps it into the receipt's public URL, which becomes the
 *   product's own "Open Superset" link. Left defaulted, that link 404s.
 * - The stack checkout must be the one whose runtime/ the gateway mounts;
 *   a bundle published by the gateway is invisible to an importer run from
 *   a different checkout of the same repo.
 */
export const runSupersetImport = (): string => {
  const stackDir = resolve(
    process.env.CATALYST_STACK_DIR ?? resolve(__dirname, "..", "..", ".."),
  );
  const projectName =
    process.env.CATALYST_STACK_PROJECT ?? "catalyst-mvp-isolated";
  const supersetPort = process.env.CATALYST_SUPERSET_PORT ?? "18088";
  const overrideFile = process.env.CATALYST_STACK_OVERRIDE ?? "";
  const revision = execFileSync("git", ["rev-parse", "HEAD"], {
    cwd: stackDir,
    encoding: "utf-8",
  }).trim();

  const composeArgs = [
    "compose",
    "--env-file",
    ".env",
    "-f",
    "docker-compose.mvp.yml",
    ...(overrideFile ? ["-f", overrideFile] : []),
    "--profile",
    "superset-import",
    "run",
    "--rm",
    "--no-deps",
    "superset-importer",
    "import",
  ];
  const output = execFileSync("docker", composeArgs, {
    cwd: stackDir,
    encoding: "utf-8",
    env: {
      ...process.env,
      COMPOSE_PROJECT_NAME: projectName,
      SUPERSET_PORT: supersetPort,
      CATALYST_IMPORTER_REVISION: revision,
    },
    stdio: ["ignore", "pipe", "pipe"],
    timeout: 300_000,
  });
  const lastLine = output.trim().split("\n").at(-1) ?? "";
  const receipt = JSON.parse(lastLine) as { status: string; dashboardUrl?: string };
  if (receipt.status !== "imported") {
    throw new Error(`superset import did not succeed: ${lastLine}`);
  }
  return receipt.dashboardUrl ?? "";
};
