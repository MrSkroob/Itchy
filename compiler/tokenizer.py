from dataclasses import dataclass
from typing import Generic, Iterator, TypeVar
from enum import StrEnum
import re


TokenRule = TypeVar("TokenRule", bound=StrEnum)


@dataclass(frozen=True)
class Token(Generic[TokenRule]):
    kind: TokenRule
    literal: str
    line: int
    char: int

# these tend to be treated specially other than the other rules below:
class GenericRules(StrEnum):
    Whitespace = r"[ \t]+"

# could also be considered as compiler rules. 
# definitions provide, well, definitions for certain rules that haven't been defined explicitly in
# your BNF file. 
# typically, they'd be rules that are too generic or simple to warrant a rule, like numbers and symbols and keywords.
class Definitions(StrEnum):
    NoRefresh = r"\b(norefresh)\b"
    Define = r"\b(define)\b"
    ElseIf = r"\b(elseif)\b"
    Return = r"\b(return)\b"
    Shared = r"\b(shared)\b"
    While = r"\b(while)\b"
    Break = r"\b(break)\b"
    Else = r"\b(else)\b"
    List = r"\b(list)\b"
    For = r"\b(for)\b"
    Var = r"\b(var)\b"
    Not = r"\b(not)\b"
    Let = r"\b(let)\b"
    If = r"\b(if)\b"
    In = r"\b(in)\b"
    Number = r"[0-9][_0-9]*(\.[0-9][_0-9]*)?"
    String = r"[a-z0-9]*(\"(?:\\.|[^\\\"])*\"|\'(?:\\.|[^\\'])*\')"
    Bool = r"\b(true)|(false)\b"
    Symbol = r"([a-zA-Z_][a-zA-Z0-9_]*)|\$"
    Binop = r"\+|-|\*|\/|\^|\%|\.{2}|(<=)|<|(>=)|>|(==)|(!=)|(and)|(or)"
    Assign = r"=|(\*=)|(\%=)|(\^=)|(\+=)|(-=)|(/=)"
    Unop = r"-|(not)"
    SingleEqual = r"="
    Type = r"\b(number)|(string)|(list)|(bool)\b"
    Colon = r":"
    Dot = r"\."
    FieldSeperator = r","
    OpenBracket = r"\("
    OpenSquareBracket = r"\["
    OpenCurlyBracket = r"\{"
    CloseBracket = r"\)"
    CloseSquareBracket = r"\]"
    CloseCurlyBracket = r"\}"
    StatementSeperator = r"[\n;]"


class BNFRules(StrEnum):
    Flag = "^--!"
    Comment = r"//.*"
    Assign = "::="
    CurlyBrackets = r"(?<!\")\{(.*?)\}(?!\")"
    SquareBrackets = r"(?<!\")\[(.*?)\](?!\")"
    Rule = r"<\w*>"
    StringLiteral = r"[a-z0-9]*(\"(?:\\.|[^\\\"])*\"|\'(?:\\.|[^\\'])*\')"
    Whitespace = r"[ \t]+"
    Pipe = r"\|"
    StatementSeperator = r"[\n]+"


class Tokenizer(Generic[TokenRule]):
    def __init__(self, rules: type[TokenRule]) -> None:
        # compile the set of regex into singular regex.
        parts: list[str] = []
        for rule in rules:
            parts.append(f"(?P<{rule.name}>{rule.value})")
        
        self.rules = rules
        self.regex = re.compile("|".join(parts))

    def read(self, text: str) -> Iterator[Token[TokenRule]]:
        line = 1
        char = 1
        pos = 0
        in_comment = False

        while pos < len(text):
            if text[pos] == "\n":
                line += 1
                char = 1
                pos += 1
                in_comment = False
                continue

            match = self.regex.match(text, pos)

            if match is None:
                raise ValueError(f"Invalid character {text[pos]!r} at line {line}, char {char}")
            
            group = match.lastgroup
            literal = match.group()

            if group is None:
                raise AssertionError("Empty group")

            kind = self.rules[group]

            if kind.name == "Comment":
                in_comment = True

            if not in_comment:
                yield Token(kind, literal, line, char)

            pos = match.end()
            char += len(literal)
