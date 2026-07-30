# This code was made entirely with Chat-GPT.
# sorry :(

from dataclasses import is_dataclass, fields
from typing import Any


def print_ast(node: Any, indent: int = 0) -> None:
    print(format_ast(node, indent))


def format_ast(node: Any, indent: int = 0) -> str:
    lines: list[str] = []
    _format_ast(node, lines, indent)
    return "\n".join(lines)


def _format_ast(node: Any, lines: list[str], indent: int) -> None:
    prefix = "  " * indent

    if isinstance(node, tuple):
        if not node:
            lines.append(prefix + "()")
            return

        lines.append(prefix + "(")
        for item in node: # type: ignore
            _format_ast(item, lines, indent + 1)
        lines.append(prefix + ")")
        return

    if isinstance(node, list):
        if not node:
            lines.append(prefix + "[]")
            return

        lines.append(prefix + "[")
        for item in node: # type: ignore
            _format_ast(item, lines, indent + 1)
        lines.append(prefix + "]")
        return

    if is_dataclass(node):
        cls_name = type(node).__name__
        lines.append(prefix + f"{cls_name}(")

        for field in fields(node):
            value = getattr(node, field.name)
            field_prefix = "  " * (indent + 1)

            if _is_leaf(value):
                lines.append(field_prefix + f"{field.name}={value!r}")
            else:
                lines.append(field_prefix + f"{field.name}=")
                _format_ast(value, lines, indent + 2)

        lines.append(prefix + ")")
        return

    lines.append(prefix + repr(node))


def _is_leaf(value: Any) -> bool:
    return (
        value is None
        or isinstance(value, (str, int, float, bool))
    )