from itchy.parser import ExpectedState, ParseError
from itchy.tokenizer import Token, Definitions, GenericRules
from itchy.assembler import CompilerError


EXPECTED_PRIORITY: dict[Definitions, int] = {
    # Closing delimiters
    Definitions.CloseBracket: 0,
    Definitions.CloseSquareBracket: 0,
    Definitions.CloseCurlyBracket: 0,

    # Structural punctuation
    Definitions.FieldSeperator: 1,
    Definitions.Colon: 1,
    Definitions.StatementSeperator: 1,

    # Keywords
    Definitions.Else: 2,
    Definitions.ElseIf: 2,
    Definitions.In: 2,

    # Operators
    Definitions.Assign: 3,
    Definitions.Binop: 3,

    # Expression/value starters
    Definitions.Symbol: 4,
    Definitions.Number: 4,
    Definitions.String: 4,
    Definitions.Bool: 4,
    Definitions.OpenBracket: 4,
    Definitions.OpenSquareBracket: 4,
}


TOKEN_NAMES: dict[str, str] = {
    # Keywords
    Definitions.Define.name: "'define'",
    Definitions.ElseIf.name: "'elseif'",
    Definitions.Return.name: "'return'",
    Definitions.Shared.name: "'shared'",
    Definitions.Event.name: "'event'",
    Definitions.While.name: "'while'",
    Definitions.Warp.name: "'warp'",
    Definitions.Else.name: "'else'",
    Definitions.For.name: "'for'",
    Definitions.If.name: "'if'",
    Definitions.In.name: "'in'",

    # Values
    Definitions.Number.name: "a number",
    Definitions.String.name: "a string",
    Definitions.Symbol.name: "an identifier",
    Definitions.Bool.name: "'true' or 'false'",
    Definitions.Type.name: "'var', 'bool', or 'list'",

    # Operators
    Definitions.Assign.name: "an assignment operator",
    Definitions.Binop.name: "an operator",

    # Punctuation
    Definitions.Colon.name: "':'",
    Definitions.Dot.name: "'.'", # not used, but might be for imports in the future.
    Definitions.FieldSeperator.name: "','",
    Definitions.OpenBracket.name: "'('",
    Definitions.CloseBracket.name: "')'",
    Definitions.OpenSquareBracket.name: "'['",
    Definitions.CloseSquareBracket.name: "']'",
    Definitions.OpenCurlyBracket.name: "'{'",
    Definitions.CloseCurlyBracket.name: "'}'",
    Definitions.StatementSeperator.name: "';'",
}


def _expected_sort_key(kind: Definitions) -> tuple[int, str]:
    return (
        EXPECTED_PRIORITY.get(kind, 3),
        kind.name,
    )


def format_compiler_error(
    error: CompilerError,
    source: str,
    filename: str = "<source>",
) -> str:
    lines = source.splitlines()
    span = error.error_node.span if error.error_node is not None else None

    if span is None:
        # no span info available - fall back to a bare message
        return f'  File "{filename}"\nCompilerError: {error}'

    start = span.start
    end = span.end

    line_number = start.line
    if 1 <= line_number <= len(lines):
        source_line = lines[line_number - 1]
    else:
        source_line = ""

    if end.line == start.line:
        pointer_width = max(1, end.character - start.character)
    else:
        # span crosses multiple lines - just underline to the end of the first line
        pointer_width = max(1, len(source_line) - start.character + 1)

    return (
        f'  File "{filename}", line {line_number}\n'
        f"    {source_line}\n"
        f"    {' ' * (start.character - 1)}{'^' * pointer_width}\n"
        f"{error.__class__.__name__}: {error}"
    )


def _choose_expected(
    kinds: set[Definitions],
) -> list[Definitions]:
    closers = kinds & CLOSING_DELIMITERS

    if closers:
        return sorted(closers, key=_expected_sort_key)

    return sorted(kinds, key=_expected_sort_key)


def get_message(error: ParseError, expected: ExpectedState):
    pos = expected.pos
    token = (
        error.tokens[pos]
        if 0 <= pos < len(error.tokens)
        else None
    )

    if token is None:
        found = "end of file"
    else:
        found = _describe_found_token(token)

    expected_kinds = {
        item.definition
        for item in expected.items
        if isinstance(item.definition, Definitions)
    }

    expected_names = [
        _describe_token_kind(kind)
        for kind in _choose_expected(expected_kinds)
    ]

    if expected_names:
        message = (
            f"expected {_join_expected(expected_names, 1)}, "
            f"but found {found}"
        )
    else:
        message = f"unexpected {found}"

    return message

def format_syntax_error(
    error: ParseError,
    expected: ExpectedState,
    source: str,
    filename: str,
) -> str:
    pos = expected.pos
    token = (
        error.tokens[pos]
        if 0 <= pos < len(error.tokens)
        else None
    )

    if token is None:
        line_number, character = _end_position(source)
        underline_length = 1
    else:
        line_number = token.line
        character = token.char
        underline_length = max(1, len(token.literal))

    message = get_message(error, expected)

    source_lines = source.splitlines()

    source_index = line_number
    if not (0 <= source_index < len(source_lines)):
        source_index = line_number - 1

    line_text = (
        source_lines[source_index]
        if 0 <= source_index < len(source_lines)
        else ""
    )

    display_line = source_index + 1
    caret_padding = _visual_padding(line_text[:character])
    caret = " " * caret_padding + "^" * underline_length

    return (
        f'  File "{filename}", line {display_line}, column {character}\n'
        f"    {line_text}\n"
        f"    {caret}\n"
        f"SyntaxError: {message}"
    )


def _describe_token_kind(
    kind: Definitions | GenericRules,
) -> str:
    return TOKEN_NAMES.get(
        kind.name,
        _split_enum_name(kind.name),
    )


def _describe_found_token(token: Token[Definitions]) -> str:
    if token.kind.name == "EOF":
        return "end of file"

    if token.kind.name == "Newline":
        return "a newline"

    if token.literal:
        return repr(token.literal)

    return _split_enum_name(token.kind.name)


def _split_enum_name(name: str) -> str:
    result = ""

    for char in name:
        if char.isupper() and result:
            result += " "
        result += char.lower()

    return result


CLOSING_DELIMITERS = {
    Definitions.CloseBracket,
    Definitions.CloseSquareBracket,
    Definitions.CloseCurlyBracket,
}


def _join_expected(expected: list[str], limit: int = 5) -> str:
    if len(expected) > limit:
        remaining = len(expected) - limit
        expected = expected[:limit] + [
            f"{remaining} other possibilities"
        ]

    if len(expected) == 1:
        return expected[0]

    if len(expected) == 2:
        return f"{expected[0]} or {expected[1]}"

    return ", ".join(expected[:-1]) + f", or {expected[-1]}"


def _visual_padding(text: str, tab_size: int = 4) -> int:
    position = 0

    for character in text:
        if character == "\t":
            position += tab_size - position % tab_size
        else:
            position += 1

    return position


def _end_position(source: str) -> tuple[int, int]:
    lines = source.splitlines()

    if not lines:
        return 0, 0

    return len(lines) - 1, len(lines[-1])