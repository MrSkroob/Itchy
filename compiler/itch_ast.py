from dataclasses import dataclass
from parser import ParsedNode, Token, Sequence, Repeat, OptionalNode, Alternative
from tokenizer import GenericRules, Definitions
from typing import Callable
import ast


ParsedChild = ParsedNode | Token[Definitions]


class ASTNode:
    pass


class Stmt(ASTNode):
    pass


class Expr(ASTNode):
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
class FunctionDefStmt(Stmt):
    name: str
    params: tuple["Param", ...]
    body: tuple[Stmt, ...]
    no_refresh: bool = False


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
class CallStmt(Stmt):
    callee: Expr
    arg_groups: tuple[tuple[Expr, ...], ...]


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
    callee: Expr
    arg_groups: tuple[tuple[Expr, ...], ...]


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


@dataclass(frozen=True)
class ForInBody:
    iterable: VarRef


@dataclass(frozen=True)
class FunctionParts:
    name: str
    params: tuple[Param, ...]
    body: tuple[Stmt, ...]


@dataclass(frozen=True)
class AssignAction:
    value: Expr


@dataclass(frozen=True)
class CallAction:
    arg_groups: tuple[tuple[Expr, ...], ...]


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


def find_first_node(node: ParsedNode, name: str):
    """
    Strictly finds the first occuring node with said name
    """
    for child in flat_children(node):
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


def first_token(node: ParsedNode, name: str):
    for child in flat_children(node):
        if is_token(child, name):
            assert isinstance(child, Token)
            return child
    
    raise ValueError(f"No token found with name {name}")


def has_token(node: ParsedNode, name: str):
    return any(is_token(i, name) for i in flat_children(node))


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
        expr = BinaryOpExpr(
            left=expr,
            op=op,
            right=operand_builder(operand),
        )

    return expr


def build_unary(node: ParsedNode) -> Expr:
    children = flat_children(node)

    op: str | None = None
    primary: ParsedNode | None = None

    for child in children:
        if is_token(child, name="Unop"):
            assert isinstance(child, Token)
            op = child.literal

        elif isinstance(child, ParsedNode) and child.name == "primary":
            primary = child

    if primary is None:
        raise ValueError(f"need something to work with big dawg: {node!r}")

    expr = build_primary(primary)

    if op is None:
        return expr

    return UnaryOpExpr(op, expr)


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
            return BoolExpr(child.literal.lower() == "true")

        if is_token(child, name="Number"):
            assert isinstance(child, Token)
            return NumberExpr(float(child.literal))

        if is_token(child, name="String"):
            assert isinstance(child, Token)
            return StringExpr(parse_string(child.literal))

        if isinstance(child, ParsedNode) and child.name == "tableconstructor":
            return build_tableconstructor(child)

    var_node: ParsedNode | None = None
    functioncall_node: ParsedNode | None = None

    for child in children:
        if isinstance(child, ParsedNode) and child.name == "var":
            var_node = child

        elif isinstance(child, ParsedNode) and child.name == "functioncall":
            functioncall_node = child

    if var_node is not None:
        var_expr = VarExpr(build_var(var_node))

        if functioncall_node is not None:
            return FunctionCallExpr(
                callee=var_expr,
                arg_groups=build_functioncall(functioncall_node),
            )

        return var_expr

    raise ValueError(f"this ain't a literal g: {node!r}")


def build_var(node: ParsedNode) -> VarRef:
    # children = flat_children(node)

    symbols: str = first_token(node, Definitions.Symbol.name).literal
    slice_expr: Expr | None = None
    has_slice = has_node(node, "slice")
    if has_slice:
        slice_expr = build_slice(find_first_node(node, "slice"))
    # slice_expr: Expr | None = None

    # for child in children:
    #     # this represents the dot operator
    #     # i.e. var1.var2.var3
    #     # thing is, scratch doesn't really support this stuff, 
    #     # so i have no idea why the language supports this. 
    #     # i might just remove this functionality outright?
    #     if is_token(child, Definitions.Symbol.name):
    #         assert isinstance(child, Token)
    #         symbols.append(child.literal)

    #     elif isinstance(child, ParsedNode) and child.name == "slice":
    #         slice_expr = build_slice(child)

    if not symbols:
        raise ValueError(f"how u gonna want a variable with no name: {node!r}")

    return VarRef(
        root=symbols[0],
        slice_expr=slice_expr,
    )


