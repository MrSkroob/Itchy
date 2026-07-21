from parser import Parser, ParseError, FailState
from itch_ast import build_ast
from tools.ast_printer import print_ast
from assembler import Assembler
from tree import Terminal, NonTerminal, Rule
# from pathlib import Path

parser = Parser()
assembler = Assembler()

print("Running")


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
            parsed = parser.read(source)
            tree = build_ast(parsed.tree)
            assembler.assemble(tree, output, target)
            print_ast(tree)
        except ParseError:
            fail_state = parser.fail_state
            if fail_state is not None:
                
                # describe failure
                print(format_syntax_error(fail_state, source, file))
    


with open("input/code.txt") as f:
    compile("input/code.txt", "output/Scratch Project.sb3", "Sprite1")
    
