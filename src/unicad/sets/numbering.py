"""Drawing numbers for new sheets, from the company profile's pattern and the numbers the project already uses.

Fresh numbers come from one sequence block per drawing kind (register 000, SLD 001-, layout 101-, earthing 301-, LPS after the
earthing sheets or from 351, strings 401-, schedules 501-, BOM 601-), skipping every number the project or the set already uses.
The discipline field is the kind's own code when the profile's `discipline_codes` names the kind, else the electrical code."""

import re
import string
from dataclasses import dataclass, field

from unicad.domain.project import Project, Sheet
from unicad.drafting.sheets.model import CompanyProfile

BLOCKS: dict[str, int] = {"register": 0, "sld": 1, "layout": 101, "earthing": 301, "lps": 351, "strings": 401, "cable_schedule": 501, "bom": 601}
FOLLOWS: dict[str, str] = {"lps": "earthing"}
"""A block that continues right after another kind's sheets when that kind is in the set and ends below the block's own start."""


def _regex(pattern: str) -> re.Pattern[str]:
    out = []
    for literal, name, _, _ in string.Formatter().parse(pattern):
        out.append(re.escape(literal))
        if name is not None:
            out.append(r"(?P<seq>\d+)" if name == "seq" else rf"(?P<{name}>.+?)")
    return re.compile("".join(out) + "$")


def _code(text: str, limit: int = 8) -> str:
    """A short code from words: initials of plain words, the digits of words with a number ('utility-50mwac' -> 'U50')."""
    words = [w for w in re.split(r"[^A-Za-z0-9]+", text.upper()) if w]
    code = "".join(re.sub(r"[^0-9]", "", w) or w[0] for w in words)
    return code[:limit] or "X"


@dataclass
class Numberer:
    """Hands out drawing numbers: a project's existing numbers of a sheet kind first (in sheet order), then fresh ones from the
    kind's block, with the client and project codes the project already uses."""

    profile: CompanyProfile
    client: str
    project: str
    reusable: dict[str, list[Sheet]] = field(default_factory=dict)
    used: set[str] = field(default_factory=set)
    issued: dict[str, list[int]] = field(default_factory=dict)

    @classmethod
    def for_project(cls, project: Project, profile: CompanyProfile) -> "Numberer":
        rx = _regex(profile.drawing_number_pattern)
        found = [m for s in project.sheets if (m := rx.match(s.number))]
        client = next((m.group("client") for m in found if "client" in m.groupdict()), None)
        code = next((m.group("project") for m in found if "project" in m.groupdict()), None)
        reusable: dict[str, list[Sheet]] = {}
        for s in project.sheets:
            reusable.setdefault(s.kind.value, []).append(s)
        return cls(profile, client or project.client.code or _code(project.client.name), code or _code(project.id), reusable, {s.number for s in project.sheets})

    def reuse(self, kind: str) -> Sheet | None:
        """The next not yet reused project sheet of `kind` (a SheetKind value), whose number, paper and revisions a planned sheet takes over."""
        left = self.reusable.get(kind) or []
        return left.pop(0) if left else None

    def discipline(self, block: str) -> str:
        return block if block in self.profile.discipline_codes else "electrical"

    def _start(self, block: str) -> int:
        start = BLOCKS.get(block, max(BLOCKS.values()) + 100)
        before = self.issued.get(FOLLOWS.get(block, ""))
        if before and max(before) + 1 < start:
            return max(before) + 1
        return start

    def fresh(self, block: str = "sld") -> str:
        """The lowest unused number of `block` (a set kind: 'sld', 'layout', 'earthing', ...)."""
        seq = max(self.issued.get(block, [self._start(block) - 1])) + 1
        while (number := self.number(block, seq)) in self.used:
            seq += 1
        self.used.add(number)
        self.issued.setdefault(block, []).append(seq)
        return number

    def number(self, block: str, seq: int) -> str:
        return self.profile.drawing_number(client=self.client, project=self.project, discipline=self.discipline(block), seq=seq)
