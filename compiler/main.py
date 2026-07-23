from parser import Parser, ParseError, FailState
from itch_ast import build_ast
# from tools.ast_printer import print_ast
from assembler import Assembler

import os
# from os.path import isfile
# import argparse

from pathlib import Path

ROOT = Path(__file__).parent.parent

parser = Parser()
assembler = Assembler()


def format_syntax_error(
    state: FailState,
    source: str,
    filename: str = "<source>",
    message: str = "invalid syntax",
) -> str:
    
    # we use max because state.pos has - 1 applied to it already, so it's impossible for it to be greater than the number of tokens.
    token = state.tokens[max(0, state.pos)]
    lines = source.splitlines()

    line_number = token.line
    character = token.char

    if 1 <= line_number <= len(lines):
        source_line = lines[line_number - 1]
    else:
        source_line = ""

    pointer_width = max(1, len(token.literal))

    return (
        f'  File "{filename}", line {line_number}\n'
        f"    {source_line}\n"
        f"    {' ' * (character - 1)}{'^' * pointer_width}\n"
        f"SyntaxError: {message}"
    )


def compile(file: str, output: str, target: str):
    """
    Compiles code to an existing output .sb3 file, targetting a specific sprite/stage
    """
    with open(file) as f:
        source = f.read()
        try:
            assembler.prepare_assemble()
            parsed = parser.read(source)
            tree = build_ast(parsed.tree)
            assembler.assemble(tree, output, target)
            # print_ast(tree)
        except ParseError:
            fail_state = parser.fail_state
            if fail_state is not None:
                
                # describe failure
                print(format_syntax_error(fail_state, source, file))
    
    
def main():
    input_path = ROOT / "input"
    output_path = ROOT / "output"
    
    stage = None
    paths: list[Path] = []

    for path in os.listdir(str(input_path.absolute())):
        file_name = os.path.basename(path)

        abs_path = input_path / file_name

        if file_name == "Stage.txt":
            stage = abs_path
        else:
            paths.append(abs_path)
    
    if stage is not None:
        paths.insert(0, stage)

    for file_name in paths:
        print("compiling: ", file_name)
        compile(str(file_name), str((output_path / "Scratch Project.sb3")), os.path.basename(str(file_name.with_suffix(''))))


if __name__ == "__main__":
    print("Running...")
    main()
    print("OKAY!")
