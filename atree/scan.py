"""Collect talks from every adapter."""
from .agents import AGENTS
from .agents.base import prune_caches


def scan_all(errors=None):
    talks = []
    for agent in AGENTS:
        try:
            talks.extend(agent.scan())
        except Exception as e:  # one broken adapter must not hide the others
            msg = "scanning %s failed: %s" % (agent.label, e)
            if errors is None:
                print("atree: " + msg)
            else:
                errors.append(msg)
    prune_caches()
    return talks
