import { describe, expect, it } from "vitest";
import { workbenchEditorDigest } from "./editorDigest";

describe("workbench editor digest", () => {
  it("matches the gateway RFC 8785 golden vector", () => {
    expect(
      workbenchEditorDigest({
        sql: "SELECT 1",
        parameters: [],
        expectedColumns: [],
      }),
    ).toBe(
      "82d9696f92e64acb0c4edba843633c97eb23fd3f22887d93755eb86971855105",
    );
  });
});
