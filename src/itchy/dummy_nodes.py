from typing import Protocol
from itchy.tokenizer import Definitions, Token
from itchy.tree import ParsedNode, Alternative, Sequence, OptionalNode


def extract_tokens(node: ParsedNode) -> list[Token[Definitions]]:
    tokens: list[Token[Definitions]] = []

    for token in node.children:
        if not isinstance(token, Token):
            tokens.extend(extract_tokens(token))
            continue
        tokens.append(token)

    return tokens


def find_tokens(node: ParsedNode, kind: Definitions) -> list[Token[Definitions]]:
    tokens: list[Token[Definitions]] = []

    for token in node.children:
        if not isinstance(token, Token):
            continue
        if token.kind == kind:
            tokens.append(token)
            # return token, index == len(node.children) - 1

    for child in node.children:
        if isinstance(child, ParsedNode):
            result = find_tokens(child, kind)
            tokens.extend(result)

    return tokens


def find_nodes(node: ParsedNode, name: str) -> list[ParsedNode]:
    result: list[ParsedNode] = []

    if node.name == name:
        result.append(node)

    for child in node.children:
        if isinstance(child, ParsedNode):
            result.extend(find_nodes(child, name))

    return result


def find_last_node(node: ParsedNode, name: str) -> ParsedNode | None:
    nodes = find_nodes(node, name)
    return nodes[-1] if nodes else None


def make_equation(line: int=1, char: int=1):
    number = Token(Definitions.Number, "0", line, char, dummy_token=True)
    return (number,)


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
                ParsedNode(OptionalNode.__name__, (), dummy_node=True),
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


def dummy_token_factory(kind: Definitions, value: str):
    def dummy_token(line: int=1, char: int=1):
        return (
            Token(
                kind,
                value,
                line,
                char,
                dummy_token=True
            ),
        )
    return dummy_token


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
                    ParsedNode("chunk", (), dummy_node=True),
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
        dummy_node=True
    )

    return (
        ParsedNode(
            Alternative.__name__,
            (wrap,),
            dummy_node=True
        ),
    )


def make_bracket_factory(bracket: str, token_kind: Definitions):
    def bracket_factory(line: int=1, char: int=1):
        return (Token(
            token_kind,
            literal=bracket,
            line=line,
            char=char,
            dummy_token=True
        ),)
    return bracket_factory

class DummyFactory(Protocol):
    def __call__(self, line: int=1, char: int=1) -> tuple[ParsedNode | Token[Definitions], ...]:
        return ()


Strategy = dict[str, DummyFactory]


RECOVERY_STRATEGIES: Strategy = {
    "primary": make_dummy_primary,
    "args": make_args,
    Definitions.OpenBracket: make_bracket_factory("(", Definitions.OpenBracket),
    Definitions.OpenCurlyBracket: make_bracket_factory("{", Definitions.OpenCurlyBracket),
    Definitions.OpenSquareBracket: make_bracket_factory("[", Definitions.OpenSquareBracket)
}


AGGRESSIVE_STRATEGIES: Strategy = {
    **RECOVERY_STRATEGIES,
    "wrap": make_wrap,
    "stat": make_stat
}

# strategies where we will try not to "add" syntax that changes meaning.
ANALYSIS_STRATEGIES: Strategy = {
    "primary": make_dummy_primary,
    "stat": make_stat,
    "varlist1": make_paramlist,
    # Definitions.OpenBracket.name: make_bracket_factory("(", Definitions.OpenBracket),
    # Definitions.OpenCurlyBracket.name: make_bracket_factory("{", Definitions.OpenCurlyBracket),
    # Definitions.OpenSquareBracket.name: make_bracket_factory("[", Definitions.OpenSquareBracket),
    Definitions.CloseBracket.name: make_bracket_factory(")", Definitions.CloseBracket),
    Definitions.CloseCurlyBracket.name: make_bracket_factory("}", Definitions.CloseCurlyBracket),
    Definitions.CloseSquareBracket.name: make_bracket_factory("]", Definitions.CloseSquareBracket),
    Definitions.StatementSeparator.name: make_bracket_factory(";", Definitions.StatementSeparator)
    # "wrap": make_wrap,
    # "chunk": make_chunk,
    # Definitions.CloseCurlyBracket.name: dummy_token_factory(Definitions.CloseCurlyBracket, "}"),
    # Definitions.OpenCurlyBracket.name: dummy_token_factory(Definitions.OpenCurlyBracket, "{"),
    # Definitions.CloseCurlyBracket.name: dummy_token_factory(Definitions.CloseCurlyBracket, "}"),
    # Definitions.OpenBracket.name: dummy_token_factory(Definitions.OpenBracket, "(")
}


FUNC_SIGNATURE_STRATEGIES: Strategy = {
    **ANALYSIS_STRATEGIES,
    "args": make_args
}