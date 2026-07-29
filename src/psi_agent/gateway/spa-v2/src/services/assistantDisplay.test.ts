import { describe, expect, it } from "vitest";
import { preferResultBelowRule } from "./assistantDisplay";

describe("preferResultBelowRule", () => {
  it("returns text unchanged when there is no rule", () => {
    expect(preferResultBelowRule("plain reply")).toBe("plain reply");
  });

  it("prefers body below --- when head is a short plan", () => {
    const raw =
      "我先看看桌面路径，再写脚本。\n\n---\n\n```python\nprint(1)\n```";
    expect(preferResultBelowRule(raw)).toBe("```python\nprint(1)\n```");
  });

  it("keeps full text when head is long (likely a real sectioned doc)", () => {
    const head = "x".repeat(801);
    const raw = `${head}\n\n---\n\ntail`;
    expect(preferResultBelowRule(raw)).toBe(raw);
  });

  it("keeps full text when tail is empty", () => {
    const raw = "only head\n\n---\n\n";
    expect(preferResultBelowRule(raw)).toBe(raw);
  });
});
