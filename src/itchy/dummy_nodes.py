from typing import Callable
from itchy.tokenizer import Definitions, Token
from itchy.tree import ParsedNode, Alternative, Sequence, OptionalNode



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


RECOVERY_STRATEGIES: dict[str, Callable[[], tuple[ParsedNode]]] = {
    "primary": make_dummy_primary,
    "args": make_args
}