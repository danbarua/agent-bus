"""Path setup, and the one thing worth saying about the emulator.

`cloud/` is flat modules rather than a package: it is never installed, only run
in a container and imported by these tests, so a package boundary would buy
nothing and cost an import prefix everywhere.
"""

import os
import socket
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

EMULATOR = os.environ.get("FIRESTORE_EMULATOR_HOST", "127.0.0.1:8080")


def _emulator_up() -> bool:
    host, _, port = EMULATOR.partition(":")
    try:
        with socket.create_connection((host, int(port or 8080)), timeout=0.5):
            return True
    except OSError:
        return False


@pytest.fixture
def firestore():
    """A client against the emulator, or a skip that says how to get one.

    Named in the skip on purpose. A suite that quietly skipped its only real
    store test is the "green build that tested nothing" failure the compose file
    already warns about.
    """
    if not _emulator_up():
        pytest.skip(
            f"no Firestore emulator on {EMULATOR}. Start one with:\n"
            "  gcloud emulators firestore start --host-port=127.0.0.1:8080"
        )
    os.environ["FIRESTORE_EMULATOR_HOST"] = EMULATOR
    import store as store_mod
    from google.cloud import firestore as fs
    return store_mod.Firestore(client=fs.Client(project="agent-bus-test"))


@pytest.fixture
def address():
    """A queue nobody else in this run touches.

    The emulator keeps data for the life of the process, so two tests naming
    `desktop:claude:inbox` see each other's messages -- which is how the first
    version of the ack test failed, reading a message the round-trip test had
    left behind. Same shape as a unit test reading the developer's live bus.
    """
    import uuid
    return "desktop", f"t{uuid.uuid4().hex[:10]}"
