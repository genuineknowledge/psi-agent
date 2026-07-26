# Generated from grammar/FusionFlow.g4 by ANTLR 4.13.2
# encoding: utf-8
from antlr4 import *
from io import StringIO
import sys
if sys.version_info[1] > 5:
	from typing import TextIO
else:
	from typing.io import TextIO

def serializedATN():
    return [
        4,1,60,218,2,0,7,0,2,1,7,1,2,2,7,2,2,3,7,3,2,4,7,4,2,5,7,5,2,6,7,
        6,2,7,7,7,2,8,7,8,2,9,7,9,2,10,7,10,2,11,7,11,2,12,7,12,2,13,7,13,
        2,14,7,14,2,15,7,15,2,16,7,16,2,17,7,17,2,18,7,18,2,19,7,19,2,20,
        7,20,2,21,7,21,2,22,7,22,2,23,7,23,2,24,7,24,2,25,7,25,1,0,1,0,1,
        0,5,0,56,8,0,10,0,12,0,59,9,0,1,0,4,0,62,8,0,11,0,12,0,63,1,0,1,
        0,1,1,1,1,1,1,1,1,5,1,72,8,1,10,1,12,1,75,9,1,1,1,1,1,1,2,1,2,1,
        3,1,3,1,3,1,4,1,4,1,4,1,4,1,4,1,5,1,5,1,5,5,5,92,8,5,10,5,12,5,95,
        9,5,1,6,1,6,1,6,1,6,1,6,3,6,102,8,6,1,7,1,7,1,7,1,7,1,7,1,7,1,7,
        1,7,3,7,112,8,7,1,7,1,7,1,7,1,7,1,7,1,7,5,7,120,8,7,10,7,12,7,123,
        9,7,1,8,1,8,1,8,1,8,1,9,1,9,1,10,1,10,1,10,1,10,1,10,1,10,1,10,1,
        10,1,10,1,10,1,10,3,10,142,8,10,1,10,1,10,1,10,1,10,1,10,1,10,1,
        10,1,10,1,10,5,10,153,8,10,10,10,12,10,156,9,10,1,11,1,11,1,11,3,
        11,161,8,11,1,11,1,11,1,12,1,12,1,12,1,12,1,12,1,12,1,12,1,12,1,
        12,1,13,1,13,1,13,5,13,177,8,13,10,13,12,13,180,9,13,1,14,1,14,3,
        14,184,8,14,1,14,1,14,1,15,1,15,3,15,190,8,15,1,16,1,16,1,17,1,17,
        1,18,1,18,3,18,198,8,18,1,19,1,19,1,19,1,19,3,19,204,8,19,1,20,1,
        20,1,21,1,21,1,22,1,22,1,23,1,23,1,24,1,24,1,25,1,25,1,25,0,2,14,
        20,26,0,2,4,6,8,10,12,14,16,18,20,22,24,26,28,30,32,34,36,38,40,
        42,44,46,48,50,0,9,1,0,33,38,1,0,39,40,1,0,41,43,1,0,1,6,1,0,7,11,
        1,0,12,17,1,0,18,23,2,0,45,45,47,48,1,0,30,31,216,0,57,1,0,0,0,2,
        67,1,0,0,0,4,78,1,0,0,0,6,80,1,0,0,0,8,83,1,0,0,0,10,88,1,0,0,0,
        12,101,1,0,0,0,14,111,1,0,0,0,16,124,1,0,0,0,18,128,1,0,0,0,20,141,
        1,0,0,0,22,157,1,0,0,0,24,164,1,0,0,0,26,173,1,0,0,0,28,181,1,0,
        0,0,30,189,1,0,0,0,32,191,1,0,0,0,34,193,1,0,0,0,36,197,1,0,0,0,
        38,203,1,0,0,0,40,205,1,0,0,0,42,207,1,0,0,0,44,209,1,0,0,0,46,211,
        1,0,0,0,48,213,1,0,0,0,50,215,1,0,0,0,52,53,3,8,4,0,53,54,5,51,0,
        0,54,56,1,0,0,0,55,52,1,0,0,0,56,59,1,0,0,0,57,55,1,0,0,0,57,58,
        1,0,0,0,58,61,1,0,0,0,59,57,1,0,0,0,60,62,3,2,1,0,61,60,1,0,0,0,
        62,63,1,0,0,0,63,61,1,0,0,0,63,64,1,0,0,0,64,65,1,0,0,0,65,66,5,
        0,0,1,66,1,1,0,0,0,67,68,5,24,0,0,68,69,3,4,2,0,69,73,5,54,0,0,70,
        72,3,6,3,0,71,70,1,0,0,0,72,75,1,0,0,0,73,71,1,0,0,0,73,74,1,0,0,
        0,74,76,1,0,0,0,75,73,1,0,0,0,76,77,5,55,0,0,77,3,1,0,0,0,78,79,
        3,32,16,0,79,5,1,0,0,0,80,81,3,12,6,0,81,82,5,51,0,0,82,7,1,0,0,
        0,83,84,5,26,0,0,84,85,3,48,24,0,85,86,5,49,0,0,86,87,3,10,5,0,87,
        9,1,0,0,0,88,93,3,34,17,0,89,90,5,50,0,0,90,92,3,34,17,0,91,89,1,
        0,0,0,92,95,1,0,0,0,93,91,1,0,0,0,93,94,1,0,0,0,94,11,1,0,0,0,95,
        93,1,0,0,0,96,97,3,20,10,0,97,98,5,32,0,0,98,99,3,20,10,0,99,102,
        1,0,0,0,100,102,3,22,11,0,101,96,1,0,0,0,101,100,1,0,0,0,102,13,
        1,0,0,0,103,104,6,7,-1,0,104,105,5,52,0,0,105,106,3,14,7,0,106,107,
        5,53,0,0,107,112,1,0,0,0,108,109,5,29,0,0,109,112,3,14,7,4,110,112,
        3,16,8,0,111,103,1,0,0,0,111,108,1,0,0,0,111,110,1,0,0,0,112,121,
        1,0,0,0,113,114,10,3,0,0,114,115,5,27,0,0,115,120,3,14,7,4,116,117,
        10,2,0,0,117,118,5,28,0,0,118,120,3,14,7,3,119,113,1,0,0,0,119,116,
        1,0,0,0,120,123,1,0,0,0,121,119,1,0,0,0,121,122,1,0,0,0,122,15,1,
        0,0,0,123,121,1,0,0,0,124,125,3,20,10,0,125,126,3,18,9,0,126,127,
        3,20,10,0,127,17,1,0,0,0,128,129,7,0,0,0,129,19,1,0,0,0,130,131,
        6,10,-1,0,131,132,5,52,0,0,132,133,3,20,10,0,133,134,5,53,0,0,134,
        142,1,0,0,0,135,142,3,24,12,0,136,142,3,22,11,0,137,142,3,28,14,
        0,138,139,7,1,0,0,139,142,3,20,10,5,140,142,3,30,15,0,141,130,1,
        0,0,0,141,135,1,0,0,0,141,136,1,0,0,0,141,137,1,0,0,0,141,138,1,
        0,0,0,141,140,1,0,0,0,142,154,1,0,0,0,143,144,10,4,0,0,144,145,5,
        44,0,0,145,153,3,20,10,4,146,147,10,3,0,0,147,148,7,2,0,0,148,153,
        3,20,10,4,149,150,10,2,0,0,150,151,7,1,0,0,151,153,3,20,10,3,152,
        143,1,0,0,0,152,146,1,0,0,0,152,149,1,0,0,0,153,156,1,0,0,0,154,
        152,1,0,0,0,154,155,1,0,0,0,155,21,1,0,0,0,156,154,1,0,0,0,157,158,
        3,36,18,0,158,160,5,52,0,0,159,161,3,26,13,0,160,159,1,0,0,0,160,
        161,1,0,0,0,161,162,1,0,0,0,162,163,5,53,0,0,163,23,1,0,0,0,164,
        165,5,25,0,0,165,166,5,52,0,0,166,167,3,14,7,0,167,168,5,50,0,0,
        168,169,3,20,10,0,169,170,5,50,0,0,170,171,3,20,10,0,171,172,5,53,
        0,0,172,25,1,0,0,0,173,178,3,20,10,0,174,175,5,50,0,0,175,177,3,
        20,10,0,176,174,1,0,0,0,177,180,1,0,0,0,178,176,1,0,0,0,178,179,
        1,0,0,0,179,27,1,0,0,0,180,178,1,0,0,0,181,183,5,56,0,0,182,184,
        3,26,13,0,183,182,1,0,0,0,183,184,1,0,0,0,184,185,1,0,0,0,185,186,
        5,57,0,0,186,29,1,0,0,0,187,190,3,48,24,0,188,190,3,50,25,0,189,
        187,1,0,0,0,189,188,1,0,0,0,190,31,1,0,0,0,191,192,5,47,0,0,192,
        33,1,0,0,0,193,194,5,46,0,0,194,35,1,0,0,0,195,198,5,47,0,0,196,
        198,3,38,19,0,197,195,1,0,0,0,197,196,1,0,0,0,198,37,1,0,0,0,199,
        204,3,40,20,0,200,204,3,42,21,0,201,204,3,44,22,0,202,204,3,46,23,
        0,203,199,1,0,0,0,203,200,1,0,0,0,203,201,1,0,0,0,203,202,1,0,0,
        0,204,39,1,0,0,0,205,206,7,3,0,0,206,41,1,0,0,0,207,208,7,4,0,0,
        208,43,1,0,0,0,209,210,7,5,0,0,210,45,1,0,0,0,211,212,7,6,0,0,212,
        47,1,0,0,0,213,214,7,7,0,0,214,49,1,0,0,0,215,216,7,8,0,0,216,51,
        1,0,0,0,17,57,63,73,93,101,111,119,121,141,152,154,160,178,183,189,
        197,203
    ]

