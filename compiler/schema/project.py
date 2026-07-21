from dataclasses import dataclass
from enum import Enum
from typing import Any
from pydantic import BaseModel, Field, StrictBool,create_model



@dataclass(frozen=True)
class KeyedObject(BaseModel):
    key: str = Field()


class Project:
    targets : list[TargetObject]
    monitors : list[Any]
    extensions : list[Any]
    meta: dict[str, Any]
    targets_names:set[str]
    
    def __init__(self, targets: list[TargetObject], monitors: list[Any], extensions: list[Any], meta: dict[str, Any]):
        self.targets = targets
        self.monitors = monitors
        self.extensions = extensions
        self.meta = meta
    
    def add_target(self, target: TargetObject):
        if(target.name in self.targets_names):
            raise ValueError(f"Target with name {target.name} already exists in project")
        self.targets.append(target)
        self.targets_names.add(target.name)
        
class Variable(KeyedObject):
    name:str
    value:Any
class List(KeyedObject):
    pass
class VideoStateEnum(Enum):
    ON = "on"
    OFF = "off"
    ON_Flipped = "on-flipped"

@dataclass(frozen=True)
class BroadCast(KeyedObject):
    name:str
    
class Costume(BaseModel):
    assetId: str
    name: str
    bitmapResolution: int
    md5ext: str
    dataFormat: str
    rotationCenterX: int
    rotationCenterY: int

class Sound(BaseModel):
    assetId: str
    name: str
    dataFormat: str
    format: str
    rate: int
    sampleCount: int
    md5ext: str

class BlockField(BaseModel):
    name: str
    value: Any

class Block(BaseModel):
    opcode: str
    next: str | None
    parent: str | None
    inputs: dict[str, Any]
    fields: dict[str, Any]
    shadow: bool
    topLevel: bool
    x:int
    y:int
    shadow:bool
    topLevel:bool = Field(default=False)
   
class TargetObject:
    name: str
    isStage:bool
    variables: dict[str, list[Any]]
    lists: dict[str, list[Any]]
    broadcasts: dict[str, str]
    blocks: dict[str, Any]
    costumes: list[Costume]
    currentCostume: int
    comments: dict[str, Any]
    sounds: list[Sound]
    volume:int
    layerOrder:int
    tempo: int
    videoState: VideoStateEnum
    videoTransparency: int
    textToSpeechLanguage: str
    
    