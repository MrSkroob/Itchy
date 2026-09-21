from dataclasses import dataclass
from typing import cast

from itchy.tokenizer import Token, Definitions
from itchy.tree import Rule, ParsedNode, GrammarNode, get_root_node, \
    Alternative, Sequence, OptionalNode, Repeat, NonTerminal, Terminal


TokenList = list[Token[Definitions]]


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
    def __init__(self, rules: list[Rule], allow_recovery: bool=False) -> None:
        self.allow_recovery = allow_recovery
        self.rollback_stack: TokenList = []
        self.rules=rules
        self.pos: int = 0

    def next_token(self, tokens: TokenList):
        token = self.peek(tokens, 1)
        if token:
            self.pos += 1

        return token

    def skip(self, tokens: TokenList, count: int = 1) -> None: 
        """ Skips up to `count` tokens. """ 
        self.pos = min(len(tokens), self.pos + count)

    def peek(self, tokens: TokenList, ahead: int):
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
            pos=start_pos,
            failed=True,
            failure_cause=furthest_result
        )

    def parse_sequence(self, node: Sequence, tokens: TokenList, parent_node: GrammarNode | None):
        start_pos = self.pos
        children: list[ParsedNode | Token[Definitions]] = []
        for index, part in enumerate(node.children):
            result = self.parse_node(part, tokens, node)
            if result.failed:
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

    def parse_optional(self, node: OptionalNode, tokens: TokenList, parent_node: GrammarNode | None=None):
        start_pos = self.pos

        result = self.parse_node(node.child, tokens, parent_node=node)

        if result.failed:
            self.pos = start_pos
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

    def parse_repeat(self, node: Repeat, tokens: TokenList, parent_node: GrammarNode | None=None):
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

    def parse_terminal(self, node: Terminal, tokens: TokenList, parent_node: GrammarNode | None=None):
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

    def parse_node(self, node: GrammarNode, tokens: TokenList, parent_node: GrammarNode | None=None) -> ParseResult:
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
                return self.parse_rule(cast(Rule, node.rule), tokens)
            case Terminal():
                return self.parse_terminal(node, tokens, parent_node)


    def parse_rule(self, rule: Rule, tokens: TokenList) -> ParseResult:
        return self.parse_node(rule.body, tokens)

    def parse(self, tokens: TokenList) -> ParseResult:
        self.stack = []
        program_node = get_root_node(self.rules)
        self.pos=0
        return self.parse_rule(program_node, tokens)

