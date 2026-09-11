from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class ModuleGroup:
    id: str
    year_key: str
    name: str
