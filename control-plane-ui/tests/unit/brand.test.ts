import { describe, expect, it } from "vitest";
import { BRAND } from "@/lib/brand";

describe("RaiCentral brand tokens", () => {
  it("matches logo identity colors", () => {
    expect(BRAND.navy).toBe("#122132");
    expect(BRAND.teal).toBe("#008467");
  });
});