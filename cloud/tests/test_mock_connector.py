"""The one property that makes the mock connector worth having.

It is an operator tool, not library code, so there is little here to unit
test -- and the thing that would make it *useless* is not a bug you could
catch by running it. Hence one check that reads the source.
"""

import ast
import os

MOCK = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                    "mock_connector.py")

SERVER_MODULES = {"app", "oauth", "contract", "store"}


def _imports() -> set[str]:
    with open(MOCK, encoding="utf-8") as f:
        tree = ast.parse(f.read())
    names = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            names.update(a.name for a in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            names.add(node.module)
    return names


def test_the_mock_connector_shares_no_code_with_the_server():
    """A client that imported the server's own helpers would agree with it by
    construction and prove nothing.

    The point of driving a deployment with this is that every value is built
    from the wire format alone -- the PKCE challenge, the form encoding, the
    JSON-RPC envelope -- because that is all a real connector has. Importing
    `oauth.pkce_challenge` to talk to a server that verifies with
    `oauth.pkce_matches` tests that a function agrees with itself.
    """
    shared = {n.split(".")[0] for n in _imports()} & SERVER_MODULES
    assert not shared, (
        f"mock_connector imports {sorted(shared)} from the server it is meant to "
        "be independent of. Build the value from the wire format instead."
    )


def test_it_is_also_independent_of_the_bus():
    """The other direction. A connector is a stranger to the bus: it reaches
    the team only through the cloud, and anything it learned by importing
    `agent_bus` would be a fact no real connector could have."""
    leaked = [n for n in _imports() if "agent_bus" in n]
    assert not leaked, f"mock_connector imports {leaked}"


def test_the_image_ships_every_module_the_server_imports():
    """The Dockerfile copies its modules by name, so adding one is two edits.

    Missing the second fails at container start with ModuleNotFoundError --
    after a green test run, a green build and a push, on the deploy. Found
    exactly that way while adding `logs.py`.
    """
    cloud_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    dockerfile = os.path.join(cloud_dir, "Dockerfile")
    with open(dockerfile, encoding="utf-8") as f:
        copied = {
            tok for line in f if line.startswith("COPY ") and ".py" in line
            for tok in line.split() if tok.endswith(".py")
        }
    on_disk = {
        f for f in os.listdir(cloud_dir)
        if f.endswith(".py") and f != "mock_connector.py"
    }
    missing = on_disk - copied
    assert not missing, (
        f"{sorted(missing)} live in cloud/ but the image never copies them. "
        "The container will fail at import, on the deploy."
    )
