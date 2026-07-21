import type { CheckResult, GenerateResult } from "./types.js";

/**
 * Future implementations consume checker output only. When `canGenerate` is
 * false, they return `{ code: null, diagnostics }` and never placeholder or
 * approximate TypeScript. Generation is deterministic and treats DSL values
 * as data rather than raw TypeScript; diagnostics preserve stable source order.
 * It does not parse, repeat static checks, execute workflows, or introduce a
 * backend registry. This placeholder is not wired into Haitun, and the explicit
 * current throw only prevents accidental use before implementation. Future
 * unsupported workflows are normal `{ code: null, diagnostics }` results; only
 * infrastructure or programming faults may throw.
 */
export function generateTypeScript(checkResult: CheckResult): GenerateResult {
  void checkResult;
  throw new Error("FusionFlow Next TypeScript generator is not implemented.");
}
