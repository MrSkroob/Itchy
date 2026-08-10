from __future__ import annotations
from typing import Mapping
from dataclasses import dataclass, field
from itchy.tokenizer import Definitions, GenericRules, Tokenizer, Token
from itchy.tree import Rule, Terminal, NonTerminal, Alternative, OptionalNode, Repeat, Sequence, GrammarNode, ParsedNode, build_parse_tree, get_root_node


DEBUG = False


@dataclass(frozen=True)
class ExpectedToken:
    definition: Definitions | GenericRules
    path: tuple[str, ...]


@dataclass()
class ExpectedState:
    pos: int = -1
    items: set[ExpectedToken] = field(default_factory=set[ExpectedToken])

    def record(self, pos: int, definition: Definitions | GenericRules, rule_path: tuple[str, ...]) -> None:
        expectation = ExpectedToken(definition, rule_path)

        if pos > self.pos:
            self.pos = pos
            self.items = {expectation}
        elif pos == self.pos:
            self.items.add(expectation)


@dataclass
class ParseResult():
    tree: ParsedNode | Token[Definitions]
    pos: int

    def __repr__(self) -> str:
        return str(self.tree)


@dataclass
class FailState():
    node: GrammarNode
    tokens: list[Token[Definitions]]
    pos: int


class ParseError(Exception):
    def __init__(self, tokens: list[Token[Definitions]], pos: int, rule_start: int, failed_rule: Rule, node: GrammarNode, 
                 previous_valid_tree: ParseResult | None=None) -> None:
        self.tokens = tokens
        self.pos = pos
        self.node = node
        self.rule = failed_rule
        self.rule_start = rule_start
        self.previous_valid_tree: ParseResult | None = previous_valid_tree
        super().__init__()


class InvalidTreeError(Exception):
    def __init__(self, message: str) -> None:
        super().__init__(message)


def debug_print(message: str):
    if not DEBUG:
        return
    print(message)


def print_token_safe(tokens: list[Token[Definitions]], pos: int):
    if len(tokens) == 0:
        return ""
    return tokens[min(pos, len(tokens) - 1)].literal


