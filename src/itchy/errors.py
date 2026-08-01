from collections.abc import Iterable
from itchy.parser import FailState
from itchy.tokenizer import Token, Definitions
from itchy.assembler import CompilerError

from itchy.tree import (
    Alternative,
    GrammarNode,
    NonTerminal,
    OptionalNode,
    Repeat,
    Sequence,
    Terminal,
)


# Friendly names for tokens whose enum names are not suitable for users.
TOKEN_NAMES: dict[str, str] = {
    # Keywords
    "Define": "'define'",
    "ElseIf": "'elseif'",
    "Return": "'return'",
    "Shared": "'shared'",
    "Event": "'event'",
    "While": "'while'",
    "Break": "'break'",
    "Warp": "'warp'",
    "Else": "'else'",
    "For": "'for'",
    "Let": "'let'",
    "If": "'if'",
    "In": "'in'",

    # Values
    "Number": "a number",
    "String": "a string",
    "Symbol": "an identifier",
    "Bool": "'true' or 'false'",
    "Type": "'var', 'bool', or 'list'",

    # Operators
    "Assign": "an assignment operator",
    "Binop": "a binary operator",

    # Punctuation
    "Colon": "':'",
    "Dot": "'.'",
    "FieldSeperator": "','",
    "OpenBracket": "'('",
    "CloseBracket": "')'",
    "OpenSquareBracket": "'['",
    "CloseSquareBracket": "']'",
    "OpenCurlyBracket": "'{'",
    "CloseCurlyBracket": "'}'",
    "StatementSeperator": "';'",

    # Normally not useful in an error message, but included for completeness
    "Whitespace": "whitespace",
    "Comment": "a comment",
}


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


def format_syntax_error(
    fail_state: FailState,
    source: str,
    filename: str,
) -> str:
    token = _token_at_failure(fail_state)
    expected = _expected_descriptions(fail_state.node)

    if token is None:
        line_number, character = _end_position(source)
        found = "end of file"
        underline_length = 1
    else:
        line_number = token.line
        character = token.char
        found = _describe_found_token(token)
        underline_length = max(1, len(token.literal))

    source_lines = source.splitlines()

    # This supports either zero-based or one-based token line numbers.
    source_index = line_number
    if not (0 <= source_index < len(source_lines)):
        source_index = line_number - 1

    line_text = (
        source_lines[source_index]
        if 0 <= source_index < len(source_lines)
        else ""
    )

    display_line = source_index + 1
    display_column = character + 1

    # Do not let tabs make the caret visibly misaligned.
    caret_padding = _visual_padding(line_text[:character])
    caret = " " * caret_padding + "^" * underline_length

    if expected:
        message = f"expected {_join_expected(expected)}, but found {found}"
    else:
        message = f"unexpected {found}"

    return (
        f'  File "{filename}", line {display_line}, column {display_column}\n'
        f"    {line_text}\n"
        f"    {caret}\n"
        f"SyntaxError: {message}"
    )


def _token_at_failure(fail_state: FailState):
    if not fail_state.tokens:
        return None

    if fail_state.pos >= len(fail_state.tokens):
        return None

    return fail_state.tokens[fail_state.pos]


def _expected_descriptions(node: GrammarNode) -> list[str]:
    terminals = _first_terminals(node, visited_rules=set())

    descriptions: list[str] = []
    seen: set[str] = set()

    for terminal in terminals:
        description = _describe_terminal(terminal)

        if description not in seen:
            seen.add(description)
            descriptions.append(description)

    return descriptions


def _first_terminals(
    node: GrammarNode,
    visited_rules: set[str],
) -> list[Terminal]:
    """
    Finds terminals that could legally appear at the beginning of `node`.

    This is similar to computing a small FIRST set for the failed grammar node.
    """
    match node:
        case Terminal():
            return [node]

        case Alternative(options=options):
            return _flatten(
                _first_terminals(option, visited_rules.copy())
                for option in options
            )

        case Sequence(children=children):
            terminals: list[Terminal] = []

            for child in children:
                terminals.extend(
                    _first_terminals(child, visited_rules.copy())
                )

                # A required child must occur before later sequence children.
                if not _is_optional(child):
                    break

            return terminals

        case OptionalNode(child=child) | Repeat(child=child):
            return _first_terminals(child, visited_rules)

        case NonTerminal(name=name, rule=rule):
            if rule is None or name in visited_rules:
                return []

            visited_rules.add(name)
            return _first_terminals(rule.body, visited_rules)

        case _:
            return []


def _is_optional(node: GrammarNode) -> bool:
    match node:
        case OptionalNode() | Repeat():
            return True

        case Alternative(options=options):
            return any(_is_optional(option) for option in options)

        case Sequence(children=children):
            return all(_is_optional(child) for child in children)

        case NonTerminal(rule=rule):
            return rule is not None and _is_optional(rule.body)

        case _:
            return False


def _describe_terminal(terminal: Terminal) -> str:
    token_kind = terminal.child
    name = token_kind.name

    friendly_name = TOKEN_NAMES.get(name)
    if friendly_name is not None:
        return friendly_name

    # Keywords such as Define, While and Return are clearer as literals.
    value = getattr(token_kind, "value", None)

    if isinstance(value, str) and token_kind.name == Definitions.String.name:
        return repr(value)

    return _split_enum_name(name)


def _describe_found_token(token: Token[Definitions]) -> str:
    kind_name = token.kind.name
    literal = token.literal

    if kind_name == "EOF":
        return "the end of the file"

    if kind_name == "Newline":
        return "a newline"

    if literal:
        return repr(literal)

    return _split_enum_name(kind_name)


def _split_enum_name(name: str) -> str:
    words: list[str] = []

    for character in name:
        if character.isupper() and words:
            words.append(" ")
        words.append(character.lower())

    return "".join(words)


def _join_expected(expected: list[str], limit: int = 5) -> str:
    if len(expected) > limit:
        remaining = len(expected) - limit
        expected = expected[:limit] + [f"{remaining} other possibilities"]

    if len(expected) == 1:
        return expected[0]

    if len(expected) == 2:
        return f"{expected[0]} or {expected[1]}"

    return ", ".join(expected[:-1]) + f", or {expected[-1]}"


def _flatten(groups: Iterable[list[Terminal]]) -> list[Terminal]:
    result: list[Terminal] = []

    for group in groups:
        result.extend(group)

    return result


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