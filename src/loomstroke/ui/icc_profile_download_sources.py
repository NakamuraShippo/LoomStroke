"""Official ICC review pages, verified on 2026-10-10 (never file URLs)."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class IccProfileDownloadSource:
    """A printing condition and its provider's terms/information page."""

    source_id: str
    region_source: str
    profile_name: str
    provider: str
    url: str


ICC_PROFILE_DOWNLOAD_SOURCES = (
    IccProfileDownloadSource(
        "japan-color-2011",
        "Japan",
        "Japan Color 2011 Coated",
        "JPMA / X-Rite",
        "https://japancolor.jp/icc.html",
    ),
    IccProfileDownloadSource(
        "gracol-2013",
        "North America",
        "GRACoL 2013 CRPC6",
        "Idealliance / X-Rite (ICC)",
        "https://registry.color.org/profile-registry/GRACoL2013_CRPC6",
    ),
    IccProfileDownloadSource(
        "swop-2013",
        "North America",
        "SWOP 2013 C3 CRPC5",
        "Idealliance / X-Rite (ICC)",
        "https://registry.color.org/profile-registry/SWOP2013C3_CRPC5",
    ),
    IccProfileDownloadSource(
        "pso-coated-v3",
        "Europe",
        "PSO Coated v3 / FOGRA51",
        "ECI (ICC)",
        "https://registry.color.org/profile-registry/PSOcoated_v3",
    ),
    IccProfileDownloadSource(
        "pso-uncoated-v3",
        "Europe",
        "PSO Uncoated v3 / FOGRA52",
        "ECI (ICC)",
        "https://registry.color.org/profile-registry/PSOuncoated_v3_FOGRA52",
    ),
    IccProfileDownloadSource(
        "aptec-coated-2025",
        "Hong Kong / Asia",
        "APTEC Offset Coated LinearCTV 2025",
        "APTEC (ICC)",
        "https://registry.color.org/profile-registry/APTEC_Offset_Coated_LinearCTV_2025",
    ),
    IccProfileDownloadSource(
        "aptec-uncoated-2025",
        "Hong Kong / Asia",
        "APTEC Offset Uncoated LinearCTV 2025",
        "APTEC (ICC)",
        "https://registry.color.org/profile-registry/APTEC_Offset_Uncoated_LinearCTV_2025",
    ),
)
