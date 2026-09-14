"""SCORP V4 transactional Master A and dynamic Worker runtime."""

from .models import AcceptanceStatus, CommitResult, IntentState
from .work_result import (
    WorkResultRejected,
    decode_work_result_response,
    result_content_sha256,
    validate_work_result,
)
from .master_watchdog import MasterWatchdog
from .master_supervisor import MasterSupervisor, SupervisorDecision, SupervisorLoopResult
from .master_controller import ControllerRejected, ControllerStep, MasterAController
from .execution_adapter import ExecutionAdapterRejected, ExecutionReceipt, LocalExecutionAdapter
from .git_worktree import GitWorktreeManager, GitWorktreeReceipt, GitWorktreeRejected
from .runtime_protocol import RuntimeProtocolError, RuntimeRequest, parse_request
from .runtime_commands import RuntimeCommandService
from .operator_control import OperatorControlService

__all__ = [
    "AcceptanceStatus",
    "CommitResult",
    "IntentState",
    "WorkResultRejected",
    "decode_work_result_response",
    "result_content_sha256",
    "validate_work_result",
    "MasterWatchdog",
    "MasterSupervisor",
    "SupervisorDecision",
    "SupervisorLoopResult",
    "ControllerRejected",
    "ControllerStep",
    "MasterAController",
    "ExecutionAdapterRejected",
    "ExecutionReceipt",
    "LocalExecutionAdapter",
    "GitWorktreeManager",
    "GitWorktreeReceipt",
    "GitWorktreeRejected",
    "RuntimeProtocolError",
    "RuntimeRequest",
    "parse_request",
    "RuntimeCommandService",
    "OperatorControlService",
]
