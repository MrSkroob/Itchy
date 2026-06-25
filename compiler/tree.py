# special multi node tree for easier traversal
from dataclasses import dataclass
from tokenizer import *


@dataclass(frozen=True)
class Rule:
    name: str
    body: GrammarNode


class GrammarNode:
    pass


@dataclass(frozen=True)
class Alternative(GrammarNode):
    options: tuple[GrammarNode, ...]


@dataclass(frozen=True)
class Sequence(GrammarNode):
    children: tuple[GrammarNode, ...]


@dataclass(frozen=True)
class OptionalNode(GrammarNode):
    child: GrammarNode


@dataclass(frozen=True)
class Repeat(GrammarNode):
    child: GrammarNode


@dataclass(frozen=True)
class TempNonTerminal(GrammarNode):
    rule: str


@dataclass(frozen=True)
class NonTerminal(GrammarNode):
    rule: Rule


@dataclass(frozen=True)
class Terminal(GrammarNode):
    child: str


"""
class EBNFParser:
    def __init__(self, tokens: list[Token]) -> None:
        self.tokens = tokens
        self.pos = 0

    def peek(self) -> Token | None:
        if self.pos >= len(self.tokens):
            return None
        return self.tokens[self.pos]

    def match(self, kind: Tok) -> Token | None:
        tok = self.peek()
        if tok is not None and tok.kind is kind:
            self.pos += 1
            return tok
        return None

    def expect(self, kind: Tok) -> Token:
        tok = self.match(kind)
        if tok is None:
            raise SyntaxError(f"Expected {kind}, got {self.peek()}")
        return tok

    def parse_rule(self) -> Rule:
        name_tok = self.expect(Tok.NONTERMINAL)
        self.expect(Tok.DEFINE)
        body = self.parse_alternative()

        name = name_tok.literal[1:-1]
        return Rule(name, body)

    def parse_alternative(self) -> GrammarNode:
        options = [self.parse_sequence()]

        while self.match(Tok.PIPE):
            options.append(self.parse_sequence())

        if len(options) == 1:
            return options[0]

        return Alternative(options)

    def parse_sequence(self) -> GrammarNode:
        children: list[GrammarNode] = []

        while True:
            tok = self.peek()

            if tok is None:
                break

            if tok.kind in {Tok.PIPE, Tok.RBRACK, Tok.RBRACE, Tok.RPAREN}:
                break

            children.append(self.parse_item())

        if len(children) == 1:
            return children[0]

        return Sequence(children)

    def parse_item(self) -> GrammarNode:
        tok = self.peek()

        if tok is None:
            raise SyntaxError("Unexpected end of input")

        if tok.kind is Tok.NONTERMINAL:
            self.pos += 1
            return NonTerminal(tok.literal[1:-1])

        if tok.kind is Tok.TERMINAL:
            self.pos += 1
            return Terminal(tok.literal[1:-1])

        if tok.kind is Tok.LBRACK:
            self.pos += 1
            child = self.parse_alternative()
            self.expect(Tok.RBRACK)
            return OptionalNode(child)

        if tok.kind is Tok.LBRACE:
            self.pos += 1
            child = self.parse_alternative()
            self.expect(Tok.RBRACE)
            return Repeat(child)

        if tok.kind is Tok.LPAREN:
            self.pos += 1
            child = self.parse_alternative()
            self.expect(Tok.RPAREN)
            return child

        raise SyntaxError(f"Unexpected token: {tok}")
"""


BNFToken = Token[BNFRules]


class BNFTreeBuilder:
    def __init__(self, tokens: list[BNFToken]) -> None:
        self.tokens = tokens
        self.saved_token = None
        self.pos = 0

    def return_token(self, token: BNFToken | None) -> None:
        if token is None:
            return
        if self.saved_token is not None:
            raise AssertionError("Should not return more than one token at once.")
        self.saved_token = token

    def peek(self) -> BNFToken | None:
        if self.pos >= len(self.tokens):
            return None
        token = self.tokens[self.pos]
        while token.kind in {BNFRules.Comment, BNFRules.Whitespace, GenericRules.Whitespace}:
            self.pos += 1
            token = self.tokens[self.pos]
        return token
    
    def match(self, kind: BNFRules | GenericRules) -> BNFToken | None:
        token = self.peek()
        if token is not None and token.kind is kind:
            self.pos += 1
            return token
        return None
    
    def expect(self, kind: BNFRules | GenericRules) -> BNFToken:
        token = self.match(kind)
        if token is None:
            raise SyntaxError(f"Expected {kind}, got {self.peek()}")
        return token
    
    def parse_rules(self) -> list[Rule]:
        rules: list[Rule] = []
        while True:
            rules.append(self.parse_rule())
            while self.match(GenericRules.StatementSeperator):
                pass
            if self.match(GenericRules.EOF):
                break
        return rules
        
    def parse_rule(self) -> Rule:
        name_token = self.expect(BNFRules.NonTerminalRule)
        self.expect(BNFRules.Assign)
        body = self.parse_alternative()

        name = name_token.literal[1:-1]
        return Rule(name, body)
    
    def parse_alternative(self) -> GrammarNode:
        options = [self.parse_sequence()]

        while self.match(BNFRules.Pipe):
            options.append(self.parse_sequence())

        if len(options) == 1:
            return options[0]

        return Alternative(tuple(i for i in options))

    def parse_sequence(self) -> GrammarNode:
        children: list[GrammarNode] = []

        while True:
            tok = self.peek()

            if tok is None:
                break

            if tok.kind in {BNFRules.Pipe, BNFRules.CloseSquareBrace, BNFRules.CloseCurlyBrace, BNFRules.CloseBrace, GenericRules.StatementSeperator}:
                break

            children.append(self.parse_item())

        if len(children) == 1:
            return children[0]

        return Sequence(tuple(i for i in children))

    def parse_item(self) -> GrammarNode:
        token = self.peek()

        if token is None:
            raise SyntaxError("Unexpected end of input")
        
        match token.kind:
            case BNFRules.NonTerminalRule:
                self.pos += 1
                return TempNonTerminal(token.literal[1:-1])
            case BNFRules.TerminalRule:
                self.pos += 1
                return Terminal(token.literal[1:-1])
            case BNFRules.OpenSquareBrace:
                self.pos += 1
                child = self.parse_alternative()
                self.expect(BNFRules.CloseSquareBrace)
                return OptionalNode(child)
            case BNFRules.OpenCurlyBrace:
                self.pos += 1
                child = self.parse_alternative()
                self.expect(BNFRules.CloseCurlyBrace)
                return Repeat(child)
            case BNFRules.OpenBrace:
                self.pos += 1
                child = self.parse_alternative()
                self.expect(BNFRules.CloseBrace)
                return child
            case _:
                pass

        raise SyntaxError(f"Unexpected token: {token}")


def link_grammar(rules: list[Rule]):
    pass


def build_parse_tree():
    tokenizer = Tokenizer(BNFRules)
    with open("bnf.txt") as f:
        token_stream = tokenizer.read(f.read())
        rules = BNFTreeBuilder(list(token_stream)).parse_rules()
        for rule in rules:
            print(rule)


if __name__ == "__main__":
    build_parse_tree()

