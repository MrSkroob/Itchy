from dataclasses import dataclass, field
from parser import ParsedNode, Token, Sequence, Repeat, OptionalNode, Alternative
from tokenizer import GenericRules, Definitions
from shared_templates import SourceSpan, SourcePosition
from typing import Callable
import ast


ParsedChild = ParsedNode | Token[Definitions]


@dataclass(frozen=True, kw_only=True)
class ASTNode:
    span: SourceSpan = field(default=SourceSpan(SourcePosition(0, 0), SourcePosition(0, 0)), kw_only=True, repr=False)

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

# the reason why the main algorithm is not a class in this case is because in
# Parser.py, it has an interface .read() where it needs an existing tokenizer, pre-build parse tree, etc.
# it was simply better for the namespace for it to be neatly inside a class.

# this doesn't have any internal dependencies (really).


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


# MAIN PROGRAM STARTS HERE
# also if you're wondering why i'm using isinstance suddenly it's because i feel like it
def build_equation(node: ParsedNode) -> Expr:
    return build_comparison(find_first_node(node, "comparison"))


def build_comparison(node: ParsedNode) -> Expr:
    return build_left_associative(
        node=node,
        operand_rule="addition",
        operand_builder=build_addition,
    )


def build_addition(node: ParsedNode) -> Expr:
    return build_left_associative(
        node=node,
        operand_rule="multiplication",
        operand_builder=build_multiplication,
    )


def build_multiplication(node: ParsedNode) -> Expr:
    return build_left_associative(
        node=node,
        operand_rule="unary",
        operand_builder=build_unary,
    )


def build_left_associative(
    *,
    node: ParsedNode,
    operand_rule: str,
    operand_builder: Callable[[ParsedNode], Expr],
) -> Expr:
    children = flat_children(node)

    operands: list[ParsedNode] = []
    operators: list[str] = []

    for child in children:
        if isinstance(child, ParsedNode) and child.name == operand_rule:
            operands.append(child)

        elif isinstance(child, Token):
            operators.append(child.literal)

    if not operands:
        raise ValueError(f"i wanted an operand. you gave me: <{node.name}>")

    expr = operand_builder(operands[0])

    for op, operand in zip(operators, operands[1:]):
        right = operand_builder(operand)
        expr = BinaryOpExpr(
            left=expr,
            op=op,
            right=right,
            span=SourceSpan(
                start=expr.span.start,
                end=right.span.end
            )
        )

    return expr


def build_unary(node: ParsedNode) -> Expr:
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

    expr = build_primary(primary)

    if op is None:
        return expr

    return UnaryOpExpr(op.literal, expr, 
                       span=SourceSpan(
                           start=SourcePosition(op.line, op.char),
                           end=expr.span.end
                       ))


def build_primary(node: ParsedNode) -> Expr:
    children = flat_children(node)

    for child in children:
        if isinstance(child, ParsedNode) and child.name == "literals":
            return build_literals(child)

        if isinstance(child, ParsedNode) and child.name == "equation":
            return build_equation(child)

    raise ValueError(f"this ain't a primary g: {node!r}")


def build_literals(node: ParsedNode) -> Expr:
    children = flat_children(node)

    for child in children:
        if is_token(child, name="Bool"):
            assert isinstance(child, Token)
            return BoolExpr(child.literal.lower() == "true", span=child.span)

        if is_token(child, name="Number"):
            assert isinstance(child, Token)
            return NumberExpr(parse_number(child.literal), span=child.span)

        if is_token(child, name="String"):
            assert isinstance(child, Token)
            return StringExpr(parse_string(child.literal), span=child.span)

        if isinstance(child, ParsedNode) and child.name == "tableconstructor":
            return build_tableconstructor(child)
        
        if isinstance(child, ParsedNode) and child.name == "var":
            has_slice = has_node(child, "slice")
            var_name = find_first_token(child, Definitions.Symbol.name)

            if has_slice:
                slice_expr = build_slice(find_first_node(child, "slice"))

                span = SourceSpan(
                    var_name.span.start,
                    slice_expr.span.end
                )

                return VarExpr(VarRef(
                    var_name.literal,
                    slice_expr,
                    span=span
                ), span=span)
            else:
                return VarExpr(VarRef(
                    var_name.literal,
                    span = var_name.span
                ), span=var_name.span)
        
        if isinstance(child, ParsedNode) and child.name == "functioncall":
            func_name = find_first_token(child, Definitions.Symbol.name)
            arg_list = build_varlist1(find_first_node(child, "args"))

            return FunctionCallExpr(
                func_name.literal,
                arg_list,
                span=SourceSpan(
                    func_name.span.start,
                    arg_list[-1].span.end if len(arg_list) > 0 else func_name.span.end
                )
            )

    raise ValueError(f"this ain't a literal g: {node.children}")


