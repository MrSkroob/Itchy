from itchy.tokenizer import Definitions, Token
from itchy.tree import ParsedNode, Alternative


def make_dummy_primary(line: int=1, char: int=1) -> ParsedNode:
    number = Token(
        Definitions.Number,
        "0",
        line,
        char
    )

    literals = ParsedNode(
        "literals",
        (
            ParsedNode(
                Alternative.__name__,
                (number,)
            ),
        )
    )

    return ParsedNode(
        "primary",
        (
            ParsedNode(
                Alternative.__name__,
                (literals,)
            ),
        )
    )

