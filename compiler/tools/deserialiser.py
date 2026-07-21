import argparse
import json
import zipfile
from typing import Any


def load_project(file_name: str) -> dict[str, Any]:
    """Returns the json data inside the scratch project with given file name"""
    with zipfile.ZipFile(file_name, "r") as f:
        project_json = json.loads(f.read("project.json").decode("utf-8"))
    return project_json


def print_out_all(json_dict: dict[str, Any]):
    print(json.dumps(json_dict, indent=4))


def print_out_stuff(json_dict: dict[str, Any]):
    # print("FULL: ", json.dumps(project_json, indent=4))
    for i in json_dict["targets"]:
        print("\nTARGET:", i["name"])
        print(i["variables"])
        print(i["lists"])
        blocks = i["blocks"]
        for id in blocks:
            print(id, json.dumps(blocks[id], indent=4, ensure_ascii=True))


# `project_json = load_project("scratch_project/Scratch Project.sb3")
# print_out_stuff(project_json)
# print_out_all(load_project("scratch_project/Guinnea Pig.sb3"))


parser = argparse.ArgumentParser()
parser.add_argument("--dir", help="file directory to print out")
args = parser.parse_args()
project_json = load_project(args.dir)
print_out_stuff(project_json)


# example of block data:
# for "motion_turnleft":
# "DEGREES" : [ -- obviously the name of the parameter
#   1 -- See below
#   [ clear
#       4, -- See below
#       "15" -- This is the value inputed by the user, i.e. 15 degrees
#   ]
# ]

"""
From: https://en.scratch-wiki.info/wiki/Scratch_File_Format

A shadow block is a reporter in an input for which one can enter or pick a value, 
and which cannnot be dragged around but can be replaced by a normal reporter.
[7] Scratch internally considers these to be blocks although they are not usually thought of as such. 
(These notions come from Blockly, which Scratch Blocks are based on.) 

An object associating names with arrays representing inputs into which other blocks may be dropped, including C mouths. 
The first element of each array is 1 if the input is a shadow, 2 if there is no shadow, and 3 if there is a shadow but it is obscured by the input.

The second is either the ID of the input or an array representing it as described in the table below. 
If there is an obscured shadow, the third element is its ID or an array representing it.
"""