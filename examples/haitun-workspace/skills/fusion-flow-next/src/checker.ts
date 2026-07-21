import type { Workflow } from "./core-ir.js";
import type { CheckResult } from "./types.js";

/**
 * Checks successfully parsed Workflow Core IR. The current stub throws only
 * because the checker implementation is unavailable. Once implemented,
 * ordinary workflow errors and lowering diagnostics are returned in the
 * CheckResult diagnostics list rather than thrown.
 *
 * Future implementations own static workflow semantics such as operator
 * contracts and arity, duplicates, and reference integrity. For example, S01
 * requires the first argument of `input_workflow` and `output_workflow` to
 * equal the enclosing workflow name. For TypeScript lowering, the checker only
 * answers whether lossless/exact lowering exists; it does not build, rewrite,
 * or approximate emitter shapes.
 *
 * This boundary must not parse source, emit TypeScript, or execute workflows.
 */
export function checkWorkflow(coreIR: Workflow): CheckResult {
  void coreIR;
  throw new Error("FusionFlow Next checker is not implemented.");
}
