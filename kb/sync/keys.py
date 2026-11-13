"""Device keys, epoch keys, the key ring, recovery codes, pairing secrets and the two programs that do the cryptography.

`age` does every encryption and `ssh-keygen -Y` every signature. Python only derives, tags and encodes.
"""
import hashlib
import hmac
import json
import os
import re
import secrets
import subprocess
import tempfile

from .. import COMMAND

# The name Sync's bytes are built from: key derivation, pack and roster namespaces, account claims and the relay's upload
# headers. The relay reads the headers and every computer the rest as they are, so tests/sync/test_wire.py pins them: a
# rename that changes them must change the relay in the same release, and every sync group starts over.
WIRE_NAME = COMMAND

AGE_RECIPIENT = re.compile(r"age1[a-z0-9]{58}")
AGE_IDENTITY = re.compile(r"AGE-SECRET-KEY-1[0-9A-Z]{58}")
SSH_KEY = re.compile(r"ssh-ed25519 [A-Za-z0-9+/]{68}")
DEVICE_ID = re.compile(r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}")
BECH32 = "qpzry9x8gf2tvdw0s3jn54khce6mua7l"
CROCKFORD = "0123456789ABCDEFGHJKMNPQRSTVWXYZ"
WORDS = """amber anchor apple arrow aspen atlas autumn badge bamboo banner barley basin beacon berry birch blanket
bloom bottle bramble breeze brick bridge brook bucket butter cabin cactus camel candle canyon captain carbon cargo
carpet castle cedar cellar chalk cherry chimney cider cinder circle citrus clover cobalt comet copper coral cotton
cradle crane crater crystal dagger daisy dawn delta desert diamond dolphin dragon drum dune eagle echo ember emerald
falcon feather fern fiddle finch flame flint forest fossil fountain fox galaxy garden garnet ginger glacier globe
granite grape gravel harbor harvest hazel heron hollow honey horizon iris island ivory jade jasmine jelly jungle
juniper kettle kite lagoon lantern lark laurel lava lemon lily linen lizard lotus magnet maple marble meadow melon
meteor mint mirror mist moss mountain mulberry needle nectar nest nickel nutmeg oak oasis ocean olive onyx orbit
orchard otter owl paddle palm panda paper parrot peach pearl pebble pepper pigeon pine planet plum pocket pollen
pond poppy prairie prism pumpkin quartz quiet quill rabbit radish rain raven reef ribbon river robin rocket rose
ruby saddle saffron sage salmon sand sapphire satin season shadow shell silver sketch sky slate snow sparrow spice
spruce squirrel star stone storm stream sugar summit sunset swan tangle temple thistle thunder tiger timber topaz
torch tulip tundra turtle valley velvet violet walnut water willow window winter wolf wren yarrow zebra zephyr acorn
almond badger beetle blossom canoe caramel cashew cloud compass cricket cypress dove fable fjord gecko geyser
glimmer hammock hedge igloo indigo kayak koala ladder lilac llama mango marsh mosaic""".split()


class Refused(Exception):
    """Data from the relay or another computer failed a check."""


class Unaccepted(ValueError):
    """A value the person gave that Sync does not take. Its text is written here and never quotes the value."""


class ToolError(Exception):
    """`age` or `ssh-keygen` is missing, or failed on input this computer made."""


# What to install for each tool this file runs.
INSTALL = {"ssh-keygen": "Install OpenSSH's client: macOS has it, and on Debian or Ubuntu it is `apt install openssh-client`.",
           "age": "Install age from https://age-encryption.org, or with `brew install age` or `apt install age`."}
# The namespace a computer signs its renewal under, so no signature made for git or anything else passes as one. The app
# checks the same string (RENEW_NAMESPACE in its sshsig.ts), which spells the product's name, so a rename changes both.
RENEW_NAMESPACE = f"{COMMAND}-renew@v1"


def _run(args, data=b""):
    try:
        return subprocess.run(args, input=data, capture_output=True)
    except FileNotFoundError:
        raise ToolError(f"{args[0]} is not installed. {INSTALL.get(args[0], '')}".strip()) from None


def write_private(path, data):
    tmp = f"{path}.tmp"
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "wb") as fh:
        fh.write(data)
    os.replace(tmp, path)


def save_json(path, obj):
    write_private(path, json.dumps(obj, indent=1).encode())


def load_json(path, default=None):
    if not os.path.exists(path):
        return default
    with open(path, encoding="utf-8") as fh:
        return json.load(fh)


