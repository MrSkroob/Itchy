from __future__ import annotations
from pathlib import Path
import uuid
import json
import re
import zipfile

import tempfile
import os

from dataclasses import dataclass
from enum import Enum, StrEnum

from typing import Any
from itch_ast import \
    Stmt, VarRef, BlockStmt, IfStmt, BreakStmt, ForInStmt, WhileStmt, AssignStmt, ReturnStmt, VarDefStmt, ForRangeStmt, FunctionCallStmt, FunctionDefStmt, EventHandlerStmt, \
    IfBranch, Expr, NumberExpr, BoolExpr, StringExpr, VarExpr, UnaryOpExpr, BinaryOpExpr, TableExpr, FunctionCallExpr, Program


ScratchBlock = dict[str, Any]
StrOptional = str | None
DEFAULT_PROJECT={
    "targets": []
}

HEXCODE = re.compile(r"^#(?:[0-9a-fA-F]{3}){1,2}$")


class VariableTypes(StrEnum):
    NUMBER = "number"
    STRING = "string"
    BOOLEAN = "boolean"
    LIST = "list"
    UNKNOWN = "unknown"


# return types as tuples are OKAY, because serialisation converts them all to lists anyway.
ScratchInputRaw = tuple["InputType", tuple["DataType", str] | tuple["DataType", str, str]] | tuple["InputType", str]
ScratchFieldRaw = tuple[str, None] | tuple[str, str]

@dataclass(frozen=True)
class ScratchInput:
    value: ScratchInputRaw
    return_type: VariableTypes = VariableTypes.UNKNOWN


class DataType(Enum):
    NUMBER = 4
    POSITIVE_NUMBER = 5
    POSITIVE_INTEGER = 6
    INTEGER = 7
    ANGLE = 8
    COLOR = 9 # using american spelling because Scratch is american
    STRING = 10
    BROADCAST = 11
    VARIABLE = 12
    LIST = 13

# scratch_blocks.py imports VariableTypes from this module, so this import
# has to come after VariableTypes is defined above -- otherwise it's a
# circular import (assembler -> scratch_blocks -> assembler) that fails
# because VariableTypes doesn't exist on the partially-initialized module
# yet.
from scratch_blocks import SCRATCH_BLOCKS, Block, Reporter, Event, Menu

# serialisable json
JSONValue = int | str | float | bool | None | list["JSONValue"] | dict[str, "JSONValue"]

# stuff to be serialised
Serialisable = Enum | tuple["Serialisable", ...] | list["Serialisable"] | dict[str, "Serialisable"] | JSONValue


class InputType(Enum):
    SHADOW_ONLY = 1
    BLOCK_ONLY = 2
    BLOCK_AND_SHADOW = 3 # do not use - because compiler does not have default values.


class CompilerError(BaseException):
    def __init__(self, message: str) -> None:
        super().__init__(message)


@dataclass(frozen=True)
class BlockRange:
    first: StrOptional
    last: StrOptional


@dataclass(frozen=True)
class VariableData:
    name: str
    id: str
    context: StrOptional
    var_type: VariableTypes
    is_list: bool
    shared: bool
    initial_value: Any


@dataclass(frozen=True)
class ProcedureInfo:
    name: str
    prototype_id: str
    proccode: str
    argument_ids: tuple[str, ...]
    argument_names: tuple[str, ...]
    argument_defaults: tuple[str, ...]


