from __future__ import annotations

from typing import Any
from dataclasses import dataclass, field
from enum import StrEnum, Enum


@dataclass(frozen=True)
class SourcePosition:
    line: int
    character: int


@dataclass(frozen=True)
class SourceSpan:
    start: SourcePosition
    end: SourcePosition


@dataclass(frozen=True, kw_only=True)
class ASTNode:
    span: SourceSpan = field(default=SourceSpan(SourcePosition(-1, -1), SourcePosition(-1, -1)), kw_only=True, repr=False)
    dummy: bool=False


SPRITE_TEMPLATE: dict[str, Any] = {
    "isStage": False,
    "name": "",
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
    "visible": True,
    "x": 0,
    "y": 0,
    "size": 100,
    "direction": 90,
    "draggable": False,
    "rotationStyle": "all around",
}


PROJECT_TEMPLATE: dict[str, Any] = {
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
        }
    ],

    "monitors": [],
    "extensions": [],

    "meta": {
        "semver": "3.0.0",
        "vm": "0.2.0",
        "agent": "itchy"
    },
}


COSTUME_TEMPLATE: dict[str, Any] = {
    "name": "costume1",
    "bitmapResolution": 1,
    "dataFormat": "svg",
    "assetId": "",
    "md5ext": "",
    "rotationCenterX": 0,
    "rotationCenterY": 0
}


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


# some of these types are used internally and are not accessible to the user
# they help with function signature suggestions
# but users can't declare variables using some of these types.
class VariableTypes(StrEnum):
    VAR = "var"
    STRING = "string"
    NUMBER = "number"
    BOOL = "bool"
    LIST = "list"
    UNKNOWN = "unknown"
    NOTHING = "nothing"


class AssetTypes(StrEnum):
    SPRITE = "sprite"
    COSTUME = "costume"
    SOUND = "sound"

# 
VARIABLE_TYPE_TO_USER_TYPES: dict[VariableTypes, VariableTypes] = {
    VariableTypes.VAR: VariableTypes.VAR,
    VariableTypes.LIST: VariableTypes.STRING,
    VariableTypes.STRING: VariableTypes.VAR,
    VariableTypes.NUMBER: VariableTypes.VAR
}


DATA_TO_VARIABLE_TYPE: dict[DataType, VariableTypes] = {
    DataType.NUMBER: VariableTypes.NUMBER,
    DataType.POSITIVE_NUMBER: VariableTypes.NUMBER,
    DataType.POSITIVE_INTEGER: VariableTypes.NUMBER,
    DataType.INTEGER: VariableTypes.NUMBER,
    DataType.ANGLE: VariableTypes.NUMBER,
    DataType.COLOR: VariableTypes.STRING,
    DataType.STRING: VariableTypes.STRING,
    DataType.BROADCAST: VariableTypes.STRING,
    DataType.VARIABLE: VariableTypes.VAR,
    DataType.LIST: VariableTypes.LIST
}
