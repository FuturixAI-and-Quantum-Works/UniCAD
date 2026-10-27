import functools
import inspect
import logging
import traceback
from pathlib import Path
from collections.abc import Callable
from typing import ParamSpec, TypeVar

from mcp.server.mcpserver.exceptions import ToolError
from pydantic import ValidationError

P = ParamSpec("P")
R = TypeVar("R")


def _short(value: object, limit: int = 60) -> str:
    text = repr(value)
    return text if len(text) <= limit else text[: limit - 3] + "..."


log = logging.getLogger(__name__)


def _where(exc: BaseException) -> str:
    """'file.py:line in function' of the innermost frame in unicad (else the innermost at all), for a report that points at the bug."""
    frames = [f for f in traceback.extract_tb(exc.__traceback__) if Path(f.filename) != Path(__file__)]
    ours = [f for f in frames if "unicad" in Path(f.filename).parts]
    f = (ours or frames or [None])[-1]
    return f"{Path(f.filename).name}:{f.lineno} in {f.name}" if f else ""


def anticipated(exc: BaseException) -> bool:
    """Failures raised on purpose with a message for the caller: validation, ValueError, and lookups whose KeyError is a sentence."""
    if isinstance(exc, ValidationError):
        return True
    if isinstance(exc, KeyError):
        return bool(exc.args) and isinstance(exc.args[0], str) and " " in exc.args[0]
    return isinstance(exc, ValueError) and bool(str(exc))


def unexpected(exc: BaseException) -> str:
    """'Type: message (at file:line in function)' for a failure the code did not anticipate; never empty."""
    text = repr(exc.args[0]) if isinstance(exc, KeyError) and exc.args else str(exc)
    where = _where(exc)
    return f"{type(exc).__name__}: {text or 'no message'}" + (f" (at {where})" if where else "")


def message(exc: BaseException) -> str:
    """The domain's own wording, without pydantic wrappers, URLs or tracebacks; unanticipated failures name their type."""
    if not anticipated(exc):
        return unexpected(exc)
    if isinstance(exc, ValidationError):
        parts = []
        for err in exc.errors():
            text = err["msg"].removeprefix("Value error, ")
            loc = ".".join(str(p) for p in err["loc"])
            parts.append(text if err["type"] == "value_error" or not loc else f"{loc}: {text} (got {_short(err['input'])})")
        return "; ".join(parts)
    return str(exc.args[0]) if isinstance(exc, KeyError) else str(exc)


def guarded(fn: Callable[P, R]) -> Callable[P, R]:
    """Turn every failure into a tool error the client can read: anticipated ones carry their message, anything else its type,
    message and where it was raised (with the traceback in the server log)."""

    @functools.wraps(fn)
    def wrapper(*args: P.args, **kwargs: P.kwargs) -> R:
        try:
            return fn(*args, **kwargs)
        except ToolError:
            raise
        except Exception as e:
            if not anticipated(e):
                log.error("tool %s failed", fn.__name__, exc_info=e)
            raise ToolError(message(e)) from None

    wrapper.__unicad_guarded__ = True  # type: ignore[attr-defined]

    if fn.__doc__:
        wrapper.__doc__ = inspect.cleandoc(fn.__doc__)
    return wrapper
