from __future__ import annotations
from dataclasses import dataclass
from shared_templates import VariableTypes, DataType


@dataclass(frozen=True)
class ReturnType:
    name: str
    return_type: DataType = DataType.NUMBER

@dataclass
class Field:
    name: str
    expected: tuple[str, ...]


@dataclass(frozen=True)
class Menu:
    """
    May have default values, but is effectively identical to an Input
    """
    opcode: str
    name: str
    field_name: str | None=None


@dataclass(frozen=True)
class Block:
    inputs: tuple[ReturnType | Menu, ...] = ()
    fields: tuple[Field, ...] = ()
    # note that some blocks (like video sensing) *do* have multiple menus. 

    # special case for broadcasts. while on the programmer's side they'd just use a bare string:
    """
    event_broadcast("message1")
    """
    # but scratch also wants an ID. so what do you do? hardcode that shit.
    broadcasts: tuple[str, ...] = ()
    variables: tuple[str, ...] = ()


"""
motion_movesteps(10)
"""
@dataclass(frozen=True)
class Reporter:
    """
    Describes how to assemble a floating (reporter-shaped) scratch block.
    """
    inputs: tuple[ReturnType | Menu, ...] = ()
    fields: tuple[Field, ...] = ()
    return_type: VariableTypes = VariableTypes.NUMBER
    variables: tuple[str, ...] = ()
 
@dataclass(frozen=True)
class Event:
    """
    Describes how to assemble event (hat) blocks.
    """
    inputs: tuple[ReturnType | Menu, ...] = ()
    fields: tuple[Field, ...] = ()
    broadcasts: tuple[str, ...] = ()


VALID_KEYS: tuple[str, ...] = tuple([i for i in "abcdefghijklmnopqrstuvwxyz"] + ["space", "up arrow", "down arrow", "right arrow", "left arrow", "any"])


LIST_FIELD = Field("LIST", ())
VARIABLE_FIELD = Field("VARIABLE", ())
EFFECT_FIELD = Field("EFFECT", (
    "color", "fisheye", "whirl", "pixelate", "mosaic", "brightness", "ghost"
))
SOUND_EFFECT_FIELD = Field("EFFECT", ("PITCH", "PAN LEFT/RIGHT"))

