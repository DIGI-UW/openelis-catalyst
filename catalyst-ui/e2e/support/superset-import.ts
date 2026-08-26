import { execFileSync } from "node:child_process";
import { readFileSync } from "node:fs";
import { resolve } from "node:path";

/*
 * Run the pinned Superset importer against the live stack — the seam between
 * "Superset bundle ready" and "Imported" — through the Clinical AI
 * Validation Harness operator wrapper. The wrapper owns the isolated Compose
 * assembly, ports, sibling Hub context, and dependency startup/waits.
 *
 * The MVP deliberately has no Superset REST publication: Catalyst writes a
 * native bundle to the outbox and an operator runs the pinned CLI. This
 * helper is that operator, so the e2e run exercises the seam the product
 * actually has, and the recording shows the status flip honestly.
 *
 * CATALYST_HARNESS_DIR must identify the checkout that owns the running
 * isolated stack. Its wrapper verifies the exact pinned Catalyst checkout,
 * so the importer and Gateway necessarily share one outbox and receipt tree.
 */
export const runSupersetImport = (expectedBundleDigest: string): string => {
  if (!/^[a-f0-9]{64}$/.test(expectedBundleDigest)) {
    throw new Error("expected Superset bundle digest is not a SHA-256 value");
  }
  const configuredHarnessDir = process.env.CATALYST_HARNESS_DIR?.trim();
  if (!configuredHarnessDir) {
    throw new Error("CATALYST_HARNESS_DIR is required for the isolated import");
  }
  const harnessDir = resolve(configuredHarnessDir);
  const currentPointerPath = resolve(
    harnessDir,
    "targets",
    "catalyst",
    "runtime",
    "superset",
    "outbox",
    "current.json",
  );
  const currentPointer = JSON.parse(
    readFileSync(currentPointerPath, "utf-8"),
  ) as { bundle?: { sha256?: unknown } };
  if (currentPointer.bundle?.sha256 !== expectedBundleDigest) {
    throw new Error(
      `Superset outbox points to ${String(currentPointer.bundle?.sha256 ?? "no bundle")}; expected ${expectedBundleDigest}`,
    );
  }
  const wrapper = resolve(harnessDir, "scripts", "catalyst-mvp.sh");
  const output = execFileSync(wrapper, ["superset-import"], {
    cwd: harnessDir,
    encoding: "utf-8",
    env: process.env,
    stdio: ["ignore", "pipe", "pipe"],
    timeout: 300_000,
  });
  const lastLine = output.trim().split("\n").at(-1) ?? "";
  const receipt = JSON.parse(lastLine) as {
    status: string;
    bundleDigest?: string;
    dashboardUrl?: string;
  };
  if (!["imported", "already_imported"].includes(receipt.status)) {
    throw new Error(`superset import did not succeed: ${lastLine}`);
  }
  if (receipt.bundleDigest !== expectedBundleDigest) {
    throw new Error(
      `superset imported ${receipt.bundleDigest ?? "no bundle"}; expected ${expectedBundleDigest}`,
    );
  }
  return receipt.dashboardUrl ?? "";
};
