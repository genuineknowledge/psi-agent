/*
 * This bootstrap grammar defines syntax only. Whether an operator exists, is
 * legal in a workflow, or has backend support belongs to the static checker.
 *
 * `==` is assertion equality. Numeric equality uses `=` and remains outside
 * this bootstrap until numeric and formula syntax is added.
 */
grammar FusionFlow;

workflowFile
    : workflowDeclaration EOF
    ;

workflowDeclaration
    : WORKFLOW IDENTIFIER LBRACE assertion* RBRACE
    ;

assertion
    : operatorCall ASSERTION_EQUALS value SEMICOLON
    ;

operatorCall
    : IDENTIFIER LPAREN argumentList? RPAREN
    ;

argumentList
    : value (COMMA value)*
    ;

value
    : IDENTIFIER
    | booleanLiteral
    | listLiteral
    ;

booleanLiteral
    : TRUE
    | FALSE
    ;

listLiteral
    : LBRACKET (value (COMMA value)*)? RBRACKET
    ;

WORKFLOW: 'workflow';
TRUE: 'True';
FALSE: 'False';
ASSERTION_EQUALS: '==';
LBRACE: '{';
RBRACE: '}';
LPAREN: '(';
RPAREN: ')';
LBRACKET: '[';
RBRACKET: ']';
COMMA: ',';
SEMICOLON: ';';
IDENTIFIER: [a-zA-Z_] [a-zA-Z0-9_]*;
WS: [ \t\r\n]+ -> skip;
