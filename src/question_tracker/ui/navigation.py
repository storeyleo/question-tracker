"""Screen history stores destinations, never references to disposable widgets."""
from dataclasses import dataclass, field
from collections.abc import Callable


@dataclass(frozen=True)
class Destination:
    screen: str
    args: tuple = ()
    on_back: Callable | None = field(default=None, compare=False)


class NavigationHistory:
    def __init__(self) -> None:
        self.entries: list[Destination] = []
        self.index = -1

    @property
    def current(self) -> Destination | None:
        return self.entries[self.index] if self.index >= 0 else None

    def replace_current(self, entry: Destination) -> None:
        if self.index >= 0:
            self.entries[self.index] = entry

    def visit(self, entry: Destination) -> None:
        if entry == self.current:
            return
        del self.entries[self.index + 1:]
        self.entries.append(entry)
        self.index += 1

    def move(self, offset: int) -> Destination | None:
        index = self.index + offset
        if not 0 <= index < len(self.entries):
            return None
        self.index = index
        return self.current
