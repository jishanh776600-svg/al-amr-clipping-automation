"""Task Planning, DAG Dependency Resolution, and Execution Graph."""

from typing import Dict, List, Optional, Set
from clipping.agent.models import AgentTask
from clipping.agent.state import TaskState
from clipping.agent.exceptions import TaskDependencyError
from clipping.logging.logger import get_logger

logger = get_logger("clipping.agent.planner")


class TaskGraph:
    """
    Directed Acyclic Graph (DAG) representing tasks and dependency constraints.
    Enforces cycle detection and calculates topological execution order.
    """

    def __init__(self, tasks: Optional[List[AgentTask]] = None):
        self._tasks: Dict[str, AgentTask] = {}
        for t in (tasks or []):
            self.add_task(t)

    def add_task(self, task: AgentTask) -> None:
        """Adds a task to the execution graph and validates dependencies."""
        self._tasks[task.task_id] = task

    def get_task(self, task_id: str) -> Optional[AgentTask]:
        return self._tasks.get(task_id)

    @property
    def all_tasks(self) -> List[AgentTask]:
        return list(self._tasks.values())

    def validate_acyclic(self) -> None:
        """Detects cycles within the dependency graph. Raises TaskDependencyError if cyclic."""
        visited: Set[str] = set()
        rec_stack: Set[str] = set()

        def dfs(task_id: str) -> None:
            visited.add(task_id)
            rec_stack.add(task_id)

            task = self._tasks.get(task_id)
            if task:
                for dep_id in task.dependencies:
                    if dep_id not in self._tasks:
                        # Dependency external or pending
                        continue
                    if dep_id not in visited:
                        dfs(dep_id)
                    elif dep_id in rec_stack:
                        raise TaskDependencyError(f"Cyclic dependency detected involving task '{task_id}' -> '{dep_id}'")

            rec_stack.remove(task_id)

        for tid in self._tasks:
            if tid not in visited:
                dfs(tid)

    def get_topological_order(self) -> List[AgentTask]:
        """Calculates topological execution sequence so dependencies execute before dependents."""
        self.validate_acyclic()
        in_degree: Dict[str, int] = {tid: 0 for tid in self._tasks}
        dependents: Dict[str, List[str]] = {tid: [] for tid in self._tasks}

        for tid, task in self._tasks.items():
            for dep in task.dependencies:
                if dep in self._tasks:
                    dependents[dep].append(tid)
                    in_degree[tid] += 1

        queue = [tid for tid, deg in in_degree.items() if deg == 0]
        order: List[AgentTask] = []

        while queue:
            curr_id = queue.pop(0)
            order.append(self._tasks[curr_id])

            for dep_id in dependents.get(curr_id, []):
                in_degree[dep_id] -= 1
                if in_degree[dep_id] == 0:
                    queue.append(dep_id)

        if len(order) != len(self._tasks):
            raise TaskDependencyError("Unable to calculate topological order; unresolved dependency cycle exists")

        return order

    def resolve_ready_tasks(self, completed_task_ids: Set[str]) -> List[AgentTask]:
        """
        Returns tasks whose dependencies are completely satisfied by completed_task_ids
        and that are currently in PENDING or PLANNED state.
        """
        ready: List[AgentTask] = []
        for task in self._tasks.values():
            if task.status in (TaskState.PENDING, TaskState.PLANNED):
                deps_satisfied = all(dep in completed_task_ids for dep in task.dependencies)
                if deps_satisfied:
                    ready.append(task)
        return ready


class TaskPlanner:
    """
    Translates high-level business objectives into structured task graphs.
    """

    def plan_clipping_workflow(
        self,
        source_uri: str,
        campaign_id: str = "default_campaign",
        parent_task_id: Optional[str] = None,
    ) -> TaskGraph:
        """Constructs an end-to-end autonomous clipping task plan."""
        import uuid
        from clipping.agent.models import TaskType, TaskPriority

        clipping_task_id = f"task_clip_{uuid.uuid4().hex[:10]}"
        clip_task = AgentTask(
            task_id=clipping_task_id,
            parent_task_id=parent_task_id,
            campaign_id=campaign_id,
            objective=f"Process source video into vertical clips: {source_uri}",
            task_type=TaskType.MEDIA_CLIPPING,
            priority=TaskPriority.HIGH,
            inputs={
                "source_uri": source_uri,
                "campaign_id": campaign_id,
                "capability": "media_clipping",
            },
            status=TaskState.PLANNED,
        )

        graph = TaskGraph([clip_task])
        graph.validate_acyclic()
        return graph