def build_var(node: ParsedNode) -> VarRef:
    # children = flat_children(node)

    symbol: Token[Definitions] = find_first_token(node, Definitions.Symbol.name)
    slice_expr: Expr | None = None
    has_slice = has_node(node, "slice")
    if has_slice:
        slice_expr = build_slice(find_first_node(node, "slice"))

    if not symbol:
        raise ValueError(f"how u gonna want a variable with no name: {node!r}")

    return VarRef(
        root=symbol.literal,
        slice_expr=slice_expr,
        span=SourceSpan(
            symbol.span.start,
            slice_expr.span.end if slice_expr is not None else symbol.span.end
        )
    )


def build_slice(node: ParsedNode) -> Expr:
    return build_equation(find_first_node(node, "equation"))


def build_tableconstructor(node: ParsedNode) -> TableExpr:
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
            args = build_varlist1(child)
            if len(args) > 0:
                return TableExpr(args, span=span)
            else:
                return TableExpr(args, span=span)

    return TableExpr((), span=span)


def build_varlist1(node: ParsedNode) -> tuple[Expr, ...]:
    values: list[Expr] = []

    for child in flat_children(node):
        if isinstance(child, ParsedNode) and child.name == "equation":
            values.append(build_equation(child))
        elif isinstance(child, ParsedNode) and child.name == "varlist1":
            values.extend(build_varlist1(child))

    return tuple(values)


def build_namelist(node: ParsedNode) -> tuple[str, ...]:
    return tuple(
        i.literal for i in flat_children(node) if is_token(i, "Symbol") and isinstance(i, Token)
    )


def build_functioncall(node: ParsedNode) -> Stmt:
    function_name = find_first_token(node, Definitions.Symbol.name)
    args = build_varlist1(find_first_node(node, "args"))
    return FunctionCallStmt(
        function_name.literal,
        args,
        span=SourceSpan(
            function_name.span.start,
            args[-1].span.end if len(args) > 0 else function_name.span.end
        )
    )


def build_varassignstat(node: ParsedNode) -> Stmt:
    var_node = find_first_node(node, "var")
    operation = find_first_token(node, Definitions.Assign.name)
    action_node = find_first_node(node, "equation")

    target = build_var(var_node)
    action = build_equation(action_node)

    if operation.literal == "=":
        return AssignStmt(
            target,
            action,
            span=SourceSpan(
                target.span.start,
                action.span.end
            )
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
            BinaryOpExpr(VarExpr(target, span=target.span), 
                         OPERATION_TO_BINOP[operation.literal], action, 
                         span=SourceSpan(target.span.start, SourcePosition(target.span.end.line, target.span.end.character + 2))),
            span=SourceSpan(
                target.span.start,
                action.span.end
            )
        )

def build_vardefstat(node: ParsedNode) -> VarDefStmt:
    shared = has_token(node, "Shared")


    type_token = find_first_token(node, "Type")
    symbol_token = find_first_token(node, "Symbol")

    start = type_token.span.start

    if shared:
        start = find_first_token(node, "Shared").span.start

    return VarDefStmt(
        type_token.literal,
        symbol_token.literal,
        shared,
        span=SourceSpan(
            start=start,
            end=symbol_token.span.end
        )
    )


def build_paramlist(node: ParsedNode) -> tuple[Param, ...]:
    return tuple(
        build_argtype(child)
        for child in flat_children(node)
        if isinstance(child, ParsedNode) and child.name == "argtype"
    )


def build_argtype(node: ParsedNode) -> Param:
    children = flat_children(node)

    name = expect_token(children[0], name="Symbol")
    type_name = expect_token(children[2], name="Type")

    return Param(name.literal, type_name.literal, span=SourceSpan(name.span.start, type_name.span.end))