def build_slice(node: ParsedNode) -> Expr:
    return build_equation(find_first_node(node, "equation"))


def build_tableconstructor(node: ParsedNode) -> TableExpr:
    for child in flat_children(node):
        if isinstance(child, ParsedNode) and child.name == "explist1":
            return TableExpr(build_explist1(child))

    return TableExpr(())


def build_explist1(node: ParsedNode) -> tuple[Expr, ...]:
    values: list[Expr] = []

    for child in flat_children(node):
        if isinstance(child, ParsedNode) and child.name == "equation":
            values.append(build_equation(child))

        elif isinstance(child, ParsedNode) and child.name == "explist1":
            values.extend(build_explist1(child))

    return tuple(values)


def build_varlist1(node: ParsedNode) -> tuple[Expr, ...]:
    values: list[Expr] = []

    for child in flat_children(node):
        if isinstance(child, ParsedNode) and child.name == "literals":
            values.append(build_literals(child))

    return tuple(values)


def build_namelist(node: ParsedNode) -> tuple[str, ...]:
    return tuple(
        i.literal for i in flat_children(node) if is_token(i, "Symbol") and isinstance(i, Token)
    )


def build_args(node: ParsedNode) -> tuple[Expr, ...]:
    for child in flat_children(node):
        if isinstance(child, ParsedNode) and child.name == "varlist1":
            return build_varlist1(child)

    return ()


def build_assignorcall(node: ParsedNode) -> AssignOrCall:
    children = flat_children(node)

    if any(is_token(i, name="Assign") for i in children):
        equation = find_first_node(node, "equation")
        return AssignAction(build_equation(equation))

    functioncall = find_first_node(node, "functioncall")
    return CallAction(build_functioncall(functioncall))


def build_functioncall(node: ParsedNode) -> tuple[tuple[Expr, ...], ...]:
    return tuple(
        build_args(child)
        for child in flat_children(node)
        if isinstance(child, ParsedNode) and child.name == "args"
    )


def build_varassignstat(node: ParsedNode) -> Stmt:
    var_node = find_first_node(node, "var")
    action_node = find_first_node(node, "assignorcall")

    target = build_var(var_node)
    action = build_assignorcall(action_node)

    if isinstance(action, AssignAction):
        return AssignStmt(
            target,
            action.value,
        )

    return CallStmt(
        VarExpr(target),
        action.arg_groups,
    )


def build_vardefstat(node: ParsedNode) -> VarDefStmt:
    shared = has_token(node, "Shared")

    type_token = first_token(node, "Type")
    symbol_token = first_token(node, "Symbol")

    return VarDefStmt(
        type_token.literal,
        symbol_token.literal,
        shared,
    )


def build_paramlist(node: ParsedNode) -> tuple[Param, ...]:
    return tuple(
        build_argtype(child)
        for child in flat_children(node)
        if isinstance(child, ParsedNode) and child.name == "argtype"
    )


def build_argtype(node: ParsedNode) -> Param:
    children = flat_children(node)

    name = expect_token(children[0], name="Symbol").literal
    type_name = expect_token(children[2], name="Type").literal

    return Param(name, type_name)


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
    name = expect_token(children[0], Definitions.Symbol.name).literal
    funcbody = expect_node(children[1], "funcbody")

    params, body = build_funcbody(funcbody)

    return FunctionParts(
        name,
        params,
        body
    ) 


