"""The negatives. These are the specification; the flow is what is left over.

Written before `oauth.py` existed, deliberately. A security check retrofitted to
an implementation tends to assert what the code does rather than what it must
do -- and the predecessor made mutation-checking a rule after finding a
**vacuous test inside the very check written to enforce it**.

Every assertion here is mutation-checked before the change lands: break the
guard, watch this fail, restore. A guard nobody has watched fail is a guard
nobody has tested. The results go in the pull request, next to the diff they
describe.
"""

import time

import oauth
import pytest

KEY = b"\x01" * 32
ISSUER = "https://bus.example.invalid"
RESOURCE = f"{ISSUER}/mcp"
ALLOWED = ["https://claude.ai/api/mcp/auth_callback"]


class StubStore:
    """Clients and codes, in a dict. The real one is Firestore; nothing here
    depends on which."""

    def __init__(self):
        self.clients: dict[str, dict] = {}
        self.codes: dict[str, dict] = {}

    def put_client(self, record):
        self.clients[record["client_id"]] = record

    def client(self, client_id):
        return self.clients.get(client_id)

    def put_code(self, code, record):
        self.codes[code] = record

    def take_code(self, code):
        """Single use: reading it consumes it. That is the replay defence, and
        it lives in the store so it cannot be forgotten at a call site."""
        return self.codes.pop(code, None)


@pytest.fixture
def store():
    return StubStore()


# ----------------------------------------------------------------- the tokens

def test_a_token_survives_a_round_trip():
    tok = oauth.sign_token({"sub": "dan", "kind": "access",
                            "exp": time.time() + 60}, KEY)
    assert oauth.verify_token(tok, KEY)["sub"] == "dan"


def test_a_tampered_payload_is_refused():
    """The signature is the whole security property. Editing the claims and
    keeping the signature must not work."""
    tok = oauth.sign_token({"sub": "dan", "kind": "access",
                            "exp": time.time() + 60}, KEY)
    _payload, sig = tok.split(".")
    forged = oauth.sign_token({"sub": "someone-else", "kind": "access",
                               "exp": time.time() + 60}, KEY).split(".")[0]
    assert oauth.verify_token(f"{forged}.{sig}", KEY) is None


def test_a_token_signed_with_another_key_is_refused():
    tok = oauth.sign_token({"sub": "dan", "kind": "access",
                            "exp": time.time() + 60}, b"\x02" * 32)
    assert oauth.verify_token(tok, KEY) is None


def test_an_expired_token_is_refused():
    tok = oauth.sign_token({"sub": "dan", "kind": "access",
                            "exp": time.time() - 1}, KEY)
    assert oauth.verify_token(tok, KEY) is None


@pytest.mark.parametrize("junk", ["", "nodot", "a.b.c", "!!!.???"])
def test_rubbish_is_refused_rather_than_raising(junk):
    """A malformed Authorization header is a 401, not a 500. Anyone can send
    one."""
    assert oauth.verify_token(junk, KEY) is None


# ------------------------------------------------------------------- the PKCE

def test_the_right_verifier_matches_its_challenge():
    verifier = "a" * 64
    assert oauth.pkce_matches(verifier, oauth.pkce_challenge(verifier))


def test_the_wrong_verifier_does_not():
    assert not oauth.pkce_matches("b" * 64, oauth.pkce_challenge("a" * 64))


# -------------------------------------------------- dynamic client registration

def test_a_redirect_uri_outside_the_allowlist_is_refused_at_register():
    """The predecessor's consent page was a single Allow button with no login,
    and it survived on hostname obscurity. Certificate Transparency logs are
    world-readable and indexed, so the hostname is enumerable within minutes of
    the certificate issuing -- it was never a defence. A stranger who finds the
    host must not be able to complete a flow, which is why this is an allowlist
    *and* a passphrase, not either."""
    s = StubStore()
    with pytest.raises(oauth.Refused, match="redirect_uri"):
        oauth.register_client(s, {"redirect_uris": ["https://evil.example/cb"]},
                              allowlist=ALLOWED)
    assert s.clients == {}, "nothing may be registered on the refused path"


def test_a_registration_survives_a_restart():
    """Not anticipated -- found. ChatGPT caches the client_id from an earlier
    registration and reuses it directly against /authorize rather than
    re-registering, so an in-memory registry orphaned a real client on restart
    and broke reconnection with "Invalid authorization request"."""
    s = StubStore()
    reg = oauth.register_client(s, {"redirect_uris": ALLOWED}, allowlist=ALLOWED)
    assert s.client(reg["client_id"])["redirect_uris"] == ALLOWED


# ------------------------------------------------------------------- the codes

def _code(store, **over):
    args = {"client_id": "c1", "redirect_uri": ALLOWED[0],
            "code_challenge": oauth.pkce_challenge("v" * 64),
            "resource": RESOURCE, "scope": "mcp"}
    args.update(over)
    return oauth.issue_code(store, **args)


def test_a_code_is_good_once(store):
    """Replay is the attack this stops. Consumed on read, in the store, so a
    call site cannot forget to delete it."""
    code = _code(store)
    assert oauth.redeem_code(store, code=code, verifier="v" * 64,
                             redirect_uri=ALLOWED[0], client_id="c1")
    with pytest.raises(oauth.Refused, match="code"):
        oauth.redeem_code(store, code=code, verifier="v" * 64,
                          redirect_uri=ALLOWED[0], client_id="c1")


def test_a_code_redeemed_with_the_wrong_verifier_is_refused(store):
    code = _code(store)
    with pytest.raises(oauth.Refused, match="verifier"):
        oauth.redeem_code(store, code=code, verifier="wrong" * 13,
                          redirect_uri=ALLOWED[0], client_id="c1")


def test_an_expired_code_is_refused(store):
    """60 seconds, matching Claude's own auth-code lifetime expectations."""
    code = _code(store)
    store.codes[code]["expiresAt"] = time.time() - 1
    with pytest.raises(oauth.Refused, match="expired"):
        oauth.redeem_code(store, code=code, verifier="v" * 64,
                          redirect_uri=ALLOWED[0], client_id="c1")


def test_a_code_is_bound_to_its_redirect_uri_and_client(store):
    """Both are part of what was consented to. A code that works from anywhere
    is a code worth stealing."""
    code = _code(store)
    with pytest.raises(oauth.Refused, match="redirect_uri"):
        oauth.redeem_code(store, code=code, verifier="v" * 64,
                          redirect_uri="https://elsewhere.example/cb",
                          client_id="c1")
    code = _code(store)
    with pytest.raises(oauth.Refused, match="client"):
        oauth.redeem_code(store, code=code, verifier="v" * 64,
                          redirect_uri=ALLOWED[0], client_id="someone-else")


# ---------------------------------------------------------------- the consent

@pytest.mark.parametrize("given", [None, "", "wrong", "hunter3"])
def test_consent_without_the_passphrase_is_refused(given):
    """The half the predecessor did not have. The allowlist stops a stranger
    redirecting the code somewhere they control; this stops them completing a
    flow at all."""
    assert not oauth.consent_granted(given, expected="hunter2")


def test_consent_with_the_passphrase_is_granted():
    assert oauth.consent_granted("hunter2", expected="hunter2")


def test_an_unset_passphrase_grants_nothing():
    """A misconfigured deployment must fail closed. If the passphrase is not
    configured, `consent_granted("", expected="")` comparing equal would open
    the flow to everyone who found the hostname in a CT log."""
    assert not oauth.consent_granted("", expected="")
    assert not oauth.consent_granted("anything", expected="")
    assert not oauth.consent_granted(None, expected="")
