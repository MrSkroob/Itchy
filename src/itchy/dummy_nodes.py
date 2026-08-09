from itchy.tokenizer import Definitions, Token
from itchy.tree import ParsedNode, Alternative, Sequence, OptionalNode


def find_token(node: ParsedNode, kind: Definitions) -> Token[Definitions] | None:
    for token in node.children:
        if not isinstance(token, Token):
            continue
        if token.kind == kind:
            return token

    for child in node.children:
        if isinstance(child, ParsedNode):
            result = find_token(child, kind)
            if result is not None:
                return result

    return None


def find_node(node: ParsedNode, name: str) -> ParsedNode | None:
    if node.name == name:
        return node

    for child in node.children:
        if isinstance(child, ParsedNode):
            result = find_node(child, name)
            if result is not None:
                return result

    return None


def make_dummy_primary(line: int = 1, char: int = 1) -> tuple[ParsedNode]:
    """
    A literal 0.
    """
    number = Token(Definitions.Number, "0", line, char)

    literals = ParsedNode(
        "literals",
        (
            ParsedNode(
                Alternative.__name__,
                (number,),
            ),
        ),
    )

    return (ParsedNode(
        Alternative.__name__,
        (literals,),
    ),)


def make_var(line: int = 1, char: int = 1):
    """
    
    """
    return (
        ParsedNode(
            Sequence.__name__,
            (
                Token(Definitions.Symbol, "__error__", line, char),
                ParsedNode(OptionalNode.__name__, ()),
            ),
        ),
    )


def make_args(line: int = 1, char: int = 1):
    """Recover a broken argument list as an empty ``()`` argument list."""
    return (
        ParsedNode(
            Sequence.__name__,
            (
                Token(Definitions.OpenBracket, "(", line, char),
                ParsedNode(OptionalNode.__name__, ()),
                Token(Definitions.CloseBracket, ")", line, char),
            ),
        ),
    )


def make_paramlist(line: int = 1, char: int = 1):
    """
    Recover a malformed function parameter list as empty.
    """
    return ()


def make_chunk(line: int = 1, char: int = 1):
    """Recover a malformed statement sequence as an empty chunk."""
    return ()


def make_wrap(line: int = 1, char: int = 1):
    """
    Recover a malformed block as ``{}``.
    """
    return (
        ParsedNode(
            Sequence.__name__,
            (
                Token(Definitions.OpenCurlyBracket, "{", line, char),
                ParsedNode("chunk", ()),
                Token(Definitions.CloseCurlyBracket, "}", line, char),
            ),
        ),
    )


Strategy = dict[str, tuple[ParsedNode | Token[Definitions], ...]]


RECOVERY_STRATEGIES: Strategy = {
    "primary": make_dummy_primary(),
    "args": make_args()
}


FUNC_SIGNATURE_STRATEGIES: Strategy = {
    **RECOVERY_STRATEGIES,
    "wrap": make_wrap()
}