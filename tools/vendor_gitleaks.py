"""Write kb/gitleaks.py, the provider token rules kb takes from gitleaks, from a gitleaks source tree at a release.

    python3 tools/vendor_gitleaks.py <gitleaks source tree> <release tag> <sha256 of its source tarball>

It reads the tree's config/gitleaks.toml and LICENSE and runs nothing from the tree. It takes a rule that finds a
token by its own shape, a provider's prefix or structure, and leaves:
- a rule that finds a value by a word near it ("adafruit ... = <32 characters>"), which kb's named values cover and
  which would mask ordinary text by a word;
- the few that read a command, a file or a document around a secret (SKIP);
- a rule whose every example kb's mask already hides whole, which kb knows;
- a rule that matches any of tests/ordinary.py's ordinary texts, which would mask them;
- a rule with no example its own pattern matches.
Each rule's examples are drawn from its own pattern (tests/samples.py), as gitleaks draws its own. The Go regular
expressions become Python's: a flag set inside a group scoped to the rest of that group, "\\z" as "\\Z", POSIX classes
spelled out, what ends a token looked at ahead, a token starting where a word does, and compiled ASCII-only, as Go reads \\w, \\d, \\s and \\b. What it took and left, and why, goes to stdout.
"""
import os
import re
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from kb import mask as kb_mask  # noqa: E402
from tests import ordinary, samples  # noqa: E402

try:
    import tomllib
except ImportError:  # Python 3.9 and 3.10
    from kb._vendor import tomli as tomllib

# Rules that read around a secret, not a token's shape: a command line, a document, a file's markup, a link's host.
SKIP = {
    "curl-auth-header": "reads a curl command line, which kb's Authorization and curl readings cover",
    "curl-auth-user": "reads a curl command line, which kb's curl -u reading covers",
    "kubernetes-secret-yaml": "reads a YAML document around a secret",
    "private-key": "kb reads private keys by their own structure",
    "nuget-config-password": "reads a file's markup around a password",
    "freemius-secret-key": "reads a PHP array around a secret",
    "gitlab-session-cookie": "kb reads cookies by name",
    "sidekiq-sensitive-url": "reads a link's host around a credential, which kb's link reading covers",
    "generic-api-key": "finds any value by a word near it",
}
POSIX = {"[:alnum:]": "A-Za-z0-9", "[:alpha:]": "A-Za-z", "[:digit:]": "0-9", "[:xdigit:]": "0-9A-Fa-f",
         "[:upper:]": "A-Z", "[:lower:]": "a-z", "[:space:]": r" \t\r\n\v\f", "[:word:]": r"\w"}
EXAMPLES = 8
ORDINARY = 6000


def python_regex(go):
    """A Go regular expression as Python's: "(?i)" inside a pattern scoped to the rest of its group, "\\z" as "\\Z",
    and POSIX classes spelled out."""
    for name, members in POSIX.items():
        go = go.replace(name, members)
    go = go.replace(r"\z", r"\Z")
    out, scopes, top, i, in_class = [], [], 0, 0, False
    while i < len(go):
        c = go[i]
        if c == "\\":
            out.append(go[i:i + 2])
            i += 2
            continue
        if in_class:
            in_class = c != "]"
        elif c == "[":
            in_class = True
            if go[i + 1:i + 2] == "]":  # a "]" first in a class is a member
                out.append("[]")
                i += 2
                continue
        elif go.startswith("(?i)", i) and i:
            out.append("(?i:")  # Go's flag holds to the end of the group it is in
            if scopes:
                scopes[-1] += 1
            else:
                top += 1
            i += 4
            continue
        elif c == "(":
            scopes.append(0)
        elif c == ")":
            out.append(")" * scopes.pop())
        out.append(c)
        i += 1
    return "".join(out) + ")" * top


def terminator_ahead(source):
    """`source` with the group it ends in, where that reads what ends a token, a mark or the text's end ("(?:[^\\w-]|\\Z)"),
    looked at ahead instead of read: the same tokens match, and what ends one stays out of what is masked."""
    opens, last, i, in_class = [], None, 0, False
    while i < len(source):
        c = source[i]
        if c == "\\":
            i += 2
            continue
        if in_class:
            in_class = c != "]"
        elif c == "[":
            in_class = True
            i += 2 if source[i + 1:i + 2] == "]" else 1
            continue
        elif c == "(":
            opens.append(i)
        elif c == ")":
            last = (opens.pop(), i)
        i += 1
    if not last or last[1] != len(source) - 1 or not source.startswith("(?:", last[0]):
        return source
    body = source[last[0] + 3:-1]
    if re.search(r"(?:^|\|)(?:\$|\\Z)(?:\||$)", body):
        return source[:last[0]] + "(?=" + body + ")"
    return source


# What a rule may start with that already says where a token starts: a word boundary, the text's start, a lookbehind.
_STARTS = ("\\b", "^", "(?:^|", "(?<")
WORD_START = "(?<![A-Za-z0-9])"


