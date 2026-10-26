import functools
import inspect
from collections.abc import Callable
from typing import ParamSpec, TypeVar

from mcp.server.mcpserver.exceptions import ToolError
from pydantic import ValidationError

P = ParamSpec("P")
R = TypeVar("R")


def _short(value: object, limit: int = 60) -> str:
    text = repr(value)
    return text if len(text) <= limit else text[: limit - 3] + "..."


def message(exc: BaseException) -> str:
    """The domain's own wording, without pydantic wrappers, URLs or tracebacks."""
    if isinstance(exc, ValidationError):
        parts = []
        for err in exc.errors():
            text = err["msg"].removeprefix("Value error, ")
            loc = ".".join(str(p) for p in err["loc"])
            parts.append(text if err["type"] == "value_error" or not loc else f"{loc}: {text} (got {_short(err['input'])})")
        return "; ".join(parts)
    if isinstance(exc, KeyError) and exc.args:
        return str(exc.args[0])
    return str(exc)


def guarded(fn: Callable[P, R]) -> Callable[P, R]:
    """Turn domain failures into structured tool errors carrying only the message."""

    @functools.wraps(fn)
    def wrapper(*args: P.args, **kwargs: P.kwargs) -> R:
        try:
            return fn(*args, **kwargs)
        except ToolError:
            raise
        except (ValidationError, ValueError, KeyError) as e:
            raise ToolError(message(e)) from None

    if fn.__doc__:
        wrapper.__doc__ = inspect.cleandoc(fn.__doc__)
    return wrapper
