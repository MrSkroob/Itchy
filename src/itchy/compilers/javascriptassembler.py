from dataclasses import dataclass, replace, is_dataclass, fields
import json
from pathlib import Path
from typing import Iterable, Mapping

from itchy.itch_ast import (
    AssetExpr, AssignStmt, BinaryOpExpr, BlockStmt, BoolExpr, BreakStmt,
    EventHandlerStmt, Expr, ForInStmt, ForRangeStmt, ForeverStmt,
    FunctionCallExpr, FunctionCallStmt, FunctionDefStmt, IfStmt,
    NumberExpr, Program, ReturnStmt, Stmt, StringExpr, TableExpr,
    UnaryOpExpr, VarDefStmt, VarExpr, VarRef, WhileStmt,
)
from itchy.errors import CompilerError
from itchy.compilers.sharedmethods import SharedMethods

from itchy.scratch_blocks import SCRATCH_BLOCKS, YIELDABLES


TABS = "    "

OP_TO_JAVA_OP_MAP = {
    "not": "!",
    "and": "&&",
    "or": "||",
    "==": "===",
}

EVENT_NAMES = {
    "event_whenflagclicked": "GREEN_FLAG",
    "event_whenkeypressed": "KEY_STROKE",
    "event_whenbackdropswitchesto": "BACKDROP_CHANGE",
    "event_whengreaterthan": "LOUDNESS_EXCEED",
    "event_whenbroadcastreceived": "MESSAGE",
    "control_start_as_clone": "CLONE",
    "event_whenthisspriteclicked": "SPRITE_CLICK",
}


@dataclass()
class ProcedureInfo:
    warp: bool
    # name: str
    # params_list: str
    # definition_indent: int
    # definition_line: int | None
    yieldable: bool=False


@dataclass(frozen=True)
class SpriteData:
    name: str


@dataclass(frozen=True)
class Context:
    sprite_name: SpriteData
    proc_info: ProcedureInfo
    locals: frozenset[str] = frozenset()


