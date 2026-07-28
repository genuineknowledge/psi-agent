/*
 * FusionFlow surface-syntax contract.
 *
 * The parser owns tokens, delimiters, file-level identity declarations,
 * assertions, formulas, terms, List literals, the three-argument shape of
 * if(...), and the names and owner categories of the preset operators.
 *
 * Concepts and operator signatures come from an external catalog; source files
 * cannot redefine them. The checker and catalog own identity/operator lookup,
 * concept compatibility, ordinary operator arity and types, value constraints,
 * workflow legality, and exact backend support. Lowering/runtime own execution
 * order, dependencies, list-valued data relations, branch evaluation, and
 * retries/timeouts.
 *
 * For a compact, readable BNF and consistency with KEDispatcher, preset
 * operators remain syntax sugar over the same flexible call shape instead of
 * getting separate arity-constrained parser rules. After syntax parsing, the
 * checker/catalog validates ordinary operator arity and types.
 *
 * Complete inline documentation is therefore part of this grammar contract:
 * every preset operator below lists its parameter types, return type, and
 * explicit arity for human and agent readers. if(...) is the one call-like
 * surface expression whose arity is fixed by this grammar.
 */
grammar FusionFlow;

/* A file has optional global identity declarations, then one or more workflows. */
workflowFile
    : (constDecl SEMICOLON)* workflowDecl+ EOF
    ;

workflowDecl
    : WORKFLOW workflowName LBRACE workflowItem* RBRACE
    ;

workflowName
    : identifier
    ;

/*
 * Workflow blocks contain assertions only; each assertion ends with a semicolon.
 * A standalone Bool-returning operator call is shorthand for `call == True`.
 * The parser resolves the catalog return type before applying this shorthand.
 */
workflowItem
    : assertion SEMICOLON
    ;

/* Attach concrete identities to concepts already defined by the catalog. */
constDecl
    : CONST constantName COLON conceptNameList
    ;

conceptNameList
    : conceptName (COMMA conceptName)*
    ;

/* Explicit assertions use '=='; '=' is reserved for equality comparisons in formulas. */
assertion
    : term ASSERT_EQ term
    | operatorCall
    ;

/*
 * Conditions bottom out at term comparisons; a bare term is not a formula.
 * Logical precedence, high to low: !, AND, then OR. Parentheses override it.
 * Implication and biconditional forms are intentionally outside this surface.
 */
formula
    : LPAREN formula RPAREN
    | NOT formula
    | left=formula AND right=formula
    | left=formula OR right=formula
    | comparison
    ;

comparison
    : term comparisonOp term
    ;

comparisonOp
    : NUMERIC_EQ
    | NOT_EQUALS
    | LT
    | LTE
    | GT
    | GTE
    ;

/*
 * Value terms include calls, lists, literals, arithmetic, and if expressions.
 * Arithmetic precedence, high to low: unary +/-; right-associative ^; * / %;
 * then +/-. Lists are ordinary terms, including the result side of the four
 * canonical list-valued dataflow operators. Legality remains checker-owned.
 */
term
    : LPAREN term RPAREN
    | ifExpression
    | operatorCall
    | listLiteral
    | op=(PLUS | MINUS) term
    | <assoc=right> left=term op=CARET right=term
    | left=term op=(STAR | DIVIDE | MODULO) right=term
    | left=term op=(PLUS | MINUS) right=term
    | atomicTerm
    ;

operatorCall
    : operatorName LPAREN termList? RPAREN
    ;

/*
 * Value-producing if(condition formula, then term, else term), always arity 3.
 * The grammar permits recursive terms, but the executable graph backend accepts
 * only a named Artifact equality and represents N-way priority with several
 * named intermediate Artifacts. Inline and nested if terms remain syntax-only
 * unless another backend implements them. if is not a block or Step.
 */
ifExpression
    : IF LPAREN formula COMMA term COMMA term RPAREN
    ;

termList
    : term (COMMA term)*
    ;

listLiteral
    : LBRACK termList? RBRACK
    ;

atomicTerm
    : constantName
    | booleanLiteral
    ;

/* Lowercase names identify workflows, constants, and catalog operators. */
identifier
    : LOWID
    ;

conceptName
    : UPID
    ;

operatorName
    : LOWID
    | workflowBuiltinOperator
    ;

/*
 * Complete catalog: 4 workflow + 5 step + 4 data/resource + 6 agent = 19.
 * Owner categories are disjoint;
 * cross-cutting labels such as dataflow, control, and configuration stay in
 * comments rather than duplicating names across parser rules.
 */
