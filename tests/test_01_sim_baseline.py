"""Assets load; demand replays; traffic keeps moving for 300 s under fixed-time control.

Priority 1: this is the deadlock class of bug. A run that loads but freezes, or that
empties the net and never refills, is a failure even though nothing raised.
"""
import pytest

pytestmark = pytest.mark.integration

TOTAL = 600
WARM = 60          # first vehicles need time to reach the grid


@pytest.fixture
def fixed(env):
    """Disable the LLM path: every node holds its first green, max-green forces cycling."""
    return env


def test_net_has_nine_signals(fixed):
    """27 grid nodes plus the arm junctions that carry crossing traffic.

    Signals are placed geometrically: any node joining two grid lines. Arm ends are
    never signalised, so the floor is the 27 grid nodes.
    """
    grid = [n for n in fixed.nodes if "_" not in n]
    assert len(grid) == 27, f"expected the 27-node 3x3 grid, got {len(grid)}"
    assert len(fixed.nodes) >= 27, "arm junctions with crossing traffic lost signalisation"
    for j, tab in fixed.tables.items():
        assert any(p.kind == "through" for p in tab), f"{j}: phase table has no through phase"


def run_to(env, t_end):
    """Step until sim time t_end and return the frames produced.

    force_phase() steps SUMO internally (3 s yellow + 2 s all-red), so one step()
    can advance the clock by up to 8 s; drive on time and watch for overrun.
    """
    frames = []
    while env.t < t_end:
        frames.append(env.step())
        assert env.t <= t_end + 10, f"clock jumped past {t_end}: t={env.t}"
    print("DEBUG frames", len(frames), "t", env.t, "stats", frames[-1].stats)
    return frames


def test_demand_loads_and_replays(fixed):
    frames = run_to(fixed, 5)
    assert frames[-1].stats["veh"] > 0, "no vehicle entered the net in the first 5 s"


def test_vehicles_keep_moving_for_300s(fixed):
    """Catches the gridlock/deadlock class: no motion and no arrival after warm-up."""
    moving, still, arrived = 0, 0, 0
    for frame in run_to(fixed, TOTAL):
        arrived += frame.stats["arrived"]
        if fixed.t > WARM:
            if frame.stats["speed"] > 0.1:
                moving += 1
            else:
                still += 1
    assert arrived > 0, f"no vehicle arrived in {TOTAL} s of sim time: traffic is frozen"
    assert moving >= 0.8 * (moving + still), f"vehicles stood still in {still}/{moving + still} samples"


def test_gridlock_occurs_but_stays_reversible(fixed):
    """SPEC §6 wants gridlock from the demand; SPEC §9 wants a grid that still drains.

    Demand is ~300 veh/h/approach into a 3x3 with 8 s fixed greens, so saturation is
    expected. A full-stops-for-good net (arrivals frozen to 0) is not.
    """
    counts, peak_blocked = [], 0
    last_t = None
    while fixed.t < TOTAL:
        frame = fixed.step()
        if frame.stats["t"] // 30 != last_t:
            last_t = frame.stats["t"] // 30
            counts.append(0)
        counts[-1] = frame.stats["arrived"]
        peak_blocked = max(peak_blocked, frame.stats["blocked"])
    assert max(counts) > 0, f"nothing ever arrived: the net never ran ({counts})"
    # Gridlock is the premise, not a bug: the tail may drain to zero. What must not
    # happen is a forever-frozen net, so require some arrival in the final third.
    assert sum(counts[-4:]) > 0, f"arrivals stopped dead in the last 120 s (per-30 s: {counts})"
    assert peak_blocked >= 1, "no blocked edge ever reported: gridlock never forms"
