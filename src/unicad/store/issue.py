"""Whether the model is still the content that was issued: the latest issue revision records a fingerprint of the project as issued."""

import hashlib
import json

from unicad.domain.project import IssueRevision, Project


def content_sha(project: Project) -> str:
    """Fingerprint of everything the drawings are rendered from, leaving out the issue revisions themselves (project and sheet revision lists)."""
    data = project.model_dump(mode="json", exclude_defaults=True)
    data.pop("revisions", None)
    for s in data.get("sheets", ()):
        s.pop("revisions", None)
    return hashlib.sha256(json.dumps(data, sort_keys=True, ensure_ascii=False).encode()).hexdigest()[:16]


def latest_issue(project: Project) -> IssueRevision | None:
    return project.revisions[-1] if project.revisions else None


def not_issued_content(project: Project) -> str | None:
    """Why the model is not the issued revision, or None when it is (or nothing was issued, or the issue predates fingerprints)."""
    issue = latest_issue(project)
    if issue is None or issue.content_sha is None or issue.content_sha == content_sha(project):
        return None
    at = f" (model revision {issue.model_revision})" if issue.model_revision is not None else ""
    return (f"the model has changed since Rev {issue.rev} was issued{at}: drawings rendered from it are not the issued Rev {issue.rev}; "
            f"issue a new revision mark with project_issue, or project_issue(rev='{issue.rev}', amend=true) to re-issue Rev {issue.rev} with this content")