def derive(secret, label):
    return hmac.new(secret, f"{WIRE_NAME}-sync/v1/".encode() + label.encode(), hashlib.sha256).digest()


def tag(key, data):
    return hmac.new(key, data, hashlib.sha256).digest()


def tag_ok(key, data, mac):
    return hmac.compare_digest(tag(key, data), mac)


def seal(recipients, data):
    for r in recipients:
        if not AGE_RECIPIENT.fullmatch(r):
            raise Refused("not an age X25519 recipient")
    args = ["age"]
    for r in recipients:
        args += ["-r", r]
    proc = _run(args, data)
    if proc.returncode:
        raise ToolError(f"age could not seal: {proc.stderr.decode(errors='replace').strip()}")
    return proc.stdout


def open_file(identity_path, data):
    proc = _run(["age", "-d", "-i", identity_path], data)
    if proc.returncode:
        raise Refused("age refused the ciphertext")
    return proc.stdout


def open_with(identities, data):
    with tempfile.TemporaryDirectory(prefix=f"{COMMAND}-sync-") as tmp:
        path = os.path.join(tmp, "identities")
        write_private(path, "".join(i + "\n" for i in identities).encode())
        return open_file(path, data)


def _polymod(values):
    gen = (0x3B6A57B2, 0x26508E6D, 0x1EA119FA, 0x3D4233DD, 0x2A1462B3)
    chk = 1
    for v in values:
        top = chk >> 25
        chk = (chk & 0x1FFFFFF) << 5 ^ v
        for i in range(5):
            chk ^= gen[i] if (top >> i) & 1 else 0
    return chk


def _to5(data):
    acc = bits = 0
    out = []
    for byte in data:
        acc, bits = (acc << 8) | byte, bits + 8
        while bits >= 5:
            bits -= 5
            out.append((acc >> bits) & 31)
    if bits:
        out.append((acc << (5 - bits)) & 31)
    return out


def age_identity(raw32):
    """A 32-byte secret as an age X25519 identity, so derived values become real age keys."""
    hrp = "age-secret-key-"
    data = _to5(raw32)
    poly = _polymod([ord(c) >> 5 for c in hrp] + [0] + [ord(c) & 31 for c in hrp] + data + [0] * 6) ^ 1
    check = [(poly >> 5 * (5 - i)) & 31 for i in range(6)]
    return (hrp + "1" + "".join(BECH32[d] for d in data + check)).upper()


def new_identity():
    return age_identity(secrets.token_bytes(32))


def recipient_of(identity):
    proc = _run(["age-keygen", "-y"], identity.encode() + b"\n")
    recipient = proc.stdout.decode().strip()
    if proc.returncode or not AGE_RECIPIENT.fullmatch(recipient):
        raise ToolError("age-keygen could not read the identity")
    return recipient


def is_pair(identity, recipient):
    """Whether `recipient` is the public key of `identity`, which came from another computer."""
    proc = _run(["age-keygen", "-y"], identity.encode() + b"\n")
    return proc.returncode == 0 and proc.stdout.decode().strip() == recipient


def make_device_keys(age_path, ssh_path):
    if not os.path.exists(age_path):
        if _run(["age-keygen", "-o", age_path]).returncode:
            raise ToolError("age-keygen could not make the device key")
        os.chmod(age_path, 0o600)
    if not os.path.exists(ssh_path):
        if _run(["ssh-keygen", "-q", "-t", "ed25519", "-N", "", "-C", "", "-f", ssh_path]).returncode:
            raise ToolError("ssh-keygen could not make the signing key")
        os.chmod(ssh_path, 0o600)


def device_recipient(age_path):
    proc = _run(["age-keygen", "-y", age_path])
    recipient = proc.stdout.decode().strip()
    if proc.returncode or not AGE_RECIPIENT.fullmatch(recipient):
        raise ToolError("age-keygen could not read the device key")
    return recipient


def ssh_public(ssh_path):
    with open(ssh_path + ".pub", encoding="utf-8") as fh:
        return " ".join(fh.read().split()[:2])


def renew_proof(ssh_path, device_code, device_id):
    """This computer's signature, for the app, that it holds the private key of the device it signs in again: over the
    device code it polls with and that device's id, in the renewal's own namespace."""
    return sign(ssh_path, RENEW_NAMESPACE, f"{device_code}\n{device_id}\n".encode()).decode("ascii")


