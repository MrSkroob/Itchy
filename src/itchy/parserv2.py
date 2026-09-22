from dataclasses import dataclass, field
from typing import cast

from itchy.dummy_nodes import Strategy
from itchy.tokenizer import Token, Definitions, GenericRules, Tokenizer
from itchy.tree import Rule, ParsedNode, GrammarNode, get_root_node, build_parse_tree, \
    Alternative, Sequence, OptionalNode, Repeat, NonTerminal, Terminal


TokenList = list[Token[Definitions]]


tokenizer = Tokenizer(Definitions, {"Comment", "Whitespace", "Newline", "BlockComment"})

# TODO: i think partial trees are getting absorbed in parse_non_terminal...

@dataclass(frozen=True)
class ExpectedToken:
    definition: Definitions | GenericRules
    path: tuple[str, ...]

    def __repr__(self) -> str:
        return str(self.definition)


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

    def return_copy(self):
        return ExpectedState(self.pos, self.items.copy())

    def create_and_reset(self):
        new_state = ExpectedState(self.pos, self.items)
        self.items = set()
        self.pos = -1
        return new_state


@dataclass(kw_only=True)
class PartialParse():
    rule: Rule
    tree: ParsedNode | Token[Definitions]
    start_pos: int
    pos: int


@dataclass(kw_only=True)
class ParseResult():
    # used for AST
    tree: ParsedNode | Token[Definitions]
    tokens: list[Token[Definitions]]

    # used for error recovery
    node: GrammarNode
    parent_node: GrammarNode | None=None
    child_index: int | None=None # used for determining error node location 

    start_pos: int
    pos: int
    failed: bool
    expected: ExpectedState=field(default_factory=ExpectedState)
    failure_cause: ParseResult | None=None
    # incomplete_parse: PartialParse | None=None


    def partial_tree_rule(self, rule: str) -> ParseResult | None:
        if isinstance(self.node, NonTerminal):
            return self

        if self.failure_cause is None:
            return

        return self.failure_cause.partial_tree_rule(rule)

    @property
    def partial_tree(self) -> ParsedNode | Token[Definitions]:
        """
        Returns a partially-complete tree. You can use this alongside a non-strict AST-Builder to
        get some useful information about what the developer was trying to write. 
        """
        if self.failure_cause is None:
            return self.tree

        if isinstance(self.tree, ParsedNode):
            # if len(self.tree.children) == 1:
            #     return ParsedNode(
            #         self.tree.name,
            #         (self.failure_cause.partial_tree,)
            #     )
            if isinstance(self.node, Sequence):
                return ParsedNode(
                    self.tree.name, 
                    self.tree.children + (
                        self.failure_cause.partial_tree,
                    )
                )
            else:
                return ParsedNode(
                    self.tree.name,
                    (self.failure_cause.partial_tree,)
                )
        else:
            return self.tree

    @property
    def progress_made(self) -> int:
        return self.pos - self.start_pos

    @property
    def deepest(self) -> "ParseResult":
        result = self

        while result.failure_cause is not None:
            result = result.failure_cause

        return result

    def __repr__(self) -> str:
        return str(self.node) + ": " + str(self.tree)

