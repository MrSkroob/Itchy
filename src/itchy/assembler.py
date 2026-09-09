from __future__ import annotations
import uuid
import json
import re
import zipfile

import tempfile
import os
import hashlib
import wave
from collections.abc import Mapping
import shutil

from typing import TypeVar, Iterable
from dataclasses import dataclass, field, replace
from enum import Enum, StrEnum

from copy import deepcopy
from pathlib import Path
from typing import Any
from itchy.shared_templates import VARIABLE_TYPE_TO_USER_TYPES, VariableTypes, DataType, SourceSpan, SPRITE_TEMPLATE, COSTUME_TEMPLATE, PROJECT_TEMPLATE, DATA_TO_VARIABLE_TYPE, ASTNode
from itchy.errors import CompilerError, CompilerWarning, CompilerErrorCodes, Unbound, NotReferenced, Shadow, DuplicateDefinitionError,\
    NeverReached, \
    ArgumentError, NotDefinedError, InvalidTypeError, SyntaxError, TypeMismatch, ReturnNothingError
from itchy.scratch_blocks import SCRATCH_BLOCKS, STAGE_BLOCKS, Block, Reporter, Event, Menu
from itchy.itch_ast import \
    Param, \
    Stmt, VarRef, BlockStmt, IfStmt, BreakStmt, ForInStmt, WhileStmt, AssignStmt, ReturnStmt, VarDefStmt, ForRangeStmt, FunctionCallStmt, FunctionDefStmt, EventHandlerStmt, \
    ForeverStmt, IfBranch, Expr, NumberExpr, BoolExpr, StringExpr, VarExpr, UnaryOpExpr, BinaryOpExpr, TableExpr, FunctionCallExpr, AssetExpr, Program
from itchy.mp3_parser import mp3_metadata


T = TypeVar("T")
ScratchBlock = dict[str, Any]
StrOptional = str | None

FRAME_INDEX = "compiler:frame_index"
STACK_ITERABLE = "compiler:stack_iterable"
FIND_STACK_FRAME = "compiler:find_stack_frame"
RETURN_STACK = "compiler:return_values"
# FLAG_STACK = "compiler:return_flags" 
PUSH_RETURN_FRAME = "compiler:push_return_frame"
SET_RETURN_VALUE = "compiler:set_return_value"
POP_RETURN_FRAME = "compiler:pop_return_frame"
THREAD_ARG = "compiler:frame_id"

DEFAULT_LAYER = 0
DEFAULT_THREAD = 1

HEXCODE = re.compile(r"^#(?:[0-9a-fA-F]{3}){1,2}$")
ROOT = Path(__file__).parent
TEMP_FILE_SRC = ROOT / "assets" / "empty.svg"

# return types as tuples are OKAY, because serialisation converts them all to lists anyway.
ScratchInputRaw = tuple["InputType", tuple["DataType", str] | tuple["DataType", str, str]] | tuple["InputType", str]
ScratchFieldRaw = tuple[str, None] | tuple[str, str]

@dataclass
class ScratchInput:
    value: ScratchInputRaw
    return_type: set[VariableTypes] = field(default_factory=lambda: {VariableTypes.NOTHING})
    manufactured: bool=False # tells code that this input was automatically generated (not by the user) 
    # so should be ignored in subsequent error collection.

# serialisable json
JSONValue = int | str | float | bool | None | list["JSONValue"] | dict[str, "JSONValue"]

# stuff to be serialised
Serialisable = Enum | tuple["Serialisable", ...] | list["Serialisable"] | dict[str, "Serialisable"] | JSONValue


class InputType(Enum):
    SHADOW_ONLY = 1
    BLOCK_ONLY = 2
    BLOCK_AND_SHADOW = 3 # do not use - because compiler does not have default values.


PLACE_HOLDER_0 = ScratchInput((InputType.BLOCK_AND_SHADOW, (DataType.NUMBER, "0")), {VariableTypes.VAR}, True)


class SymbolType(StrEnum):
    PARAMETER = "parameter"
    VARIABLE = "variable"
    FUNCTION = "function"
    MESSAGE = "message"
    EVENT = "event"
    ASSET = "asset"


@dataclass(frozen=True, kw_only=True)
class Context:
    function_context: StrOptional
    layer: int
    thread_id: int


@dataclass(frozen=True, kw_only=True)
class SymbolOccurence:
    span: SourceSpan
    definition_location: SourceSpan | None
    context: StrOptional
    symbol_type: SymbolType
    name: str


@dataclass
class BlockRange:
    first: StrOptional
    last: StrOptional
    manufactured: bool=False


@dataclass(frozen=True)
class MessageData:
    uri: str
    name: str
    id: str


@dataclass(frozen=True)
class VariableData:
    uri: str
    name: str
    id: str
    context: Context
    var_type: VariableTypes
    is_list: bool
    shared: bool
    initial_value: Any
    definition_location: SourceSpan | None


@dataclass
class ProcedureInfo:
    name: str
    prototype_id: str
    proccode: str
    argument_ids: tuple[str, ...]
    argument_names: tuple[str, ...]
    argument_defaults: tuple[str, ...]

    # compiler only. does not get serialised
    definition_location: SourceSpan | None
    argument_types: tuple[VariableTypes, ...]
    # occurence of the last bit of code in the function (so you can append to it)
    last_location: SourceSpan | None=None

    # if applicable
    return_types: set[VariableTypes]=field(default_factory=lambda: {VariableTypes.NOTHING})


def type_error_factory(name: str, index: int, expected: VariableTypes, actual: set[VariableTypes], stmt: ASTNode):
    if len(actual) == 1:
        return InvalidTypeError(f"'{name}': expected '{expected.value}' not '{list(actual)[0].value}'", stmt)
    else:
        return InvalidTypeError(f"'{name}': not one of ({", ".join(i.value for i in actual)}) \
                                                        matches argument {index} of type {expected.value}", stmt)