def build_functionstat(node: ParsedNode) -> FunctionDefStmt:
    no_refresh = has_token(node, Definitions.NoRefresh.name)
    function = find_first_node(node, "function")
    parts = build_function(function)

    return FunctionDefStmt(
        parts.name,
        parts.params,
        parts.body,
        no_refresh
    )


def build_for_body(node: ParsedNode) -> ForBody:
    children = flat_children(node)

    if any(is_token(child, Definitions.In.name) for child in children):
        # var_node = next(search_nodes(children, "var"))
        var_node = next(
            i for i in children 
            if isinstance(i, ParsedNode) and i.name == "var"
            )

        return ForInBody(build_var(var_node))
    
    equations = [
        i for i in children
        if isinstance(i, ParsedNode) and i.name == "equation"
    ]

    return ForRangeBody(
        build_equation(equations[0]),
        build_equation(equations[1]),
        build_equation(equations[3])
    )


def build_forstat(node: ParsedNode):
    children = flat_children(node)

    expect_token(children[0], "For")
    var_name = expect_token(children[1], "Symbol").literal

    forbody = expect_node(children[2], "forbody")
    wrap = expect_node(children[3], "wrap")

    body_spec = build_for_body(forbody)
    body = build_wrap(wrap)

    if isinstance(body_spec, ForRangeBody):
        return ForRangeStmt(
            var_name,
            body_spec.start,
            body_spec.stop,
            body_spec.step,
            body
        )
    else:
        return ForInStmt(
            var_name,
            body_spec.iterable,
            body
        )


def build_ifstat(node: ParsedNode):
    children = flat_children(node)

    branches: list[IfBranch] = []
    else_body: tuple[Stmt, ...] = ()

    expect_token(children[0], "If")
    condition = build_equation(expect_node(children[1], "equation"))
    body = build_wrap(expect_node(children[2], "wrap"))
    i = 3

    branches.append(IfBranch(condition, body))

    while i < len(children) and is_token(children[i], "ElseIf"):
        i += 1

        condition = build_equation(expect_node(children[i], "equation"))
        i += 1

        body = build_wrap(expect_node(children[i], "wrap"))
        i += 1

        branches.append(IfBranch(condition, body))
    
    if i < len(children) and is_token(children[i], "Else"):
        i += 1
        else_body = build_wrap(expect_node(children[i], "wrap"))

    return IfStmt(
        tuple(branches),
        else_body
    ) 


def build_whilestat(node: ParsedNode):
    condition = find_first_node(node, "equation")
    body = find_first_node(node, "wrap")

    return WhileStmt(
        build_equation(condition),
        build_wrap(body)
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

    if any(is_token(i, Definitions.Break.name) for i in children):
        return BreakStmt()
    
    if any(is_token(i, Definitions.Return.name) for i in children):
        values: tuple[Expr, ...] = ()

        for child in children:
            if isinstance(child, ParsedNode) and child.name == "explist1":
                values = build_explist1(child)
        
        return ReturnStmt(values)

    raise ValueError("that's not good :[")


def build_stat(node: ParsedNode) -> Stmt:
    for child in flat_children(node):
        if not isinstance(child, ParsedNode):
            continue

        match child.name:
            case "wrap":
                return BlockStmt(build_wrap(child))
            
            case "whilestat":
                return build_whilestat(child)
            
            case "ifstat":
                return build_ifstat(child)
            
            case "forstat":
                return build_forstat(child)
            
            case "functionstat":
                return build_functionstat(child)
            
            case "vardefstat":
                return build_vardefstat(child)
            
            case "varassignstat":
                return build_varassignstat(child)
            
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


def build_program(node: ParsedNode):
    chunk = find_first_node(node, "chunk")
    return Program(build_chunk(chunk))


def build_ast(tree: ParsedChild) -> Program:
    if isinstance(tree, Token):
        raise ValueError("gang what do you expect me to do with this")

    assert tree.name == "program", f"give me a root node. i got {tree.name} instead :/"

    return build_program(tree)