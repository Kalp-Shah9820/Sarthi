"""Base class for the seven agents and the arbiter."""

from sarthi.blackboard.store import Blackboard
from sarthi.llm.gateway import LlmGateway

PHASES = ("sense", "decide", "resolve", "execute")


class Agent:
    key: str = "agent"      # the frontend's i18n key, e.g. "macroSentinel"
    phase: str = "sense"    # one of PHASES

    def __init__(self, bb: Blackboard, llm: LlmGateway, settings, strategy: dict):
        self.bb = bb
        self.llm = llm
        self.settings = settings
        self.strategy = strategy

    async def run(self, state: dict) -> dict:
        """Graph entry point. One agent failing must not kill the run: the error is logged and reported."""
        try:
            return await self.work(state)
        except Exception as exc:  # noqa: BLE001  isolation boundary between agents
            self.bb.error(self.key, exc, phase=self.phase)
            return {"errors": [f"{self.key}: {type(exc).__name__}: {exc}"]}

    async def work(self, state: dict) -> dict:
        """Do the agent's job and return a partial state update. CPU-heavy steps go through asyncio.to_thread."""
        raise NotImplementedError
