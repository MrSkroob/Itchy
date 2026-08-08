from itchy.tokenizer import Definitions, Token
from itchy.tree import ParsedNode, Alternative, Sequence, OptionalNode



def make_dummy_primary(line: int = 1, char: int = 1) -> ParsedNode:
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

    return ParsedNode(
        "primary",
        (
            ParsedNode(
                Alternative.__name__,
                (literals,),
            ),
        ),
    )


def recover_var(line: int = 1, char: int = 1):
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


def recover_args(line: int = 1, char: int = 1):
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


def recover_paramlist(line: int = 1, char: int = 1):
    """
    Recover a malformed function parameter list as empty.
    """
    return ()


def recover_chunk(line: int = 1, char: int = 1):
    """Recover a malformed statement sequence as an empty chunk."""
    return ()


def recover_wrap(line: int = 1, char: int = 1):
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