def sign(ssh_path, namespace, data):
    proc = _run(["ssh-keygen", "-Y", "sign", "-q", "-f", ssh_path, "-n", namespace], data)
    if proc.returncode:
        raise ToolError(f"ssh-keygen could not sign: {proc.stderr.decode(errors='replace').strip()}")
    return proc.stdout


def verify(principal, ssh_key, namespace, data, signature):
    """True when `signature` is `principal`'s over `data`. The principal is a device id, never a label."""
    if not DEVICE_ID.fullmatch(principal) or not SSH_KEY.fullmatch(ssh_key):
        return False
    with tempfile.TemporaryDirectory(prefix=f"{COMMAND}-sync-") as tmp:
        signers, sig = os.path.join(tmp, "allowed_signers"), os.path.join(tmp, "sig")
        write_private(signers, f'{principal} namespaces="{namespace}" {ssh_key}\n'.encode())
        write_private(sig, signature)
        proc = _run(["ssh-keygen", "-Y", "verify", "-f", signers, "-I", principal, "-n", namespace, "-s", sig], data)
        return proc.returncode == 0


def _encode(data, nchars):
    num = int.from_bytes(data, "big") << (nchars * 5 - len(data) * 8)
    return "".join(CROCKFORD[(num >> (5 * (nchars - 1 - k))) & 31] for k in range(nchars))


def _decode(chars, nbytes):
    num = 0
    for c in chars:
        num = num * 32 + CROCKFORD.index(c)
    spare = len(chars) * 5 - nbytes * 8
    if num & ((1 << spare) - 1):
        raise Unaccepted("not a valid code")
    return (num >> spare).to_bytes(nbytes, "big")


def _show(head, chars):
    return "-".join([head] + [chars[i:i + 4] for i in range(0, len(chars), 4)])


def _parse(text, head, nbytes, nchars):
    t = text.strip().upper().replace("-", "").replace(" ", "")
    if not t.startswith(head):
        raise Unaccepted("not a valid code")
    t = t[len(head):].replace("I", "1").replace("L", "1").replace("O", "0")
    if len(t) != nchars or any(c not in CROCKFORD for c in t):
        raise Unaccepted("not a valid code")
    return _decode(t, nbytes)


_CODE_READ = re.compile(r"VRC1[0-9A-Z]{20,32}|VP1[0-9A-Z]{20,26}")
# The same, from every head, including one inside the characters of the code before it: a code cut short, and a second
# code glued behind it, whose head the first code's read would take as its own characters.
_EACH_CODE = re.compile(r"(?=(VRC1[0-9A-Z]{20,32}|VP1[0-9A-Z]{20,26}))")


# Eight groups of four, with spaces, dashes or a line break between them in any mix, or none. A lookahead, so that a
# group a neighboring word starts never hides the code that begins one group later.
_HEADLESS = re.compile(r"(?<![0-9A-Za-z])(?=([0-9A-Za-z]{4}(?:[ \t\r\n-]{0,3}[0-9A-Za-z]{4}){7})(?![0-9A-Za-z]))")
_CODE_CHARS = frozenset(CROCKFORD + "ILO")


def code_spans(text):
    """Where `text` holds most or all of a recovery or pairing code, read the way `_parse` reads one: upper-cased, with
    every space and dash skipped, and every line break too, since a code can be wrapped. Returns (start, end) pairs in
    `text`."""
    chars, where = _code_stream(text)
    return [(where[m.start()], where[m.end() - 1] + 1) for m in _CODE_READ.finditer("".join(chars))]


def _code_stream(text):
    """`text` upper-cased with every space, dash and line break skipped, as a code is read, and the place in `text` of
    each of its characters."""
    chars, where = [], []
    for i, c in enumerate(text):
        if c not in " -\n\r\t":
            for u in c.upper():
                chars.append(u)
                where.append(i)
    return chars, where


# More than eight groups of four, as two or more codes without their heads run together are: one run, with no break
# between the codes to tell where one ends.
_HEADLESS_RUN = re.compile(r"(?<![0-9A-Za-z])[0-9A-Za-z]{4}(?:[ \t\r\n-]{0,3}[0-9A-Za-z]{4}){8,}(?![0-9A-Za-z])")


def _a_code_body(chars):
    """Whether 32 characters are what a code written without its head holds (`headless_spans`): code characters, a digit
    and a letter past F."""
    chars = chars.upper()
    return chars.isascii() and set(chars) <= _CODE_CHARS and any(c.isdigit() for c in chars) and any(
        c.isalpha() and c > "F" for c in chars)


