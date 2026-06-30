# special multi node tree for easier traversal
from dataclasses import dataclass
from tokenizer import *


class GrammarNode:
    pass


@dataclass(frozen=True)
class Rule:
    name: str
    body: GrammarNode


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


@dataclass
class NonTerminal(GrammarNode):
    name: str
    rule: Rule | None = None


@dataclass(frozen=True)
class Terminal(GrammarNode):
    child: str


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
                return NonTerminal(token.literal[1:-1])
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
    rule_map = {rule.name: rule for rule in rules}

    for rule in rules:
        link_node(rule.body, rule_map)


def link_node(node: GrammarNode, rule_map: dict[str, Rule]) -> None:
    match node:
        case NonTerminal():
            node.rule = rule_map[node.name]

        case Sequence():
            for child in node.children:
                link_node(child, rule_map)

        case Alternative():
            for option in node.options:
                link_node(option, rule_map)

        case OptionalNode():
            link_node(node.child, rule_map)

        case Repeat():
            link_node(node.child, rule_map)

        case Terminal():
            pass

        case GrammarNode():
            pass


def build_parse_tree():
    tokenizer = Tokenizer(BNFRules)
    with open("bnf.txt") as f:
        token_stream = tokenizer.read(f.read())
        rules = BNFTreeBuilder(list(token_stream)).parse_rules()
        link_grammar(rules)
    
    return rules


def walk_tree():
    pass


if __name__ == "__main__":
    rules = build_parse_tree()
    for rule in rules:
        if rule.name == "<chunk>":
            print(rule)

