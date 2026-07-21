import type { Workflow } from "./core-ir.js";

/**
 * A source location.
 *
 * Both line and column are 1-based. Public diagnostics deliberately omit
 * character offsets to avoid UTF-16/UTF-32 ambiguity.
 */
export interface SourcePosition {
  readonly line: number;
  readonly column: number;
}

export interface SourceSpan {
  readonly start: SourcePosition;
  /** Exclusive end position; spans use the half-open interval `[start, end)`. */
  readonly end: SourcePosition;
}

export type DiagnosticSeverity = "error" | "warning";

export interface Diagnostic {
  readonly severity: DiagnosticSeverity;
  readonly message: string;
  readonly span?: SourceSpan;
  /** An identifier from the language design review such as S01; never a diagnostic code, URL, or source span. */
  readonly designReference?: string;
}

export interface ParseResult {
  /** Non-null exactly when this phase produced no error diagnostics. */
  readonly coreIR: Workflow | null;
  /** Parser diagnostics only; callers combine phase results when needed. */
  readonly diagnostics: readonly Diagnostic[];
}

/**
 * The checker only receives non-null Core IR from a successful parse;
 * parse failures never produce CheckResult.
 */
export interface CheckResult {
  readonly coreIR: Workflow;
  /** Checker diagnostics only; parser diagnostics are not repeated here. */
  readonly diagnostics: readonly Diagnostic[];
  /**
   * True exactly when there are no checker errors and exact TypeScript lowering
   * is supported.
   */
  readonly canGenerate: boolean;
}

/**
 * Non-null `code` means generation succeeded and may coexist with warning
 * diagnostics. Any error diagnostic requires `code: null`.
 */
export interface GenerateResult {
  /** Null means no TypeScript; the generator must never emit placeholder or approximate code. */
  readonly code: string | null;
  /** Generator diagnostics only; parser and checker diagnostics are not repeated here. */
  readonly diagnostics: readonly Diagnostic[];
}
