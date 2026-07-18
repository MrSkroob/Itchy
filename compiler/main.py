from parser import Parser
from itch_ast import build_ast
from tools.ast_printer import print_ast


parser = Parser()

print("Running")

with open("input/code.txt") as f:
    result = parser.read(f.read())
    tree = build_ast(result.tree)
    print_ast(tree)
