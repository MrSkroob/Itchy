from parser import Parser
from tokenizer import Tokenizer, Definitions
from itch_ast import build_ast
from tools.ast_printer import print_ast


parser = Parser()

with open("input/code.txt") as f:
    # for token in Tokenizer(Definitions, {"Whitespace"}).read(f.read()):
    #     print(token.literal)
    result = parser.read(f.read())
    # print(result)
    # print_ast(result)
    tree = build_ast(result.tree)
    print_ast(tree)
