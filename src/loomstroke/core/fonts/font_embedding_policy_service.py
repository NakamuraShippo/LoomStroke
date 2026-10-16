"""Permission facts + user attestation -> export / package policy (CT-25 F3).

``fsType`` evidence is combined with an explicit user attestation; the
outcome is always one of the stable options the dialogs show.  Unknown
never becomes allowed on its own.
"""

from __future__ import annotations

from dataclasses import replace
from typing import Literal

from .font_permission_assessment import FontPermissionAssessment

__all__ = ["FontEmbeddingPolicyService"]

ExportChoice = Literal["embed_subset", "embed_full", "outline", "rasterize", "refuse"]
Attestation = Literal["permitted", "denied"] | None


class FontEmbeddingPolicyService:
    def assess(
        self, assessment: FontPermissionAssessment, *, user_attestation: Attestation = None
    ) -> FontPermissionAssessment:
        """Apply the user's explicit attestation to the package-copy decision."""
        if assessment.embedding == "restricted":
            return replace(
                assessment,
                package_copy="denied",
                evidence_codes=(*assessment.evidence_codes, "restricted_overrides_attestation"),
            )
        if user_attestation == "permitted":
            return replace(
                assessment,
                package_copy="allowed",
                evidence_codes=(*assessment.evidence_codes, "user_attested_permitted"),
            )
        if user_attestation == "denied":
            return replace(
                assessment,
                package_copy="denied",
                evidence_codes=(*assessment.evidence_codes, "user_attested_denied"),
            )
        return assessment

    @staticmethod
    def export_choices(assessment: FontPermissionAssessment) -> tuple[ExportChoice, ...]:
        """Options an export dialog may offer for this font, most preferred first."""
        choices: list[ExportChoice] = []
        if assessment.may_embed_subset:
            choices.append("embed_subset")
        elif assessment.may_embed and assessment.no_subsetting:
            choices.append("embed_full")
        choices.extend(("outline", "rasterize"))
        if assessment.embedding in {"restricted", "unknown"}:
            choices = ["outline", "rasterize", "refuse"]
        return tuple(choices)

    @staticmethod
    def may_collect(assessment: FontPermissionAssessment) -> bool:
        return assessment.package_copy == "allowed"
