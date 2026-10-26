from collections.abc import Callable

from ezdxf.layouts import BlockLayout

from .model import Attr, Category, Pin, SymbolDef

REGISTRY: dict[str, SymbolDef] = {}


def symbol(
    key: str,
    title: str,
    category: Category,
    standard: str,
    width: float,
    height: float,
    pins: tuple[Pin, ...],
    attrs: tuple[Attr, ...] = (),
    *,
    tag_prefix: str = "",
) -> Callable[[Callable[[BlockLayout], None]], Callable[[BlockLayout], None]]:
    def register(draw: Callable[[BlockLayout], None]) -> Callable[[BlockLayout], None]:
        if key in REGISTRY:
            raise ValueError(f"duplicate symbol key {key!r}")
        REGISTRY[key] = SymbolDef(key, title, category, standard, width, height, pins, attrs, draw, tag_prefix)
        return draw

    return register
