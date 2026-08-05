from enum import StrEnum

"""
YES. THIS IS IN itchy/tokenizer.py 
YES. THIS IS A DUPLICATE OF THAT
NO. I AM NOT DEALING WITH PYTHON'S SHITTY IMPORT SYSTEM
GOODBYE.
"""
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


SCOPE_GROUPS: dict[str, tuple[Definitions, ...]] = {
    "keyword.declaration.itchy": (
        Definitions.Define,
        Definitions.Event,
    ),
    "keyword.control.conditional.itchy": (
        Definitions.If,
        Definitions.ElseIf,
        Definitions.Else,
    ),
    "keyword.control.loop.itchy": (
        Definitions.While,
        Definitions.For,
    ),
    "keyword.control.flow.itchy": (
        Definitions.Return,
    ),
}


TEXTMATE_SCOPES = {
    definition: scope
    for scope, definitions in SCOPE_GROUPS.items()
    for definition in definitions
}


TEXTMATE_SCOPES.update({
    Definitions.Shared: "storage.modifier.itchy",
    Definitions.Warp: "storage.modifier.itchy",

    Definitions.In: "keyword.operator.word.itchy",

    Definitions.Type: "storage.type.itchy",
    Definitions.Bool: "constant.language.boolean.itchy",
    Definitions.Number: "constant.numeric.itchy",

    Definitions.Assign: "keyword.operator.assignment.itchy",
    Definitions.Binop: "keyword.operator.arithmetic.itchy",

    Definitions.Comment: "comment.line.double-slash.itchy",
})


SPECIAL_PATTERNS: dict[Definitions, dict[str, str | list[dict[str, str]]]] = {
    Definitions.BlockComment: {
        "name": "comment.block.itchy",
        "begin": r"/\*",
        "end": r"\*/",
    },
    Definitions.String: {
        "name": "string.quoted.double.itchy",
        "begin": '"',
        "end": '"',
        "patterns": [
            {
                "name": "constant.character.escape.itchy",
                "match": r"\\.",
            },
        ],
    }
}


