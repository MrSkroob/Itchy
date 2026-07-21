from __future__ import annotations
from dataclasses import dataclass
from assembler import VariableTypes, DataType


@dataclass(frozen=True)
class ReturnType:
    name: str
    return_type: DataType = DataType.NUMBER


@dataclass(frozen=True)
class Menu:
    """
    May have default values, but is effectively identical to an Input
    """
    opcode: str
    name: str


@dataclass(frozen=True)
class Block:
    inputs: tuple[ReturnType | Menu, ...] = ()
    fields: tuple[str, ...] = ()
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
    fields: tuple[str, ...] = ()
    return_type: VariableTypes = VariableTypes.NUMBER
 
@dataclass(frozen=True)
class Event:
    """
    Describes how to assemble event (hat) blocks.
    """
    inputs: tuple[ReturnType | Menu, ...] = ()
    fields: tuple[str, ...] = ()
    broadcasts: tuple[str, ...] = ()

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
    "motion_setrotationstyle": Block(fields=("STYLE",)),

    "motion_xposition": Reporter((),),
    "motion_yposition": Reporter((),),
    "motion_direction": Reporter((),),

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
    "looks_changeeffectby": Block((ReturnType("CHANGE"),), ("EFFECT",)),
    "looks_seteffectto":  Block((ReturnType("VALUE"),), ("EFFECT",)),
    "looks_cleargraphiceffects": Block(()),
    "looks_gotofrontback": Block(fields=("FRONT_BACK",)),
    "looks_goforwardbackwardlayers": Block((ReturnType("NUM"),), ("FORWARD_BACKWARD",)),
    

    "looks_size": Reporter((),),
    "looks_costumenumbername": Reporter(
        fields=("NUMBER_NAME",), return_type=VariableTypes.STRING,
    ),
    "looks_backdropnumbername": Reporter(
        (), fields=("NUMBER_NAME",), return_type=VariableTypes.STRING,
    ),

    # --- sound ----------------------------------------------------------
    "sound_playuntildone": Block((Menu("sound_sounds_menu", "SOUND_MENU"),)),
    "sound_play": Block((Menu("sound_sounds_menu", "SOUND_MENU"),)),
    "sound_stopallsounds": Block(()),
    "sound_changeeffectby": Block((ReturnType("CHANGE"),), ("EFFECT",)),
    "sound_seteffectto": Block((ReturnType("VALUE"),), ("EFFECT",)),
    "sound_cleareffects": Block(()),

    "sound_changevolumeby": Block((ReturnType("VOLUME"),)),
    "sound_setvolumeto": Block((ReturnType("VOLUME"),)),

    "sound_volume": Reporter(()),

    # --- operator ----------------------------------------------------------
    "operator_mod": Reporter((ReturnType("NUM1"), ReturnType("NUM2"))),
    "operator_round": Reporter((ReturnType("NUM"),)),
    "operator_mathop": Reporter(
        (ReturnType("NUM"),), ("OPERATOR",)
    ),
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
    "operator_mathop": Reporter((ReturnType("NUM", DataType.NUMBER),), (("OPERATOR",))),

    # --- control ----------------------------------------------------
    # note: control_wait_until takes a CONDITION input like control_repeat_until
    # does elsewhere in the assembler -- it's still just a plain command block.
    "event_whenflagclicked": Event(),
    "event_whenkeypressed": Event(fields=("KEY_OPTION",)),
    "event_whenthisspriteclicked": Event(),
    "event_whenbackdropswitchesto": Event(fields=("BACKDROP",)),
    "event_whengreaterthan": Event((ReturnType("VALUE"),), ("WHENGREATERTHANMENU",)),
    "event_whenbroadcastreceived": Event(fields=("BROADCAST_OPTION",), broadcasts=("BROADCAST_OPTION",)),

    "control_wait": Block((ReturnType("DURATION", DataType.POSITIVE_NUMBER),)),
    "control_wait_until": Block((ReturnType("CONDITION"),)),
    "control_stop": Block(fields=("STOP_OPTION",)), # this is basically the return block. though we'll need to figure out how to return variables.
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

    "sensing_setdragmode": Block(fields=("DRAG_MODE",)),
    "sensing_loudness": Reporter(),
    "sensing_timer": Reporter(),
    "sensing_resettimer": Block(),

    "sensing_of": Reporter((Menu("sensing_of_object_menu", "OBJECT"),), ("PROPERTY",)),
    "sensing_current": Reporter(fields=("CURRENTMENU",),),
    "sensing_dayssince2000": Reporter(),

    "sensing_username": Reporter(return_type=VariableTypes.STRING),
    "sensing_online": Reporter(return_type=VariableTypes.BOOLEAN),
    
    # -- lists and variables
    "data_addtolist": Block((ReturnType("ITEM", DataType.STRING),), ("LIST",), variables=("LIST",)),
    "data_deleteoflist": Block((ReturnType("INDEX"),), ("LIST",), variables=("LIST",)),
    "data_deletealloflist": Block(fields=("LIST",), variables=("LIST",)),
    "data_insertatlist": Block((ReturnType("ITEM"), ReturnType("INDEX")), ("LIST",), variables=("LIST",)),
    "data_itemnumoflist": Block((ReturnType("ITEM"),), ("LIST",), variables=("LIST",)),
    "data_lengthoflist": Block(fields=("LIST",), variables=("LIST",)),
    "data_showlist": Block(fields=("LIST",), variables=("LIST",)),
    "data_hidelist": Block(fields=("LIST",), variables=("LIST",)),

    "data_showvariable": Block(fields=("VARIABLE",), variables=("VARIABLE",)),
    "data_hidevariable": Block(fields=("VARIABLE",), variables=("VARIABLE",)),
}