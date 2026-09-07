"""Autonomous Production, Strict Compliance Gate, and Telegram Human Approval Subsystem."""

from clipping.contracts.production import (
    ProductionStatus,
    ReviewStatus,
    ProductionComplianceResult,
    ProductionArtifact,
    OperatorInterventionRecord,
    RevisionRecord,
)
from clipping.production.repository import ProductionRepository
from clipping.production.content_analyzer import ProductionContentAnalyzer, ProductionCandidateSegment
from clipping.production.video_editor import ProductionVideoEditor, RenderedClipResult
from clipping.production.compliance_gate import ProductionComplianceGate, GeneratedClipMetadata
from clipping.production.telegram_review import TelegramReviewSystem
from clipping.production.challenge_escalation import ChallengeEscalationManager
from clipping.production.engine import AutonomousProductionEngine
from clipping.production.orchestrator import ProductionPipelineOrchestrator

__all__ = [
    "ProductionStatus",
    "ReviewStatus",
    "ProductionComplianceResult",
    "ProductionArtifact",
    "OperatorInterventionRecord",
    "RevisionRecord",
    "ProductionRepository",
    "ProductionContentAnalyzer",
    "ProductionCandidateSegment",
    "ProductionVideoEditor",
    "RenderedClipResult",
    "ProductionComplianceGate",
    "GeneratedClipMetadata",
    "TelegramReviewSystem",
    "ChallengeEscalationManager",
    "AutonomousProductionEngine",
    "ProductionPipelineOrchestrator",
]
