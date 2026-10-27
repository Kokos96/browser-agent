from .agents.orchestrator import QuizOrchestrator


class BrowserAgent(QuizOrchestrator):
    """Primary BrowserAgent entrypoint backed by the multi-agent QuizOrchestrator."""
    pass


__all__ = ["BrowserAgent", "QuizOrchestrator"]
