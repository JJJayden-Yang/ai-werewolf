"""supervisor —— A 主导的调度层（B/C 对接）。

编排：Engine ↔ ContextAssembler ↔ Agent ↔ EventLog ↔ Belief。
跨边界依赖（ContextAssembler / Agent / EventSink）通过构造注入 + Protocol，
不硬依赖 context / agent_runtime / agent_policy 的具体实现。
"""

from supervisor.supervisor import GameRunError, Supervisor

__all__ = ["GameRunError", "Supervisor"]
