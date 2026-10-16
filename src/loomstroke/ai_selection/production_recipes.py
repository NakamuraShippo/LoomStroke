"""Lightweight SAM3 ProductionRecipe descriptors."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

SAM3_PRODUCTION_RECIPE_CATALOG_SCHEMA_VERSION = "sam3.production_recipe_catalog.v1"

SAM3_PRODUCTION_RECIPE_IDS = (
    "sam3_extract_masks",
    "sam3_split_character_layers",
    "sam3_prepare_inpaint_package",
    "sam3_detect_speech_bubbles",
)

_COMIC_QUALITY_PROFILES = ("comic_draft_preview", "comic_publish_preview")


@dataclass(frozen=True, slots=True)
class Sam3ProductionRecipeDescriptor:
    """Immutable descriptor for one SAM3 ProductionRecipe candidate."""

    recipe_id: str
    display_name: str
    description: str
    export_profiles: tuple[str, ...]
    estimated_artifacts: tuple[str, ...]
    side_effects: tuple[str, ...]
    domain: str = "comic_page"
    quality_profiles: tuple[str, ...] = _COMIC_QUALITY_PROFILES
    requires_external_services: bool = False
    requires_sam3_worker: bool = True
    experimental: bool = True
    version: str = "1.0.0"

    def to_dict(self) -> dict[str, Any]:
        return {
            "schemaVersion": SAM3_PRODUCTION_RECIPE_CATALOG_SCHEMA_VERSION,
            "recipeId": self.recipe_id,
            "domain": self.domain,
            "version": self.version,
            "displayName": self.display_name,
            "description": self.description,
            "qualityProfiles": list(self.quality_profiles),
            "exportProfiles": list(self.export_profiles),
            "requiresExternalServices": self.requires_external_services,
            "requiresSam3Worker": self.requires_sam3_worker,
            "sideEffects": list(self.side_effects),
            "estimatedArtifacts": list(self.estimated_artifacts),
            "experimental": self.experimental,
        }

    def to_runner_recipe(self) -> dict[str, Any]:
        return {
            "recipeId": self.recipe_id,
            "domain": self.domain,
            "version": self.version,
            "displayName": self.display_name,
            "description": self.description,
            "modes": ["dry_run", "submit"],
            "defaultQualityProfile": self.quality_profiles[0],
            "qualityProfiles": list(self.quality_profiles),
            "supportedQualityProfiles": list(self.quality_profiles),
            "supportedExportProfiles": list(self.export_profiles),
            "requiresExternalServices": self.requires_external_services,
            "supportsRework": True,
            "supportsCandidateGeneration": True,
            "experimental": self.experimental,
        }


def sam3_production_recipe_descriptors() -> tuple[Sam3ProductionRecipeDescriptor, ...]:
    """Return the SAM3 ProductionRecipe descriptors in stable catalog order."""

    return (
        Sam3ProductionRecipeDescriptor(
            recipe_id="sam3_extract_masks",
            display_name="SAM3 Extract Masks",
            description="Extract comic-page mask candidates and package records with SAM3.",
            export_profiles=("sam3_mask_package",),
            estimated_artifacts=(
                "mask_package_json",
                "candidate_scorecard_json",
                "lineage_json",
            ),
            side_effects=("asset_registration", "production_record_write"),
        ),
        Sam3ProductionRecipeDescriptor(
            recipe_id="sam3_split_character_layers",
            display_name="SAM3 Split Character Layers",
            description="Prepare character/background mask candidates for layered comic output.",
            export_profiles=("sam3_layered_character_package",),
            estimated_artifacts=(
                "layered_character_package_json",
                "candidate_scorecard_json",
                "lineage_json",
            ),
            side_effects=("asset_registration", "production_record_write"),
        ),
        Sam3ProductionRecipeDescriptor(
            recipe_id="sam3_prepare_inpaint_package",
            display_name="SAM3 Prepare Inpaint Package",
            description="Prepare inpaint/protect mask package records for comic page rework.",
            export_profiles=("sam3_inpaint_package",),
            estimated_artifacts=(
                "inpaint_package_json",
                "protect_mask_json",
                "qa_report_json",
                "lineage_json",
            ),
            side_effects=("asset_registration", "production_record_write"),
        ),
        Sam3ProductionRecipeDescriptor(
            recipe_id="sam3_detect_speech_bubbles",
            display_name="SAM3 Detect Speech Bubbles",
            description="Detect speech-bubble mask candidates for text protection workflows.",
            export_profiles=("sam3_mask_package",),
            estimated_artifacts=(
                "speech_bubble_mask_package_json",
                "candidate_scorecard_json",
                "qa_report_json",
                "lineage_json",
            ),
            side_effects=("asset_registration", "production_record_write"),
        ),
    )


def sam3_production_recipe_catalog_payload() -> dict[str, Any]:
    """Return a JSON-safe SAM3 ProductionRecipe catalog payload."""

    descriptors = sam3_production_recipe_descriptors()
    return {
        "schemaVersion": SAM3_PRODUCTION_RECIPE_CATALOG_SCHEMA_VERSION,
        "recipes": [descriptor.to_dict() for descriptor in descriptors],
    }


__all__ = [
    "SAM3_PRODUCTION_RECIPE_CATALOG_SCHEMA_VERSION",
    "SAM3_PRODUCTION_RECIPE_IDS",
    "Sam3ProductionRecipeDescriptor",
    "sam3_production_recipe_catalog_payload",
    "sam3_production_recipe_descriptors",
]
