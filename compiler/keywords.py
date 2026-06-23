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


class Keyword(StrEnum):
    NoRefresh = r"\b(norefresh)\b"
    Type = r"\b(number|string|list|bool|any)\b"
    Bool = r"\b(true)|(false)\b"
    BoolOp = r"(and)|(or)"
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


class BNFRules(StrEnum):
    Flag = "^--!"
    Comment = r"//.*"
    Assign = "::="
    CurlyBrackets = r"(?<!\")\{(.*?)\}(?!\")"
    SquareBrackets = r"(?<!\")\[(.*?)\](?!\")"
    Rule = r"<\w*>"
    StringLiteral = r"[a-z0-9]*(\"(?:\\.|[^\\\"])*\"|\'(?:\\.|[^\\'])*\')"
    # Symbol = r"<([a-zA-Z_][a-zA-Z0-9_]*)>|<\$>"
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

        while pos < len(text):
            if text[pos] == "\n":
                line += 1
                char = 1
                pos += 1
                continue

            match = self.regex.match(text, pos)

            if match is None:
                raise ValueError(f"Invalid character {text[pos]!r} at line {line}, char {char}")
            
            group = match.lastgroup
            literal = match.group()

            if group is None:
                raise AssertionError("Empty group [this should not happen]")

            kind = self.rules[group]
            yield Token(kind, literal, line, char)

            pos = match.end()
            char += len(literal)