def headless_spans(text):
    """Where `text` may hold a recovery code written without its VRC1 head: 32 code characters in any case, run
    together or in the printed groups of four. A code holds a digit and a letter past F, all but about one in 150,000
    do, so a hex run, such as a hash or a uuid without its dashes, and a run of words, are never taken for one. A run
    of 16, 24 or more groups is two or more codes without heads run together, when it is all in one case, as a code
    prints, and each 32 of it is a code: text with both cases, base64's or a word's, is not."""
    out = []
    for m in _HEADLESS.finditer(text):
        if _a_code_body(re.sub(r"[ \t\r\n-]", "", m.group(1))):
            out.append((m.start(), m.start() + len(m.group(1))))
    for m in _HEADLESS_RUN.finditer(text):
        chars = re.sub(r"[ \t\r\n-]", "", m.group())
        if len(chars) % 32 == 0 and chars in (chars.upper(), chars.lower()) and all(
                _a_code_body(chars[i:i + 32]) for i in range(0, len(chars), 32)):
            out.append((m.start(), m.end()))
    return out


# A code's head, as _CODE_READ reads it from text with its breaks skipped: a code is known by its head.
_HEAD_READ = re.compile(r"(?i)v[ \t\r\n-]*(?:r[ \t\r\n-]*c|p)[ \t\r\n-]*1")
_BARE_WIDTH = 32  # the characters of a code written without its head
_BARE_LEAST = 20  # and the fewest of one that a first code cut short leaves: its read takes up to 12 of the second's
_GAP = 4  # the widest gap in the text between two characters of one code: a break of up to three characters


def glued_code_spans(text):
    """`code_spans`, with each span grown over what is glued to it. A code with a letter or digit touching it, on either
    side, has something glued on: the whole run of letters and digits is part of it. And a run of fewer than 32 that
    the groups past the next break bring to 32, or to 20 or more when the first code is cut short and its read has
    taken the front of the second, is a second code, written without its head and in two pieces, so those groups
    are too. A second code's last groups past a space would otherwise stay, since kb's mask hides the
    run glued to a hidden code only up to the break and what is left is too short to read as a code. A code with
    nothing glued to it comes back as `code_spans` has it."""
    if not _HEAD_READ.search(text):
        return code_spans(text)
    chars, where = _code_stream(text)
    alnum = [c.isascii() and c.isalnum() for c in chars]
    size = len(chars)
    # Where the run of letters and digits touching a character stops (`stop`) and starts (`start`), found once, so a long
    # run with a head at every few characters is read in the time it takes to read it, not once for each head.
    stop = [size] * (size + 1)
    for i in range(size - 1, -1, -1):
        stop[i] = stop[i + 1] if i and alnum[i] and where[i] == where[i - 1] + 1 else i
    start = list(range(size))
    for i in range(1, size):
        start[i] = start[i - 1] if alnum[i - 1] and where[i] == where[i - 1] + 1 else i
    spans = []
    for m in _EACH_CODE.finditer("".join(chars)):
        lo, hi = m.start(1), stop[m.end(1)]  # the run touching its end
        total, j = hi - m.end(1), hi
        while 0 < total < _BARE_WIDTH and j < size and alnum[j] and where[j] - where[j - 1] <= _GAP:
            k = stop[j + 1]  # a group past a break, up to the next
            total, j = total + k - j, k
            hi = j if _BARE_LEAST <= total <= _BARE_WIDTH else hi
        lo = start[lo]  # and the run touching its front
        total, j = m.start(1) - lo, lo
        while 0 < total < _BARE_WIDTH and j > 0 and alnum[j - 1] and where[j] - where[j - 1] <= _GAP:
            k = start[j - 1]
            total, j = total + j - k, k
            lo = j if total == _BARE_WIDTH else lo
        spans.append((where[lo], where[hi - 1] + 1))
    return spans


def has_glued_code(text):
    """Whether `text` holds a code with a letter or digit touching it, which `glued_code_spans` grows."""
    return bool(_HEAD_READ.search(text)) and glued_code_spans(text) != code_spans(text)


