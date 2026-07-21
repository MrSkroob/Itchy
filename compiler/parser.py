from __future__ import annotations
from dataclasses import dataclass
from tokenizer import Definitions, Tokenizer, Token
from tree import Rule, Terminal, NonTerminal, Alternative, OptionalNode, Repeat, Sequence, GrammarNode, build_parse_tree, get_root_node


DEBUG = False


@dataclass(frozen=True)
class ParsedNode():
    name: str
    children: tuple[ParsedNode | Token[Definitions], ...]

    def __repr__(self) -> str:
        output: list[str] = []

        for i in self.children:
            output.append(str(i))

        return f"[{', '.join(output)}]"


@dataclass
class ParseResult():
    tree: ParsedNode | Token[Definitions]
    pos: int

    def __repr__(self) -> str:
        return str(self.tree)


@dataclass
class FailState():
    node: GrammarNode
    tokens: list[Token[Definitions]]
    pos: int


class ParseError(Exception):
    def __init__(self, tokens: list[Token[Definitions]], pos: int, node: GrammarNode) -> None:
        self.tokens = tokens
        self.pos = pos
        self.node = node
        super().__init__()


class InvalidTreeError(Exception):
    def __init__(self, message: str) -> None:
        super().__init__(message)


def debug_print(message: str):
    if not DEBUG:
        return
    print(message)


def print_token_safe(tokens: list[Token[Definitions]], pos: int):
    return tokens[min(pos, len(tokens) - 1)].literal


class ASTNode():
    node_type: str


class Parser:
    def __init__(self) -> None:
        self.rules = build_parse_tree()
        self.tokenizer = Tokenizer(Definitions, {"Comment", "Whitespace", "Newline"})
        self.furthest_error: ParseError | None = None

    @property
    def fail_state(self):
        if self.furthest_error is None:
            return None
        return FailState(
            self.furthest_error.node,
            self.furthest_error.tokens,
            self.furthest_error.pos - 1
        )

    def make_error(self, tokens: list[Token[Definitions]], pos: int, node: GrammarNode):
        error = ParseError(tokens, pos, node)
        
        if self.furthest_error is None or pos > self.furthest_error.pos:
            self.furthest_error = error
        
        return error

    def parse_rule(self, rule: Rule, tokens: list[Token[Definitions]], pos: int) -> ParseResult:
        result = self.parse_node(rule.body, tokens, pos)

        return ParseResult(ParsedNode(rule.name, (result.tree, )), result.pos)

    def parse_node(self, node: GrammarNode, tokens: list[Token[Definitions]], pos: int) -> ParseResult:
        match node:
            case Terminal(value):
                if pos < len(tokens) and value.name == tokens[pos].kind.name:
                    debug_print(f"{print_token_safe(tokens, pos)}. Matched {value.name}")
                    return ParseResult(tokens[pos], pos + 1)
                debug_print(f"{print_token_safe(tokens, pos)}. Terminal rule not matched {value.name}")
                raise self.make_error(tokens, pos, node)
            
            case NonTerminal(_, rule):
                if rule is None:
                    raise InvalidTreeError("Invalid tree - no linking rule")

                debug_print(f"{print_token_safe(tokens, pos)}. Trying {node}")
                return self.parse_rule(rule, tokens, pos)
            
            case Sequence(children):
                parsed_children: list[ParsedNode | Token[Definitions]] = []
                result = None
                for child in children:
                    try:
                        result = self.parse_node(child, tokens, pos)
                            
                        parsed_children.append(result.tree)
                        pos = result.pos
                    except ParseError:
                        debug_print(f"{print_token_safe(tokens, pos)}. Sequence broken {node}.")
                        # propagate the error upwards
                        raise self.make_error(tokens, pos, node)
                    
                
                if result is None:
                    raise AssertionError("Invalid tree - empty sequence")
                
                debug_print(f"{print_token_safe(tokens, pos)}. Matched {node}")
                return ParseResult(
                    ParsedNode(Sequence.__name__, tuple(parsed_children)), pos
                )

            case Alternative(options):
                for option in options:
                    try:
                        result = self.parse_node(option, tokens, pos)
                        debug_print(f"{print_token_safe(tokens, pos)}. Matched {node}")
                        return ParseResult(
                            ParsedNode(Alternative.__name__, (result.tree,)),
                            result.pos
                        )
                    except ParseError:
                        self.make_error(tokens, pos, node)
                debug_print(f"Nothing matched {node}. {print_token_safe(tokens, pos)}")

                raise self.make_error(tokens, pos, node)
        
            case OptionalNode(child):
                try:
                    result = self.parse_node(child, tokens, pos)
                    debug_print(f"{print_token_safe(tokens, pos)}. Matched {node}")
                    return ParseResult(
                        ParsedNode(
                            OptionalNode.__name__, (result.tree,)
                        ),
                        result.pos
                    )
                except ParseError:
                    self.make_error(tokens, pos, node)
                    debug_print(f"{print_token_safe(tokens, pos)}. Skipping {node}")
                    return ParseResult(
                        ParsedNode(
                            OptionalNode.__name__, ()
                        ),
                        pos
                    )
                
            case Repeat(child):
                parsed_children: list[ParsedNode | Token[Definitions]] = []  # type: ignore[no-redef]

                while True:
                    try:
                        result = self.parse_node(child, tokens, pos)
                    except ParseError:
                        self.make_error(tokens, pos, node)
                        debug_print(f"{print_token_safe(tokens, pos)}. Skipping {node}")
                        break

                    if result.pos == pos:
                        break

                    parsed_children.append(result.tree)

                    pos = result.pos

                return ParseResult(
                    ParsedNode(Repeat.__name__, tuple(parsed_children)),
                    pos
                )
            
            case GrammarNode():
                raise TypeError("Reached bare GrammarNode")


    def build_ast(self) -> None:
        raise NotImplementedError()


    def read(self, text: str) -> ParseResult:
        root = get_root_node(self.rules)
        tokens = list(self.tokenizer.read(text))
        result = self.parse_rule(root, tokens, 0)

        return result
