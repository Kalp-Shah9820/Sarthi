"""Learning what the manager accepts: one Beta distribution of approval rate per kind of action.

An "arm" is a kind of action in a context, e.g. `ghost:transfer`. Each approval or dismissal updates it.
Thompson sampling (drawing from the distribution rather than using its mean) keeps a little exploration
alive, so an option that was dismissed early is still tried occasionally.
"""

from scipy.stats import beta as beta_dist

from sarthi.db import session
from sarthi.models import BanditArm

PRIOR = (2.0, 2.0)   # no history: approval rate believed to be around 50 %, held loosely


def get_arm(key: str) -> tuple[float, float]:
    """(alpha, beta) for an arm; the prior if it has never been updated. Reading never creates a row."""
    with session() as s:
        arm = s.get(BanditArm, key)
        return (arm.alpha, arm.beta) if arm else PRIOR


def mean(key: str) -> float:
    a, b = get_arm(key)
    return a / (a + b)


def sample(key: str, rng) -> float:
    """One Thompson draw of the approval rate."""
    a, b = get_arm(key)
    return float(rng.beta(a, b))


def lower_bound(key: str, q: float = 0.10) -> float:
    """Approval rate we are (1 - q) sure the true rate exceeds."""
    a, b = get_arm(key)
    return float(beta_dist.ppf(q, a, b))


def record(key: str, approved: bool) -> float:
    """Update an arm with one decision. Returns the change in its mean approval rate."""
    with session() as s:
        arm = s.get(BanditArm, key) or BanditArm(key=key, alpha=PRIOR[0], beta=PRIOR[1])
        before = arm.alpha / (arm.alpha + arm.beta)
        if approved:
            arm.alpha += 1
        else:
            arm.beta += 1
        after = arm.alpha / (arm.alpha + arm.beta)
        s.add(arm)
        s.commit()
    return after - before
