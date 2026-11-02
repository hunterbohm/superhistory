"""Ordinary text an agent session, a note or a mail holds, near secrets' words and shapes but holding none: ids and
hashes of every kind, public keys, encoded data, names, paths, links, log lines and code. mask() keeps all of it, and
no rule kb takes from another rule set may match any of it (tests/test_gitleaks.py)."""
import base64
import random
import string

LOWER, UPPER, DIGITS = string.ascii_lowercase, string.ascii_uppercase, string.digits
HEX = "0123456789abcdef"
WORDS = ("plan", "ledger", "walrus", "budget", "pelican", "deploy", "review", "invoice", "harbour", "garden", "release",
         "token", "secret", "password", "key", "session", "auth", "vault", "sentry", "shopify", "sendgrid", "stripe",
         "pypi", "github", "gitlab", "slack", "notion", "linear", "grafana", "heroku", "databricks", "planetscale",
         "doppler", "pulumi", "postman", "twilio", "square", "facebook", "openai", "anthropic", "huggingface")


def run(r, alphabet, n):
    return "".join(r.choice(alphabet) for _ in range(n))


# Words that name no secret, for a name a value is given to, which a secret's word would make a secret's name.
PLAIN = WORDS[:11] + WORDS[17:]


def word(r, words=WORDS):
    return r.choice(words)


def ident(r, words=WORDS):
    """A code identifier: camel case, snake case, a constant or a dotted name."""
    words = [word(r, words) for _ in range(r.randint(2, 5))]
    return r.choice((lambda: words[0] + "".join(w.title() for w in words[1:]), lambda: "_".join(words),
                     lambda: "_".join(words).upper(), lambda: ".".join(words), lambda: "-".join(words)))()


def b64(r, n):
    return base64.b64encode(bytes(r.getrandbits(8) for _ in range(n))).decode()