def word_start(source):
    """`source` starting where a word does, as a token does, unless it says where it starts already: gitleaks' Twilio
    rule, "SK" and 32 hex digits, matched inside an ordinary id such as "TASK<hex digest>"."""
    flags = "(?i)" if source.startswith("(?i)") else ""
    body = source[len(flags):]
    return source if body.startswith(_STARTS) else flags + WORD_START + body


def secret_group(rule, pattern, drawn):
    """The group that holds the secret, as gitleaks reads it: the rule's secretGroup, else the first group that holds
    anything. Where that is a part of the token, as a link's groups are, more than the mark or two that stand around a
    token short of the whole match, the whole match, 0."""
    if rule.get("secretGroup"):
        return rule["secretGroup"]
    for text in drawn:
        m = pattern.fullmatch(text)
        first = next((g for g in range(1, pattern.groups + 1) if m.group(g)), 0)
        if first and (m.start(first) - m.start() > 2 or m.end() - m.end(first) > 2):
            return 0
    return 1 if pattern.groups else 0


def secret(m, group):
    """The secret in a match: its group, or for group 1 the first group that holds anything, as gitleaks reads it."""
    if group == 1:
        return next((m.group(g) for g in range(1, m.re.groups + 1) if m.group(g)), "")
    return m.group(group) or ""


def main(tree, tag, digest):
    with open(os.path.join(tree, "config", "gitleaks.toml"), "rb") as fh:
        config = tomllib.load(fh)
    with open(os.path.join(tree, "LICENSE"), encoding="utf-8") as fh:
        licence = fh.read().strip()
    texts = ordinary.texts(ORDINARY, "vendor")
    kb_mask._GITLEAKS = kb_mask._GITLEAKS_AFTER_A_BREAK = []  # kb's mask as it is without the table
    allow = config.get("allowlist", {})
    taken, left = [], []
    for rule in config["rules"]:
        rule_id, go = rule["id"], rule.get("regex", "")
        if rule_id in SKIP:
            left.append((rule_id, SKIP[rule_id]))
            continue
        if not go or r"[\w.-]{0,50}?" in go:
            left.append((rule_id, "finds a value by a word near it"))
            continue
        source = word_start(terminator_ahead(python_regex(go)))
        pattern = re.compile(source, re.ASCII)
        drawn = samples.samples(pattern, EXAMPLES, rule_id)
        group = secret_group(rule, pattern, drawn)
        examples = [secret(pattern.fullmatch(text), group) for text in drawn]
        examples = [e for e in examples if e]
        if not examples:
            left.append((rule_id, "no example its own pattern matches"))
            continue
        if all("[masked]" == kb_mask.mask(example) for example in examples):
            left.append((rule_id, "kb's mask already hides each example whole"))
            continue
        hit = next((t for t in texts for m in pattern.finditer(t) if secret(m, group)), None)
        if hit is not None:
            left.append((rule_id, f"matches ordinary text: {hit[:80]!r}"))
            continue
        allowed = [python_regex(a) for block in rule.get("allowlists", []) for a in block.get("regexes", [])
                   if block.get("regexTarget", "secret") == "secret" and block.get("condition", "OR") == "OR"]
        stopwords = [w for block in rule.get("allowlists", []) for w in block.get("stopwords", [])]
        taken.append((rule_id, source, group, float(rule.get("entropy") or 0),
                      tuple(k.lower() for k in rule.get("keywords", [])), tuple(allowed), tuple(stopwords)))
    write(tag, digest, licence, taken, left, allow)
    print(f"took {len(taken)} of {len(config['rules'])} rules")
    for rule_id, why in left:
        print(f"left {rule_id}: {why}")


def write(tag, digest, licence, taken, left, allow):
    lines = ['"""Provider token rules taken from gitleaks, generated by tools/vendor_gitleaks.py: do not edit by hand.',
             "",
             f"gitleaks {tag}, https://github.com/gitleaks/gitleaks, source tarball sha256 {digest}.",
             f"{len(taken)} of its rules, each a token's own shape; the script says which it left and why.",
             "",
             "Each rule: (id, Python regular expression, ASCII-only, the group holding the secret, the least Shannon",
             "entropy the secret has, keywords one of which a text must hold, lower case, regular expressions and",
             "words that mark the secret as none). ALLOW_REGEXES and STOPWORDS are gitleaks' own for every rule.",
             "",
             "gitleaks' licence:",
             "",
             *licence.splitlines(),
             '"""',
             "",
             f"VERSION = {tag!r}",
             f"SOURCE_SHA256 = {digest!r}",
             f"ALLOW_REGEXES = {tuple(python_regex(a) for a in allow.get('regexes', []))!r}",
             f"STOPWORDS = {tuple(allow.get('stopwords', []))!r}",
             "RULES = ("]
    for rule in taken:
        lines.append(f"    {rule!r},")
    lines.append(")")
    with open(os.path.join(ROOT, "kb", "gitleaks.py"), "w", encoding="utf-8") as fh:
        fh.write("\n".join(lines) + "\n")


if __name__ == "__main__":
    main(*sys.argv[1:4])
