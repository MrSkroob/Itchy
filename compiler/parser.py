from __future__ import annotations
from tokenizer import *
from tree import *


@dataclass(frozen=True)
class ParsedNode():
    name: str
    children: tuple[ParsedNode | Token[Definitions], ...]


@dataclass
class ParseResult():
    tree: ParsedNode | Token[Definitions]
    pos: int


def print_token_safe(tokens: list[Token[Definitions]], pos: int):
    return tokens[min(pos, len(tokens) - 1)].kind.name


class Parser:
    def __init__(self) -> None:
        self.rules = build_parse_tree()
        # {"Whitespace", "Comment"}
        self.tokenizer = Tokenizer(Definitions, {"Comment", "Whitespace", "Newline"})
        self.visited: dict[str, int] = {}

    def parse_node(self, node: GrammarNode, tokens: list[Token[Definitions]], pos: int) -> ParseResult:
        if type(node) is NonTerminal:
            if node.name not in self.visited:
                self.visited[node.name] = 0
            self.visited[node.name] += 1
            if self.visited[node.name] > 2:
                raise SyntaxError("Recursion depth reached")

        match node:
            case Terminal(value):
                if pos < len(tokens) and node.child.name == tokens[pos].kind.name:
                    self.visited.clear()
                    print(f"Match OKAY: {print_token_safe(tokens, pos)}, {node}")
                    return ParseResult(tokens[pos], pos + 1)
                raise SyntaxError(f"Terminal rule not matched: {print_token_safe(tokens, pos)} != {value.name}")
            
            case NonTerminal(_, rule):
                if rule is None:
                    raise AssertionError("Invalid tree - no linking rule")
                return self.parse_node(rule.body, tokens, pos)
            
            case Sequence(children):
                parsed_children: list[ParsedNode | Token[Definitions]] = []
                result = None
                for child in children:
                    result = self.parse_node(child, tokens, pos)
                    parsed_children.append(result.tree)
                    pos = result.pos
                
                if result is None:
                    raise AssertionError("Invalid tree - empty sequence")
                
                print(f"Match OKAY: {print_token_safe(tokens, pos)}, {node}")

                return ParseResult(
                    ParsedNode(Sequence.__name__, tuple(parsed_children)), pos
                )

            case Alternative(options):
                print(f"Try match {print_token_safe(tokens, pos)} {node}")
                for option in options:
                    try:
                        result = self.parse_node(option, tokens, pos)
                        print(f"Success {print_token_safe(tokens, pos)} {node}")
                        # return result
                        return ParseResult(
                            ParsedNode(Alternative.__name__, (result.tree,)),
                            result.pos
                        )
                    except SyntaxError:
                        continue
                raise SyntaxError(f"No valid options. {print_token_safe(tokens, pos)} not in {node}")
        
            case OptionalNode(child):
                try:
                    result = self.parse_node(child, tokens, pos)
                    return ParseResult(
                        ParsedNode(
                            OptionalNode.__name__, (result.tree,)
                        ),
                        result.pos
                    )
                except SyntaxError:
                    return ParseResult(
                        ParsedNode(
                            OptionalNode.__name__, ()
                        ),
                        pos
                    )
                
            case Repeat(child):
                print(f"Try match {print_token_safe(tokens, pos)} {node}")
                parsed_children: list[ParsedNode | Token[Definitions]] = []

                while True:
                    try:
                        result = self.parse_node(child, tokens, pos)
                        print(f"Success {print_token_safe(tokens, pos)} {node}")
                    except SyntaxError:
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
            

    def build_ast():
        pass


    def read(self, text: str) -> ParseResult:
        tokens = list(self.tokenizer.read(text))
        return self.parse_node(get_root_node(self.rules).body, tokens, 0)
