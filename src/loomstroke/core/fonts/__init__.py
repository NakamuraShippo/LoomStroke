"""Font portability (CT-25, spec 25): inventory, identity, permissions, substitution.

Pure Python (no Qt) — the Qt font database and glyph outlining live in
``ui``.  One class per module:

- :mod:`font_request` / :mod:`font_identity` / :mod:`font_resolution_result`
- :mod:`font_usage` / :mod:`font_usage_inventory` / :mod:`font_inventory_service`
- :mod:`font_catalog` (Protocol) / :mod:`font_identity_service`
- :mod:`font_permission_assessment` / :mod:`opentype_permission_reader` /
  :mod:`font_embedding_policy_service`
- :mod:`font_substitution_plan` / :mod:`font_substitution_planner`
- :mod:`outlined_text_payload`
"""

from .font_catalog import FontCatalog
from .font_embedding_policy_service import FontEmbeddingPolicyService
from .font_identity import FontIdentity
from .font_identity_service import FontIdentityService
from .font_inventory_service import FontInventoryService
from .font_permission_assessment import FontPermissionAssessment
from .font_request import FontRequest
from .font_resolution_result import FontResolutionResult
from .font_substitution_plan import FontSubstitutionPlan
from .font_substitution_planner import FontSubstitutionPlanner
from .font_usage import FontUsage
from .font_usage_inventory import FontUsageInventory
from .opentype_permission_reader import OpenTypePermissionReader
from .outlined_text_payload import OutlinedTextPayload

__all__ = [
    "FontCatalog",
    "FontEmbeddingPolicyService",
    "FontIdentity",
    "FontIdentityService",
    "FontInventoryService",
    "FontPermissionAssessment",
    "FontRequest",
    "FontResolutionResult",
    "FontSubstitutionPlan",
    "FontSubstitutionPlanner",
    "FontUsage",
    "FontUsageInventory",
    "OpenTypePermissionReader",
    "OutlinedTextPayload",
]
