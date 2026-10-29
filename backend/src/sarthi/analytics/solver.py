"""Every call into SciPy's HiGHS solver (milp, linprog) runs on one long-lived thread.

HiGHS keeps scheduler state per operating-system thread. The agents do their number work in short-lived
worker threads (asyncio.to_thread), so the solver was entered from a different thread almost every time,
and now and then it wrote through a stale pointer: the intermittent "Windows fatal exception: access
violation" seen in test runs, traced to scipy/optimize/_highspy/_core.pyd (a write to address 0x77).
Calling it from the same thread every time avoids that, and costs nothing: the problems are tiny.
"""

import threading
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor

THREAD_NAME = "sarthi-highs"

_lock = threading.Lock()
_executor: ThreadPoolExecutor | None = None


def _pool() -> ThreadPoolExecutor:
    global _executor
    with _lock:
        if _executor is None:
            _executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix=THREAD_NAME)
        return _executor


def on_solver_thread() -> bool:
    return threading.current_thread().name.startswith(THREAD_NAME)


def solve[T](fn: Callable[..., T], *args, **kwargs) -> T:
    """Run `fn(*args, **kwargs)` on the solver thread and return its result (exceptions are re-raised here)."""
    if on_solver_thread():          # already there, e.g. a solver call made from inside another
        return fn(*args, **kwargs)
    return _pool().submit(fn, *args, **kwargs).result()
