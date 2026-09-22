from __future__ import annotations
# special multi node tree for easier traversal
from dataclasses import dataclass, field
# from tokenizer import *
from itchy.tokenizer import BNFRules, Definitions, GenericRules, Token, Tokenizer, compile_rules
from typing import Any, Iterable



from pathlib import Path

BNF_PATH = Path(__file__).parent / "bnf.txt"



@dataclass(frozen=True)
class ParsedNode():
    name: str
    children: tuple[ParsedNode | Token[Definitions], ...]
    dummy_node: bool=False

    def __repr__(self) -> str:
        output: list[str] = []

        for i in self.children:
            output.append(str(i))

        return f"[{', '.join(output)}]"


class GrammarNode():
    pass

@dataclass()
class Rule:
    name: str
    body: GrammarNode
    first: set[Terminal]=field(default_factory=lambda: set()) # the set of terminals which can fulfill the first index of this rule
    nullable: bool=False # determines whether we can skip over this rule while calculating first


def print_array(array: Iterable[Any], brackets: tuple[str, str], glue: str):
    """
    Prints an iterable with 'brackets' to wrap around and 'glue' as a separator

    For example, 
    
    array=[1,2,3,4,5], 
    
    brackets=("(",")"), 
    
    glue=" | " gives you an output of:

    (1 | 2 | 3 | 4 | 5)
    """
    output: list[str] = []
    for thing in array:
        output.append(str(thing))
    return f"{brackets[0]}{glue.join(output)}{brackets[1]}"


@dataclass(frozen=True)
class Alternative(GrammarNode):
    """A tuple of rules that one should be fulfilled"""
    options: tuple[GrammarNode, ...]

    def __repr__(self) -> str:
        return print_array(self.options, ('', ''), " | ")


@dataclass(frozen=True)
class Sequence(GrammarNode):
    """A tuple of rules that should all be fulfilled"""
    children: tuple[GrammarNode, ...]
    def __repr__(self) -> str:
        return print_array(self.children, ('', ''), ', ')


@dataclass(frozen=True)
class OptionalNode(GrammarNode):
    """A rule that can be fulfilled"""
    child: GrammarNode

    def __repr__(self) -> str:
        return "[" + str(self.child) + "]"

@dataclass(frozen=True)
class Repeat(GrammarNode):
    """A rule that can be fulfilled more than once"""
    child: GrammarNode

    def __repr__(self) -> str:
        return "{" + str(self.child) + "}"


@dataclass
class NonTerminal(GrammarNode):
    """A rule that requires the fulfilment of another rule"""
    name: str
    rule: Rule | None = None

    def __repr__(self) -> str:
        return self.name


@dataclass(frozen=True)
class Terminal(GrammarNode):
    """A rule that can be fulfilled or not (no traversal)"""
    child: Definitions | GenericRules
    literal: str | None=None  # if the rules explicitly state a string rather than something like <RuleName>, then we should fill this in. 

    def __repr__(self) -> str:
        return self.child.name


BNFToken = Token[BNFRules]


class BNFTreeBuilder:
    def __init__(self, tokens: list[BNFToken]) -> None:
        self.tokens = tokens
        self.saved_token = None
        self.pos = 0
        self.regex = compile_rules(Definitions)

    def str_to_rule_enum(self, text: str) -> GenericRules | Definitions:
        # first try matching rule directly
        try: 
            return GenericRules[text]
        except KeyError:
            pass

        try: 
            return Definitions[text]
        except KeyError:
            pass

        # no match, so we try matching via regex instead (most likely is a literal string)
        match = self.regex.match(text)
        
        if match is None:
            raise KeyError(text)

        group = match.lastgroup
        if group is None:
            # should not happen, but we have it here to shut the linter up.
            raise AssertionError("Empty group")

        return Definitions[group]

    def peek(self) -> BNFToken | None:
        """
        Returns the next valid token that isn't a comment or whitespace
        """
        if self.pos >= len(self.tokens):
            return None
        token = self.tokens[self.pos]
        while token.kind in {BNFRules.Comment, BNFRules.Whitespace, GenericRules.Whitespace}:
            self.pos += 1
            token = self.tokens[self.pos]
        return token
    
    def match(self, kind: BNFRules | GenericRules) -> BNFToken | None:
        """
        Returns the token if the token matches the kind supplied
        """
        token = self.peek()
        if token is not None and token.kind is kind:
            self.pos += 1
            return token
        return None
    
    def expect(self, kind: BNFRules | GenericRules) -> BNFToken:
        """
        Raises an error if match doesn't find anything
        """
        token = self.match(kind)
        if token is None:
            raise SyntaxError(f"Expected {kind}, got {self.peek()}")
        return token
    
    def parse_rules(self) -> list[Rule]:
        """
        Parses all rules
        """
        rules: list[Rule] = []
        while True:
            rules.append(self.parse_rule())
            while self.match(GenericRules.Newline):
                pass
            if self.match(GenericRules.EOF):
                break
        return rules
        
    def parse_rule(self) -> Rule:
        name_token = self.expect(BNFRules.NonTerminalRule)
        # THERE BETTER BE AN ASSIGN SYMBOL ':='
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

            # these indicate the sequence has terminated, so stop parsing. 
            if tok.kind in {BNFRules.Pipe, BNFRules.CloseSquareBrace, BNFRules.CloseCurlyBrace, BNFRules.CloseBrace, GenericRules.StatementSeparator, GenericRules.Newline}:
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
                # shitty hack to test if the text is a literal string - i can't be bothered to use regex
                text = token.literal
                is_string = text[0] in ['"', "'"] and text[-1] in ['"', "'"]
                try:
                    return Terminal(self.str_to_rule_enum(text[1:-1]), literal=text[1:-1] if is_string else None)
                except KeyError:
                    return Terminal(GenericRules[text[1:-1]], literal=text[1:-1] if is_string else None)
            case BNFRules.OpenSquareBrace:
                self.pos += 1
                child = self.parse_alternative()
                # there better be a closing square bracket or I SWEAR TO GOD
                self.expect(BNFRules.CloseSquareBrace)
                return OptionalNode(child)
            case BNFRules.OpenCurlyBrace:
                self.pos += 1
                child = self.parse_alternative()
                # THERE BETTER BE A CLOSING CURLY BRACKET
                self.expect(BNFRules.CloseCurlyBrace)
                return Repeat(child)
            case BNFRules.OpenBrace:
                self.pos += 1
                child = self.parse_alternative()
                # BETTER BE A CLOSING BRACKET!!!
                self.expect(BNFRules.CloseBrace)
                return child
            case _:
                pass

        raise SyntaxError(f"Unexpected token: {token}")


