"""Local runtime infrastructure for state, provider scheduling, and stages."""

__all__ = [
    "PipelineExecutor",
    "ProviderRuntime",
    "RunContext",
    "StageResult",
    "StateStore",
    "default_state_store_path",
]


def __getattr__(name: str):
    if name == "ProviderRuntime":
        from .provider_runtime import ProviderRuntime
        return ProviderRuntime
    if name in {"PipelineExecutor", "RunContext", "StageResult"}:
        from .stage_executor import PipelineExecutor, RunContext, StageResult
        return {
            "PipelineExecutor": PipelineExecutor,
            "RunContext": RunContext,
            "StageResult": StageResult,
        }[name]
    if name in {"StateStore", "default_state_store_path"}:
        from .state_store import StateStore, default_state_store_path
        return {
            "StateStore": StateStore,
            "default_state_store_path": default_state_store_path,
        }[name]
    raise AttributeError(name)
