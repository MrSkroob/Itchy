from dataclasses import dataclass, field
from itchy.tree import ParsedNode
from itchy.parser import Token, Sequence, Repeat, OptionalNode, Alternative
from itchy.tokenizer import GenericRules, Definitions, Tokenizer
from itchy.shared_templates import SourceSpan, SourcePosition
from typing import Callable
import ast


ParsedChild = ParsedNode | Token[Definitions]


@dataclass(frozen=True)
class SemanticToken:
    line: int
    character: int
    length: int
    token_type: str
    modifiers: tuple[str, ...] = ()

def utf16_length(text: str) -> int:
    return len(text.encode("utf-16-le")) // 2


def collect_comment_tokens(source: str) -> list[SemanticToken]:
    """
    Comments are dropped by the tokenizer the parser uses (they're
    blacklisted), so they never reach build_ast at all. Run a second,
    throwaway tokenizer pass over the raw source that keeps comments, just
    to harvest their positions for highlighting.
    """
    raw_tokenizer = Tokenizer(Definitions, {"Whitespace", "Newline"})

    return [
        SemanticToken(
            line=token.line - 1,
            character=token.char - 1,
            length=utf16_length(token.literal),
            token_type="comment",
        )
        for token in raw_tokenizer.read(source)
        if token.kind == Definitions.Comment
    ]


@dataclass(frozen=True, kw_only=True)
class ASTNode:
    span: SourceSpan = field(default=SourceSpan(SourcePosition(-1, -1), SourcePosition(-1, -1)), kw_only=True, repr=False)
    dummy: bool=False

class Stmt(ASTNode):
    pass


class Expr(ASTNode):
    pass


class Event(ASTNode):
    pass


@dataclass(frozen=True)
class Program(ASTNode):
    body: tuple[Stmt, ...]


@dataclass(frozen=True)
class BlockStmt(Stmt):
    body: tuple[Stmt, ...]


@dataclass(frozen=True)
class WhileStmt(Stmt):
    condition: Expr
    body: tuple[Stmt, ...]


@dataclass(frozen=True)
class IfBranch(ASTNode):
    condition: Expr
    body: tuple[Stmt, ...]


@dataclass(frozen=True)
class IfStmt(Stmt):
    branches: tuple[IfBranch, ...]
    else_body: tuple[Stmt, ...]


@dataclass(frozen=True)
class ForRangeStmt(Stmt):
    variable: str
    start: Expr
    stop: Expr
    step: Expr
    body: tuple[Stmt, ...]


@dataclass(frozen=True)
class ForInStmt(Stmt):
    variable: str
    iterable: "VarRef"
    body: tuple[Stmt, ...]


@dataclass(frozen=True)
class EventHandlerStmt(Stmt):
    name: str
    params: tuple[Expr, ...]
    body: tuple[Stmt, ...]


@dataclass(frozen=True)
class FunctionDefStmt(Stmt):
    name: str
    params: tuple["Param", ...]
    body: tuple[Stmt, ...]
    warp: bool = False


@dataclass(frozen=True)
class VarDefStmt(Stmt):
    type_name: str
    name: str
    shared: bool = False


@dataclass(frozen=True)
class VarRef(ASTNode):
    root: str
    slice_expr: Expr | None = None


@dataclass(frozen=True)
class AssignStmt(Stmt):
    target: VarRef
    value: Expr


@dataclass(frozen=True)
class FunctionCallStmt(Stmt):
    callee: str
    args: tuple[Expr, ...]


@dataclass(frozen=True)
class BreakStmt(Stmt):
    pass


@dataclass(frozen=True)
class ReturnStmt(Stmt):
    values: tuple[Expr, ...]


@dataclass(frozen=True)
class Param(ASTNode):
    name: str
    type_name: str


@dataclass(frozen=True)
class VarExpr(Expr):
    ref: VarRef


@dataclass(frozen=True)
class FunctionCallExpr(Expr):
    callee: str
    args: tuple[Expr, ...]


@dataclass(frozen=True)
class TableExpr(Expr):
    values: tuple[Expr, ...]


@dataclass(frozen=True)
class BoolExpr(Expr):
    value: bool


@dataclass(frozen=True)
class NumberExpr(Expr):
    value: int | float


@dataclass(frozen=True)
class StringExpr(Expr):
    value: str


