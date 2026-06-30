from tokenizer import *
from tree import *


@dataclass
class ParseResult:
    node: Token[Definitions] | None
    pos: int


class Parser:
    def __init__(self, bnf_file: str) -> None:
        self.rules = build_parse_tree()
        self.tokenizer = Tokenizer(Definitions)

    def parse_node(self, node: GrammarNode, tokens: list[Token[Definitions]], pos: int) -> ParseResult:
        match node:
            case Terminal(value):
                if pos < len(tokens) and tokens[pos].kind is value:
                    return ParseResult(tokens[pos], pos + 1)
                raise SyntaxError
            
            case NonTerminal(_, rule):
                if rule is None:
                    raise AssertionError("Invalid tree - no linking rule")
                return self.parse_node(rule.body, tokens, pos)
            
            case Sequence(children):
                result = None
                for child in children:
                    result = self.parse_node(child, tokens, pos)
                    pos = result.pos
                
                if result is None:
                    raise AssertionError("Invalid tree - empty sequence")

                return result

            case Alternative(options):
                for option in options:
                    try:
                        return self.parse_node(option, tokens, pos)
                    except SyntaxError:
                        pass
                raise SyntaxError
        
            case OptionalNode(child):
                try:
                    return self.parse_node(child, tokens, pos)
                except SyntaxError:
                    return ParseResult(None, pos)
                
            case Repeat(child):
                while True:
                    result = self.parse_node(child, tokens, pos)
                    if result.pos == pos:
                        break

                    pos = result.pos
                
                return result
            
            case GrammarNode():
                pass

    def read(self, text: str) -> ParseResult:
        tokens = list(self.tokenizer.read(text))
        return self.parse_node(get_root_node(self.rules).body, tokens, 0)

            