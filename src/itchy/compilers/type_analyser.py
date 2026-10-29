"""
Splitting some of the responsibility of analysis from the assembler
to this.
"""


from dataclasses import dataclass
from enum import Enum, StrEnum, auto
from itchy.shared_templates import VariableTypes
from itchy.itch_ast import (
    AssetExpr, AssignStmt, BinaryOpExpr, BlockStmt, BoolExpr, BreakStmt,
    EventHandlerStmt, Expr, ForInStmt, ForRangeStmt, ForeverStmt,
    FunctionCallExpr, FunctionCallStmt, FunctionDefStmt, IfStmt,
    NumberExpr, Program, ReturnStmt, Stmt, StringExpr, TableExpr,
    UnaryOpExpr, VarDefStmt, VarExpr, WhileStmt
)


StrOptional = str | None

@dataclass
class Context:
    proc: str | None = None


class TypeInferer:
    def __init__(self) -> None:
        self.variables: dict[str, VariableTypes] = {}
        self.parameters: dict[tuple[str, str], VariableTypes] = {}
        self.procedures: dict[str, FunctionDefStmt] = {}

    def _get_var_type(self, var_name: str, context: Context) -> VariableTypes:
        if context.proc != None:
            if (var_name, context.proc) in self.parameters:
                return self.parameters[(var_name, context.proc)]
            elif var_name in self.variables:
                return self.variables[var_name]
        else:
            if var_name in self.variables:
                return self.variables[var_name]
        return VariableTypes.UNKNOWN

    def handle_statement(self, stmt: Stmt, context: Context) -> set[VariableTypes]:
        types: set[VariableTypes] = set()

        """
        We want to go through the bodies in case they contain a 'return', which will
        affect whatever function definition we're currently iterating through.

        Some don't, as they don't contain bodies.
        """
        match stmt:
            case BlockStmt():
                types.update(self.handle_body(stmt.body, context))
            case VarDefStmt():
                # only exists here for completion's sake
                pass
                # self.variables[stmt.name] = VariableTypes(stmt.type_name)
            case AssignStmt():
                self.handle_expr(stmt.value, context)
            case ForeverStmt():
                types.update(self.handle_body(stmt.body, context))
            case IfStmt():
                for branch in stmt.branches:
                    types.update(self.handle_body(branch.body, context))
                types.update(self.handle_body(stmt.else_body, context))
            case WhileStmt():
                types.update(self.handle_body(stmt.body, context))
            case ForRangeStmt():
                types.update(self.handle_body(stmt.body, context))
            case ForInStmt():
                types.update(self.handle_body(stmt.body, context))
            case EventHandlerStmt():
                types.update(self.handle_body(stmt.body, context))
            case FunctionDefStmt():
                types.update(self.handle_body(stmt.body, Context(stmt.name)))
                # stmt.inferred_types = types.copy()
            case FunctionCallStmt():
                for i in stmt.args:
                    self.handle_expr(i, context);
            case BreakStmt():
                pass
            case ReturnStmt():
                if len(stmt.values) == 0:
                    types.add(VariableTypes.NOTHING)
                else:
                    for i in stmt.values:
                        types.update(self.handle_expr(i, context))
            case _:
                raise TypeError("NOTHING MATCHED?!?!?!?!?")

        return types


    def handle_expr(self, expr: Expr, context: Context) -> set[VariableTypes]:
        types: set[VariableTypes] = set()

        # FIXME: update traversal to include expression's children
        match expr:
            case NumberExpr():
                types = {VariableTypes.NUMBER}
            case StringExpr():
                types = {VariableTypes.STRING}
            case AssetExpr():
                types = {VariableTypes.STRING}
            case BoolExpr():
                types = {VariableTypes.BOOL}
            case UnaryOpExpr():
                match expr.op:
                    case "not":
                        types = {VariableTypes.BOOL}
                    case "-":
                        types = {VariableTypes.NUMBER}
                    case _:
                        types = {VariableTypes.UNKNOWN}
                self.handle_expr(expr.value, context)
            case BinaryOpExpr():
                match expr.op:
                    case ">" | "<" | "and" | "or" | "in":
                        types = {VariableTypes.BOOL}
                    case "+" | "-" | "*" | "/":
                        types = {VariableTypes.NUMBER}
                    case _:
                        types = {VariableTypes.UNKNOWN}
                self.handle_expr(expr.left, context)
                self.handle_expr(expr.right, context)
            case FunctionCallExpr():
                for arg in expr.args:
                    self.handle_expr(arg, context)
                    # print(arg.return_types)

                types = self.procedure_return_types.get(expr.callee, {VariableTypes.UNKNOWN}).copy()
            case VarExpr():
                if expr.ref.slice_expr:
                    var_type = self._get_var_type(expr.ref.root, context)
                    if var_type == VariableTypes.LIST:
                        self.handle_expr(expr.ref.slice_expr, context)
                        types = {VariableTypes.VAR}
                    elif var_type != VariableTypes.UNKNOWN:
                        types = {VariableTypes.STRING}
                else:
                    # expr.return_types = 
                    types = {self._get_var_type(expr.ref.root, context)}
            case TableExpr() :
                types = {VariableTypes.LIST}
                for value in expr.values: 
                    self.handle_expr(value, context)
            case _:
                types = {VariableTypes.UNKNOWN}
                raise TypeError("WHAT")

        # raise TypeError("NOTHING MATCHED?!?!??!?!?!")
        expr.return_types = types
        return types

    def _collect_procedures(self, program: Program):
        for stmt in program.body:
            if isinstance(stmt, FunctionDefStmt):
                self.procedures[stmt.name] = stmt
                for parameter in stmt.params:
                    self.parameters[(parameter.name, stmt.name)] = VariableTypes(parameter.type_name)
                # self.handle_body(stmt.body, Context(stmt.name))

    def handle_body(self, body: tuple[Stmt, ...], context: Context) -> set[VariableTypes]:
        types: set[VariableTypes] = set()
        for stmt in body:
            types.update(self.handle_statement(stmt, context))
        return types

    def handle_program(self, program: Program):
        self.procedures = {}
        self.parameters = {}
        self.procedures = {}
        self._collect_procedures(program)

        # collect global variables before inference.
        for stmt in program.body:
            if isinstance(stmt, VarDefStmt):
                self.variables[stmt.name] = VariableTypes(stmt.type_name)

        # initialise explicitly annotated return types.
        self.procedure_return_types = {
            name: {VariableTypes(t) for t in stmt.type_annotation}
            for name, stmt in self.procedures.items()
        }

        while True:
            changed = False

            for name, stmt in self.procedures.items():
                inferred = self.handle_body(stmt.body, Context(name))

                # user defined annotations should take precedance
                # we don't really care if they don't match; let the compiler deal
                # with it.
                if stmt.type_annotation:
                    continue

                current = self.procedure_return_types[name]

                # print(inferred, stmt.name)

                if not inferred.issubset(current):
                    current.update(inferred)
                    changed = True

                self.procedures[name].inferred_types = current

            if not changed:
                break

        # for i in self.procedures.values():
        #     print(i.inferred_types, i.name)

        self.handle_body(program.body, Context())