workflowBuiltinOperator
    : workflowOwnerOperator
    | stepOwnerOperator
    | dataResourceOperator
    | agentOwnerOperator
    ;

/*
 * Workflow owner (external I/O and workflow-level control/configuration):
 *   input_workflow(Workflow) -> List                  [arity 1]
 *   output_workflow(Workflow) -> List                 [arity 1]
 *   max_concurrency(Workflow) -> Integer             [arity 1]
 *   workflow_timeout(Workflow) -> Integer            [arity 1]
 */
workflowOwnerOperator
    : 'input_workflow'
    | 'output_workflow'
    | 'max_concurrency'
    | 'workflow_timeout'
    ;

/*
 * Step owner (identity, execution binding, timeout, and retry configuration):
 *   step_name(Step) -> StepName                      [arity 1]
 *   step_instruction(Step) -> Instruction            [arity 1]
 *   step_executor(Step) -> Executor                  [arity 1]
 *   step_timeout(Step) -> Integer                    [arity 1]
 *   max_attempts(Step) -> Integer                    [arity 1]
 */
stepOwnerOperator
    : 'step_name'
    | 'step_instruction'
    | 'step_executor'
    | 'step_timeout'
    | 'max_attempts'
    ;

/*
 * Data, loop, and resource owner:
 *   consumes(Step) -> List                            [arity 1]
 *   produces(Step) -> List                            [arity 1]
 *   foreach_item(Step, List) -> Artifact              [arity 2]
 *   resource_requirement(Step, Resource) -> Integer   [arity 2]
 */
dataResourceOperator
    : 'consumes'
    | 'produces'
    | 'foreach_item'
    | 'resource_requirement'
    ;

/*
 * Agent owner (model/runtime configuration and execution limits):
 *   agent_config(Agent, Model, Engine, ApiBase) -> Bool [arity 4]
 *   allowed_tool(Agent, Tool) -> Bool                   [arity 2]
 *   max_output_tokens(Agent) -> Integer                 [arity 1]
 *   temperature(Agent) -> ComplexNumber                 [arity 1]
 *   reasoning_effort(Agent) -> ReasoningEffort          [arity 1]
 *   max_turns(Agent) -> Integer                         [arity 1]
 */
agentOwnerOperator
    : 'agent_config'
    | 'allowed_tool'
    | 'max_output_tokens'
    | 'temperature'
    | 'reasoning_effort'
    | 'max_turns'
    ;

/* Constants are numbers, restricted quoted IDs, or lowercase identifiers. */
constantName
    : NUMBER
    | RELATIVE_PATH_ID
    | QUOTEDCONSTANTID
    | LOWID
    ;

booleanLiteral
    : TRUE
    | FALSE
    ;

/* Keywords and symbolic aliases are case-sensitive exactly as listed below. */
WORKFLOW : 'workflow';
IF : 'if';
CONST : 'const';
AND : 'AND' | 'and' | '&';
OR : 'OR' | 'or' | '|';
NOT : '!';
TRUE : 'True' | 'true' | 'TRUE';
FALSE : 'False' | 'false' | 'FALSE';
ASSERT_EQ : '==';
NUMERIC_EQ : '=';
NOT_EQUALS : '!=';
LTE : '<=';
GTE : '>=';
LT : '<';
GT : '>';
PLUS : '+';
MINUS : '-';
STAR : '*';
DIVIDE : '/';
MODULO : '%';
CARET : '^';

NUMBER
    : DIGITS '.' DIGITS
    | DIGITS
    ;

fragment DIGIT : [0-9];
fragment DIGITS : DIGIT+;
fragment UPPERID : [A-Z][A-Za-z0-9_]*;
fragment LOWERID : [a-z][A-Za-z0-9_]*;

UPID : UPPERID;
LOWID : LOWERID;
RELATIVE_PATH_ID : '"./' [A-Za-z0-9._/-]+ '"';
/* Restricted ID, not a general string: no whitespace or escape sequences. */
QUOTEDCONSTANTID : '"' [A-Za-z0-9.!#$%?@_{|}~`]* '"';
COLON : ':';
COMMA : ',';
SEMICOLON : ';';
LPAREN : '(';
RPAREN : ')';
LBRACE : '{';
RBRACE : '}';
LBRACK : '[';
RBRACK : ']';
WS : [ \t\r\n]+ -> skip;
LINE_COMMENT : '--' ~[\r\n]* -> skip;
BLOCK_COMMENT : '/*' .*? '*/' -> skip;