@dataclass(frozen=True)
class UnaryOpExpr(Expr):
    op: str
    value: Expr


@dataclass(frozen=True)
class BinaryOpExpr(Expr):
    left: Expr
    op: str
    right: Expr


@dataclass(frozen=True)
class ForRangeBody:
    start: Expr
    stop: Expr
    step: Expr
    span: SourceSpan


@dataclass(frozen=True)
class ForInBody:
    iterable: VarRef
    span: SourceSpan


@dataclass(frozen=True)
class EventParts:
    name: str
    params: tuple[Expr, ...]
    body: tuple[Stmt, ...]


@dataclass(frozen=True)
class FunctionParts:
    name: str
    params: tuple[Param, ...]
    body: tuple[Stmt, ...]
    span: SourceSpan


@dataclass(frozen=True)
class AssignAction:
    value: Expr
    

@dataclass(frozen=True)
class CallAction:
    arg_groups: tuple[Expr, ...]


ForBody = ForRangeBody | ForInBody
AssignOrCall = AssignAction | CallAction


def is_token(
    x: ParsedChild,
    name: str | None = None,
    literal: str | None = None
) -> bool:
    if not isinstance(x, Token):
        return False

    if name is not None and x.kind.name != name:
        return False

    if literal is not None and x.literal != literal:
        return False

    return True


def is_node(node: ParsedChild, name: str | None = None) -> bool:
    return isinstance(node, ParsedNode) and (name is None or node.name == name)


def expect_node(node: ParsedChild, name: str) -> ParsedNode:
    if not is_node(node, name):
        raise ValueError(f"Expected `{name}` got {node}")
    
    assert isinstance(node, ParsedNode)

    return node


def expect_token(token: ParsedChild, name: str | None=None, literal: str | None=None) -> Token[Definitions]:
    if not is_token(token, name, literal):
        raise ValueError(f"Expected token with name {name}, literal {literal}, got {token}")

    assert type(token) is Token

    return token


def flat_children(node: ParsedNode):
    """
    Removes sequence/repeat/optional/alternative identifiers
    """
    output: list[ParsedChild] = []

    def visit(child: ParsedChild):
        if isinstance(child, ParsedNode) and child.name in {Sequence.__name__, Repeat.__name__, OptionalNode.__name__, Alternative.__name__}:
            for grandchild in child.children:
                visit(grandchild)
        else:
            output.append(child)
    
    for child in node.children:
        visit(child)

    return output


def find_first_node(node: ParsedNode, name: str, children: list[ParsedChild] | None=None):
    """
    Strictly finds the first occuring node with said name
    """
    for child in children or flat_children(node):
        if is_node(child, name):
            assert isinstance(child, ParsedNode)
            return child
    
    raise ValueError(f"No child found with name {name}")


def search_nodes(nodes: list[ParsedChild], name: str) -> list[ParsedNode]:
    return [
        i for i in nodes
        if isinstance(i, ParsedNode) and i.name == name
    ]


# def all_nodes(node: ParsedNode, name: str):
#     return search_nodes(flat_children(node), name)
def has_node(node: ParsedNode, name: str) -> bool:
    return any(is_node(i, name) for i in flat_children(node))


def find_first_token(node: ParsedNode, name: str, children: list[ParsedChild] | None=None):
    for child in children or flat_children(node):
        if is_token(child, name):
            assert isinstance(child, Token)
            return child

    raise ValueError(f"No token found with name {name}")


def has_token(node: ParsedNode, name: str, children: list[ParsedChild] | None=None):
    return any(is_token(i, name) for i in children or flat_children(node))


def parse_number(text: str):
    value = float(text)
    return int(value) if value.is_integer() else value


def parse_string(text: str):
    try:
        value = ast.literal_eval(text)
    except ValueError:
        return text.strip('"')
    
    if not isinstance(value, str):
        raise ValueError("STRING NOT STRING?! OOGA BOOGA")

    return value


