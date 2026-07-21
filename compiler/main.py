from parser import Parser
from itch_ast import build_ast
from tools.ast_printer import print_ast
from assembler import Assembler

from tokenizer import Tokenizer, Definitions


parser = Parser()
assembler = Assembler()

print("Running")

with open("input/code.txt") as f:
    # tokenizer = Tokenizer(Definitions, {"Whitespace", "Newline"})
    # for token in tokenizer.read(f.read()):
    #     print(token.literal, token.kind.name)

    result = parser.read(f.read())
    tree = build_ast(result.tree)
    assembler.assemble(tree, "output/Scratch Project.sb3", "Sprite1")
    
    print_ast(tree)
