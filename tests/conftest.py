"""Shared fixtures. No network, no sleeps over 1 s, seeded.

The live net/demand are copied into tests/fixtures at import time *if the fixture
differs from assets/*, so a parallel agent regenerating assets/ cannot break the
suite mid-run, but the suite still tells us when the shipped assets disagree.
"""
import os
import shutil

import pytest

from core import contract as C

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
FIX = os.path.join(os.path.dirname(os.path.abspath(__file__)), "fixtures")
ASSETS = os.path.join(ROOT, "assets")

LOG_PATH = os.path.join(ROOT, "logs", "test_failures.log")


def _sync(name):
    src = os.path.join(ASSETS, name)
    dst = os.path.join(FIX, name)
    os.makedirs(FIX, exist_ok=True)
    if os.path.exists(src) and (not os.path.exists(dst)
                                or os.path.getmtime(src) > os.path.getmtime(dst) + 60):
        shutil.copyfile(src, dst)
    return dst


@pytest.fixture(scope="session")
def net_path():
    return _sync("taft.net.xml")


@pytest.fixture(scope="session")
def demand_path():
    return _sync("demand.rou.xml")


@pytest.fixture(scope="session", autouse=True)
def _failure_log(request):
    os.makedirs(os.path.dirname(LOG_PATH), exist_ok=True)
    with open(LOG_PATH, "w", encoding="utf-8") as f:
        f.write("# pytest run %s\n" % __import__("time").strftime("%Y-%m-%d %H:%M:%S"))
    yield


@pytest.hookimpl(hookwrapper=True)
def pytest_runtest_makereport(item, call):
    outcome = yield
    rep = outcome.get_result()
    if rep.failed:
        with open(LOG_PATH, "a", encoding="utf-8") as f:
            f.write(f"FAILED {item.nodeid}\n{(rep.longreprtext or '')[-4000:]}\n\n")


# ---------------------------------------------------------------- summary accounting

STATS = {"passed": 0, "failed": 0, "error": 0, "skipped": 0, "xfail": 0}


def pytest_sessionfinish(session, exitstatus):
    with open(LOG_PATH, "a", encoding="utf-8") as f:
        f.write("SUMMARY %r\n" % (STATS,))


def pytest_runtest_logreport(report):
    if report.when == "call" or (report.when == "setup" and report.outcome == "failed"):
        key = "failed" if report.outcome == "failed" else "passed"
        STATS[key] = STATS.get(key, 0) + 1


# ---------------------------------------------------------------- live sim


@pytest.fixture
def env(request):
    from env.env import Env

    live = request.node.get_closest_marker("live")
    e = Env()
    if live is not None:
        e.start()
    else:
        # session-scoped libsumo is not possible (one process per episode), so
        # start() is stubbed with the fixture net/demand when asked.
        start_with(e, request)
    yield e
    e.close()


def start_with(e, request):
    import libsumo as traci

    from env.env import SUMO_BIN

    net = request.getfixturevalue("net_path")
    rou = request.getfixturevalue("demand_path")
    traci.start([SUMO_BIN, "-n", net, "-r", rou, "--seed", "1", "--time-to-teleport", "-1",
                 "--no-step-log", "--no-warnings", "--collision.action", "none",
                 "--step-length", "1", "--device.tripinfo.probability", "1.0",
                 "--tripinfo-output", os.path.join(ROOT, "logs", "tripinfo.xml")])
    e._index()
    e.started = True


# ---------------------------------------------------------------- hand-built observations


def ph(idx, kind="through", movements=("e_in",)):
    return C.Phase(idx, "G" + "r", tuple(movements), kind)


def obs(node="A11", phase=0, in_green=12, min_green=8, max_green=45, eligible=(1, 2),
        parked=False, locked_by="", last_switch=-99, t=0):
    return C.NodeObs(
        node=node, name="Taft Ave", phase=phase, in_green=in_green, min_green=min_green,
        max_green=max_green,
        approaches=[C.Approach("e_in", [4], [10], 0)],
        outgoing=[C.OutLink("e_out", 2, 30, 0)],
        neighbours=[{"node": "B11", "name": "Vito Cruz", "phase": 0, "queue": 3, "spillback": 1}],
        eligible=list(eligible), tickets=[], locked_by=locked_by, parked=parked,
        last_switch=last_switch, t=t)


def tables(*states_per_phase):
    return [C.Phase(i, s, ("e_in",), "through") for i, s in enumerate(states_per_phase)]


# ---------------------------------------------------------------- fake llm


class FakeLLM:
    """Stands in for llm.llm.LLM: scripted answers per node, one per tick."""

    def __init__(self, script=None, default=("hold", -1, 10), oracle=False, stall=None):
        self.script = dict(script or {})
        self.default = default
        self.oracle = oracle
        self.stall = set(stall or ())
        self.ticks = []
        self.plan = {"diagnosis": "test", "cycle": [], "steps": [], "release_order": []}

    async def tick(self, obs):
        self.ticks.append([o.node for o in obs])
        out = []
        for o in obs:
            if o.node in self.stall or o.parked:
                out.append(C.Intent(o.node, "hold", -1, 10, "no answer", "stall"))
                continue
            q = self.script.get(o.node)
            if q:
                act, tgt, green, why = q.pop(0)
            elif self.oracle and o.eligible:
                act, tgt, green, why = "switch", min(o.eligible), 15, "test"
            else:
                act, tgt, green, why = self.default
            out.append(C.Intent(o.node, act, tgt, green, why))
        return out

    async def orchestrate(self, report):
        return dict(self.plan)