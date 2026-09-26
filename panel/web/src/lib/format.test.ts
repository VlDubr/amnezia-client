import { describe, expect, it } from "vitest";
import { formatBytes, formatDate } from "./format";

describe("formatBytes", () => {
  it("formats sizes with binary units", () => {
    expect(formatBytes(0)).toBe("0 B");
    expect(formatBytes(512)).toBe("512 B");
    expect(formatBytes(1536)).toBe("1.5 KB");
    expect(formatBytes(5 * 1024 ** 3)).toBe("5.0 GB");
  });
});

describe("formatDate", () => {
  it("formats ISO dates for the locale", () => {
    expect(formatDate("2026-09-30", "ru")).toBe("30.09.2026");
    expect(formatDate("2026-09-30", "en")).toBe("Sep 30, 2026");
    expect(formatDate(null, "ru")).toBe("—");
  });
});