def code_mask_spans(text, headless=False, glued=False):
    """The spans `mask_codes` replaces: every code `code_spans` finds, and with `headless`, every one `headless_spans`
    finds, those that touch or overlap merged. With `glued`, the codes as `glued_code_spans` grows them."""
    merged = []
    for start, end in sorted((glued_code_spans(text) if glued else code_spans(text))
                             + (headless_spans(text) if headless else [])):
        if merged and start <= merged[-1][1]:
            merged[-1] = (merged[-1][0], max(end, merged[-1][1]))
        else:
            merged.append((start, end))
    return merged


def mask_codes(text, headless=False):
    """`text` with every span `code_mask_spans` names replaced by "[masked]"."""
    pieces, last = [], 0
    for start, end in code_mask_spans(text, headless):
        pieces += (text[last:start], "[masked]")
        last = end
    return "".join(pieces) + text[last:]


def new_recovery_code():
    secret = secrets.token_bytes(20)
    return _show("VRC1", _encode(secret, 32)), secret


def parse_recovery_code(text):
    return _parse(text, "VRC1", 20, 32)


def new_pairing_code():
    secret = secrets.token_bytes(16)
    return _show("VP1", _encode(secret, 26)), secret


def parse_pairing_code(text):
    return _parse(text, "VP1", 16, 26)


def recovery_keys(secret):
    """The recovery age identity and tag key that a recovery code stands for."""
    return age_identity(derive(secret, "recovery-age")), derive(secret, "recovery-tag")


class PairingKeys:
    def __init__(self, secret):
        self.slot_id = derive(secret, "slot")
        self.join_key = derive(secret, "join")
        self.join_identity = age_identity(derive(secret, "join-age"))
        self.welcome_key = derive(secret, "welcome")


def fingerprint_words(device_id, age_recipient, ssh_key):
    digest = hashlib.sha256(f"{device_id}\n{age_recipient}\n{ssh_key}".encode()).digest()
    return " ".join(WORDS[b] for b in digest[:3])


class KeyRing:
    """Every epoch key so far as epoch -> (identity, recipient), plus the recovery recipient, tag key, `since` (the
    roster version the recovery code was made at) and `head` (the roster version named where this ring learned it)."""

    def __init__(self, epochs, recovery):
        self.epochs = dict(epochs)
        self.recovery = {"head": recovery["since"], **recovery}

    @property
    def newest(self):
        return max(self.epochs)

    def identity(self, epoch):
        return self.epochs[epoch][0]

    def recipient(self, epoch):
        return self.epochs[epoch][1]

    def with_epoch(self, epoch, identity):
        return KeyRing({**self.epochs, epoch: (identity, recipient_of(identity))}, self.recovery)

    def with_recovery(self, recipient, tag_key, since):
        return KeyRing(self.epochs, {"recipient": recipient, "tag": tag_key, "since": since, "head": since})

    def upto(self, epoch):
        return KeyRing({e: pair for e, pair in self.epochs.items() if e <= epoch}, self.recovery)

    def merged(self, other, head=None):
        """Adds the epochs this ring lacks; a key it already holds never changes. `other`'s recovery key replaces this
        one only when `head`, the roster version its envelope names, is not older than this one's, so a replayed older
        envelope cannot bring back an old recovery key."""
        recovery = self.recovery
        if head is not None and head >= self.recovery["head"]:
            recovery = {**other.recovery, "head": head}
        return KeyRing({**other.epochs, **self.epochs}, recovery)

    def to_json(self):
        return {"epochs": {str(e): list(pair) for e, pair in sorted(self.epochs.items())},
                "recovery": {"recipient": self.recovery["recipient"], "tag": self.recovery["tag"].hex(),
                             "since": self.recovery["since"], "head": self.recovery["head"]}}

    @classmethod
    def from_json(cls, obj):
        try:
            epochs = {int(e): (i, r) for e, (i, r) in obj["epochs"].items()}
            rec = obj["recovery"]
            recovery = {"recipient": rec["recipient"], "tag": bytes.fromhex(rec["tag"]), "since": int(rec["since"]),
                        "head": int(rec.get("head", rec["since"]))}
        except (KeyError, TypeError, ValueError, AttributeError):
            raise Refused("bad key ring") from None
        good = epochs and all(e >= 1 and AGE_IDENTITY.fullmatch(i) and AGE_RECIPIENT.fullmatch(r)
                              for e, (i, r) in epochs.items())
        if not good or not AGE_RECIPIENT.fullmatch(recovery["recipient"]) or len(recovery["tag"]) != 32:
            raise Refused("bad key ring")
        return cls(epochs, recovery)
