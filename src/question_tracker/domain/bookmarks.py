from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class BookmarkType:
    id: str
    name: str
    sort_order: int
