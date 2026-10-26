"""Drawing numbers for new sheets, from the company profile's pattern and the numbers the project already uses."""

import re
from dataclasses import dataclass, field

from unicad.domain.project import Project, Sheet
from unicad.drafting.sheets.model import CompanyProfile, pattern_regex


def _code(text: str, limit: int = 8) -> str:
    """A short code from words: initials of plain words, the digits of words with a number ('utility-50mwac' -> 'U50')."""
    words = [w for w in re.split(r"[^A-Za-z0-9]+", text.upper()) if w]
    code = "".join(re.sub(r"[^0-9]", "", w) or w[0] for w in words)
    return code[:limit] or "X"


@dataclass
class Numberer:
    """Hands out drawing numbers: a project's existing SLD numbers first (in sheet order), then fresh ones
    after the highest sequence number the project uses, with the client and project codes it already uses."""

    profile: CompanyProfile
    client: str
    project: str
    issued: list[str]
    reusable: dict[str, list[Sheet]] = field(default_factory=dict)

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
        """The next not yet reused project sheet of `kind`, whose number, paper and revisions a planned sheet takes over."""
        left = self.reusable.get(kind) or []
        return left.pop(0) if left else None

    def fresh(self, discipline: str = "electrical") -> str:
        number = self.profile.next_number(self.issued, client=self.client, project=self.project, discipline=discipline)
        self.issued.append(number)
        return number
