import json
from dataclasses import fields, is_dataclass
from enum import Enum

from itchy.scratch_blocks import STAGE_BLOCKS, SCRATCH_BLOCKS


KIND_NAMES = {
    "Block": "block",
    "Reporter": "reporter",
    "Event": "event",
    "ReturnType": "input",
    "Menu": "menu",
    "Field": "field",
}


def to_jsonable(value):
    if is_dataclass(value):
        result = {}

        kind = KIND_NAMES.get(type(value).__name__)
        if kind is not None:
            result["kind"] = kind

        for field in fields(value):
            result[field.name] = to_jsonable(getattr(value, field.name))

        return result

    if isinstance(value, Enum):
        return value.name

    if isinstance(value, dict):
        return {
            str(key): to_jsonable(item)
            for key, item in value.items()
        }

    if isinstance(value, (list, tuple, set)):
        return [to_jsonable(item) for item in value]

    return value


output = {
    "STAGE_BLOCKS": to_jsonable(STAGE_BLOCKS),
    "SCRATCH_BLOCKS": to_jsonable(SCRATCH_BLOCKS),
}

with open("src/itchy/assets/scratch_blocks.json", "w", encoding="utf-8") as file:
    json.dump(output, file, indent=4)