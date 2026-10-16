"""ComfyUIとの連携ブリッジモジュール"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime
from enum import Enum
from typing import Any, ClassVar

from ..common.events import PluginEvent
from ..common.models import ComposedPrompt
from .bus_utils import request_with_timeout

logger = logging.getLogger(__name__)


class WorkflowStatus(Enum):
    """ワークフロー実行状態"""

    PENDING = "pending"
    QUEUED = "queued"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"


@dataclass
class ComfyUIWorkflowParams:
    """ComfyUIワークフローパラメータ"""

    steps: int = 20
    cfg_scale: float = 7.0
    sampler_name: str = "euler"
    scheduler: str = "normal"
    seed: int = -1  # -1 = ランダム
    width: int = 512
    height: int = 512
    batch_size: int = 1
    denoise: float = 1.0
    model: str = "default"
    vae: str = "default"
    clip_skip: int = -1

    def to_dict(self) -> dict[str, Any]:
        """辞書形式に変換"""
        return {
            "steps": self.steps,
            "cfg_scale": self.cfg_scale,
            "sampler_name": self.sampler_name,
            "scheduler": self.scheduler,
            "seed": self.seed,
            "width": self.width,
            "height": self.height,
            "batch_size": self.batch_size,
            "denoise": self.denoise,
            "model": self.model,
            "vae": self.vae,
            "clip_skip": self.clip_skip,
        }


@dataclass
class WorkflowResult:
    """ワークフロー実行結果"""

    workflow_id: str
    status: WorkflowStatus
    prompt_id: str
    positive_prompt: str
    negative_prompt: str
    params: ComfyUIWorkflowParams
    started_at: datetime
    completed_at: datetime | None = None
    error_message: str | None = None
    images: list[str] = None  # 画像パスまたはBase64データ
    metadata: dict[str, Any] = None

    def __post_init__(self):
        if self.images is None:
            self.images = []
        if self.metadata is None:
            self.metadata = {}


class ComfyUIBridge:
    """ComfyUIとの通信ブリッジ"""

    def __init__(self, event_bus: Any):
        """
        Args:
            event_bus: イベントバス（ExtendedEventBus）
        """  # noqa: RUF002
        self.event_bus = event_bus
        self._active_workflows: dict[str, WorkflowResult] = {}
        self._register_handlers()

    def _register_handlers(self) -> None:
        """イベントハンドラーの登録"""
        # ComfyUIからのレスポンスハンドラー
        self.event_bus.subscribe("comfyui.workflow.started", self._handle_workflow_started)
        self.event_bus.subscribe("comfyui.workflow.progress", self._handle_workflow_progress)
        self.event_bus.subscribe("comfyui.workflow.completed", self._handle_workflow_completed)
        self.event_bus.subscribe("comfyui.workflow.failed", self._handle_workflow_failed)

    async def send_to_comfyui(
        self, prompt: ComposedPrompt, params: ComfyUIWorkflowParams | None = None, priority: int = 0
    ) -> str:
        """
        ComfyUIにプロンプトを送信

        Args:
            prompt: 組み合わせ済みプロンプト
            params: ワークフローパラメータ
            priority: 優先度（0が最高）

        Returns:
            workflow_id: ワークフローID
        """  # noqa: RUF002
        if params is None:
            params = ComfyUIWorkflowParams()

        # ワークフローIDを生成
        import uuid  # noqa: PLC0415

        workflow_id = str(uuid.uuid4())

        # 結果オブジェクトを作成
        result = WorkflowResult(
            workflow_id=workflow_id,
            status=WorkflowStatus.PENDING,
            prompt_id=prompt.id,
            positive_prompt=prompt.positive_prompt,
            negative_prompt=prompt.negative_prompt,
            params=params,
            started_at=datetime.now(),
        )

        self._active_workflows[workflow_id] = result

        # イベントペイロードを構築
        payload = {
            "workflow_id": workflow_id,
            "positive_prompt": prompt.positive_prompt,
            "negative_prompt": prompt.negative_prompt,
            "params": params.to_dict(),
            "priority": priority,
            "metadata": {
                "prompt_id": prompt.id,
                "snippet_ids": prompt.snippet_ids,
                "template_id": prompt.template_id,
            },
        }

        # ComfyUIプラグインにイベント送信
        event = PluginEvent.create_request(
            event_type="comfyui.workflow.execute", source_plugin="prompt_editor", payload=payload
        )

        try:
            # イベント送信（タイムアウト10秒）  # noqa: RUF003
            response = await request_with_timeout(self.event_bus, event, timeout=10.0)

            if response.get("success"):
                result.status = WorkflowStatus.QUEUED
                logger.info(f"ワークフロー送信成功: {workflow_id}")
            else:
                result.status = WorkflowStatus.FAILED
                result.error_message = response.get("error", "Unknown error")
                logger.error(f"ワークフロー送信失敗: {result.error_message}")

        except TimeoutError:
            result.status = WorkflowStatus.FAILED
            result.error_message = "ComfyUI response timeout"
            logger.error("ComfyUIからの応答がタイムアウトしました")

        except (AttributeError, RuntimeError, ValueError) as e:
            result.status = WorkflowStatus.FAILED
            result.error_message = str(e)
            logger.error(f"ワークフロー送信エラー: {e}")

        return workflow_id

    async def cancel_workflow(self, workflow_id: str) -> bool:
        """
        ワークフローをキャンセル

        Args:
            workflow_id: ワークフローID

        Returns:
            成功した場合True
        """
        if workflow_id not in self._active_workflows:
            logger.warning(f"ワークフローが見つかりません: {workflow_id}")
            return False

        # キャンセルイベント送信
        event = PluginEvent.create_request(
            event_type="comfyui.workflow.cancel",
            source_plugin="prompt_editor",
            payload={"workflow_id": workflow_id},
        )

        try:
            response = await request_with_timeout(self.event_bus, event, timeout=5.0)
            if response.get("success"):
                self._active_workflows[workflow_id].status = WorkflowStatus.CANCELLED
                logger.info(f"ワークフローキャンセル成功: {workflow_id}")
                return True

        except (TimeoutError, AttributeError, RuntimeError, ValueError) as e:
            logger.error(f"ワークフローキャンセルエラー: {e}")

        return False

    def get_workflow_status(self, workflow_id: str) -> WorkflowResult | None:
        """
        ワークフローの状態を取得

        Args:
            workflow_id: ワークフローID

        Returns:
            WorkflowResult or None
        """
        return self._active_workflows.get(workflow_id)

    def get_active_workflows(self) -> list[WorkflowResult]:
        """
        アクティブなワークフロー一覧を取得

        Returns:
            実行中のワークフローリスト
        """
        return [
            w
            for w in self._active_workflows.values()
            if w.status in [WorkflowStatus.PENDING, WorkflowStatus.QUEUED, WorkflowStatus.RUNNING]
        ]

    async def _handle_workflow_started(self, event: PluginEvent) -> None:
        """ワークフロー開始イベントの処理"""
        workflow_id = event.payload.get("workflow_id")
        if workflow_id in self._active_workflows:
            self._active_workflows[workflow_id].status = WorkflowStatus.RUNNING
            logger.info(f"ワークフロー開始: {workflow_id}")

    async def _handle_workflow_progress(self, event: PluginEvent) -> None:
        """ワークフロー進行状況イベントの処理"""
        workflow_id = event.payload.get("workflow_id")
        progress = event.payload.get("progress", 0)

        if workflow_id in self._active_workflows:
            result = self._active_workflows[workflow_id]
            result.metadata["progress"] = progress
            logger.debug(f"ワークフロー進行中: {workflow_id} ({progress}%)")

    async def _handle_workflow_completed(self, event: PluginEvent) -> None:
        """ワークフロー完了イベントの処理"""
        workflow_id = event.payload.get("workflow_id")

        if workflow_id in self._active_workflows:
            result = self._active_workflows[workflow_id]
            result.status = WorkflowStatus.COMPLETED
            result.completed_at = datetime.now()
            result.images = event.payload.get("images", [])
            result.metadata.update(event.payload.get("metadata", {}))

            logger.info(f"ワークフロー完了: {workflow_id}")

            # 完了通知イベントを発行
            completion_event = PluginEvent.create_notification(
                event_type="prompteditor.workflow.completed",
                source_plugin="prompt_editor",
                payload={
                    "workflow_id": workflow_id,
                    "prompt_id": result.prompt_id,
                    "images": result.images,
                    "duration": (result.completed_at - result.started_at).total_seconds(),
                },
            )
            await self.event_bus.emit_async(completion_event.event_type, completion_event)

    async def _handle_workflow_failed(self, event: PluginEvent) -> None:
        """ワークフロー失敗イベントの処理"""
        workflow_id = event.payload.get("workflow_id")

        if workflow_id in self._active_workflows:
            result = self._active_workflows[workflow_id]
            result.status = WorkflowStatus.FAILED
            result.completed_at = datetime.now()
            result.error_message = event.payload.get("error", "Unknown error")

            logger.error(f"ワークフロー失敗: {workflow_id} - {result.error_message}")

            # 失敗通知イベントを発行
            failure_event = PluginEvent.create_notification(
                event_type="prompteditor.workflow.failed",
                source_plugin="prompt_editor",
                payload={
                    "workflow_id": workflow_id,
                    "prompt_id": result.prompt_id,
                    "error": result.error_message,
                },
            )
            await self.event_bus.emit_async(failure_event.event_type, failure_event)

    def cleanup_completed_workflows(self, older_than_hours: int = 24) -> int:
        """
        完了したワークフローをクリーンアップ

        Args:
            older_than_hours: この時間より古い完了ワークフローを削除

        Returns:
            削除されたワークフロー数
        """
        from datetime import timedelta  # noqa: PLC0415

        cutoff_time = datetime.now() - timedelta(hours=older_than_hours)
        to_remove = []

        for workflow_id, result in self._active_workflows.items():
            if (
                result.status
                in [
                    WorkflowStatus.COMPLETED,
                    WorkflowStatus.FAILED,
                    WorkflowStatus.CANCELLED,
                ]
                and result.completed_at
                and result.completed_at < cutoff_time
            ):
                to_remove.append(workflow_id)

        for workflow_id in to_remove:
            del self._active_workflows[workflow_id]

        if to_remove:
            logger.info(f"{len(to_remove)}個の古いワークフローをクリーンアップしました")

        return len(to_remove)


class ComfyUIPresets:
    """ComfyUI用のプリセット設定"""

    PRESETS: ClassVar[dict[str, ComfyUIWorkflowParams]] = {
        "fast": ComfyUIWorkflowParams(
            steps=10,
            cfg_scale=5.0,
            sampler_name="euler_a",
            scheduler="karras",
        ),
        "balanced": ComfyUIWorkflowParams(
            steps=20,
            cfg_scale=7.0,
            sampler_name="euler",
            scheduler="normal",
        ),
        "quality": ComfyUIWorkflowParams(
            steps=30,
            cfg_scale=8.0,
            sampler_name="dpm_2m",
            scheduler="karras",
        ),
        "ultra": ComfyUIWorkflowParams(
            steps=50,
            cfg_scale=10.0,
            sampler_name="dpm_2m_sde",
            scheduler="exponential",
        ),
    }

    @classmethod
    def get_preset(cls, name: str) -> ComfyUIWorkflowParams:
        """プリセットを取得"""
        return cls.PRESETS.get(name, cls.PRESETS["balanced"])