def build_funcbody(node: ParsedNode) -> tuple[tuple[Param, ...], tuple[Stmt, ...]]:
    params: tuple[Param, ...] = ()
    body: tuple[Stmt, ...] = ()

    for child in flat_children(node):
        if isinstance(child, ParsedNode) and child.name == "paramlist":
            params = build_paramlist(child)

        elif isinstance(child, ParsedNode) and child.name == "wrap":
            body = build_wrap(child)

    return params, body    


def build_function(node: ParsedNode) -> FunctionParts:
    children = flat_children(node)
    name = expect_token(children[0], Definitions.Symbol.name)
    funcbody = expect_node(children[1], "funcbody")

    params, body = build_funcbody(funcbody)

    if len(body) > 0:
        end = body[-1].span.end
    elif len(params) > 0:
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


def build_functionstat(node: ParsedNode) -> FunctionDefStmt:
    warp = has_token(node, Definitions.Warp.name)
    function = find_first_node(node, "function")
    parts = build_function(function)

    start = None

    if warp:
        start = find_first_token(node, Definitions.Warp.name)

    return FunctionDefStmt(
        parts.name,
        parts.params,
        parts.body,
        warp,
        span=SourceSpan(
            start=start.span.start if start else parts.span.start,
            end=parts.span.end
        )
    )


def build_eventstat(node: ParsedNode) -> EventHandlerStmt:
    children = flat_children(node)
    name = expect_token(children[1], Definitions.Symbol.name)
    eventbody = expect_node(children[2], "args")
    wrap = expect_node(children[3], "wrap")

    args = build_varlist1(eventbody)
    wrap_nodes = build_wrap(wrap)

    if len(wrap_nodes) > 0:
        end = wrap_nodes[-1].span.end
    elif len(args) > 0:
        end = args[-1].span.end
    else:
        end = name.span.end
    
    return EventHandlerStmt(
        name.literal, 
        build_varlist1(eventbody),
        build_wrap(wrap),
        span=SourceSpan(
            start=name.span.start,
            end=end
        )
    )


def build_for_body(node: ParsedNode) -> ForBody:
    children = flat_children(node)

    if any(is_token(child, Definitions.In.name) for child in children):
        # var_node = next(search_nodes(children, "var"))
        var_node = build_var(next(
            i for i in children 
            if isinstance(i, ParsedNode) and i.name == "var"
            ))

        return ForInBody(var_node, span=var_node.span)
    
    equations = [
        i for i in children
        if isinstance(i, ParsedNode) and i.name == "equation"
    ]

    equation_start = build_equation(equations[0])
    equation_stop = build_equation(equations[1])
    step = build_equation(equations[2])

    return ForRangeBody(
        equation_start,
        equation_stop,
        step,
        span=SourceSpan(
            start=equation_start.span.start,
            end=step.span.end
        )
    )


def build_forstat(node: ParsedNode):
    children = flat_children(node)

    for_token = expect_token(children[0], "For")
    var_name = expect_token(children[1], "Symbol").literal

    forbody = expect_node(children[2], "forbody")
    wrap = expect_node(children[3], "wrap")

    body_spec = build_for_body(forbody)
    body = build_wrap(wrap)

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
            )
        )
    else:
        
        return ForInStmt(
            var_name,
            body_spec.iterable,
            body,
            span=SourceSpan(
                start=for_token.span.start,
                end=end
            )
        )


def build_ifstat(node: ParsedNode):
    children = flat_children(node)

    branches: list[IfBranch] = []
    else_body: tuple[Stmt, ...] = ()

    if_token = children[0]
    assert isinstance(if_token, Token) and if_token.kind.name == Definitions.If.name
    condition = build_equation(expect_node(children[1], "equation"))
    body = build_wrap(expect_node(children[2], "wrap"))
    i = 3

    branches.append(IfBranch(condition, body, span=SourceSpan(if_token.span.start, body[-1].span.end if len(body) > 0 else condition.span.end)))

    while i < len(children) and is_token(children[i], "ElseIf"):
        elseif_token = children[i]
        assert isinstance(elseif_token, Token)

        i += 1

        condition = build_equation(expect_node(children[i], "equation"))
        i += 1

        body = build_wrap(expect_node(children[i], "wrap"))
        i += 1

        branches.append(IfBranch(condition, body, span=SourceSpan(elseif_token.span.start, body[-1].span.end if len(body) > 0 else condition.span.end)))
    
    if i < len(children) and is_token(children[i], "Else"):
        i += 1
        else_body = build_wrap(expect_node(children[i], "wrap"))

    if len(else_body) > 0:
        end = else_body[-1].span.end
    else:
        # branches guaranteed to have at least one member.
        end = branches[-1].span.end
    

    return IfStmt(
        tuple(branches),
        else_body,
        span=SourceSpan(if_token.span.start, end)
    ) 


