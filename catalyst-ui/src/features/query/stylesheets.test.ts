import { describe, expect, it } from "vitest";
import { readFileSync, readdirSync, statSync } from "node:fs";
import { join } from "node:path";

const walk = (dir: string): string[] =>
  readdirSync(dir).flatMap((entry: string) => {
    const full = join(dir, entry);
    return statSync(full).isDirectory()
      ? walk(full)
      : full.endsWith(".css")
        ? [full]
        : [];
  });

/**
 * A stylesheet with one unbalanced brace does not fail to build, does not warn,
 * and does not throw. Everything after the break is swallowed into an
 * unterminated block and silently stops applying — which is exactly what
 * happened: a rule inside `@media (max-width: 48rem)` was rewritten and took
 * the media query's closing brace with it, so every rule below it in the file
 * stopped applying above 48rem. The page rendered, the tests passed, and the
 * layout was gone.
 */
describe("stylesheets", () => {
  const files = walk("src");

  it("finds stylesheets to check", () => {
    expect(files.length).toBeGreaterThan(0);
  });

  it.each(files)("%s has balanced braces", (file) => {
    const source = readFileSync(file, "utf8");
    const opens = (source.match(/\{/g) ?? []).length;
    const closes = (source.match(/\}/g) ?? []).length;
    expect({ file, opens, closes }).toEqual({ file, opens, closes: opens });
  });

  it.each(files)("%s never closes more than it opens", (file) => {
    const source = readFileSync(file, "utf8");
    let depth = 0;
    for (const character of source) {
      if (character === "{") depth += 1;
      if (character === "}") depth -= 1;
      expect(depth).toBeGreaterThanOrEqual(0);
    }
    expect(depth).toBe(0);
  });
});
