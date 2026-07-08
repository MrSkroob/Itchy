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


def debug_print(message: str):
    if not DEBUG:
        return
    print(message)


def print_token_safe(tokens: list[Token[Definitions]], pos: int):
    return tokens[min(pos, len(tokens) - 1)].kind.name


class ASTNode():
    node_type: str


class Parser:
    def __init__(self) -> None:
        self.rules = build_parse_tree()
        self.tokenizer = Tokenizer(Definitions, {"Comment", "Whitespace", "Newline"})

    def parse_rule(self, rule: Rule, tokens: list[Token[Definitions]], pos: int) -> ParseResult:
        result = self.parse_node(rule.body, tokens, pos)

        return ParseResult(ParsedNode(rule.name, (result.tree, )), result.pos)

    def parse_node(self, node: GrammarNode, tokens: list[Token[Definitions]], pos: int) -> ParseResult:
        match node:
            case Terminal(value):
                if pos < len(tokens) and value.name == tokens[pos].kind.name:
                    debug_print(f"Matched: {print_token_safe(tokens, pos)} == {value.name}")
                    return ParseResult(tokens[pos], pos + 1)
                debug_print(f"Terminal rule not matched: {print_token_safe(tokens, pos)} != {value.name}")
                raise SyntaxError
            
            case NonTerminal(_, rule):
                if rule is None:
                    raise AssertionError("Invalid tree - no linking rule")

                debug_print(f"Trying: {print_token_safe(tokens, pos)} in {node}")
                return self.parse_rule(rule, tokens, pos)
            
            case Sequence(children):
                parsed_children: list[ParsedNode | Token[Definitions]] = []
                result = None
                for child in children:
                    try:
                        result = self.parse_node(child, tokens, pos)
                            
                        parsed_children.append(result.tree)
                        pos = result.pos
                    except SyntaxError:
                        debug_print(f"Sequence broken {node}. {print_token_safe(tokens, pos)}")
                        raise SyntaxError
                    
                
                if result is None:
                    raise AssertionError("Invalid tree - empty sequence")
                
                debug_print(f"Matched: {print_token_safe(tokens, pos)} in {node}")
                return ParseResult(
                    ParsedNode(Sequence.__name__, tuple(parsed_children)), pos
                )

            case Alternative(options):
                for option in options:
                    try:
                        result = self.parse_node(option, tokens, pos)
                        debug_print(f"Matched: {print_token_safe(tokens, pos)} in {node}")
                        return ParseResult(
                            ParsedNode(Alternative.__name__, (result.tree,)),
                            result.pos
                        )
                    except SyntaxError:
                        pass
                debug_print(f"Nothing matched {node}. {print_token_safe(tokens, pos)}")
                raise SyntaxError
        
            case OptionalNode(child):
                try:
                    result = self.parse_node(child, tokens, pos)
                    debug_print(f"Matched: {print_token_safe(tokens, pos)} in {node}")
                    return ParseResult(
                        ParsedNode(
                            OptionalNode.__name__, (result.tree,)
                        ),
                        result.pos
                    )
                except SyntaxError:
                    debug_print(f"Skipping {print_token_safe(tokens, pos)}")
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
                    except SyntaxError:
                        debug_print(f"Skipping {print_token_safe(tokens, pos)}")
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
