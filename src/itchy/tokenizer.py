from dataclasses import dataclass
from typing import Generic, Iterator, TypeVar
from enum import StrEnum
from itchy.shared_templates import SourceSpan, SourcePosition
import re

# these tend to be treated specially other than the other rules below:
class GenericRules(StrEnum):
    Whitespace = "WHITESPACE"
    StatementSeperator = "STATEMENT_SEPERATOR"
    Newline = "NEWLINE"
    EOF = "EOF"

NEWLINE_PATTERN = re.compile(r"\r\n|\r|\n")
TokenRule = TypeVar("TokenRule", bound=StrEnum)

def advance_position(
    literal: str,
    line: int,
    character: int,
) -> tuple[int, int]:
    """
    Advance a one-based source position over `literal`.

    Newlines may be represented by:
        CRLF: \\r\\n
        CR:   \\r
        LF:   \\n
    """
    newline_matches = list(NEWLINE_PATTERN.finditer(literal))

    if not newline_matches:
        return line, character + len(literal)

    line += len(newline_matches)

    final_newline = newline_matches[-1]
    characters_after_newline = len(literal[final_newline.end():])

    # Character positions are one-based.
    character = 1 + characters_after_newline

    return line, character


@dataclass(frozen=True)
class Token(Generic[TokenRule]):
    kind: TokenRule | GenericRules
    literal: str
    line: int
    char: int

    @property
    def span(self) -> SourceSpan:
        end_line, end_character = advance_position(
            self.literal,
            self.line,
            self.char,
        )

        return SourceSpan(
            start=SourcePosition(
                line=self.line,
                character=self.char,
            ),
            end=SourcePosition(
                line=end_line,
                character=end_character,
            ),
        )

    def __repr__(self) -> str:
        return f"Token: {self.kind.name} on line {self.line}"


# could also be considered as compiler rules. 
# definitions provide, well, definitions for certain rules that haven't been defined explicitly in
# your BNF file. 
# typically, they'd be rules that are too generic or simple to warrant a rule, like numbers and symbols and keywords.
class Definitions(StrEnum):
    Comment = r"//.*"
    BlockComment = r"/\*[\s\S]*?\*/"
    Define = r"\b(define)\b"
    ElseIf = r"\b(elseif)\b"
    Return = r"\b(return)\b"
    Shared = r"\b(shared)\b"
    Event = r"\b(event)\b"
    While = r"\b(while)\b"
    # Break = r"\b(break)\b" # no support, but here for completion's sake
    Warp = r"\b(warp)\b"
    Else = r"\b(else)\b"
    For = r"\b(for)\b"
    # Not = r"\b(not)\b"
    If = r"\b(if)\b"
    In = r"\b(in)\b"
    Number = r"[0-9][_0-9]*(\.[0-9][_0-9]*)?"
    Type = r"\b(?:var|bool|list)\b"
    Bool = r"\b(?:true|false)\b"
    Assign = r"\*=|\+=|-=|/=|=(?!=)"
    Binop = r"\.\.|<=|>=|==|!=|\+|-|\*|/|<|>|\b(?:and|or)\b|\b(not)\b"
    String = r"[a-z0-9]*(\"(?:\\.|[^\\\"])*\"|\'(?:\\.|[^\\'])*\')"
    Symbol = r"([a-zA-Z_][a-zA-Z0-9_]*)|\$"
    Colon = r":"
    Dot = r"\."
    FieldSeperator = r","
    OpenBracket = r"\("
    OpenSquareBracket = r"\["
    OpenCurlyBracket = r"\{"
    CloseBracket = r"\)"
    CloseSquareBracket = r"\]"
    CloseCurlyBracket = r"\}"
    Whitespace = r"[ \t]+"
    StatementSeperator = r";"

# regex that is vital in interpreting bnf. 
class BNFRules(StrEnum):
    Comment = r"//.*"
    Assign = "::="
    # CurlyBrackets = r"(?<!\")\{(.*?)\}(?!\")"
    # SquareBrackets = r"(?<!\")\[(.*?)\](?!\")"
    NonTerminalRule = r"<[a-z][a-z0-9_]*>"
    TerminalRule = r"[a-z0-9]*(\"(?:\\.|[^\\\"])*\"|\'(?:\\.|[^\\'])*\')|<[A-Z][A-Za-z0-9_]*>"
    OpenCurlyBrace = r"\{"
    CloseCurlyBrace = r"\}"
    OpenSquareBrace = r"\["
    CloseSquareBrace = r"\]"
    OpenBrace = r"\("
    CloseBrace = r"\)"
    Whitespace = r"[ \t]+"
    Pipe = r"\|"


# compile the set of regex into singular regex.
def compile_rules(rules: type[TokenRule]):
    parts: list[str] = []
    for rule in rules:
        parts.append(f"(?P<{rule.name}>{rule.value})")

    return re.compile("|".join(parts))



class Tokenizer(Generic[TokenRule]):
    def __init__(
        self,
        rules: type[TokenRule],
        blacklist: set[str],
    ) -> None:
        self.rules = rules
        self.regex = compile_rules(rules)
        self.blacklist = blacklist

    def read(
        self,
        text: str,
    ) -> Iterator[Token[TokenRule]]:
        line = 1
        char = 1
        pos = 0

        while pos < len(text):
            # Handle CRLF, CR, and LF as exactly one newline.
            newline_match = NEWLINE_PATTERN.match(text, pos)

            if newline_match is not None:
                literal = newline_match.group(0)

                if GenericRules.Newline.name not in self.blacklist:
                    yield Token(
                        kind=GenericRules.Newline,
                        literal=literal,
                        line=line,
                        char=char,
                    )

                line, char = advance_position(
                    literal,
                    line,
                    char,
                )
                pos = newline_match.end()
                continue

            match = self.regex.match(text, pos)

            if match is None:
                # Retaining your previous behaviour: stop at the first
                # unrecognised character.
                break

            group = match.lastgroup
            literal = match.group(0)

            if group is None:
                raise AssertionError("Matched token has no named group")

            kind = self.rules[group]

            if kind.name not in self.blacklist:
                # Do not strip the literal. Its contents and length must
                # agree with its source position.
                yield Token(
                    kind=kind,
                    literal=literal,
                    line=line,
                    char=char,
                )

            # This also handles multiline block comments correctly.
            line, char = advance_position(
                literal,
                line,
                char,
            )
            pos = match.end()

        yield Token(
            kind=GenericRules.EOF,
            literal=r"\Z",
            line=line,
            char=char,
        )
# {"Whitespace", "Comment"}


if __name__ == "__main__":
    tokenizer = Tokenizer(BNFRules, set())

    for token in tokenizer.read(open('compiler/bnf.txt').read()):
        print(token)
