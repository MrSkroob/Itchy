from __future__ import annotations
import uuid
import json
import re
import zipfile

import tempfile
import os

from typing import TypeVar
from dataclasses import dataclass, field
from enum import Enum

from copy import deepcopy
from pathlib import Path
from typing import Any
from itchy.shared_templates import VariableTypes, DataType, SPRITE_TEMPLATE, COSTUME_TEMPLATE, VARIABLE_TYPE_TO_USER_TYPES
from itchy.scratch_blocks import SCRATCH_BLOCKS, Block, Reporter, Event, Menu
from itchy.itch_ast import \
    ASTNode, Param, \
    Stmt, VarRef, BlockStmt, IfStmt, BreakStmt, ForInStmt, WhileStmt, AssignStmt, ReturnStmt, VarDefStmt, ForRangeStmt, FunctionCallStmt, FunctionDefStmt, EventHandlerStmt, \
    IfBranch, Expr, NumberExpr, BoolExpr, StringExpr, VarExpr, UnaryOpExpr, BinaryOpExpr, TableExpr, FunctionCallExpr, Program


T = TypeVar("T")
ScratchBlock = dict[str, Any]
StrOptional = str | None

RETURN_STACK = "compiler:return_values"
FLAG_STACK = "compiler:return_flags" 
PUSH_RETURN_FRAME = "compiler:push_return_frame"
SET_RETURN_VALUE = "compiler:set_return_value"
POP_RETURN_FRAME = "compiler:pop_return_frame"


HEXCODE = re.compile(r"^#(?:[0-9a-fA-F]{3}){1,2}$")
ROOT = Path(__file__).parent
TEMP_FILE_SRC = ROOT / "assets" / "empty.svg"

# return types as tuples are OKAY, because serialisation converts them all to lists anyway.
ScratchInputRaw = tuple["InputType", tuple["DataType", str] | tuple["DataType", str, str]] | tuple["InputType", str]
ScratchFieldRaw = tuple[str, None] | tuple[str, str]

@dataclass(frozen=True)
class ScratchInput:
    value: ScratchInputRaw
    return_type: VariableTypes = VariableTypes.UNKNOWN
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


PLACE_HOLDER_0 = ScratchInput((InputType.SHADOW_ONLY, (DataType.NUMBER, "0")), VariableTypes.NUMBER, True)


class CompilerError(Exception):
    def __init__(self, message: str, error_node: ASTNode | None) -> None:
        self.error_node = error_node
        super().__init__(message)


class NotDefinedError(CompilerError):
    pass


class InvalidTypeError(CompilerError):
    pass


class ArgumentError(CompilerError):
    pass


class SyntaxError(CompilerError):
    pass


@dataclass
class BlockRange:
    first: StrOptional
    last: StrOptional
    manufactured: bool=False


@dataclass(frozen=True)
class VariableData:
    name: str
    id: str
    context: StrOptional
    var_type: VariableTypes
    is_list: bool
    shared: bool
    initial_value: Any


@dataclass
class ProcedureInfo:
    name: str
    prototype_id: str
    proccode: str
    argument_ids: tuple[str, ...]
    argument_names: tuple[str, ...]
    argument_defaults: tuple[str, ...]

    # compiler only. does not get serialised
    argument_types: tuple[VariableTypes, ...]

    # if applicable
    return_types: set[VariableTypes]=field(default_factory=set[VariableTypes])


