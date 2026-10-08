"""Language-model access. `get_llm()` returns the process-wide gateway."""

from sarthi.config import get_settings

_gateway = None


def get_llm():
    global _gateway
    if _gateway is None:
        from sarthi.llm.gateway import LlmGateway

        _gateway = LlmGateway(get_settings())
    return _gateway


def reset_llm() -> None:
    """Forget the gateway so the next `get_llm()` re-reads settings (used by tests)."""
    global _gateway
    _gateway = None
