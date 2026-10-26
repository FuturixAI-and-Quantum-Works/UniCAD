from typing import Annotated

from pydantic import Field

ProjectIdArg = Annotated[str, Field(description="Project id, e.g. 'rooftop-500kwp'. project_list shows existing ids.")]
RevisionArg = Annotated[
    int | str | None,
    Field(description="Revision number or snapshot label. Omit for the latest revision."),
]