class Parser:
    def __init__(self, *, skip_bad_tokens: bool=False, skip_rules_on_fail: dict[str, tuple[ParsedNode | Token[Definitions], ...]]=dict()) -> None:
        """
        skip_rules_on_fail makes the parser skip the rule entirely if that rule fails.
        rule_blacklist makes the parser not evaluate the rule at all.
        """

        self.rules = build_parse_tree()
        self.tokenizer = Tokenizer(Definitions, {"Comment", "Whitespace", "Newline", "BlockComment"})
        self.furthest_error: ParseError | None = None
        self.expected = ExpectedState()
        self.rule_stack: list[str] = []
        self.skip_bad_tokens: bool = skip_bad_tokens
        self.skip_rules_on_fail = skip_rules_on_fail
        self.halt: bool = False
        # furthest place we got before failing

        # best place to recover a tree from (a terminal isn't gonna be that helpful)
        # we want to basically go back to the last valid rule we fulfilled. 
        self.deepest_partial: ParseResult | None = None

        # True if the ParseResult returned by the most recent `read()` call
        # is a best-effort recovered tree (i.e. the source had a syntax
        # error and `read()` fell back to `deepest_partial`) rather than a
        # clean, complete parse. Callers can check this -- together with
        # `fail_state`/`furthest_error` -- to still report the syntax error
        # even though `read()` itself no longer raises for recoverable
        # failures.
        self.recovered_from_error: bool = False


    def reset_expected(self):
        self.expected = ExpectedState()

    def record_expected(self, token_kind: Definitions | GenericRules, pos: int):
        self.expected.record(pos=pos,
                             definition=token_kind,
                             rule_path=tuple(self.rule_stack))

    def cancel(self):
        self.halt = True

    @property
    def expected_items(self):
        return self.expected.items


    @property
    def fail_state(self):
        if self.furthest_error is None:
            return None
        return FailState(
            self.furthest_error.node,
            self.furthest_error.tokens,
            self.furthest_error.pos - 1
        )

    @property
    def recovered_tree(self) -> ParsedNode | Token[Definitions] | None:
        """
        The best-effort parse tree covering everything that was
        successfully parsed up to (and including) the point of the syntax
        error. `None` if nothing at all could be parsed.

        This has the same node shape (Sequence/Repeat/OptionalNode/rule
        wrappers) as a normal successful parse tree, just truncated at the
        point of failure -- so it can be fed through the same
        `flat_children`-based AST builders used for a clean parse, though
        the branch containing the error will typically be missing trailing
        children (e.g. a missing closing brace).
        """
        return self.deepest_partial.tree if self.deepest_partial is not None else None

    def _consider_partial(self, result: ParseResult | None) -> None:
        if result is None:
            return

        if self.deepest_partial is None or result.pos >= self.deepest_partial.pos:
            self.deepest_partial = result
            

    def make_error(self, tokens: list[Token[Definitions]], pos: int, rule_start: int, failed_rule: Rule,
                    node: GrammarNode, previous_valid_tree: ParseResult | None=None):
        error = ParseError(tokens, pos, rule_start, failed_rule, node, previous_valid_tree)
        
        if self.furthest_error is None or pos > self.furthest_error.pos:
            self.furthest_error = error
        
        return error
    

    def parse_rule(self, rule: Rule, tokens: list[Token[Definitions]], pos: int) -> ParseResult:
        if self.halt:
            return ParseResult(ParsedNode("", ()), pos)

  
        self.rule_stack.append(rule.name)
        try:
            result = self.parse_node(rule, pos, rule.body, tokens, pos)
            debug_print(f"Rule completed: {rule.name}")
            self.rule_stack.pop()
        except ParseError as error:
            if rule.name not in self.skip_rules_on_fail:

                # On success this wraps the matched body in a ParsedNode named
                # after the rule (e.g. "ifstat", "wrap"). Do the same for a
                # partial match, so a recovered tree looks the same shape-wise
                # as a fully successful one, and downstream code (e.g.
                # find_first_node(node, "wrap")) can still recognise it.
                if error.previous_valid_tree is not None:
                    wrapped = ParseResult(
                        ParsedNode(rule.name, (error.previous_valid_tree.tree,)),
                        error.previous_valid_tree.pos,
                    )
                    error.previous_valid_tree = wrapped
                    self._consider_partial(wrapped)
                self.rule_stack.pop()
                raise error
            else:
                self.rule_stack.pop()
                return ParseResult(
                    ParsedNode(rule.name, self.skip_rules_on_fail[rule.name]),
                    error.pos
                )

        return ParseResult(ParsedNode(rule.name, (result.tree, )), result.pos)

    def parse_node(self, current_rule: Rule, start_pos: int, node: GrammarNode, tokens: list[Token[Definitions]], pos: int) -> ParseResult:
        match node:
            case Terminal(value):
                if pos < len(tokens) and value.name == tokens[pos].kind.name:
                    debug_print(f"{print_token_safe(tokens, pos)}. Matched {value.name}")
                    return ParseResult(tokens[pos], pos + 1)
                self.record_expected(node.child, pos)
                debug_print(f"{print_token_safe(tokens, pos)}. Terminal rule not matched {value.name}")
                raise self.make_error(tokens, pos, start_pos, current_rule, node)
            
            case NonTerminal(_, rule):
                if rule is None:
                    raise InvalidTreeError("Invalid tree - no linking rule")

                debug_print(f"{print_token_safe(tokens, pos)}. Trying {node}")
                return self.parse_rule(rule, tokens, pos)
            
            case Sequence(children):
                parsed_children: list[ParsedNode | Token[Definitions]] = []
                result = None
                for child in children:
                    try:
                        result = self.parse_node(current_rule, start_pos, child, tokens, pos)
                            
                        parsed_children.append(result.tree)
                        pos = result.pos
                    except ParseError as error:      
                        partial = error.previous_valid_tree

                        if partial is not None:
                            partial_result = ParseResult(
                                ParsedNode(
                                    Sequence.__name__,
                                    (
                                        *parsed_children,
                                        partial.tree
                                    )
                                ),
                                partial.pos
                            )

                            self._consider_partial(partial_result)
                            error.previous_valid_tree = partial_result
                        else:
                            partial_result = ParseResult(
                                ParsedNode(
                                    Sequence.__name__,
                                    (
                                        *parsed_children,
                                    )
                                ),
                                pos
                            )
                            self._consider_partial(partial_result)

                        # partial_children = parsed_children.copy()

                        # if error.previous_valid_tree is not None:
                        #     partial_children.append(
                        #         error.previous_valid_tree.tree
                        #     )

                        # partial_result = ParseResult(
                        #     ParsedNode(
                        #         Sequence.__name__,
                        #         tuple(partial_children)
                        #     ),
                        #     error.previous_valid_tree.pos
                        #     if error.previous_valid_tree is not None
                        #     else pos
                        # )

                        # error.previous_valid_tree = partial_result
                        # self._consider_partial(partial_result)

                        debug_print(f"{print_token_safe(tokens, pos)}. Sequence broken {node}.")
                        # propagate the error upwards
                        raise self.make_error(tokens, pos, start_pos, current_rule, node, partial_result)
                    
                
                if result is None:
                    raise AssertionError("Invalid tree - empty sequence")
                
                debug_print(f"{print_token_safe(tokens, pos)}. Matched {node}")
                return ParseResult(
                    ParsedNode(Sequence.__name__, tuple(parsed_children)), pos
                )

            case Alternative(options):
                best_error: ParseError | None = None
                # best_progress = -1

                for option in options:
                    try:
                        result = self.parse_node(current_rule, start_pos, option, tokens, pos)
                        debug_print(f"{print_token_safe(tokens, pos)}. Matched {node}")
                        return ParseResult(
                            ParsedNode(Alternative.__name__, (result.tree,)),
                            result.pos
                        )
                    except ParseError as error:
                        partial = error.previous_valid_tree

                        # progress = partial.pos if partial is not None else error.pos

                        if best_error is None or error.pos > best_error.pos:
                            best_error = error
                            # best_progress = progress
                        self.make_error(tokens, start_pos, pos, current_rule, node)
                debug_print(f"Nothing matched {node}. {print_token_safe(tokens, pos)}")
                assert best_error is not None

                partial = best_error.previous_valid_tree

                if partial is not None:
                    partial_result = ParseResult(
                        ParsedNode(
                            Alternative.__name__,
                            (partial.tree,)
                        ),
                        partial.pos
                    )

                    best_error.previous_valid_tree = partial_result
                    self._consider_partial(partial_result)

                raise best_error
        
            case OptionalNode(child):
                start_pos = pos
                try:
                    result = self.parse_node(current_rule, start_pos, child, tokens, pos)
                    debug_print(f"{print_token_safe(tokens, pos)}. Matched {node}")
                    return ParseResult(
                        ParsedNode(
                            OptionalNode.__name__, (result.tree,)
                        ),
                        result.pos
                    )
                except ParseError as error:
                    if error.pos == start_pos:
                        return ParseResult(
                            ParsedNode(
                                OptionalNode.__name__,
                                (),
                            ),
                            start_pos
                        )

                    if error.previous_valid_tree is not None:
                        error.previous_valid_tree = ParseResult(
                            ParsedNode(
                                OptionalNode.__name__,
                                (error.previous_valid_tree.tree,)
                            ),
                            error.previous_valid_tree.pos
                        )
                        # We're about to backtrack this optional away (treat
                        # it as if it never matched), even though it made
                        # real progress before failing. Register that
                        # progress with the recovery tracker before it's
                        # lost, since it's still the best information we
                        # have about what the writer was trying to express
                        # at this point in the source.
                        self._consider_partial(error.previous_valid_tree)

                    self.make_error(tokens, pos, start_pos, current_rule, node)
                    debug_print(f"{print_token_safe(tokens, pos)}. Skipping {node}")
                    return ParseResult(
                        ParsedNode(
                            OptionalNode.__name__, ()
                        ),
                        pos
                    )
                
            case Repeat(child):
                parsed_children: list[ParsedNode | Token[Definitions]] = []  # type: ignore[no-redef]

                while True:
                    attempt_pos = pos
                    try:
                        result = self.parse_node(current_rule, start_pos, child, tokens, pos)
                    except ParseError as error:
                        """
                        dying here could mean one of two things:

                        1. repetition is supposed to stop here (i.e. it's the end of a recursive rule)
                        2. repetition is half finished and stopping here is a syntax error (does not match any rules later)

                        if 1 occured, that's okay.

                        if 2 occured, we want the tree to not be discarded.
                        """
                        if (
                            error.previous_valid_tree is not None 
                            and error.previous_valid_tree.pos > attempt_pos
                        ):
                            partial = error.previous_valid_tree
                            recovered_repeat = ParseResult(
                                ParsedNode(
                                    Repeat.__name__,
                                    (
                                        *parsed_children,
                                        partial.tree,
                                    ),
                                ),
                                partial.pos,
                            )

                            self._consider_partial(recovered_repeat)

                        debug_print(f"{print_token_safe(tokens, pos)}. Skipping {node}")
                        break
                            

                    if result.pos == pos:
                        break

                    parsed_children.append(result.tree)

                    pos = result.pos

                return ParseResult(
                    ParsedNode(Repeat.__name__, tuple(parsed_children)),
                    pos
                )
            
            case GrammarNode():
                raise TypeError("Reached bare GrammarNode")


    def build_ast(self) -> None:
        raise NotImplementedError()


    def parse(self, root: Rule, tokens: list[Token[Definitions]]) -> ParseResult:
        self.furthest_error = None
        self.deepest_partial = None
        self.reset_expected()
        self.rule_stack.clear()

        result = self.parse_rule(root, tokens, 0)
        return result


    def read(self, text: str) -> ParseResult:
        # Reset per-parse state so a Parser instance can be reused across
        # multiple `read()` calls without leaking stale error/recovery info
        # from a previous file into the next one.
        self.halt = False
        self.recovered_from_error = False
        root = get_root_node(self.rules)
        tokens = list(self.tokenizer.read(text))

        if not self.skip_bad_tokens:
            return self.parse(root, tokens)
        else:
            """
            We slowly remove characters starting from the error location to the start of the rule until things work.
            """
            progress = None
            offset_tries = 8
            max_tries = offset_tries * 2
            tries = 0
            shift = 0
            offset = 1
            working_tokens = tokens.copy()
  

            while True:
                try:
                    result = self.parse(root, working_tokens)
                    print(len(working_tokens), shift)
                    return result
                except ParseError as e:
                    if self.halt:
                        raise
                    if len(tokens) == 0:
                        raise

                    error = self.furthest_error or e

                    error_pos = min(len(error.tokens) - 1, error.pos)
                    
                    if progress is None:
                        progress = error_pos

                    can_delete = len(error.tokens) > 0
                    line = 0
                    # we made more progress, try to delete current line.
                    
                    if error_pos > progress:
                        progress = error_pos
                        tokens = working_tokens.copy()
                        if can_delete:
                            line = error.tokens[error_pos].line - offset
                        shift = 0
                        tries = 0
                    else:
                        # delete the line above until something works.
                        if can_delete:
                            line = (error.tokens[error_pos].line - offset) + shift
                        shift -= 1

                    if can_delete:
                        working_tokens = [i for i in working_tokens if i.line != line]

                    # bad hack :(
                    # the error reporter sometimes reports the incorrect line by 1, so we need to test
                    # offsets both 1 and 0.
                    if tries == offset_tries:
                        shift = 0
                        offset = 0
                        working_tokens = tokens.copy()

                    if tries > max_tries:
                        raise
                tries += 1
            