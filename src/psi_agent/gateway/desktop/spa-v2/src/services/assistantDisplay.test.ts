import { describe, expect, it } from "vitest";
import {
  plainTextFromMarkdown,
  plainTextPreview,
  preferResultBelowRule,
} from "./assistantDisplay";

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

describe("plainTextFromMarkdown", () => {
  it("strips headings, bold, and inline code for context previews", () => {
    const raw =
      "好问题！具体来说：\n\n## 我当前的 SSE 机制\n\n**连接方式**: 走 HTTP，以 `text/event-stream` 推送。\n\n**消息格式** 如下";
    expect(plainTextFromMarkdown(raw)).toBe(
      "好问题！具体来说： 我当前的 SSE 机制 连接方式: 走 HTTP，以 text/event-stream 推送。 消息格式 如下",
    );
  });

  it("keeps link labels and drops targets", () => {
    expect(plainTextFromMarkdown("见 [文档](https://example.com) 说明")).toBe(
      "见 文档 说明",
    );
  });

  it("clips with ellipsis via plainTextPreview", () => {
    const long = "字".repeat(130);
    expect(plainTextPreview(long, 120)).toBe(`${"字".repeat(120)}…`);
  });
});
