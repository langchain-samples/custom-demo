"""Dummy UN report corpus.

This stands in for a real document store. Each entry carries free-text
(`text`) that the agent can quote/ground on, plus machine-readable `data`
(structured stats) the agent can feed straight into dashboard widgets.

The corpus intentionally covers the three demo personas:
  - Donor:              humanitarian aid impact in Egypt
  - Vulnerable group:   resources for displaced families in Iran
  - Technical/NGO:      water scarcity & sanitation in Canada
plus a couple of neighbours so retrieval has to actually discriminate.
"""

from __future__ import annotations

from typing import Any, TypedDict


class Document(TypedDict):
    id: str
    title: str
    source: str
    region: str
    topic: str
    period: str
    text: str
    data: dict[str, Any]


CORPUS: list[Document] = [
    {
        "id": "egypt-aid-q2-2026",
        "title": "Egypt Humanitarian Response — Quarterly Impact Review (Q2 2026)",
        "source": "OCHA Egypt Situation Report No. 14",
        "region": "Egypt",
        "topic": "humanitarian aid impact funding beneficiaries sectors donor",
        "period": "Q2 2026 (Apr–Jun)",
        "text": (
            "Over the second quarter of 2026, UN-coordinated humanitarian assistance in "
            "Egypt reached an estimated 2.4 million people, up from 1.9 million in Q1. "
            "Food security and cash-based interventions accounted for the largest share of "
            "delivery, followed by health and protection services. Against a quarterly "
            "appeal of US$180 million, donors disbursed US$126 million, leaving the response "
            "68% funded. Independent post-distribution monitoring recorded a beneficiary "
            "satisfaction rate of 87% and a measurable improvement in the food consumption "
            "score across 74% of assisted households."
        ),
        "data": {
            "people_reached": 2_400_000,
            "people_reached_prev": 1_900_000,
            "appeal_usd": 180_000_000,
            "funded_usd": 126_000_000,
            "funded_pct": 68,
            "beneficiary_satisfaction_pct": 87,
            "sector_funding_usd": {
                "Food & Cash": 54_000_000,
                "Health": 28_000_000,
                "Protection": 19_000_000,
                "WASH": 14_000_000,
                "Education": 11_000_000,
            },
            "monthly_people_reached": {
                "Apr": 720_000,
                "May": 810_000,
                "Jun": 870_000,
            },
        },
    },
    {
        "id": "egypt-aid-q1-2026",
        "title": "Egypt Humanitarian Response — Quarterly Impact Review (Q1 2026)",
        "source": "OCHA Egypt Situation Report No. 13",
        "region": "Egypt",
        "topic": "humanitarian aid impact funding beneficiaries sectors donor",
        "period": "Q1 2026 (Jan–Mar)",
        "text": (
            "In the first quarter of 2026, UN-coordinated assistance in Egypt reached "
            "1.9 million people against a quarterly appeal of US$165 million, of which "
            "US$103 million was received (62% funded). Food and cash assistance remained "
            "the dominant modality."
        ),
        "data": {
            "people_reached": 1_900_000,
            "appeal_usd": 165_000_000,
            "funded_usd": 103_000_000,
            "funded_pct": 62,
        },
    },
    {
        "id": "iran-displaced-2026",
        "title": "Iran Inter-Agency Situation Report — Displaced Families (July 2026)",
        "source": "UNHCR/OCHA Iran Situation Report No. 9",
        "region": "Iran",
        "topic": "displaced families resources shelter services assistance vulnerable",
        "period": "As of July 2026",
        "text": (
            "The latest inter-agency situation report estimates 480,000 internally displaced "
            "people across five provinces in Iran. Available assistance for displaced "
            "families includes 62 active shelter sites, 38 mobile health clinics, cash "
            "assistance covering 71% of registered families, and 24 child-friendly and "
            "protection spaces. Families can access services through 14 registration hubs; "
            "the most acute reported needs are shelter, safe drinking water, and winterization "
            "support. Referral pathways for gender-based violence and unaccompanied minors "
            "are operational in all five provinces."
        ),
        "data": {
            "displaced_people": 480_000,
            "registered_families": 96_000,
            "cash_coverage_pct": 71,
            "resources": {
                "Shelter sites": 62,
                "Mobile health clinics": 38,
                "Registration hubs": 14,
                "Protection/child spaces": 24,
                "Water distribution points": 45,
            },
            "displaced_by_province": {
                "Sistan-Baluchestan": 168_000,
                "Kerman": 96_000,
                "Hormozgan": 84_000,
                "Khuzestan": 78_000,
                "Fars": 54_000,
            },
            "top_needs": ["Shelter", "Safe drinking water", "Winterization", "Health care"],
        },
    },
    {
        "id": "canada-wash-2026",
        "title": "Canada — Water Scarcity & Sanitation Needs Assessment (2026)",
        "source": "UN-Water / WHO-UNICEF JMP Country Brief",
        "region": "Canada",
        "topic": "water scarcity sanitation wash assessment access hygiene technical",
        "period": "2026 assessment cycle",
        "text": (
            "While Canada has high national coverage of water and sanitation services, the "
            "assessment highlights persistent gaps in remote and Indigenous communities. "
            "National access to safely managed drinking water stands at 94%, but drops to "
            "72% in surveyed remote communities. Long-term drinking water advisories remained "
            "in effect in 28 communities at the time of assessment. Access to safely managed "
            "sanitation is 87% nationally. Seasonal water stress affects the Prairie provinces, "
            "with a measured water-stress index of 41 on a 0–100 scale."
        ),
        "data": {
            "water_access_national_pct": 94,
            "water_access_remote_pct": 72,
            "sanitation_access_pct": 87,
            "long_term_advisories": 28,
            "water_stress_index": 41,
            "access_by_region": {
                "Urban": 99,
                "Rural": 91,
                "Remote/Northern": 72,
                "Indigenous communities": 68,
            },
            "advisories_trend": {
                "2022": 51,
                "2023": 44,
                "2024": 38,
                "2025": 33,
                "2026": 28,
            },
        },
    },
    {
        "id": "sudan-aid-2026",
        "title": "Sudan Humanitarian Response — Quarterly Snapshot (Q2 2026)",
        "source": "OCHA Sudan Situation Report No. 21",
        "region": "Sudan",
        "topic": "humanitarian aid impact funding beneficiaries displaced",
        "period": "Q2 2026",
        "text": (
            "UN-coordinated assistance in Sudan reached 6.1 million people in Q2 2026 against "
            "an appeal of US$1.2 billion, which was 41% funded. Displacement continued to rise."
        ),
        "data": {
            "people_reached": 6_100_000,
            "appeal_usd": 1_200_000_000,
            "funded_pct": 41,
        },
    },
]
