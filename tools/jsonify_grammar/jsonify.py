import json
from pathlib import Path
from typing import Any
from constants import *


def make_textmate_pattern(
    definition: Definitions,
) -> dict[str, Any]:
    special = SPECIAL_PATTERNS.get(definition)

    if special is not None:
        return special.copy()

    try:
        scope = TEXTMATE_SCOPES[definition]
    except KeyError as error:
        raise ValueError(
            f"No TextMate scope configured for {definition.name}"
        ) from error

    return {
        "name": scope,
        "match": definition.value,
    }


def generate_textmate_grammar(
    output_path: str | Path,
) -> None:
    patterns = [
        make_textmate_pattern(definition)
        for definition in Definitions
        if (
            definition in TEXTMATE_SCOPES
            or definition in SPECIAL_PATTERNS
        )
    ]

    grammar = { # type: ignore
        "$schema": (
            "https://raw.githubusercontent.com/"
            "martinring/tmlanguage/master/"
            "tmlanguage.json"
        ),
        "name": "Itchy",
        "scopeName": "source.itchy",
        "patterns": patterns,
    }

    output_path = Path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)

    output_path.write_text(
        json.dumps(grammar, indent=2) + "\n",
        encoding="utf-8",
    )


if __name__ == "__main__":
    generate_textmate_grammar(
        "src/itchy/assets/itchy.tmLanguage.json"
    )