class ASTBuilder:
    """
    Finally, a tangible reason for a class!
    We're going to have multiple calls to this bad boy, and they're likely in separate threads.
    We need different ASTBuilder objects to ensure `function_scope` and `called_function` remain separate between calls. 


    ``function_scope`` is the enclosing function currently being built.
    ``called_function`` is the function whose argument list is currently being
    built. Keeping these separate prevents a call such as ``foo(...)`` from
    making ``foo``'s parameters appear in the caller's lexical scope.
    """

    def __init__(self) -> None:
        self.semantic_tokens: list[SemanticToken] = []
        self.function_scope: str | None = None
        self.called_function: FunctionCallStmt | FunctionCallExpr | None = None
        self.argument_index: int = 0
        self.function_definitions: dict[FunctionDefStmt, SourceSpan] = {}
        self.var_definitions: dict[VarDefStmt, SourceSpan] = {}

    def reset(self) -> None:
        self.argument_index = 0
        self.semantic_tokens = []
        self.function_scope = None
        self.called_function = None
        self.function_definitions = {}
        self.var_definitions = {}

    def emit_token(
        self,
        token: Token[Definitions] | None,
        token_type: str,
        modifiers: tuple[str, ...] = (),
    ) -> None:
        if token is None:
            return

        if token.dummy_token:
            return

        # The tokenizer stores one-based positions; LSP positions are zero-based.
        self.semantic_tokens.append(
            SemanticToken(
                line=token.line - 1,
                character=token.char - 1,
                length=utf16_length(token.literal),
                token_type=token_type,
                modifiers=modifiers,
            )
        )

    def build(
        self,
        tree: ParsedChild,
        source: str | None = None,
        *,
        include_comments: bool = False,
    ) -> Program:
        self.reset()
        program = self._build_ast(tree)

        if include_comments and source is not None:
            self.semantic_tokens.extend(collect_comment_tokens(source))

        self.semantic_tokens.sort(key=lambda token: (token.line, token.character))
        return program

    def build_with_semantic_tokens(
        self,
        tree: ParsedChild,
        source: str | None = None,
        *,
        include_comments: bool = False,
    ) -> tuple[Program, list[SemanticToken]]:
        program = self.build(
            tree,
            source,
            include_comments=include_comments,
        )
        return program, self.semantic_tokens.copy()

    def build_equation(self, node: ParsedNode) -> Expr:
        return self.build_comparison(find_first_node(node, "comparison"))
    
    
    def build_comparison(self, node: ParsedNode) -> Expr:
        return self.build_left_associative(
            node=node,
            operand_rule="addition",
            operand_builder=self.build_addition,
        )
    
    
    def build_addition(self, node: ParsedNode) -> Expr:
        return self.build_left_associative(
            node=node,
            operand_rule="multiplication",
            operand_builder=self.build_multiplication,
        )
    
    
    def build_multiplication(self, node: ParsedNode) -> Expr:
        return self.build_left_associative(
            node=node,
            operand_rule="unary",
            operand_builder=self.build_unary,
        )
    
    
    def build_left_associative(
        self,
        *,
        node: ParsedNode,
        operand_rule: str,
        operand_builder: Callable[[ParsedNode], Expr],
    ) -> Expr:
        children = flat_children(node)
    
        operands: list[ParsedNode] = []
        operators: list[Token[Definitions]] = []
    
        for child in children:
            if isinstance(child, ParsedNode) and child.name == operand_rule:
                operands.append(child)
    
            elif isinstance(child, Token):
                operators.append(child)
                self.emit_token(child, "operator")
    
        if not operands:
            raise ValueError(f"i wanted an operand. you gave me: <{node.name}>")
    
        expr = operand_builder(operands[0])
    
        for op_token, operand in zip(operators, operands[1:]):
            op = op_token.literal
            right = operand_builder(operand)
            expr = BinaryOpExpr(
                left=expr,
                op=op,
                right=right,
                span=SourceSpan(
                    start=expr.span.start,
                    end=right.span.end
                ), 
                dummy=node.dummy_node
            )
    
        return expr
    
    
    def build_unary(self, node: ParsedNode) -> Expr:
        children = flat_children(node)
    
        op: Token[Definitions] | None = None
        primary: ParsedNode | None = None
    
        for child in children:
            if (isinstance(child, Token) and child.literal == "-") and child.kind == Definitions.Binop:
                op = child
            elif isinstance(child, ParsedNode) and child.name == "primary":
                primary = child
    
        if primary is None:
            raise ValueError(f"need something to work with big dawg: {node!r}")
    
        expr = self.build_primary(primary)
    
        if op is None:
            return expr
    
        self.emit_token(op, "operator")
    
        return UnaryOpExpr(op.literal, expr, 
                           span=SourceSpan(
                               start=SourcePosition(op.line, op.char),
                               end=expr.span.end
                           ), dummy=node.dummy_node)
    
    
    def build_primary(self, node: ParsedNode) -> Expr:
        children = flat_children(node)
    
        for child in children:
            if isinstance(child, ParsedNode) and child.name == "literals":
                return self.build_literals(child)
    
            if isinstance(child, ParsedNode) and child.name == "equation":
                return self.build_equation(child)

        raise ValueError(f"this ain't a primary g: {node!r}")
    
    
    def build_literals(self, node: ParsedNode) -> Expr:
        children = flat_children(node)
    
        for child in children:
            if is_token(child, name="Bool"):
                assert isinstance(child, Token)
                # self.emit_token(child, "boolean")
                return BoolExpr(child.literal.lower() == "true", span=child.span, dummy=node.dummy_node)
    
            if is_token(child, name="Number"):
                assert isinstance(child, Token)
                # self.emit_token(child, "number")
                return NumberExpr(parse_number(child.literal), span=child.span, dummy=node.dummy_node)
    
            if is_token(child, name="String"):
                assert isinstance(child, Token)
                # self.emit_token(child, "string")
                return StringExpr(parse_string(child.literal), span=child.span, dummy=node.dummy_node)
    
            if isinstance(child, ParsedNode) and child.name == "tableconstructor":
                return self.build_tableconstructor(child)
            
            if isinstance(child, ParsedNode) and child.name == "var":
                has_slice = has_node(child, "slice")
                var_name = find_first_token(child, Definitions.Symbol.name)
                self.emit_token(var_name, "variable")
    
                if has_slice:
                    slice_expr = self.build_slice(find_first_node(child, "slice"))
    
                    span = SourceSpan(
                        var_name.span.start,
                        slice_expr.span.end
                    )
    
                    return VarExpr(VarRef(
                        var_name.literal,
                        slice_expr,
                        span=span,
                        dummy=var_name.dummy_token
                    ), span=span, dummy=node.dummy_node)
                else:
                    return VarExpr(VarRef(
                        var_name.literal,
                        span = var_name.span,
                        dummy=var_name.dummy_token
                    ), span=var_name.span, dummy=node.dummy_node)
            
            if isinstance(child, ParsedNode) and child.name == "functioncall":
                func_name = find_first_token(child, Definitions.Symbol.name)
                self.emit_token(func_name, "function")
                arg_list = self.build_varlist1(find_first_node(child, "args"))
                stmt = FunctionCallExpr(
                    func_name.literal,
                    arg_list,
                    span=SourceSpan(
                        func_name.span.start,
                        arg_list[-1].span.end if len(arg_list) > 0 else func_name.span.end
                    ), dummy=node.dummy_node
                )
                self.called_function = stmt
                return stmt
    
        raise ValueError(f"this ain't a literal g: {node.children}")
    
    
    def build_var(self, node: ParsedNode, modifiers: tuple[str, ...] = ()) -> VarRef:
        # children = flat_children(node)
    
        symbol: Token[Definitions] = find_first_token(node, Definitions.Symbol.name)
        slice_expr: Expr | None = None
        has_slice = has_node(node, "slice")
        if has_slice:
            slice_expr = self.build_slice(find_first_node(node, "slice"))
    
        if not symbol:
            raise ValueError(f"how u gonna want a variable with no name: {node!r}")
    
        self.emit_token(symbol, "variable", modifiers)
    
        return VarRef(
            root=symbol.literal,
            slice_expr=slice_expr,
            span=SourceSpan(
                symbol.span.start,
                slice_expr.span.end if slice_expr is not None else symbol.span.end
            ), dummy=node.dummy_node
        )
    
    
    def build_slice(self, node: ParsedNode) -> Expr:
        return self.build_equation(find_first_node(node, "equation"))
    
    
    def build_tableconstructor(self, node: ParsedNode) -> TableExpr:
        children = flat_children(node)
    
        bracket_tokens = [
            child
            for child in children
            if isinstance(child, Token)
            and child.kind in {
                Definitions.OpenSquareBracket,
                Definitions.CloseSquareBracket,
            }
        ]
    
        # guaranteed to have at least two members (assuming is valid syntax)
        span = SourceSpan(bracket_tokens[0].span.start, bracket_tokens[-1].span.end)
    
        for child in children:
            if isinstance(child, ParsedNode) and child.name == "varlist1":
                args = self.build_varlist1(child)
                if len(args) > 0:
                    return TableExpr(args, span=span, dummy=node.dummy_node)
                else:
                    return TableExpr(args, span=span, dummy=node.dummy_node)
    
        return TableExpr((), span=span, dummy=node.dummy_node)
    
    
    def build_varlist1(self, node: ParsedNode) -> tuple[Expr, ...]:
        values: list[Expr] = []
        children = flat_children(node)

        index = 0

        for child in children:
            if isinstance(child, ParsedNode) and child.name == "equation":
                index += 1
                self.argument_index = index
                values.append(self.build_equation(child))
            elif isinstance(child, ParsedNode) and child.name == "varlist1":
                values.extend(self.build_varlist1(child))

        self.argument_index = index
        
        return tuple(values)
    
    
    def build_namelist(self, node: ParsedNode) -> tuple[str, ...]:
        return tuple(
            i.literal for i in flat_children(node) if is_token(i, "Symbol") and isinstance(i, Token)
        )
    
    
    def build_functioncall(self, node: ParsedNode) -> FunctionCallStmt:
        function_name = find_first_token(node, Definitions.Symbol.name)
        self.emit_token(function_name, "function")
        self.argument_index = 0
        args = self.build_varlist1(find_first_node(node, "args"))
        stmt = FunctionCallStmt(
            function_name.literal,
            args,
            span=SourceSpan(
                function_name.span.start,
                args[-1].span.end if len(args) > 0 else function_name.span.end
            ), dummy=node.dummy_node
        )
        self.called_function = stmt
        return stmt 
    
    def build_varassignstat(self, node: ParsedNode) -> AssignStmt:
        var_node = find_first_node(node, "var")
        operation = find_first_token(node, Definitions.Assign.name)
        action_node = find_first_node(node, "equation")
    
        target = self.build_var(var_node, ("modification",))
        self.emit_token(operation, "operator")
        action = self.build_equation(action_node)
    
        if operation.literal == "=":
            return AssignStmt(
                target,
                action,
                span=SourceSpan(
                    target.span.start,
                    action.span.end
                ), dummy=node.dummy_node
            )
        else:
            OPERATION_TO_BINOP = {
                "*=": "*",
                "%=": "%",
                "^=": "^",
                "+=": "+",
                "-=": "-",
                "/=": "/"
            }
    
            return AssignStmt(
                target,
                BinaryOpExpr(VarExpr(target, span=target.span, dummy=target.dummy), 
                            OPERATION_TO_BINOP[operation.literal], action, 
                            span=SourceSpan(target.span.start, SourcePosition(target.span.end.line, target.span.end.character + 2)), 
                            dummy=node.dummy_node),
                span=SourceSpan(
                    target.span.start,
                    action.span.end
                ), dummy=node.dummy_node
            )
    
    def build_vardefstat(self, node: ParsedNode) -> VarDefStmt:
        shared = has_token(node, "Shared")
    
    
        type_token = find_first_token(node, "Type")
        symbol_token = find_first_token(node, "Symbol")
    
        self.emit_token(type_token, "type")
        self.emit_token(symbol_token, "variable", ("declaration",))
    
        start = type_token.span.start
    
        if shared:
            shared_token = find_first_token(node, "Shared")
            # self.emit_token(shared_token, "keyword")
            start = shared_token.span.start

        stmt = VarDefStmt(
            type_token.literal,
            symbol_token.literal,
            shared,
            span=SourceSpan(
                start=start,
                end=symbol_token.span.end
            ), dummy=node.dummy_node
        )

        self.var_definitions[stmt] = stmt.span
    
        return stmt
    
    
    def build_paramlist(self, node: ParsedNode) -> tuple[Param, ...]:
        return tuple(
            self.build_argtype(child)
            for child in flat_children(node)
            if isinstance(child, ParsedNode) and child.name == "argtype"
        )
    
    
    def build_argtype(self, node: ParsedNode) -> Param:
        children = flat_children(node)
    
        name = expect_token(children[0], name="Symbol")
        type_name = expect_token(children[2], name="Type")
    
        self.emit_token(name, "parameter", ("declaration", "readonly"))
        self.emit_token(type_name, "type")
    
        return Param(name.literal, type_name.literal, span=SourceSpan(name.span.start, type_name.span.end), dummy=node.dummy_node)
    
    
    def build_funcbody(self, node: ParsedNode) -> tuple[tuple[Param, ...], tuple[Stmt, ...]]:
        params: tuple[Param, ...] = ()
        body: tuple[Stmt, ...] = ()
    
        for child in flat_children(node):
            if isinstance(child, ParsedNode) and child.name == "paramlist":
                params = self.build_paramlist(child)
    
            elif isinstance(child, ParsedNode) and child.name == "wrap":
                body = self.build_wrap(child)
    
        return params, body    
    
    
    def build_function(self, node: ParsedNode) -> FunctionParts:
        children = flat_children(node)
        name = expect_token(children[0], Definitions.Symbol.name)
        previous_scope = self.function_scope
        self.function_scope = name.literal
        self.emit_token(name, "function", ("declaration",))
        funcbody = expect_node(children[1], "funcbody")
    
        params, body = self.build_funcbody(funcbody)
        self.function_scope = previous_scope
    
        if len(params) > 0:
            end = params[-1].span.end
        else:
            end = name.span.end
    
        return FunctionParts(
            name.literal,
            params,
            body,
            span=SourceSpan(
                start=name.span.start,
                end=end
            )
        ) 
    
    
    def build_functionstat(self, node: ParsedNode) -> FunctionDefStmt:
        warp = has_token(node, Definitions.Warp.name)
    
        define = find_first_token(node, Definitions.Define.name)
        # self.emit_token(define_token, "keyword")
    
        function = find_first_node(node, "function")
        parts = self.build_function(function)

        stmt = FunctionDefStmt(
            parts.name,
            parts.params,
            parts.body,
            warp,
            span=SourceSpan(
                start=define.span.start,
                end=parts.span.end
            ), dummy=node.dummy_node
        )

        self.function_definitions[stmt] = stmt.span
        
        return stmt
    
    
    def build_eventstat(self, node: ParsedNode) -> EventHandlerStmt:
        children = flat_children(node)
        event = expect_token(children[0], Definitions.Event.name)
        # self.emit_token(event_token, "keyword")
        name = expect_token(children[1], Definitions.Symbol.name)
        self.emit_token(name, "event")
        eventbody = expect_node(children[2], "args")
        wrap = expect_node(children[3], "wrap")
        
        args = self.build_varlist1(eventbody)
        wrap_nodes = self.build_wrap(wrap)
    
        if len(args) > 0:
            end = args[-1].span.end
        else:
            end = name.span.end
        
        return EventHandlerStmt(
            name.literal, 
            args,
            wrap_nodes,
            span=SourceSpan(
                start=event.span.start,
                end=end
            ), dummy=node.dummy_node
        )
    
    
    def build_for_body(self, node: ParsedNode) -> ForBody:
        children = flat_children(node)
    
        if any(is_token(child, Definitions.In.name) for child in children):
            in_token = next(child for child in children if is_token(child, Definitions.In.name))
            assert isinstance(in_token, Token)
            # self.emit_token(in_token, "keyword")
    
            # var_node = next(search_nodes(children, "var"))
            var_node = self.build_var(next(
                i for i in children 
                if isinstance(i, ParsedNode) and i.name == "var"
                ))
    
            return ForInBody(var_node, span=var_node.span)
        
        equations = [
            i for i in children
            if isinstance(i, ParsedNode) and i.name == "equation"
        ]
    
        equation_start = self.build_equation(equations[0])
        equation_stop = self.build_equation(equations[1])
        step = self.build_equation(equations[2])
    
        return ForRangeBody(
            equation_start,
            equation_stop,
            step,
            span=SourceSpan(
                start=equation_start.span.start,
                end=step.span.end
            )
        )
    
    
    def build_forstat(self, node: ParsedNode):
        children = flat_children(node)
    
        for_token = expect_token(children[0], "For")
        # self.emit_token(for_token, "keyword")
    
        var_name_token = expect_token(children[1], "Symbol")
        self.emit_token(var_name_token, "variable", ("declaration",))
        var_name = var_name_token.literal
    
        forbody = expect_node(children[2], "forbody")
        wrap = expect_node(children[3], "wrap")
    
        body_spec = self.build_for_body(forbody)
        body = self.build_wrap(wrap)
    
        end = None
    
        if len(body) > 0:
            end = body[-1].span.end
    
        if not end:
            end = body_spec.span.end
    
        if isinstance(body_spec, ForRangeBody):
            return ForRangeStmt(
                var_name,
                body_spec.start,
                body_spec.stop,
                body_spec.step,
                body,
                span=SourceSpan(
                    start=for_token.span.start,
                    end=end
                ), dummy=node.dummy_node
            )
        else:
            
            return ForInStmt(
                var_name,
                body_spec.iterable,
                body,
                span=SourceSpan(
                    start=for_token.span.start,
                    end=end
                ), dummy=node.dummy_node
            )
    
    
    def build_ifstat(self, node: ParsedNode):
        children = flat_children(node)
    
        branches: list[IfBranch] = []
        else_body: tuple[Stmt, ...] = ()
    
        if_token = children[0]
        assert isinstance(if_token, Token) and if_token.kind.name == Definitions.If.name
        # self.emit_token(if_token, "keyword")
        condition = self.build_equation(expect_node(children[1], "equation"))
        body = self.build_wrap(expect_node(children[2], "wrap"))
        i = 3
    
        branches.append(IfBranch(condition, body, 
                                 span=SourceSpan(if_token.span.start, body[-1].span.end if len(body) > 0 else condition.span.end), 
                                 dummy=node.dummy_node))
    
        while i < len(children) and is_token(children[i], "ElseIf"):
            elseif_token = children[i]
            assert isinstance(elseif_token, Token)
            # self.emit_token(elseif_token, "keyword")
    
            i += 1
    
            condition = self.build_equation(expect_node(children[i], "equation"))
            i += 1
    
            body = self.build_wrap(expect_node(children[i], "wrap"))
            i += 1
    
            branches.append(IfBranch(condition, body, 
                                     span=SourceSpan(elseif_token.span.start, body[-1].span.end if len(body) > 0 else condition.span.end),
                                     dummy=node.dummy_node))
        
        if i < len(children) and is_token(children[i], "Else"):
            else_token = children[i]
            assert isinstance(else_token, Token)
            # self.emit_token(else_token, "keyword")
            i += 1
            else_body = self.build_wrap(expect_node(children[i], "wrap"))
    
        if len(else_body) > 0:
            end = else_body[-1].span.end
        else:
            # branches guaranteed to have at least one member.
            end = branches[-1].span.end
        
    
        return IfStmt(
            tuple(branches),
            else_body,
            span=SourceSpan(if_token.span.start, end),
            dummy=node.dummy_node
        ) 
    
    
    def build_whilestat(self, node: ParsedNode):
        while_token = find_first_token(node, Definitions.While.name)
        # self.emit_token(while_token, "keyword")
        condition = find_first_node(node, "equation")
        body = find_first_node(node, "wrap")
    
        equation = self.build_equation(condition)
        wrap = self.build_wrap(body)
    
        return WhileStmt(
            equation,
            wrap,
            span=SourceSpan(
                while_token.span.start,
                wrap[-1].span.end if len(wrap) > 0 else equation.span.end
            ),
            dummy=node.dummy_node
        )
    
    
    def build_wrap(self, node: ParsedNode):
        chunk = ()
        for child in flat_children(node):
            # if it passed the parser, we can sort of guarantee that the next node will be a chunk node,
            # but whatever...
    
            if isinstance(child, ParsedNode) and child.name == "chunk":
                chunk = self.build_chunk(child)
                break
    
        if has_node(node, "laststat"):
            chunk = chunk + (self.build_laststat(find_first_node(node, "laststat")),)
        
        # chunks are allowed to be empty
        return chunk
    
    
    def build_laststat(self, node: ParsedNode) -> ReturnStmt:
        children = flat_children(node)
    
        # if has_token(node, Definitions.Break.name, children):
        #     break_token = find_first_token(
        #         node,
        #         Definitions.Break.name,
        #         children,
        #     )
        #     self.emit_token(break_token, "keyword")
        #     return BreakStmt(span=break_token.span)
    
        if has_token(node, Definitions.Return.name, children):
            return_token = find_first_token(
                node,
                Definitions.Return.name,
                children,
            )
            # self.emit_token(return_token, "keyword")
    
            equation_node = next(
                (
                    child
                    for child in children
                    if isinstance(child, ParsedNode) and child.name == "equation"
                ),
                None,
            )
    
            if equation_node is None:
                return ReturnStmt(
                    (),
                    span=return_token.span,
                    dummy=node.dummy_node
                )
    
            value = self.build_equation(equation_node)
    
            return ReturnStmt(
                (value,),
                span=SourceSpan(
                    start=return_token.span.start,
                    end=value.span.end,
                ),
                dummy=node.dummy_node
            )
    
        raise ValueError("that's not good :[")
    
    def build_stat(self, node: ParsedNode) -> Stmt:
        for child in flat_children(node):
            if not isinstance(child, ParsedNode):
                continue
            
            match child.name:
                case "wrap":
                    wrap = self.build_wrap(child)
    
                    bracket_tokens = [
                        child
                        for child in flat_children(child)
                        if isinstance(child, Token)
                        and child.kind in {
                            Definitions.OpenCurlyBracket,
                            Definitions.CloseCurlyBracket,
                        }
                    ]
                    
                    return BlockStmt(wrap, span=SourceSpan(bracket_tokens[0].span.start, bracket_tokens[-1].span.end), dummy=node.dummy_node)
                
                case "whilestat":
                    return self.build_whilestat(child)
                
                case "ifstat":
                    return self.build_ifstat(child)
                
                case "forstat":
                    return self.build_forstat(child)
                
                case "functionstat":
                    return self.build_functionstat(child)
                
                case "eventstat":
                    return self.build_eventstat(child)
                
                case "vardefstat":
                    return self.build_vardefstat(child)
                
                case "varassignstat":
                    return self.build_varassignstat(child)
                
                case "functioncall":
                    return self.build_functioncall(child)
    
                case _:
                    pass
        
        raise ValueError(f"this is very bad: {node}")
    
    
    def build_chunk(self, node: ParsedNode):
        statements: list[Stmt] = []
    
        for child in flat_children(node):
            if is_token(child, name=GenericRules.StatementSeperator.name):
                continue
            if is_token(child, name=GenericRules.EOF.name):
                continue
    
            if isinstance(child, ParsedNode):
                if child.name == "stat":
                    statements.append(self.build_stat(child))
        
        return tuple(statements)
    
    
    def build_program(self, node: ParsedNode) -> Program:
        children = flat_children(node)
    
        variable_definitions = tuple(
            self.build_vardefstat(child)
            for child in children
            if isinstance(child, ParsedNode) and child.name == "vardefstat"
        )
    
        chunk = self.build_chunk(next(
            child
            for child in children
            if isinstance(child, ParsedNode) and child.name == "chunk"
        ))
    
        if len(variable_definitions) > 0:
            start = variable_definitions[0].span.start
        elif len(chunk) > 0:
            start = chunk[0].span.start
        else:
            start = SourcePosition(0, 0)
    
        if len(chunk) > 0:
            end = chunk[-1].span.end
        elif len(variable_definitions) > 0:
            end = variable_definitions[-1].span.end
        else:
            end = SourcePosition(0, 0)
    
        return Program(variable_definitions + chunk, span=SourceSpan(start, end), dummy=node.dummy_node)
    
    
    def _build_ast(self, tree: ParsedChild) -> Program:
        if isinstance(tree, Token):
            raise ValueError("gang what do you expect me to do with this")
    
        assert tree.name == "program", f"give me a root node. i got {tree.name} instead :/"
    
        return self.build_program(tree)

def build_ast(tree: ParsedChild) -> Program:
    """Compatibility wrapper that uses a fresh builder for every call."""
    return ASTBuilder().build(tree)


def build_ast_with_semantic_tokens(
    tree: ParsedChild,
    source: str | None = None,
    *,
    include_comments: bool = False,
) -> tuple[Program, list[SemanticToken]]:
    """Compatibility wrapper that returns independently collected tokens."""
    return ASTBuilder().build_with_semantic_tokens(
        tree,
        source,
        include_comments=include_comments,
    )
