import { describe, expect, it } from "vitest";
import { resolveTheme } from "./theme";

describe("resolveTheme", () => {
  it("follows the system only while no explicit choice is made", () => {
    expect(resolveTheme("system", false)).toBe("g10");
    expect(resolveTheme("system", true)).toBe("g100");
  });

  it("lets an explicit choice override the system in both directions", () => {
    // The failure worth guarding: a viewer who chose light, on a machine set
    // to dark, must get light -- and the reverse.
    expect(resolveTheme("light", true)).toBe("g10");
    expect(resolveTheme("dark", false)).toBe("g100");
  });

  it("uses Gray 10 for light, as the design contract mandates", () => {
    expect(resolveTheme("light", false)).toBe("g10");
  });
});
