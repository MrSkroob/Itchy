import pytest
from itchy.parser import Parser, ParseError
from itchy.dummy_nodes import ANALYSIS_STRATEGIES, make_wrap


@pytest.mark.parametrize("source", [
    # Empty/minimal program, if legal.
    "",

    # Simple function.
    """
    define foo() {
    }
    """,

    # Nested constructs.
    """
    define foo() {
        while true {
        }
    }
    """,

    # Statement inside nested construct.
    """
    define foo() {
        while true {
            motion_movesteps(10);
        }
    }
    """,

    # Multiple statements in a Repeat.
    """
    define foo() {
        motion_movesteps(10);
        motion_movesteps(20);
        motion_movesteps(30);
    }
    """,

    # Multiple top-level chunks.
    """
    define foo() {
    }

    define bar() {
    }
    """,
])
def test_valid_code_has_no_parse_errors(source: str):
    parser = Parser(
        skip_bad_tokens=True,
        skip_rules_on_fail=ANALYSIS_STRATEGIES,
        recoverable_rules={"wrap": make_wrap},
    )

    result = parser.read(source)

    assert result is not None
    assert parser.accumulated_errors == []


def test_repeat_can_end_normally():
    parser = Parser()

    parser.read("""
    define foo() {
        while true {
            motion_movesteps(10);
        }

        motion_movesteps(20);
    }
    """)

    assert parser.accumulated_errors == []


@pytest.mark.parametrize("source", [
    """
    define foo() {
        motion_movesteps(10);
    }
    """,
    """
    define foo() {
        while true {
        }
    }
    """,
    """
    define foo() {
        if true {
        }
    }
    """,
])


def test_valid_statement_alternatives_do_not_become_diagnostics(source: str):
    parser = Parser()

    parser.read(source)

    assert parser.accumulated_errors == []


def test_real_error_is_still_reported():
    try:
        parser = Parser()

        parser.read("""
        define foo() {
            while true {
                ohthisiskrillingme;
            }
        }
        """)
        raise AssertionError("failed test. parseerror should be raised")
    except ParseError:
        pass
