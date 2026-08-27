from __future__ import annotations
import uuid
import json
import re
import zipfile

import tempfile
import os

from typing import TypeVar, Iterable
from dataclasses import dataclass, field
from enum import Enum, StrEnum

from copy import deepcopy
from pathlib import Path
from typing import Any
from itchy.shared_templates import VariableTypes, DataType, SourceSpan, SPRITE_TEMPLATE, COSTUME_TEMPLATE, ASTNode
from itchy.errors import CompilerError, CompilerErrorCodes, UnboundError, NotReferencedError, DuplicateDefinitionError,\
    ArgumentError, NotDefinedError, InvalidTypeError, SyntaxError, TypeMismatchError, ReturnNothingError
from itchy.scratch_blocks import SCRATCH_BLOCKS, Block, Reporter, Event, Menu
from itchy.itch_ast import \
    Param, \
    Stmt, VarRef, BlockStmt, IfStmt, BreakStmt, ForInStmt, WhileStmt, AssignStmt, ReturnStmt, VarDefStmt, ForRangeStmt, FunctionCallStmt, FunctionDefStmt, EventHandlerStmt, \
    IfBranch, Expr, NumberExpr, BoolExpr, StringExpr, VarExpr, UnaryOpExpr, BinaryOpExpr, TableExpr, FunctionCallExpr, Program


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


PLACE_HOLDER_0 = ScratchInput((InputType.SHADOW_ONLY, (DataType.NUMBER, "0")), {VariableTypes.VAR}, True)


class SymbolType(StrEnum):
    PARAMETER = "parameter"
    VARIABLE = "variable"
    FUNCTION = "function"
    EVENT = "event"


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