class Parser():
    def __init__(self, allow_recovery: bool=False, 
                       allow_insertions: bool=False,
                       stat_rule: str="stat",
                       recovery_rules: dict[str, set[str]] | None=None, 
                       statement_separator: str=";",
                       recovery_nodes: Strategy | None=None) -> None:
        self.allow_recovery = allow_recovery
        self.allow_insertions = allow_insertions
        self.recovery_nodes = recovery_nodes or {}
        self.recovery_rules = recovery_rules or {
            "stat": {"}"},
            "chunk": {"}"},
            "vardefstat": {";"}
        }
        self.statement_separator = statement_separator
        self.rules=build_parse_tree()

        for rule in self.rules:
            if rule.name == stat_rule:
                self.stat_rule = rule

        if not self.stat_rule:
            raise NameError(f"no stat rule with the name {stat_rule}")
    
        self.pos: int = 0
        self.rule_stack: list[str] = []
        self.accumulated_errors: list[ParseResult] = []
        self.expected = ExpectedState()

    def get_recovery_target(self, result: ParseResult) -> ParseResult | None:
        """
        Returns the deepest rule to recover from (i.e. the root cause that's not the Terminal)
        """
        current: ParseResult | None = result
        target: ParseResult | None = None

        while current is not None:
            if isinstance(current.node, NonTerminal):
                rule = cast(Rule, current.node.rule)

                if rule.name in self.recovery_rules and (current.progress_made > 0 or target is None):
                    target = current

            current = current.failure_cause

        return target

    def matches_terminal(self, token: Token[Definitions], node: Terminal):
        return (
            node.child.name == token.kind.name
            and node.literal is None 
            or node.literal == token.literal
        )

    def in_first(self, token: Token[Definitions], rule: Rule):
        for terminal in rule.first:
            if self.matches_terminal(token, terminal):
                return True
        return False

    def _skip_statement(self, tokens: TokenList, recovery_chars: set[str] | None=None):
        while self.pos < len(tokens):
            token = tokens[self.pos]

            if not recovery_chars:
                if self.in_first(token, self.stat_rule):
                    return
            else:
                if token.literal in recovery_chars:
                    # print("skipped to", token.literal)
                    return

            self.pos += 1

    def recover(self, result: ParseResult, tokens: TokenList):
        result.expected = self.expected.create_and_reset()
        self.accumulated_errors.append(result)
        target = self.get_recovery_target(result)

        if target is None:
            # print("WHAT ARE WE TRYING TO RECOVER?!??!?!!?!?")
            return

        # if target.failure_cause and isinstance(target.failure_cause.node, NonTerminal):
        #     rule = cast(Rule, target.failure_cause.node.rule)
        # else:
        rule = cast(Rule, cast(NonTerminal, target.node).rule)
        recovery_chars = self.recovery_rules[rule.name]

        self.pos = target.pos

        # advance to next statement
        next_sequence: Sequence | None = None
        sequence_target = target
        while True:
            if isinstance(sequence_target.node, Sequence):
                next_sequence = sequence_target.node
                break
            if not sequence_target.failure_cause:
                break

            sequence_target = sequence_target.failure_cause

        skip_entire_statement = False
        if next_sequence and sequence_target.child_index:
            skip_entire_statement = sequence_target.child_index < len(next_sequence.children) - 1

        self._skip_statement(tokens, recovery_chars if skip_entire_statement else None)
        

        # we reached EOF. this is truly a bruh moment.
        if self.pos >= len(tokens):
            # print("THERE'S NO MORE INFORMATION TO WORK WITH ASSHOLE!!!")
            # return None
            self.pos = len(tokens) - 1

        if tokens[self.pos].literal in recovery_chars:
            self.pos += 1

        if self.pos < len(tokens):
            if not self.in_first(tokens[self.pos], self.stat_rule):
                self._skip_statement(tokens, recovery_chars)
                self.pos += 1

        return ParseResult(
            tree=ParsedNode(
                name=rule.name,
                children=(),
                dummy_node=True,
            ),
            tokens=tokens,
            node=target.node,
            parent_node=target.parent_node,
            start_pos=target.start_pos,
            pos=self.pos,
            failed=False
        )
    
    def parse_alternative(self, node: Alternative, 
                                tokens: TokenList, 
                                parent_node: GrammarNode | None=None) -> ParseResult:
        start_pos: int = self.pos
        furthest_progress = self.pos
        furthest_result = None
        for option in node.options:
            self.pos = start_pos
            result = self.parse_node(option, tokens, node)

            if furthest_result is None or result.pos > furthest_progress:
                furthest_progress = result.pos
                furthest_result = result

            if not result.failed:
                return ParseResult(
                    tree=ParsedNode(
                        name=Alternative.__name__,
                        children=(result.tree,)
                    ),
                    node=node,
                    tokens=tokens,
                    parent_node=parent_node,
                    start_pos=start_pos,
                    pos=result.pos,
                    failed=False
                )


        self.pos = start_pos

        furthest_result = cast(ParseResult, furthest_result)

        return ParseResult(
            tree=ParsedNode(
                name=Alternative.__name__,
                children=(furthest_result.tree,)
            ),
            node=node,
            tokens=tokens,
            parent_node=parent_node,
            start_pos=start_pos,
            pos=furthest_result.pos,
            expected=self.expected.return_copy(),
            failed=True,
            failure_cause=furthest_result
        )

    def parse_sequence(self, node: Sequence, 
                             tokens: TokenList, 
                             parent_node: GrammarNode | None):
        start_pos = self.pos
        children: list[ParsedNode | Token[Definitions]] = []
        for index, part in enumerate(node.children):
            result = self.parse_node(part, tokens, node)
            if result.failed:
                recovered = False
                if self.allow_insertions:
                    if isinstance(part, Terminal):
                        if part.child.name in self.recovery_nodes:
                            result.expected = self.expected.return_copy()
                            self.accumulated_errors.append(result)
                            children.extend(self.recovery_nodes[part.child.name]())
                            continue                        

                if not recovered:
                    self.pos = start_pos
                    return ParseResult(
                        tree=ParsedNode(
                            Sequence.__name__, 
                            tuple(children)
                        ),
                        tokens=tokens,
                        node=node,
                        parent_node=parent_node,
                        child_index=index,
                        start_pos=start_pos,
                        pos=result.pos,
                        expected=self.expected.return_copy(),
                        failed=True,
                        failure_cause=result,
                    )

            children.append(result.tree)

        self.expected.create_and_reset()
        return ParseResult(
            tree=ParsedNode(
                Sequence.__name__,
                tuple(children)
            ),
            node=node,
            parent_node=parent_node,
            tokens=tokens,
            start_pos=start_pos,
            pos=self.pos,
            failed=False
        )

    def parse_optional(self, node: OptionalNode, 
                             tokens: TokenList, 
                             parent_node: GrammarNode | None=None):
        start_pos = self.pos

        result = self.parse_node(node.child, tokens, parent_node=node)

        if result.failed:
            self.pos = start_pos

            if result.pos == start_pos:
                return ParseResult(
                    tree=ParsedNode(
                        OptionalNode.__name__,
                        children=()
                    ),
                    node=node,
                    parent_node=parent_node,
                    start_pos=start_pos,
                    tokens=tokens,
                    pos=start_pos,
                    failed=False
                )
            
            return ParseResult(
                tree=ParsedNode(
                    OptionalNode.__name__,
                    children=(result.tree,)
                ),
                node=node,
                parent_node=parent_node,
                tokens=tokens,
                start_pos=start_pos,
                pos=self.pos,
                expected=self.expected.return_copy(),
                failed=True,
                failure_cause=result
            )

        return ParseResult(
            tree=ParsedNode(
                OptionalNode.__name__,
                (result.tree,),
            ),
            node=node,
            tokens=tokens,
            parent_node=parent_node,
            start_pos=start_pos,
            pos=self.pos,
            failed=False
        )

    def parse_repeat(self, node: Repeat, 
                           tokens: TokenList, 
                           parent_node: GrammarNode | None=None):
        start_pos = self.pos
        children: list[ParsedNode | Token[Definitions]] = []

        while True:
            iter_start = self.pos
            result = self.parse_node(node.child, tokens, node)
            if result.failed:
                # not a failure, we genuinely terminated here.
                if result.pos == iter_start:
                    self.pos = iter_start
                    break

                # we matched into something then failed. 
                # not good!

                if self.allow_recovery:
                    recovered = self.recover(result, tokens)

                    if recovered is not None:
                        children.append(recovered.tree)

                        if self.pos <= iter_start:
                            raise RuntimeError("Recovery succeeded but no progress was made.")

                        continue

                self.pos = start_pos

                children.append(result.tree)

                return ParseResult(
                    tree=ParsedNode(
                        Repeat.__name__,
                        tuple(children),
                    ),
                    node=node,
                    tokens=tokens,
                    parent_node=parent_node,
                    start_pos=start_pos,
                    pos=result.pos,
                    expected=self.expected.return_copy(),
                    failed=True,
                    failure_cause=result
                )

            if self.pos == iter_start:
                raise RuntimeError("Repeat node succeeded without consuming anything. Double check the grammar.")

            children.append(result.tree)

        return ParseResult(
            tree=ParsedNode(
                Repeat.__name__,
                children=tuple(children)
            ),
            tokens=tokens,
            node=node,
            parent_node=parent_node,
            start_pos=start_pos,
            pos=self.pos,
            failed=False
        )

    def parse_non_terminal(self, node: NonTerminal, 
                                 tokens: TokenList, 
                                 parent_node: GrammarNode | None=None,
                                 rule: Rule | None=None):
        start_pos = self.pos
        rule = rule or cast(Rule, node.rule)

        self.rule_stack.append(rule.name)

        result = self.parse_rule(rule, tokens)
        self.rule_stack.pop()

        if result.failed:
            self.pos = start_pos

            return ParseResult(
                tree=ParsedNode(
                    rule.name,
                    children=(result.tree,)
                ),
                node=node,
                parent_node=parent_node,
                start_pos=start_pos,
                pos=result.pos,
                expected=self.expected.return_copy(),
                failed=True,
                tokens=tokens,
                failure_cause=result
            )

        return ParseResult(
            tree=ParsedNode(
                rule.name,
                children=(result.tree,)
            ),
            node=node,
            parent_node=parent_node,
            start_pos=start_pos,
            pos=self.pos,
            tokens=tokens,
            failed=False
        )

    def parse_terminal(self, node: Terminal, 
                             tokens: TokenList, 
                             parent_node: GrammarNode | None=None):
        pos = self.pos
        if pos < len(tokens) and self.matches_terminal(tokens[pos], node):
            # debug_print(f"{print_token_safe(tokens, pos)}. Matched {value.name}")
            self.pos += 1
            return ParseResult(
                tree=tokens[pos],
                node=node,
                parent_node=parent_node,
                start_pos=pos,
                pos=self.pos,
                tokens=tokens,
                failed=False
            )

        self.expected.record(pos, node.child, tuple(self.rule_stack))

        return ParseResult(
            tree=ParsedNode(
                Terminal.__name__,
                (),
            ),
            node=node,
            parent_node=parent_node,
            start_pos=pos,
            pos=pos,
            tokens=tokens,
            expected=self.expected.return_copy(),
            failed=True
        )

    def parse_node(self, node: GrammarNode, 
                         tokens: TokenList, 
                         parent_node: GrammarNode | None=None) -> ParseResult:
        match node:
            case Alternative():
                return self.parse_alternative(node, tokens, parent_node)
            case Sequence():
                return self.parse_sequence(node, tokens, parent_node)
            case OptionalNode():
                return self.parse_optional(node, tokens, parent_node)
            case Repeat():
                return self.parse_repeat(node, tokens, parent_node)
            case NonTerminal():
                return self.parse_non_terminal(node, tokens, parent_node)
            case Terminal():
                return self.parse_terminal(node, tokens, parent_node)
            case GrammarNode():
                raise RuntimeError("A bare grammarnode was found. Check `tree.py`")


    def parse_rule(self, rule: Rule, tokens: TokenList) -> ParseResult:
        return self.parse_node(rule.body, tokens)

    def read(self, text: str) -> ParseResult:
        return self.parse(list(tokenizer.read(text)))

    def parse(self, tokens: TokenList) -> ParseResult:
        program_node = get_root_node(self.rules)
        self.pos = 0
        self.rule_stack = []
        self.accumulated_errors = []
        result = self.parse_non_terminal(cast(NonTerminal, program_node.body), tokens, rule=program_node)
        # result.expected = self.expected
        if result.failed:
            self.accumulated_errors.append(result)

        return result