SHAPES = (
    lambda r: f"commit {run(r, HEX, 40)}", lambda r: f"git checkout {run(r, HEX, 7)}",
    lambda r: f"Merge: {run(r, HEX, 7)} {run(r, HEX, 7)}", lambda r: f"sha256:{run(r, HEX, 64)}",
    lambda r: f"{run(r, HEX, 64)}  dist/kb-1.{r.randint(0, 9)}.tar.gz", lambda r: f"md5 {run(r, HEX, 32)}",
    lambda r: f"trace_id={run(r, HEX, 32)} span_id={run(r, HEX, 16)}",
    lambda r: "-".join(run(r, HEX, n) for n in (8, 4, 4, 4, 12)),
    lambda r: f'"integrity": "sha512-{b64(r, 64)}"', lambda r: f'integrity="sha384-{b64(r, 48)}"',
    lambda r: f"data:image/png;base64,iVBORw0KGgo{b64(r, 60)}",
    lambda r: f"ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAI{b64(r, 32)[:43]} sam@studio",
    lambda r: f"ssh-rsa AAAAB3NzaC1yc2EAAAADAQABAAABAQ{b64(r, 180)} sam@studio",
    lambda r: f"ami-{run(r, HEX, 17)} sg-{run(r, HEX, 17)} subnet-{run(r, HEX, 17)} vpc-{run(r, HEX, 17)}",
    lambda r: f"arn:aws:iam::{run(r, DIGITS, 12)}:role/{ident(r)}",
    lambda r: f"arn:aws:s3:::{word(r)}-{word(r)}-{run(r, DIGITS, 4)}/{ident(r)}.json",
    lambda r: f"pod/{word(r)}-{run(r, HEX, 10)}-{run(r, LOWER + DIGITS, 5)} Running",
    lambda r: f"v{r.randint(0, 9)}.{r.randint(0, 40)}.{r.randint(0, 99)}-{r.randint(1, 99)}-g{run(r, HEX, 7)}",
    lambda r: f"channel C0{run(r, UPPER + DIGITS, 8)} user U0{run(r, UPPER + DIGITS, 8)}",
    lambda r: f"cus_{run(r, LOWER + UPPER + DIGITS, 14)} pi_{run(r, LOWER + UPPER + DIGITS, 24)}",
    lambda r: f"msg_01{run(r, LOWER + UPPER + DIGITS, 22)} chatcmpl-{run(r, LOWER + UPPER + DIGITS, 29)}",
    lambda r: f"req_{run(r, LOWER + DIGITS, 24)} file-{run(r, LOWER + UPPER + DIGITS, 24)}",
    lambda r: f"01{run(r, '0123456789ABCDEFGHJKMNPQRSTVWXYZ', 24)}",
    lambda r: f"{ident(r)}({ident(r)}, {r.randint(0, 99)})", lambda r: f"const {ident(r, PLAIN)} = {ident(r)}.{ident(r)}();",
    lambda r: f"s.{ident(r)}{ident(r).title()};", lambda r: f"import {ident(r)} from '{word(r)}-{word(r)}'",
    lambda r: f"API-{'_'.join(word(r).upper() for _ in range(3))}", lambda r: f"SKU {run(r, UPPER + DIGITS, 12)}",
    lambda r: f"https://{word(r)}.example.com/{ident(r)}?id={run(r, DIGITS, 8)}&page={r.randint(1, 9)}",
    lambda r: f"~/vault/notes/{word(r)}-{word(r)}-{r.randint(2020, 2030)}.md",
    lambda r: f"/home/sam/code/{word(r)}/{ident(r)}.py:{r.randint(1, 900)}",
    lambda r: f"2026-11-0{r.randint(1, 9)}T{r.randint(10, 23)}:{r.randint(10, 59)}:00Z INFO {word(r)} took "
              f"{r.randint(1, 900)}ms",
    lambda r: f"the {word(r)} {word(r)} for {word(r)} is due on {r.randint(1, 28)} November",
    lambda r: f"rotate the {word(r)} {word(r)} after the {word(r)} review", lambda r: f"#{run(r, HEX, 6)}",
    lambda r: f"{word(r)}@{word(r)}.example.org", lambda r: f"pip install {word(r, PLAIN)}-{word(r, PLAIN)}=={r.randint(1, 9)}.0",
    lambda r: f"npm i @{word(r)}/{word(r)}-{word(r)}", lambda r: f"FROM python:3.{r.randint(9, 13)}-slim",
    lambda r: f"max_tokens: {r.randint(256, 200000)}", lambda r: f"{word(r).upper()}_TIMEOUT={r.randint(1, 600)}",
    lambda r: f"fingerprint {':'.join(run(r, HEX, 2) for _ in range(16))}",
    lambda r: f"SHA256:{b64(r, 32)[:43]} sam@studio (ED25519)",
    lambda r: f"{r.choice(('TASK', 'DISK', 'MASK', 'RISK', 'DESK'))}{run(r, HEX, 32)}",
    lambda r: f"PWD=/{word(r, PLAIN)}{r.randint(1, 99)} OLDPWD=~/{word(r, PLAIN)}{r.randint(1, 9)}",
    lambda r: f"opt -passes={word(r, PLAIN)}-{word(r, PLAIN)} pass={word(r, PLAIN)}-{word(r, PLAIN)}",
    lambda r: '{"Key":"%s","Value":"%s-%d"}' % (r.choice(("Name", "Env", "Team", "Owner", "Project")), word(r, PLAIN),
                                                r.randint(1, 99)),
    lambda r: '{"key":"%s","value":"https://%s.example.com/%s"}' % (ident(r, PLAIN), word(r, PLAIN), word(r, PLAIN)),
    lambda r: '{"name":"%s","value":"%s"}' % (r.choice(("Accept", "Content-Type", "User-Agent", "Cache-Control")),
                                              r.choice(("text/html", "no-cache", "application/json", "Mozilla/5.0"))),
    lambda r: '["%s","%s %s"]' % (r.choice(("cookie", "Cookie", "password", "token", "authorization")), word(r, PLAIN),
                                  word(r, PLAIN)),
    lambda r: f"{r.choice('soxe')}.{ident(r, PLAIN)}{r.randint(1, 2099)} = {r.choice(('true', 'null', '0'))};",
    lambda r: f"<token>{r.choice(('internationalization', 'counterrevolutionaries', 'responsibilities'))}</token>",
    lambda r: "<tokens>%s</tokens>" % "".join(
        f"<token>{r.choice(('HTTP', 'UTF8', 'JSON', 'IPv6', 'Base64', ''))}"
        f"{''.join(word(r, PLAIN).title() for _ in range(r.randint(1, 3)))}{r.randint(0, 2099)}</token>"
        for _ in range(r.randint(1, 3))),
    lambda r: "[masked]%s%s.md" % (r.choice(("\x1b[0m", "\x9b0m", "\\u001b[0m", "\\x1b[0m", "\\033[0m", "\x7f")),
                                   r.choice(("README", "CHANGELOG", "notes", "plan-v2"))),
)


def text(r):
    """A few ordinary pieces, joined as a line, a list or a paragraph joins them."""
    return r.choice((" ", "\n", ", ", " | ", "; ")).join(r.choice(SHAPES)(r) for _ in range(r.randint(1, 5)))


def texts(n, seed="ordinary"):
    return [text(random.Random(f"{seed}:{i}")) for i in range(n)]
