from dataclasses import dataclass
from typing import cast

from itchy.dummy_nodes import Strategy
from itchy.tokenizer import Token, Definitions, Tokenizer
from itchy.tree import Rule, ParsedNode, GrammarNode, get_root_node, build_parse_tree, \
    Alternative, Sequence, OptionalNode, Repeat, NonTerminal, Terminal


TokenList = list[Token[Definitions]]


tokenizer = Tokenizer(Definitions, {"Comment", "Whitespace", "Newline", "BlockComment"})


@dataclass(kw_only=True)
class ParseResult():
    # used for AST
    tree: ParsedNode | Token[Definitions]

    # used for error recovery
    node: GrammarNode
    parent_node: GrammarNode | None=None
    child_index: int | None=None # used for determining error node location 

    start_pos: int
    pos: int
    failed: bool
    failure_cause: ParseResult | None=None

    @property
    def progress_made(self) -> int:
        return self.pos - self.start_pos

    @property
    def deepest(self) -> "ParseResult":
        result = self

        while result.failure_cause is not None:
            result = result.failure_cause

        return result


class Parser():
    def __init__(self, allow_recovery: bool=False, 
                       recovery_rules: dict[str, set[str]] | None=None, 
                       statement_separator: str=";",
                       recovery_nodes: Strategy | None=None) -> None:
        self.allow_recovery = allow_recovery
        self.recovery_nodes = recovery_nodes or {}
        self.recovery_rules = recovery_rules or {
            "stat": {";", "}"},
            "chunk": {";", "}"}
        }
        self.statement_separator = statement_separator
        self.rules=build_parse_tree()
        self.pos: int = 0
        self.accumulated_errors: list[ParseResult] = []

    def get_recovery_target(self, result: ParseResult) -> ParseResult | None:
        """
        Returns the deepest rule to recover from (i.e. the root cause that's not the Terminal)
        """
        current: ParseResult | None = result
        target: ParseResult | None = None

        while current is not None:
            if isinstance(current.node, NonTerminal):
                rule = cast(Rule, current.node.rule)

                if rule.name in self.recovery_rules and current.progress_made > 0:
                    target = current

            current = current.failure_cause

        return target

    def recover(self, result: ParseResult, tokens: TokenList):
        self.accumulated_errors.append(result)
        target = self.get_recovery_target(result)

        if target is None:
            return

        rule = cast(Rule, cast(NonTerminal, target.node).rule)
        recovery_chars = self.recovery_rules[rule.name]

        self.pos = target.pos

        # advance to next statement
        while self.pos < len(tokens):
            token = tokens[self.pos]

            if token.literal in recovery_chars:
                break

            self.pos += 1

        # we reached EOF. this is truly a bruh moment.
        if self.pos >= len(tokens):
            return None

        if tokens[self.pos].literal == self.statement_separator:
            self.pos += 1

        return ParseResult(
            tree=ParsedNode(
                name=rule.name,
                children=(),
                dummy_node=True,
            ),
            node=target.node,
            parent_node=target.parent_node,
            start_pos=target.start_pos,
            pos=self.pos,
            failed=False
        )

    def skip(self, tokens: TokenList, count: int=1) -> None: 
        """ Skips up to `count` tokens. """ 
        self.pos = min(len(tokens), self.pos + count)

    def token_at(self, tokens: TokenList, pos: int, ahead: int=0):
        index = pos + ahead

        if index >= len(tokens):
            return None

        return tokens[index]

    def peek(self, tokens: TokenList, ahead: int=0):
        """
        Gets the next token without advancing by `ahead` amount
        """
        index = self.pos + ahead

        if index >= len(tokens):
            return

        return tokens[index]

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
            parent_node=parent_node,
            start_pos=start_pos,
            pos=furthest_result.pos,
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
                if self.allow_recovery:
                    if isinstance(part, Terminal):
                        if part.child.name in self.recovery_nodes:
                            self.accumulated_errors.append(result)
                            result = ParseResult(
                                tree=cast(Token[Definitions], 
                                          self.recovery_nodes[part.child.name]()),
                                node=node,
                                parent_node=parent_node,
                                start_pos=result.start_pos,
                                pos=result.pos + 1,
                                failed=False
                            )
                            recovered = True

                if not recovered:
                    self.pos = start_pos
                    return ParseResult(
                        tree=ParsedNode(
                            Sequence.__name__, 
                            tuple(children)
                        ),
                        node=node,
                        parent_node=parent_node,
                        child_index=index,
                        start_pos=start_pos,
                        pos=result.pos,
                        failed=True,
                        failure_cause=result,
                    )

            children.append(result.tree)

        return ParseResult(
            tree=ParsedNode(
                Sequence.__name__,
                tuple(children)
            ),
            node=node,
            parent_node=parent_node,
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
                start_pos=start_pos,
                pos=self.pos,
                failed=True,
                failure_cause=result
            )

        return ParseResult(
            tree=ParsedNode(
                OptionalNode.__name__,
                (result.tree,),
            ),
            node=node,
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

                return ParseResult(
                    tree=ParsedNode(
                        Repeat.__name__,
                        tuple(children),
                    ),
                    node=node,
                    parent_node=parent_node,
                    start_pos=start_pos,
                    pos=result.pos,
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
            node=node,
            parent_node=parent_node,
            start_pos=start_pos,
            pos=self.pos,
            failed=False
        )

    def parse_non_terminal(self, node: NonTerminal, 
                                 tokens: TokenList, 
                                 parent_node: GrammarNode | None=None):
        start_pos = self.pos
        rule = cast(Rule, node.rule)

        result = self.parse_rule(rule, tokens)

        if result.failed:
            self.pos = start_pos

            return ParseResult(
                tree=result.tree,
                node=node,
                parent_node=parent_node,
                start_pos=start_pos,
                pos=result.pos,
                failed=True,
                failure_cause=result
            )

        return ParseResult(
            tree=result.tree,
            node=node,
            parent_node=parent_node,
            start_pos=start_pos,
            pos=self.pos,
            failed=False
        )

    def parse_terminal(self, node: Terminal, 
                             tokens: TokenList, 
                             parent_node: GrammarNode | None=None):
        pos = self.pos
        if pos < len(tokens) and node.child.name == tokens[pos].kind.name \
            and (node.literal and node.literal == tokens[pos].literal or not node.literal):
            # debug_print(f"{print_token_safe(tokens, pos)}. Matched {value.name}")
            self.pos += 1
            return ParseResult(
                tree=tokens[pos],
                node=node,
                parent_node=parent_node,
                start_pos=pos,
                pos=self.pos,
                failed=False
            )

        return ParseResult(
            tree=ParsedNode(
                Terminal.__name__,
                (),
            ),
            node=node,
            parent_node=parent_node,
            start_pos=pos,
            pos=pos,
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
        self.pos=0
        return self.parse_rule(program_node, tokens)

