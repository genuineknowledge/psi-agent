import type { Diagnostic } from "./types.js";

/** A DSL syntax capability required by one planned function. */
export interface PlannedSyntax {
  readonly description: string;
  /**
   * `null` explicitly means the planner could not find matching DSL syntax; it
   * must not invent a name. A non-null name must be non-empty after trimming.
   * Empty or whitespace-only names are invalid, and the future checker treats
   * them as unmapped.
   */
  readonly name: string | null;
}

export interface PlannedFunction {
  readonly id: string;
  readonly description: string;
  /**
   * Must contain at least one mapping. Empty means invalid or incomplete
   * planner output; the future checker warns and sets `canAuthorWorkflow` to
   * false.
   */
  readonly syntax: readonly PlannedSyntax[];
}

export interface PlanningCheckResult {
  /**
   * Means only that the declared planned functions have acceptable syntax
   * mappings. It does not imply DSL parse validity, backend lowering, or
   * successful execution.
   */
  readonly canAuthorWorkflow: boolean;
  readonly diagnostics: readonly Diagnostic[];
}

/**
 * Haitun must call this after listing planned functions and before authoring the
 * DSL.
 *
 * The caller supplies the syntax names that are actually available; a
 * non-empty name is not assumed to exist. A future implementation emits
 * warnings for null or unavailable syntax mappings and sets
 * `canAuthorWorkflow` to false. It only checks declared items and cannot prove
 * that the LLM listed every required function. The current implementation
 * throws only because this module is not wired or implemented; a future
 * missing mapping is a normal result, not an exception.
 */
export function checkPlannedFunctions(
  functions: readonly PlannedFunction[],
  availableSyntaxNames: readonly string[],
): PlanningCheckResult {
  void functions;
  void availableSyntaxNames;
  throw new Error("FusionFlow Next planning check is not implemented.");
}
