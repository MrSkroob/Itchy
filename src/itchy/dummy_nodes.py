from typing import Protocol
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
    number = Token(Definitions.Number, "0", line, char, dummy_token=True)

    literals = ParsedNode(
        "literals",
        (
            ParsedNode(
                Alternative.__name__,
                (number,),
                dummy_node=True
            ),
        ),
        dummy_node=True
    )

    return (ParsedNode(
        Alternative.__name__,
        (literals,),
        dummy_node=True
    ),)


def make_var(line: int = 1, char: int = 1):
    """
    
    """
    return (
        ParsedNode(
            Sequence.__name__,
            (
                Token(Definitions.Symbol, "__error__", line, char, dummy_token=True),
                ParsedNode(OptionalNode.__name__, ()),
            ),
            dummy_node=True
        ),
    )


def make_args(line: int = 1, char: int = 1):
    """Recover a broken argument list as an empty ``()`` argument list."""
    return (
        ParsedNode(
            Sequence.__name__,
            (
                Token(Definitions.OpenBracket, "(", line, char, dummy_token=True),
                ParsedNode(OptionalNode.__name__, (), dummy_node=True),
                Token(Definitions.CloseBracket, ")", line, char, dummy_token=True),
            ),
            dummy_node=True
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
                Token(Definitions.OpenCurlyBracket, "{", line, char, dummy_token=True),
                ParsedNode("chunk", (), dummy_node=True),
                Token(Definitions.CloseCurlyBracket, "}", line, char, dummy_token=True),
            ),
            dummy_node=True
        ),
    )


def make_stat(line: int=1, char: int=1):
    """
    soooo don't use this normally, but this might work for the semantic parser in order to not die when you
    make an unfinished statement
    """

    wrap = ParsedNode(
        "wrap",
        (
            ParsedNode(
                Sequence.__name__,
                (
                    Token(
                        Definitions.OpenCurlyBracket,
                        "{",
                        line,
                        char,
                        dummy_token=True
                    ),
                    ParsedNode("chunk", ()),
                    Token(
                        Definitions.CloseCurlyBracket,
                        "}",
                        line,
                        char,
                        dummy_token=True
                    ),
                ),
                dummy_node=True
            ),
        ),
    )

    return (
        ParsedNode(
            Alternative.__name__,
            (wrap,),
            dummy_node=True
        ),
    )



class DummyFactory(Protocol):
    def __call__(self, line: int=1, char: int=1) -> tuple[ParsedNode | Token[Definitions], ...]:
        return ()


Strategy = dict[str, DummyFactory]


RECOVERY_STRATEGIES: Strategy = {
    "primary": make_dummy_primary,
    "args": make_args
}


AGGRESSIVE_STRATEGIES: Strategy = {
    **RECOVERY_STRATEGIES,
    "wrap": make_wrap,
    "stat": make_stat
}