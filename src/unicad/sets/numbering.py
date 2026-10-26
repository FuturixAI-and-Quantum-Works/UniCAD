"""Drawing numbers for new sheets, from the company profile's pattern and the numbers the project already uses."""

import re
from dataclasses import dataclass, field

from unicad.domain.project import Project, Sheet
from unicad.drafting.sheets.model import CompanyProfile, pattern_regex

BLOCKS: dict[str, int] = {"register": 0, "sld": 1, "layout": 101, "earthing": 301, "lps": 351, "strings": 401, "cable_schedule": 501, "bom": 601}
FOLLOWS: dict[str, str] = {"lps": "earthing"}
"""A block that continues right after another kind's sheets when that kind is in the set and ends below the block's own start."""


def _code(text: str, limit: int = 8) -> str:
    """A short code from words: initials of plain words, the digits of words with a number ('utility-50mwac' -> 'U50')."""
    words = [w for w in re.split(r"[^A-Za-z0-9]+", text.upper()) if w]
    code = "".join(re.sub(r"[^0-9]", "", w) or w[0] for w in words)
    return code[:limit] or "X"


@dataclass
class Numberer:
    """Hands out drawing numbers: a project's existing numbers of a sheet kind first (in sheet order, via `reuse`), then fresh
    ones from the kind's block, skipping every number in `issued` (the project's numbers and those handed out so far). Stored numbers
    are kept even when they follow another company's pattern: an issued drawing keeps its number.

    `fresh(block)` takes a set kind ('register', 'sld', 'layout', 'earthing', 'lps', 'strings', 'cable_schedule', 'bom') or a
    discipline name ('electrical', 'civil', ...). A kind's discipline is the kind itself when the profile's `discipline_codes`
    names it, else 'electrical'; a discipline name numbers like an SLD of that discipline.

    Blocks are offsets from the discipline's base: register 0, SLD 1, layout 101, earthing 301, LPS 351 (or right after the
    earthing sheets when there are any), strings 401, cable schedule 501, BOM 601. The base is 0, giving those numbers as they
    are, unless the profile counts per discipline (`numbering.per_discipline`) and sets `numbering.start` for the discipline:
    then the base is start - 1, so the SLD takes the start number, the register the one below it and every other kind
    start + (its block - 1). With per_discipline each discipline also has its own sequence."""

    profile: CompanyProfile
    client: str
    project: str
    issued: list[str]
    reusable: dict[str, list[Sheet]] = field(default_factory=dict)
    taken: dict[str, list[int]] = field(default_factory=dict)

    @classmethod
    def for_project(cls, project: Project, profile: CompanyProfile) -> "Numberer":
        rx = pattern_regex(profile.drawing_number_pattern)
        found = [m for s in project.sheets if (m := rx.match(s.number))]
        client = next((m.group("client") for m in found if "client" in m.groupdict()), None)
        code = next((m.group("project") for m in found if "project" in m.groupdict()), None)
        reusable: dict[str, list[Sheet]] = {}
        for s in project.sheets:
            reusable.setdefault(s.kind.value, []).append(s)
        return cls(profile, client or project.client.code or _code(project.client.name), code or _code(project.id), [s.number for s in project.sheets], reusable)

    def reuse(self, kind: str) -> Sheet | None:
        """The next not yet reused project sheet of `kind` (a SheetKind value), whose number, paper and revisions a planned sheet takes over."""
        left = self.reusable.get(kind) or []
        return left.pop(0) if left else None

    def discipline(self, block: str) -> str:
        return block if block in self.profile.discipline_codes or block not in BLOCKS else "electrical"

    def base(self, discipline: str) -> int:
        numbering = self.profile.numbering
        return numbering.start[discipline] - 1 if numbering.per_discipline and discipline in numbering.start else 0

    def _start(self, block: str) -> int:
        start = self.base(self.discipline(block)) + BLOCKS.get(block, BLOCKS["sld"])
        before = self.taken.get(FOLLOWS.get(block, ""))
        if before and max(before) + 1 < start:
            return max(before) + 1
        return start

    def fresh(self, block: str = "sld") -> str:
        """The lowest unused number of `block` after those it already handed out."""
        seq = max(self.taken.get(block, [self._start(block) - 1])) + 1
        while (number := self.number(block, seq)) in self.issued:
            seq += 1
        self.issued.append(number)
        self.taken.setdefault(block, []).append(seq)
        return number

    def number(self, block: str, seq: int) -> str:
        return self.profile.drawing_number(client=self.client, project=self.project, discipline=self.discipline(block), seq=seq)