def build_whilestat(node: ParsedNode):
    while_token = find_first_token(node, "while")
    condition = find_first_node(node, "equation")
    body = find_first_node(node, "wrap")

    equation = build_equation(condition)
    wrap = build_wrap(body)

    return WhileStmt(
        equation,
        wrap,
        span=SourceSpan(
            while_token.span.start,
            wrap[-1].span.end if len(wrap) > 0 else equation.span.end
        )
    )


def build_wrap(node: ParsedNode):
    for child in flat_children(node):
        # if it passed the parser, we can sort of guarantee that the next node will be a chunk node,
        # but whatever...

        if isinstance(child, ParsedNode) and child.name == "chunk":
            return build_chunk(child)
    
    # chunks are allowed to be empty
    return ()


def build_laststat(node: ParsedNode) -> Stmt:
    children = flat_children(node)

    if has_token(node, Definitions.Break.name, children):
        break_token = find_first_token(node, Definitions.Break.name, children)
        return BreakStmt(span=break_token.span)
    
    if has_token(node, Definitions.Return.name):
        return_token = find_first_token(node, Definitions.Return.name, children)
        values: tuple[Expr, ...] = ()

        for child in children:
            if isinstance(child, ParsedNode) and child.name == "varlist1":
                values = build_varlist1(child)
        
        return ReturnStmt(values, span=SourceSpan(return_token.span.start, values[-1].span.end if len(values) > 0 else return_token.span.end))

    raise ValueError("that's not good :[")


def build_stat(node: ParsedNode) -> Stmt:
    for child in flat_children(node):
        if not isinstance(child, ParsedNode):
            continue

        match child.name:
            case "wrap":
                wrap = build_wrap(child)

                bracket_tokens = [
                    child
                    for child in flat_children(child)
                    if isinstance(child, Token)
                    and child.kind in {
                        Definitions.OpenSquareBracket,
                        Definitions.CloseSquareBracket,
                    }
                ]
                
                return BlockStmt(wrap, span=SourceSpan(bracket_tokens[0].span.start, bracket_tokens[-1].span.end))
            
            case "whilestat":
                return build_whilestat(child)
            
            case "ifstat":
                return build_ifstat(child)
            
            case "forstat":
                return build_forstat(child)
            
            case "functionstat":
                return build_functionstat(child)
            
            case "eventstat":
                return build_eventstat(child)
            
            case "vardefstat":
                return build_vardefstat(child)
            
            case "varassignstat":
                return build_varassignstat(child)
            
            case "functioncall":
                return build_functioncall(child)

            case _:
                pass
    
    raise ValueError(f"this is very bad: {node}")


def build_chunk(node: ParsedNode):
    statements: list[Stmt] = []

    for child in flat_children(node):
        if is_token(child, name=GenericRules.StatementSeperator.name):
            continue
        if is_token(child, name=GenericRules.EOF.name):
            continue

        if isinstance(child, ParsedNode):
            if child.name == "stat":
                statements.append(build_stat(child))
            elif child.name == "laststat":
                statements.append(build_laststat(child))
    
    return tuple(statements)


def build_program(node: ParsedNode) -> Program:
    children = flat_children(node)

    variable_definitions = tuple(
        build_vardefstat(child)
        for child in children
        if isinstance(child, ParsedNode) and child.name == "vardefstat"
    )

    chunk = build_chunk(next(
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

    return Program(variable_definitions + chunk, span=SourceSpan(start, end))


def build_ast(tree: ParsedChild) -> Program:
    if isinstance(tree, Token):
        raise ValueError("gang what do you expect me to do with this")

    assert tree.name == "program", f"give me a root node. i got {tree.name} instead :/"

    return build_program(tree)