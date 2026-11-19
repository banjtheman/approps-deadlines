"""The pages we crawl.

Each Senate Appropriations subcommittee page has a "Submit Written Testimony"
link. Most point at an intermediate /download/... page that forwards to the
PDF of guidelines; some point straight at the PDF.
"""

from __future__ import annotations

from dataclasses import dataclass

SENATE_BASE = "https://www.appropriations.senate.gov/subcommittees/"


@dataclass(frozen=True)
class Subcommittee:
    id: str  # short, stable key used in state and file names
    chamber: str
    name: str
    page_url: str


def _senate(id_: str, name: str, slug: str) -> Subcommittee:
    return Subcommittee(id=f"senate-{id_}", chamber="Senate", name=name, page_url=SENATE_BASE + slug)


SENATE_SUBCOMMITTEES: list[Subcommittee] = [
    _senate(
        "ag",
        "Agriculture, Rural Development, Food and Drug Administration, and Related Agencies",
        "agriculture-rural-development-food-and-drug-administration-and-related-agencies",
    ),
    _senate("cjs", "Commerce, Justice, Science, and Related Agencies", "commerce-justice-science-and-related-agencies"),
    _senate("defense", "Defense", "defense"),
    _senate("energy-water", "Energy and Water Development", "energy-and-water-development"),
    _senate("fsgg", "Financial Services and General Government", "financial-services-and-general-government"),
    _senate("homeland", "Homeland Security", "homeland-security"),
    _senate("interior", "Interior, Environment, and Related Agencies", "interior-environment-and-related-agencies"),
    _senate(
        "labor-hhs",
        "Labor, Health and Human Services, Education, and Related Agencies",
        "labor-health-and-human-services-education-and-related-agencies",
    ),
    _senate("leg-branch", "Legislative Branch", "legislative-branch"),
    _senate(
        "milcon-va",
        "Military Construction, Veterans Affairs, and Related Agencies",
        "military-construction-veterans-affairs-and-related-agencies",
    ),
    _senate("sfops", "State, Foreign Operations, and Related Programs", "state-foreign-operations-and-related-programs"),
    _senate(
        "thud",
        "Transportation, Housing and Urban Development, and Related Agencies",
        "transportation-housing-and-urban-development-and-related-agencies",
    ),
]
