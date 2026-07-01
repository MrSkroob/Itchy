from tokenizer import *
from tree import *


@dataclass
class ParseResult:
    node: Token[Definitions] | None
    pos: int


class Parser:
    def __init__(self) -> None:
        self.rules = build_parse_tree()
        self.tokenizer = Tokenizer(Definitions)
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
                    print(f"Match OKAY: {tokens[pos].kind.name}, {node}")
                    return ParseResult(tokens[pos], pos + 1)
                raise SyntaxError(f"Terminal rule not matched: {value.name} != {tokens[pos].kind.name}")
            
            case NonTerminal(_, rule):
                if rule is None:
                    raise AssertionError("Invalid tree - no linking rule")
                return self.parse_node(rule.body, tokens, pos)
            
            case Sequence(children):
                try:
                    result = None
                    for child in children:
                        result = self.parse_node(child, tokens, pos)
                        pos = result.pos
                    
                    if result is None:
                        raise AssertionError("Invalid tree - empty sequence")
                    
                    print(f"Match OKAY: {tokens[pos].kind.name}, {node}")

                    return result
                except SyntaxError as e:
                    print(e)
                    raise SyntaxError(f"Could not match sequence. {tokens[pos].kind.name} not in {node}")

            case Alternative(options):
                for option in options:
                    try:
                        result = self.parse_node(option, tokens, pos)
                        print(f"Match OKAY (so far): {tokens[pos].kind.name}, {node}")
                        return result
                    except SyntaxError as e:
                        print(e)
                        pass
                raise SyntaxError(f"No valid options. {tokens[pos].kind.name} not in {node}")
        
            case OptionalNode(child):
                try:
                    return self.parse_node(child, tokens, pos)
                except SyntaxError:
                    print(f"Skipping {node}")
                    return ParseResult(None, pos)
                
            case Repeat(child):
                while True:
                    try:
                        result = self.parse_node(child, tokens, pos)
                    except SyntaxError as e:
                        print(e)
                        print(f"Skipping {node}")
                        break

                    if result.pos == pos:
                        break

                    pos = result.pos

                print(f"Match OKAY: {tokens[pos].kind.name}, {node}")

                return ParseResult(None, pos)
            
            case GrammarNode():
                raise AssertionError("Reached bare GrammarNode")

    def read(self, text: str) -> ParseResult:
        tokens = list(self.tokenizer.read(text))
        return self.parse_node(get_root_node(self.rules).body, tokens, 0)

            