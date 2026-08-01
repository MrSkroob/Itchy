from __future__ import annotations
from dataclasses import dataclass, field
from itchy.tokenizer import Definitions, GenericRules, Tokenizer, Token
from itchy.tree import Rule, Terminal, NonTerminal, Alternative, OptionalNode, Repeat, Sequence, GrammarNode, build_parse_tree, get_root_node


DEBUG = False

@dataclass(frozen=True)
class ExpectedToken:
    definition: Definitions | GenericRules
    path: tuple[str, ...]


@dataclass()
class ExpectedState:
    pos: int = -1
    paths: set[ExpectedToken] = field(default_factory=set[ExpectedToken])

    def record(self, pos: int, definition: Definitions | GenericRules, rule_path: tuple[str, ...]) -> None:
        expectation = ExpectedToken(definition, rule_path)

        if pos > self.pos:
            self.pos = pos
            self.items = {expectation}
        else:
            self.items.add(expectation)


@dataclass(frozen=True)
class ParsedNode():
    name: str
    children: tuple[ParsedNode | Token[Definitions], ...]

    def __repr__(self) -> str:
        output: list[str] = []

        for i in self.children:
            output.append(str(i))

        return f"[{', '.join(output)}]"


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
    def __init__(self, tokens: list[Token[Definitions]], pos: int, node: GrammarNode, previous_valid_tree: ParseResult | None=None) -> None:
        self.tokens = tokens
        self.pos = pos
        self.node = node
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
    return tokens[min(pos, len(tokens) - 1)].literal


class ASTNode():
    node_type: str


class Parser:
    def __init__(self) -> None:
        self.rules = build_parse_tree()
        self.tokenizer = Tokenizer(Definitions, {"Comment", "Whitespace", "Newline"})
        self.furthest_error: ParseError | None = None
        self.expected = ExpectedState()
        self.rule_stack: list[str] = [] 
        # furthest place we got before failing

        # best place to recover a tree from (a terminal isn't gonna be that helpful)
        # we want to basically go back to the last valid rule we fulfilled. 
        self.deepest_partial: ParseResult | None = None


    def reset_expected(self):
        self.expected = ExpectedState()

    def record_expected(self, token_kind: Definitions | GenericRules, pos: int):
        self.expected.record(pos=pos,
                             definition=token_kind,
                             rule_path=tuple(self.rule_stack))


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

    def make_error(self, tokens: list[Token[Definitions]], pos: int, node: GrammarNode, previous_valid_tree: ParseResult | None=None):
        error = ParseError(tokens, pos, node, previous_valid_tree)
        
        if self.furthest_error is None or pos > self.furthest_error.pos:
            self.furthest_error = error
        
        return error

    def parse_rule(self, rule: Rule, tokens: list[Token[Definitions]], pos: int) -> ParseResult:
        self.rule_stack.append(rule.name)
        try:
            result = self.parse_node(rule.body, tokens, pos)
            self.rule_stack.pop()
        except ParseError as error:
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

        return ParseResult(ParsedNode(rule.name, (result.tree, )), result.pos)

    def parse_node(self, node: GrammarNode, tokens: list[Token[Definitions]], pos: int) -> ParseResult:
        match node:
            case Terminal(value):
                if pos < len(tokens) and value.name == tokens[pos].kind.name:
                    debug_print(f"{print_token_safe(tokens, pos)}. Matched {value.name}")
                    return ParseResult(tokens[pos], pos + 1)
                self.record_expected(node.child, pos)
                debug_print(f"{print_token_safe(tokens, pos)}. Terminal rule not matched {value.name}")
                raise self.make_error(tokens, pos, node)
            
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
                        result = self.parse_node(child, tokens, pos)
                            
                        parsed_children.append(result.tree)
                        pos = result.pos
                    except ParseError as error:
                        if isinstance(child, Terminal):
                            self.record_expected(child.child, pos)
                        
                        partial_children = parsed_children.copy()

                        if error.previous_valid_tree is not None:
                            partial_children.append(
                                error.previous_valid_tree.tree
                            )
                            

                        partial_result = ParseResult(
                            ParsedNode(
                                Sequence.__name__,
                                tuple(partial_children)
                            ),
                            error.previous_valid_tree.pos
                            if error.previous_valid_tree is not None
                            else pos
                        )

                        error.previous_valid_tree = partial_result
                        self._consider_partial(partial_result)

                        debug_print(f"{print_token_safe(tokens, pos)}. Sequence broken {node}.")
                        # propagate the error upwards
                        raise self.make_error(tokens, pos, node, partial_result)
                    
                
                if result is None:
                    raise AssertionError("Invalid tree - empty sequence")
                
                debug_print(f"{print_token_safe(tokens, pos)}. Matched {node}")
                return ParseResult(
                    ParsedNode(Sequence.__name__, tuple(parsed_children)), pos
                )

            case Alternative(options):
                best_error: ParseError | None = None
                parse_result = None

                for option in options:
                    try:
                        result = self.parse_node(option, tokens, pos)

                        # test all options so we have more options
                        if parse_result is None:
                            debug_print(f"{print_token_safe(tokens, pos)}. Matched {node}")
                            parse_result = result

                    except ParseError as error:
                        if isinstance(option, Terminal):
                            self.record_expected(option.child, pos)
                            
                        if best_error is None or error.pos > best_error.pos:
                            best_error = error
                        self.make_error(tokens, pos, node)

                if parse_result is not None:
                    return parse_result
                
                debug_print(f"Nothing matched {node}. {print_token_safe(tokens, pos)}")
                assert best_error is not None
                raise best_error
        
            case OptionalNode(child):
                start_pos = pos
                try:
                    result = self.parse_node(child, tokens, pos)
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

                    # self.make_error(tokens, pos, node)
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
                    try:
                        result = self.parse_node(child, tokens, pos)
                    except ParseError as error:
                        partial_result = ParseResult(
                            ParsedNode(
                                Repeat.__name__,
                                tuple(parsed_children),
                            ),
                            pos
                        )

                        if error.previous_valid_tree is None:
                            error.previous_valid_tree = partial_result

                        # The failed final attempt is about to be dropped on
                        # the floor (Repeat always "succeeds" with whatever
                        # it matched so far). For recovery purposes, also
                        # register a combined view -- everything matched by
                        # earlier repetitions *plus* the partially-parsed
                        # failing one -- so an error deep inside e.g. the
                        # third statement of a block doesn't lose the first
                        # two statements from the recovered fragment.
                        combined = ParseResult(
                            ParsedNode(
                                Repeat.__name__,
                                tuple(parsed_children) + (error.previous_valid_tree.tree,),
                            ),
                            error.previous_valid_tree.pos,
                        )
                        self._consider_partial(partial_result)
                        self._consider_partial(combined)

                        # self.make_error(tokens, pos, node, partial_result)
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


    def read(self, text: str) -> ParseResult:
        # Reset per-parse state so a Parser instance can be reused across
        # multiple `read()` calls without leaking stale error/recovery info
        # from a previous file into the next one.
        self.furthest_error = None
        self.deepest_partial = None
        self.reset_expected()
        self.rule_stack.clear()

        root = get_root_node(self.rules)
        tokens = list(self.tokenizer.read(text))
        result = self.parse_rule(root, tokens, 0)

        return result