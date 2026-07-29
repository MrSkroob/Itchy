from parser import Parser, ParseError
from itch_ast import build_ast
# from tools.ast_printer import print_ast
from errors import format_syntax_error, format_compiler_error
from assembler import Assembler, CompilerError
import argparse

import os
# from os.path import isfile
# import argparse

from pathlib import Path

ROOT = Path(__file__).parent.parent

parser = Parser()
assembler = Assembler()


def compile(file: str, output: str, target: str):
    """
    Compiles code to an existing output .sb3 file, targetting a specific sprite/stage
    """
    with open(file) as f:
        source = f.read()
        try:
            assembler.prepare(output)
            parsed = parser.read(source)
            tree = build_ast(parsed.tree)
            assembler.assemble(tree, output, target)
            # print_ast(tree)
        except (ParseError, CompilerError) as e:
            if isinstance(e, ParseError):
                fail_state = parser.fail_state
                if fail_state is not None:
                    print(format_syntax_error(fail_state, source, file))
            else:
                print(format_compiler_error(e, source, file))

            return False
    return True

    
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
    cli_parser = argparse.ArgumentParser(
        prog="Itchy Compiler",
        description="Compiles itchy code to .sb3"
    )
    cli_parser.add_argument("source", help="Path to the itch code", type=str)
    cli_parser.add_argument("output", help="Output .sb3 file", type=str)
    args = cli_parser.parse_args()
    source_path = Path(args.source)
    compile(args.source, args.output, os.path.basename(str(source_path.with_suffix(''))))
