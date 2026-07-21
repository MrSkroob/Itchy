from parser import Parser
from itch_ast import build_ast
from tools.ast_printer import print_ast
from assembler import Assembler


parser = Parser()
assembler = Assembler()

print("Running")


def compile(code: str, output: str, target: str):
    """
    Compiles code to an existing output .sb3 file, targetting a specific sprite/stage
    """
    parsed = parser.read(code)
    tree = build_ast(parsed.tree)
    assembler.assemble(tree, output, target)
    print_ast(tree)


with open("input/code.txt") as f:

    compile(f.read(), "output/Scratch Project.sb3", "Sprite1")
    
