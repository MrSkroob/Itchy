from parser import Parser
from itch_ast import build_ast


parser = Parser()

with open("input/code.txt") as f:
    result = parser.read(f.read())
    build_ast(result.tree)
