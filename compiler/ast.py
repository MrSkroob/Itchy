from dataclasses import dataclass
from abstractclass import EmptyAbstractClass

class ASTNode(EmptyAbstractClass):
    pass


class Statement(ASTNode):
    pass


class Expr(ASTNode):
    pass

@dataclass(frozen=True)
class Program(ASTNode):
    body: tuple[Statement, ...]