class Assembler:
    def __init__(self) -> None:
        self.variables: dict[str, VariableData] = {} # includes lists.
        self.blocks: dict[str, ScratchBlock] = {}
        self.procedures: dict[str, ProcedureInfo] = {}

        # we don't need to worry about function "variables" since they are arguments.
        # i.e. they are not treated as variables and are treated as read-only.
        self.variable_map: dict[str, str] = {}

        self.messages: dict[str, str] = {}

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
        block: ScratchBlock = {
            "opcode": opcode,
            "next": None,
            "parent": parent,
            "inputs": inputs or {},
            "fields": fields or {},
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
        assert key in self.variable_map, "variable not defined!"
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
        
        procedure = self.procedures[context]
        if var_name in procedure.argument_names:
            raise AssertionError(f"{var_name} IS READ ONLY!!!")

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

        for stmt in program.body:
            block_range = self.emit_stmt(stmt, None, None)

            if block_range.first is None:
                # e.g. a bare VarDefStmt, which doesn't emit a block
                continue

            first_block = self.blocks[block_range.first]
            first_block["topLevel"] = True
            first_block["parent"] = None
            first_block["x"] = x
            first_block["y"] = y

            x += 200
    
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
                return self.emit_event_handler(stmt)
            case FunctionDefStmt():
                return self.emit_function_def(stmt, parent)
            case FunctionCallStmt():
                return self.emit_function_call(stmt, parent, context)
            case BreakStmt():
                raise NotImplementedError("Not implemented")
            case ReturnStmt():
                raise NotImplementedError("Not implemented")
            case _:
                raise TypeError("Bad statement type")
            
    def emit_scratch_block(self, stmt: FunctionCallStmt, parent: str | None, context: StrOptional) -> BlockRange | None:
        if stmt.callee not in SCRATCH_BLOCKS:
            return None

        block_data = SCRATCH_BLOCKS[stmt.callee]

        if not isinstance(block_data, Block):
            raise CompilerError(
                f"{stmt.callee!r} is should be a stack block"
            )

        expected_args = len(block_data.inputs) + len(block_data.fields)

        if len(stmt.args) != expected_args:
            raise CompilerError(
                f"Block {stmt.callee!r} expects {expected_args} argument(s), got {len(stmt.args)}"
            )

        inputs: dict[str, ScratchInputRaw] = {}
        fields: dict[str, ScratchFieldRaw] = {}


        block_id = self.make_block(
            opcode=stmt.callee,
            parent=parent,
        )

        # inputs come first, positionally, then fields -- matches how the
        # expected_args check above adds them together.
        for arg, arg_expr in zip(block_data.inputs, stmt.args):
            if arg in block_data.broadcasts:
                if not isinstance(arg_expr, StringExpr):
                    inputs[arg.name] = (
                        self.emit_expr(arg_expr, context, block_id).value
                    )
                else:
                    broadcast_id = self.define_broadcast(arg_expr.value)
                    inputs[arg.name] = (InputType.SHADOW_ONLY,
                                        (DataType.BROADCAST, arg_expr.value, broadcast_id))
            elif arg in block_data.variables:
                if not isinstance(arg_expr, VarExpr):
                    inputs[arg.name] = (
                        self.emit_expr(arg_expr, context, block_id).value
                    )
                else:
                    var_id = self.get_variable(arg_expr.ref.root)
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
                                arg.name: (
                                    arg_expr.value,
                                    None
                                )
                            })

                        inputs[arg.name] = (InputType.SHADOW_ONLY, menu_id)
                    else:
                        inputs[arg.name] = (InputType.SHADOW_ONLY, 
                            (arg.return_type, arg_expr.value))
                else:
                    inputs[arg.name] = self.emit_expr(arg_expr, context, block_id).value

        for field_name, arg_expr in zip(block_data.fields, stmt.args[len(block_data.inputs):]):
            if field_name in block_data.variables:
                if not isinstance(arg_expr, VarExpr):
                    raise CompilerError(
                        f"{stmt.callee}: argument for {field_name!r} must be a variable"
                    )
                fields[field_name] = (arg_expr.ref.root, self.get_variable(arg_expr.ref.root))
            elif field_name in block_data.broadcasts:
                if not isinstance(arg_expr, StringExpr):
                    raise CompilerError(
                        f"{stmt.callee}: argument for {field_name!r} must be a string literal"
                    )
                fields[field_name] = (arg_expr.value, self.define_broadcast(arg_expr.value))
            else:
                if not isinstance(arg_expr, StringExpr):
                    raise CompilerError(
                        f"{stmt.callee}: argument for {field_name!r} must be a string literal"
                    )
                fields[field_name] = (arg_expr.value, None)

        self.blocks[block_id]["fields"] = fields
        self.blocks[block_id]["inputs"] = inputs

        return BlockRange(block_id, block_id)
            
    def emit_function_call(self, stmt: FunctionCallStmt, parent: str | None, context: StrOptional) -> BlockRange:
        if stmt.callee not in self.procedures:
            # is either a custom scratch block or a hallucination :v
            block_range = self.emit_scratch_block(stmt, parent, context)
            assert block_range is not None, "bad bad this procedure doesn't exist"
            return block_range
        
        info = self.procedures[stmt.callee]

        if len(stmt.args) != len(info.argument_ids):
            raise ValueError(
                f"Function {stmt.callee!r} expects {len(info.argument_ids)} arguments, "
                f"got {len(stmt.args)}"
            )

        inputs: dict[str, ScratchInputRaw] = {}
        block_id = self.new_id()

        for arg_id, arg_expr in zip(info.argument_ids, stmt.args):
            emitted_arg = self.emit_expr(arg_expr, context, block_id)
            inputs[arg_id] = emitted_arg.value

        self.make_block(
            opcode="procedures_call",
            id=block_id,
            parent=parent,
            inputs=inputs,
        )

        self.blocks[block_id]["mutation"] = {
            "tagName": "mutation",
            "children": [],
            "proccode": info.proccode,
            "argumentids": json.dumps(list(info.argument_ids)),
            "warp": "false",
        }

        return BlockRange(block_id, block_id)
    
    def emit_event_handler(self, stmt: EventHandlerStmt) -> BlockRange:
        if stmt.name not in SCRATCH_BLOCKS:
            raise CompilerError(f"{stmt.name!r} is not a known event")

        block_data = SCRATCH_BLOCKS[stmt.name]

        if not isinstance(block_data, Event):
            raise CompilerError(
                f"{stmt.name!r} is should be a hat/event block"
            )

        # unlike Block/Reporter, an Event's `broadcasts` entries are not a
        # subset of `inputs` -- they're their own trailing group of
        # field-shaped arguments (see event_whenbroadcastreceived), so they
        # get counted on top of inputs and fields rather than overlapping.
        expected_args = len(block_data.inputs) + len(block_data.fields)

        if len(stmt.params) != expected_args:
            raise CompilerError(
                f"Event {stmt.name!r} expects {expected_args} argument(s), got {len(stmt.params)}"
            )

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
        for arg, arg_expr in zip(block_data.inputs, stmt.params):
            if arg in block_data.broadcasts:
                if not isinstance(arg_expr, StringExpr):
                    inputs[arg.name] = (
                        self.emit_expr(arg_expr, None, event_id).value
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
                            fields={arg.name: (arg_expr.value, None)})
                        inputs[arg.name] = (InputType.SHADOW_ONLY, menu_id)
                    else:
                        inputs[arg.name] = (InputType.SHADOW_ONLY, (arg.return_type, arg_expr.value))
                else:
                    inputs[arg.name] = self.emit_expr(arg_expr, None, event_id).value

        field_args = stmt.params[len(block_data.inputs):]

        for field_name, arg_expr in zip(block_data.fields, field_args):
            if not isinstance(arg_expr, StringExpr):
                raise CompilerError(f"{stmt.name}: argument for {field_name!r} must be a string literal")
            if field_name in block_data.broadcasts:
                fields[field_name] = (arg_expr.value, self.define_broadcast(arg_expr.value))
            else:
                fields[field_name] = (arg_expr.value, None)

        # make_block does `inputs or {}` / `fields or {}`, so when they start
        # out empty it silently swaps in a fresh dict instead of keeping our
        # reference -- write back explicitly so anything filled in above
        # actually lands on the block.
        self.blocks[event_id]["inputs"] = inputs
        self.blocks[event_id]["fields"] = fields

        body = self.emit_sequence(stmt.body, event_id, None)

        if body.first is not None:
            self.blocks[event_id]["next"] = body.first

        return BlockRange(event_id, body.last or event_id)
            
    def emit_function_def(self, stmt: FunctionDefStmt, parent: StrOptional) -> BlockRange:
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
        proccode_parts: list[str] = [stmt.name]

        for param in stmt.params:
            arg_id = self.new_id()

            argument_ids.append(arg_id)
            argument_names.append(param.name)

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
        proccode = " ".join(proccode_parts)

        self.procedures[stmt.name] = ProcedureInfo(
            name=stmt.name,
            prototype_id=prototype_id,
            proccode=proccode,
            argument_ids=argument_ids_tuple,
            argument_names=argument_names_tuple,
            argument_defaults=argument_defaults_tuple,
        )

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

        self.assert_writable_name(stmt.variable, context)

        var_id = self.define_variable(False, "number", stmt.variable, context)
        set_id = self.new_id()
        self.make_block(
            "data_setvariableto",
            id=set_id,
            parent=parent,
            fields={
                "VARIABLE": (stmt.variable, var_id)
            },
            inputs={
                "VALUE": self.emit_expr(stmt.start, context, set_id).value
            }
        )

        stop_condition = BinaryOpExpr(
            left=VarExpr(VarRef(stmt.variable)),
            op=">",
            right=stmt.stop
        )

        # repeat
        repeat_id = self.new_id()
        self.make_block(
            "control_repeat_until",
            id=repeat_id,
            parent=set_id,
            inputs={
                "CONDITION": self.emit_expr(stop_condition, context, repeat_id).value
            }
        )
        
        self.blocks[set_id]["next"] = repeat_id

        change_id = self.new_id()
        self.make_block(
            opcode="data_changevariableby",
            id=change_id,
            parent=repeat_id,
            fields={
                "VARIABLE": (stmt.variable, var_id)
            },
            inputs={
                "VALUE": self.emit_expr(stmt.step, context, change_id).value
            }
        )

        body = self.emit_sequence(stmt.body, change_id, context)

        if body.first is None:
            self.blocks[repeat_id]["inputs"]["SUBSTACK"] = (InputType.BLOCK_ONLY, change_id)
        else:
            self.blocks[repeat_id]["inputs"]["SUBSTACK"] = (InputType.BLOCK_ONLY, body.first)
            assert body.last is not None, "body.first isn't None, but body.last is?"
            self.blocks[body.last]["next"] = change_id
            self.blocks[change_id]["parent"] = body.last
        
        return BlockRange(set_id, repeat_id)
    
    def emit_for_in(self, stmt: ForInStmt, parent: StrOptional, context: StrOptional):
        list_variable_name = "list_getter" + self.new_id()
        iterable_id = self.get_variable(stmt.iterable.root)

        self.assert_writable_name(stmt.variable, context)
        # we *still* need this id to be unique, because even if it's in a for loop, scratch considers it global.
        # so we need a variable with a unique name to avoid amiguity.
        var_id = self.define_variable(False, "number", list_variable_name, context) # not to be used by the programmer, so is given garbage name.
        var_list_item_id = self.define_variable(False, "number", stmt.variable, context) # variable type doesn't matter as long as it's not 'list'

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

        # iterator variable
        set_id = self.new_id()
        self.make_block(
            "data_setvariableto",
            id=set_id,
            parent=parent,
            fields={
                "VARIABLE": (list_variable_name, var_id)
            },
            inputs={
                "VALUE": self.emit_expr(NumberExpr(1), context, set_id).value
            }
        )

        # operator that gets n item of list
        list_set_id = self.new_id()

        if self.variables[iterable_id].is_list:
            itemoflist = self.emit_function_expr(FunctionCallExpr("data_itemoflist",
                                                                  (VarExpr(VarRef(list_variable_name)), 
                                                                   VarExpr(VarRef(stmt.iterable.root)))
                                                                   ), context, list_set_id)
            stop_condition = BinaryOpExpr(
                left=VarExpr(VarRef(list_variable_name)),
                op=">",
                right=FunctionCallExpr("data_lengthoflist", (VarExpr(stmt.iterable),))
            )
        else:
            itemoflist = self.emit_function_expr(FunctionCallExpr("operator_letter_of", 
                                                                  (VarExpr(VarRef(list_variable_name)), 
                                                                   VarExpr(VarRef(stmt.iterable.root)))
                                                                   ), context, list_set_id)
            stop_condition = BinaryOpExpr(
                left=VarExpr(VarRef(list_variable_name)),
                op=">",
                right=FunctionCallExpr("operator_length", (VarExpr(stmt.iterable),))
            )

        

        # repeat
        repeat_id = self.new_id()
        self.make_block(
            "control_repeat_until",
            parent=set_id,
            id=repeat_id,
            inputs={
                "CONDITION": self.emit_expr(stop_condition, context, repeat_id).value
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
                "VALUE": self.emit_expr(NumberExpr(1), context, change_id).value
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

        self.make_block(
            opcode="control_repeat_until",
            id=block_id,
            parent=parent,
            inputs={
                "CONDITION": self.emit_expr(not_condition, context, block_id).value
            }
        )

        body = self.emit_sequence(stmt.body, block_id, context)

        if body.first is not None:
            self.blocks[block_id]["inputs"]["SUBSTACK"] = (InputType.BLOCK_ONLY, body.first)
        
        return BlockRange(block_id, block_id)
            
    def emit_if(self, stmt: IfStmt, parent: StrOptional, context: StrOptional):
        first = self.emit_if_branch_chain(
            stmt.branches,
            stmt.else_body,
            0,
            parent,
            context
        )

        return BlockRange(first, first)

    def emit_if_branch_chain(self, branches: tuple[IfBranch, ...], else_body: tuple[Stmt, ...], index: int, parent: StrOptional, context: StrOptional):
        branch = branches[index]
        has_else = index + 1 < len(branches) or bool(else_body)

        opcode = "control_if_else" if has_else else "control_if"

        block_id = self.new_id()
        self.make_block(
            opcode=opcode,
            id=block_id,
            parent=parent,
            inputs={
                "CONDITION": self.emit_expr(branch.condition, context, block_id).value
            }
        )

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
                    context
                )
                self.blocks[block_id]["inputs"]["SUBSTACK2"] = (InputType.BLOCK_ONLY, nested_if)
            else:
                # no more if statements. rest of the code is not part of this if branch
                else_blocks = self.emit_sequence(else_body, block_id, context)
                if else_blocks.first is not None:
                    self.blocks[block_id]["inputs"]["SUBSTACK2"] = (InputType.BLOCK_ONLY, else_blocks.first)
        
        return block_id
    
    
    def emit_assignment(self, target: VarRef, value: Expr, parent: StrOptional, context: StrOptional) -> BlockRange:
        if context is not None and target.root in self.procedures[context].argument_names:
            raise AssertionError("do not assign read-only arguments!!")
        
        if target.slice_expr is not None:
            var_id = self.get_variable(target.root)
            variable = self.variables[var_id]

            if variable.is_list:
                # is a list!
                block_id = self.new_id()
                self.make_block(
                    "data_replaceitemoflist",
                    id=block_id,
                    parent=parent,
                    inputs={
                        "INDEX": self.emit_expr(target.slice_expr, context, block_id).value,
                        "ITEM": self.emit_expr(value, context, block_id).value
                    },
                    fields={
                        "LIST": (target.root, var_id)
                    }
                )

                return BlockRange(block_id, block_id)
            else:
                # string interpolation

                # This commented code is for documentation - 
                # it's a nice pattern to see how inputs/fields work in Scratch json :+1:

                # var_id = self.get_variable(target.root)
                # block_id = self.make_block(
                #     "data_setvariableto",
                #     parent=parent,
                #     fields={
                #         "VARIABLE": [target.root, var_id]
                #     },
                #     inputs={
                #         "VALUE": self.emit_expr(value)
                #     }
                # )

                # self.make_block(
                #     "operator_letter_of",
                #     parent=block_id,
                #     inputs={
                #         "LETTER": self.emit_expr(target.slice_expr),
                #         "STRING": [InputType.REPORTER, [
                #             DataType.VARIABLE,
                #             target.root,
                #             var_id
                #         ]]
                #     }
                # )
                # return BlockRange(
                #     block_id,
                #     block_id
                # )

                raise TypeError("Strings do not support item assignment")
        else:
            var_id = self.get_variable(target.root) 
            block_id = self.new_id()
            self.make_block(
                "data_setvariableto",
                id=block_id,
                parent=parent,
                fields={
                    "VARIABLE": (target.root, var_id)
                },
                inputs={
                    "VALUE": self.emit_expr(value, context, block_id).value
                }
            )
            
            return BlockRange(
                block_id,
                block_id,
            )
    
    def emit_expr(self, expr: Expr, context: StrOptional, parent: StrOptional) -> ScratchInput:
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
                return ScratchInput((InputType.SHADOW_ONLY, (DataType.STRING, str(value).lower())), VariableTypes.BOOLEAN)
            case VarExpr(ref=ref):
                return self.emit_var_ref(ref, context, parent)
            case UnaryOpExpr(op=op, value=value):
                return self.emit_unary_expr(op, value, context, parent)
            case BinaryOpExpr(left=left, op=op, right=right):
                return self.emit_binary_expr(left, op, right, context, parent)
            case FunctionCallExpr():
                # only available for scratch built-ins :v
                return self.emit_function_expr(expr, context, parent)
            case TableExpr():
                raise NotImplementedError("out of scope for now :v")
            case _:
                raise TypeError("Bare expression (coder sucks :/)")

        
        # return expression
    def emit_function_expr(self, expr: FunctionCallExpr, context: StrOptional, parent: StrOptional) -> ScratchInput:
        block_data = SCRATCH_BLOCKS[expr.callee]

        if not isinstance(block_data, Reporter):
            raise CompilerError(
                f"{expr.callee!r} is a block, not a reporter -- it can't be called in a statement"
            )
    
        expected_args = len(block_data.inputs) + len(block_data.fields)

        if len(expr.args) != expected_args:
            raise CompilerError(
                f"Block {expr.callee!r} expects {expected_args} argument(s), got {len(expr.args)}"
            )
        
        block_id = self.make_block(
            opcode=expr.callee,
            parent=parent
        )

        inputs: dict[str, ScratchInputRaw] = {}
        fields: dict[str, ScratchFieldRaw] = {}
        
        for arg, arg_expr in zip(block_data.inputs, expr.args):
            if arg.name in block_data.variables:
                if not isinstance(arg_expr, VarExpr):
                    inputs[arg.name] = (
                        self.emit_expr(arg_expr, context, block_id).value
                    )
                else:
                    var_id = self.get_variable(arg_expr.ref.root)
                    inputs[arg.name] = (InputType.SHADOW_ONLY,
                                        (DataType.VARIABLE, arg_expr.ref.root, var_id))
            else:
                if isinstance(arg_expr, StringExpr):
                    if isinstance(arg, Menu):
                        # create the menu
                        menu_id = self.make_block(
                            arg.opcode, 
                            block_id,
                            fields={
                                arg.name: (
                                    arg_expr.value,
                                    None
                                )
                            })

                        inputs[arg.name] = (InputType.SHADOW_ONLY, menu_id)
                    else:
                        inputs[arg.name] = (InputType.SHADOW_ONLY, (arg.return_type, arg_expr.value))
                else:
                    inputs[arg.name] = self.emit_expr(arg_expr, context, parent).value

        for field_name, arg_expr in zip(block_data.fields, expr.args[len(block_data.inputs):]):
            if field_name in block_data.variables:
                if not isinstance(arg_expr, VarExpr):
                    raise CompilerError(
                        f"{expr.callee}: argument for {field_name!r} must be a variable"
                    )
                fields[field_name] = (arg_expr.ref.root, self.get_variable(arg_expr.ref.root))
            else:
                if not isinstance(arg_expr, StringExpr):
                    raise CompilerError(
                        f"{expr.callee}: argument for {field_name!r} must be a string literal"
                    )

                fields[field_name] = (arg_expr.value, None)
            
        self.blocks[block_id]["fields"] = fields
        self.blocks[block_id]["inputs"] = inputs

        return ScratchInput(
            (InputType.BLOCK_ONLY if block_data.return_type == VariableTypes.BOOLEAN else InputType.BLOCK_AND_SHADOW, block_id), block_data.return_type
        )

    def emit_unary_expr(self, op: str, value: Expr, context: StrOptional, parent: StrOptional) -> ScratchInput:
        block_id = self.new_id()
        if op in {"not", "!"}:
            self.make_block(
                opcode="operator_not",
                id=block_id,
                parent=parent,
                inputs={
                    "OPERAND": self.emit_expr(value, context, block_id).value,
                },
            )
            return ScratchInput((InputType.BLOCK_ONLY, block_id), VariableTypes.BOOLEAN)

        if op == "-":
            self.make_block(
                opcode="operator_subtract",
                id=block_id,
                parent=parent,
                inputs={
                    "NUM1": (InputType.SHADOW_ONLY, (DataType.NUMBER, "0")),
                    "NUM2": self.emit_expr(value, context, block_id).value,
                },
            )
            return ScratchInput((InputType.BLOCK_AND_SHADOW, block_id), VariableTypes.NUMBER)

        raise NotImplementedError(f"Unsupported unary operator: {op}")
    
    def emit_binary_expr(self, left: Expr, op: str, right: Expr, context: StrOptional, parent: StrOptional) -> ScratchInput:
        block_id = self.new_id()

        left_expr = self.emit_expr(left, context, block_id)
        right_expr = self.emit_expr(right, context, block_id)

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
        }[op]
        if op in {"==", ">", "<", "and", "or"}:
            return_type = VariableTypes.BOOLEAN
        else:
            return_type = VariableTypes.NUMBER

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
            (InputType.BLOCK_ONLY if return_type == VariableTypes.BOOLEAN else InputType.BLOCK_AND_SHADOW, block_id),
            return_type,
        )

    def emit_var_ref(self, ref: VarRef, context: StrOptional, parent: StrOptional) -> ScratchInput:
        if context in self.procedures:
            procedure_info = self.procedures[context]

            try:
                arg_index = procedure_info.argument_names.index(ref.root)
            except ValueError:
                raise AssertionError("bad bad bad! this argument doesn't exist!")
            
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

            return ScratchInput(
                (
                    InputType.BLOCK_ONLY,
                    reporter_id
                )
            )
        else:
            var_id = self.get_variable(ref.root)
            var_type = self.variables[var_id].var_type
            if ref.slice_expr is not None:
                if var_type is VariableTypes.LIST:
                    operator_id = self.make_block(
                        opcode="data_itemoflist",
                        parent=parent,
                        inputs={
                            "INDEX": self.emit_expr(ref.slice_expr, context, parent).value
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
                            "LETTER": self.emit_expr(ref.slice_expr, context, parent).value,
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

    def _serialise_variables(self) -> dict[str, list[Any]]:
        return {
            var_id: [variable.name, variable.initial_value]
            for var_id, variable in self.variables.items()
            if not variable.is_list
        }

    def _serialise_lists(self) -> dict[str, list[Any]]:
        return {
            var_id: [variable.name, variable.initial_value]
            for var_id, variable in self.variables.items()
            if variable.is_list
        }

    def _serialise_broadcasts(self) -> dict[str, str]:
        return {broadcast_id: name for name, broadcast_id in self.messages.items()}

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
        
        project_file = Path(project_file)
        
        with zipfile.ZipFile(project_file, "a") as f:
            if("project.json" not in f.namelist()):
                with f.open("project.json", "w") as project_file:
                    data=json.dumps(DEFAULT_PROJECT)
                    project_file.write(data.encode("utf-8"))
                    project_file.flush()
            project = json.loads(f.read("project.json").decode("utf-8"))


        self.emit_program(program)

        targets: list[dict[str, Any]] = project.get("targets", [])

        sprite_target = None
        for candidate in targets:
            if not candidate.get("isStage", False) and candidate.get("name") == target:
                sprite_target = candidate
                break

        if sprite_target is None:
            sprite_target = {}

        sprite_target["variables"] = self._serialise_variables()
        sprite_target["lists"] = self._serialise_lists()
        sprite_target["broadcasts"] = self._serialise_broadcasts()
        sprite_target["blocks"] = self._serialise_blocks()
        sprite_target["comments"] = {}
        project["targets"].append(sprite_target)

        # with open(project_file, "w", encoding="utf-8") as f:
        #     json.dump(project, f)

        json_dumped = json.dumps(project, ensure_ascii=True)

        project_directory = os.path.dirname(os.path.abspath(project_file))
        temporary_fd, temporary_path = tempfile.mkstemp(
            suffix=".sb3",
            dir=project_directory,
        )
        os.close(temporary_fd)

        try:
            with zipfile.ZipFile(project_file, "r") as source:
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

                    destination.writestr("project.json", json_dumped)

            # Validate the completed archive before replacing the original.
            with zipfile.ZipFile(temporary_path, "r") as completed_archive:
                bad_file = completed_archive.testzip()
                if bad_file is not None:
                    raise CompilerError(
                        f"Generated Scratch archive contains a corrupt file: {bad_file!r}"
                    )

            os.replace(temporary_path, project_file)
        finally:
            if os.path.exists(temporary_path):
                os.remove(temporary_path)
        # with zipfile.ZipFile(project_file, mode="w", compression=zipfile.ZIP_DEFLATED, compresslevel=9) as f:
        #     json_dumped = json.dumps(project, ensure_ascii=True)
        #     f.writestr("project.json", data=json_dumped)
        #     f.testzip()

        
    
# def augment_program(project_name: str, target_name: str, code: dict[str, Any]):
#     with open(project_name, "w", encoding="utf-8") as f:
#         project = json.load(f)
#         targets: list[dict[str, Any]] = project["targets"]
#         for target in targets:
#             if target["name"] == target_name:
                
#                 break

            
        
    
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