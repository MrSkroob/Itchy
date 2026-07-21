import sys

from parser import Parser
from itch_ast import build_ast
from tools.ast_printer import print_ast
from assembler import Assembler
from argparse import ArgumentParser,Action,Namespace,HelpFormatter
from pathlib import Path

parser = Parser()
assembler = Assembler()

print("Running")

argparser = ArgumentParser(description="Compile Scratch code")

argparser.add_argument("--output", help="Output file to compile", default="output/output.sb3")
argparser.add_argument("inputs", help="Input file to compile",action="append")

args = argparser.parse_args()


for inp in args.inputs:
    with open(inp) as f:
        result = parser.read(f.read())
        tree = build_ast(result.tree)
        outputPath = Path(args.output)
        outputPath.parent.mkdir(parents=True, exist_ok=True)
        assembler.assemble(tree, outputPath, "Sprite1")
    
        
        print_ast(tree)