def link_grammar(rules: list[Rule]):
    """
    Applies link_node to all nodes (if applicable)
    """
    rule_map = {rule.name: rule for rule in rules}

    for rule in rules:
        link_node(rule.body, rule_map)


def link_node(node: GrammarNode, rule_map: dict[str, Rule]) -> None:
    """
    Points to the rule that a node is referencing (if it's a NonTerminal)

    For example, 

    <alphabet> := "a" | "b" | "c" | ...
    <word> := <alphabet> {<alphabet>} // <-- <alphabet> links to the original <alphabet> rule
    """
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


def node_nullable(node: GrammarNode) -> bool:
    """
    Returns whether `node` can match without consuming any tokens.
    """
    match node:
        case Terminal():
            return False

        case NonTerminal():
            if node.rule is None:
                raise AssertionError(
                    f"NonTerminal {node.name!r} has not been linked"
                )

            return node.rule.nullable

        case Alternative():
            return any(
                node_nullable(option)
                for option in node.options
            )

        case Sequence():
            return all(
                node_nullable(child)
                for child in node.children
            )

        case OptionalNode() | Repeat():
            return True

        case _:
            pass

    raise TypeError(
        f"Unknown grammar node: {type(node).__name__}"
    )


def calculate_nullable(rules: list[Rule]) -> None:
    """
    Calculates whether each rule can match without consuming a token.

    This uses a fixed-point calculation so recursive rules are safe.
    """
    changed = True

    while changed:
        changed = False

        for rule in rules:
            if rule.nullable:
                continue

            if node_nullable(rule.body):
                rule.nullable = True
                changed = True


def node_first(node: GrammarNode) -> set[Terminal]:
    """Returns the currently known FIRST set for a node."""
    match node:
        case Terminal():
            return {node}

        case NonTerminal():
            if not node.rule:
                raise AssertionError("Rule not linked yet")
            return set(node.rule.first)

        case Sequence():
            first: set[Terminal] = set()

            for child in node.children:
                first.update(
                    node_first(child)
                )

                if not node_nullable(child):
                    break

            return first

        case Alternative():
            first: set[Terminal] = set()

            for option in node.options:
                first.update(
                    node_first(option)
                )

            return first

        case OptionalNode() | Repeat():
            return node_first(node.child)

        case GrammarNode():
            raise TypeError("bad!! bare grammar node!!")
            


def generate_first(rules: list[Rule]) -> None:
    """
    Calculates FIRST sets for all grammar rules.
    """
    changed = True

    while changed:
        changed = False

        for rule in rules:
            before = len(rule.first)

            rule.first.update(
                node_first(rule.body)
            )

            if len(rule.first) != before:
                changed = True


def build_parse_tree():
    tokenizer = Tokenizer(BNFRules, {"Whitespace", "Comment"})
    with BNF_PATH.open("r") as f:
        token_stream = tokenizer.read(f.read())
        rules = BNFTreeBuilder(list(token_stream)).parse_rules()
        link_grammar(rules)
        calculate_nullable(rules)
        generate_first(rules)
    
    return rules


def get_root_node(rules: list[Rule]):
    for rule in rules:
        if rule.name == "program":
            return rule
    raise AssertionError("root node not found. check if bnf file has 'program' as a rule.")


if __name__ == "__main__":
    rules = build_parse_tree()
    for rule in rules:
        print(rule.body)

