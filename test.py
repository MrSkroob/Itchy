import pytest

from typing import TypeVar
from itchy.parser import Parser, ParseError
from itchy.parserv2 import Parser as Parser2
from itchy.dummy_nodes import ANALYSIS_STRATEGIES, make_wrap


OLD_PARSER = "Parser"
NEW_PARSER = "Parser2"
PARSER_OPTION = NEW_PARSER


ParserTest = TypeVar("ParserTest", Parser, Parser2)


def make_parser() -> Parser | Parser2:
    # Replace these with the same strategies/options your compiler normally uses.
    if PARSER_OPTION == NEW_PARSER:
        return Parser2(
            allow_recovery=True,
            recovery_nodes=ANALYSIS_STRATEGIES
        )
    
    return Parser(
        skip_bad_tokens=True,
        skip_rules_on_fail=ANALYSIS_STRATEGIES,
        recoverable_rules={"wrap": make_wrap},
    )


def assert_valid(source: str):
    parser = make_parser()
    result = parser.read(source)

    assert result is not None
    assert parser.accumulated_errors == []


def assert_invalid(source: str):
    parser = make_parser()
    try:
        result = parser.read(source)
        assert result is not None
    except ParseError:
        pass

    assert parser.accumulated_errors


# ---------------------------------------------------------------------------
# Valid programs
# ---------------------------------------------------------------------------

@pytest.mark.parametrize(
    "source",
    [
        # Empty function.
        """
        define foo() {
        }
        """,

        # Simple statement.
        """
        define foo() {
            motion_movesteps(10);
        }
        """,

        # Multiple statements.
        """
        define foo() {
            motion_movesteps(10);
            motion_turnright(15);
            motion_movesteps(20);
        }
        """,

        # While.
        """
        define foo() {
            while true {
                motion_movesteps(10);
            }
        }
        """,

        # Nested while.
        """
        define foo() {
            while true {
                while true {
                    motion_movesteps(10);
                }
            }
        }
        """,

        # Statement after block.
        """
        define foo() {
            while true {
                motion_movesteps(10);
            }

            motion_movesteps(20);
        }
        """,

        # Statement before block.
        """
        define foo() {
            motion_movesteps(10);

            while true {
                motion_movesteps(20);
            }
        }
        """,

        # Multiple functions / chunks.
        """
        define foo() {
            motion_movesteps(10);
        }

        define bar() {
            motion_movesteps(20);
        }
        """,

        # Empty block followed by another function.
        """
        define foo() {
        }

        define bar() {
        }
        """,
    ],
)
def test_valid_programs(source: str):
    assert_valid(source)


# ---------------------------------------------------------------------------
# Repeat regressions
# ---------------------------------------------------------------------------

def test_repeat_ends_normally_at_closing_brace():
    assert_valid(
        """
        define foo() {
            while true {
                motion_movesteps(10);
            }
        }
        """
    )


def test_repeat_allows_multiple_statements():
    assert_valid(
        """
        define foo() {
            motion_movesteps(10);
            motion_movesteps(20);
            motion_movesteps(30);
        }
        """
    )


def test_nested_repeat_ends_normally():
    assert_valid(
        """
        define foo() {
            while true {
                while true {
                    motion_movesteps(10);
                    motion_movesteps(20);
                }

                motion_movesteps(30);
            }
        }
        """
    )


# ---------------------------------------------------------------------------
# Invalid statements
# ---------------------------------------------------------------------------

def test_invalid_statement_in_function():
    assert_invalid(
        """
        define foo() {
            yourekrillingmeman;
        }
        """
    )


def test_invalid_statement_without_semicolon():
    assert_invalid(
        """
        define foo() {
            yourekrillingmeman
        }
        """
    )


def test_invalid_statement_inside_while():
    assert_invalid(
        """
        define foo() {
            while true {
                yourekrillingmeman;
            }
        }
        """
    )


def test_invalid_statement_without_semicolon_inside_while():
    assert_invalid(
        """
        define foo() {
            while true {
                yourekrillingmeman
            }
        }
        """
    )


def test_repeat_allows_empty_function_body():
    assert_valid(
        """
        define foo() {
        }
        """
    )


def test_repeat_allows_single_statement():
    assert_valid(
        """
        define foo() {
            motion_movesteps(10);
        }
        """
    )


def test_repeat_stops_at_closing_brace():
    assert_valid(
        """
        define foo() {
            motion_movesteps(10);
        }

        define bar() {
            motion_movesteps(20);
        }
        """
    )


def test_nested_repeat_blocks():
    assert_valid(
        """
        define foo() {
            if true {
                motion_movesteps(10);
                motion_movesteps(20);
            }

            motion_movesteps(30);
        }
        """
    )


def test_repeat_rejects_partially_matched_statement():
    assert_invalid(
        """
        define foo() {
            motion_movesteps(
        }
        """
    )


def test_repeat_rejects_invalid_statement_between_valid_statements():
    assert_invalid(
        """
        define foo() {
            motion_movesteps(10);
            @
            motion_movesteps(20);
        }
        """
    )

