from dataclasses import dataclass
from typing import Generic, Iterator, TypeVar
from enum import StrEnum
import re

# these tend to be treated specially other than the other rules below:
class GenericRules(StrEnum):
    Whitespace = "WHITESPACE"
    StatementSeperator = "STATEMENT_SEPERATOR"
    Newline = "NEWLINE"
    EOF = "EOF"


TokenRule = TypeVar("TokenRule", bound=StrEnum)


@dataclass(frozen=True)
class Token(Generic[TokenRule]):
    kind: TokenRule | GenericRules
    literal: str
    line: int
    char: int

# could also be considered as compiler rules. 
# definitions provide, well, definitions for certain rules that haven't been defined explicitly in
# your BNF file. 
# typically, they'd be rules that are too generic or simple to warrant a rule, like numbers and symbols and keywords.
class Definitions(StrEnum):
    Comment = r"//.*"
    NoRefresh = r"\b(norefresh)\b"
    Define = r"\b(define)\b"
    ElseIf = r"\b(elseif)\b"
    Return = r"\b(return)\b"
    Shared = r"\b(shared)\b"
    While = r"\b(while)\b"
    Break = r"\b(break)\b"
    Else = r"\b(else)\b"
    For = r"\b(for)\b"
    Var = r"\b(var)\b"
    # Not = r"\b(not)\b"
    Let = r"\b(let)\b"
    If = r"\b(if)\b"
    In = r"\b(in)\b"
    Number = r"[0-9][_0-9]*(\.[0-9][_0-9]*)?"
    Type = r"\b(number)|(string)|(list)|(bool)\b"
    Bool = r"\b(true)|(false)\b"
    Binop = r"\.{2}|<=|>=|==|!=|\+|-|\*|/|\^|%|<|>|\b(and|or)\b"
    String = r"[a-z0-9]*(\"(?:\\.|[^\\\"])*\"|\'(?:\\.|[^\\'])*\')"
    Symbol = r"([a-zA-Z_][a-zA-Z0-9_]*)|\$"
    Assign = r"=|(\*=)|(\%=)|(\^=)|(\+=)|(-=)|(/=)"
    Unop = r"-|\b(not)\b"
    SingleEqual = r"="
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
    def __init__(self, rules: type[TokenRule], blacklist: set[str]) -> None:
        self.rules = rules
        self.regex = compile_rules(rules)
        self.blacklist = blacklist

    def read(self, text: str) -> Iterator[Token[TokenRule]]:
        line = 1
        char = 1
        pos = 0

        while pos < len(text):
            if text[pos] == "\n":
                if GenericRules.Newline.name not in self.blacklist:
                    yield Token(GenericRules.Newline, text[pos], line, char)
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
                # should not happen, but we have it here to shut the linter up.
                raise AssertionError("Empty group")

            kind = self.rules[group]

            if kind.name not in self.blacklist:
                yield Token(kind, literal, line, char)

            pos = match.end()
            char += len(literal)
            
        yield Token(GenericRules.EOF, r"\Z", line, char)


# {"Whitespace", "Comment"}


if __name__ == "__main__":
    tokenizer = Tokenizer(BNFRules, set())

    for token in tokenizer.read(open('compiler/bnf.txt').read()):
        print(token)