class JavascriptAssembler(SharedMethods):
    """Emit an ES module registering Itchy programs with an existing VM.

    The resulting output.js exports registerProject(vm, contexts, EventType).
    contexts is a mapping of sprite names to ExecutionContext objects.
    """

    def __init__(self) -> None:
        # Kept for compatibility; this module is wired to the VM by its caller.
        self.prepare()

    def prepare(self) -> None:
        self.indent = 0
        self.code: list[str] = []
        self.global_variables: set[str] = set()
        self.procedures: dict[str, ProcedureInfo] = {}

    def add_code(self, code: str = "") -> None:
        self.code.append((TABS * self.indent) + code if code else "")

    @staticmethod
    def js_string(value: str) -> str:
        return json.dumps(value, ensure_ascii=False)

    def variable_map(self, name: str, context: Context) -> str:
        if context.sprite_name.name.lower() == "stage" or name in self.global_variables:
            return "context.backdrop.variables"
        return "context.sprite.variables"

    # def make_proc_yieldable(self, proc_info: ProcedureInfo):
    #     if proc_info.yieldable:
    #         return

    #     proc_info.yieldable = True

    #     if proc_info.definition_line is None:
    #         return

    #     self.code[proc_info.definition_line] = \
    #         f"{proc_info.definition_indent * TABS}function* {proc_info.name}(context{proc_info.params_list}) {{"
    #     proc_info.definition_line = None

    def emit_expr(self, expr: Expr, context: Context) -> str:
        expr = self.fold_expr(expr)

        match expr:
            case NumberExpr():
                return str(expr.value)
            case StringExpr():
                return self.js_string(str(expr.value))
            case AssetExpr():
                return self.emit_expr(expr.args[0], context)
            case BoolExpr():
                return "true" if expr.value else "false"
            # case VarExpr():
            #     name = expr.ref.root
            #     if name in context.locals:
            #         return name
            #     return f"{self.variable_map(name, context)}.get({self.js_string(name)})"
            case UnaryOpExpr():
                operator = OP_TO_JAVA_OP_MAP.get(expr.op, expr.op)
                return f"({operator}{self.emit_expr(expr.value, context)})"
            case BinaryOpExpr():
                operator = OP_TO_JAVA_OP_MAP.get(expr.op, expr.op)

                if expr.op == "in":
                    return (f"({self.emit_expr(expr.left, context)}.includes("
                        f"{self.emit_expr(expr.right, context)}))")

                return (f"({self.emit_expr(expr.left, context)} {operator} "
                        f"{self.emit_expr(expr.right, context)})")
            case FunctionCallExpr():
                return self.emit_function_call(expr, context)
            case TableExpr():
                return f"[{', '.join(self.emit_expr(v, context) for v in expr.values)}]"
            case VarExpr():
                return self.emit_var_ref(expr.ref, context)
            case _:
                raise NotImplementedError(f"Expression not supported: {type(expr).__name__}")

    def emit_var_ref(self, expr: VarRef, context: Context):
        name = expr.root
        if name in context.locals:
            return name
        if expr.slice_expr:
            return f"{self.variable_map(name, context)}.get({self.js_string(name)})[{self.emit_expr(expr.slice_expr, context)}]"
        return f"{self.variable_map(name, context)}.get({self.js_string(name)})"

    def emit_function_call(self, expr: FunctionCallExpr | FunctionCallStmt, context: Context):
        is_scratch_block = expr.callee in SCRATCH_BLOCKS
        args = ", ".join(self.emit_expr(arg, context) for arg in expr.args)
        comma = ", " if args else ""

        if is_scratch_block:
            command = f"ScratchBlock.{expr.callee}(context{comma}{args})"
            yieldable = expr.callee in YIELDABLES
        else:
            command = f"{expr.callee}(context{comma}{args})"
            yieldable = self.procedures[expr.callee].yieldable

        if yieldable:
            return f"(yield* {command})"

        return command

    def emit_statements(self, statements: Iterable[Stmt], context: Context) -> None:
        self.indent += 1
        try:
            for stmt in statements:
                self.emit_stmt(stmt, context)
        finally:
            self.indent -= 1

    def emit_stmt(self, stmt: Stmt, context: Context) -> None:
        match stmt:
            case BlockStmt(body=body):
                self.add_code("{")
                self.emit_statements(body, context)
                self.add_code("}")

            case VarDefStmt():
                name = stmt.name
                defaults = {"list": "[]", "bool": "false", "string": '""'}
                value = defaults.get(stmt.type_name.lower(), "0")
                if stmt.shared or context.sprite_name.name.lower() == "stage":
                    target = "context.backdrop.variables"
                else:
                    target = "context.sprite.variables"
                self.add_code(f"{target}.set({self.js_string(name)}, {value});")

            case AssignStmt():
                name = stmt.target.root
                value = self.emit_expr(stmt.value, context)
                if name in context.locals:
                    self.add_code(f"{name} = {value};")
                else:
                    target = self.variable_map(name, context)
                    self.add_code(f"{target}.set({self.js_string(name)}, {value});")

            case ForeverStmt():
                self.add_code("while (true) {")
                self.emit_statements(stmt.body, context)
                self.emit_frame_yield(context)
                self.add_code("}")

            case IfStmt():
                for index, branch in enumerate(stmt.branches):
                    keyword = "if" if index == 0 else "else if"
                    condition = self.emit_expr(branch.condition, context)
                    self.add_code(f"{keyword} ({condition}) {{")
                    self.emit_statements(branch.body, context)
                    self.add_code("}")
                if stmt.else_body:
                    self.add_code("else {")
                    self.emit_statements(stmt.else_body, context)
                    self.add_code("}")

            case WhileStmt():
                self.add_code(f"while ({self.emit_expr(stmt.condition, context)}) {{")
                self.emit_statements(stmt.body, context)
                self.emit_frame_yield(context)
                self.add_code("}")

            case ForRangeStmt():
                variable = stmt.variable
                start = self.emit_expr(stmt.start, context)
                stop = self.emit_expr(stmt.stop, context)
                step = self.emit_expr(stmt.step, context)
                # Evaluate bounds once and handle both positive and negative steps.
                self.add_code(
                    f"for (let {variable} = {start}, __end = {stop}, __step = {step}; "
                    f"__step !== 0 && (__step > 0 ? {variable} < __end : {variable} > __end); "
                    f"{variable} += __step) {{"
                )
                loop_context = replace(context, locals=context.locals | {variable})
                self.emit_statements(stmt.body, loop_context)
                self.emit_frame_yield(context)
                self.add_code("}")

            case ForInStmt():
                variable = stmt.variable
                iterable = self.emit_var_ref(stmt.iterable, context)
                self.add_code(f"for (let {variable} of {iterable}) {{")
                loop_context = replace(context, locals=context.locals | {variable})
                self.emit_statements(stmt.body, loop_context)
                self.emit_frame_yield(context)
                self.add_code("}")

            case EventHandlerStmt():
                self.emit_event_stmt(stmt, context)

            case FunctionDefStmt():
                name = stmt.name
                params = [p.name for p in stmt.params]
                params_list = (", " + ", ".join(params)) if params else ""

                proc_info = self.procedures[name]

                keyword = "function*" if proc_info.yieldable else "function"

                self.add_code(f"{keyword} {name}(context{params_list}) {{")

                function_context = replace(
                    context,
                    proc_info=proc_info,
                    locals=frozenset(params),
                )

                self.emit_statements(stmt.body, function_context)
                self.add_code("}")

            case FunctionCallStmt():
                self.add_code(f"{self.emit_function_call(stmt, context)};")

            case BreakStmt():
                raise NotImplementedError("Sorry, not implemented.")

            case ReturnStmt():
                values = stmt.values
                if not values:
                    self.add_code("return;")
                elif len(values) == 1:
                    self.add_code(f"return {self.emit_expr(values[0], context)};")
                else:
                    self.add_code(
                        "return [" + ", ".join(self.emit_expr(v, context) for v in values) + "];"
                    )

            case _:
                raise NotImplementedError(f"Statement not supported: {type(stmt).__name__}")

    def emit_frame_yield(self, context: Context) -> None:
        # Warp procedures do *not* pause at the end of each loop iteration.
        if not context.proc_info.warp:
            self.indent += 1
            self.add_code("yield { type: 'waitOneFrame' };")
            self.indent -= 1
            # self.make_proc_yieldable(context.proc_info)

    def emit_event_stmt(self, stmt: EventHandlerStmt, context: Context) -> None:
        event_name = EVENT_NAMES.get(stmt.name)
        if event_name is None:
            raise NotImplementedError(f"Unknown event: {stmt.name}")

        params = ", ".join(self.emit_expr(param, context) for param in stmt.params)
        self.add_code("vm.registerEvent(")
        self.indent += 1
        self.add_code(f"EventType.{event_name},")
        self.add_code(f"[{params}],")
        self.add_code("{")
        self.indent += 1
        self.add_code("context,")
        self.add_code("script: function* (context) {")

        script_context = replace(context, proc_info=ProcedureInfo(warp=False, 
                                                                #  name=stmt.name,
                                                                #  params_list="",
                                                                #  definition_indent=self.indent, 
                                                                #  definition_line=None
                                                                 ),
                                                                 locals=frozenset()
                                )
        self.emit_statements(stmt.body, script_context)
        self.add_code("}")
        self.indent -= 1
        self.add_code("}")
        self.indent -= 1
        self.add_code(");")

    # def emit_program(self, program: Program, context: Context) -> None:
        

    def collect_procedures(self, program: Program) -> None:
        self.procedures.clear()

        for stmt in program.body:
            if isinstance(stmt, FunctionDefStmt):
                self.procedures[stmt.name] = ProcedureInfo(
                    warp=stmt.warp
                )

    def analyse_yieldability(self, program: Program) -> None:
        dependencies: dict[str, set[str]] = {
            name: set()
            for name in self.procedures
        }

        def inspect(node: tuple[Stmt, ...] | Stmt, owner: str) -> bool:
            # Nested definitions are separate execution contexts.
            if isinstance(node, (FunctionDefStmt, EventHandlerStmt)):
                return False

            directly_yields = False

            # Non-warp loops yield once per iteration.
            if isinstance(node, (
                WhileStmt,
                ForeverStmt,
                ForRangeStmt,
                ForInStmt,
            )):
                directly_yields = not self.procedures[owner].warp

            # Function calls
            if isinstance(node, (FunctionCallExpr, FunctionCallStmt)):
                callee = node.callee

                if callee in SCRATCH_BLOCKS:
                    if callee in YIELDABLES:
                        directly_yields = True

                elif callee in self.procedures:
                    dependencies[owner].add(callee)

            # Recursively inspect children.
            if isinstance(node, tuple):
                for child in node:
                    if inspect(child, owner):
                        directly_yields = True

            elif is_dataclass(node) and not isinstance(node, type):
                for field in fields(node):
                    child = getattr(node, field.name)

                    if inspect(child, owner):
                        directly_yields = True

            return directly_yields

        # First pass: find direct yields and function dependencies.
        for stmt in program.body:
            if not isinstance(stmt, FunctionDefStmt):
                continue

            self.procedures[stmt.name].yieldable = inspect(
                stmt.body,
                stmt.name
            )

        # Second pass: propagate yieldability through dependencies.
        changed = True

        while changed:
            changed = False

            for name, proc_info in self.procedures.items():
                if proc_info.yieldable:
                    continue

                if any(
                    self.procedures[callee].yieldable
                    for callee in dependencies[name]
                ):
                    proc_info.yieldable = True
                    changed = True

    def assemble(
        self,
        programs: Mapping[str, Program],
        project_directory: str | Path,
        _: str | Path | None = None,
    ) -> Path:
        project_directory = Path(project_directory)
        if not project_directory.is_dir():
            raise CompilerError(
                f"Project directory '{project_directory}' does not exist.", None
            )

        self.prepare()
        # Find shared/stage variables before compiling any sprite references.
        for sprite_name, program in programs.items():
            for stmt in program.body:
                if isinstance(stmt, VarDefStmt) and (
                    stmt.shared or sprite_name.lower() == "stage"
                ):
                    self.global_variables.add(stmt.name)

        self.add_code("export function registerProject(vm, contexts, EventType, ScratchBlock) {")

        for sprite_name, program in programs.items():
            # self.procedures = {}
            self.collect_procedures(program)
            self.analyse_yieldability(program)
            context = Context(SpriteData(sprite_name), ProcedureInfo(warp=False, 
                                                                    #  name=sprite_name,
                                                                    #  params_list="",
                                                                    #  definition_indent=self.indent, 
                                                                    #  definition_line=None
                                                                     ))
            self.indent += 1
            self.add_code("{")
            self.indent += 1
            self.add_code(f"const context = contexts[{self.js_string(sprite_name)}];")
            self.add_code(
                f"if (!context) throw new Error({self.js_string('Missing execution context for ' + sprite_name)});"
            )
            self.indent -= 1
            self.emit_statements(program.body, context)
            # self.emit_program(program, context)
            self.add_code("}")
            self.indent -= 1

        self.add_code("}")

        output_file = project_directory / "output.js"
        output_file.write_text("\n".join(self.code) + "\n", encoding="utf-8")
        return output_file