class Assembler:
    def __init__(self, is_strict: bool=True) -> None:
        """
        is_strict: whether the compiler should halt on error. Enabling this option will also disable any write to the .sb3 file.
        """
        self.variables: dict[str, VariableData] = {} # includes lists.
        self.blocks: dict[str, ScratchBlock] = {}
        self.procedures: dict[str, ProcedureInfo] = {}

        self.is_strict = is_strict
        # we don't need to worry about function "variables" since they are arguments.
        # i.e. they are not treated as variables and are treated as read-only.
        # variable name -> id
        self.variable_map: dict[str, str] = {}

        self.messages: dict[str, str] = {}

        self.costumes: set[str] = set()
        self.errors: list[CompilerError] = []

        # for debugging/error messages
        self.current_token = None

    def raise_or_return(self, error: CompilerError, return_value: T=BlockRange(None, None, True)) -> T:
        """
        Raises an error if strict mode is on (default) or returns a value.
        """
        if self.is_strict:
            raise error
        self.errors.append(error)
        return return_value
    
    def new_id(self) -> str:
        return uuid.uuid4().hex[:20]

    def add_block(self, block: ScratchBlock, id: StrOptional) -> str:
        block_id = id or self.new_id()
        self.blocks[block_id] = block
        return block_id
    
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
    

    def get_variable(self, name: str) -> str:
        """
        Returns a variable ID without any extra functionality.
        Do this when you strictly expect the variable to exist, and want to error if it wasn't implicitly/explicitly defined previously.
        """
        key = name
        if key not in self.variable_map:
            raise NameError(f"variable {name} not defined!")
        return self.variable_map[key]

    def define_broadcast(self, name: str) -> str:
        if name in self.messages:
            return self.messages[name]

        broadcast_id = self.new_id()
        self.messages[name] = broadcast_id
        return broadcast_id
    
    def assert_writable_name(self, var_name: str, context: StrOptional) -> None:
        if context is None:
            return

        if context not in self.procedures:
            return
        
        procedure = self.procedures[context]
        if var_name in procedure.argument_names:
            raise ValueError(f"{var_name} IS READ ONLY!!!")

    def define_variable(self, shared: bool, type_name: str, name: str, context: StrOptional) -> str:
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

        key = name

        if key in self.variable_map:
            return self.variable_map[name]

        var_id = self.new_id()

        variable = VariableData(
            name=name, 
            id=self.new_id(),
            context=context,
            var_type=VariableTypes(type_name),
            is_list=is_list,
            shared=shared,
            initial_value=default_value
        )

        self.variables[var_id] = variable
        self.variable_map[key] = var_id

        return var_id
    
    def emit_program(self, program: Program) -> None:
        """
        Emits every top-level statement in `program` as its own independent
        script. Unlike emit_sequence, this does NOT chain the statements
        together via next/parent -- each top-level hat (or orphan stack) is
        its own script in Scratch, so they only need to be spaced apart on
        the canvas, not linked to one another.
        """
        x, y = 100, 100

        self.define_variable(False, "list", RETURN_STACK, None)
        self.define_variable(False, "list", FLAG_STACK, None)

        # return_helper
        push_return_frame = FunctionDefStmt(
            name=PUSH_RETURN_FRAME,
            params=(),
            body=(
                FunctionCallStmt("data_addtolist", (StringExpr(""), VarExpr(VarRef(RETURN_STACK)))),
                FunctionCallStmt("data_addtolist", (StringExpr("false"), VarExpr(VarRef(FLAG_STACK)))),
            )
        )

        # return_helper
        set_return_value = FunctionDefStmt(
            name=SET_RETURN_VALUE,
            params=(Param("value", "var"),),
            body=(
                FunctionCallStmt("data_replaceitemoflist", (FunctionCallExpr("data_lengthoflist", 
                                                                            (VarExpr(
                                                                                VarRef(RETURN_STACK)),)), 
                                                            VarExpr(
                                                                VarRef("value")), 
                                                            VarExpr(
                                                                VarRef(RETURN_STACK)))),
                FunctionCallStmt("data_replaceitemoflist", (FunctionCallExpr("data_lengthoflist", 
                                                                                            (VarExpr(
                                                                                                VarRef(FLAG_STACK)),)), 
                                                                            StringExpr("true"),
                                                                            VarExpr(
                                                                                VarRef(FLAG_STACK)))),
            )
        )

        # # return helper
        pop_return_frame = FunctionDefStmt(
            name=POP_RETURN_FRAME,
            params=(),
            body=(
                FunctionCallStmt("data_deleteoflist", (FunctionCallExpr("data_lengthoflist", 
                                                                                        (VarExpr(
                                                                                            VarRef(RETURN_STACK)),)), 
                                                                        VarExpr(
                                                                            VarRef(RETURN_STACK)))),
                FunctionCallStmt("data_deleteoflist", (FunctionCallExpr("data_lengthoflist", 
                                                                                                        (VarExpr(
                                                                                                            VarRef(FLAG_STACK)),)), 
                                                                                        VarExpr(
                                                                                            VarRef(FLAG_STACK)))),
            )
        )

        pre_defines = (push_return_frame, set_return_value, pop_return_frame)

        def emit_statements(statements: tuple[Stmt, ...]):
            nonlocal x
            nonlocal y
            for stmt in statements:
                block_range = self.emit_stmt(stmt, None, None)
                if block_range.first is None:
                    # e.g. a bare VarDefStmt, which doesn't emit a block
                    continue

                first_block = self.blocks[block_range.first]
                first_block["topLevel"] = True
                first_block["parent"] = None
                first_block["x"] = x
                first_block["y"] = y

                y += 200

        emit_statements(pre_defines)
        emit_statements(program.body)
    
    
    def emit_sequence(
            self,
            statements: tuple[Stmt, ...],
            parent: StrOptional,
            context: StrOptional
        ) -> BlockRange:
        first: StrOptional = None
        last: StrOptional = None

        for stmt in statements:
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
            
            last = emitted.last

        
        return BlockRange(first, last)
    
    def emit_stmt(self, stmt: Stmt, parent: StrOptional, context: StrOptional) -> BlockRange:
        match stmt:
            case BlockStmt(body=body):
                return self.emit_sequence(body, parent, context)
            case VarDefStmt(shared=shared, type_name=type_name, name=name):
                if type_name not in {VariableTypes.VAR.value, VariableTypes.LIST.value, VariableTypes.BOOL.value}:
                    return self.raise_or_return(InvalidTypeError(f"Invalid variable type: {type_name}.\
                                                                 Scratch only permits var, list and bool.", stmt))
                self.define_variable(shared, type_name, name, context)
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


    def emit_return(self, stmt: ReturnStmt, parent: StrOptional, context: StrOptional) -> BlockRange:
        if context is None:
            return self.raise_or_return(SyntaxError("'return' outside of function", stmt))

        proc_data = self.procedures[context]
        return_variable = proc_data.name + ":return"
        self.define_variable(False, "var", return_variable, None)

        body: list[Stmt] = []

        if len(stmt.values) > 0:
            # technically it's always 1 or 0, but this was left over for future where we might support more than one
            # return expressions (tuples)
            proc_data.return_types.add(self.emit_expr(stmt.values[0], context, BlockRange(None, None), None).return_type)
            for value in stmt.values:
                body.append(
                    FunctionCallStmt(SET_RETURN_VALUE, (value,))
                )

        control_stop = FunctionCallStmt(
            "control_stop", (StringExpr("this script"),)
        )

        body.append(control_stop)

        if len(stmt.values) > 0:
            return self.emit_if(
                IfStmt(
                    branches=(IfBranch(
                        condition=BinaryOpExpr(FunctionCallExpr("data_itemoflist", (
                            FunctionCallExpr("data_lengthoflist", (VarExpr(VarRef(FLAG_STACK)),
                                                                                    )),
                            VarExpr(VarRef(FLAG_STACK)), 
                                                                                    
                                                                                )
                                                                ), 
                                                                "==", 
                                                                StringExpr("false")),
                        body=tuple(body)
                    ),),
                    else_body=()
                ),
                parent=parent,
                context=context
            )
        else:
            return self.emit_function_call(control_stop, parent, context)
        

    def emit_scratch_block(self, stmt: FunctionCallStmt, parent: StrOptional, context: StrOptional) -> BlockRange | None:
        if stmt.callee not in SCRATCH_BLOCKS:
            return None

        block_data = SCRATCH_BLOCKS[stmt.callee]

        if not isinstance(block_data, Block):
            return self.raise_or_return(
                InvalidTypeError(
                    f"{stmt.callee} should be a stack block",
                    stmt
                )
            )

        expected_args = len(block_data.inputs) + len(block_data.fields)

        if len(stmt.args) != expected_args:
            raise ArgumentError(
                f"Block {stmt.callee} expects {expected_args} argument(s), got {len(stmt.args)}",
                stmt
            )

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
            if arg in block_data.broadcasts:
                if not isinstance(arg_expr, StringExpr):
                    inputs[arg.name] = (
                        self.emit_expr(arg_expr, context, block_range, block_id).value
                    )
                else:
                    broadcast_id = self.define_broadcast(arg_expr.value)
                    inputs[arg.name] = (InputType.SHADOW_ONLY,
                                        (DataType.BROADCAST, arg_expr.value, broadcast_id))
            elif arg in block_data.variables:
                if not isinstance(arg_expr, VarExpr):
                    inputs[arg.name] = (
                        self.emit_expr(arg_expr, context, block_range, block_id).value
                    )
                else:
                    try:
                        var_id = self.get_variable(arg_expr.ref.root)
                        inputs[arg.name] = (InputType.SHADOW_ONLY,
                                            (DataType.VARIABLE, arg_expr.ref.root, var_id))
                    except NameError:
                        return self.raise_or_return(NotDefinedError(f"{arg_expr.ref.root} not defined.", arg_expr))
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
                    fields[field.name] = (arg_expr.ref.root, self.get_variable(arg_expr.ref.root))
                except NameError:
                    return self.raise_or_return(NotDefinedError(f"{arg_expr.ref.root} not defined.", arg_expr))
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
                        ArgumentError(f"{arg_expr.value} is not one of {field.expected}", arg_expr)
                    )

                fields[field.name] = (arg_expr.value, None)

            index += 1

        self.blocks[block_id]["fields"] = fields
        self.blocks[block_id]["inputs"] = inputs

        return block_range
            
    def emit_function_call(self, stmt: FunctionCallStmt, parent: StrOptional, context: StrOptional) -> BlockRange:
        if stmt.callee not in self.procedures:
            # is either a custom scratch block or a hallucination :v
            block_range = self.emit_scratch_block(stmt, parent, context)
            if block_range is None:
                return self.raise_or_return(NotDefinedError(f"Procedure {stmt.callee} is not defined and is not a valid scratch block.", stmt))
            return block_range

        info = self.procedures[stmt.callee]
        
        if len(stmt.args) != len(info.argument_ids):
            return self.raise_or_return(ArgumentError(
                f"Function {stmt.callee} expects {len(info.argument_ids)} arguments, "
                f"got {len(stmt.args)}",
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
        for arg_id, arg_type, arg_expr in zip(info.argument_ids, info.argument_types, stmt.args):
            emitted_arg = self.emit_expr(
                arg_expr,
                context,
                block_range,
                block_id,
            )

            arg_type = VARIABLE_TYPE_TO_USER_TYPES.get(arg_type, arg_type)
            user_arg_type = VARIABLE_TYPE_TO_USER_TYPES.get(emitted_arg.return_type, emitted_arg.return_type)

            if arg_type != user_arg_type:
                return self.raise_or_return(InvalidTypeError(f"{stmt.callee}: argument {index} expected {arg_type} not {user_arg_type}", arg_expr))
            
            inputs[arg_id] = emitted_arg.value
            index += 1

        self.blocks[block_id]["mutation"] = {
            "tagName": "mutation",
            "children": [],
            "proccode": info.proccode,
            "argumentids": json.dumps(list(info.argument_ids)),
            "warp": "false",
        }

        return block_range
    
    def emit_event_handler(self, stmt: EventHandlerStmt, context: StrOptional) -> BlockRange:
        if context is not None:
            return self.raise_or_return(CompilerError(f"Cannot start a new thread while inside a function/event", stmt))

        if stmt.name not in SCRATCH_BLOCKS:
            return self.raise_or_return(NotDefinedError(f"{stmt.name} is not a known event", stmt))

        block_data = SCRATCH_BLOCKS[stmt.name]

        if not isinstance(block_data, Event):
            return self.raise_or_return(CompilerError(
                f"{stmt.name} should be a hat/event block", stmt
            ))

        # unlike Block/Reporter, an Event's `broadcasts` entries are not a
        # subset of `inputs` -- they're their own trailing group of
        # field-shaped arguments (see event_whenbroadcastreceived), so they
        # get counted on top of inputs and fields rather than overlapping.
        expected_args = len(block_data.inputs) + len(block_data.fields)

        if len(stmt.params) != expected_args:
            return self.raise_or_return(ArgumentError(
                f"Event {stmt.name} expects {expected_args} argument(s), got {len(stmt.params)}",
                stmt
            ))

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

        for arg, arg_expr in zip(block_data.inputs, stmt.params):
            if arg in block_data.broadcasts:
                if not isinstance(arg_expr, StringExpr):
                    inputs[arg.name] = (
                        self.emit_expr(arg_expr, None, BlockRange(event_id, event_id), event_id).value
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
                    inputs[arg.name] = self.emit_expr(arg_expr, None, BlockRange(event_id, event_id), event_id).value
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

        body = self.emit_sequence(stmt.body, event_id, stmt.name)

        if body.first is not None:
            self.blocks[event_id]["next"] = body.first

        return BlockRange(event_id, body.last or event_id)
            
    def emit_function_def(self, stmt: FunctionDefStmt, parent: StrOptional) -> BlockRange:
        if parent is not None:
            return self.raise_or_return(SyntaxError("Cannot define function inside of another", stmt))

        self.define_variable(False, "var", stmt.name + ":return", None)

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

        for param in stmt.params:
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


            self.define_variable(False, param.type_name, param.name, definition_id)

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

        self.procedures[stmt.name] = ProcedureInfo(
            name=stmt.name,
            prototype_id=prototype_id,
            proccode=proccode,
            argument_ids=argument_ids_tuple,
            argument_names=argument_names_tuple,
            argument_defaults=argument_defaults_tuple,
            argument_types=argument_types_tuple
        )

        # for concise' sake, append a return statement always
        body_range = self.emit_sequence(stmt.body, definition_id, stmt.name)

        if body_range.first is not None:
            self.blocks[definition_id]["next"] = body_range.first
            self.blocks[body_range.first]["parent"] = definition_id

        return BlockRange(
            first=definition_id,
            last=body_range.last or definition_id,
        )
    
    def emit_for_range(self, stmt: ForRangeStmt, parent: StrOptional, context: StrOptional):
        # iterable variable

        try:
            self.assert_writable_name(stmt.variable, context)
        except NameError:
            return self.raise_or_return(CompilerError(f"Cannot override read only argument {stmt.variable}", stmt.start))

        var_id = self.define_variable(False, "var", stmt.variable, context)
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
    
    def emit_for_in(self, stmt: ForInStmt, parent: StrOptional, context: StrOptional):
        list_variable_name = "compiler:" + self.new_id()
        try:
            iterable_id = self.get_variable(stmt.iterable.root)
        except NameError:
            return self.raise_or_return(NotDefinedError(f"{stmt.iterable.root} not defined.", stmt.iterable))

        self.assert_writable_name(stmt.variable, context)
        # we *still* need this id to be unique, because even if it's in a for loop, scratch considers it global.
        # so we need a variable with a unique name to avoid amiguity.
        var_id = self.define_variable(False, "var", list_variable_name, context) # not to be used by the programmer, so is given garbage name.
        var_list_item_id = self.define_variable(False, "var", stmt.variable, context) # variable type doesn't matter as long as it's not 'list'

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
    
    def emit_while(self, stmt: WhileStmt, parent: StrOptional, context: StrOptional):
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
            
    def emit_if(self, stmt: IfStmt, parent: StrOptional, context: StrOptional) -> BlockRange:
        return self.emit_if_branch_chain(
            stmt.branches,
            stmt.else_body,
            0,
            parent,
            context,
        )

    def emit_if_branch_chain(self, branches: tuple[IfBranch, ...], else_body: tuple[Stmt, ...], index: int, parent: StrOptional, context: StrOptional):
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

        then_blocks = self.emit_sequence(branch.body, block_id, context)

        if then_blocks.first is not None:
            self.blocks[block_id]["inputs"]["SUBSTACK"] = (InputType.BLOCK_ONLY, then_blocks.first)

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
    
    
    def emit_assignment(self, target: VarRef, value: Expr, parent: StrOptional, context: StrOptional) -> BlockRange:
        if context in self.procedures and target.root in self.procedures[context].argument_names:
            return self.raise_or_return(CompilerError(f"Cannot assign read only argument {target.root}", target))

        inputs: dict[str, ScratchInputRaw] = {}
        
        if target.slice_expr is not None:
            try:
                var_id = self.get_variable(target.root)
            except NameError:
                return self.raise_or_return(NotDefinedError(f"{target.root} not defined.", target))
            
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
            # if 
            try:
                var_id = self.get_variable(target.root) 
            except NameError:
                return self.raise_or_return(NotDefinedError(f"{target.root} not defined.", target))
            
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
            inputs["VALUE"] = self.emit_expr(
                value, context, block_range, block_id
            ).value

            return block_range
    
    def emit_expr(self, expr: Expr, context: StrOptional, block_parent: BlockRange, parent: StrOptional) -> ScratchInput:
        # block_id = self.new_id()
        # expression: ScratchInput = [InputType.REPORTER, block_id]
        
        match expr:
            case NumberExpr(value=value):
                return ScratchInput((InputType.SHADOW_ONLY, (DataType.NUMBER, str(value))), VariableTypes.NUMBER)
            case StringExpr(value=value):
                if re.match(HEXCODE, value) is not None:
                    return ScratchInput((InputType.SHADOW_ONLY, (DataType.COLOR, value)), VariableTypes.STRING)
                else:
                    return ScratchInput((InputType.SHADOW_ONLY, (DataType.STRING, value)), VariableTypes.STRING)
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

                return ScratchInput((InputType.BLOCK_ONLY, operator_id), VariableTypes.BOOL)
            case VarExpr(ref=ref):
                return self.emit_var_ref(ref, context, block_parent, parent)
            case UnaryOpExpr(op=op, value=value):
                return self.emit_unary_expr(op, value, context, block_parent, parent)
            case BinaryOpExpr(left=left, op=op, right=right):
                return self.emit_binary_expr(left, op, right, context, block_parent, parent)
            case FunctionCallExpr():
                # only available for scratch built-ins :v
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
        """
        Inserts command blocks required by an expression immediately before
        the statement block that consumes the expression.

        The consumer is ``block_range.last``.  ``block_range.first`` may
        already point to setup generated by an earlier argument, so further
        setup is inserted after the existing setup and before the consumer.

        This is intended for blocks that are still being emitted.  The outer
        ``next``/``SUBSTACK`` connection is established later by
        ``emit_sequence`` or the surrounding control-block emitter.
        """
        if setup.first is None:
            return

        if block_range.first is None or block_range.last is None:
            raise ValueError(
                "Cannot add expression setup to an empty block range"
            )

        assert setup.last is not None

        consumer_id = block_range.last
        consumer = self.blocks[consumer_id]

        if self.blocks[setup.last]["next"] is not None:
            raise ValueError(
                "Expression setup already has a block after its final block"
            )

        if block_range.first == consumer_id:
            # This is the first setup sequence inserted before the consumer.
            outer_parent = consumer["parent"]

            self.blocks[setup.first]["parent"] = outer_parent
            self.blocks[setup.last]["next"] = consumer_id
            consumer["parent"] = setup.last
            block_range.first = setup.first
            return

        # Existing setup already leads into the consumer. Append the new
        # setup immediately before the consumer to preserve left-to-right
        # argument evaluation.
        previous_id = consumer["parent"]
        if previous_id is None:
            raise ValueError(
                f"Consumer block {consumer_id!r} has no preceding setup block"
            )

        previous = self.blocks[previous_id]
        if previous.get("next") != consumer_id:
            raise ValueError(
                f"Block {previous_id!r} is the consumer's parent but does "
                f"not point to {consumer_id!r} through 'next'"
            )

        previous["next"] = setup.first
        self.blocks[setup.first]["parent"] = previous_id
        self.blocks[setup.last]["next"] = consumer_id
        consumer["parent"] = setup.last


    def replace_substack_child(
        self,
        parent_id: str,
        old_child: str,
        new_child: str,
    ) -> bool:
        parent_block: dict[str, ScratchBlock] = self.blocks[parent_id]

        for input_name, input_value in parent_block["inputs"].items():
            if not input_name.startswith("SUBSTACK"):
                continue

            if (
                isinstance(input_value, (tuple))
                and len(input_value) >= 2 # type: ignore
                and input_value[1] == old_child
            ):
                if isinstance(input_value, tuple): # type: ignore
                    parent_block["inputs"][input_name] = (
                        input_value[0],
                        new_child,
                        *input_value[2:],
                    )
                else:
                    input_value[1] = new_child

                return True

        return False


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
    def emit_function_expr(self, expr: FunctionCallExpr, context: StrOptional, block_parent: BlockRange, parent: StrOptional) -> ScratchInput:
        # if expr.callee not in SCRATCH_BLOCKS:
        #     raise CompilerError(
        #         f"{expr.callee} is not a valid scratch block",
        #         expr
        #     )
        if expr.callee not in SCRATCH_BLOCKS:
            setup = BlockRange(None, None)

            push_return_frame = self.emit_function_call(FunctionCallStmt(
                PUSH_RETURN_FRAME,
                ()
            ), None, None)

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
                        FunctionCallExpr(
                            "data_lengthoflist",
                            (
                                VarExpr(
                                    VarRef(RETURN_STACK),
                                ),
                            ),
                        ),
                        VarExpr(
                            VarRef(RETURN_STACK),
                        ),
                    ),
                ),
                None,
                None,
            )

            setup = self.append_range(
                setup,
                set_variable,
            )

            pop_return_frame = self.emit_function_call(FunctionCallStmt(
                POP_RETURN_FRAME,
                ()
            ), None, None)

            setup = self.append_range(
                setup,
                pop_return_frame,
            )

            self.insert_setup_before_consumer(
                block_parent,
                setup,
            )

            return self.emit_var_ref(
                VarRef(expr.callee + ":return"),
                None,
                block_parent,
                parent,
            )
        else:
            block_data = SCRATCH_BLOCKS[expr.callee]

            if not isinstance(block_data, Reporter):
                return self.raise_or_return(CompilerError(
                    f"{expr.callee} does not return anything.",
                    expr
                ), PLACE_HOLDER_0)
        
            expected_args = len(block_data.inputs) + len(block_data.fields)

            if len(expr.args) != expected_args:
                return self.raise_or_return(ArgumentError(
                    f"Block {expr.callee} expects {expected_args} argument(s), got {len(expr.args)}",
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
                            var_id = self.get_variable(arg_expr.ref.root)
                        except NameError:
                            return self.raise_or_return(NotDefinedError(f"{arg_expr.ref.root} not defined.", arg_expr), PLACE_HOLDER_0)
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
                        fields[field.name] = (arg_expr.ref.root, self.get_variable(arg_expr.ref.root))
                    except NameError:
                        return self.raise_or_return(
                            NotDefinedError(f"{arg_expr.ref.root} not defined.", arg_expr),
                            PLACE_HOLDER_0
                        )
                else:
                    if not isinstance(arg_expr, StringExpr):
                        return self.raise_or_return(InvalidTypeError(
                            f"{expr.callee}: argument {index} must be a string literal",
                            arg_expr
                        ), PLACE_HOLDER_0)

                    if arg_expr.value not in field.expected and len(field.expected) > 0:
                        return self.raise_or_return(ArgumentError(f"{arg_expr.value} is not one of {field.expected}", arg_expr),
                                                    PLACE_HOLDER_0)

                    fields[field.name] = (arg_expr.value, None)
                index += 1

                
            self.blocks[block_id]["fields"] = fields
            self.blocks[block_id]["inputs"] = inputs

            return ScratchInput(
                (InputType.BLOCK_ONLY if block_data.return_type == VariableTypes.BOOL else InputType.BLOCK_AND_SHADOW, block_id), block_data.return_type
            )

    def emit_unary_expr(self, op: str, value: Expr, context: StrOptional, block_parent: BlockRange, parent: StrOptional) -> ScratchInput:
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
            return ScratchInput((InputType.BLOCK_ONLY, block_id), VariableTypes.BOOL)

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
                return ScratchInput((InputType.BLOCK_ONLY, block_id), VariableTypes.NUMBER)

        raise NotImplementedError(f"Unsupported unary operator: {op}")
    
    def emit_binary_expr(self, left: Expr, op: str, right: Expr, context: StrOptional, block_parent: BlockRange, parent: StrOptional) -> ScratchInput:
        block_id = self.new_id()

        left_expr = self.emit_expr(left, context, block_parent, block_id)
        right_expr = self.emit_expr(right, context, block_parent, block_id)

        if op == "in":
            if right_expr.return_type is VariableTypes.LIST:
                if not isinstance(right, VarExpr):
                    return self.raise_or_return(InvalidTypeError(f"Right expression must be a list", right), PLACE_HOLDER_0)

                try:
                    list_id = self.get_variable(right.ref.root)
                except NameError:
                    return self.raise_or_return(NotDefinedError(f"{right.ref.root} is not defined", right), PLACE_HOLDER_0)

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
                    (InputType.BLOCK_AND_SHADOW, block_id), VariableTypes.BOOL
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
            (InputType.BLOCK_ONLY if return_type == VariableTypes.BOOL else InputType.BLOCK_AND_SHADOW, block_id),
            return_type,
        )

    def emit_var_ref(self, ref: VarRef, context: StrOptional, block_parent: BlockRange, parent: StrOptional) -> ScratchInput:
        if (
            context in self.procedures
            and ref.root in self.procedures[context].argument_names
        ):
            procedure_info = self.procedures[context]

            try:
                arg_index = procedure_info.argument_names.index(ref.root)
            except ValueError:
                return self.raise_or_return(ArgumentError(f"Argument {ref.root} doesn't exist.", ref), PLACE_HOLDER_0)
            
            _, *arg_types = procedure_info.proccode.split(" %")
            arg_type = arg_types[arg_index]
            arg_name = procedure_info.argument_names[arg_index]

            ARGS_TO_OPCODE = {
                "b": "argument_reporter_boolean",
                "s": "argument_reporter_string_number"
            }

            opcode = ARGS_TO_OPCODE[arg_type]
            # argument_opcode = procedure_info.argument_names[arg_index]

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

            if len(procedure_info.return_types) == 1:
                return_type = list(procedure_info.return_types)[0]
            else:
                return_type = VariableTypes.VAR

            return ScratchInput(
                (
                    InputType.BLOCK_ONLY,
                    reporter_id
                ),
                return_type=return_type
            )
        else:
            try:
                var_id = self.get_variable(ref.root)
            except NameError:
                return self.raise_or_return(NotDefinedError(f"{ref.root} not defined.", ref), PLACE_HOLDER_0)
            
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
                        ), VariableTypes.STRING
                    )

            else:
                return ScratchInput(
                    (
                        InputType.SHADOW_ONLY,
                        (
                            DataType.LIST if var_type == VariableTypes.LIST else DataType.VARIABLE,
                            ref.root,
                            var_id
                        )
                    ),
                    var_type
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
        }

    def _serialise_lists(self, is_stage: bool=False) -> dict[str, list[Any]]:
        return {
            var_id: [variable.name, variable.initial_value]
            for var_id, variable in self.variables.items()
            if variable.is_list and variable.shared == is_stage
        }

    def _serialise_broadcasts(self) -> dict[str, str]:
        return {broadcast_id: name for name, broadcast_id in self.messages.items()}

    def get_stage(self, f: zipfile.ZipFile) -> dict[str, dict[str, tuple[str, Any] | str]]:
        project = json.loads(f.read("project.json").decode("utf-8"))
        targets: list[dict[str, Any]] = project.get("targets", [])
        for candidate in targets:
            if candidate.get("isStage", True):
                return candidate
        raise CompilerError(f"No stage target in project file.", None)

    def prepare(self, target: str | None=None) -> None:
        """
        Prepares the assembler to assemble the next file. It does the following:
        1. Clears blocks, variables, lists, etc. that are local to the sprite
        2. *Keeps* stage/global data
        """
        if target is not None:
            with zipfile.ZipFile(target, "r") as f:
                stage = self.get_stage(f)

            for var_id, var_data in stage["variables"].items():
                self.variables[var_id] = VariableData(var_data[0], var_id, None, VariableTypes.VAR, False, True, var_data[1])

            for broadcast_id, broadcast_name in stage["broadcasts"].items():
                assert isinstance(broadcast_name, str)
                self.messages[broadcast_id] = broadcast_name

        # we do not clear shared variables/lists, 
        for variable_id in list(self.variables):
            variable_data = self.variables[variable_id]
            
            if variable_data.shared:
                continue

            del self.variable_map[variable_data.name]
            del self.variables[variable_id]

        self.errors.clear()
        self.blocks.clear()
        self.procedures.clear()
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