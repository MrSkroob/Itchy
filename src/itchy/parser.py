from __future__ import annotations
from dataclasses import dataclass, field
from itchy.tokenizer import Definitions, GenericRules, Tokenizer, Token
from itchy.tree import Rule, Terminal, NonTerminal, Alternative, \
    OptionalNode, Repeat, Sequence, GrammarNode, ParsedNode, build_parse_tree, get_root_node

from itchy.dummy_nodes import Strategy, find_first_node, extend_node


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


class ParseError(Exception):
    def __init__(self, tokens: list[Token[Definitions]], pos: int, rule_start: int, failed_rule: Rule, node: GrammarNode, expected: ExpectedState,
                 previous_valid_tree: ParseResult | None=None) -> None:
        self.tokens = tokens
        self.pos = pos
        self.node = node
        self.rule = failed_rule
        self.rule_start = rule_start
        self.expected = expected
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


BRACKET_PAIRS: dict[str, str] = {
    "{": "}",
    "(": ")",
    "[": "]"
}


class Parser:
    def __init__(self, *, skip_bad_tokens: bool=False, skip_rules_on_fail: Strategy=dict(), 
                 recoverable_rules: set[str] | None=None) -> None:
        """
        skip_rules_on_fail makes the parser skip the rule entirely if that rule fails.
        
        Please do not set rules that appear as mandatory in `recoverable_rules`. For example,
        <chunk> might seem okay, but in <program> it is not wrapped with `[]` or `{}`, so it's considered mandatory.

        Modify `terminators` to whatever your language requires. In Itchy's case, `}`, `]`, `;`, `)` are considered decent terminators. 
        """

        self.rules = build_parse_tree()
        self.tokenizer = Tokenizer(Definitions, {"Comment", "Whitespace", "Newline", "BlockComment"})
        self.furthest_error: ParseError | None = None

        self.expected = ExpectedState()
        self.rule_stack: list[str] = []
        self.alt_memo: dict[tuple[str, int], ParseResult] = {} # alternative might back track multiple times.
        self.skip_bad_tokens: bool = skip_bad_tokens
        self.skip_rules_on_fail = skip_rules_on_fail
        self.recoverable_rules = recoverable_rules or set()
        # self.terminators = {"}", ";", ")", "]"}
        # self.opening_terminators = {"{", "(", "["}
        self.unclosed_terminators: list[str] = []
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
        self.accumulated_errors: list[ParseError] = []
        self.speculative_errors: dict[int, ParseError] = {}


    def reset_expected(self):
        self.expected = ExpectedState()

    def record_expected(self, token_kind: Definitions | GenericRules, pos: int):
        self.expected.record(pos=pos,
                             definition=token_kind,
                             rule_path=tuple(self.rule_stack))

    def cancel(self):
        self.halt = True

    def can_recover_repeat(self, current_rule: Rule) -> bool:
        return current_rule.name in self.recoverable_rules

    def advance(self, initial_pos: int, error: ParseError, tokens: list[Token[Definitions]]):
        recovery_start = max(
            initial_pos,
            min(error.pos, len(tokens)),
        )

        new_pos = self._skip_block(
            tokens,
            recovery_start,
        )

        # Guarantee forward progress for an otherwise unrecoverable
        # garbage token.
        if new_pos <= initial_pos:
            if new_pos < len(tokens) and tokens[new_pos].literal in BRACKET_PAIRS.values():
                return

            new_pos = min(new_pos + 1, len(tokens))

        # okay, if we're still not advancing we're probably at EOF.
        if new_pos == initial_pos:
            return

        return new_pos

    def _skip_block(self, tokens: list[Token[Definitions]], pos: int):
        if pos >= len(tokens):
            return pos

        # line = tokens[pos].line
        i = pos

        while i < len(tokens):
            token = tokens[i]

            # skip the entire hecking block!
            if token.literal in BRACKET_PAIRS.values():
                return min(i + 1, len(tokens) - 1)

            # if token.line > line:
            #     return i

            i += 1

        return i

    @property
    def expected_items(self):
        return self.expected.items

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
            

    def make_error(self, tokens: list[Token[Definitions]], pos: int, rule_start: int, failed_rule: Rule, node: GrammarNode, previous_valid_tree: ParseResult | None=None):
        is_new_furthest = self.furthest_error is None or pos > self.furthest_error.pos
        if is_new_furthest:
            expected_snapshot = ExpectedState(0, self.expected.items.copy())
        else:
            assert self.furthest_error is not None
            expected_snapshot = self.furthest_error.expected
        error = ParseError(tokens, pos, rule_start, failed_rule, node, expected_snapshot, previous_valid_tree)
        if is_new_furthest:
            self.furthest_error = error
        assert self.furthest_error is not None
        if pos in self.speculative_errors:
            self.speculative_errors[pos] = self.furthest_error
        return error
    

    def parse_rule(self, rule: Rule, tokens: list[Token[Definitions]], pos: int, *, allow_recovery: bool=False) -> ParseResult:
        if self.halt:
            raise InterruptedError()

  
        self.rule_stack.append(rule.name)
        try:
            result = self.parse_node(rule, pos, rule.body, tokens, pos, allow_recovery=allow_recovery)
            # debug_print(f"Rule completed: {rule.name}")
            self.rule_stack.pop()
        except ParseError as error:
            if (rule.name not in self.skip_rules_on_fail) or not allow_recovery:

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
                # this error might be raised in syntactically correct code. 
                self.rule_stack.pop()
                if 0 <= pos < len(tokens):
                    self.speculative_errors[pos] = error
                    return ParseResult(
                        ParsedNode(rule.name, self.skip_rules_on_fail[rule.name](tokens[pos].line, tokens[pos].char)),
                        error.pos
                    )
                raise

                

        return ParseResult(ParsedNode(rule.name, (result.tree, )), result.pos)

    def parse_node(self, current_rule: Rule, start_pos: int, node: GrammarNode, tokens: list[Token[Definitions]], pos: int, *,
                   allow_recovery: bool=False) -> ParseResult:
        match node:
            case Terminal(value):
                if pos < len(tokens) and value.name == tokens[pos].kind.name \
                    and (node.literal and node.literal == tokens[pos].literal or not node.literal):
                    # debug_print(f"{print_token_safe(tokens, pos)}. Matched {value.name}")

                    if pos in self.speculative_errors and not tokens[pos].dummy_token and allow_recovery:
                        del self.speculative_errors[pos]

                    return ParseResult(tokens[pos], pos + 1)

                self.record_expected(node.child, pos)
                # debug_print(f"{print_token_safe(tokens, pos)}. Terminal rule not matched {value.name}")

                error = self.make_error(tokens, pos, start_pos, current_rule, node)

                # if value.name in self.skip_rules_on_fail and tokens[pos].kind != GenericRules.EOF:
                #     self.accumulated_errors.append(error)
                #     return ParseResult(self.skip_rules_on_fail[value.name]()[0], pos + 1)

                raise error
            
            case NonTerminal(_, rule):
                if rule is None:
                    raise InvalidTreeError("Invalid tree - no linking rule")

                # debug_print(f"{print_token_safe(tokens, pos)}. Trying {node}")
                return self.parse_rule(rule, tokens, pos, allow_recovery=allow_recovery)
            
            case Sequence(children):
                parsed_children: list[ParsedNode | Token[Definitions]] = []
                result = None
                for child in children:
                    try:
                        result = self.parse_node(current_rule, start_pos, child, tokens, pos, allow_recovery=allow_recovery)
                            
                        parsed_children.append(result.tree)
                        pos = result.pos
                    except ParseError as e:      
                        partial = e.previous_valid_tree

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
                            e.previous_valid_tree = partial_result
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

                        error = self.make_error(tokens, pos, start_pos, current_rule, node, partial_result)
                        if not self.skip_bad_tokens:
                            # debug_print(f"{print_token_safe(tokens, pos)}. Sequence broken {node}.")
                            raise error
                        if not isinstance(e.node, Terminal):
                            # debug_print(f"{print_token_safe(tokens, pos)}. Sequence broken {node}.")
                            raise error

                        # print("DAG NABBIT", e.node.child.name)

                        recovery_token = self.skip_rules_on_fail.get(e.node.child.name)
                        if not recovery_token:
                            raise error

                        print("recovered")
                        
                        self.accumulated_errors.append(error)
                        parsed_children.append(recovery_token()[0])
                        # raise error
                
                if result is None:
                    raise AssertionError("Invalid tree - empty sequence")
                
                # debug_print(f"{print_token_safe(tokens, pos)}. Matched {node}")
                return ParseResult(
                    ParsedNode(Sequence.__name__, tuple(parsed_children)), pos
                )

            case Alternative(options):
                best_error: ParseError | None = None

                for option in options:
                    try:
                        key = (current_rule.name, pos)
                        if not allow_recovery:
                            cached = self.alt_memo.get(key)
                            if cached is not None:
                                return cached
                            
                        result = self.parse_node(current_rule, start_pos, option, tokens, pos, allow_recovery=allow_recovery)
                        # debug_print(f"{print_token_safe(tokens, pos)}. Matched {node}")

                        if not allow_recovery:
                            self.alt_memo[key] = result
                            
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

                # debug_print(f"Nothing matched {node}. {print_token_safe(tokens, pos)}")
                assert best_error is not None

                # we want to first check if any of these 'alternative rules' actually advanced. 
                # if they did, then they are likely something that we can recover

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

                # if self.skip_bad_tokens and self.can_recover_repeat(current_rule):
                #     if new_pos := self.advance(pos, best_error, tokens):
                #         self.accumulated_errors.append(best_error)
                #         return self.parse_node(current_rule, pos, node, tokens, new_pos, allow_recovery=allow_recovery)

                raise best_error
        
            case OptionalNode(child):
                start_pos = pos
                try:
                    result = self.parse_node(current_rule, start_pos, child, tokens, pos, allow_recovery=False)
                    # debug_print(f"{print_token_safe(tokens, pos)}. Matched {node}")
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
                    # debug_print(f"{print_token_safe(tokens, pos)}. Skipping {node}")
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
                        result = self.parse_node(current_rule, start_pos, child, tokens, pos, allow_recovery=False)
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

                        # debug_print(f"{print_token_safe(tokens, pos)}. Skipping {node}")

                        # if not allow_recovery:
                        #     break

                        # # chunks, statements, etc. do not try to recover from rules that
                        # # would cause the parser to get stuck in a forever loop.
                        # if not self.can_recover_repeat(current_rule):
                        #     break

                        # if attempt_pos < len(tokens)\
                        #     and tokens[attempt_pos].literal in BRACKET_PAIRS.values():
                        #     break

                        # self.accumulated_errors.append(error)

                        # if new_pos := self.advance(attempt_pos, error, tokens):
                        #     pos = new_pos
                        #     continue

                        break
                        # recovery_start = max(
                        #     attempt_pos,
                        #     min(error.pos, len(tokens)),
                        # )

                        # new_pos = self._recover_to_next_line(
                        #     tokens,
                        #     recovery_start,
                        # )

                        # # Guarantee forward progress for an otherwise unrecoverable
                        # # garbage token.
                        # if new_pos <= attempt_pos:
                        #     if new_pos < len(tokens) and tokens[new_pos].literal == "}":
                        #         break

                        #     new_pos = min(attempt_pos + 1, len(tokens))

                        # pos = new_pos
                            

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
        result = None
        pos = 0
        extra_children: list[ParsedNode | Token[Definitions]] = []
        while result is None:
            try:
                self.reset_expected()
                self.rule_stack.clear()
                self.furthest_error = None
                self.deepest_partial = None
                self.speculative_errors = {}
                result = self.parse_rule(root, tokens, pos, allow_recovery=self.skip_bad_tokens)
            except ParseError as e:
                self.accumulated_errors.append(self.furthest_error or e)
                if self.skip_bad_tokens:
                    if self.recovered_tree:
                        if e.previous_valid_tree:
                            assert isinstance(e.previous_valid_tree.tree, ParsedNode)
                            chunk_node = find_first_node(e.previous_valid_tree.tree, "chunk")
                            if chunk_node is not None:
                                extra_children.extend(chunk_node.children)
                                
                    pos = min(e.pos + 1, len(tokens))
                    pos = self.advance(pos, e, tokens)
                    if not pos:
                        raise
                else:
                    raise

        if pos == 0:
            # print(len(result.tree.children[0].children))
            return result

        result_children = result.tree
        assert isinstance(result_children, ParsedNode)

        new_node = extend_node(result_children, "chunk", tuple(extra_children))

        return ParseResult(tree=new_node, pos=0)
        


    def read(self, text: str) -> ParseResult:
        # Reset per-parse state so a Parser instance can be reused across
        # multiple `read()` calls without leaking stale error/recovery info
        # from a previous file into the next one.

        self.accumulated_errors = []
        self.alt_memo = {}

        self.halt = False
        self.recovered_from_error = False
        root = get_root_node(self.rules)
        tokens = list(self.tokenizer.read(text))

        # if not self.skip_bad_tokens:
        return self.parse(root, tokens)