class Assembler:
    def __init__(self, uri: str, is_strict: bool=True, compile_with_warnings: bool=False) -> None:
        """
        is_strict: whether the compiler should halt on error. Enabling this option will also disable any write to the .sb3 file.
        """
        self.variables: dict[str, VariableData] = {} # includes lists.
        self.blocks: dict[str, ScratchBlock] = {}
        self.procedures: dict[str, ProcedureInfo] = {}

        self.block_pool = SCRATCH_BLOCKS
        self.compiling = None
        self.emitted_return = False

        self.is_strict = is_strict
        self.uri = uri
        self.compile_with_warnings = compile_with_warnings or not is_strict
        # we don't need to worry about function "variables" since they are arguments.
        # i.e. they are not treated as variables and are treated as read-only.
        # variable name -> id
        self.variable_map: dict[tuple[str, StrOptional], str] = {}

        # set of variable ids that can be overriden because they were defined in the project.
        self.overridable: set[str] = set()

        self.thread_number = 0
        # name, id
        self.messages: dict[str, MessageData] = {}

        self.costumes: set[str] = set()
        self.errors: list[CompilerError] = []

        self.non_referenced_functions: dict[str, FunctionDefStmt] = {} 
        self.non_referenced_variables: dict[tuple[str, StrOptional], list[VarDefStmt | Param]] = {}

        self.symbols: list[tuple[SymbolOccurence, ASTNode]] = []

        # for debugging/error messages
        self.current_token = None

    def raise_or_return(self, error: CompilerError, return_value: T=BlockRange(None, None, True)) -> T:
        """
        Raises an error if strict mode is on (default) or returns a value.
        """
        if error.error_node and not error.error_node.dummy:
            self.errors.append(error)

        if isinstance(error, CompilerWarning) and self.compile_with_warnings:
            return return_value
        if self.is_strict:
            raise error
        return return_value

    def new_thread_id(self) -> int:
        self.thread_number += 1
        return self.thread_number
    
    def new_id(self) -> str:
        return uuid.uuid4().hex[:20]

    def add_block(self, block: ScratchBlock, id: StrOptional) -> str:
        block_id = id or self.new_id()
        self.blocks[block_id] = block
        return block_id

    def register_symbol(self, symbol: SymbolOccurence, stmt: ASTNode):
        if stmt.dummy:
            return

        self.symbols.append((symbol, stmt))
    
    def make_block(
            self,
            opcode: str,
            id: str | None=None,
            parent: StrOptional=None,
            inputs: dict[str, ScratchInputRaw] | None=None,
            fields: dict[str, ScratchFieldRaw] | None=None,
            mutation: dict[str, Any] | None=None,
            top_level: bool=False,
            shadow: bool=False,
            x: int | None=None,
            y: int | None=None
        ) -> str:
        # please note that we will use Scratch's naming scheme (javascript) in string names so they'll match up in the
        # final json.

        if inputs is None:
            inputs = {}

        if fields is None:
            fields = {}

        block: ScratchBlock = {
            "opcode": opcode,
            "next": None,
            "parent": parent,
            "inputs": inputs,
            "fields": fields,
            "shadow": shadow,
            "topLevel": top_level
        }

        if mutation is not None:
            block["mutation"] = mutation
        
        if top_level:
            block["x"] = x if x is not None else 100
            block["y"] = y if y is not None else 100
        
        return self.add_block(block, id)
            

    def count_args(self, args: tuple[Expr | Stmt, ...]):
        length = 0
        for i in args:
            if i.dummy is True:
                continue
            length += 1
        return length

    @staticmethod
    def _menu_literal_value(expr: Expr) -> str | None:
        """
        Returns the literal value of an expression when it can be represented
        by one of Scratch's menu shadow blocks.

        Asset expressions such as @costume("walk1") are compile-time string
        literals too, so keeping them as menu shadows preserves Scratch's
        dropdown UI instead of replacing the menu with a plain string input.
        """
        if isinstance(expr, StringExpr):
            return expr.value

        if (
            isinstance(expr, AssetExpr)
            and len(expr.args) == 1
            and isinstance(expr.args[0], StringExpr)
        ):
            return expr.args[0].value

        return None

    def get_variable_safe(self, stmt: VarRef, context: Context) -> tuple[str, StrOptional] | None:
        """
        Returns a variable key without any extra functionality. Returns none instead of raising an 
        error if the variable exists.
        """
        name = stmt.root

        symbol_type = SymbolType.VARIABLE
        function_owner = context.function_context
        
        if (name, function_owner) in self.variable_map:
            if function_owner is not None \
                and function_owner in self.procedures \
                and name in self.procedures[function_owner].argument_names:

                symbol_type = SymbolType.PARAMETER
            key = (name, function_owner)
            # raise NameError(f"variable {name} is not defined!")
        elif (name, None) in self.variable_map:
            key = (name, None)
        else:
            return

        self.flag_referenced_variable(self.variable_map[key], context)

        variable = self.variables[self.variable_map[key]]

        self.register_symbol(
            SymbolOccurence(
                span=stmt.span,
                definition_location=variable.definition_location,
                context=key[1],
                symbol_type=symbol_type,
                name=stmt.root
            ), stmt
        )

        return key

    def get_variable(self, stmt: VarRef, context: Context) -> str:
        """
        Returns a variable ID without any extra functionality.
        Do this when you strictly expect the variable to exist, and want to error if it wasn't implicitly/explicitly defined previously.
        """
        key = self.get_variable_safe(stmt, context)
        if key is None:
            raise NameError(f"{stmt.root} does not exist!")

        return self.variable_map[key]

    def define_broadcast(self, name: str) -> str:
        message = self.messages.get(name)

        if message:
            return message.id

        self.messages[name] = MessageData(self.uri, name, name)
        return name
    
    def assert_writable_name(self, var_name: str, context: Context) -> None:
        function_context = context.function_context

        if function_context is None:
            return

        if function_context not in self.procedures:
            return
        
        procedure = self.procedures[function_context]
        if var_name in procedure.argument_names:
            raise ValueError(f"{var_name} IS READ ONLY!!!")

    def is_parameter(self, var_id: str):
        variable = self.variables[var_id]

        if variable.context.function_context is None:
            return False

        if variable.context.function_context in self.procedures:
            proc_info = self.procedures[variable.context.function_context]
            return variable.name in proc_info.argument_names

        return False

    def define_variable(self, shared: bool, type_name: str, name: str, context: Context, source_location: SourceSpan | None) -> str:
        """
        Returns a variable ID. NOT a block ID.
        You may also use this if you're okay with the variable not existing beforehand (typically for loop variables and other compiler-defined, single use variables.)
        """
        # shared defines if the variable can be accessible to all sprites

        is_list = type_name == "list"
        if is_list:
            default_value = []
        else:
            default_value = 0

        # if context.function_context in self.procedures:

        key = (name, context.function_context)

        if key in self.variable_map:
            self.flag_referenced_variable(self.variable_map[key], context)
            return self.variable_map[key]

        var_id = self.new_id()

        variable = VariableData(
            name=name, 
            uri=self.uri,
            id=var_id,
            context=context,
            var_type=VariableTypes(type_name),
            is_list=is_list,
            shared=shared,
            initial_value=default_value,
            definition_location=source_location
        )

        self.variables[var_id] = variable
        self.variable_map[key] = var_id

        return var_id

    def flag_non_referenced_function(self, function: FunctionDefStmt):
        if function.name in self.block_pool:
            return

        if function.name not in self.procedures:
            return

        self.non_referenced_functions[function.name] = function

    def flag_referenced_function(self, function_name: str):
        if function_name in self.non_referenced_functions:
            del self.non_referenced_functions[function_name]

    def flag_non_referenced_variable(self, var_id: str, stmt: VarDefStmt | VarRef | Param, context: Context):
        variable = self.variables[var_id]

        if variable.shared:
            return

        if isinstance(stmt, VarDefStmt):
            self.non_referenced_variables[(variable.name, None)] = [stmt]
        else:
            function_context = context.function_context
            if isinstance(stmt, VarRef):
                stmt = VarDefStmt(variable.var_type.value, variable.name, variable.shared, span=stmt.span)
            if (variable.name, function_context) not in self.non_referenced_variables:
                self.non_referenced_variables[(variable.name, function_context)] = []
            self.non_referenced_variables[(variable.name, function_context)].append(stmt)
        
    def flag_referenced_variable(self, var_id: str, context: Context):
        variable = self.variables[var_id]
        key = (variable.name, context.function_context)
        key2 = (variable.name, None)

        if key in self.non_referenced_variables:
            self.non_referenced_variables[key].pop()
            if len(self.non_referenced_variables[key]) == 0:
                del self.non_referenced_variables[key]

        if key2 in self.non_referenced_variables:
            del self.non_referenced_variables[key2]


    def emit_statements(self, statements: Iterable[Stmt], x: int=100, y: int=100):
        """
        emits statements that do not necessarily have to be linked together.
        """
        for stmt in statements:
            block_range = self.emit_stmt(stmt, None, Context(
                function_context=None, 
                thread_id=DEFAULT_THREAD, 
                layer=DEFAULT_LAYER))
            if block_range.first is None:
                if isinstance(stmt, VarDefStmt):
                    continue
                # e.g. a bare VarDefStmt, which doesn't emit a block
                continue

            if stmt.__class__ not in {EventHandlerStmt, FunctionDefStmt}:
                error = NeverReached("This statement will never be called", stmt)
                self.raise_or_return(error, None)
                break

            first_block = self.blocks[block_range.first]
            first_block["topLevel"] = True
            first_block["parent"] = None
            first_block["x"] = x
            first_block["y"] = y

            y += 200


    def _emit_return_helpers(self):
        """
        This was originally part of emit_program(), but having return statement related
        helpers polluting your workspace even if you didn't use any felt a bit wrong. 
        """
        if self.emitted_return:
            return
        self.emitted_return = True
        context = Context(
            function_context=None,
            layer=DEFAULT_LAYER,
            thread_id=DEFAULT_THREAD
        )

        self.define_variable(False, "var", FRAME_INDEX, context, None)
        self.define_variable(False, "list", RETURN_STACK, context, None)
        # self.define_variable(False, "list", FLAG_STACK, None, None)
        self.define_variable(False, "var", STACK_ITERABLE, context, None)

        # return_helper
        push_return_frame = FunctionDefStmt(
            name=PUSH_RETURN_FRAME,
            warp=True,
            params=(Param("frame_id", "var"),),
            body=(
                FunctionCallStmt("data_addtolist", (VarExpr(VarRef("frame_id")), VarExpr(VarRef(RETURN_STACK)))),
                FunctionCallStmt("data_addtolist", (StringExpr(""), VarExpr(VarRef(RETURN_STACK)))),
                FunctionCallStmt("data_addtolist", (StringExpr("false"), VarExpr(VarRef(RETURN_STACK)))),
            )
        )

        #
        find_frame = FunctionDefStmt(
            name=FIND_STACK_FRAME,
            warp=True,
            params=(Param("frame_id", "var"),),
            body=(
                ForRangeStmt(STACK_ITERABLE, start=NumberExpr(1), 
                                stop=FunctionCallExpr("data_lengthoflist", (VarExpr(VarRef(RETURN_STACK)),)), 
                                step=NumberExpr(3), 
                                body=(
                                    IfStmt(
                                        branches=(IfBranch(
                                            BinaryOpExpr(VarExpr(VarRef(STACK_ITERABLE)), "==", VarExpr(VarRef("frame_id"))),
                                            body=(
                                                AssignStmt(VarRef(FRAME_INDEX), VarExpr(VarRef(STACK_ITERABLE))),
                                                FunctionCallStmt("control_stop", (StringExpr("this script"),)),
                                            )
                                        ),),
                                        else_body=()
                                    ),
                                )),
            )
        )

        # return_helper
        set_return_value = FunctionDefStmt(
            name=SET_RETURN_VALUE,
            warp=True,
            params=(Param("value", "var"), Param("frame_id", "var")),
            body=(
                FunctionCallStmt(FIND_STACK_FRAME, (VarExpr(VarRef("frame_id")),)),
                # FunctionCallStmt("data_replaceitemoflist", (VarExpr(VarRef("stack_id")), VarExpr(VarRef("value")))),
                FunctionCallStmt("data_replaceitemoflist", (BinaryOpExpr(VarExpr(VarRef("frame_id")), "+", NumberExpr(1)), 
                                                                            VarExpr(VarRef("value")),
                                                                            VarExpr(
                                                                                VarRef(RETURN_STACK)))),
                FunctionCallStmt("data_replaceitemoflist", (BinaryOpExpr(VarExpr(VarRef("frame_id")), "+", NumberExpr(2)), 
                                                                                            StringExpr("true"),
                                                                                            VarExpr(
                                                                                                VarRef(RETURN_STACK)))),
            )
        )

        # # return helper
        pop_return_frame = FunctionDefStmt(
            name=POP_RETURN_FRAME,
            warp=True,
            params=(Param("frame_id", "var"),),
            body=(
                FunctionCallStmt(FIND_STACK_FRAME, (VarExpr(VarRef("frame_id")),)),
                FunctionCallStmt("data_deleteoflist", (VarExpr(VarRef(FRAME_INDEX)), 
                                                                        VarExpr(
                                                                            VarRef(RETURN_STACK)))),
                FunctionCallStmt("data_deleteoflist", (VarExpr(VarRef(FRAME_INDEX)), 
                                                                        VarExpr(
                                                                            VarRef(RETURN_STACK)))),
                FunctionCallStmt("data_deleteoflist", (VarExpr(VarRef(FRAME_INDEX)), 
                                                                        VarExpr(
                                                                            VarRef(RETURN_STACK)))),
            )
        )

        pre_defines = (push_return_frame, find_frame, set_return_value, pop_return_frame)

        self.emit_statements(pre_defines)


    def _collect_variables(self, program: Program, owner: str):
        """
        Since the grammar forces you to define variables at the top, we need to collect the variables
        of all files first and then pass them into `global_variables` when calling `prepare`. 
        """
        global_vars: dict[str, VariableData] = {}

        for stmt in program.body:
            if not isinstance(stmt, VarDefStmt):
                break

            if not stmt.shared and owner.casefold() != "stage":
                continue

            # this has an added benefit of making sure that stage variables are actually global

            global_vars[stmt.name] = VariableData(
                uri=owner,
                name=stmt.name,
                id=self.new_id(),
                context=Context(
                    function_context=None,
                    layer=DEFAULT_LAYER,
                    thread_id=DEFAULT_THREAD,
                ),
                var_type=VariableTypes(stmt.type_name),
                is_list=stmt.type_name == "list",
                shared=True,
                initial_value=[] if stmt.type_name == "list" else 0,
                definition_location=stmt.span,
            )

        return global_vars

    
    def emit_program(self, program: Program) -> None:
        """
        Takes a program object only and emits each sequence within said program.
        uses .emit_statements() internally so statements do not connect to each other. 
        """
        self.emit_statements(program.body)


        for variables in self.non_referenced_variables.values():
            for variable in variables:
                self.errors.append(
                    NotReferenced(
                        f"'{variable.name}' is not referenced",
                        error_node=variable
                    )
                )

        for function in self.non_referenced_functions.values():
            self.errors.append(
                NotReferenced(
                    f"'{function.name}' is not referenced",
                    error_node=function
                )
            )

    def emit_sequence(
            self,
            statements: tuple[Stmt, ...],
            parent: StrOptional,
            context: Context,
            new_layer: bool=True
        ) -> BlockRange:

        first: StrOptional = None
        last: StrOptional = None

        final_return_statement: ReturnStmt | None = None
        proc_info: ProcedureInfo | None = None

        if new_layer:
            context = Context(function_context=context.function_context,
                              layer=context.layer + 1,
                              thread_id=context.thread_id)

        if context.function_context in self.procedures:
            proc_info = self.procedures[context.function_context]
            
        for index, stmt in enumerate(statements):
            emitted = self.emit_stmt(stmt, parent, context)

            if emitted.first is None:
                continue

            if first is None:
                first = emitted.first
                self.blocks[first]["parent"] = parent

                # if not self.can_have_next(parent):
                #     error = NeverReached("This statement will never be called", stmt)
                #     self.raise_or_return(error)
                #     break
            else:
                assert last is not None

                if not self.can_have_next(last):
                    error = NeverReached("This statement will never be called", stmt)
                    self.raise_or_return(error)
                    break

                self.blocks[last]["next"] = emitted.first
                self.blocks[emitted.first]["parent"] = last

            if index == len(statements) - 1 and context.layer == 1:
                if isinstance(stmt, ReturnStmt):
                    final_return_statement = stmt
                if not stmt.dummy and proc_info:
                    proc_info.last_location = stmt.span
            
            last = emitted.last

        if context.function_context in self.procedures:
            proc_info = self.procedures[context.function_context]
            if final_return_statement is not None \
            and VariableTypes.NOTHING in proc_info.return_types:
                proc_info.return_types.remove(VariableTypes.NOTHING)
        
        return BlockRange(first, last)
    
    def emit_stmt(self, stmt: Stmt, parent: StrOptional, context: Context) -> BlockRange:
        match stmt:
            case BlockStmt(body=body):
                # i don't think this is actually ever used...
                # all 'wrap' things are consumed. there aren't really any individual {} statements.
                return self.emit_sequence(body, parent, context)
            case VarDefStmt(shared=shared, type_name=type_name, name=name):
                if type_name not in {VariableTypes.VAR.value, VariableTypes.LIST.value, VariableTypes.BOOL.value}:
                    return self.raise_or_return(InvalidTypeError(f"Invalid variable type: '{type_name}'.\
                                                                 Scratch only permits var, list and bool.", stmt))


                if name not in self.overridable and (name, None) in self.variable_map:
                    error = Shadow(f"Variable '{stmt.name}' is shadowed by variable of same name", stmt)
                    # if not self.compile_with_warnings:
                    self.raise_or_return(error)
                    return BlockRange(None, None)

                # allow variable to override existing one in project at least once. 
                # any subsequent definitions will be counted as duplicates.
                if name in self.overridable:
                    self.overridable.remove(name)

                var_id = self.define_variable(shared, type_name, name, Context(
                    function_context=None, 
                    thread_id=context.thread_id, 
                    layer=context.layer), stmt.span)

                self.register_symbol(SymbolOccurence(
                    span=stmt.span,
                    definition_location=stmt.span,
                    context=None,
                    symbol_type=SymbolType.VARIABLE,
                    name=name
                ), stmt)

                self.flag_non_referenced_variable(var_id, stmt, context)
                return BlockRange(None, None)
            case AssignStmt(target=target, value=value):
                return self.emit_assignment(target, value, parent, context)
            case ForeverStmt():
                return self.emit_forever(stmt, parent, context)
            case IfStmt():
                return self.emit_if(stmt, parent, context)
            case WhileStmt():
                return self.emit_while(stmt, parent, context)
            case ForRangeStmt():
                return self.emit_for_range(stmt, parent, context)
            case ForInStmt():
                return self.emit_for_in(stmt, parent, context)
            case EventHandlerStmt():
                return self.emit_event_handler(stmt, context)
            case FunctionDefStmt():
                return self.emit_function_def(stmt, parent)
            case FunctionCallStmt():
                return self.emit_function_call(stmt, parent, context)
            case BreakStmt():
                raise NotImplementedError("Not implemented")
            case ReturnStmt():
                return self.emit_return(stmt, parent, context)
            case _:
                raise TypeError("Bad statement type")

    @staticmethod
    def static_type_check(a: VariableTypes, b: set[VariableTypes]):
        if a in b:
            return True

        # scratch represents the contents of a list as a space separated string... for some reason.
        # so, whenever you use it as a parameter, it is treated is a string. 

        # if a == VariableTypes.LIST:
        #     a = VariableTypes.STRING

        if VariableTypes.LIST in b:
            b.add(VariableTypes.STRING)
            b.remove(VariableTypes.LIST)

        if a == VariableTypes.NOTHING:
            return False

        if a == VariableTypes.VAR:
            return True

        if a == VariableTypes.UNKNOWN:
            return True

        if VariableTypes.VAR in b:
            return True


    def type_check(self, a: VariableTypes, b: set[VariableTypes], node: ASTNode | None):
        if VariableTypes.LIST in b:
            if node:
                self.raise_or_return(TypeMismatch("Lists will be converted into a space separated string. Are you sure this is what you want?", node))
        return_bool = self.static_type_check(a, b)
        return return_bool


    def emit_return(self, stmt: ReturnStmt, parent: StrOptional, context: Context) -> BlockRange:
        function_context = context.function_context
        if function_context is None or not self.procedures.get(function_context):
            return self.raise_or_return(SyntaxError("'return' outside of function", stmt, CompilerErrorCodes.REMOVE_RETURN))

        proc_data = self.procedures[function_context]
        return_variable = proc_data.name + ":return"
        self.define_variable(False, "var", return_variable, Context(
            function_context=None, 
            thread_id=context.thread_id, 
            layer=context.layer), None)

        body: list[Stmt] = []

        if self.count_args(stmt.values) > 1:
            return self.raise_or_return(SyntaxError("Can only return one value at a time", stmt))

        returns_things = self.count_args(stmt.values) > 0

        if returns_things:
            self._emit_return_helpers()
            # technically it's always 1 or 0, but this was left over for future where we might support more than one
            # return expressions (tuples)
            return_type = self.emit_expr(stmt.values[0], context, BlockRange(None, None, True), None).return_type

            if VariableTypes.NOTHING in return_type:
                proc_data.return_types.add(VariableTypes.STRING)

            proc_data.return_types = \
                proc_data.return_types.union(return_type)
            for value in stmt.values:
                body.append(
                    FunctionCallStmt(SET_RETURN_VALUE, (value, VarExpr(VarRef(FRAME_INDEX))))
                )
        # else:
            # proc_data.return_types.add(VariableTypes.STRING)

        control_stop = FunctionCallStmt(
            "control_stop", (StringExpr("this script"),)
        )

        body.append(control_stop)

        if not returns_things:
            return self.emit_sequence(tuple(body), parent=parent, context=context, new_layer=False)
        else:
            return self.emit_sequence(
                parent=parent,
                context=context,
                new_layer=False,
                statements=(
                    FunctionCallStmt(FIND_STACK_FRAME, (VarExpr(VarRef(THREAD_ARG)),)),
                    IfStmt(
                        branches=(IfBranch(
                            condition=BinaryOpExpr(
                                FunctionCallExpr("data_itemoflist", 
                                                    (BinaryOpExpr(VarExpr(VarRef(FRAME_INDEX)), "+", NumberExpr(2)), 
                                                    VarExpr(VarRef(RETURN_STACK)))), 
                                                    "==", 
                                                    StringExpr("false")),
                            body=tuple(body),
                        ),),
                        else_body=())
                    )
                )
        

    def _scratch_expected_args(self, block_data: Block | Reporter | Event) -> int:
        return len(block_data.inputs) + len(block_data.fields)

    def _resolve_scratch_variable(
        self,
        *,
        callee: str,
        index: int,
        arg_expr: Expr,
        context: Context,
        require_list: bool,
    ) -> tuple[str, VarExpr] | None:
        if not isinstance(arg_expr, VarExpr):
            self.raise_or_return(
                InvalidTypeError(
                    f"{callee}: argument {index} must be a variable",
                    arg_expr,
                ),
                None,
            )
            return None

        try:
            var_id = self.get_variable(arg_expr.ref, context)
        except NameError:
            error = Unbound(
                f"'{arg_expr.ref.root}' is not defined.",
                arg_expr,
                data={"name": arg_expr.ref.root},
            )
            self.raise_or_return(error, None)

            var_id = self.define_variable(
                False,
                "list" if require_list else "var",
                arg_expr.ref.root,
                context,
                None,
            )

        if require_list and not self.variables[var_id].is_list:
            self.raise_or_return(
                type_error_factory(
                    callee,
                    index,
                    VariableTypes.LIST,
                    {VariableTypes.VAR},
                    arg_expr,
                ),
                None,
            )
            return None

        return var_id, arg_expr

    def _emit_scratch_input_slot(
        self,
        *,
        callee: str,
        index: int,
        arg: Any,
        arg_expr: Expr,
        block_data: Block | Reporter | Event,
        context: Context,
        block_parent: BlockRange,
        block_id: str,
        literal_input_type: InputType,
    ) -> ScratchInputRaw:
        broadcasts = getattr(block_data, "broadcasts", ())
        variables = getattr(block_data, "variables", ())

        if arg.name in broadcasts:
            if isinstance(arg_expr, StringExpr):
                broadcast_id = self.define_broadcast(arg_expr.value)
                return (
                    InputType.SHADOW_ONLY,
                    (DataType.BROADCAST, arg_expr.value, broadcast_id),
                )

            return self.emit_expr(
                arg_expr,
                context,
                block_parent,
                block_id,
            ).value

        if arg.name in variables and isinstance(arg_expr, VarExpr):
            resolved = self._resolve_scratch_variable(
                callee=callee,
                index=index,
                arg_expr=arg_expr,
                context=context,
                require_list=arg.name == "LIST",
            )
            if resolved is None:
                return PLACE_HOLDER_0.value

            var_id, var_expr = resolved
            data_type = (
                DataType.LIST
                if arg.name == "LIST"
                else DataType.VARIABLE
            )
            return (
                literal_input_type,
                (data_type, var_expr.ref.root, var_id),
            )

        if arg.name in variables:
            return self.emit_expr(
                arg_expr,
                context,
                block_parent,
                block_id,
            ).value

        if isinstance(arg, Menu):
            menu_value = self._menu_literal_value(arg_expr)

            if menu_value is not None:
                # AssetExpr needs emit_expr() for symbol registration.
                if not isinstance(arg_expr, StringExpr):
                    self.emit_expr(
                        arg_expr,
                        context,
                        block_parent,
                        block_id,
                    )

                menu_id = self.make_block(
                    opcode=arg.opcode,
                    parent=block_id,
                    fields={
                        arg.field_name or arg.name: (
                            menu_value,
                            None,
                        )
                    },
                    shadow=True,
                )
                return (
                    InputType.BLOCK_AND_SHADOW,
                    menu_id,
                )

            emitted = self.emit_expr(
                arg_expr,
                context,
                block_parent,
                block_id,
            )

            if not self.type_check(
                VariableTypes.STRING,
                emitted.return_type,
                arg_expr,
            ):
                return self.raise_or_return(
                    type_error_factory(
                        callee,
                        index,
                        VariableTypes.STRING,
                        emitted.return_type,
                        arg_expr,
                    ),
                    PLACE_HOLDER_0.value,
                )

            return emitted.value

        expected_type = VARIABLE_TYPE_TO_USER_TYPES[
            DATA_TO_VARIABLE_TYPE[arg.return_type]
        ]

        if isinstance(arg_expr, StringExpr):
            actual_type = {VariableTypes.STRING}

            if not self.type_check(
                expected_type,
                actual_type,
                arg_expr,
            ):
                return self.raise_or_return(
                    type_error_factory(
                        callee,
                        index,
                        expected_type,
                        actual_type,
                        arg_expr,
                    ),
                    PLACE_HOLDER_0.value,
                )

            return (
                literal_input_type,
                (arg.return_type, arg_expr.value),
            )

        emitted = self.emit_expr(
            arg_expr,
            context,
            block_parent,
            block_id,
        )

        if not self.type_check(
            expected_type,
            emitted.return_type,
            arg_expr,
        ):
            return self.raise_or_return(
                type_error_factory(
                    callee,
                    index,
                    expected_type,
                    emitted.return_type,
                    arg_expr,
                ),
                PLACE_HOLDER_0.value,
            )

        return emitted.value

    def _emit_scratch_field_slot(
        self,
        *,
        callee: str,
        index: int,
        field: Any,
        arg_expr: Expr,
        block_data: Block | Reporter | Event,
        context: Context,
    ) -> ScratchFieldRaw:
        broadcasts = getattr(block_data, "broadcasts", ())
        variables = getattr(block_data, "variables", ())

        if field.name in variables:
            resolved = self._resolve_scratch_variable(
                callee=callee,
                index=index,
                arg_expr=arg_expr,
                context=context,
                require_list=field.name == "LIST",
            )
            if resolved is None:
                return ("", None)

            var_id, var_expr = resolved
            return (var_expr.ref.root, var_id)

        if not isinstance(arg_expr, (StringExpr, AssetExpr)):
            return self.raise_or_return(
                InvalidTypeError(
                    f"{callee}: argument {index} must be a string literal",
                    arg_expr,
                ),
                ("", None),
            )

        if isinstance(arg_expr, StringExpr):
            value = arg_expr.value
        else:
            expr = self.emit_expr(arg_expr, Context(function_context=None, 
                                             layer=DEFAULT_LAYER, 
                                             thread_id=DEFAULT_THREAD), 
                                             BlockRange(None, None, True), None)
            value = expr.value[1][1]

        if field.name in broadcasts:
            return (
                value,
                self.define_broadcast(value),
            )

        if (
            value not in field.expected
            and not getattr(field, "is_variable", False)
        ):
            return self.raise_or_return(
                ArgumentError(
                    f"'{value}' is not one of {field.expected}",
                    arg_expr,
                ),
                ("", None),
            )

        return (value, None)

    def _emit_scratch_slots(
        self,
        *,
        callee: str,
        args: tuple[Expr, ...],
        block_data: Block | Reporter | Event,
        context: Context,
        block_parent: BlockRange,
        block_id: str,
        literal_input_type: InputType,
    ) -> tuple[
        dict[str, ScratchInputRaw],
        dict[str, ScratchFieldRaw],
    ]:
        inputs: dict[str, ScratchInputRaw] = {}
        fields: dict[str, ScratchFieldRaw] = {}

        for index, (arg, arg_expr) in enumerate(
            zip(block_data.inputs, args)
        ):
            inputs[arg.name] = self._emit_scratch_input_slot(
                callee=callee,
                index=index,
                arg=arg,
                arg_expr=arg_expr,
                block_data=block_data,
                context=context,
                block_parent=block_parent,
                block_id=block_id,
                literal_input_type=literal_input_type,
            )

        field_offset = len(block_data.inputs)
        field_args = args[field_offset:]

        for offset, (field, arg_expr) in enumerate(
            zip(block_data.fields, field_args)
        ):
            fields[field.name] = self._emit_scratch_field_slot(
                callee=callee,
                index=field_offset + offset,
                field=field,
                arg_expr=arg_expr,
                block_data=block_data,
                context=context,
            )

        return inputs, fields

    def emit_scratch_block(
        self,
        stmt: FunctionCallStmt,
        parent: StrOptional,
        context: Context,
    ) -> BlockRange | None:
        if stmt.callee not in self.block_pool:
            return None

        block_data = self.block_pool[stmt.callee]

        if not isinstance(block_data, Block):
            return self.raise_or_return(
                InvalidTypeError(
                    f"'{stmt.callee}' should be a stack block",
                    stmt,
                )
            )

        expected_args = self._scratch_expected_args(block_data)
        actual_args = self.count_args(stmt.args)

        if actual_args != expected_args:
            return self.raise_or_return(
                ArgumentError(
                    f"Block '{stmt.callee}' expects {expected_args} "
                    f"argument(s), got {actual_args}",
                    stmt,
                )
            )

        block_id = self.make_block(
            opcode=stmt.callee,
            parent=parent,
        )
        block_range = BlockRange(block_id, block_id)

        inputs, fields = self._emit_scratch_slots(
            callee=stmt.callee,
            args=stmt.args,
            block_data=block_data,
            context=context,
            block_parent=block_range,
            block_id=block_id,
            literal_input_type=InputType.SHADOW_ONLY,
        )

        self.blocks[block_id]["inputs"] = inputs
        self.blocks[block_id]["fields"] = fields

        return block_range

    def emit_function_call(self, stmt: FunctionCallStmt, parent: StrOptional, context: Context) -> BlockRange:
        if stmt.callee not in self.procedures:
            # is either a custom scratch block or a hallucination :v
            block_range = self.emit_scratch_block(stmt, parent, context)
            if block_range is None:
                return self.raise_or_return(NotDefinedError(f"Procedure '{stmt.callee}' is not defined and is not a valid scratch block", stmt))
            self.register_symbol(
                SymbolOccurence(
                    span=stmt.span,
                    definition_location=None,
                    context=context.function_context,
                    symbol_type=SymbolType.FUNCTION,
                    name=stmt.callee
                ), stmt
            )
            return block_range

        self.flag_referenced_function(stmt.callee)

        info = self.procedures[stmt.callee]

        self.register_symbol(
            SymbolOccurence(
                span=stmt.span,
                definition_location=info.definition_location,
                context=context.function_context,
                symbol_type=SymbolType.FUNCTION,
                name=stmt.callee
            ), stmt
        )

        args = stmt.args

        if self.count_args(args) == len(info.argument_names) - 1:
            if context.function_context in self.procedures:
                args += (VarExpr(VarRef(THREAD_ARG)),)
            else:
                args += (NumberExpr(context.thread_id),)

        if self.count_args(args) != len(info.argument_ids):
            return self.raise_or_return(ArgumentError(
                f"Function '{stmt.callee}' expects {len(info.argument_ids) - 1} arguments, "
                f"got {self.count_args(stmt.args)}",
                stmt
            ))

        inputs: dict[str, ScratchInputRaw] = {}
        block_id = self.new_id()

        self.make_block(
            opcode="procedures_call",
            id=block_id,
            parent=parent,
            inputs=inputs,
        )
        block_range = BlockRange(block_id, block_id)

        index = 0

        failure: InvalidTypeError | None = None

        for arg_id, arg_type, arg_expr in zip(info.argument_ids, info.argument_types, args):
            emitted_arg = self.emit_expr(
                arg_expr,
                context,
                block_range,
                block_id,
            )

            user_arg_type = emitted_arg.return_type

            if isinstance(arg_expr, VarRef) and not arg_expr.dummy:
                self.get_variable(arg_expr, context)

            if not self.type_check(arg_type, user_arg_type, arg_expr):
                failure = type_error_factory(stmt.callee, index, arg_type, user_arg_type, arg_expr)
                self.errors.append(failure)
            
            inputs[arg_id] = emitted_arg.value
            index += 1

        if failure is not None:
            return self.raise_or_return(failure)

        self.blocks[block_id]["mutation"] = {
            "tagName": "mutation",
            "children": [],
            "proccode": info.proccode,
            "argumentids": json.dumps(list(info.argument_ids)),
            "warp": "false",
        }

        return block_range
    
    def emit_event_handler(
        self,
        stmt: EventHandlerStmt,
        context: Context,
    ) -> BlockRange:
        if context.function_context is not None:
            return self.raise_or_return(
                CompilerError(
                    "Cannot start a new thread while inside a function/event",
                    stmt,
                )
            )

        if stmt.name not in self.block_pool:
            return self.raise_or_return(
                NotDefinedError(
                    f"'{stmt.name}' is not a known event",
                    stmt,
                )
            )

        block_data = self.block_pool[stmt.name]

        if not isinstance(block_data, Event):
            return self.raise_or_return(
                CompilerError(
                    f"'{stmt.name}' should be a hat/event block",
                    stmt,
                )
            )

        self.register_symbol(
            SymbolOccurence(
                span=stmt.span,
                definition_location=None,
                context=context.function_context,
                symbol_type=SymbolType.EVENT,
                name=stmt.name,
            ),
            stmt,
        )

        expected_args = self._scratch_expected_args(block_data)
        actual_args = self.count_args(stmt.params)

        if actual_args != expected_args:
            return self.raise_or_return(
                ArgumentError(
                    f"Event '{stmt.name}' expects {expected_args} "
                    f"argument(s), got {actual_args}",
                    stmt,
                )
            )

        context = Context(
            function_context=None,
            layer=DEFAULT_LAYER,
            thread_id=self.new_thread_id(),
        )

        event_id = self.make_block(
            opcode=stmt.name,
            top_level=True,
        )
        event_range = BlockRange(event_id, event_id)

        inputs, fields = self._emit_scratch_slots(
            callee=stmt.name,
            args=stmt.params,
            block_data=block_data,
            context=context,
            block_parent=event_range,
            block_id=event_id,
            literal_input_type=InputType.SHADOW_ONLY,
        )

        self.blocks[event_id]["inputs"] = inputs
        self.blocks[event_id]["fields"] = fields

        body = self.emit_sequence(
            stmt.body,
            event_id,
            context,
        )

        if body.first is not None:
            self.blocks[event_id]["next"] = body.first

        return BlockRange(
            event_id,
            body.last or event_id,
        )

    def can_have_next(self, block_id: StrOptional) -> bool:
        if not block_id:
            return True
        block = self.blocks[block_id]
        opcode = block["opcode"]

        if opcode == "control_forever":
            return False

        if opcode == "control_stop":
            stop_option = block["fields"]["STOP_OPTION"][0]
            if stop_option in ["this script", "all"]:
                return False

        if opcode == "control_repeat_until":
            condition = block["inputs"]["CONDITION"][1]
            operator = self.blocks[condition]
            if operator["opcode"] == "operator_equals":
                operand1 = operator["inputs"]["OPERAND1"]
                operand2 = operator["inputs"]["OPERAND2"]
                if operand1[1][1] == "true" and operand2[1][1] == "false":
                    return False

        return True
            
    def emit_function_def(self, stmt: FunctionDefStmt, parent: StrOptional) -> BlockRange:
        if parent is not None:
            return self.raise_or_return(SyntaxError("Cannot define function inside of another", stmt, CompilerErrorCodes.REMOVE_RETURN))

        if stmt.name in self.procedures:
            return self.raise_or_return(DuplicateDefinitionError(f"Function '{stmt.name}' shadowed by function of same name.", stmt))

        context = Context(
            function_context=stmt.name, 
            thread_id=DEFAULT_THREAD, 
            layer=DEFAULT_LAYER)

        self.register_symbol(
            SymbolOccurence(
                span=stmt.span,
                definition_location=stmt.span,
                context=None,
                symbol_type=SymbolType.FUNCTION,
                name=stmt.name
            ), stmt
        )
        self.define_variable(False, "var", stmt.name + ":return", Context(function_context=None, thread_id=DEFAULT_THREAD, layer=DEFAULT_LAYER), None)

        definition_id = self.make_block(
            opcode="procedures_definition",
            parent=parent,
            inputs={},
        )

        prototype_id = self.make_block(
            opcode="procedures_prototype",
            parent=definition_id,
            inputs={},
            shadow=True,
        )

        argument_ids: list[str] = []
        argument_names: list[str] = []
        argument_defaults: list[str] = []
        argument_types: list[VariableTypes] = []
        proccode_parts: list[str] = [stmt.name]

        if len(stmt.params) == 0 or stmt.params[-1].name != THREAD_ARG:
            params = stmt.params + (Param(THREAD_ARG, "number", dummy=True),)
        else:
            params = stmt.params

        for param in params:
            arg_id = self.new_id()

            var_type = VariableTypes(param.type_name)
            if var_type == VariableTypes.LIST:
                self.raise_or_return(TypeMismatch("Lists will be converted into a space separated string. Are you sure this is what you want?", param))

            argument_ids.append(arg_id)
            argument_names.append(param.name)
            argument_types.append(var_type)

            if param.type_name == "bool":
                proccode_parts.append("%b")
                argument_defaults.append("false")
            else:
                proccode_parts.append("%s")
                argument_defaults.append("")

            var_id = self.define_variable(False, param.type_name, param.name, context, param.span)
            self.flag_non_referenced_variable(var_id, param, context)
            self.register_symbol(SymbolOccurence(
                span=param.span,
                definition_location=param.span,
                context=stmt.name,
                symbol_type=SymbolType.PARAMETER,
                name=param.name
            ), param)

        prototype = self.blocks[prototype_id]

        prototype["mutation"] = {
            "tagName": "mutation",
            "children": [],
            "proccode": " ".join(proccode_parts),
            "argumentids": json.dumps(argument_ids),
            "argumentnames": json.dumps(argument_names),
            "argumentdefaults": json.dumps(argument_defaults),
            "warp": str(stmt.warp).lower(),
        }

        self.blocks[definition_id]["inputs"]["custom_block"] = (
            InputType.BLOCK_ONLY,
            prototype_id,
        )

        argument_ids_tuple = tuple(argument_ids)
        argument_names_tuple = tuple(argument_names)
        argument_defaults_tuple = tuple(argument_defaults)
        argument_types_tuple = tuple(argument_types)
        proccode = " ".join(proccode_parts)

        proc_info = ProcedureInfo(
            name=stmt.name,
            prototype_id=prototype_id,
            proccode=proccode,
            argument_ids=argument_ids_tuple,
            argument_names=argument_names_tuple,
            argument_defaults=argument_defaults_tuple,
            argument_types=argument_types_tuple,
            definition_location=stmt.span
        )

        self.procedures[stmt.name] = proc_info

        self.flag_non_referenced_function(stmt)

        # for concise' sake, append a return statement always
        body_range = self.emit_sequence(stmt.body, definition_id, context)

        if body_range.first is not None:
            self.blocks[definition_id]["next"] = body_range.first
            self.blocks[body_range.first]["parent"] = definition_id

        return BlockRange(
            first=definition_id,
            last=body_range.last or definition_id,
        )
    
    def emit_for_range(self, stmt: ForRangeStmt, parent: StrOptional, context: Context):
        # iterable variable
        try:
            self.assert_writable_name(stmt.variable, context)
        except NameError:
            return self.raise_or_return(CompilerError(f"Cannot override read only argument '{stmt.variable}'", stmt.start))

        var_id = self.define_variable(False, "var", stmt.variable, context, stmt.span)
        set_id = self.new_id()

        set_inputs: dict[str, ScratchInputRaw] = {}

        self.make_block(
            "data_setvariableto",
            id=set_id,
            parent=parent,
            fields={
                "VARIABLE": (stmt.variable, var_id)
            },
            inputs=set_inputs
        )

        set_range = BlockRange(set_id, set_id)
        set_inputs["VALUE"] = self.emit_expr(
            BinaryOpExpr(stmt.start, "-", stmt.step), context, set_range, set_id
        ).value

        stop_condition = BinaryOpExpr(
            left=VarExpr(VarRef(stmt.variable)),
            op=">",
            right=BinaryOpExpr(stmt.stop, "-", stmt.step),
            span=stmt.span,
        )

        # repeat
        repeat_id = self.new_id()
        repeat_inputs: dict[str, ScratchInputRaw] = {}
        self.make_block(
            "control_repeat_until",
            id=repeat_id,
            parent=set_id,
            inputs=repeat_inputs,
        )
        repeat_range = BlockRange(repeat_id, repeat_id)
        repeat_inputs["CONDITION"] = self.emit_expr(
            stop_condition, context, repeat_range, repeat_id
        ).value

        assert set_range.last is not None
        assert repeat_range.first is not None
        self.blocks[set_range.last]["next"] = repeat_range.first
        self.blocks[repeat_range.first]["parent"] = set_range.last

        change_id = self.new_id()
        change_inputs: dict[str, ScratchInputRaw] = {}
        self.make_block(
            opcode="data_changevariableby",
            id=change_id,
            parent=repeat_id,
            fields={
                "VARIABLE": (stmt.variable, var_id)
            },
            inputs=change_inputs,
        )
        change_range = BlockRange(change_id, change_id)
        change_inputs["VALUE"] = self.emit_expr(
            stmt.step, context, change_range, change_id
        ).value

        body = self.emit_sequence(stmt.body, change_id, context)

        assert change_range.first is not None
        if body.first is None:
            self.blocks[repeat_id]["inputs"]["SUBSTACK"] = (
                InputType.BLOCK_ONLY,
                change_id,
            )
            # self.blocks[change_range.first]["parent"] = repeat_id
        else:
            self.blocks[repeat_id]["inputs"]["SUBSTACK"] = (
                InputType.BLOCK_ONLY,
                change_id,
            )
            assert body.first is not None
            self.blocks[change_id]["next"] = body.first
            # self.blocks[body.last]["next"] = change_range.first
            # self.blocks[change_range.first]["parent"] = body.last

        return BlockRange(set_range.first, repeat_id)
    
    def emit_for_in(self, stmt: ForInStmt, parent: StrOptional, context: Context):
        list_variable_name = "compiler:" + self.new_id()
        try:
            iterable_id = self.get_variable(stmt.iterable, context)
        except NameError:
            error = Unbound(f"'{stmt.iterable.root}' is not defined.", stmt.iterable, data={"name": stmt.iterable.root})
            self.raise_or_return(error)
            iterable_id = self.define_variable(False, "var", stmt.iterable.root, context, None)

        self.assert_writable_name(stmt.variable, context)
        # we *still* need this id to be unique, because even if it's in a for loop, scratch considers it global.
        # so we need a variable with a unique name to avoid amiguity.
        iterable_variable_data = self.variables[iterable_id]
        var_type = "var" if iterable_variable_data.var_type == VariableTypes.LIST else iterable_variable_data.var_type.value

        var_id = self.define_variable(False, "var", list_variable_name, context, None) # not to be used by the programmer, so is given garbage name.
        var_list_item_id = self.define_variable(False, var_type, stmt.variable, context, stmt.span)

        """
        temp = 1 // set_id
        repeat until temp > len(stmt.iterable) // repeat_id
            // substack
            i = stmt.iterable[temp] // list_set_id
            temp += 1 // change_id
            ... body ...
        end repeat

        temp is parented to parent
        repeat is parented to temp

        list_set_id is parented to repeat's substack
        change_id is parented to list_set_id

        subsequent body is parented to change_id
        """
        repeat_id = self.new_id()

        # iterator variable
        set_id = self.new_id()
        set_inputs: dict[str, ScratchInputRaw] = {}
        self.make_block(
            "data_setvariableto",
            id=set_id,
            parent=parent,
            fields={
                "VARIABLE": (list_variable_name, var_id)
            },
            inputs=set_inputs
        )
        set_inputs["VALUE"] = self.emit_expr(NumberExpr(0), context, BlockRange(set_id, set_id), set_id).value

        # operator that gets n item of list
        list_set_id = self.new_id()

        if self.variables[iterable_id].is_list:
            itemoflist = self.emit_function_expr(FunctionCallExpr("data_itemoflist",
                                                                  (VarExpr(VarRef(list_variable_name)), 
                                                                   VarExpr(VarRef(stmt.iterable.root)))
                                                                   ), context, BlockRange(set_id, set_id), list_set_id)
            stop_condition = BinaryOpExpr(
                left=VarExpr(VarRef(list_variable_name)),
                op=">",
                right=FunctionCallExpr("data_lengthoflist", (VarExpr(stmt.iterable),))
            )
        else:
            itemoflist = self.emit_function_expr(FunctionCallExpr("operator_letter_of", 
                                                                  (VarExpr(VarRef(list_variable_name)), 
                                                                   VarExpr(VarRef(stmt.iterable.root)))
                                                                   ), context, BlockRange(set_id, set_id), list_set_id)
            stop_condition = BinaryOpExpr(
                left=VarExpr(VarRef(list_variable_name)),
                op=">",
                right=FunctionCallExpr("operator_length", (VarExpr(stmt.iterable),))
            )

        

        # repeat
        self.make_block(
            "control_repeat_until",
            parent=set_id,
            id=repeat_id,
            inputs={
                "CONDITION": self.emit_expr(stop_condition, context, BlockRange(set_id, set_id), repeat_id).value
            }
        )

        self.blocks[set_id]["next"] = repeat_id

        # utility variable that is set to the item# of the array
        self.make_block(
            "data_setvariableto",
            id=list_set_id,
            parent=repeat_id,
            fields={
                "VARIABLE": (stmt.variable, var_list_item_id)
            },
            inputs={
                "VALUE": itemoflist.value
            }
        )

        change_id = self.new_id()
        self.make_block(
            opcode="data_changevariableby",
            id=change_id,
            parent=list_set_id,
            fields={
                "VARIABLE": (stmt.variable, var_id)
            },
            inputs={
                "VALUE": self.emit_expr(NumberExpr(1), context, BlockRange(set_id, set_id), change_id).value
            }
        )

        self.blocks[list_set_id]["next"] = change_id

        body = self.emit_sequence(stmt.body, change_id, context)

        if body.first is not None:
            self.blocks[change_id]["next"] = body.first

        self.blocks[repeat_id]["inputs"]["SUBSTACK"] = (InputType.BLOCK_ONLY, list_set_id)

        return BlockRange(set_id, repeat_id)

    def emit_forever(self, stmt: ForeverStmt, parent: StrOptional, context: Context):        
        block_id = self.new_id()
        inputs: dict[str, ScratchInputRaw] = {}
        self.make_block(
            opcode="control_forever",
            id=block_id,
            parent=parent,
            inputs=inputs
        )
        block_range = BlockRange(block_id, block_id)

        body = self.emit_sequence(stmt.body, block_id, context)

        if body.first is not None:
            self.blocks[block_id]["inputs"]["SUBSTACK"] = (InputType.BLOCK_ONLY, body.first)
        
        return block_range
    
    def emit_while(self, stmt: WhileStmt, parent: StrOptional, context: Context):
        """
        Scratch does not support while loops normally, but *does* support repeat until blocks. A good way to emulate it is to
        do:

        repeat until not <condition> do
            // code here
        end
        """
        not_condition = UnaryOpExpr("not", stmt.condition)

        block_id = self.new_id()
        inputs: dict[str, ScratchInputRaw] = {}
        self.make_block(
            opcode="control_repeat_until",
            id=block_id,
            parent=parent,
            inputs=inputs
        )
        block_range = BlockRange(block_id, block_id)
        inputs["CONDITION"] = self.emit_expr(
            not_condition, context, block_range, block_id
        ).value

        body = self.emit_sequence(stmt.body, block_id, context)

        if body.first is not None:
            self.blocks[block_id]["inputs"]["SUBSTACK"] = (InputType.BLOCK_ONLY, body.first)
        
        return block_range
            
    def emit_if(self, stmt: IfStmt, parent: StrOptional, context: Context) -> BlockRange:
        return self.emit_if_branch_chain(
            stmt.branches,
            stmt.else_body,
            0,
            parent,
            context,
        )

    def emit_if_branch_chain(self, branches: tuple[IfBranch, ...], else_body: tuple[Stmt, ...], index: int, parent: StrOptional, context: Context):
        branch = branches[index]
        has_else = index + 1 < len(branches) or bool(else_body)

        opcode = "control_if_else" if has_else else "control_if"

        block_id = self.new_id()
        inputs: dict[str, ScratchInputRaw] = {}
        self.make_block(
            opcode=opcode,
            id=block_id,
            parent=parent,
            inputs=inputs
        )
        block_range = BlockRange(block_id, block_id)
        inputs["CONDITION"] = self.emit_expr(
            branch.condition, context, block_range, block_id
        ).value

        then_body = self.emit_sequence(branch.body, block_id, context)

        if then_body.first is not None:
            self.blocks[block_id]["inputs"]["SUBSTACK"] = (InputType.BLOCK_ONLY, then_body.first)

        if has_else:
            if index + 1 < len(branches):
                # if this isn't the last branch (there is more)
                nested_if = self.emit_if_branch_chain(
                    branches,
                    else_body,
                    index + 1,
                    block_id,
                    context,
                )
                self.blocks[block_id]["inputs"]["SUBSTACK2"] = (
                    InputType.BLOCK_ONLY,
                    nested_if.first,
                )
            else:
                # no more if statements. rest of the code is not part of this if branch
                else_blocks = self.emit_sequence(else_body, block_id, context)
                if else_blocks.first is not None:
                    self.blocks[block_id]["inputs"]["SUBSTACK2"] = (InputType.BLOCK_ONLY, else_blocks.first)
        
        return block_range


    def _assign_list(self):
        pass


    def _assign_variable(self):
        pass
    
    
    def emit_assignment(self, target: VarRef, value: Expr, parent: StrOptional, context: Context) -> BlockRange:
        if context.function_context in self.procedures and \
            target.root in self.procedures[context.function_context].argument_names:
            return self.raise_or_return(CompilerError(f"Cannot assign read only argument '{target.root}'", target))

        inputs: dict[str, ScratchInputRaw] = {}

        try:
            var_id = self.get_variable(target, context)
            variable = self.variables[var_id]
            # self.register_symbol(SymbolOccurence(
            #     span=target.span,
            #     definition_location=variable.definition_location,
            #     context=context.function_context,
            #     symbol_type=SymbolType.VARIABLE,
            #     name=target.root
            # ), target)
        except NameError:
            error = Unbound(f"'{target.root}' is not defined.", target, data={"name": target.root})
            if not self.compile_with_warnings:
                return self.raise_or_return(error)
            self.errors.append(error)
            var_id = self.define_variable(False, "list" if target.slice_expr is not None else "var", target.root, context, None)

        variable = self.variables[var_id]

        if isinstance(value, TableExpr):
            if not variable.is_list:
                error = TypeMismatch(
                    "Assigning a list to a variable will convert it into a space separated string.", target
                )
                self.raise_or_return(error)
                
                var_id = self._make_table_expr(value, context)
                list_variable_name = self.variables[var_id].name

                value = VarExpr(VarRef(list_variable_name))
            else:
                statements: list[Stmt] = []
                statements.append(FunctionCallStmt("data_deletealloflist", (VarExpr(VarRef(variable.name)),)))
                for expr in value.values:
                    statements.append(FunctionCallStmt("data_addtolist", (expr, VarExpr(VarRef(variable.name)))))

                return self.emit_sequence(tuple(statements), parent, context, False)

        if target.slice_expr is not None:

            if variable.is_list:
                # is a list!
                block_id = self.new_id()
                self.make_block(
                    "data_replaceitemoflist",
                    id=block_id,
                    parent=parent,
                    inputs=inputs,
                    fields={
                        "LIST": (target.root, var_id)
                    }
                )
                block_range = BlockRange(block_id, block_id)
                inputs["INDEX"] = self.emit_expr(
                    target.slice_expr, context, block_range, block_id
                ).value
                inputs["ITEM"] = self.emit_expr(
                    value, context, block_range, block_id
                ).value

                return block_range
            else:
                return self.raise_or_return(InvalidTypeError("Strings do not support item assignment", target))
        else:
            block_id = self.new_id()
            self.make_block(
                "data_setvariableto",
                id=block_id,
                parent=parent,
                fields={
                    "VARIABLE": (target.root, var_id)
                },
                inputs=inputs
            )

            block_range = BlockRange(block_id, block_id)

            expr = self.emit_expr(
                value, context, block_range, block_id
            )

            if not self.type_check(self.variables[var_id].var_type, expr.return_type, value):
                error = TypeMismatch(
                    f"{target.root}: not one of ({", ".join(i.value for i in expr.return_type)}) matches {self.variables[var_id].var_type}", 
                    value)
                self.raise_or_return(error)

            inputs["VALUE"] = expr.value

            return block_range


    @staticmethod
    def _is_primitive(expr: Expr) -> bool:
        return expr.__class__ in {NumberExpr | StringExpr | AssetExpr | BoolExpr | VarExpr}


    def fold_expr(self, expr: Expr) -> Expr:
        if self._is_primitive(expr):
            return expr

        match expr:
            case UnaryOpExpr(value=value, op=op):
                value = self.fold_expr(value)

                if op == "-" and isinstance(value, NumberExpr):
                    return NumberExpr(
                        -value.value,
                        span=expr.span,
                    )

                if op == "not" and isinstance(value, BoolExpr):
                    return BoolExpr(
                        not value.value,
                        span=expr.span,
                    )

                return replace(expr, value=value)

            case BinaryOpExpr(left=left, right=right, op=op):
                left = self.fold_expr(left)
                right = self.fold_expr(right)

                # Numeric constant folding
                if isinstance(left, NumberExpr) and isinstance(right, NumberExpr):
                    match op:
                        case "+":
                            return NumberExpr(
                                left.value + right.value,
                                span=expr.span,
                            )

                        case "-":
                            return NumberExpr(
                                left.value - right.value,
                                span=expr.span,
                            )

                        case "*":
                            return NumberExpr(
                                left.value * right.value,
                                span=expr.span,
                            )

                        case "/":
                            # Let Scratch handle division by zero.
                            if right.value != 0:
                                return NumberExpr(
                                    left.value / right.value,
                                    span=expr.span,
                                )

                        case "==":
                            return BoolExpr(
                                left.value == right.value,
                                span=expr.span,
                            )

                        case ">":
                            return BoolExpr(
                                left.value > right.value,
                                span=expr.span,
                            )

                        case "<":
                            return BoolExpr(
                                left.value < right.value,
                                span=expr.span,
                            )

                        case _:
                            pass

                # Boolean constant folding
                if isinstance(left, BoolExpr) and isinstance(right, BoolExpr):
                    match op:
                        case "and":
                            return BoolExpr(
                                left.value and right.value,
                                span=expr.span,
                            )

                        case "or":
                            return BoolExpr(
                                left.value or right.value,
                                span=expr.span,
                            )

                        case "==":
                            return BoolExpr(
                                left.value == right.value,
                                span=expr.span,
                            )

                        case _:
                            pass

                # Children may have folded even if this expression didn't.
                return replace(
                    expr,
                    left=left,
                    right=right,
                )
            case _:
                return expr

    
    def emit_expr(self, expr: Expr, context: Context, block_parent: BlockRange, parent: StrOptional) -> ScratchInput:
        # block_id = self.new_id()
        # expression: ScratchInput = [InputType.REPORTER, block_id]
        expr = self.fold_expr(expr)
        
        match expr:
            case NumberExpr(value=value):
                return ScratchInput((InputType.SHADOW_ONLY, (DataType.NUMBER, str(value))), {VariableTypes.NUMBER})
            case StringExpr(value=value):
                if re.match(HEXCODE, value) is not None:
                    return ScratchInput((InputType.SHADOW_ONLY, (DataType.COLOR, value)), {VariableTypes.STRING})
                else:
                    return ScratchInput((InputType.SHADOW_ONLY, (DataType.STRING, value)), {VariableTypes.STRING})
            case AssetExpr():
                if self.count_args(expr.args) != 1:
                    return self.raise_or_return(ArgumentError(
                            f"'{expr.asset_type.value}' expects 1 argument, got {self.count_args(expr.args)}",
                            expr
                        ), PLACE_HOLDER_0)

                if not isinstance(expr.args[0], StringExpr):
                    return self.raise_or_return(InvalidTypeError(
                        f"{expr.asset_type.value}: argument 0 must be a string literal", expr.args[0]
                    ), PLACE_HOLDER_0)
                if not block_parent.manufactured:
                    self.register_symbol(SymbolOccurence(
                        span=expr.span,
                        definition_location=expr.args[0].span,
                        context=context.function_context,
                        symbol_type=SymbolType.ASSET,
                        name=expr.args[0].value
                    ), expr)

                return ScratchInput((InputType.SHADOW_ONLY, (DataType.STRING, expr.args[0].value)), {VariableTypes.STRING})
            case BoolExpr(value=value):
                # in scratch:
                # if (0 == 0) == "true" is true, so we can just use strings without any fancy conversion

                operator_id = self.make_block(
                    opcode="operator_equals",
                    parent=parent,
                    inputs={
                        "OPERAND1": (
                            InputType.SHADOW_ONLY, (
                                DataType.STRING,
                                "true"
                            )
                        ),
                        "OPERAND2": (
                            InputType.SHADOW_ONLY, (
                                DataType.STRING,
                                str(value).lower()
                            )
                        )
                    }
                )

                return ScratchInput((InputType.BLOCK_ONLY, operator_id), {VariableTypes.BOOL})
            case VarExpr(ref=ref):
                return self.emit_var_ref(ref, context, block_parent, parent)
            case UnaryOpExpr(op=op, value=value):
                return self.emit_unary_expr(op, value, context, block_parent, parent)
            case BinaryOpExpr(left=left, op=op, right=right):
                return self.emit_binary_expr(left, op, right, context, block_parent, parent)
            case FunctionCallExpr():
                return self.emit_function_expr(expr, context, block_parent, parent)
            case TableExpr():
                return self.emit_table_expr(expr, context, block_parent, parent)
            case _:
                raise TypeError("Bare expression (coder sucks :/)")

    def _make_table_expr(self, expr: TableExpr, context: Context):
        list_variable_name = ":compiler_list" + self.new_id()
        var_id = self.define_variable(False, "list", list_variable_name, context, None)

        flag_contents: list[Stmt] = [FunctionCallStmt("data_deletealloflist", (VarExpr(VarRef(list_variable_name)),))]
        flag_contents.extend(FunctionCallStmt("data_addtolist", (item, VarExpr(VarRef(list_variable_name))))  
                            for item in expr.values)

        body: tuple[Stmt] = (
            EventHandlerStmt("event_whenflagclicked", (), body=tuple(flag_contents)),
        )

        self.emit_statements(body)
        return var_id

    def emit_table_expr(self, expr: TableExpr, context: Context, block_parent: BlockRange, parent: StrOptional) -> ScratchInput:
        var_id = self._make_table_expr(expr, context)
        list_variable_name = self.variables[var_id].name

        return ScratchInput(
                            (
                                InputType.BLOCK_AND_SHADOW,
                                (
                                    DataType.LIST,
                                    list_variable_name,
                                    var_id
                                )
                            ),
                            {VariableTypes.LIST}
                        )


    def insert_setup_before_consumer(
        self,
        block_range: BlockRange,
        setup: BlockRange,
    ) -> None:
        if block_range.manufactured:
            return
        
        if setup.first is None:
            return

        if block_range.first is None or block_range.last is None:
            # this can also occur when we just want something to evaluate, and not necessarily want to generate
            # any blocks.
            raise ValueError(
                f"Cannot add expression setup to an empty block range: {block_range}"
            )

        assert setup.last is not None

        consumer_id = block_range.last
        consumer = self.blocks[consumer_id]

        if self.blocks[setup.last]["next"] is not None:
            raise ValueError(
                "Expression setup already has a block after its final block"
            )

        previous_id = consumer["parent"]

        """
        There is no command block before the consumer.
        This includes cases such as:
        
            define foo(a: number) {
                return a
            }
        
        where `return` is the first statement in the function.
        """
        if previous_id is None:
            self.blocks[setup.first]["parent"] = None
            self.blocks[setup.last]["next"] = consumer_id
            consumer["parent"] = setup.last

            if block_range.first == consumer_id:
                block_range.first = setup.first

            return

        # Is the parent actually an existing setup block?
        previous = self.blocks[previous_id]

        if previous.get("next") == consumer_id:
            previous["next"] = setup.first
            self.blocks[setup.first]["parent"] = previous_id

            self.blocks[setup.last]["next"] = consumer_id
            consumer["parent"] = setup.last
            return

        # Otherwise this is the first setup sequence before the consumer.
        outer_parent = consumer["parent"]

        self.blocks[setup.first]["parent"] = outer_parent
        self.blocks[setup.last]["next"] = consumer_id
        consumer["parent"] = setup.last

        if block_range.first == consumer_id:
            block_range.first = setup.first


    def append_range(
        self,
        chain: BlockRange,
        added: BlockRange,
    ) -> BlockRange:
        """
        Appends `added` to `chain`.

            chain -> added
        """
        if added.first is None:
            return chain

        if chain.first is None:
            return BlockRange(
                first=added.first,
                last=added.last,
            )

        assert chain.last is not None
        assert added.last is not None

        self.blocks[chain.last]["next"] = added.first
        self.blocks[added.first]["parent"] = chain.last

        return BlockRange(
            first=chain.first,
            last=added.last,
        )

        # return expression
    def emit_function_expr(
        self,
        expr: FunctionCallExpr,
        context: Context,
        block_parent: BlockRange,
        parent: StrOptional,
    ) -> ScratchInput:
        if (
            expr.callee not in self.block_pool
            and expr.callee in self.procedures
        ):
            return self._emit_procedure_expr(
                expr,
                context,
                block_parent,
                parent,
            )

        return self._emit_scratch_reporter_expr(
            expr,
            context,
            block_parent,
            parent,
        )

    def _emit_scratch_reporter_expr(
        self,
        expr: FunctionCallExpr,
        context: Context,
        block_parent: BlockRange,
        parent: StrOptional,
    ) -> ScratchInput:
        if expr.callee not in self.block_pool:
            return self.raise_or_return(
                NotDefinedError(
                    f"Procedure '{expr.callee}' is not defined "
                    "and is not a valid scratch block",
                    expr,
                ),
                PLACE_HOLDER_0,
            )

        block_data = self.block_pool[expr.callee]

        self.register_symbol(
            SymbolOccurence(
                span=expr.span,
                definition_location=None,
                context=context.function_context,
                symbol_type=SymbolType.FUNCTION,
                name=expr.callee,
            ),
            expr,
        )

        if not isinstance(block_data, Reporter):
            return self.raise_or_return(
                CompilerError(
                    f"'{expr.callee}' does not return anything.",
                    expr,
                ),
                PLACE_HOLDER_0,
            )

        expected_args = self._scratch_expected_args(block_data)
        actual_args = self.count_args(expr.args)

        if actual_args != expected_args:
            return self.raise_or_return(
                ArgumentError(
                    f"Block '{expr.callee}' expects {expected_args} "
                    f"argument(s), got {actual_args}",
                    expr,
                ),
                PLACE_HOLDER_0,
            )

        block_id = self.make_block(
            opcode=expr.callee,
            parent=parent,
        )

        inputs, fields = self._emit_scratch_slots(
            callee=expr.callee,
            args=expr.args,
            block_data=block_data,
            context=context,
            block_parent=block_parent,
            block_id=block_id,
            literal_input_type=InputType.BLOCK_AND_SHADOW,
        )

        self.blocks[block_id]["inputs"] = inputs
        self.blocks[block_id]["fields"] = fields

        input_type = (
            InputType.BLOCK_ONLY
            if VariableTypes.BOOL in block_data.return_type
            else InputType.BLOCK_AND_SHADOW
        )

        return ScratchInput(
            (input_type, block_id),
            block_data.return_type,
        )

    def _emit_procedure_expr(
        self,
        expr: FunctionCallExpr,
        context: Context,
        block_parent: BlockRange,
        parent: StrOptional,
    ) -> ScratchInput:
        proc_info = self.procedures[expr.callee]

        if VariableTypes.NOTHING in proc_info.return_types:
            return self.raise_or_return(
                ReturnNothingError(
                    f"{expr.callee}: not all codepaths have a return statement",
                    expr,
                    data={"name": expr.callee},
                ),
                PLACE_HOLDER_0,
            )

        self._emit_return_helpers()

        thread_id: Expr
        if context.function_context in self.procedures:
            thread_id = VarExpr(VarRef(THREAD_ARG))
        else:
            thread_id = NumberExpr(context.thread_id)

        self.register_symbol(
            SymbolOccurence(
                span=expr.span,
                definition_location=proc_info.definition_location,
                context=context.function_context,
                symbol_type=SymbolType.FUNCTION,
                name=expr.callee,
            ),
            expr,
        )

        setup = BlockRange(None, None)

        setup = self.append_range(
            setup,
            self.emit_function_call(
                FunctionCallStmt(PUSH_RETURN_FRAME, (thread_id,)),
                None,
                context,
            ),
        )

        setup = self.append_range(
            setup,
            self.emit_function_call(
                FunctionCallStmt(expr.callee, expr.args),
                None,
                context,
            ),
        )

        setup = self.append_range(
            setup,
            self.emit_assignment(
                VarRef(expr.callee + ":return"),
                FunctionCallExpr(
                    "data_itemoflist",
                    (
                        BinaryOpExpr(
                            VarExpr(VarRef(FRAME_INDEX)),
                            "+",
                            NumberExpr(1),
                        ),
                        VarExpr(VarRef(RETURN_STACK)),
                    ),
                ),
                None,
                context,
            ),
        )

        setup = self.append_range(
            setup,
            self.emit_function_call(
                FunctionCallStmt(POP_RETURN_FRAME, (thread_id,)),
                None,
                context,
            ),
        )

        self.insert_setup_before_consumer(
            block_parent,
            setup,
        )

        return_input = self.emit_var_ref(
            VarRef(expr.callee + ":return"),
            context,
            block_parent,
            parent,
        )
        return_input.return_type = set(proc_info.return_types)

        return return_input

    def emit_unary_expr(self, op: str, value: Expr, context: Context, block_parent: BlockRange, parent: StrOptional) -> ScratchInput:
        block_id = self.new_id()
        if op == "not":
            emitted_return = self.emit_expr(value, context, block_parent, block_id)
            if not self.type_check(VariableTypes.BOOL, emitted_return.return_type, value):
                return self.raise_or_return(
                    type_error_factory("not", 0, VariableTypes.BOOL, emitted_return.return_type, value),
                    PLACE_HOLDER_0,
                )
            
            self.make_block(
                opcode="operator_not",
                id=block_id,
                parent=parent,
                inputs={
                    "OPERAND": emitted_return.value,
                },
            )
            return ScratchInput((InputType.BLOCK_ONLY, block_id), {VariableTypes.BOOL})

        if op == "-":
            emitted_return = self.emit_expr(value, context, block_parent, block_id)
            if not self.type_check(VariableTypes.NUMBER, emitted_return.return_type, value):
                self.raise_or_return(
                    type_error_factory("-", 0, VariableTypes.NUMBER, emitted_return.return_type, value)
                )

            self.make_block(
                opcode="operator_multiply",
                id=block_id,
                parent=parent,
                inputs={
                    "NUM1": (InputType.BLOCK_AND_SHADOW, (DataType.NUMBER, "-1")),
                    "NUM2": emitted_return.value,
                },
            )
            return ScratchInput((InputType.BLOCK_AND_SHADOW, block_id), {VariableTypes.NUMBER})

        raise NotImplementedError(f"Unsupported unary operator: {op}")
    
    def emit_binary_expr(self, left: Expr, op: str, right: Expr, context: Context, block_parent: BlockRange, parent: StrOptional) -> ScratchInput:
        block_id = self.new_id()

        left_expr = self.emit_expr(left, context, block_parent, block_id)
        right_expr = self.emit_expr(right, context, block_parent, block_id)

        if op == "in":
            if VariableTypes.LIST in right_expr.return_type:
                if not isinstance(right, VarExpr):
                    return self.raise_or_return(type_error_factory("in", 1, VariableTypes.LIST, right_expr.return_type, right), PLACE_HOLDER_0)
                try:
                    list_id = self.get_variable(right.ref, context)
                except NameError:
                    error = Unbound(f"'{right.ref.root}' is not defined", right, data={"name": right})
                    if not self.compile_with_warnings:
                        return self.raise_or_return(error, PLACE_HOLDER_0)
                    list_id = self.define_variable(False, "list", right.ref.root, context, None)
                    self.errors.append(error)

                self.make_block(
                    opcode="data_listcontainsitem",
                    id=block_id,
                    parent=parent,
                    inputs={
                        "ITEM": left_expr.value
                    },
                    fields={
                        "LIST": (right.ref.root, list_id)
                    }
                )

                return ScratchInput(
                    (InputType.BLOCK_ONLY, block_id), {VariableTypes.BOOL}
                )
                
        
        opcode, left_name, right_name = {
            "+": ("operator_add", "NUM1", "NUM2"),
            "-": ("operator_subtract", "NUM1", "NUM2"),
            "*": ("operator_multiply", "NUM1", "NUM2"),
            "/": ("operator_divide", "NUM1", "NUM2"),
            "==": ("operator_equals", "OPERAND1", "OPERAND2"),
            ">": ("operator_gt", "OPERAND1", "OPERAND2"),
            "<": ("operator_lt", "OPERAND1", "OPERAND2"),
            "and": ("operator_and", "OPERAND1", "OPERAND2"),
            "or": ("operator_or", "OPERAND1", "OPERAND2"),
            "in": ("operator_contains", "STRING1", "STRING2")
        }[op]
        if op in {"==", ">", "<", "and", "or"}:
            return_type = VariableTypes.BOOL
        else:
            return_type = VariableTypes.VAR

        left_type_check: VariableTypes = VariableTypes.VAR
        right_type_check: VariableTypes = VariableTypes.VAR

        if op == "in":
            right_type_check = VariableTypes.STRING
        elif op in {"and", "or"}:
            left_type_check = VariableTypes.BOOL
            right_type_check = VariableTypes.BOOL
        else:
            left_type_check = VariableTypes.VAR
            right_type_check = VariableTypes.VAR

        if not self.type_check(left_type_check, left_expr.return_type, left):
            self.raise_or_return(
                type_error_factory(opcode, 0, left_type_check, left_expr.return_type, left),
            )

        if not self.type_check(right_type_check, right_expr.return_type, right):
            self.raise_or_return(
                type_error_factory(opcode, 1, right_type_check, right_expr.return_type, right),
            )

        left_input = left_expr.value
        right_input = right_expr.value

        self.make_block(
            opcode=opcode,
            parent=parent,
            id=block_id,
            inputs={
                left_name: left_input,
                right_name: right_input
            },
        )

        return ScratchInput(
            (InputType.BLOCK_ONLY if VariableTypes.BOOL == return_type else InputType.BLOCK_AND_SHADOW, block_id),
            {return_type},
        )

    def emit_var_ref(self, ref: VarRef, context: Context, block_parent: BlockRange, parent: StrOptional) -> ScratchInput:
        function_context = context.function_context
        if function_context in self.procedures \
            and ref.root in self.procedures[function_context].argument_names:
            procedure_info = self.procedures[function_context]

            try:
                arg_index = procedure_info.argument_names.index(ref.root)
            except ValueError:
                return self.raise_or_return(ArgumentError(f"Argument '{ref.root}' doesn't exist.", ref), PLACE_HOLDER_0)
            
            # _, *arg_types = procedure_info.proccode.split(" %")
            arg_types = procedure_info.argument_types
            arg_type = arg_types[arg_index]
            arg_name = procedure_info.argument_names[arg_index]

            if arg_type is VariableTypes.BOOL:
                opcode = "argument_reporter_boolean"
            else:
                opcode = "argument_reporter_string_number"

            self.flag_referenced_variable(self.variable_map[(arg_name, function_context)], context)

            print("parameter var ref", ref.root)
            if not block_parent.manufactured:
                self.register_symbol(
                    SymbolOccurence(
                        span=ref.span,
                        definition_location=self.variables[self.variable_map[(arg_name, function_context)]].definition_location,
                        context=function_context,
                        symbol_type=SymbolType.PARAMETER,
                        name=ref.root
                    ), ref
                )

            reporter_id = self.make_block(
                opcode=opcode,
                parent=parent,
                fields={
                    "VALUE": (
                        arg_name,
                        None
                    )
                }
            )

            return ScratchInput(
                (
                    InputType.BLOCK_AND_SHADOW,
                    reporter_id
                ),
                return_type={arg_type}
            )
        else:
            try:
                var_id = self.get_variable(ref, context)
            except NameError:
                error = Unbound(f"'{ref.root}' is not defined.", ref, data={"name": ref.root})
                self.raise_or_return(error, PLACE_HOLDER_0)
                var_id = self.define_variable(False, "var" if ref.slice_expr is None else "list", ref.root, context, None)
            
            var_type = self.variables[var_id].var_type

            if ref.slice_expr is not None:
                if var_type is VariableTypes.LIST:
                    operator_id = self.make_block(
                        opcode="data_itemoflist",
                        parent=parent,
                        inputs={
                            "INDEX": self.emit_expr(ref.slice_expr, context, block_parent, parent).value
                        },
                        fields={
                            "LIST": (
                                ref.root,
                                var_id
                            )
                        }
                    )
                    return ScratchInput(
                        (InputType.BLOCK_AND_SHADOW,
                        operator_id), 
                        {VariableTypes.VAR}
                    )
                else:
                    operator_id = self.make_block(
                        opcode="operator_letter_of",
                        parent=parent,
                        inputs={
                            "LETTER": self.emit_expr(ref.slice_expr, context, block_parent, parent).value,
                            "STRING": (
                                InputType.BLOCK_AND_SHADOW,  (
                                    DataType.VARIABLE,
                                    ref.root,
                                    var_id
                                )
                            )
                        },
                    )

                    return ScratchInput(
                        (
                            InputType.BLOCK_AND_SHADOW,
                            operator_id
                        ), {VariableTypes.STRING}
                    )

            else:
                return ScratchInput(
                    (
                        InputType.BLOCK_AND_SHADOW,
                        (
                            DataType.LIST if VariableTypes.LIST == var_type else DataType.VARIABLE,
                            ref.root,
                            var_id
                        )
                    ),
                    {var_type}
                )
        

    @staticmethod
    def _serialise_value(value: Serialisable) -> JSONValue:
        """
        Recursively converts our internal placeholder representations
        (Enum members, tuples) into the plain ints/lists that Scratch's
        project.json actually expects, e.g.:
            (InputType.SHADOW_ONLY, (DataType.NUMBER, "10"))
            -> [1, [4, "10"]]
        """
        if isinstance(value, Enum):
            return value.value

        if isinstance(value, (tuple, list)):
            return [Assembler._serialise_value(item) for item in value]

        if isinstance(value, dict):
            return {key: Assembler._serialise_value(item) for key, item in value.items()}

        return value

    def _serialise_blocks(self) -> dict[str, ScratchBlock]:
        serialized: dict[str, ScratchBlock] = {}

        for block_id, block in self.blocks.items():
            new_block = dict(block)
            new_block["inputs"] = {
                name: self._serialise_value(value)
                for name, value in block.get("inputs", {}).items()
            }
            new_block["fields"] = {
                name: self._serialise_value(value)
                for name, value in block.get("fields", {}).items()
            }
            serialized[block_id] = new_block

        return serialized

    def _serialise_variables(self, is_stage: bool=False) -> dict[str, list[Any]]:
        return {
            var_id: [variable.name, variable.initial_value]
            for var_id, variable in self.variables.items()
            if not variable.is_list and variable.shared == is_stage 
            and not self.is_parameter(var_id)
        }

    def _serialise_lists(self, is_stage: bool=False) -> dict[str, list[Any]]:
        return {
            var_id: [variable.name, variable.initial_value]
            for var_id, variable in self.variables.items()
            if variable.is_list and variable.shared == is_stage
        }

    def _serialise_broadcasts(self) -> dict[str, str]:
        return {broadcast_id.id: name for name, broadcast_id in self.messages.items()}


    def get_targets(self, f: zipfile.ZipFile) -> list[dict[str, Any]]:
        project = json.loads(f.read("project.json").decode("utf-8"))
        targets: list[dict[str, Any]] = project.get("targets", [])
        return targets

    def get_stage(self, f: zipfile.ZipFile) -> dict[str, dict[str, tuple[str, Any] | str]]:
        targets = self.get_targets(f)
        for candidate in targets:
            if candidate.get("isStage"):
                return candidate
        raise CompilerError(f"No stage target in project file.", None)

    def prepare(self, target: str | None=None, global_messages: dict[str, MessageData]={}, global_variables: dict[str, VariableData]={}, 
                uri_override: str | None=None) -> None:
        """
        Prepares the assembler to assemble the next file. It does the following:
        1. Clears blocks, variables, lists, etc. that are local to the sprite
        2. *Keeps* stage/global data
        """
        self.emitted_return = False
        self.thread_number = 0
        self.messages = {}
        self.variable_map = {}
        self.variables = {}
        self.overridable = set()

        if target is not None:
            with zipfile.ZipFile(target, "r") as f:
                stage = self.get_stage(f)

            for var_id, var_data in stage["variables"].items():
                if "compiler:" in var_data[0]:
                    continue

                self.variable_map[(var_data[0], None)] = var_id
                self.variables[var_id] = VariableData(self.uri, 
                                                      var_data[0], 
                                                      var_id, 
                                                      Context(function_context=None, layer=DEFAULT_LAYER, thread_id=DEFAULT_THREAD), 
                                                      VariableTypes.VAR, 
                                                      False, True, 
                                                      var_data[1], 
                                                      None)
                self.overridable.add(var_data[0])

            for broadcast_id, broadcast_name in stage["broadcasts"].items():
                assert isinstance(broadcast_name, str)
                self.messages[broadcast_id] = MessageData("", broadcast_name, broadcast_id)

        # we do not clear shared variables/lists, 
        for variable_id, variable_data in list(global_variables.items()):
            # do not add to existing variables if WE were the ones defining it.
            if variable_data.uri == (uri_override or self.uri):
                # signal to external LSP that we might want to delete this variable
                # continue
                self.overridable.add(variable_data.name)
            
            self.variable_map[(variable_data.name, variable_data.context.function_context)] = variable_id
            self.variables[variable_id] = VariableData(
                variable_data.uri,
                variable_data.name,
                variable_data.id,
                variable_data.context,
                variable_data.var_type,
                variable_data.is_list,
                variable_data.shared,
                variable_data.initial_value,
                None # we recreate a duplicate because we need to have the span set to None.
            )

        for message, message_data in global_messages.items():
            if message_data.uri == self.uri:
                continue

            self.messages[message] = message_data

        self.non_referenced_variables = {}
        self.non_referenced_functions = {}
        self.errors = []
        self.blocks = {}
        self.procedures = {}
        self.symbols = []
        self.current_token = None


    def _asset_id(self, path: Path) -> str:
        return hashlib.md5(path.read_bytes()).hexdigest()


    def _load_costumes(self, dir: Path, assets: list[tuple[Path, str]]) -> list[dict[str, Any]]:
        costumes: list[dict[str, Any]] = []

        if not dir.exists():
            return costumes

        for path in sorted(dir.iterdir()):
            if not path.is_file():
                continue

            extension = path.suffix.lower().lstrip(".")

            if extension not in {"svg", "png", "jpg", "jpeg"}:
                continue

            asset_id = self._asset_id(path)
            archive_name = f"{asset_id}.{extension}"

            # shallow copy for this dictionary is okay. 
            costume = COSTUME_TEMPLATE.copy()

            costumes.append(costume)
            assets.append(
                (path, archive_name)
            )

        return costumes


    def _load_wav(self, dir: Path) -> dict[str, Any]:
        asset_id = self._asset_id(dir)

        with wave.open(str(dir), "rb") as f:
            rate = f.getframerate()
            sample_count = f.getnframes()

        return {
            "name": dir.stem,
            "assetId": asset_id,
            "dataFormat": "wav",
            "md5ext": f"{asset_id}.wav",
            "rate": rate,
            "sampleCount": sample_count
        }


    def _load_mp3(self, dir: Path) -> dict[str, Any]:
        try:
            sample_rate, sample_count = mp3_metadata(dir)
        except ValueError as e:
            raise CompilerError(str(e), None)

        asset_id = self._asset_id(dir)

        return {
            "name": dir.stem,
            "assetId": asset_id,
            "dataFormat": "mp3",
            "md5ext": f"{asset_id}.mp3",
            "rate": sample_rate,
            "sampleCount": sample_count
        }


    def _load_sounds(self, dir: Path, assets: list[tuple[Path, str]]) -> list[dict[str, Any]]:
        # TODO: WAV/MP3 metadata extraction
        sounds: list[dict[str, Any]] = []
        
        if not dir.exists():
            return sounds

        # we sort the files for consistency. 
        # iterdir() does not guarantee any order, so each compilation will result in files
        # being in different places.
        # this is especially bad if you expect `costume2` to go after `costume1` in your code.
        for path in sorted(dir.iterdir()):
            if not path.is_file():
                continue

            if path.suffix.lower() == ".wav":
                sounds.append(self._load_wav(path))
            elif path.suffix.lower() == ".mp3":
                sounds.append(self._load_mp3(path))

        return sounds


    def assemble(
        self,
        programs: Mapping[str, Program],
        project_directory: str | Path,
        project_file: str | Path | None = None,
    ) -> Path:
        """
        Compiles an entire Itchy project into one Scratch .sb3.

        `programs` maps Scratch target names to their parsed Itchy programs:

            {
                "Stage": stage_program,
                "Sprite1": sprite1_program,
                "Enemy": enemy_program,
            }

        The Stage is always compiled first so that project-wide variables,
        lists, and broadcasts exist before sprites are compiled.

        Layout:

            ProjectName/
                Stage/
                    CodeFile.itch
                    costumes/
                    sounds/

                Sprite1/
                    CodeFile.itch
                    costumes/
                    sounds/
        """
        if not self.is_strict:
            raise CompilerError(
                f"is_strict mode is {self.is_strict}. "
                "Please remove the parameter or set it to true before continuing.",
                None,
            )

        project_directory = Path(project_directory)

        if not project_directory.is_dir():
            raise CompilerError(
                f"Project directory '{project_directory}' does not exist.",
                None,
            )

        if project_file is None:
            project_file = project_directory / "Scratch Project.sb3"
        else:
            project_file = Path(project_file)

        if not programs:
            raise CompilerError(
                "No targets were provided for compilation.",
                None,
            )

        # This is the project that gets modified target-by-target.
        #
        # The real output file is left untouched until every target has
        # successfully compiled.
        temporary_fd, temporary_path = tempfile.mkstemp(
            suffix=".sb3",
            dir=project_directory,
        )

        os.close(temporary_fd)

        working_project = Path(temporary_path)

        # mkstemp creates an empty file, which isn't a valid .sb3.
        working_project.unlink()

        try:
            # Start from the existing Scratch project when one exists.
            if project_file.exists():
                shutil.copy2(
                    project_file,
                    working_project,
                )

            # Stable sort: Stage first, everything else keeps the order
            # supplied by `programs`.
            ordered_programs = sorted(
                programs.items(),
                key=lambda item: item[0].lower() != "stage",
            )

            global_variables: dict[str, VariableData] = {}

            for target, program in ordered_programs:
                global_variables.update(self._collect_variables(program, target))

            for target, program in ordered_programs:
                self.compiling = target
                # prepare() already does exactly what we need here:
                #
                # - clears target-local compilation state
                # - reloads Stage variables/lists/broadcasts
                # - therefore preserves their Scratch IDs
                #
                # On the very first target there may not be a project yet.
                if project_file.exists():
                    self.prepare(str(project_file), global_variables=global_variables, uri_override=target)
                else:
                    self.prepare(global_variables=global_variables, uri_override=target)

                self._assemble_target(
                    program=program,
                    project_directory=project_directory,
                    project_file=working_project,
                    target=target,
                )

            # Final sanity check before touching the user's actual output.
            with zipfile.ZipFile(working_project, "r") as output:
                if bad_file := output.testzip():
                    raise CompilerError(
                        f"Generated project has a corrupt file: {bad_file}",
                        None,
                    )

                if "project.json" not in output.namelist():
                    raise CompilerError(
                        "Generated project does not contain project.json.",
                        None,
                    )

                # Also make sure project.json itself is valid JSON.
                json.loads(
                    output.read("project.json").decode("utf-8")
                )

            # The actual .sb3 is replaced only after the complete project
            # has compiled successfully.
            os.replace(
                working_project,
                project_file,
            )

        finally:
            if working_project.exists():
                working_project.unlink()

        return project_file


    def _ensure_costume(self, sprite_target: dict[str, Any], assets: list[tuple[Path, str]]) -> None:
        if sprite_target.get("costumes"):
            return

        costume = deepcopy(COSTUME_TEMPLATE)

        asset_id = uuid.uuid4().hex
        asset_name = f"{asset_id}.svg"

        costume["assetId"] = asset_id
        costume["md5ext"] = asset_name

        sprite_target["costumes"] = [costume]

        assets.append(
            (TEMP_FILE_SRC, asset_name)
        )


    def _assemble_target(
        self,
        program: Program,
        project_directory: Path,
        project_file: Path,
        target: str,
    ) -> None:
        """
        Compiles one target into the temporary working .sb3.

        This should only be called by assemble().
        """

        target_dir = project_directory / target

        if not target_dir.is_dir():
            raise CompilerError(
                f"Target directory '{target}' does not exist.",
                None,
            )

        if project_file.exists():
            with zipfile.ZipFile(project_file, "r") as f:
                project = json.loads(
                    f.read("project.json").decode("utf-8")
                )
        else:
            project = deepcopy(PROJECT_TEMPLATE)

        targets: list[dict[str, Any]] = project["targets"]

        stage_target: dict[str, Any] | None = None
        sprite_target: dict[str, Any] | None = None

        for candidate in targets:
            # Do NOT default this to True.
            #
            # Otherwise a malformed/non-stage target missing `isStage`
            # accidentally becomes the Stage.
            if candidate.get("isStage", False):
                stage_target = candidate

            if candidate.get("name") == target:
                sprite_target = candidate

        if stage_target is None:
            raise CompilerError(
                "This project doesn't have a stage target.",
                None,
            )

        target_is_stage = target.lower() == "stage"

        if target_is_stage:
            sprite_target = stage_target
            self.block_pool = STAGE_BLOCKS

        else:
            self.block_pool = SCRATCH_BLOCKS

            if sprite_target is None:
                sprite_target = deepcopy(SPRITE_TEMPLATE)
                sprite_target["name"] = target

                sprite_target["layoutOrder"] = (
                    max(
                        (
                            candidate.get("layoutOrder", 0)
                            for candidate in targets
                        ),
                        default=0,
                    )
                    + 1
                )

                targets.append(sprite_target)

        assert sprite_target is not None

        # -------------------------------------------------------------
        # Remember the assets belonging to this target before replacing
        # its costume/sound lists.
        # -------------------------------------------------------------

        old_target_assets: set[str] = set()

        for asset in (
            sprite_target.get("costumes", [])
            + sprite_target.get("sounds", [])
        ):
            md5ext = asset.get("md5ext")

            if isinstance(md5ext, str):
                old_target_assets.add(md5ext)

        # An asset may be shared by more than one Scratch target because
        # Scratch assets are content-addressed. Don't delete it if another
        # target still refers to it.
        other_target_assets: set[str] = set()

        for candidate in targets:
            if candidate is sprite_target:
                continue

            for asset in (
                candidate.get("costumes", [])
                + candidate.get("sounds", [])
            ):
                md5ext = asset.get("md5ext")

                if isinstance(md5ext, str):
                    other_target_assets.add(md5ext)

        removable_old_assets = (
            old_target_assets - other_target_assets
        )

        # -------------------------------------------------------------
        # Compilation
        # -------------------------------------------------------------

        self.emit_program(program)

        sprite_target["blocks"] = self._serialise_blocks()
        sprite_target["comments"] = {}

        # Use the same stage test everywhere.
        if not target_is_stage:
            sprite_target["variables"] = (
                self._serialise_variables()
            )

            sprite_target["lists"] = (
                self._serialise_lists()
            )

        # prepare() loaded the previously generated Stage state before
        # this target was emitted, so these serializers contain the
        # accumulated project-wide state while preserving existing IDs.
        stage_target["variables"] = (
            self._serialise_variables(True)
        )

        stage_target["lists"] = (
            self._serialise_lists(True)
        )

        stage_target["broadcasts"] = (
            self._serialise_broadcasts()
        )

        # -------------------------------------------------------------
        # Assets
        # -------------------------------------------------------------

        costumes_dir = target_dir / "costumes"
        sounds_dir = target_dir / "sounds"

        assets: list[tuple[Path, str]] = []

        sprite_target["costumes"] = self._load_costumes(
            costumes_dir,
            assets,
        )

        sprite_target["sounds"] = self._load_sounds(
            sounds_dir,
            assets,
        )

        self._ensure_costume(sprite_target, assets)

        dumped = json.dumps(
            project,
            ensure_ascii=True,
        )

        new_asset_names = {
            archive_name
            for _, archive_name in assets
        }

        # -------------------------------------------------------------
        # Rewrite the working .sb3 atomically
        # -------------------------------------------------------------

        temporary_fd, temporary_path = tempfile.mkstemp(
            suffix=".sb3",
            dir=project_directory,
        )

        os.close(temporary_fd)

        try:
            with zipfile.ZipFile(
                temporary_path,
                mode="w",
                compression=zipfile.ZIP_DEFLATED,
                compresslevel=9,
            ) as destination:

                if project_file.exists():
                    with zipfile.ZipFile(
                        project_file,
                        "r",
                    ) as source:

                        destination.comment = source.comment

                        for archive_entry in source.infolist():
                            archive_name = archive_entry.filename

                            if archive_name == "project.json":
                                continue

                            # This target used to own the asset and no
                            # other target still references it.
                            if archive_name in removable_old_assets:
                                continue

                            # We're about to write the new version below.
                            if archive_name in new_asset_names:
                                continue

                            destination.writestr(
                                archive_entry,
                                source.read(archive_name),
                            )

                for path, name in assets:
                    destination.write(
                        str(path.absolute()),
                        arcname=name,
                    )

                destination.writestr(
                    "project.json",
                    dumped,
                )

            with zipfile.ZipFile(
                temporary_path,
                "r",
            ) as output:
                if bad_file := output.testzip():
                    raise CompilerError(
                        f"Generated project has a corrupt file: {bad_file}",
                        None,
                    )

            os.replace(
                temporary_path,
                project_file,
            )

        finally:
            if os.path.exists(temporary_path):
                os.remove(temporary_path)