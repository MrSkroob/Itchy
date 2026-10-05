from __future__ import annotations

# from abc import ABC, abstractmethod
from collections.abc import Mapping


from pathlib import Path
from itchy.itch_ast import Program

from typing import Protocol


class BaseAssembler(Protocol):
    def __init__(self, uri: str, is_strict: bool=True, compile_with_warnings: bool=False) -> None:
        """
        is_strict: whether the compiler should halt on error. Enabling this option will also disable any write to the .sb3 file.
        """
        ...

    def emit_program(self, program: Program) -> None:
        """
        Takes a program object only and emits each sequence within said program.
        uses .emit_statements() internally so statements do not connect to each other. 
        """
        ...

    def assemble(
        self,
        programs: Mapping[str, Program],
        project_directory: str | Path,
        project_file: str | Path | None = None,
    ) -> Path:
        ... # OMG I FOUND A USE FOR ...!!!!!!!!!!!!!
    