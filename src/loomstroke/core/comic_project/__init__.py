"""Comic project: story / dialogue / production management (CT-17, spec 17).

Pure, Qt-free domain.  One class per module:

- ids / ordering: :mod:`story_id_factory`, :mod:`story_order_key`
- models: :mod:`comic_project`, :mod:`story_chapter`, :mod:`story_scene`,
  :mod:`comic_page_entry`, :mod:`comic_panel_entry`, :mod:`dialogue_entry`,
  :mod:`production_task`, :mod:`document_link`
- aggregate: :mod:`comic_project_snapshot`, :mod:`comic_project_codec`,
  :mod:`comic_project_mutator`
- persistence: :mod:`comic_project_store` (atomic sidecar JSON),
  :mod:`revision_conflict_error`, :mod:`comic_project_service`
- import / export: :mod:`script_import_row`, :mod:`script_import_preview`,
  :mod:`comic_script_importer`, :mod:`comic_script_exporter`
- canvas bridge: :mod:`link_report`, :mod:`comic_document_link_resolver`,
  :mod:`dialogue_apply_plan`, :mod:`dialogue_sync_plan`,
  :mod:`dialogue_apply_planner`
- projection: :mod:`production_projection_builder`
"""

from .comic_document_link_resolver import ComicDocumentLinkResolver
from .comic_page_entry import ComicPageEntry
from .comic_panel_entry import ComicPanelEntry
from .comic_project import ComicProject
from .comic_project_codec import ComicProjectCodec
from .comic_project_mutator import ComicProjectMutator
from .comic_project_service import ComicProjectService
from .comic_project_snapshot import ComicProjectSnapshot
from .comic_project_store import ComicProjectStore
from .comic_script_exporter import ComicScriptExporter
from .comic_script_importer import ComicScriptImporter
from .dialogue_apply_plan import DialogueApplyPlan
from .dialogue_apply_planner import DialogueApplyPlanner
from .dialogue_entry import DialogueEntry
from .dialogue_sync_plan import DialogueSyncPlan
from .document_link import DocumentLink
from .link_report import LinkReport
from .production_projection_builder import ProductionProjectionBuilder
from .production_task import ProductionTask
from .revision_conflict_error import RevisionConflictError
from .script_import_preview import ScriptImportPreview
from .script_import_row import ScriptImportRow
from .story_chapter import StoryChapter
from .story_id_factory import StoryIdFactory
from .story_order_key import StoryOrderKey
from .story_scene import StoryScene

__all__ = [
    "ComicDocumentLinkResolver",
    "ComicPageEntry",
    "ComicPanelEntry",
    "ComicProject",
    "ComicProjectCodec",
    "ComicProjectMutator",
    "ComicProjectService",
    "ComicProjectSnapshot",
    "ComicProjectStore",
    "ComicScriptExporter",
    "ComicScriptImporter",
    "DialogueApplyPlan",
    "DialogueApplyPlanner",
    "DialogueEntry",
    "DialogueSyncPlan",
    "DocumentLink",
    "LinkReport",
    "ProductionProjectionBuilder",
    "ProductionTask",
    "RevisionConflictError",
    "ScriptImportPreview",
    "ScriptImportRow",
    "StoryChapter",
    "StoryIdFactory",
    "StoryOrderKey",
    "StoryScene",
]
