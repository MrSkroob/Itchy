# AI DISCLOSURE:
# This code was developed with assistance from OpenAI's ChatGPT.
# AI-generated suggestions were reviewed, modified, and integrated by the author.

from itchy.dummy_nodes import ANALYSIS_STRATEGIES# , find_last_node
from itchy.parserv2 import Parser, ParseResult
from itchy.itch_ast import ASTBuilder, Program
from itchy.errors import format_compiler_error, format_syntax_error
from itchy.assembler import Assembler, CompilerError

import argparse

from pathlib import Path

import time

from tools.ast_printer import print_ast


# Replace RULES with whatever list of grammar rules your parser uses.
strict_parser = Parser()

non_strict_parser = Parser(
    allow_recovery=True,
    allow_insertions=True,
    recovery_nodes=ANALYSIS_STRATEGIES,
)

DEBUG_MODE = False

if DEBUG_MODE:
    parser = non_strict_parser
else:
    parser = strict_parser

ast_builder = ASTBuilder(is_strict=not DEBUG_MODE)
strict_assembler = Assembler("")
non_strict_assembler = Assembler("", compile_with_warnings=True)


def compile_targets(
    files: list[Path],
    project: Path,
    output: Path | None,
    allow_warnings: bool = False,
) -> Path | None:
    programs: dict[str, Program] = {}
    metadata: dict[str, tuple[str, Path]] = {}

    start = time.time()

    for file in files:
        source = file.read_text(encoding="utf-8")

        if output is None:
            output = project / "Scratch Project.sb3"

        if output.is_dir():
            output = output / "Scratch Project.sb3"

        parsed: ParseResult = parser.read(source)

        if parsed.failed:
            # print_ast(ast_builder.build_eventstat(find_last_node(parsed.partial_tree, "eventstat")))
            print(
                format_syntax_error(
                    parsed,
                    parsed.expected,
                    source,
                    str(file),
                )
            )
            return None

        tree = ast_builder.build(parsed.tree)

        if DEBUG_MODE:
            print_ast(tree)

        programs[file.stem] = tree
        metadata[file.stem] = (source, file)

    finish = time.time()
    print("COMPILATION TIME: ", finish - start)

    if allow_warnings:
        assembler = non_strict_assembler
    else:
        assembler = strict_assembler

    try:
        return assembler.assemble(
            programs,
            project,
            output,
        )

    except CompilerError as e:
        if assembler.compiling is None:
            print(e.message)

        else:
            file_metadata = metadata[assembler.compiling]

            print(
                format_compiler_error(
                    e,
                    file_metadata[0],
                    str(file_metadata[1]),
                )
            )

        return None


def compile_project(
    project: Path,
    output: Path | None,
    exact_target: str | None = None,
    allow_warnings: bool = False,
) -> bool:
    """
    Compiles an Itchy project directory into an .sb3.

    Expected layout:

        Project/
            Stage/
                Stage.itch
                costumes/
                sounds/

            Sprite1/
                Sprite1.itch
                costumes/
                sounds/

            AnotherSprite/
                AnotherSprite.itch
                costumes/
                sounds/
    """

    stage_directory = project / "Stage"
    stage_file = stage_directory / "Stage.itch"

    if not stage_directory.is_dir():
        print("Project does not contain a Stage directory.")
        return False

    if not stage_file.is_file():
        print(f"Stage source file not found: {stage_file}")
        return False

    # Discover sprite directories.
    #
    # A directory is considered a sprite if it contains a matching
    # source file:
    #
    #     Ball/Ball.itch
    #
    # This means unrelated directories such as .git and .vscode are
    # automatically ignored.
    sprites: list[Path] = []

    for directory in project.iterdir():
        if not directory.is_dir():
            continue

        if (
            exact_target
            and directory.stem.casefold() != exact_target.casefold()
            and directory.stem.casefold() != "stage"
        ):
            continue

        source_file = directory / f"{directory.name}.itch"

        if not source_file.is_file():
            continue

        sprites.append(source_file)

    output_path = compile_targets(
        sprites,
        project,
        output,
        allow_warnings,
    )

    if output_path is None:
        return False

    print(f"Done. Output: {str(output_path)}")
    return True


def main() -> int:
    cli_parser = argparse.ArgumentParser(
        prog="Itchy Compiler",
        description="Compiles an Itchy project to .sb3",
    )

    cli_parser.add_argument(
        "--allow-warnings",
        action="store_true",
    )

    cli_parser.add_argument(
        "source",
        help="Path to the Itchy project directory or script",
        type=str,
    )

    cli_parser.add_argument(
        "output",
        help="Output .sb3 file",
        nargs="?",
        type=str,
        default="",
    )

    args = cli_parser.parse_args()

    project_path = Path(args.source)
    output_path = Path(args.output) if args.output else None

    compile_file = not project_path.is_dir()

    if (
        output_path
        and output_path.suffix.lower() != ".sb3"
        and not output_path.is_dir()
    ):
        print(
            f"Provided output '{output_path}' "
            "is not an .sb3 file or a directory."
        )
        return 1

    if compile_file:
        if not compile_project(
            project_path.parent.parent,
            output_path,
            project_path.stem,
            args.allow_warnings,
        ):
            return 1

    else:
        if not compile_project(
            project_path,
            output_path,
            allow_warnings=args.allow_warnings,
        ):
            return 1

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