# opcode -> ordered slots.
SCRATCH_BLOCKS: dict[str, Block | Reporter | Event] = {
    # --- motion -------------------------------------------------------
    "motion_movesteps": Block((ReturnType("STEPS"),)),
    "motion_turnright": Block((ReturnType("DEGREES"),)),
    "motion_turnleft": Block((ReturnType("DEGREES"),)),
    "motion_goto": Block((Menu("motion_goto_menu", "TO"),)),
    "motion_glideto": Block((ReturnType("SECS"), Menu("motion_glideto_menu", "TO"))),
    "motion_gotoxy": Block((ReturnType("X"), ReturnType("Y"))),
    "motion_glidesecstoxy": Block((ReturnType("SECS"), ReturnType("X"), ReturnType("Y"))),
    "motion_pointindirection": Block((ReturnType("DIRECTION", DataType.ANGLE),)),
    "motion_pointtowards": Block((Menu("motion_pointtowards_menu", "TOWARDS"),)),
    "motion_changexby": Block((ReturnType("DX"),)),
    "motion_setx": Block((ReturnType("X"),)),
    "motion_changeyby": Block((ReturnType("DY"),)),
    "motion_sety": Block((ReturnType("Y"),)),
    "motion_ifonedgebounce": Block(()),
    "motion_setrotationstyle": Block(fields=(Field("STYLE", ("left-right", "don't rotate", "all around")),)),

    "motion_xposition": Reporter(),
    "motion_yposition": Reporter(),
    "motion_direction": Reporter(),

    # --- looks ----------------------------------------------------------
    "looks_sayforsecs": Block((ReturnType("MESSAGE", DataType.STRING), ReturnType("SECS"))),
    "looks_say": Block((ReturnType("MESSAGE", DataType.STRING),)),
    "looks_thinkforsecs": Block((ReturnType("MESSAGE", DataType.STRING), ReturnType("SECS"))),
    "looks_think": Block((ReturnType("MESSAGE", DataType.STRING),)),
    "looks_switchcostumeto": Block((Menu("looks_costume", "COSTUME"),)),
    "looks_nextcostume": Block(()),
    "looks_switchbackdropto": Block((Menu("looks_backdrops", "BACKDROP"),)),
    "looks_nextbackdrop": Block(()),
    "looks_show": Block(()),
    "looks_hide": Block(()),
    "looks_changesizeby": Block((ReturnType("CHANGE"),)),
    "looks_setsizeto": Block((ReturnType("SIZE"),)),
    "looks_changeeffectby": Block((ReturnType("CHANGE"),), (EFFECT_FIELD,)),
    "looks_seteffectto":  Block((ReturnType("VALUE"),), (EFFECT_FIELD,)),
    "looks_cleargraphiceffects": Block(()),
    "looks_gotofrontback": Block(fields=(Field("FRONT_BACK", ("front", "back")),)),
    "looks_goforwardbackwardlayers": Block((ReturnType("NUM"),), (Field("FORWARD_BACKWARD", ("forward", "backward")),)),
    

    "looks_size": Reporter((),),
    "looks_costumenumbername": Reporter(
        fields=(Field("NUMBER_NAME", ("name", "number")),), return_type=VariableTypes.STRING,
    ),
    "looks_backdropnumbername": Reporter(
        (), fields=(Field("NUMBER_NAME", ("name", "number")),), return_type=VariableTypes.STRING,
    ),

    # --- sound ----------------------------------------------------------
    "sound_playuntildone": Block((Menu("sound_sounds_menu", "SOUND_MENU"),)),
    "sound_play": Block((Menu("sound_sounds_menu", "SOUND_MENU"),)),
    "sound_stopallsounds": Block(()),
    "sound_changeeffectby": Block((ReturnType("CHANGE"),), (SOUND_EFFECT_FIELD,)),
    "sound_seteffectto": Block((ReturnType("VALUE"),), (SOUND_EFFECT_FIELD,)),
    "sound_cleareffects": Block(()),

    "sound_changevolumeby": Block((ReturnType("VOLUME"),)),
    "sound_setvolumeto": Block((ReturnType("VOLUME"),)),

    "sound_volume": Reporter(()),

    # --- operator ----------------------------------------------------------
    "operator_random": Reporter((ReturnType("FROM"), ReturnType("TO"))),
    "operator_mod": Reporter((ReturnType("NUM1"), ReturnType("NUM2"))),
    "operator_round": Reporter((ReturnType("NUM"),)),
    "operator_random": Reporter((ReturnType("FROM"), ReturnType("TO"))),
    "operator_join": Reporter(
        (ReturnType("STRING1", DataType.STRING), ReturnType("STRING2", DataType.STRING)),
        return_type=VariableTypes.STRING,
    ),
    "operator_letter_of": Reporter(
        (ReturnType("LETTER"), ReturnType("STRING", DataType.STRING)),
        return_type=VariableTypes.STRING,
    ),
    "operator_length": Reporter((ReturnType("STRING", DataType.STRING),)),
    "operator_contains": Reporter((ReturnType("STRING1", DataType.STRING), ReturnType("STRING2", DataType.STRING)), return_type=VariableTypes.BOOLEAN),
    "operator_round": Reporter((ReturnType("NUM"),)),
    "operator_mathop": Reporter(
        (ReturnType("NUM"),), (Field("OPERATOR", (
            "abs",
            "floor",
            "ceiling",
            "sqrt",
            "sin",
            "cos",
            "tan",
            "asin",
            "acos",
            "atan",
            "ln",
            "log",
            "e ^",
            "10 ^"
        )),)
    ),

    # --- control ----------------------------------------------------
    # note: control_wait_until takes a CONDITION input like control_repeat_until
    # does elsewhere in the assembler -- it's still just a plain command block.
    "event_whenflagclicked": Event(),
    "event_whenkeypressed": Event(fields=(Field("KEY_OPTION", VALID_KEYS),)),
    "event_whenthisspriteclicked": Event(),
    "event_whenbackdropswitchesto": Event(fields=(Field("BACKDROP", ()),)),
    "event_whengreaterthan": Event((ReturnType("VALUE"),), (Field("WHENGREATERTHANMENU", ("LOUDNESS", "TIMER")),)),
    "event_whenbroadcastreceived": Event(fields=(Field("BROADCAST_OPTION", ()),), broadcasts=("BROADCAST_OPTION",)),

    "control_wait": Block((ReturnType("DURATION", DataType.POSITIVE_NUMBER),)),
    "control_wait_until": Block((ReturnType("CONDITION"),)),
    "control_stop": Block(fields=(Field("STOP_OPTION", ("this script", "all", "other scripts in sprite")),)), # this is basically the return block. though we'll need to figure out how to return variables.
    "control_delete_this_clone": Block(()),

    "event_broadcast": Block((ReturnType("BROADCAST_INPUT", DataType.STRING),), broadcasts=("BROADCAST_INPUT",)),
    "event_broadcastandwait": Block((ReturnType("BROADCAST_INPUT", DataType.STRING),), broadcasts=("BROADCAST_INPUT",)),

    # --- sensing ----------------------------------------------------
    "sensing_touchingobject": Reporter((Menu("sensing_touchingobjectmenu", "TOUCHINGOBJECTMENU"),), return_type=VariableTypes.BOOLEAN),
    "sensing_touchingcolor": Reporter((ReturnType("COLOR", DataType.COLOR),), return_type=VariableTypes.BOOLEAN),
    "sensing_coloristouchingcolor": Reporter((ReturnType("COLOR", DataType.COLOR), ReturnType("COLOR2", DataType.COLOR)), return_type=VariableTypes.BOOLEAN),
    "sensing_distanceto": Reporter((Menu("sensing_distancetomenu", "DISTANCETOMENU"),)),

    "sensing_askandwait": Block((ReturnType("QUESTION", DataType.STRING),)),
    "sensing_answer": Reporter((), return_type=VariableTypes.STRING),

    "sensing_keypressed": Reporter((Menu("sensing_keyoptions", "KEY_OPTION"),)),
    "sensing_mousedown": Reporter(return_type=VariableTypes.BOOLEAN),
    "sensing_mousex": Reporter(),
    "sensing_mousey": Reporter(),

    "sensing_setdragmode": Block(fields=(Field("DRAG_MODE", ("draggable", "not draggable")),)),
    "sensing_loudness": Reporter(),
    "sensing_timer": Reporter(),
    "sensing_resettimer": Block(),

    "sensing_of": Reporter((Menu("sensing_of_object_menu", "OBJECT"),), (Field("PROPERTY", ()),)),
    "sensing_current": Reporter(fields=(Field("CURRENTMENU", (
        "year",
        "month",
        "date",
        "dayofweek",
        "hour",
        "minute",
        "second"
    )),),),
    "sensing_dayssince2000": Reporter(),

    "sensing_username": Reporter(return_type=VariableTypes.STRING),
    "sensing_online": Reporter(return_type=VariableTypes.BOOLEAN),
    
    # -- lists and variables
    "data_addtolist": Block((ReturnType("ITEM", DataType.STRING),), (LIST_FIELD,), variables=("LIST",)),
    "data_deleteoflist": Block((ReturnType("INDEX"),), (LIST_FIELD,), variables=("LIST",)),
    "data_deletealloflist": Block(fields=(LIST_FIELD,), variables=("LIST",)),
    "data_insertatlist": Block((ReturnType("ITEM", DataType.STRING), ReturnType("INDEX")), (LIST_FIELD,), variables=("LIST",)),
    
    "data_itemoflist": Reporter((ReturnType("INDEX"),), (LIST_FIELD,), variables=("LIST",)),
    "data_itemnumoflist": Reporter((ReturnType("ITEM", DataType.STRING),), (LIST_FIELD,), variables=("LIST",)),
    "data_lengthoflist": Reporter(fields=(LIST_FIELD,), variables=("LIST",)),
    "data_listcontainsitem": Reporter((ReturnType("ITEM"),), (LIST_FIELD,), variables=("LIST",)),

    "data_showlist": Block(fields=(LIST_FIELD,), variables=("LIST",)),
    "data_hidelist": Block(fields=(LIST_FIELD,), variables=("LIST",)),

    "data_showvariable": Block(fields=(VARIABLE_FIELD,), variables=("VARIABLE",)),
    "data_hidevariable": Block(fields=(VARIABLE_FIELD,), variables=("VARIABLE",)),

    # -- pen tool
    "pen_clear": Block(),
    "pen_stamp": Block(),
    "pen_penDown": Block(),
    "pen_penUp": Block(),
    "pen_setPenColorToColor": Block((ReturnType("COLOR", DataType.COLOR),)),
    "pen_changePenColorParamBy": Block((Menu("COLOR_PARAM", "pen_menu_colorParam", "colorParam"), ReturnType("VALUE"))),
    "pen_setPenColorParamTo": Block((Menu("COLOR_PARAM", "pen_menu_colorParam", "colorParam"), ReturnType("VALUE"))),
    "pen_changePenSizeBy": Block((ReturnType("SIZE"),)),
    "pen_setPenSizeTo": Block((ReturnType("SIZE"),))
}