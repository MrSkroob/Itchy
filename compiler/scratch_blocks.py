from __future__ import annotations
from dataclasses import dataclass


@dataclass(frozen=True)
class Input:
    """A plain value input (e.g. STEPS, X, MESSAGE). Filled in via emit_expr."""
    name: str


@dataclass(frozen=True)
class Field:
    """
    A dropdown field (e.g. STYLE, EFFECT). The corresponding itch
    argument must be a string literal whose value is one of `allowed`.
    """
    name: str
    allowed: tuple[str, ...]


Slot = Input | Field


@dataclass(frozen=True)
class Block:
    slots: tuple[Slot, ...] = ()
    # note that some blocks (like video sensing) *do* have multiple menus. 
    menus: tuple[Input] | None=None


# opcode -> ordered slots.
SIMPLE_SCRATCH_BLOCKS: dict[str, Block] = {
    # --- motion -------------------------------------------------------
    "motion_movesteps": Block((Input("STEPS"),)),
    "motion_turnright": Block((Input("DEGREES"),)),
    "motion_turnleft": Block((Input("DEGREES"),)),
    "motion_gotoxy": Block((Input("X"), Input("Y"))),
    "motion_glidesecstoxy": Block((Input("SECS"), Input("X"), Input("Y"))),
    "motion_pointindirection": Block((Input("DIRECTION"),)),
    "motion_changexby": Block((Input("DX"),)),
    "motion_setx": Block((Input("X"),)),
    "motion_changeyby": Block((Input("DY"),)),
    "motion_sety": Block((Input("Y"),)),
    "motion_ifonedgebounce": Block(()),
    "motion_setrotationstyle": Block((
        Field("STYLE", ("left-right", "don't rotate", "all around")),
    )),

    # --- looks ----------------------------------------------------------
    "looks_sayforsecs": Block((Input("MESSAGE"), Input("SECS"))),
    "looks_say": Block((Input("MESSAGE"),)),
    "looks_thinkforsecs": Block((Input("MESSAGE"), Input("SECS"))),
    "looks_think": Block((Input("MESSAGE"),)),
    "looks_show": Block(()),
    "looks_hide": Block(()),
    "looks_changesizeby": Block((Input("CHANGE"),)),
    "looks_setsizeto": Block((Input("SIZE"),)),
    "looks_changeeffectby": Block((
        Field("EFFECT", ("COLOR", "FISHEYE", "WHIRL", "PIXELATE", "MOSAIC", "BRIGHTNESS", "GHOST")),
        Input("CHANGE"),
    )),
    "looks_seteffectto": Block((
        Field("EFFECT", ("COLOR", "FISHEYE", "WHIRL", "PIXELATE", "MOSAIC", "BRIGHTNESS", "GHOST")),
        Input("VALUE"),
    )),
    "looks_cleargraphiceffects": Block(()),
    "looks_gotofrontback": Block((Field("FRONT_BACK", ("front", "back")),)),
    "looks_goforwardbackwardlayers": Block((
        Field("FORWARD_BACKWARD", ("forward", "backward")),
        Input("NUM"),
    )),
    "looks_nextcostume": Block(()),
    "looks_nextbackdrop": Block(()),

    # --- sound ----------------------------------------------------------
    "sound_stopallsounds": Block(()),
    "sound_changevolumeby": Block((Input("VOLUME"),)),
    "sound_setvolumeto": Block((Input("VOLUME"),)),
    "sound_changeeffectby": Block((
        Field("EFFECT", ("PITCH", "PAN")),
        Input("VALUE"),
    )),
    "sound_seteffectto": Block((
        Field("EFFECT", ("PITCH", "PAN")),
        Input("VALUE"),
    )),
    "sound_cleareffects": Block(()),

    # --- control ----------------------------------------------------
    # note: control_wait_until takes a CONDITION input like control_repeat_until
    # does elsewhere in the assembler -- it's still just a plain command block.
    "control_wait": Block((Input("DURATION"),)),
    "control_wait_until": Block((Input("CONDITION"),)),
    "control_stop": Block((
        Field("STOP_OPTION", ("all", "this script", "other scripts in sprite")),
    )),
    "control_delete_this_clone": Block(()),

    # --- sensing ----------------------------------------------------
    "sensing_askandwait": Block((Input("QUESTION"),)),
    "sensing_resettimer": Block(()),
    "sensing_setdragmode": Block((
        Field("DRAG_MODE", ("draggable", "not draggable")),
    )),
}


@dataclass(frozen=True)
class ReporterSpec:
    """
    Describes how to assemble a floating (reporter-shaped) scratch block.
 
    `slots` lists the block's inputs/fields, in the same left-to-right
    order they're expected to appear in FunctionCallExpr.arg_groups,
    mirroring how the block reads in the Scratch editor.
 
    `return_type` is the VariableTypes value ("number", "string", or
    "boolean") that this reporter evaluates to.
    """
    slots: tuple[Slot, ...] = ()
    return_type: str = "number"
 

FLOATING_SCRATCH_BLOCKS: dict[str, ReporterSpec] = {
    # --- operators (the remaining reporter-shaped ones -- see module
    # docstring for why +, -, *, /, =, >, <, and, or, not aren't here) ---
    "operator_mod": ReporterSpec((Input("NUM1"), Input("NUM2")), "number"),
    "operator_round": ReporterSpec((Input("NUM"),), "number"),
    "operator_mathop": ReporterSpec(
        (
            Field("OPERATOR", (
                "abs", "floor", "ceiling", "sqrt", "sin", "cos", "tan",
                "asin", "acos", "atan", "ln", "log", "e ^", "10 ^",
            )),
            Input("NUM"),
        ),
        "number",
    ),
    "operator_random": ReporterSpec((Input("FROM"), Input("TO")), "number"),
    "operator_join": ReporterSpec((Input("STRING1"), Input("STRING2")), "string"),
    "operator_letter_of": ReporterSpec((Input("LETTER"), Input("STRING")), "string"),
    "operator_length": ReporterSpec((Input("STRING"),), "number"),
 
    # --- motion -----------------------------------------------------
    "motion_xposition": ReporterSpec((), "number"),
    "motion_yposition": ReporterSpec((), "number"),
    "motion_direction": ReporterSpec((), "number"),
 
    # --- looks --------------------------------------------------------
    "looks_size": ReporterSpec((), "number"),
    "looks_costumenumbername": ReporterSpec(
        (Field("NUMBER_NAME", ("number", "name")),), "string",
    ),
    "looks_backdropnumbername": ReporterSpec(
        (Field("NUMBER_NAME", ("number", "name")),), "string",
    ),
 
    # --- sound ----------------------------------------------------------
    "sound_volume": ReporterSpec((), "number"),
 
    # --- sensing ----------------------------------------------------
    "sensing_answer": ReporterSpec((), "string"),
    "sensing_mousex": ReporterSpec((), "number"),
    "sensing_mousey": ReporterSpec((), "number"),
    "sensing_loudness": ReporterSpec((), "number"),
    "sensing_timer": ReporterSpec((), "number"),
    "sensing_username": ReporterSpec((), "string"),
    "sensing_dayssince2000": ReporterSpec((), "number"),
    "sensing_current": ReporterSpec(
        (
            Field("CURRENTMENU", (
                "YEAR", "MONTH", "DATE", "DAYOFWEEK", "HOUR", "MINUTE", "SECOND",
            )),
        ),
        "number",
    ),
}