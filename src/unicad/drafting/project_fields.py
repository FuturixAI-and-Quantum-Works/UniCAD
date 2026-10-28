"""The project's own entries in every title block: owner, developer, project title and location with the site coordinates."""

from typing import Any

from unicad.domain.project import Project, Site
from unicad.drafting.sheets.titleblock import compose_location
from unicad.drafting.sld.annotate import prose


def coordinates(site: Site) -> str | None:
    """The site's given latitude and longitude as printed, e.g. 'LAT 26.887058 N, LONG 75.719544 E'."""
    if site.latitude is None or site.longitude is None:
        return None
    lat, lon = site.latitude, site.longitude
    return f"LAT {abs(lat):.6f} {'N' if lat >= 0 else 'S'} LONG {abs(lon):.6f} {'E' if lon >= 0 else 'W'}"


def developer(project: Project) -> str | None:
    party = getattr(project, "developer", None)
    name = getattr(party, "name", party)
    return prose(name) if isinstance(name, str) and name.strip() else None


def location(project: Project) -> str:
    site = project.site
    return prose(compose_location(site.name, site.address, site.state, coordinates(site)))


def project_fields(project: Project) -> dict[str, Any]:
    """TitleBlockData fields that come from the project rather than the sheet."""
    return {"client": prose(project.client.name), "developer": developer(project), "project_title": prose(project.name), "location": location(project) or "-"}
