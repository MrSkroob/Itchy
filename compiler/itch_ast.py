from dataclasses import dataclass
from parser import ParsedNode, Token, Sequence, Repeat, OptionalNode, Alternative
from tokenizer import GenericRules, Definitions
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
class AssignStmt(Stmt):
    target: "VarRef"
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
class VarRef(ASTNode):
    root: str
    fields: tuple[str, ...] = ()
    slice_expr: Expr | None = None


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


ForBody = ForRangeBody | ForInBody

# the reason why the main algorithm is not a class in this case is because in
# Parser.py, it has an interface .read() where it needs an existing tokenizer, pre-build parse tree, etc.
# it was simply better for the namespace for it to be neatly inside a class.

# this doesn't have any internal dependencies (really).


def is_token(
    x: ParsedChild,
    name: str | None = None,
    literal: str | None = None
) -> bool:
    if not type(x) is Token:
        return False

    if name is not None and x.kind.name != name:
        return False

    if literal is not None and x.literal != literal:
        return False

    return True


def is_node(node: ParsedChild, name: str | None = None) -> bool:
    return type(node) is ParsedNode and (name is None or node.name == name)


def expect_node(node: ParsedChild, name: str) -> ParsedNode:
    if not is_node(node, name):
        raise ValueError(f"Expected `{name}` got {node}")
    
    assert type(node) is ParsedNode

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
        if type(child) is ParsedNode and child.__class__ in {Sequence, Repeat, OptionalNode, Alternative}:
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
            assert type(child) is ParsedNode
            return child
    
    raise ValueError(f"No child found with name {name}")


def all_nodes(node: ParsedNode, name: str):
    return [
        i for i in flat_children(node)
        if type(i) is ParsedNode and i.name == name
    ]


def first_token(node: ParsedNode, name: str):
    for child in flat_children(node):
        if is_token(child, name):
            assert type(child) is Token
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

def build_forstat(node: ParsedNode):
    children = flat_children(node)

    expect_token(children[0], name="For")
    var_name = expect_token(children[1], name="Symbol").literal

    forbody = expect_node(children[2], "forbody")
    wrap = expect_node(children[3], "wrap")

    body_spec = build_forbody(forbody)
    body = build_wrap(wrap)

    if isinstance(body_spec, ForRangeBody):
        return ForRangeStmt(
            var_name,
            body_spec.start,
            body_spec.stop,
            body_spec.step,
            body
        )
    elif isinstance(body_spec, ForInBody):
        return ForInStmt(
            var_name,
            body_spec.iterable,
            body
        )
    raise ValueError("NUH UH")

def build_ifstat(node: ParsedNode):
    children = flat_children(node)

    branches: list[IfBranch] = []
    else_body: tuple[Stmt, ...] = ()

    expect_token(children[0], "If")
    condition = build_equation(expect_node(children[1], "equation"))
    body = build_wrap(expect_node(children[2], "wrap"))
    i = 3

    branches.append(IfBranch(condition, body))

    while i < len(children) and is_token(children[i], name="ElseIf"):
        i += 1

        condition = build_equation(expect_node(children[i], "equation"))
        i += 1

        body = build_wrap(expect_node(children[i], "wrap"))
        i += 1

        branches.append(IfBranch(condition, body))
    
    if i < len(children) and is_token(children[i], name="Else"):
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
    
    raise ValueError(f"No valid statements in node {node}")


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

    assert tree.name == "program", "give me a root node"

    return build_program(tree)