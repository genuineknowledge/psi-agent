/** Catalog-owned concept referenced by workflow constants and operators. */
export class Concept {
  public constructor(readonly name: string) {}
}

/** A typed identity or literal declared or used by a workflow. */
export class Constant {
  public constructor(
    readonly symbol: string,
    readonly belongConcepts: readonly Concept[],
  ) {}
}

/** Catalog-owned operator signature. */
export class Operator {
  public constructor(
    readonly name: string,
    readonly inputConcepts: readonly Concept[] = [],
    readonly outputConcept: Concept | null = null,
  ) {}

  public get arity(): number {
    return this.inputConcepts.length;
  }
}

/** An operator applied to recursive term arguments. */
export class CompoundTerm {
  readonly arguments: readonly Term[];

  public constructor(
    readonly operator: Operator,
    arguments_: readonly Term[],
  ) {
    this.arguments = arguments_;
  }
}

/** Ordered list values remain ordinary terms in Workflow Core IR. */
export class ListTerm {
  public constructor(readonly items: readonly Term[]) {}
}

export type RelationSymbol = "=" | "!=" | "<" | "<=" | ">" | ">=";

/** Atomic relation between two terms. */
export class Assertion {
  public constructor(
    readonly lhs: Term,
    readonly rhs: Term,
    readonly relationSymbol: RelationSymbol = "=",
  ) {}
}

export type LogicalConnective = "NOT" | "AND" | "OR";

/** Workflow condition built from assertions with NOT, AND, or OR. */
export class ConnectiveFormula {
  readonly formulaLeft: Formula;
  readonly connective: LogicalConnective;
  readonly formulaRight: Formula | null;

  public constructor(
    formulaLeft: Formula,
    connective: "NOT",
    formulaRight?: null,
  );
  public constructor(
    formulaLeft: Formula,
    connective: "AND" | "OR",
    formulaRight: Formula,
  );
  public constructor(
    formulaLeft: Formula,
    connective: LogicalConnective,
    formulaRight: Formula | null = null,
  ) {
    if (connective === "NOT" && formulaRight !== null) {
      throw new TypeError("NOT cannot have a right formula");
    }
    if (connective !== "NOT" && formulaRight === null) {
      throw new TypeError(`${connective} requires a right formula`);
    }

    this.formulaLeft = formulaLeft;
    this.connective = connective;
    this.formulaRight = formulaRight;
  }
}

export type Term = Constant | CompoundTerm | ListTerm;
export type Formula = Assertion | ConnectiveFormula;

/** Named workflow block passed between parser and checker. */
export class Workflow {
  public constructor(
    readonly name: string,
    readonly assertions: readonly Assertion[],
  ) {}
}
