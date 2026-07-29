from typing import Any, Self
from abc import ABCMeta


class EmptyAbstractClass:
    """
    Abstract class that has no methods.
    """
    __metaclass__ = ABCMeta

    def __new__(cls, *_: tuple[Any], **__: dict[str, Any]) -> Self:
        if cls.__bases__ == (EmptyAbstractClass,):
            msg = f"Abstract class {cls.__name__} cannot be instantiated"
            raise TypeError(msg)
        
        return object.__new__(cls)
    