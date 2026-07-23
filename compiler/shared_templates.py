from typing import Any
from dataclasses import dataclass
from enum import StrEnum, Enum


@dataclass(frozen=True)
class SourcePosition:
    line: int
    character: int


@dataclass(frozen=True)
class SourceSpan:
    start: SourcePosition
    end: SourcePosition


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


class VariableTypes(StrEnum):
    NUMBER = "number"
    STRING = "string"
    BOOLEAN = "boolean"
    LIST = "list"
    UNKNOWN = "unknown"
