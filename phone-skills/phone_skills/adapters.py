"""Deterministic simulated device; no ADB or network access."""
from copy import deepcopy
from .model import keys, require
from .selector import Node, State


def state_from_fixture(value):
    keys(value, {"app", "activity", "nodes", "blocked"}, {"app", "activity", "nodes"})
    require(isinstance(value["nodes"], list), "invalid node list")
    nodes = [Node(**n) for n in value["nodes"]]
    require(len({n.key for n in nodes}) == len(nodes), "duplicate node keys")
    return State(value["app"], value["activity"], nodes, value.get("blocked", ""))


class MockAdapter:
    kind = "mock"

    def __init__(self, fixture, device_id="mock-device"):
        keys(fixture, {"initial", "states", "transitions", "observe_error_at"}, {"initial", "states", "transitions"})
        self.fixture = deepcopy(fixture)
        self.states = {k: state_from_fixture(v) for k, v in fixture["states"].items()}
        require(fixture["initial"] in self.states, "unknown initial state")
        self.current = fixture["initial"]
        self.device_id = device_id
        self.actions = []
        self.observations = 0
        self.pending = None

    def open(self):
        pass

    def close(self):
        pass

    def observe(self):
        self.observations += 1
        if self.observations == self.fixture.get("observe_error_at"):
            raise ConnectionError("simulated disconnect")
        if self.pending:
            transition, remaining = self.pending
            if remaining <= 0:
                self.current = transition["to"]
                self.pending = None
            else:
                self.pending = transition, remaining - 1
        return deepcopy(self.states[self.current])

    def act(self, step, target):
        action = {"step": step["id"], "action": step["action"], "target": target.key if target else None}
        self.actions.append(action)
        hits = [t for t in self.fixture["transitions"] if t["from"] == self.current and
                t["step"] == step["id"] and t.get("target") == action["target"]]
        require(len(hits) == 1, "fixture transition missing/ambiguous")
        transition = hits[0]
        if transition.get("error") == "before":
            raise ConnectionError("simulated action error")
        if transition.get("delay_reads", 0):
            self.pending = transition, transition["delay_reads"]
        else:
            self.current = transition["to"]
        if transition.get("error") == "after":
            raise ConnectionError("simulated lost acknowledgement")
