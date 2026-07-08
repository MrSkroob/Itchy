from __future__ import annotations
import uuid
import json

from dataclasses import dataclass
from enum import Enum

from typing import Any
from itch_ast import Stmt, VarRef, Expr, BlockStmt, IfStmt, CallStmt, BreakStmt, ForInStmt, WhileStmt, AssignStmt, ReturnStmt, VarDefStmt, ForRangeStmt, FunctionDefStmt, \
    IfBranch, NumberExpr, BoolExpr, StringExpr, VarExpr, UnaryOpExpr, BinaryOpExpr, CallAction, TableExpr, FunctionCallExpr


ScratchBlock = dict[str, Any]
ScratchInput = list[Any]
StrOptional = str | None


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


class InputType(Enum):
    LITERAL = 1
    SHADOWED = 2
    REPORTER = 3


class CompilerError(Exception):
    def __init__(self, message: str) -> None:
        super().__init__(message)


@dataclass(frozen=True)
class BlockRange:
    first: StrOptional
    last: StrOptional


class Assembler:
    def __init__(self) -> None:
        self.blocks: dict[str, ScratchBlock] = {}
        # the sprite's variables
        self.variables: dict[str, list[Any]] = {}
        self.lists: dict[str, list[Any]] = {}
        # i.e. stage's variables
        self.global_lists: dict[str, list[Any]] = {}
        self.global_variables: dict[str, list[Any]] = {}
        self.variable_ids: dict[str, str] = {}
        self.lists: dict[str, list[Any]] = {}

    def new_id(self) -> str:
        return uuid.uuid4().hex[:20]

    def add_block(self, block: ScratchBlock) -> str:
        block_id = self.new_id()
        self.blocks[block_id] = block
        return block_id
    
    def make_block(
            self,
            opcode: str,
            parent: StrOptional=None,
            inputs: dict[str, ScratchInput] | None=None,
            fields: dict[str, Any] | None=None,
            mutation: dict[str, Any] | None=None,
            top_level: bool=False,
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
            "shadow": False,
            "topLevel": top_level
        }

        if mutation is not None:
            block["mutation"] = mutation
        
        if top_level:
            block["x"] = x if x is not None else 100
            block["y"] = y if y is not None else 100
        
        return self.add_block(block)
    

    def get_variable(self, name: str) -> str:
        assert name in self.variable_ids, "variable not defined!"
        return self.variable_ids[name]


    def define_variable(self, shared: bool, type_name: str, name: str) -> str:
        """
        Returns a variable ID. NOT a block ID.
        """
        # shared defines if the variable can be accessible to all sprites
        if shared:
            if type_name == "list":
                variable_location = self.global_lists
                default_value = []
            else:
                variable_location = self.global_variables
                default_value = 0
        else:
            if type_name == "list":
                variable_location = self.lists
                default_value = []
            else:
                variable_location = self.variables
                default_value = 0

        if name in self.variable_ids:
            # highkey this should raise an error instead otherwise it's confusing...
            # return self.variable_ids[name]
            raise CompilerError(f"Do not define {name} more than once.")

        var_id = self.new_id()
        self.variable_ids[name] = var_id
        variable_location[var_id] = [name, default_value]
        return var_id
    
    def emit_sequence(
            self,
            statements: tuple[Stmt, ...],
            parent: StrOptional,
        ) -> BlockRange:
        first: StrOptional = None
        last: StrOptional = None

        for stmt in statements:
            emitted = self.emit_stmt(stmt, parent)

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
    
    def emit_stmt(self, stmt: Stmt, parent: StrOptional) -> BlockRange:
        match stmt:
            case BlockStmt(body=body):
                return self.emit_sequence(body, parent)
            case VarDefStmt(shared=shared, type_name=type_name, name=name):
                self.define_variable(shared, type_name, name)
                return BlockRange(None, None)
            case AssignStmt(target=target, value=value):
                return self.emit_assignment(target, value, parent)
            case IfStmt():
                return self.emit_if(stmt, parent)
            case WhileStmt():
                return self.emit_while(stmt, parent)
            case ForRangeStmt():
                return self.emit_for_range(stmt, parent)
            case ForInStmt():
                return self.emit_for_in(stmt, parent)
            case FunctionDefStmt():
                return self.emit_function_def(stmt, parent)
            case CallStmt():
                return self.emit_call(stmt, parent)
            case BreakStmt():
                raise NotImplementedError("Not implemented")
            case ReturnStmt():
                raise NotImplementedError("Not implemented")
            case _:
                raise TypeError("Bad statement type")
    
    def emit_for_range(self, stmt: ForRangeStmt, parent: StrOptional):
        # variable
        var_id = self.define_variable(False, "number", stmt.variable)

        set_id = self.make_block(
            "data_setvariableto",
            parent=parent,
            fields={
                "VARIABLE": [stmt.variable, var_id]
            },
            inputs={
                "VALUE": self.emit_expr(stmt.start)
            }
        )

        stop_condition = BinaryOpExpr(
            left=VarExpr(VarRef(stmt.variable)),
            op=">",
            right=stmt.stop
        )

        # repeat
        repeat_id = self.make_block(
            "control_repeat_until",
            parent=set_id,
            inputs={
                "CONDITION": self.emit_expr(stop_condition)
            }
        )
        
        self.blocks[set_id]["next"] = repeat_id

        change_id = self.make_block(
            opcode="data_changevariableby",
            parent=repeat_id,
            fields={
                "VARIABLE": [stmt.variable, var_id]
            },
            inputs={
                "VALUE": self.emit_expr(stmt.step)
            }
        )

        body = self.emit_sequence(stmt.body, change_id)

        if body.first is None:
            self.blocks[repeat_id]["inputs"]["SUBSTACK"] = [InputType.SHADOWED, change_id]
        else:
            self.blocks[repeat_id]["inputs"]["SUBSTACK"] = [InputType.SHADOWED, body.first]
            assert body.last is not None, "body.first isn't None, but body.last is?"
            self.blocks[body.last]["next"] = change_id
            self.blocks[change_id]["parent"] = body.last
        
        return BlockRange(set_id, repeat_id)
    
    def emit_for_in(self, stmt: ForInStmt, parent: StrOptional):
        var_id = self.define_variable(False, "number", stmt.variable)

        set_id = self.make_block(
            "data_setvariableto",
            parent=parent,
            fields={
                "VARIABLE": [stmt.variable, var_id]
            },
            inputs={
                "VALUE": self.emit_expr(NumberExpr(1))
            }
        )

        stop_condition = BinaryOpExpr(
            left=VarExpr(VarRef(stmt.variable)),
            op=">",
            right=FunctionCallExpr()
        )

        # repeat
        repeat_id = self.make_block(
            "control_repeat_until",
            parent=set_id,
            inputs={
                "CONDITION": self.emit_expr(stop_condition)
            }
        )
        
        self.blocks[set_id]["next"] = repeat_id

        change_id = self.make_block(
            opcode="data_changevariableby",
            parent=repeat_id,
            fields={
                "VARIABLE": [stmt.variable, var_id]
            },
            inputs={
                "VALUE": self.emit_expr(NumberExpr(1))
            }
        )

        body = self.emit_sequence(stmt.body, change_id)

        if body.first is None:
            self.blocks[repeat_id]["inputs"]["SUBSTACK"] = [InputType.SHADOWED, change_id]
        else:
            self.blocks[repeat_id]["inputs"]["SUBSTACK"] = [InputType.SHADOWED, body.first]
            assert body.last is not None, "body.first isn't None, but body.last is?"
            self.blocks[body.last]["next"] = change_id
            self.blocks[change_id]["parent"] = body.last
        
        return BlockRange(set_id, repeat_id)
    
    def emit_while(self, stmt: WhileStmt, parent: StrOptional):
        """
        Scratch does not support while loops normally, but *does* support repeat until blocks. A good way to emulate it is to
        do:

        repeat until not <condition> do
            // code here
        end
        """
        not_condition = UnaryOpExpr("not", stmt.condition)

        block_id = self.make_block(
            "control_repeat_until",
            parent=parent,
            inputs={
                "CONDITION": self.emit_expr(not_condition)
            }
        )

        body = self.emit_sequence(stmt.body, block_id)

        if body.first is not None:
            self.blocks[block_id]["inputs"]["SUBSTACK"] = [InputType.SHADOWED, body.first]
        
        return BlockRange(block_id, block_id)
            
    def emit_if(self, stmt: IfStmt, parent: StrOptional):
        first = self.emit_if_branch_chain(
            stmt.branches,
            stmt.else_body,
            0,
            parent
        )

        return BlockRange(first, first)

    def emit_if_branch_chain(self, branches: tuple[IfBranch, ...], else_body: tuple[Stmt, ...], index: int, parent: StrOptional):
        branch = branches[index]
        has_else = index + 1 < len(branches) or bool(else_body)

        opcode = "control_if_else" if has_else else "control_if"

        block_id = self.make_block(
            opcode=opcode,
            parent=parent,
            inputs={
                "CONDITION": self.emit_expr(branch.condition)
            }
        )

        then_blocks = self.emit_sequence(branch.body, block_id)

        if then_blocks.first is not None:
            self.blocks[block_id]["inputs"]["SUBSTACK"] = [InputType.SHADOWED, then_blocks.first]

        if has_else:
            if index + 1 < len(branches):
                # if this isn't the last branch (there is more)
                nested_if = self.emit_if_branch_chain(
                    branches,
                    else_body,
                    index + 1,
                    block_id
                )
                self.blocks[block_id]["inputs"]["SUBSTACK2"] = [InputType.SHADOWED, nested_if]
            else:
                # no more if statements. rest of the code is not part of this if branch
                else_blocks = self.emit_sequence(else_body, block_id)
                if else_blocks.first is not None:
                    self.blocks[block_id]["inputs"]["SUBSTACK2"] = [InputType.SHADOWED, else_blocks.first]
        
        return block_id

    
    def emit_assignment(self, target: VarRef, value: Expr, parent: StrOptional) -> BlockRange:
        is_list = False

        if target.slice_expr is not None:
            assert target.root in self.variable_ids, "list usage before declaration!"
            
            var_id = self.variable_ids[target.root]
            is_list = var_id in self.lists or var_id in self.global_lists

            if is_list:
                # is a list!
                block_id = self.make_block(
                    "data_replaceitemoflist",
                    parent=parent,
                    inputs={
                        "INDEX": self.emit_expr(target.slice_expr),
                        "ITEM": self.emit_expr(value)
                    },
                    fields={
                        "LIST": [target.root, var_id]
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

            block_id = self.make_block(
                "data_setvariableto",
                parent=parent,
                fields={
                    "VARIABLE": [target.root, var_id]
                },
                inputs={
                    "VALUE": self.emit_expr(value)
                }
            )
            
            return BlockRange(
                block_id,
                block_id
            )
    
    def emit_expr(self, expr: Expr) -> ScratchInput:
        # block_id = self.new_id()
        # expression: ScratchInput = [InputType.REPORTER, block_id]
        
        match expr:
            case NumberExpr(value=value):
                return [InputType.LITERAL, [DataType.NUMBER, str(value)]]
            case StringExpr(value=value):
                return [InputType.LITERAL, [DataType.STRING, value]]
            case BoolExpr(value=value):
                # in scratch:
                # if (0 == 0) == "true" is true, so we can just use strings without any fancy conversion :)
                return [InputType.LITERAL, [DataType.STRING, str(value).lower()]]
            case VarExpr(ref=ref):
                return self.emit_var_ref(ref)
            case UnaryOpExpr(op=op, value=value):
                return self.emit_unary_expr(op, value)
            case BinaryOpExpr(left=left, op=op, right=right):
                return self.emit_binary_expr(left, op, right)
            case CallAction(arg_groups=arg_groups):
                raise NotImplementedError("returns are hard :(")
            case TableExpr(values=values):
                raise NotImplementedError("out of scope for now :v")
            case _:
                raise TypeError("Bare expression (coder sucks :/)")

        
        return expression
    


            
        
    
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
def augment_program(project_name: str, target_name: str, code: dict[str, Any]):
    with open(project_name, "w", encoding="utf-8") as f:
        project = json.load(f)
        targets: list[dict[str, Any]] = project["targets"]
        for target in targets:
            if target["name"] == target_name:
                
                break