class Assembler:
    def __init__(self, uri: str, is_strict: bool=True, compile_with_warnings: bool=False) -> None:
        """
        is_strict: whether the compiler should halt on error. Enabling this option will also disable any write to the .sb3 file.
        """
        self.variables: dict[str, VariableData] = {} # includes lists.
        self.blocks: dict[str, ScratchBlock] = {}
        self.procedures: dict[str, ProcedureInfo] = {}

        self.is_strict = is_strict
        self.uri = uri
        self.compile_with_warnings = compile_with_warnings or not is_strict
        # we don't need to worry about function "variables" since they are arguments.
        # i.e. they are not treated as variables and are treated as read-only.
        # variable name -> id
        self.variable_map: dict[tuple[str, StrOptional], str] = {}

        # when we receive global variables that we defined and have removed since, we should mark for deletion.
        self.mark_variable_for_deletion: set[str] = set()
        self.mark_message_for_deletion: set[str] = set()

        # set of variable ids that can be overriden because they were defined in the project.
        self.overridable: set[str] = set()

        self.thread_number = 0
        # name, id
        self.messages: dict[str, MessageData] = {}

        self.costumes: set[str] = set()
        self.errors: list[CompilerError] = []

        self.non_referenced_functions: dict[str, FunctionDefStmt] = {} 
        self.non_referenced_variables: dict[tuple[str, StrOptional], list[VarDefStmt | Param]] = {}

        self.symbols: list[SymbolOccurence] = []

        # for debugging/error messages
        self.current_token = None

    def raise_or_return(self, error: CompilerError, return_value: T=BlockRange(None, None, True)) -> T:
        """
        Raises an error if strict mode is on (default) or returns a value.
        """
        if self.is_strict:
            raise error
        if error.error_node and not error.error_node.dummy:
            self.errors.append(error)
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

        self.symbols.append(symbol)
    
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

        if message and message.name in self.messages:
            if message.name in self.mark_message_for_deletion and message.uri == self.uri:
                self.mark_message_for_deletion.remove(name)
            return self.messages[name].id

        broadcast_id = self.new_id()
        self.messages[name] = MessageData(self.uri, name, broadcast_id)
        return broadcast_id
    
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
            id=self.new_id(),
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
        if function.name in SCRATCH_BLOCKS:
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
                thread_id=1, 
                layer=0))
            if block_range.first is None:
                # e.g. a bare VarDefStmt, which doesn't emit a block
                continue

            first_block = self.blocks[block_range.first]
            first_block["topLevel"] = True
            first_block["parent"] = None
            first_block["x"] = x
            first_block["y"] = y

            y += 200
    
    def emit_program(self, program: Program) -> None:
        """
        Takes a program object only and emits each sequence within said program.
        uses .emit_statements() internally so statements do not connect to each other. 
        """
        context = Context(
            function_context=None,
            layer=0,
            thread_id=1
        )

        self.define_variable(False, "var", FRAME_INDEX, context, None)
        self.define_variable(False, "list", RETURN_STACK, context, None)
        # self.define_variable(False, "list", FLAG_STACK, None, None)
        self.define_variable(False, "var", STACK_ITERABLE, context, None)

        # return_helper
        push_return_frame = FunctionDefStmt(
            name=PUSH_RETURN_FRAME,
            warp=True,
            params=(Param("frame_id", "number"),),
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
            params=(Param("frame_id", "number"),),
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
            params=(Param("value", "var"), Param("frame_id", "number")),
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
            params=(Param("frame_id", "number"),),
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
        self.emit_statements(program.body)


        for variables in self.non_referenced_variables.values():
            for variable in variables:
                self.errors.append(
                    NotReferencedError(
                        f"'{variable.name}' is not referenced",
                        error_node=variable
                    )
                )

        for function in self.non_referenced_functions.values():
            self.errors.append(
                NotReferencedError(
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
            else:
                assert last is not None
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

                if len(final_return_statement.values) == 0:
                    proc_info.return_types.add(VariableTypes.STRING)
        
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
                    error = DuplicateDefinitionError(f"Variable '{stmt.name}' is shadowed by variable of same name", stmt)
                    # if not self.compile_with_warnings:
                    return self.raise_or_return(error)
                    # self.errors.append(error)

                # allow variable to override existing one in project at least once. 
                # any subsequent definitions will be counted as duplicates.
                if name in self.overridable:
                    self.overridable.remove(name)

                if name in self.mark_variable_for_deletion and shared:
                    self.mark_variable_for_deletion.remove(name)

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


    def type_check(self, a: VariableTypes, b: set[VariableTypes]):
        if a in b:
            return True

        # scratch represents the contents of a list as a space separated string... for some reason.
        # so, whenever you use it as a parameter, it is treated is a string. 
        if a == VariableTypes.LIST:
            a = VariableTypes.STRING

        if VariableTypes.LIST in b:
            b.add(VariableTypes.VAR)
            b.remove(VariableTypes.LIST)

        if a == VariableTypes.NOTHING:
            return False

        if a == VariableTypes.VAR:
            return True

        if a == VariableTypes.UNKNOWN:
            return True

        if VariableTypes.VAR in b:
            return True

        return False


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

        if self.count_args(stmt.values) > 0:
            # technically it's always 1 or 0, but this was left over for future where we might support more than one
            # return expressions (tuples)
            # for now, we don't. i'm not convinced about multiple threads accessing the same return statement. 
            return_type = self.emit_expr(stmt.values[0], context, BlockRange(None, None, True), None).return_type

            if VariableTypes.NOTHING in return_type:
                proc_data.return_types.add(VariableTypes.STRING)

            proc_data.return_types = \
                proc_data.return_types.union(return_type)
            for value in stmt.values:
                body.append(
                    FunctionCallStmt(SET_RETURN_VALUE, (value, VarExpr(VarRef(FRAME_INDEX))))
                )


        control_stop = FunctionCallStmt(
            "control_stop", (StringExpr("this script"),)
        )

        body.append(control_stop)

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
        

    def emit_scratch_block(self, stmt: FunctionCallStmt, parent: StrOptional, context: Context) -> BlockRange | None:
        if stmt.callee not in SCRATCH_BLOCKS:
            return None

        block_data = SCRATCH_BLOCKS[stmt.callee]

        if not isinstance(block_data, Block):
            return self.raise_or_return(
                InvalidTypeError(
                    f"'{stmt.callee}' should be a stack block",
                    stmt
                )
            )

        expected_args = len(block_data.inputs) + len(block_data.fields)

        if self.count_args(stmt.args) != expected_args:
            return self.raise_or_return(ArgumentError(
                    f"Block '{stmt.callee}' expects {expected_args} argument(s), got {self.count_args(stmt.args)}",
                    stmt
                ))

        inputs: dict[str, ScratchInputRaw] = {}
        fields: dict[str, ScratchFieldRaw] = {}


        block_id = self.make_block(
            opcode=stmt.callee,
            parent=parent,
        )
        block_range = BlockRange(block_id, block_id)

        # inputs come first, positionally, then fields -- matches how the
        # expected_args check above adds them together.
        index = 0

        for arg, arg_expr in zip(block_data.inputs, stmt.args):
            # arg.return_type
            if arg.name in block_data.broadcasts:
                if not isinstance(arg_expr, StringExpr):
                    inputs[arg.name] = (
                        self.emit_expr(arg_expr, context, block_range, block_id).value
                    )
                else:
                    broadcast_id = self.define_broadcast(arg_expr.value)
                    inputs[arg.name] = (InputType.SHADOW_ONLY,
                                        (DataType.BROADCAST, arg_expr.value, broadcast_id))
            elif arg.name in block_data.variables:
                if not isinstance(arg_expr, VarExpr):
                    inputs[arg.name] = (
                        self.emit_expr(arg_expr, context, block_range, block_id).value
                    )
                else:
                    try:
                        var_id = self.get_variable(arg_expr.ref, context)
                    except NameError:
                        error = UnboundError(f"'{arg_expr.ref.root}' is not defined.", arg_expr, data={"name": arg_expr.ref.root})
                        if not self.compile_with_warnings:
                            return self.raise_or_return(error)
                        self.errors.append(error)
                        var_id = self.define_variable(False, "var", arg_expr.ref.root, context, None)

                    inputs[arg.name] = (InputType.SHADOW_ONLY,
                                        (DataType.VARIABLE, arg_expr.ref.root, var_id))
            else:
                if isinstance(arg_expr, StringExpr):
                    if isinstance(arg, Menu):
                        # create the menu
                        menu_id = self.make_block(
                            opcode=arg.opcode, 
                            id=block_id,
                            fields={
                                (arg.field_name or arg.name): (
                                    arg_expr.value,
                                    None
                                )
                            })

                        inputs[arg.name] = (InputType.SHADOW_ONLY, menu_id)
                    else:
                        inputs[arg.name] = (InputType.SHADOW_ONLY, 
                            (arg.return_type, arg_expr.value))
                else:
                    inputs[arg.name] = self.emit_expr(arg_expr, context, block_range, block_id).value

            index += 1

        for field, arg_expr in zip(block_data.fields, stmt.args[len(block_data.inputs):]):
            if field.name in block_data.variables:
                if not isinstance(arg_expr, VarExpr):
                    return self.raise_or_return(InvalidTypeError(
                        f"{stmt.callee}: argument for {index} must be a variable", arg_expr
                    ))
                try:
                    fields[field.name] = (arg_expr.ref.root, self.get_variable(arg_expr.ref, context))
                except NameError:
                    error = UnboundError(f"{arg_expr.ref.root} is not defined.", arg_expr, data={"name": arg_expr.ref.root})
                    if not self.compile_with_warnings:
                        return self.raise_or_return(error)
                    self.errors.append(error)
                    fields[field.name] = (arg_expr.ref.root, self.define_variable(False, "var", arg_expr.ref.root, context, None))
                    
            elif field.name in block_data.broadcasts:
                if not isinstance(arg_expr, StringExpr):
                    return self.raise_or_return(InvalidTypeError(
                        f"{stmt.callee}: argument {index} must be a string literal", arg_expr
                    ))
                fields[field.name] = (arg_expr.value, self.define_broadcast(arg_expr.value))
            else:
                if not isinstance(arg_expr, StringExpr):
                    return self.raise_or_return(InvalidTypeError(
                        f"{stmt.callee}: argument {index} must be a string literal", arg_expr
                    ))
                
                if arg_expr.value not in field.expected and len(field.expected) > 0:
                    return self.raise_or_return(
                        ArgumentError(f"'{arg_expr.value}' is not one of {field.expected}", arg_expr)
                    )

                fields[field.name] = (arg_expr.value, None)

            index += 1

        self.blocks[block_id]["fields"] = fields
        self.blocks[block_id]["inputs"] = inputs

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

            if not self.type_check(arg_type, user_arg_type):
                failure = InvalidTypeError(
                    f"{stmt.callee}: not one of ({", ".join(i.value for i in user_arg_type)}) matches argument {index} of type {arg_type.value}", arg_expr)
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
    
    def emit_event_handler(self, stmt: EventHandlerStmt, context: Context) -> BlockRange:
        if context.function_context is not None:
            return self.raise_or_return(CompilerError(f"Cannot start a new thread while inside a function/event", stmt))

        if stmt.name not in SCRATCH_BLOCKS:
            return self.raise_or_return(NotDefinedError(f"'{stmt.name}' is not a known event", stmt))

        block_data = SCRATCH_BLOCKS[stmt.name]

        if not isinstance(block_data, Event):
            return self.raise_or_return(CompilerError(
                f"'{stmt.name}' should be a hat/event block", stmt
            ))

        # unlike Block/Reporter, an Event's `broadcasts` entries are not a
        # subset of `inputs` -- they're their own trailing group of
        # field-shaped arguments (see event_whenbroadcastreceived), so they
        # get counted on top of inputs and fields rather than overlapping.

        self.register_symbol(
            SymbolOccurence(
                span=stmt.span,
                definition_location=None,
                context=context.function_context,
                symbol_type=SymbolType.EVENT,
                name=stmt.name
            ), stmt
        )

        expected_args = len(block_data.inputs) + len(block_data.fields)

        if self.count_args(stmt.params) != expected_args:
            # we don't want to halt here
            error = ArgumentError(
                f"Event {stmt.name} expects {expected_args} argument(s), got {self.count_args(stmt.params)}",
                stmt
            )
            if self.is_strict:
                raise error

            self.errors.append(error)

            if len(stmt.params) < expected_args:
                # but continuing if this is True is going to create additional errors.
                return self.raise_or_return(error)
            

        inputs: dict[str, ScratchInputRaw] = {}
        fields: dict[str, ScratchFieldRaw] = {}

        event_id = self.make_block(
            opcode=stmt.name,
            inputs=inputs,
            fields=fields,
            top_level=True,
        )

        # inputs come first, positionally, then fields, then broadcasts --
        # matches how the expected_args check above adds them together.
        index = 0

        context = Context(
            function_context=None,
            layer=0,
            thread_id=context.thread_id
        )

        for arg, arg_expr in zip(block_data.inputs, stmt.params):
            if arg in block_data.broadcasts:
                if not isinstance(arg_expr, StringExpr):
                    inputs[arg.name] = (
                        self.emit_expr(arg_expr, context, 
                        BlockRange(event_id, event_id), event_id).value
                    )
                else:
                    broadcast_id = self.define_broadcast(arg_expr.value)
                    inputs[arg.name] = (InputType.SHADOW_ONLY,
                                        (DataType.BROADCAST, arg_expr.value, broadcast_id))
            else:
                if isinstance(arg_expr, StringExpr):
                    if isinstance(arg, Menu):
                        menu_id = self.make_block(
                            arg.opcode,
                            event_id,
                            fields={(arg.field_name or arg.name): (arg_expr.value, None)})
                        inputs[arg.name] = (InputType.SHADOW_ONLY, menu_id)
                    else:
                        inputs[arg.name] = (InputType.SHADOW_ONLY, (arg.return_type, arg_expr.value))
                else:
                    inputs[arg.name] = self.emit_expr(arg_expr, context, 
                                                      BlockRange(event_id, event_id), event_id).value
            index += 1

        field_args = stmt.params[len(block_data.inputs):]

        for field, arg_expr in zip(block_data.fields, field_args):
            if not isinstance(arg_expr, StringExpr):
                return self.raise_or_return(InvalidTypeError(f"{stmt.name}: argument {index} must be a string literal", arg_expr))
            
            if field.name in block_data.broadcasts:
                fields[field.name] = (arg_expr.value, self.define_broadcast(arg_expr.value))
            else:
                if arg_expr.value not in field.expected and len(field.expected) > 0:
                    return self.raise_or_return(ArgumentError(f"{arg_expr.value} is not one of {field.expected}", arg_expr))
                fields[field.name] = (arg_expr.value, None)
            index += 1


        # make_block does `inputs or {}` / `fields or {}`, so when they start
        # out empty it silently swaps in a fresh dict instead of keeping our
        # reference -- write back explicitly so anything filled in above
        # actually lands on the block.
        self.blocks[event_id]["inputs"] = inputs
        self.blocks[event_id]["fields"] = fields

        body = self.emit_sequence(stmt.body, event_id, context)

        if body.first is not None:
            self.blocks[event_id]["next"] = body.first

        return BlockRange(event_id, body.last or event_id)
            
    def emit_function_def(self, stmt: FunctionDefStmt, parent: StrOptional) -> BlockRange:
        if parent is not None:
            return self.raise_or_return(SyntaxError("Cannot define function inside of another", stmt, CompilerErrorCodes.REMOVE_RETURN))

        if stmt.name in self.procedures:
            return self.raise_or_return(DuplicateDefinitionError(f"Function '{stmt.name}' shadowed by function of same name.", stmt))

        context = Context(
            function_context=stmt.name, 
            thread_id=1, 
            layer=0)

        self.register_symbol(
            SymbolOccurence(
                span=stmt.span,
                definition_location=stmt.span,
                context=None,
                symbol_type=SymbolType.FUNCTION,
                name=stmt.name
            ), stmt
        )
        self.define_variable(False, "var", stmt.name + ":return", Context(function_context=None, thread_id=1, layer=0), None)

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

            argument_ids.append(arg_id)
            argument_names.append(param.name)
            argument_types.append(VariableTypes(param.type_name))

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
            error = UnboundError(f"'{stmt.iterable.root}' is not defined.", stmt.iterable, data={"name": stmt.iterable.root})
            if not self.compile_with_warnings:
                return self.raise_or_return(error)
            self.errors.append(error)
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
    
    
    def emit_assignment(self, target: VarRef, value: Expr, parent: StrOptional, context: Context) -> BlockRange:
        if context.function_context in self.procedures and \
            target.root in self.procedures[context.function_context].argument_names:
            return self.raise_or_return(CompilerError(f"Cannot assign read only argument '{target.root}'", target))

        inputs: dict[str, ScratchInputRaw] = {}

        try:
            var_id = self.get_variable(target, context)
            variable = self.variables[var_id]
            self.flag_non_referenced_variable(var_id, VarDefStmt(
                variable.var_type.value, target.root, variable.shared, span=target.span
            ), context)
        except NameError:
            error = UnboundError(f"'{target.root}' is not defined.", target, data={"name": target.root})
            if not self.compile_with_warnings:
                return self.raise_or_return(error)
            self.errors.append(error)
            var_id = self.define_variable(False, "list" if target.slice_expr is not None else "var", target.root, context, None)
        
        if target.slice_expr is not None:
            variable = self.variables[var_id]

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

            if not self.type_check(self.variables[var_id].var_type, expr.return_type):
                error = TypeMismatchError(
                    f"{target.root}: not one of ({", ".join(i.value for i in expr.return_type)}) matches {self.variables[var_id].var_type}", 
                    value)
                if not self.compile_with_warnings:
                    return self.raise_or_return(error)
                self.errors.append(error)

            inputs["VALUE"] = expr.value

            return block_range
    
    def emit_expr(self, expr: Expr, context: Context, block_parent: BlockRange, parent: StrOptional) -> ScratchInput:
        # block_id = self.new_id()
        # expression: ScratchInput = [InputType.REPORTER, block_id]
        
        match expr:
            case NumberExpr(value=value):
                return ScratchInput((InputType.SHADOW_ONLY, (DataType.NUMBER, str(value))), {VariableTypes.NUMBER})
            case StringExpr(value=value):
                if re.match(HEXCODE, value) is not None:
                    return ScratchInput((InputType.SHADOW_ONLY, (DataType.COLOR, value)), {VariableTypes.STRING})
                else:
                    return ScratchInput((InputType.SHADOW_ONLY, (DataType.STRING, value)), {VariableTypes.STRING})
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
                raise NotImplementedError("out of scope for now :v")
            case _:
                raise TypeError("Bare expression (coder sucks :/)")


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
    def emit_function_expr(self, expr: FunctionCallExpr, context: Context, block_parent: BlockRange, parent: StrOptional) -> ScratchInput:
        if expr.callee not in SCRATCH_BLOCKS and expr.callee in self.procedures:
            proc_info = self.procedures[expr.callee]

            if VariableTypes.NOTHING in proc_info.return_types:
                error = ReturnNothingError(f"{expr.callee}: not all codepaths have a return statement", expr, data={"name": expr.callee})
                return self.raise_or_return(error, PLACE_HOLDER_0)

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
                    name=expr.callee
                ), expr
            )

            setup = BlockRange(None, None)

            push_return_frame = self.emit_function_call(FunctionCallStmt(
                PUSH_RETURN_FRAME,
                (thread_id,)
            ), None, context)

            setup = self.append_range(
                setup,
                push_return_frame,
            )

            function_call = self.emit_function_call(FunctionCallStmt(
                expr.callee,
                expr.args
            ), None, context)

            setup = self.append_range(
                setup,
                function_call,
            )

            set_variable = self.emit_assignment(
                VarRef(expr.callee + ":return"),
                FunctionCallExpr(
                    "data_itemoflist",
                    (
                        BinaryOpExpr(VarExpr(VarRef(FRAME_INDEX)), "+", NumberExpr(1)),
                        VarExpr(
                            VarRef(RETURN_STACK),
                        ),
                    ),
                ),
                None,
                context,
            )

            setup = self.append_range(
                setup,
                set_variable,
            )

            pop_return_frame = self.emit_function_call(FunctionCallStmt(
                POP_RETURN_FRAME,
                (thread_id,)
            ), None, context)

            setup = self.append_range(
                setup,
                pop_return_frame,
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

            return_input.return_type = self.procedures[expr.callee].return_types

            return return_input
        else:
            block_data = SCRATCH_BLOCKS[expr.callee]

            self.register_symbol(
                SymbolOccurence(
                    span=expr.span,
                    definition_location=None,
                    context=context.function_context,
                    symbol_type=SymbolType.FUNCTION,
                    name=expr.callee
                ), expr
            )

            if not isinstance(block_data, Reporter):
                return self.raise_or_return(CompilerError(
                    f"'{expr.callee}' does not return anything.",
                    expr
                ), PLACE_HOLDER_0)
        
            expected_args = len(block_data.inputs) + len(block_data.fields)

            if self.count_args(expr.args) != expected_args:
                return self.raise_or_return(ArgumentError(
                    f"Block '{expr.callee}' expects {expected_args} argument(s), got {self.count_args(expr.args)}",
                    expr
                ), PLACE_HOLDER_0)
            
            block_id = self.make_block(
                opcode=expr.callee,
                parent=parent
            )

            inputs: dict[str, ScratchInputRaw] = {}
            fields: dict[str, ScratchFieldRaw] = {}

            index = 0
            for arg, arg_expr in zip(block_data.inputs, expr.args):
                if arg.name in block_data.variables:
                    if not isinstance(arg_expr, VarExpr):
                        inputs[arg.name] = (
                            self.emit_expr(arg_expr, context, block_parent, block_id).value
                        )
                    else:
                        try:
                            var_id = self.get_variable(arg_expr.ref, context)

                        except NameError:
                            error = UnboundError(f"{arg_expr.ref.root} is not defined.", arg_expr, data={"name": arg_expr.ref.root})
                            if not self.compile_with_warnings:
                                return self.raise_or_return(error, PLACE_HOLDER_0)
                            self.errors.append(error)
                            var_id = self.define_variable(False, "var", arg_expr.ref.root, context, None)
                        inputs[arg.name] = (InputType.SHADOW_ONLY,
                                            (DataType.VARIABLE, arg_expr.ref.root, var_id))
                else:
                    if isinstance(arg_expr, StringExpr):
                        if isinstance(arg, Menu):
                            # create the menu
                            menu_id = self.make_block(
                                opcode=arg.opcode, 
                                parent=block_id,
                                fields={
                                    arg.name: (
                                        arg_expr.value,
                                        None
                                    )
                                }, 
                                shadow=True)

                            inputs[(arg.field_name or arg.name)] = (InputType.SHADOW_ONLY, menu_id)
                        else:
                            inputs[arg.name] = (InputType.SHADOW_ONLY, (arg.return_type, arg_expr.value))
                    else:
                        inputs[arg.name] = self.emit_expr(arg_expr, context, block_parent, block_id).value
                index += 1

            for field, arg_expr in zip(block_data.fields, expr.args[len(block_data.inputs):]):
                if field.name in block_data.variables:
                    if not isinstance(arg_expr, VarExpr):
                        return self.raise_or_return(InvalidTypeError(
                            f"{expr.callee}: argument {index} must be a variable",
                            arg_expr
                        ), PLACE_HOLDER_0)
                    try:
                        fields[field.name] = (arg_expr.ref.root, self.get_variable(arg_expr.ref, context))
                    except NameError:
                        error = UnboundError(f"'{arg_expr.ref.root}' is not defined.", arg_expr, data={"name": arg_expr.ref.root})
                        if not self.compile_with_warnings:
                            return self.raise_or_return(
                                error,
                                PLACE_HOLDER_0
                            )
                        self.errors.append(error)
                        fields[field.name] = (arg_expr.ref.root, self.define_variable(False, "var", arg_expr.ref.root, context, None))
                else:
                    if not isinstance(arg_expr, StringExpr):
                        return self.raise_or_return(InvalidTypeError(
                            f"{expr.callee}: argument {index} must be a string literal",
                            arg_expr
                        ), PLACE_HOLDER_0)

                    if arg_expr.value not in field.expected and len(field.expected) > 0:
                        return self.raise_or_return(ArgumentError(f"'{arg_expr.value}' is not one of {field.expected}", arg_expr),
                                                    PLACE_HOLDER_0)

                    fields[field.name] = (arg_expr.value, None)
                index += 1

                
            self.blocks[block_id]["fields"] = fields
            self.blocks[block_id]["inputs"] = inputs

            return ScratchInput(
                (InputType.BLOCK_ONLY if VariableTypes.BOOL in block_data.return_type else InputType.BLOCK_AND_SHADOW, block_id), block_data.return_type
            )

    def emit_unary_expr(self, op: str, value: Expr, context: Context, block_parent: BlockRange, parent: StrOptional) -> ScratchInput:
        block_id = self.new_id()
        if op in {"not", "!"}:
            self.make_block(
                opcode="operator_not",
                id=block_id,
                parent=parent,
                inputs={
                    "OPERAND": self.emit_expr(value, context, block_parent, block_id).value,
                },
            )
            return ScratchInput((InputType.BLOCK_ONLY, block_id), {VariableTypes.BOOL})

        if op == "-":
            if isinstance(value, NumberExpr):
                return self.emit_expr(
                    NumberExpr(-1 * value.value, span=value.span), context, block_parent, parent
                )
            else:
                self.make_block(
                    opcode="operator_multiply",
                    id=block_id,
                    parent=parent,
                    inputs={
                        "NUM1": (InputType.SHADOW_ONLY, (DataType.NUMBER, "-1")),
                        "NUM2": self.emit_expr(value, context, block_parent, block_id).value,
                    },
                )
                return ScratchInput((InputType.BLOCK_ONLY, block_id), {VariableTypes.NUMBER})

        raise NotImplementedError(f"Unsupported unary operator: {op}")
    
    def emit_binary_expr(self, left: Expr, op: str, right: Expr, context: Context, block_parent: BlockRange, parent: StrOptional) -> ScratchInput:
        block_id = self.new_id()

        left_expr = self.emit_expr(left, context, block_parent, block_id)
        right_expr = self.emit_expr(right, context, block_parent, block_id)

        if op == "in":
            if VariableTypes.LIST in right_expr.return_type:
                if not isinstance(right, VarExpr):
                    return self.raise_or_return(InvalidTypeError(f"Right expression must be a list", right), PLACE_HOLDER_0)

                try:
                    list_id = self.get_variable(right.ref, context)
                except NameError:
                    error = UnboundError(f"'{right.ref.root}' is not defined", right, data={"name": right})
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
                    (InputType.BLOCK_AND_SHADOW, block_id), {VariableTypes.BOOL}
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
            (InputType.BLOCK_ONLY if VariableTypes.BOOL in return_type else InputType.BLOCK_AND_SHADOW, block_id),
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
                    InputType.BLOCK_ONLY,
                    reporter_id
                ),
                return_type={arg_type}
            )
        else:
            try:
                var_id = self.get_variable(ref, context)
            except NameError:
                error = UnboundError(f"'{ref.root}' is not defined.", ref, data={"name": ref.root})
                if not self.compile_with_warnings:
                    return self.raise_or_return(error, PLACE_HOLDER_0)
                self.errors.append(error)
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
                        (InputType.BLOCK_ONLY,
                        operator_id)
                    )
                else:
                    operator_id = self.make_block(
                        opcode="operator_letter_of",
                        parent=parent,
                        inputs={
                            "LETTER": self.emit_expr(ref.slice_expr, context, block_parent, parent).value,
                            "STRING": (
                                InputType.BLOCK_ONLY,  (
                                    DataType.VARIABLE,
                                    ref.root,
                                    var_id
                                )
                            )
                        },
                    )

                    return ScratchInput(
                        (
                            InputType.BLOCK_ONLY,
                            operator_id
                        ), {VariableTypes.STRING}
                    )

            else:
                return ScratchInput(
                    (
                        InputType.SHADOW_ONLY,
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

    def prepare(self, target: str | None=None, global_messages: dict[str, MessageData]={}, global_variables: dict[str, VariableData]={}) -> None:
        """
        Prepares the assembler to assemble the next file. It does the following:
        1. Clears blocks, variables, lists, etc. that are local to the sprite
        2. *Keeps* stage/global data
        """
        self.thread_number = 0
        self.messages = {}
        self.mark_variable_for_deletion = set()
        self.mark_message_for_deletion = set()
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
                                                      Context(function_context=None, layer=0, thread_id=1), 
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
            if variable_data.uri == self.uri:
                # signal to external LSP that we might want to delete this variable
                self.mark_variable_for_deletion.add(variable_data.name)
                continue
                # self.overridable.add(variable_data.name)

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
                self.mark_message_for_deletion.add(message)
            self.messages[message] = message_data

        for variable_id in list(self.variables):
            variable_data = self.variables[variable_id]
            
            if variable_data.shared:
                continue

            del self.variable_map[(variable_data.name, variable_data.context.function_context)]
            del self.variables[variable_id]

        self.non_referenced_variables = {}
        self.non_referenced_functions = {}
        self.errors = []
        self.blocks = {}
        self.procedures = {}
        self.symbols = []
        self.current_token = None


    def assemble(self, program: Program, project_file: str, target: str) -> None:
        """
        Compiles `program` and injects the result into an existing Scratch
        project file.

        `project_file` is the path to a project.json (or an already-unzipped
        project.json from inside an .sb3) that already contains at least one
        sprite target. `target` is the name of the sprite to inject the
        compiled blocks/variables/lists/broadcasts into. `context` is
        forwarded to the emitter to establish variable scoping and should be
        None for a normal top-level program.
        """
        if not self.is_strict:
            raise CompilerError(f"is_strict mode is False. Remove the parameter before continuing.", None)
        
        with zipfile.ZipFile(project_file, "r") as f:
            project = json.loads(f.read("project.json").decode("utf-8"))

        targets: list[dict[str, Any]] = project.get("targets", [])

        stage_target = None
        sprite_target = None

        for candidate in targets:
            if candidate.get("name") == target:
                sprite_target = candidate
            if candidate.get("isStage", False):
                stage_target = candidate

        assets_to_move: list[str] = []

        if sprite_target is None:
            next_layer_order = max((candidate.get("layerOrder", 0) for candidate in targets), default=0)
            sprite_target = deepcopy(SPRITE_TEMPLATE)
            sprite_target["name"] = target
            sprite_target["layerOrder"] = next_layer_order

            costume = COSTUME_TEMPLATE.copy()
            # clone the empty svg

            # we don't use the self.new_id() because we want 36 characters
            asset_id = uuid.uuid4().hex
            costume["assetId"] = asset_id
            costume["md5ext"] = asset_id + ".svg"

            sprite_target["costumes"].append(costume)

            assets_to_move.append(asset_id + ".svg")

            targets.append(sprite_target)
            print(f"Sprite {target} not found. Creating a new one.")
        
        # shouldn't be possible if provided an .sb3 file, but here for sanity's sake. 
        if stage_target is None:
            raise CompilerError(f"Target project does not have stage.", None)

        self.emit_program(program)

        sprite_target["variables"] = self._serialise_variables()
        sprite_target["lists"] = self._serialise_lists()
        sprite_target["broadcasts"] = self._serialise_broadcasts()
        sprite_target["blocks"] = self._serialise_blocks()
        sprite_target["comments"] = {}

        stage_target["variables"] = self._serialise_variables(True)
        stage_target["lists"] = self._serialise_lists(True)

        json_dumped = json.dumps(project, ensure_ascii=True)

        project_directory = os.path.dirname(os.path.abspath(project_file))
        temporary_fd, temporary_path = tempfile.mkstemp(
            suffix=".sb3",
            dir=project_directory,
        )
        os.close(temporary_fd)

        try:
            # create a temporary file that contains the archive of existing assets so we don't override them when writing
            # to the file. 
            with zipfile.ZipFile(project_file, "r") as source:
                
                # open the temporary file and copy the contents over.
                with zipfile.ZipFile(
                    temporary_path,
                    mode="w",
                    compression=zipfile.ZIP_DEFLATED,
                    compresslevel=9,
                ) as destination:
                    destination.comment = source.comment

                    for archive_entry in source.infolist():
                        if archive_entry.filename == "project.json":
                            continue

                        destination.writestr(
                            archive_entry,
                            source.read(archive_entry.filename),
                        )

                    # move over the created temporary assets
                    for asset_name in assets_to_move:
                        destination.write(str(TEMP_FILE_SRC.absolute()), arcname=asset_name)

                    destination.writestr("project.json", json_dumped)

            # Validate the completed archive before replacing the original.
            with zipfile.ZipFile(temporary_path, "r") as completed_archive:
                bad_file = completed_archive.testzip()
                if bad_file is not None:
                    raise CompilerError(
                        f"Generated Scratch archive contains a corrupt file: {bad_file}",
                        None
                    )

            os.replace(temporary_path, project_file)
        finally:
            if os.path.exists(temporary_path):
                os.remove(temporary_path)

    
"""
"targets": [
        {
            "isStage": True,
            "name": "Stage",
            "variables": {},
            "lists": {},
            "broadcasts": {},
            "blocks": {},
            "comments": {},
            "currentCostume": 0,
            "costumes": [],
            "sounds": [],
            "volume": 100,
            "layerOrder": 0,
            "tempo": 60,
            "videoTransparency": 50,
            "videoState": "on",
            "textToSpeechLanguage": None,
        },
        {
            "isStage": False,
            "name": "Sprite1",
            "variables": self.variables,
            "lists": {},
            "broadcasts": {},
            "blocks": self.blocks,
            "comments": {},
            "currentCostume": 0,
            "costumes": [],
            "sounds": [],
            "volume": 100,
            "layerOrder": 1,
            "visible": True,
            "x": 0,
            "y": 0,
            "size": 100,
            "direction": 90,
            "draggable": False,
            "rotationStyle": "all around",
        },
    ],
    "monitors": [],
    "extensions": [],
    "meta": {
        "semver": "3.0.0",
        "vm": "0.2.0",
        "agent": "python",
    },
}
"""