# AI DISCLOSURE:
# This code was developed with assistance from OpenAI's ChatGPT.
# AI-generated suggestions were reviewed, modified, and integrated by the author.

from itchy.parser import Parser, ParseError
from itchy.itch_ast import ASTBuilder
from itchy.errors import format_syntax_error, format_compiler_error
from itchy.assembler import Assembler, CompilerError

import argparse

from pathlib import Path


parser = Parser(
    skip_bad_tokens=False,
)

ast_builder = ASTBuilder()
assembler = Assembler("")


def compile_target(
    file: Path,
    project: Path,
    output: Path | None,
    target: str,
) -> bool:
    """
    Compiles one Itchy target into the output Scratch project.

    `file` is the target's .itch source file.
    `project` is the root Itchy project directory.
    `output` is the generated .sb3 file.
    `target` is the Scratch target name.
    """

    source = file.read_text(encoding="utf-8")

    if output is None:
        output = project / "Scratch Project.sb3"
    if output.is_dir():
        output = output / "Scratch Project.sb3"

    try:
        # Once the output exists, prepare() can load project-wide
        # variables/broadcasts from the already-compiled Stage.
        if output.exists():
            assembler.prepare(str(output))
        else:
            assembler.prepare()

        parsed = parser.read(source)
        tree = ast_builder.build(parsed.tree)

        output_path = assembler.assemble(
            tree,
            str(project),
            str(output),
            target,
        )

        print(f"Compiled output at: {str(output_path)}")

    except (ParseError, CompilerError) as e:
        if isinstance(e, ParseError):
            print(
                format_syntax_error(
                    e,
                    parser.expected,
                    source,
                    str(file),
                )
            )
        else:
            print(
                format_compiler_error(
                    e,
                    source,
                    str(file),
                )
            )

        return False

    return True


def compile_project(
    project: Path,
    output: Path | None,
    exact_target: str | None=None
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
    sprites: list[tuple[str, Path]] = []

    for directory in project.iterdir():
        if not directory.is_dir():
            continue

        if directory.name == "Stage":
            continue

        if exact_target and directory.name != exact_target:
            continue

        source_file = directory / f"{directory.name}.itch"

        if not source_file.is_file():
            continue

        sprites.append(
            (directory.name, source_file)
        )

    # Keep build order deterministic.
    sprites.sort(key=lambda sprite: sprite[0])

    # Start from a clean Scratch project.
    #
    # Otherwise, if Sprite2 was present in an earlier build and its
    # directory is later deleted, Sprite2 could remain in the old .sb3.
    if output and output.exists() and not output.is_dir():
        output.unlink()

    # Stage must be assembled first because it owns project-wide
    # Scratch state such as shared variables and broadcasts.

    if not compile_target(
        stage_file,
        project,
        output,
        "Stage",
    ):
        return False

    for target, source_file in sprites:
        if not compile_target(
            source_file,
            project,
            output,
            target,
        ):
            return False

    return True


def main() -> int:
    cli_parser = argparse.ArgumentParser(
        prog="Itchy Compiler",
        description="Compiles an Itchy project to .sb3",
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
        default=""
    )

    args = cli_parser.parse_args()

    project_path = Path(args.source)
    output_path = Path(args.output) if args.output else None

    compile_file = False

    if not project_path.is_dir():
        compile_file = True
        # return 1
    

    if output_path and output_path.suffix.lower() != ".sb3" and not output_path.is_dir():
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
        ):
            return 1
    else:
        if not compile_project(
            project_path,
            output_path,
        ):
            return 1

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
