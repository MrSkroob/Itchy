from itchy.parser import Parser, ParseError
from itchy.itch_ast import ASTBuilder, Program
from tools.ast_printer import print_ast
from itchy.errors import format_syntax_error, format_compiler_error
from itchy.assembler import Assembler, CompilerError
from itchy.tokenizer import Tokenizer, Definitions
from itchy.dummy_nodes import find_node, AGGRESSIVE_STRATEGIES
import argparse

import os
# from os.path import isfile
# import argparse

from pathlib import Path

ROOT = Path(__file__).parent.parent


parser = Parser(skip_bad_tokens=True, skip_rules_on_fail=AGGRESSIVE_STRATEGIES)
ast_builder = ASTBuilder()
assembler = Assembler(is_strict=False)

   
def compile(file: str, output: str, target: str):
    """
    Compiles code to an existing output .sb3 file, targetting a specific sprite/stage
    """
    with open(file) as f:
        source = f.read()
        try:
            assembler.prepare(output)
            parsed = parser.read(source)
            tree = ast_builder.build(parsed.tree)
            # tree = build_ast(parsed.tree)
            assembler.emit_program(tree)
            # assembler.assemble(tree, output, target)
            # print_ast(tree)
        except (ParseError, CompilerError) as e:
            if isinstance(e, ParseError):
                fail_state = parser.fail_state
                # print_ast(parser.deepest_partial)
                # tree = ast_builder.build_eventstat(find_node(parser.deepest_partial.tree, "eventstat"))
                # print(tree.name, tree.params)

                if fail_state is not None:
                    print(format_syntax_error(fail_state, source, file))
            else:
                print(format_compiler_error(e, source, file))

            return False
    print([i for i in parser.speculative_errors])
    print([i for i in parser.accumulated_errors])
    print([i.error_node.span.start.line for i in assembler.errors])
    return True


def main():
    cli_parser = argparse.ArgumentParser(
        prog="Itchy Compiler",
        description="Compiles itchy code to .sb3"
    )
    cli_parser.add_argument("source", help="Path to the itch code", type=str)
    cli_parser.add_argument("output", help="Output .sb3 file", type=str)
    args = cli_parser.parse_args()
    source_path = Path(args.source)
    if source_path.suffix != ".itch":
        print(f"provided file {source_path} is not a .itch file.")
    
    compile(args.source, args.output, os.path.basename(str(source_path.with_suffix(''))))


if __name__ == "__main__":
    raise SystemExit(main())