class FusionFlowParser ( Parser ):

    grammarFileName = "FusionFlow.g4"

    atn = ATNDeserializer().deserialize(serializedATN())

    decisionsToDFA = [ DFA(ds, i) for i, ds in enumerate(atn.decisionToState) ]

    sharedContextCache = PredictionContextCache()

    literalNames = [ "<INVALID>", "'input_workflow'", "'input_workflow_multi'", 
                     "'output_workflow'", "'output_workflow_multi'", "'max_concurrency'", 
                     "'workflow_timeout'", "'step_name'", "'step_instruction'", 
                     "'step_executor'", "'step_timeout'", "'max_attempts'", 
                     "'consumes'", "'consumes_multi'", "'produces'", "'produces_multi'", 
                     "'foreach_item'", "'resource_requirement'", "'agent_config'", 
                     "'allowed_tool'", "'max_output_tokens'", "'temperature'", 
                     "'reasoning_effort'", "'max_turns'", "'workflow'", 
                     "'if'", "'const'", "<INVALID>", "<INVALID>", "'!'", 
                     "<INVALID>", "<INVALID>", "'=='", "'='", "'!='", "'<='", 
                     "'>='", "'<'", "'>'", "'+'", "'-'", "'*'", "'/'", "'%'", 
                     "'^'", "<INVALID>", "<INVALID>", "<INVALID>", "<INVALID>", 
                     "':'", "','", "';'", "'('", "')'", "'{'", "'}'", "'['", 
                     "']'" ]

    symbolicNames = [ "<INVALID>", "<INVALID>", "<INVALID>", "<INVALID>", 
                      "<INVALID>", "<INVALID>", "<INVALID>", "<INVALID>", 
                      "<INVALID>", "<INVALID>", "<INVALID>", "<INVALID>", 
                      "<INVALID>", "<INVALID>", "<INVALID>", "<INVALID>", 
                      "<INVALID>", "<INVALID>", "<INVALID>", "<INVALID>", 
                      "<INVALID>", "<INVALID>", "<INVALID>", "<INVALID>", 
                      "WORKFLOW", "IF", "CONST", "AND", "OR", "NOT", "TRUE", 
                      "FALSE", "ASSERT_EQ", "NUMERIC_EQ", "NOT_EQUALS", 
                      "LTE", "GTE", "LT", "GT", "PLUS", "MINUS", "STAR", 
                      "DIVIDE", "MODULO", "CARET", "NUMBER", "UPID", "LOWID", 
                      "QUOTEDCONSTANTID", "COLON", "COMMA", "SEMICOLON", 
                      "LPAREN", "RPAREN", "LBRACE", "RBRACE", "LBRACK", 
                      "RBRACK", "WS", "LINE_COMMENT", "BLOCK_COMMENT" ]

    RULE_workflowFile = 0
    RULE_workflowDecl = 1
    RULE_workflowName = 2
    RULE_workflowItem = 3
    RULE_constDecl = 4
    RULE_conceptNameList = 5
    RULE_assertion = 6
    RULE_formula = 7
    RULE_comparison = 8
    RULE_comparisonOp = 9
    RULE_term = 10
    RULE_operatorCall = 11
    RULE_ifExpression = 12
    RULE_termList = 13
    RULE_listLiteral = 14
    RULE_atomicTerm = 15
    RULE_identifier = 16
    RULE_conceptName = 17
    RULE_operatorName = 18
    RULE_workflowBuiltinOperator = 19
    RULE_workflowOwnerOperator = 20
    RULE_stepOwnerOperator = 21
    RULE_dataResourceOperator = 22
    RULE_agentOwnerOperator = 23
    RULE_constantName = 24
    RULE_booleanLiteral = 25

    ruleNames =  [ "workflowFile", "workflowDecl", "workflowName", "workflowItem", 
                   "constDecl", "conceptNameList", "assertion", "formula", 
                   "comparison", "comparisonOp", "term", "operatorCall", 
                   "ifExpression", "termList", "listLiteral", "atomicTerm", 
                   "identifier", "conceptName", "operatorName", "workflowBuiltinOperator", 
                   "workflowOwnerOperator", "stepOwnerOperator", "dataResourceOperator", 
                   "agentOwnerOperator", "constantName", "booleanLiteral" ]

    EOF = Token.EOF
    T__0=1
    T__1=2
    T__2=3
    T__3=4
    T__4=5
    T__5=6
    T__6=7
    T__7=8
    T__8=9
    T__9=10
    T__10=11
    T__11=12
    T__12=13
    T__13=14
    T__14=15
    T__15=16
    T__16=17
    T__17=18
    T__18=19
    T__19=20
    T__20=21
    T__21=22
    T__22=23
    WORKFLOW=24
    IF=25
    CONST=26
    AND=27
    OR=28
    NOT=29
    TRUE=30
    FALSE=31
    ASSERT_EQ=32
    NUMERIC_EQ=33
    NOT_EQUALS=34
    LTE=35
    GTE=36
    LT=37
    GT=38
    PLUS=39
    MINUS=40
    STAR=41
    DIVIDE=42
    MODULO=43
    CARET=44
    NUMBER=45
    UPID=46
    LOWID=47
    QUOTEDCONSTANTID=48
    COLON=49
    COMMA=50
    SEMICOLON=51
    LPAREN=52
    RPAREN=53
    LBRACE=54
    RBRACE=55
    LBRACK=56
    RBRACK=57
    WS=58
    LINE_COMMENT=59
    BLOCK_COMMENT=60

    def __init__(self, input:TokenStream, output:TextIO = sys.stdout):
        super().__init__(input, output)
        self.checkVersion("4.13.2")
        self._interp = ParserATNSimulator(self, self.atn, self.decisionsToDFA, self.sharedContextCache)
        self._predicates = None




    class WorkflowFileContext(ParserRuleContext):
        __slots__ = 'parser'

        def __init__(self, parser, parent:ParserRuleContext=None, invokingState:int=-1):
            super().__init__(parent, invokingState)
            self.parser = parser

        def EOF(self):
            return self.getToken(FusionFlowParser.EOF, 0)

        def constDecl(self, i:int=None):
            if i is None:
                return self.getTypedRuleContexts(FusionFlowParser.ConstDeclContext)
            else:
                return self.getTypedRuleContext(FusionFlowParser.ConstDeclContext,i)


        def SEMICOLON(self, i:int=None):
            if i is None:
                return self.getTokens(FusionFlowParser.SEMICOLON)
            else:
                return self.getToken(FusionFlowParser.SEMICOLON, i)

        def workflowDecl(self, i:int=None):
            if i is None:
                return self.getTypedRuleContexts(FusionFlowParser.WorkflowDeclContext)
            else:
                return self.getTypedRuleContext(FusionFlowParser.WorkflowDeclContext,i)


        def getRuleIndex(self):
            return FusionFlowParser.RULE_workflowFile




    def workflowFile(self):

        localctx = FusionFlowParser.WorkflowFileContext(self, self._ctx, self.state)
        self.enterRule(localctx, 0, self.RULE_workflowFile)
        self._la = 0 # Token type
        try:
            self.enterOuterAlt(localctx, 1)
            self.state = 57
            self._errHandler.sync(self)
            _la = self._input.LA(1)
            while _la==26:
                self.state = 52
                self.constDecl()
                self.state = 53
                self.match(FusionFlowParser.SEMICOLON)
                self.state = 59
                self._errHandler.sync(self)
                _la = self._input.LA(1)

            self.state = 61 
            self._errHandler.sync(self)
            _la = self._input.LA(1)
            while True:
                self.state = 60
                self.workflowDecl()
                self.state = 63 
                self._errHandler.sync(self)
                _la = self._input.LA(1)
                if not (_la==24):
                    break

            self.state = 65
            self.match(FusionFlowParser.EOF)
        except RecognitionException as re:
            localctx.exception = re
            self._errHandler.reportError(self, re)
            self._errHandler.recover(self, re)
        finally:
            self.exitRule()
        return localctx


    class WorkflowDeclContext(ParserRuleContext):
        __slots__ = 'parser'

        def __init__(self, parser, parent:ParserRuleContext=None, invokingState:int=-1):
            super().__init__(parent, invokingState)
            self.parser = parser

        def WORKFLOW(self):
            return self.getToken(FusionFlowParser.WORKFLOW, 0)

        def workflowName(self):
            return self.getTypedRuleContext(FusionFlowParser.WorkflowNameContext,0)


        def LBRACE(self):
            return self.getToken(FusionFlowParser.LBRACE, 0)

        def RBRACE(self):
            return self.getToken(FusionFlowParser.RBRACE, 0)

        def workflowItem(self, i:int=None):
            if i is None:
                return self.getTypedRuleContexts(FusionFlowParser.WorkflowItemContext)
            else:
                return self.getTypedRuleContext(FusionFlowParser.WorkflowItemContext,i)


        def getRuleIndex(self):
            return FusionFlowParser.RULE_workflowDecl




    def workflowDecl(self):

        localctx = FusionFlowParser.WorkflowDeclContext(self, self._ctx, self.state)
        self.enterRule(localctx, 2, self.RULE_workflowDecl)
        self._la = 0 # Token type
        try:
            self.enterOuterAlt(localctx, 1)
            self.state = 67
            self.match(FusionFlowParser.WORKFLOW)
            self.state = 68
            self.workflowName()
            self.state = 69
            self.match(FusionFlowParser.LBRACE)
            self.state = 73
            self._errHandler.sync(self)
            _la = self._input.LA(1)
            while (((_la) & ~0x3f) == 0 and ((1 << _la) & 77020243041452030) != 0):
                self.state = 70
                self.workflowItem()
                self.state = 75
                self._errHandler.sync(self)
                _la = self._input.LA(1)

            self.state = 76
            self.match(FusionFlowParser.RBRACE)
        except RecognitionException as re:
            localctx.exception = re
            self._errHandler.reportError(self, re)
            self._errHandler.recover(self, re)
        finally:
            self.exitRule()
        return localctx


    class WorkflowNameContext(ParserRuleContext):
        __slots__ = 'parser'

        def __init__(self, parser, parent:ParserRuleContext=None, invokingState:int=-1):
            super().__init__(parent, invokingState)
            self.parser = parser

        def identifier(self):
            return self.getTypedRuleContext(FusionFlowParser.IdentifierContext,0)


        def getRuleIndex(self):
            return FusionFlowParser.RULE_workflowName




    def workflowName(self):

        localctx = FusionFlowParser.WorkflowNameContext(self, self._ctx, self.state)
        self.enterRule(localctx, 4, self.RULE_workflowName)
        try:
            self.enterOuterAlt(localctx, 1)
            self.state = 78
            self.identifier()
        except RecognitionException as re:
            localctx.exception = re
            self._errHandler.reportError(self, re)
            self._errHandler.recover(self, re)
        finally:
            self.exitRule()
        return localctx


    class WorkflowItemContext(ParserRuleContext):
        __slots__ = 'parser'

        def __init__(self, parser, parent:ParserRuleContext=None, invokingState:int=-1):
            super().__init__(parent, invokingState)
            self.parser = parser

        def assertion(self):
            return self.getTypedRuleContext(FusionFlowParser.AssertionContext,0)


        def SEMICOLON(self):
            return self.getToken(FusionFlowParser.SEMICOLON, 0)

        def getRuleIndex(self):
            return FusionFlowParser.RULE_workflowItem




    def workflowItem(self):

        localctx = FusionFlowParser.WorkflowItemContext(self, self._ctx, self.state)
        self.enterRule(localctx, 6, self.RULE_workflowItem)
        try:
            self.enterOuterAlt(localctx, 1)
            self.state = 80
            self.assertion()
            self.state = 81
            self.match(FusionFlowParser.SEMICOLON)
        except RecognitionException as re:
            localctx.exception = re
            self._errHandler.reportError(self, re)
            self._errHandler.recover(self, re)
        finally:
            self.exitRule()
        return localctx


    class ConstDeclContext(ParserRuleContext):
        __slots__ = 'parser'

        def __init__(self, parser, parent:ParserRuleContext=None, invokingState:int=-1):
            super().__init__(parent, invokingState)
            self.parser = parser

        def CONST(self):
            return self.getToken(FusionFlowParser.CONST, 0)

        def constantName(self):
            return self.getTypedRuleContext(FusionFlowParser.ConstantNameContext,0)


        def COLON(self):
            return self.getToken(FusionFlowParser.COLON, 0)

        def conceptNameList(self):
            return self.getTypedRuleContext(FusionFlowParser.ConceptNameListContext,0)


        def getRuleIndex(self):
            return FusionFlowParser.RULE_constDecl




    def constDecl(self):

        localctx = FusionFlowParser.ConstDeclContext(self, self._ctx, self.state)
        self.enterRule(localctx, 8, self.RULE_constDecl)
        try:
            self.enterOuterAlt(localctx, 1)
            self.state = 83
            self.match(FusionFlowParser.CONST)
            self.state = 84
            self.constantName()
            self.state = 85
            self.match(FusionFlowParser.COLON)
            self.state = 86
            self.conceptNameList()
        except RecognitionException as re:
            localctx.exception = re
            self._errHandler.reportError(self, re)
            self._errHandler.recover(self, re)
        finally:
            self.exitRule()
        return localctx


    class ConceptNameListContext(ParserRuleContext):
        __slots__ = 'parser'

        def __init__(self, parser, parent:ParserRuleContext=None, invokingState:int=-1):
            super().__init__(parent, invokingState)
            self.parser = parser

        def conceptName(self, i:int=None):
            if i is None:
                return self.getTypedRuleContexts(FusionFlowParser.ConceptNameContext)
            else:
                return self.getTypedRuleContext(FusionFlowParser.ConceptNameContext,i)


        def COMMA(self, i:int=None):
            if i is None:
                return self.getTokens(FusionFlowParser.COMMA)
            else:
                return self.getToken(FusionFlowParser.COMMA, i)

        def getRuleIndex(self):
            return FusionFlowParser.RULE_conceptNameList




    def conceptNameList(self):

        localctx = FusionFlowParser.ConceptNameListContext(self, self._ctx, self.state)
        self.enterRule(localctx, 10, self.RULE_conceptNameList)
        self._la = 0 # Token type
        try:
            self.enterOuterAlt(localctx, 1)
            self.state = 88
            self.conceptName()
            self.state = 93
            self._errHandler.sync(self)
            _la = self._input.LA(1)
            while _la==50:
                self.state = 89
                self.match(FusionFlowParser.COMMA)
                self.state = 90
                self.conceptName()
                self.state = 95
                self._errHandler.sync(self)
                _la = self._input.LA(1)

        except RecognitionException as re:
            localctx.exception = re
            self._errHandler.reportError(self, re)
            self._errHandler.recover(self, re)
        finally:
            self.exitRule()
        return localctx


    class AssertionContext(ParserRuleContext):
        __slots__ = 'parser'

        def __init__(self, parser, parent:ParserRuleContext=None, invokingState:int=-1):
            super().__init__(parent, invokingState)
            self.parser = parser

        def term(self, i:int=None):
            if i is None:
                return self.getTypedRuleContexts(FusionFlowParser.TermContext)
            else:
                return self.getTypedRuleContext(FusionFlowParser.TermContext,i)


        def ASSERT_EQ(self):
            return self.getToken(FusionFlowParser.ASSERT_EQ, 0)

        def operatorCall(self):
            return self.getTypedRuleContext(FusionFlowParser.OperatorCallContext,0)


        def getRuleIndex(self):
            return FusionFlowParser.RULE_assertion




    def assertion(self):

        localctx = FusionFlowParser.AssertionContext(self, self._ctx, self.state)
        self.enterRule(localctx, 12, self.RULE_assertion)
        try:
            self.state = 101
            self._errHandler.sync(self)
            la_ = self._interp.adaptivePredict(self._input,4,self._ctx)
            if la_ == 1:
                self.enterOuterAlt(localctx, 1)
                self.state = 96
                self.term(0)
                self.state = 97
                self.match(FusionFlowParser.ASSERT_EQ)
                self.state = 98
                self.term(0)
                pass

            elif la_ == 2:
                self.enterOuterAlt(localctx, 2)
                self.state = 100
                self.operatorCall()
                pass


        except RecognitionException as re:
            localctx.exception = re
            self._errHandler.reportError(self, re)
            self._errHandler.recover(self, re)
        finally:
            self.exitRule()
        return localctx


    class FormulaContext(ParserRuleContext):
        __slots__ = 'parser'

        def __init__(self, parser, parent:ParserRuleContext=None, invokingState:int=-1):
            super().__init__(parent, invokingState)
            self.parser = parser
            self.left = None # FormulaContext
            self.right = None # FormulaContext

        def LPAREN(self):
            return self.getToken(FusionFlowParser.LPAREN, 0)

        def formula(self, i:int=None):
            if i is None:
                return self.getTypedRuleContexts(FusionFlowParser.FormulaContext)
            else:
                return self.getTypedRuleContext(FusionFlowParser.FormulaContext,i)


        def RPAREN(self):
            return self.getToken(FusionFlowParser.RPAREN, 0)

        def NOT(self):
            return self.getToken(FusionFlowParser.NOT, 0)

        def comparison(self):
            return self.getTypedRuleContext(FusionFlowParser.ComparisonContext,0)


        def AND(self):
            return self.getToken(FusionFlowParser.AND, 0)

        def OR(self):
            return self.getToken(FusionFlowParser.OR, 0)

        def getRuleIndex(self):
            return FusionFlowParser.RULE_formula



    def formula(self, _p:int=0):
        _parentctx = self._ctx
        _parentState = self.state
        localctx = FusionFlowParser.FormulaContext(self, self._ctx, _parentState)
        _prevctx = localctx
        _startState = 14
        self.enterRecursionRule(localctx, 14, self.RULE_formula, _p)
        try:
            self.enterOuterAlt(localctx, 1)
            self.state = 111
            self._errHandler.sync(self)
            la_ = self._interp.adaptivePredict(self._input,5,self._ctx)
            if la_ == 1:
                self.state = 104
                self.match(FusionFlowParser.LPAREN)
                self.state = 105
                self.formula(0)
                self.state = 106
                self.match(FusionFlowParser.RPAREN)
                pass

            elif la_ == 2:
                self.state = 108
                self.match(FusionFlowParser.NOT)
                self.state = 109
                self.formula(4)
                pass

            elif la_ == 3:
                self.state = 110
                self.comparison()
                pass


            self._ctx.stop = self._input.LT(-1)
            self.state = 121
            self._errHandler.sync(self)
            _alt = self._interp.adaptivePredict(self._input,7,self._ctx)
            while _alt!=2 and _alt!=ATN.INVALID_ALT_NUMBER:
                if _alt==1:
                    if self._parseListeners is not None:
                        self.triggerExitRuleEvent()
                    _prevctx = localctx
                    self.state = 119
                    self._errHandler.sync(self)
                    la_ = self._interp.adaptivePredict(self._input,6,self._ctx)
                    if la_ == 1:
                        localctx = FusionFlowParser.FormulaContext(self, _parentctx, _parentState)
                        localctx.left = _prevctx
                        self.pushNewRecursionContext(localctx, _startState, self.RULE_formula)
                        self.state = 113
                        if not self.precpred(self._ctx, 3):
                            from antlr4.error.Errors import FailedPredicateException
                            raise FailedPredicateException(self, "self.precpred(self._ctx, 3)")
                        self.state = 114
                        self.match(FusionFlowParser.AND)
                        self.state = 115
                        localctx.right = self.formula(4)
                        pass

                    elif la_ == 2:
                        localctx = FusionFlowParser.FormulaContext(self, _parentctx, _parentState)
                        localctx.left = _prevctx
                        self.pushNewRecursionContext(localctx, _startState, self.RULE_formula)
                        self.state = 116
                        if not self.precpred(self._ctx, 2):
                            from antlr4.error.Errors import FailedPredicateException
                            raise FailedPredicateException(self, "self.precpred(self._ctx, 2)")
                        self.state = 117
                        self.match(FusionFlowParser.OR)
                        self.state = 118
                        localctx.right = self.formula(3)
                        pass

             
                self.state = 123
                self._errHandler.sync(self)
                _alt = self._interp.adaptivePredict(self._input,7,self._ctx)

        except RecognitionException as re:
            localctx.exception = re
            self._errHandler.reportError(self, re)
            self._errHandler.recover(self, re)
        finally:
            self.unrollRecursionContexts(_parentctx)
        return localctx


    class ComparisonContext(ParserRuleContext):
        __slots__ = 'parser'

        def __init__(self, parser, parent:ParserRuleContext=None, invokingState:int=-1):
            super().__init__(parent, invokingState)
            self.parser = parser

        def term(self, i:int=None):
            if i is None:
                return self.getTypedRuleContexts(FusionFlowParser.TermContext)
            else:
                return self.getTypedRuleContext(FusionFlowParser.TermContext,i)


        def comparisonOp(self):
            return self.getTypedRuleContext(FusionFlowParser.ComparisonOpContext,0)


        def getRuleIndex(self):
            return FusionFlowParser.RULE_comparison




    def comparison(self):

        localctx = FusionFlowParser.ComparisonContext(self, self._ctx, self.state)
        self.enterRule(localctx, 16, self.RULE_comparison)
        try:
            self.enterOuterAlt(localctx, 1)
            self.state = 124
            self.term(0)
            self.state = 125
            self.comparisonOp()
            self.state = 126
            self.term(0)
        except RecognitionException as re:
            localctx.exception = re
            self._errHandler.reportError(self, re)
            self._errHandler.recover(self, re)
        finally:
            self.exitRule()
        return localctx


    class ComparisonOpContext(ParserRuleContext):
        __slots__ = 'parser'

        def __init__(self, parser, parent:ParserRuleContext=None, invokingState:int=-1):
            super().__init__(parent, invokingState)
            self.parser = parser

        def NUMERIC_EQ(self):
            return self.getToken(FusionFlowParser.NUMERIC_EQ, 0)

        def NOT_EQUALS(self):
            return self.getToken(FusionFlowParser.NOT_EQUALS, 0)

        def LT(self):
            return self.getToken(FusionFlowParser.LT, 0)

        def LTE(self):
            return self.getToken(FusionFlowParser.LTE, 0)

        def GT(self):
            return self.getToken(FusionFlowParser.GT, 0)

        def GTE(self):
            return self.getToken(FusionFlowParser.GTE, 0)

        def getRuleIndex(self):
            return FusionFlowParser.RULE_comparisonOp




    def comparisonOp(self):

        localctx = FusionFlowParser.ComparisonOpContext(self, self._ctx, self.state)
        self.enterRule(localctx, 18, self.RULE_comparisonOp)
        self._la = 0 # Token type
        try:
            self.enterOuterAlt(localctx, 1)
            self.state = 128
            _la = self._input.LA(1)
            if not((((_la) & ~0x3f) == 0 and ((1 << _la) & 541165879296) != 0)):
                self._errHandler.recoverInline(self)
            else:
                self._errHandler.reportMatch(self)
                self.consume()
        except RecognitionException as re:
            localctx.exception = re
            self._errHandler.reportError(self, re)
            self._errHandler.recover(self, re)
        finally:
            self.exitRule()
        return localctx


    class TermContext(ParserRuleContext):
        __slots__ = 'parser'

        def __init__(self, parser, parent:ParserRuleContext=None, invokingState:int=-1):
            super().__init__(parent, invokingState)
            self.parser = parser
            self.left = None # TermContext
            self.op = None # Token
            self.right = None # TermContext

        def LPAREN(self):
            return self.getToken(FusionFlowParser.LPAREN, 0)

        def term(self, i:int=None):
            if i is None:
                return self.getTypedRuleContexts(FusionFlowParser.TermContext)
            else:
                return self.getTypedRuleContext(FusionFlowParser.TermContext,i)


        def RPAREN(self):
            return self.getToken(FusionFlowParser.RPAREN, 0)

        def ifExpression(self):
            return self.getTypedRuleContext(FusionFlowParser.IfExpressionContext,0)


        def operatorCall(self):
            return self.getTypedRuleContext(FusionFlowParser.OperatorCallContext,0)


        def listLiteral(self):
            return self.getTypedRuleContext(FusionFlowParser.ListLiteralContext,0)


        def PLUS(self):
            return self.getToken(FusionFlowParser.PLUS, 0)

        def MINUS(self):
            return self.getToken(FusionFlowParser.MINUS, 0)

        def atomicTerm(self):
            return self.getTypedRuleContext(FusionFlowParser.AtomicTermContext,0)


        def CARET(self):
            return self.getToken(FusionFlowParser.CARET, 0)

        def STAR(self):
            return self.getToken(FusionFlowParser.STAR, 0)

        def DIVIDE(self):
            return self.getToken(FusionFlowParser.DIVIDE, 0)

        def MODULO(self):
            return self.getToken(FusionFlowParser.MODULO, 0)

        def getRuleIndex(self):
            return FusionFlowParser.RULE_term



    def term(self, _p:int=0):
        _parentctx = self._ctx
        _parentState = self.state
        localctx = FusionFlowParser.TermContext(self, self._ctx, _parentState)
        _prevctx = localctx
        _startState = 20
        self.enterRecursionRule(localctx, 20, self.RULE_term, _p)
        self._la = 0 # Token type
        try:
            self.enterOuterAlt(localctx, 1)
            self.state = 141
            self._errHandler.sync(self)
            la_ = self._interp.adaptivePredict(self._input,8,self._ctx)
            if la_ == 1:
                self.state = 131
                self.match(FusionFlowParser.LPAREN)
                self.state = 132
                self.term(0)
                self.state = 133
                self.match(FusionFlowParser.RPAREN)
                pass

            elif la_ == 2:
                self.state = 135
                self.ifExpression()
                pass

            elif la_ == 3:
                self.state = 136
                self.operatorCall()
                pass

            elif la_ == 4:
                self.state = 137
                self.listLiteral()
                pass

            elif la_ == 5:
                self.state = 138
                localctx.op = self._input.LT(1)
                _la = self._input.LA(1)
                if not(_la==39 or _la==40):
                    localctx.op = self._errHandler.recoverInline(self)
                else:
                    self._errHandler.reportMatch(self)
                    self.consume()
                self.state = 139
                self.term(5)
                pass

            elif la_ == 6:
                self.state = 140
                self.atomicTerm()
                pass


            self._ctx.stop = self._input.LT(-1)
            self.state = 154
            self._errHandler.sync(self)
            _alt = self._interp.adaptivePredict(self._input,10,self._ctx)
            while _alt!=2 and _alt!=ATN.INVALID_ALT_NUMBER:
                if _alt==1:
                    if self._parseListeners is not None:
                        self.triggerExitRuleEvent()
                    _prevctx = localctx
                    self.state = 152
                    self._errHandler.sync(self)
                    la_ = self._interp.adaptivePredict(self._input,9,self._ctx)
                    if la_ == 1:
                        localctx = FusionFlowParser.TermContext(self, _parentctx, _parentState)
                        localctx.left = _prevctx
                        self.pushNewRecursionContext(localctx, _startState, self.RULE_term)
                        self.state = 143
                        if not self.precpred(self._ctx, 4):
                            from antlr4.error.Errors import FailedPredicateException
                            raise FailedPredicateException(self, "self.precpred(self._ctx, 4)")
                        self.state = 144
                        localctx.op = self.match(FusionFlowParser.CARET)
                        self.state = 145
                        localctx.right = self.term(4)
                        pass

                    elif la_ == 2:
                        localctx = FusionFlowParser.TermContext(self, _parentctx, _parentState)
                        localctx.left = _prevctx
                        self.pushNewRecursionContext(localctx, _startState, self.RULE_term)
                        self.state = 146
                        if not self.precpred(self._ctx, 3):
                            from antlr4.error.Errors import FailedPredicateException
                            raise FailedPredicateException(self, "self.precpred(self._ctx, 3)")
                        self.state = 147
                        localctx.op = self._input.LT(1)
                        _la = self._input.LA(1)
                        if not((((_la) & ~0x3f) == 0 and ((1 << _la) & 15393162788864) != 0)):
                            localctx.op = self._errHandler.recoverInline(self)
                        else:
                            self._errHandler.reportMatch(self)
                            self.consume()
                        self.state = 148
                        localctx.right = self.term(4)
                        pass

                    elif la_ == 3:
                        localctx = FusionFlowParser.TermContext(self, _parentctx, _parentState)
                        localctx.left = _prevctx
                        self.pushNewRecursionContext(localctx, _startState, self.RULE_term)
                        self.state = 149
                        if not self.precpred(self._ctx, 2):
                            from antlr4.error.Errors import FailedPredicateException
                            raise FailedPredicateException(self, "self.precpred(self._ctx, 2)")
                        self.state = 150
                        localctx.op = self._input.LT(1)
                        _la = self._input.LA(1)
                        if not(_la==39 or _la==40):
                            localctx.op = self._errHandler.recoverInline(self)
                        else:
                            self._errHandler.reportMatch(self)
                            self.consume()
                        self.state = 151
                        localctx.right = self.term(3)
                        pass

             
                self.state = 156
                self._errHandler.sync(self)
                _alt = self._interp.adaptivePredict(self._input,10,self._ctx)

        except RecognitionException as re:
            localctx.exception = re
            self._errHandler.reportError(self, re)
            self._errHandler.recover(self, re)
        finally:
            self.unrollRecursionContexts(_parentctx)
        return localctx


    class OperatorCallContext(ParserRuleContext):
        __slots__ = 'parser'

        def __init__(self, parser, parent:ParserRuleContext=None, invokingState:int=-1):
            super().__init__(parent, invokingState)
            self.parser = parser

        def operatorName(self):
            return self.getTypedRuleContext(FusionFlowParser.OperatorNameContext,0)


        def LPAREN(self):
            return self.getToken(FusionFlowParser.LPAREN, 0)

        def RPAREN(self):
            return self.getToken(FusionFlowParser.RPAREN, 0)

        def termList(self):
            return self.getTypedRuleContext(FusionFlowParser.TermListContext,0)


        def getRuleIndex(self):
            return FusionFlowParser.RULE_operatorCall




    def operatorCall(self):

        localctx = FusionFlowParser.OperatorCallContext(self, self._ctx, self.state)
        self.enterRule(localctx, 22, self.RULE_operatorCall)
        self._la = 0 # Token type
        try:
            self.enterOuterAlt(localctx, 1)
            self.state = 157
            self.operatorName()
            self.state = 158
            self.match(FusionFlowParser.LPAREN)
            self.state = 160
            self._errHandler.sync(self)
            _la = self._input.LA(1)
            if (((_la) & ~0x3f) == 0 and ((1 << _la) & 77020243041452030) != 0):
                self.state = 159
                self.termList()


            self.state = 162
            self.match(FusionFlowParser.RPAREN)
        except RecognitionException as re:
            localctx.exception = re
            self._errHandler.reportError(self, re)
            self._errHandler.recover(self, re)
        finally:
            self.exitRule()
        return localctx


    class IfExpressionContext(ParserRuleContext):
        __slots__ = 'parser'

        def __init__(self, parser, parent:ParserRuleContext=None, invokingState:int=-1):
            super().__init__(parent, invokingState)
            self.parser = parser

        def IF(self):
            return self.getToken(FusionFlowParser.IF, 0)

        def LPAREN(self):
            return self.getToken(FusionFlowParser.LPAREN, 0)

        def formula(self):
            return self.getTypedRuleContext(FusionFlowParser.FormulaContext,0)


        def COMMA(self, i:int=None):
            if i is None:
                return self.getTokens(FusionFlowParser.COMMA)
            else:
                return self.getToken(FusionFlowParser.COMMA, i)

        def term(self, i:int=None):
            if i is None:
                return self.getTypedRuleContexts(FusionFlowParser.TermContext)
            else:
                return self.getTypedRuleContext(FusionFlowParser.TermContext,i)


        def RPAREN(self):
            return self.getToken(FusionFlowParser.RPAREN, 0)

        def getRuleIndex(self):
            return FusionFlowParser.RULE_ifExpression




    def ifExpression(self):

        localctx = FusionFlowParser.IfExpressionContext(self, self._ctx, self.state)
        self.enterRule(localctx, 24, self.RULE_ifExpression)
        try:
            self.enterOuterAlt(localctx, 1)
            self.state = 164
            self.match(FusionFlowParser.IF)
            self.state = 165
            self.match(FusionFlowParser.LPAREN)
            self.state = 166
            self.formula(0)
            self.state = 167
            self.match(FusionFlowParser.COMMA)
            self.state = 168
            self.term(0)
            self.state = 169
            self.match(FusionFlowParser.COMMA)
            self.state = 170
            self.term(0)
            self.state = 171
            self.match(FusionFlowParser.RPAREN)
        except RecognitionException as re:
            localctx.exception = re
            self._errHandler.reportError(self, re)
            self._errHandler.recover(self, re)
        finally:
            self.exitRule()
        return localctx


    class TermListContext(ParserRuleContext):
        __slots__ = 'parser'

        def __init__(self, parser, parent:ParserRuleContext=None, invokingState:int=-1):
            super().__init__(parent, invokingState)
            self.parser = parser

        def term(self, i:int=None):
            if i is None:
                return self.getTypedRuleContexts(FusionFlowParser.TermContext)
            else:
                return self.getTypedRuleContext(FusionFlowParser.TermContext,i)


        def COMMA(self, i:int=None):
            if i is None:
                return self.getTokens(FusionFlowParser.COMMA)
            else:
                return self.getToken(FusionFlowParser.COMMA, i)

        def getRuleIndex(self):
            return FusionFlowParser.RULE_termList




    def termList(self):

        localctx = FusionFlowParser.TermListContext(self, self._ctx, self.state)
        self.enterRule(localctx, 26, self.RULE_termList)
        self._la = 0 # Token type
        try:
            self.enterOuterAlt(localctx, 1)
            self.state = 173
            self.term(0)
            self.state = 178
            self._errHandler.sync(self)
            _la = self._input.LA(1)
            while _la==50:
                self.state = 174
                self.match(FusionFlowParser.COMMA)
                self.state = 175
                self.term(0)
                self.state = 180
                self._errHandler.sync(self)
                _la = self._input.LA(1)

        except RecognitionException as re:
            localctx.exception = re
            self._errHandler.reportError(self, re)
            self._errHandler.recover(self, re)
        finally:
            self.exitRule()
        return localctx


    class ListLiteralContext(ParserRuleContext):
        __slots__ = 'parser'

        def __init__(self, parser, parent:ParserRuleContext=None, invokingState:int=-1):
            super().__init__(parent, invokingState)
            self.parser = parser

        def LBRACK(self):
            return self.getToken(FusionFlowParser.LBRACK, 0)

        def RBRACK(self):
            return self.getToken(FusionFlowParser.RBRACK, 0)

        def termList(self):
            return self.getTypedRuleContext(FusionFlowParser.TermListContext,0)


        def getRuleIndex(self):
            return FusionFlowParser.RULE_listLiteral




    def listLiteral(self):

        localctx = FusionFlowParser.ListLiteralContext(self, self._ctx, self.state)
        self.enterRule(localctx, 28, self.RULE_listLiteral)
        self._la = 0 # Token type
        try:
            self.enterOuterAlt(localctx, 1)
            self.state = 181
            self.match(FusionFlowParser.LBRACK)
            self.state = 183
            self._errHandler.sync(self)
            _la = self._input.LA(1)
            if (((_la) & ~0x3f) == 0 and ((1 << _la) & 77020243041452030) != 0):
                self.state = 182
                self.termList()


            self.state = 185
            self.match(FusionFlowParser.RBRACK)
        except RecognitionException as re:
            localctx.exception = re
            self._errHandler.reportError(self, re)
            self._errHandler.recover(self, re)
        finally:
            self.exitRule()
        return localctx


    class AtomicTermContext(ParserRuleContext):
        __slots__ = 'parser'

        def __init__(self, parser, parent:ParserRuleContext=None, invokingState:int=-1):
            super().__init__(parent, invokingState)
            self.parser = parser

        def constantName(self):
            return self.getTypedRuleContext(FusionFlowParser.ConstantNameContext,0)


        def booleanLiteral(self):
            return self.getTypedRuleContext(FusionFlowParser.BooleanLiteralContext,0)


        def getRuleIndex(self):
            return FusionFlowParser.RULE_atomicTerm




    def atomicTerm(self):

        localctx = FusionFlowParser.AtomicTermContext(self, self._ctx, self.state)
        self.enterRule(localctx, 30, self.RULE_atomicTerm)
        try:
            self.state = 189
            self._errHandler.sync(self)
            token = self._input.LA(1)
            if token in [45, 47, 48]:
                self.enterOuterAlt(localctx, 1)
                self.state = 187
                self.constantName()
                pass
            elif token in [30, 31]:
                self.enterOuterAlt(localctx, 2)
                self.state = 188
                self.booleanLiteral()
                pass
            else:
                raise NoViableAltException(self)

        except RecognitionException as re:
            localctx.exception = re
            self._errHandler.reportError(self, re)
            self._errHandler.recover(self, re)
        finally:
            self.exitRule()
        return localctx


    class IdentifierContext(ParserRuleContext):
        __slots__ = 'parser'

        def __init__(self, parser, parent:ParserRuleContext=None, invokingState:int=-1):
            super().__init__(parent, invokingState)
            self.parser = parser

        def LOWID(self):
            return self.getToken(FusionFlowParser.LOWID, 0)

        def getRuleIndex(self):
            return FusionFlowParser.RULE_identifier




    def identifier(self):

        localctx = FusionFlowParser.IdentifierContext(self, self._ctx, self.state)
        self.enterRule(localctx, 32, self.RULE_identifier)
        try:
            self.enterOuterAlt(localctx, 1)
            self.state = 191
            self.match(FusionFlowParser.LOWID)
        except RecognitionException as re:
            localctx.exception = re
            self._errHandler.reportError(self, re)
            self._errHandler.recover(self, re)
        finally:
            self.exitRule()
        return localctx


    class ConceptNameContext(ParserRuleContext):
        __slots__ = 'parser'

        def __init__(self, parser, parent:ParserRuleContext=None, invokingState:int=-1):
            super().__init__(parent, invokingState)
            self.parser = parser

        def UPID(self):
            return self.getToken(FusionFlowParser.UPID, 0)

        def getRuleIndex(self):
            return FusionFlowParser.RULE_conceptName




    def conceptName(self):

        localctx = FusionFlowParser.ConceptNameContext(self, self._ctx, self.state)
        self.enterRule(localctx, 34, self.RULE_conceptName)
        try:
            self.enterOuterAlt(localctx, 1)
            self.state = 193
            self.match(FusionFlowParser.UPID)
        except RecognitionException as re:
            localctx.exception = re
            self._errHandler.reportError(self, re)
            self._errHandler.recover(self, re)
        finally:
            self.exitRule()
        return localctx


    class OperatorNameContext(ParserRuleContext):
        __slots__ = 'parser'

        def __init__(self, parser, parent:ParserRuleContext=None, invokingState:int=-1):
            super().__init__(parent, invokingState)
            self.parser = parser

        def LOWID(self):
            return self.getToken(FusionFlowParser.LOWID, 0)

        def workflowBuiltinOperator(self):
            return self.getTypedRuleContext(FusionFlowParser.WorkflowBuiltinOperatorContext,0)


        def getRuleIndex(self):
            return FusionFlowParser.RULE_operatorName




    def operatorName(self):

        localctx = FusionFlowParser.OperatorNameContext(self, self._ctx, self.state)
        self.enterRule(localctx, 36, self.RULE_operatorName)
        try:
            self.state = 197
            self._errHandler.sync(self)
            token = self._input.LA(1)
            if token in [47]:
                self.enterOuterAlt(localctx, 1)
                self.state = 195
                self.match(FusionFlowParser.LOWID)
                pass
            elif token in [1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12, 13, 14, 15, 16, 17, 18, 19, 20, 21, 22, 23]:
                self.enterOuterAlt(localctx, 2)
                self.state = 196
                self.workflowBuiltinOperator()
                pass
            else:
                raise NoViableAltException(self)

        except RecognitionException as re:
            localctx.exception = re
            self._errHandler.reportError(self, re)
            self._errHandler.recover(self, re)
        finally:
            self.exitRule()
        return localctx


    class WorkflowBuiltinOperatorContext(ParserRuleContext):
        __slots__ = 'parser'

        def __init__(self, parser, parent:ParserRuleContext=None, invokingState:int=-1):
            super().__init__(parent, invokingState)
            self.parser = parser

        def workflowOwnerOperator(self):
            return self.getTypedRuleContext(FusionFlowParser.WorkflowOwnerOperatorContext,0)


        def stepOwnerOperator(self):
            return self.getTypedRuleContext(FusionFlowParser.StepOwnerOperatorContext,0)


        def dataResourceOperator(self):
            return self.getTypedRuleContext(FusionFlowParser.DataResourceOperatorContext,0)


        def agentOwnerOperator(self):
            return self.getTypedRuleContext(FusionFlowParser.AgentOwnerOperatorContext,0)


        def getRuleIndex(self):
            return FusionFlowParser.RULE_workflowBuiltinOperator




    def workflowBuiltinOperator(self):

        localctx = FusionFlowParser.WorkflowBuiltinOperatorContext(self, self._ctx, self.state)
        self.enterRule(localctx, 38, self.RULE_workflowBuiltinOperator)
        try:
            self.state = 203
            self._errHandler.sync(self)
            token = self._input.LA(1)
            if token in [1, 2, 3, 4, 5, 6]:
                self.enterOuterAlt(localctx, 1)
                self.state = 199
                self.workflowOwnerOperator()
                pass
            elif token in [7, 8, 9, 10, 11]:
                self.enterOuterAlt(localctx, 2)
                self.state = 200
                self.stepOwnerOperator()
                pass
            elif token in [12, 13, 14, 15, 16, 17]:
                self.enterOuterAlt(localctx, 3)
                self.state = 201
                self.dataResourceOperator()
                pass
            elif token in [18, 19, 20, 21, 22, 23]:
                self.enterOuterAlt(localctx, 4)
                self.state = 202
                self.agentOwnerOperator()
                pass
            else:
                raise NoViableAltException(self)

        except RecognitionException as re:
            localctx.exception = re
            self._errHandler.reportError(self, re)
            self._errHandler.recover(self, re)
        finally:
            self.exitRule()
        return localctx


    class WorkflowOwnerOperatorContext(ParserRuleContext):
        __slots__ = 'parser'

        def __init__(self, parser, parent:ParserRuleContext=None, invokingState:int=-1):
            super().__init__(parent, invokingState)
            self.parser = parser


        def getRuleIndex(self):
            return FusionFlowParser.RULE_workflowOwnerOperator




    def workflowOwnerOperator(self):

        localctx = FusionFlowParser.WorkflowOwnerOperatorContext(self, self._ctx, self.state)
        self.enterRule(localctx, 40, self.RULE_workflowOwnerOperator)
        self._la = 0 # Token type
        try:
            self.enterOuterAlt(localctx, 1)
            self.state = 205
            _la = self._input.LA(1)
            if not((((_la) & ~0x3f) == 0 and ((1 << _la) & 126) != 0)):
                self._errHandler.recoverInline(self)
            else:
                self._errHandler.reportMatch(self)
                self.consume()
        except RecognitionException as re:
            localctx.exception = re
            self._errHandler.reportError(self, re)
            self._errHandler.recover(self, re)
        finally:
            self.exitRule()
        return localctx


    class StepOwnerOperatorContext(ParserRuleContext):
        __slots__ = 'parser'

        def __init__(self, parser, parent:ParserRuleContext=None, invokingState:int=-1):
            super().__init__(parent, invokingState)
            self.parser = parser


        def getRuleIndex(self):
            return FusionFlowParser.RULE_stepOwnerOperator




    def stepOwnerOperator(self):

        localctx = FusionFlowParser.StepOwnerOperatorContext(self, self._ctx, self.state)
        self.enterRule(localctx, 42, self.RULE_stepOwnerOperator)
        self._la = 0 # Token type
        try:
            self.enterOuterAlt(localctx, 1)
            self.state = 207
            _la = self._input.LA(1)
            if not((((_la) & ~0x3f) == 0 and ((1 << _la) & 3968) != 0)):
                self._errHandler.recoverInline(self)
            else:
                self._errHandler.reportMatch(self)
                self.consume()
        except RecognitionException as re:
            localctx.exception = re
            self._errHandler.reportError(self, re)
            self._errHandler.recover(self, re)
        finally:
            self.exitRule()
        return localctx


    class DataResourceOperatorContext(ParserRuleContext):
        __slots__ = 'parser'

        def __init__(self, parser, parent:ParserRuleContext=None, invokingState:int=-1):
            super().__init__(parent, invokingState)
            self.parser = parser


        def getRuleIndex(self):
            return FusionFlowParser.RULE_dataResourceOperator




    def dataResourceOperator(self):

        localctx = FusionFlowParser.DataResourceOperatorContext(self, self._ctx, self.state)
        self.enterRule(localctx, 44, self.RULE_dataResourceOperator)
        self._la = 0 # Token type
        try:
            self.enterOuterAlt(localctx, 1)
            self.state = 209
            _la = self._input.LA(1)
            if not((((_la) & ~0x3f) == 0 and ((1 << _la) & 258048) != 0)):
                self._errHandler.recoverInline(self)
            else:
                self._errHandler.reportMatch(self)
                self.consume()
        except RecognitionException as re:
            localctx.exception = re
            self._errHandler.reportError(self, re)
            self._errHandler.recover(self, re)
        finally:
            self.exitRule()
        return localctx


    class AgentOwnerOperatorContext(ParserRuleContext):
        __slots__ = 'parser'

        def __init__(self, parser, parent:ParserRuleContext=None, invokingState:int=-1):
            super().__init__(parent, invokingState)
            self.parser = parser


        def getRuleIndex(self):
            return FusionFlowParser.RULE_agentOwnerOperator




    def agentOwnerOperator(self):

        localctx = FusionFlowParser.AgentOwnerOperatorContext(self, self._ctx, self.state)
        self.enterRule(localctx, 46, self.RULE_agentOwnerOperator)
        self._la = 0 # Token type
        try:
            self.enterOuterAlt(localctx, 1)
            self.state = 211
            _la = self._input.LA(1)
            if not((((_la) & ~0x3f) == 0 and ((1 << _la) & 16515072) != 0)):
                self._errHandler.recoverInline(self)
            else:
                self._errHandler.reportMatch(self)
                self.consume()
        except RecognitionException as re:
            localctx.exception = re
            self._errHandler.reportError(self, re)
            self._errHandler.recover(self, re)
        finally:
            self.exitRule()
        return localctx


    class ConstantNameContext(ParserRuleContext):
        __slots__ = 'parser'

        def __init__(self, parser, parent:ParserRuleContext=None, invokingState:int=-1):
            super().__init__(parent, invokingState)
            self.parser = parser

        def NUMBER(self):
            return self.getToken(FusionFlowParser.NUMBER, 0)

        def QUOTEDCONSTANTID(self):
            return self.getToken(FusionFlowParser.QUOTEDCONSTANTID, 0)

        def LOWID(self):
            return self.getToken(FusionFlowParser.LOWID, 0)

        def getRuleIndex(self):
            return FusionFlowParser.RULE_constantName




    def constantName(self):

        localctx = FusionFlowParser.ConstantNameContext(self, self._ctx, self.state)
        self.enterRule(localctx, 48, self.RULE_constantName)
        self._la = 0 # Token type
        try:
            self.enterOuterAlt(localctx, 1)
            self.state = 213
            _la = self._input.LA(1)
            if not((((_la) & ~0x3f) == 0 and ((1 << _la) & 457396837154816) != 0)):
                self._errHandler.recoverInline(self)
            else:
                self._errHandler.reportMatch(self)
                self.consume()
        except RecognitionException as re:
            localctx.exception = re
            self._errHandler.reportError(self, re)
            self._errHandler.recover(self, re)
        finally:
            self.exitRule()
        return localctx


    class BooleanLiteralContext(ParserRuleContext):
        __slots__ = 'parser'

        def __init__(self, parser, parent:ParserRuleContext=None, invokingState:int=-1):
            super().__init__(parent, invokingState)
            self.parser = parser

        def TRUE(self):
            return self.getToken(FusionFlowParser.TRUE, 0)

        def FALSE(self):
            return self.getToken(FusionFlowParser.FALSE, 0)

        def getRuleIndex(self):
            return FusionFlowParser.RULE_booleanLiteral




    def booleanLiteral(self):

        localctx = FusionFlowParser.BooleanLiteralContext(self, self._ctx, self.state)
        self.enterRule(localctx, 50, self.RULE_booleanLiteral)
        self._la = 0 # Token type
        try:
            self.enterOuterAlt(localctx, 1)
            self.state = 215
            _la = self._input.LA(1)
            if not(_la==30 or _la==31):
                self._errHandler.recoverInline(self)
            else:
                self._errHandler.reportMatch(self)
                self.consume()
        except RecognitionException as re:
            localctx.exception = re
            self._errHandler.reportError(self, re)
            self._errHandler.recover(self, re)
        finally:
            self.exitRule()
        return localctx



    def sempred(self, localctx:RuleContext, ruleIndex:int, predIndex:int):
        if self._predicates == None:
            self._predicates = dict()
        self._predicates[7] = self.formula_sempred
        self._predicates[10] = self.term_sempred
        pred = self._predicates.get(ruleIndex, None)
        if pred is None:
            raise Exception("No predicate with index:" + str(ruleIndex))
        else:
            return pred(localctx, predIndex)

    def formula_sempred(self, localctx:FormulaContext, predIndex:int):
            if predIndex == 0:
                return self.precpred(self._ctx, 3)
         

            if predIndex == 1:
                return self.precpred(self._ctx, 2)
         

    def term_sempred(self, localctx:TermContext, predIndex:int):
            if predIndex == 2:
                return self.precpred(self._ctx, 4)
         

            if predIndex == 3:
                return self.precpred(self._ctx, 3)
         

            if predIndex == 4:
                return self.precpred(self._ctx, 2)
         




