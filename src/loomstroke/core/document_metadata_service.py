"""Metadata ownership for :mod:`loomstroke.core.document_service`."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, replace
from typing import TYPE_CHECKING, Any, cast

from PySide6.QtGui import QColor

from ..host_api import CanvasSpec as HostCanvasSpec
from ..host_api import SaveOptions
from ..io.file_io import ColorProfile
from ..io.layer_io import (
    DOCUMENT_FORMAT_VERSION,
    CanvasSpec,
    DocumentMetadata,
    LayerSnapshot,
    PageSnapshot,
)
from .book_layout_service import BookLayoutService
from .book_spread_layout import BookLayout
from .canvas import Canvas as LegacyCanvas
from .canvas_service_simple import SimpleCanvasService
from .comic_metadata_codec import BOOK_METADATA_NAMESPACE, MASTERS_METADATA_NAMESPACE
from .comic_metadata_codec import merge_book_metadata as _codec_merge_book_metadata
from .comic_metadata_codec import merge_masters_metadata as _codec_merge_masters_metadata
from .comic_metadata_codec import read_book_metadata as _codec_read_book_metadata
from .comic_metadata_codec import read_masters_metadata as _codec_read_masters_metadata
from .events import DocumentChangeEvent, Events

if TYPE_CHECKING:
    from .document_service import DocumentServiceImpl


@dataclass(slots=True)
class _CanvasMetadata:
    width: int
    height: int
    background_color: tuple[int, int, int, int]
    dpi: tuple[float, float]


@dataclass(slots=True)
class _DocumentState:
    metadata: DocumentMetadata
    pages: list[PageSnapshot]
    active_page_id: str | None


@dataclass(frozen=True, slots=True)
class _BookNamespaceSnapshot:
    """Opaque, namespace-scoped snapshot of the book-layout comic namespace
    (CT-06 Phase B3-c2a).

    Captures only ``plugins`` / book-namespace *presence* and the book
    namespace's own value -- never the whole metadata root -- so that
    restoring a snapshot later never reverts a sibling plugin namespace or
    an unrelated root key that changed after the snapshot was taken.
    """

    plugins_present: bool
    namespace_present: bool
    namespace_value: dict[str, Any] | None


@dataclass(frozen=True, slots=True)
class _BookNamespaceTransition:
    """Before/after namespace snapshots fixed by one successful do (CT-06
    Phase B3-c2a). ``after`` is never recomputed on redo -- it is replayed
    verbatim via :meth:`DocumentMetadataService._restore_book_namespace`.
    """

    before: _BookNamespaceSnapshot
    after: _BookNamespaceSnapshot


class DocumentMetadataService:
    """Owns document metadata caches and metadata/page snapshot shaping."""

    def __init__(self, owner: DocumentServiceImpl) -> None:
        self._owner = owner
        self._document_state: _DocumentState | None = None
        self._loaded_metadata: DocumentMetadata | None = None

    @property
    def document_state(self) -> _DocumentState | None:
        return self._document_state

    @document_state.setter
    def document_state(self, value: _DocumentState | None) -> None:
        self._document_state = value

    @property
    def loaded_metadata(self) -> DocumentMetadata | None:
        return self._loaded_metadata

    @loaded_metadata.setter
    def loaded_metadata(self, value: DocumentMetadata | None) -> None:
        self._loaded_metadata = value

    def reset_document_state(self) -> None:
        self._document_state = None
        self._loaded_metadata = None
        self.adopt_loaded_color_profile(None)

        owner = self._owner
        if owner._page_service is not None:  # noqa: SLF001
            try:
                meta = self.extract_canvas_metadata()
                canvas_spec = HostCanvasSpec(
                    width=meta.width,
                    height=meta.height,
                    dpi=meta.dpi,
                    background_color=meta.background_color,
                )
                layers = owner._capture_layers()  # noqa: SLF001
                owner._page_service.reset_pages(layers, canvas_spec)  # noqa: SLF001
            except Exception as exc:
                owner._logger.debug("Failed to reset pages: %s", exc)  # noqa: SLF001

    def prepare_save_metadata(self, opts: SaveOptions) -> DocumentMetadata:
        metadata = self.build_document_metadata(opts)

        owner = self._owner
        if owner._page_service is not None and self._loaded_metadata is not None:  # noqa: SLF001
            metadata = self.merge_loaded_metadata(metadata, self._loaded_metadata, opts)
        elif self._document_state is not None:
            metadata = self.merge_loaded_metadata(metadata, self._document_state.metadata, opts)

        metadata.ui_layout = owner._serialize_ui_layout(opts)  # noqa: SLF001
        metadata.ensure_timestamps()
        return metadata

    def prepare_save_pages(
        self,
        metadata: DocumentMetadata,
        layers: Sequence[LayerSnapshot],
    ) -> tuple[list[PageSnapshot], str]:
        owner = self._owner
        if owner._page_service is not None:  # noqa: SLF001
            owner._page_service.set_document_canvas(
                self.host_canvas_spec_from_metadata(metadata)
            )  # noqa: SLF001
            return owner._page_service.build_pages_for_save(layers, metadata)  # noqa: SLF001

        return self.build_pages_for_save(layers, metadata)

    def reset_saved_document_state(
        self,
        metadata: DocumentMetadata,
        pages: list[PageSnapshot],
        active_page_id: str | None,
    ) -> None:
        owner = self._owner
        owner._reset_history_state(reason="save")  # noqa: SLF001

        if owner._page_service is not None:  # noqa: SLF001
            self._loaded_metadata = metadata
            owner._page_service.mark_all_clean()  # noqa: SLF001
            return

        self._document_state = _DocumentState(
            metadata=metadata,
            pages=pages,
            active_page_id=active_page_id,
        )

    # ------------------------------------------------------------------
    # Comic Tools book-layout metadata (CT-06 Phase B3-c1)
    #
    # ``apply_book_metadata_update()`` is the only sanctioned way to change
    # ``metadata["plugins"]["loomstroke.comictools.book"]`` on the live
    # document. Never route a Comic Tools update through
    # ``SaveOptions.metadata={"plugins": ...}``: ``merge_loaded_metadata()``
    # replaces the whole top-level ``"plugins"`` key with a naive
    # ``dict.update()`` and would silently drop sibling plugin namespaces
    # (including the page-level comic namespace) if a raw ``"plugins"`` key
    # ever reached it that way. This method and ``merge_loaded_metadata()``
    # are unchanged by this contract; only future callers must honour it.

    def read_book_metadata(self) -> dict[str, Any]:
        """Defensive copy of the current book-layout comic namespace.

        Core-internal only (not HostAPI). Delegates entirely to
        :func:`comic_metadata_codec.read_book_metadata`; a malformed
        container raises ``ValueError`` rather than silently falling back
        to a default — that display-time fallback is
        ``BookLayoutService``'s job, not this raw accessor's.
        """
        return _codec_read_book_metadata(self._metadata_root())

    def resolve_book_layout(self) -> BookLayout:
        """Validated book layout for display (CT-06 B4-c4).

        Unlike :meth:`read_book_metadata` this never raises: a malformed or
        absent container falls back to the default layout with a warning,
        because the spread viewport has to draw *something* (spec §13).
        """
        return BookLayoutService().resolve_layout(self._metadata_root())

    def _metadata_root(self) -> Mapping[str, Any] | None:
        if self._loaded_metadata is not None:
            return self._loaded_metadata.metadata
        if self._document_state is not None:
            return self._document_state.metadata.metadata
        return None

    def apply_book_metadata_update(self, updates: Mapping[str, Any]) -> bool:
        """Copy-on-write update of the book-layout comic namespace.

        Reuses :func:`comic_metadata_codec.merge_book_metadata` for the
        actual merge (fail-closed on malformed containers via the codec's
        own ``ValueError``, defensive copies, sibling-namespace
        preservation) — this method only owns *where* the resulting
        metadata root is stored and *when* ``DOC_CHANGED`` fires. A no-op
        update (empty ``updates`` against an absent namespace, or a merge
        that produces a semantically identical root) returns ``False``
        without replacing the owner object or emitting an event.

        CT-06 Phase B3-c2a: delegates to
        :meth:`_apply_book_metadata_transition`, which additionally fixes
        namespace-scoped before/after snapshots for
        ``SetBookLayoutCommand``. This method's own signature and contract
        (bool return, no-op/malformed semantics, single ``DOC_CHANGED``)
        are unchanged.
        """
        return self._apply_book_metadata_transition(updates) is not None

    # ------------------------------------------------------------------
    # Comic Tools master definitions (CT-07 T3a).  Same transition/restore
    # machinery as the book namespace, parameterized by namespace — the
    # generic helpers below are the single implementation both delegate to
    # (AGENTS: never duplicate the same rule in two places).

    def read_masters_metadata(self) -> dict[str, Any]:
        """Defensive copy of the masters namespace (core-internal)."""
        return _codec_read_masters_metadata(self._metadata_root())

    def apply_masters_metadata_update(self, updates: Mapping[str, Any]) -> bool:
        return self._apply_masters_metadata_transition(updates) is not None

    def _apply_masters_metadata_transition(
        self, updates: Mapping[str, Any]
    ) -> _BookNamespaceTransition | None:
        return self._apply_namespace_transition(
            updates,
            namespace_key=MASTERS_METADATA_NAMESPACE,
            read_fn=_codec_read_masters_metadata,
            merge_fn=_codec_merge_masters_metadata,
            change_type="book.masters_changed",
        )

    def _restore_masters_namespace(self, snapshot: _BookNamespaceSnapshot) -> bool:
        return self._restore_namespace(
            snapshot,
            namespace_key=MASTERS_METADATA_NAMESPACE,
            read_fn=_codec_read_masters_metadata,
            change_type="book.masters_changed",
        )

    def _namespace_snapshot_from_root(
        self,
        root: Mapping[str, Any],
        namespace_key: str,
        read_fn: Any,
    ) -> _BookNamespaceSnapshot:
        """Generic namespace-scoped snapshot (B3-c2a semantics)."""
        if not isinstance(root, Mapping):
            raise ValueError("document metadata root must be a Mapping")
        plugins_present = "plugins" in root
        if plugins_present and not isinstance(root["plugins"], Mapping):
            raise ValueError("metadata['plugins'] must be a Mapping")
        namespace_present = plugins_present and namespace_key in root["plugins"]
        if namespace_present and not isinstance(root["plugins"][namespace_key], Mapping):
            raise ValueError(f"metadata['plugins'][{namespace_key!r}] must be a Mapping")
        namespace_value = read_fn(root) if namespace_present else None
        return _BookNamespaceSnapshot(
            plugins_present=plugins_present,
            namespace_present=namespace_present,
            namespace_value=namespace_value,
        )

    def _apply_namespace_transition(
        self,
        updates: Mapping[str, Any],
        *,
        namespace_key: str,
        read_fn: Any,
        merge_fn: Any,
        change_type: str,
    ) -> _BookNamespaceTransition | None:
        """Generic atomic do-side transition (B3-c2a semantics)."""
        current_metadata, owner_kind = self._current_book_metadata_owner()
        current_root = current_metadata.metadata

        before = self._namespace_snapshot_from_root(current_root, namespace_key, read_fn)
        new_root = merge_fn(current_root, updates)
        if new_root == current_root:
            return None
        after = self._namespace_snapshot_from_root(new_root, namespace_key, read_fn)

        new_metadata = replace(current_metadata, metadata=new_root)
        self._commit_book_metadata_owner(owner_kind, new_metadata)
        self._emit_comic_metadata_changed(change_type)
        return _BookNamespaceTransition(before=before, after=after)

    def _restore_namespace(
        self,
        snapshot: _BookNamespaceSnapshot,
        *,
        namespace_key: str,
        read_fn: Any,
        change_type: str,
    ) -> bool:
        """Generic namespace-scoped Undo/Redo restore (B3-c2a semantics)."""
        current_metadata, owner_kind = self._current_book_metadata_owner()
        root = current_metadata.metadata
        current_snapshot = self._namespace_snapshot_from_root(root, namespace_key, read_fn)
        plugins_now_present = current_snapshot.plugins_present

        new_plugins: dict[str, Any] = dict(root["plugins"]) if plugins_now_present else {}
        if snapshot.namespace_present:
            new_plugins[namespace_key] = read_fn(
                {"plugins": {namespace_key: snapshot.namespace_value}}
            )
        else:
            new_plugins.pop(namespace_key, None)

        keep_plugins = bool(new_plugins) or snapshot.plugins_present
        new_root: dict[str, Any] = dict(root)
        if keep_plugins:
            new_root["plugins"] = new_plugins
        else:
            new_root.pop("plugins", None)

        if new_root == root:
            return True  # idempotent success without mutation

        new_metadata = replace(current_metadata, metadata=new_root)
        self._commit_book_metadata_owner(owner_kind, new_metadata)
        self._emit_comic_metadata_changed(change_type)
        return True

    def _emit_comic_metadata_changed(self, change_type: str) -> None:
        self._owner._event_bus.emit(  # noqa: SLF001
            Events.DOC_CHANGED,
            DocumentChangeEvent(change_type=change_type, source="DocumentMetadataService"),
        )

    def _book_namespace_snapshot_from_root(self, root: Mapping[str, Any]) -> _BookNamespaceSnapshot:
        """Namespace-scoped, read-only snapshot of ``root`` (CT-06 B3-c2a).

        Validates ``root`` (and, when present, ``plugins`` / the book
        namespace) is a ``Mapping`` and fails closed with ``ValueError``
        otherwise — a malformed container is never silently treated as
        absent or repaired into ``{}``. Distinguishes "namespace absent"
        from "namespace present but empty" via ``namespace_present``, and
        likewise for ``plugins_present``. ``namespace_value`` reuses the
        existing public :func:`comic_metadata_codec.read_book_metadata`
        for its defensive copy — no private codec helper is imported.
        """
        return self._namespace_snapshot_from_root(
            root, BOOK_METADATA_NAMESPACE, _codec_read_book_metadata
        )

    def _capture_book_namespace_snapshot(self) -> _BookNamespaceSnapshot:
        """Namespace-scoped snapshot of the *current* owner's book namespace."""
        current_metadata, _owner_kind = self._current_book_metadata_owner()
        return self._book_namespace_snapshot_from_root(current_metadata.metadata)

    def _apply_book_metadata_transition(
        self, updates: Mapping[str, Any]
    ) -> _BookNamespaceTransition | None:
        """Atomic do-side transition backing ``SetBookLayoutCommand`` (B3-c2a).

        Single internal operation: resolve owner -> validate root ->
        snapshot ``before`` -> merge -> no-op check -> snapshot ``after``
        -> commit the owner once -> emit ``DOC_CHANGED`` once -> return.
        ``before``/``after`` are derived from the exact root that gets
        committed and are never re-read after the event fires, so a
        synchronous ``DOC_CHANGED`` subscriber can never observe (or
        cause) a partially-applied state.

        Returns ``None`` for a semantic no-op (owner untouched, no
        event). Raises ``ValueError`` for a malformed container (owner
        untouched, no event) — the same fail-closed contract
        :func:`comic_metadata_codec.merge_book_metadata` already applies.
        """
        return self._apply_namespace_transition(
            updates,
            namespace_key=BOOK_METADATA_NAMESPACE,
            read_fn=_codec_read_book_metadata,
            merge_fn=_codec_merge_book_metadata,
            change_type="book.layout_changed",
        )

    def _restore_book_namespace(self, snapshot: _BookNamespaceSnapshot) -> bool:
        """Namespace-scoped Undo/Redo restore (CT-06 Phase B3-c2a).

        Unlike ``apply_book_metadata_update()``'s return value ("did this
        call change anything"), this ``bool`` means "did the history
        operation apply cleanly": restoring to a state that already
        matches the live root is a successful no-op (``True``), not a
        failure — ``PageCommandService`` still moves the entry between
        its undo/redo stacks normally in that case.

        Always resolves the *current* lifecycle owner via
        ``_current_book_metadata_owner()``, never a stored container
        reference, so a normal save/load that replaced the owner object
        since ``snapshot`` was captured is never silently undone by this
        call — only the book namespace subtree is touched; every sibling
        plugin namespace and unrelated root key on the *current* root is
        carried forward untouched. Fails closed (``ValueError``, owner
        unchanged) if the current root/``plugins``/book namespace is
        malformed.

        ``plugins`` key retention rule: kept whenever the namespace-
        stripped ``plugins`` mapping is non-empty (sibling namespaces
        exist now) OR ``snapshot.plugins_present`` was ``True`` (it
        existed — possibly empty — before the command's own do); dropped
        only when both are false, reproducing raw absence.
        """
        return self._restore_namespace(
            snapshot,
            namespace_key=BOOK_METADATA_NAMESPACE,
            read_fn=_codec_read_book_metadata,
            change_type="book.layout_changed",
        )

    def _commit_book_metadata_owner(self, owner_kind: str, new_metadata: DocumentMetadata) -> None:
        """Store ``new_metadata`` into the owner slot ``owner_kind`` named.

        Shared copy-on-write commit step for
        :meth:`_apply_book_metadata_transition` and
        :meth:`_restore_book_namespace` — matches
        ``_current_book_metadata_owner()``'s four owner cases exactly.
        """
        if owner_kind == "document_state":
            document_state = self._document_state
            assert document_state is not None
            self._document_state = replace(document_state, metadata=new_metadata)
        elif owner_kind == "new_document_state":
            # CT-06 Phase B3-c1 revision: a brand-new, never-saved document
            # with no PageService follows ``prepare_save_metadata()`` /
            # the load path's own contract — that pair only ever reads
            # ``_document_state`` when there is no PageService (never
            # ``_loaded_metadata``, see ``prepare_save_metadata()``'s
            # ``elif`` branch above). Storing into ``_loaded_metadata``
            # here would be invisible to the next save and silently drop
            # the just-applied book metadata. ``build_pages_for_save()``
            # already treats an empty ``pages`` list as "build the default
            # ``p_0001`` page", so no new page-construction logic is
            # needed here.
            self._document_state = _DocumentState(
                metadata=new_metadata,
                pages=[],
                active_page_id=None,
            )
        else:
            self._loaded_metadata = new_metadata

    def _emit_book_metadata_changed(self) -> None:
        self._emit_comic_metadata_changed("book.layout_changed")

    def _current_book_metadata_owner(self) -> tuple[DocumentMetadata, str]:
        """Resolve the live ``DocumentMetadata`` a book-layout update reads
        from and (on change) is replaced back into.

        Four owner cases (CT-06 Phase B3-c1 revision), matching exactly
        the branches ``prepare_save_metadata()`` and the load path
        (``DocumentPersistenceService.load()``) already use to decide
        between ``_loaded_metadata`` and ``_document_state``:

        - ``_loaded_metadata`` set: the normal PageService-connected path
          (post-load or post-save baseline).
        - ``_loaded_metadata`` absent, ``_document_state`` set: the legacy
          no-PageService path.
        - both absent, PageService connected: build a fresh
          ``DocumentMetadata`` from the live canvas (the same helper the
          save path already uses) rather than inventing a new pending
          field — it becomes the ``_loaded_metadata`` baseline
          ``prepare_save_metadata()``'s ``if owner._page_service is not
          None and self._loaded_metadata is not None`` branch already
          knows how to read.
        - both absent, no PageService: build the same fresh
          ``DocumentMetadata``, but it must become a new
          ``_document_state`` instead — ``prepare_save_metadata()``'s
          ``elif`` branch only ever reads ``_document_state`` on this
          path, never ``_loaded_metadata``. Storing into
          ``_loaded_metadata`` here would be invisible to the next save.
        """
        if self._loaded_metadata is not None:
            return self._loaded_metadata, "loaded_metadata"
        if self._document_state is not None:
            return self._document_state.metadata, "document_state"

        fresh_metadata = self.build_document_metadata(SaveOptions())
        if self._owner._page_service is not None:  # noqa: SLF001
            return fresh_metadata, "loaded_metadata"
        return fresh_metadata, "new_document_state"

    def build_document_metadata(self, options: SaveOptions) -> DocumentMetadata:
        meta = self.extract_canvas_metadata()
        metadata = DocumentMetadata(
            width=meta.width,
            height=meta.height,
            background_color=meta.background_color,
            dpi=meta.dpi,
            color_profile=self.resolve_color_profile(options),
            app_version="0.1.0-dev",
            format_version=DOCUMENT_FORMAT_VERSION,
            metadata=dict(options.metadata) if options.metadata else {},
        )

        if options.autosave and "autosave" not in metadata.metadata:
            metadata.metadata["autosave"] = True

        return metadata

    def merge_loaded_metadata(
        self,
        current: DocumentMetadata,
        loaded: DocumentMetadata,
        options: SaveOptions,
    ) -> DocumentMetadata:
        current.created = loaded.created or current.created

        merged_meta = dict(loaded.metadata) if loaded.metadata else {}
        if options.metadata:
            merged_meta.update(options.metadata)

        current.metadata = merged_meta
        if options.autosave and "autosave" not in current.metadata:
            current.metadata["autosave"] = True

        return current

    def build_pages_for_save(
        self,
        layers: Sequence[LayerSnapshot],
        metadata: DocumentMetadata,
    ) -> tuple[list[PageSnapshot], str]:
        if self._document_state is None:
            page = PageSnapshot(id="p_0001", name="Page 1", index=0, layers=list(layers))
            return [page], page.id

        active_id = self._document_state.active_page_id
        pages: list[PageSnapshot] = []
        active_found = False

        for page in self._document_state.pages:
            if page.id == active_id and not active_found:
                active_found = True
                canvas = page.canvas
                if canvas is not None:
                    canvas = self.canvas_spec_from_metadata(metadata)
                pages.append(
                    PageSnapshot(
                        id=page.id,
                        name=page.name,
                        index=page.index,
                        layers=list(layers),
                        canvas=canvas,
                        page_thumbnail=page.page_thumbnail,
                        metadata=dict(page.metadata) if isinstance(page.metadata, dict) else None,
                        view_state=(
                            dict(page.view_state) if isinstance(page.view_state, dict) else None
                        ),
                    )
                )
            else:
                pages.append(page)

        if not pages:
            page = PageSnapshot(id="p_0001", name="Page 1", index=0, layers=list(layers))
            return [page], page.id

        if not active_found:
            first = pages[0]
            pages[0] = PageSnapshot(
                id=first.id,
                name=first.name,
                index=first.index,
                layers=list(layers),
                canvas=first.canvas,
                page_thumbnail=first.page_thumbnail,
                metadata=dict(first.metadata) if isinstance(first.metadata, dict) else None,
                view_state=dict(first.view_state) if isinstance(first.view_state, dict) else None,
            )
            active_id = pages[0].id

        return pages, cast(str, active_id)

    def metadata_for_page(
        self,
        metadata: DocumentMetadata,
        page: PageSnapshot,
    ) -> DocumentMetadata:
        if page.canvas is None:
            return metadata

        return DocumentMetadata(
            width=page.canvas.width,
            height=page.canvas.height,
            background_color=page.canvas.background_color,
            dpi=page.canvas.dpi,
            color_profile=metadata.color_profile,
            metadata=dict(metadata.metadata),
            app_version=metadata.app_version,
            format_version=metadata.format_version,
            created=metadata.created,
            modified=metadata.modified,
            ui_layout=metadata.ui_layout,
        )

    @staticmethod
    def canvas_spec_from_metadata(metadata: DocumentMetadata) -> CanvasSpec:
        return CanvasSpec(
            width=metadata.width,
            height=metadata.height,
            background_color=metadata.background_color,
            dpi=metadata.dpi,
        )

    @staticmethod
    def host_canvas_spec_from_metadata(metadata: DocumentMetadata) -> HostCanvasSpec:
        return HostCanvasSpec(
            width=metadata.width,
            height=metadata.height,
            background_color=metadata.background_color,
            dpi=metadata.dpi,
        )

    def adopt_loaded_color_profile(self, metadata: DocumentMetadata | None) -> None:
        """Make the loaded document's ICC profile the canvas working profile (CT-09).

        A name-only placeholder (``sRGB`` without bytes, which is what a
        profile-less document saves) means *untagged*; only real bytes
        become the working profile.  ``None`` clears it (new document).
        """
        profile = metadata.color_profile if metadata is not None else None
        if profile is not None and not getattr(profile, "data", None):
            profile = None
        canvas = self._owner._canvas  # noqa: SLF001
        try:
            canvas.color_profile = profile
        except Exception:
            self._owner._logger.debug("canvas does not carry a color profile", exc_info=True)

    def resolve_color_profile(self, options: SaveOptions) -> ColorProfile | None:
        if not options.embed_icc:
            return None

        canvas_profile = getattr(self._owner._canvas, "color_profile", None)  # noqa: SLF001
        if isinstance(canvas_profile, ColorProfile):
            return canvas_profile

        if isinstance(canvas_profile, dict):
            try:
                raw_data = canvas_profile.get("data")
                if isinstance(raw_data, str):
                    raw_data = raw_data.encode("latin1")
                if isinstance(raw_data, bytearray):
                    profile_data: bytes | None = bytes(raw_data)
                elif isinstance(raw_data, bytes):
                    profile_data = raw_data
                else:
                    profile_data = None

                return ColorProfile(
                    name=str(canvas_profile.get("name", "Unknown")),
                    data=profile_data,
                    colorspace=str(canvas_profile.get("colorspace", "RGB")),
                )
            except Exception as exc:
                self._owner._logger.debug(
                    "Invalid canvas color profile data: %s", exc
                )  # noqa: SLF001

        return ColorProfile.srgb()

    def metadata_to_payload(self, metadata: DocumentMetadata) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "width": metadata.width,
            "height": metadata.height,
            "background_color": list(metadata.background_color),
            "dpi": [float(metadata.dpi[0]), float(metadata.dpi[1])],
            "app_version": metadata.app_version,
            "format_version": metadata.format_version,
            "created": metadata.created,
            "modified": metadata.modified,
            "metadata": metadata.metadata or {},
        }

        if metadata.color_profile is not None:
            payload["color_profile"] = {
                "name": metadata.color_profile.name,
                "colorspace": metadata.color_profile.colorspace,
                "has_data": bool(metadata.color_profile.data),
            }
        else:
            payload["color_profile"] = None

        payload["ui_layout"] = metadata.ui_layout
        return payload

    def extract_canvas_metadata(self) -> _CanvasMetadata:
        canvas = self._owner._canvas  # noqa: SLF001
        if isinstance(canvas, SimpleCanvasService):
            width = int(getattr(canvas, "_width", 0))
            height = int(getattr(canvas, "_height", 0))
            dpi_value = float(getattr(canvas, "_dpi", 72))
            background = getattr(canvas, "_background", QColor(255, 255, 255))
            color = (
                background.red(),
                background.green(),
                background.blue(),
                background.alpha(),
            )
            return _CanvasMetadata(width, height, color, (dpi_value, dpi_value))

        if isinstance(canvas, LegacyCanvas):
            config = canvas.config
            color_raw = tuple(int(component) for component in config.background_color)
            if len(color_raw) == 3:
                rgba: tuple[int, int, int, int] = (*color_raw, 255)
            else:
                rgba = cast(tuple[int, int, int, int], color_raw)

            dpi_tuple = self.normalize_dpi(getattr(config, "dpi", (72.0, 72.0)))
            return _CanvasMetadata(
                width=config.width,
                height=config.height,
                background_color=rgba,
                dpi=dpi_tuple,
            )

        raise RuntimeError("Unsupported canvas implementation")

    def normalize_dpi(self, raw: Any) -> tuple[float, float]:
        if isinstance(raw, (int, float)):
            scalar = float(raw)
            return (scalar, scalar)

        if isinstance(raw, Sequence) and not isinstance(raw, (str, bytes, bytearray)):
            values: list[float] = []
            for item in list(raw)[:2]:
                try:
                    values.append(float(item))
                except (TypeError, ValueError):
                    continue

            if values:
                if len(values) == 1:
                    values.append(values[0])
                return (values[0], values[1])

        return (72.0, 72.0)
