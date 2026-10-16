"""Linked assets / package (CT-18): resource identity, status, place/relink plans, packaging.

Pure Python, one class per module.  UI adapters live in ``loomstroke.ui`` and
``loomstroke.plugins.links_panel``.
"""

from __future__ import annotations

from .media_type_table import MediaTypeTable
from .package_asset_entry import PackageAction, PackageAssetEntry
from .package_manifest import PackageManifest
from .package_plan import PackagePlan
from .package_result import PackageResult
from .place_resource_plan import PlaceResourcePlan
from .project_package_planner import ProjectPackagePlanner
from .project_package_writer import ProjectPackageWriter
from .relink_plan import RelinkPlan
from .resource_consumer import ResourceConsumer
from .resource_consumer_index import ResourceConsumerIndex
from .resource_content_hasher import ResourceContentHasher
from .resource_layer_metadata_codec import ResourceLayerMetadataCodec
from .resource_locator import LocatorBase, ResourceLocator
from .resource_locator_resolver import ResourceLocatorResolver
from .resource_place_planner import ResourcePlacePlanner
from .resource_record import ResourceKind, ResourceMode, ResourceRecord
from .resource_relink_planner import ResourceRelinkPlanner
from .resource_status import ResourceState, ResourceStatus
from .resource_status_service import ResourceStatusService

__all__ = [
    "LocatorBase",
    "MediaTypeTable",
    "PackageAction",
    "PackageAssetEntry",
    "PackageManifest",
    "PackagePlan",
    "PackageResult",
    "PlaceResourcePlan",
    "ProjectPackagePlanner",
    "ProjectPackageWriter",
    "RelinkPlan",
    "ResourceConsumer",
    "ResourceConsumerIndex",
    "ResourceContentHasher",
    "ResourceKind",
    "ResourceLayerMetadataCodec",
    "ResourceLocator",
    "ResourceLocatorResolver",
    "ResourceMode",
    "ResourcePlacePlanner",
    "ResourceRecord",
    "ResourceRelinkPlanner",
    "ResourceState",
    "ResourceStatus",
    "ResourceStatusService",
]
