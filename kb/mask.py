import re

_NB = r"(?<![A-Za-z0-9])"
# A secret's value ends at a space, tab or line break, and the space around its "=" is spaces and tabs: \s would also
# take U+2028, U+2029, U+0085, a vertical tab or a form feed, which end a value early or make "password=\u2028password"
# look like a placeholder.
SECRET_PATTERNS = [
    re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----[A-Za-z0-9+/=\s]*(?:-----END [A-Z ]*PRIVATE KEY-----)?"),
    re.compile(_NB + r"sk-(?:ant-|proj-|or-v1-)?[A-Za-z0-9_-]{20,}"),
    re.compile(_NB + r"(?:sk|rk|pk)_(?:live|test)_[A-Za-z0-9]{16,}"),
    re.compile(_NB + r"(?:gh[pousr]_[A-Za-z0-9]{20,}|github_pat_[A-Za-z0-9_]{20,})"),
    re.compile(_NB + r"xox[abprs]-[A-Za-z0-9-]{10,}"),
    re.compile(_NB + r"(?:AKIA|ASIA)[0-9A-Z]{16}(?![A-Za-z0-9])"),
    re.compile(_NB + r"(?:AIza[0-9A-Za-z_-]{35}|ya29\.[0-9A-Za-z_-]{20,}|GOCSPX-[A-Za-z0-9_-]{20,})"),
    re.compile(_NB + r"(?:(?:hf|gsk|npm|ntn|sbp)_[A-Za-z0-9]{30,}|lin_api_[A-Za-z0-9]{30,}|xai-[A-Za-z0-9]{40,}|"
               r"tskey-[a-z]+-[A-Za-z0-9-]{10,}|glpat-[A-Za-z0-9_-]{20,}|whsec_[A-Za-z0-9+/=]{20,}|fc-[a-f0-9]{32}|"
               r"st\.[A-Za-z0-9-]{20,}\.[A-Za-z0-9]{20,}\.[A-Za-z0-9]{20,})"),
    re.compile(r"(?<![\d])\d{8,10}:[A-Za-z0-9_-]{35}(?![A-Za-z0-9_-])"),
    re.compile(r"https://(?:[\w-]+\.)?discord(?:app)?\.com/api/webhooks/\d+/[\w-]+"),
    re.compile(r"https://hooks\.slack\.com/(?:services|workflows|triggers)/[\w/-]+"),
    re.compile(_NB + r"eyJ[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}"),
    re.compile(r"(?i)\bbearer\s+[A-Za-z0-9._~+/-]{16,}=*"),
    re.compile(r"(?i)\bbasic\s+(?=[A-Za-z0-9+/]*[0-9+/=])[A-Za-z0-9+/]{12,}={0,2}"),
]
PREFIXED_SECRETS = [
    re.compile(r"(?i)(\b(?:set-)?cookie[\"']?\s*:\s*)[^\"'\n]{8,}"),
    re.compile(r"([a-z][a-z0-9+.-]*://[^/ \t\n\r:@'\"]+:)[^/ \t\n\r@'\"]{3,}(?=@)"),
    re.compile(r"(\bcurl\b[^\n|;]*?\s(?:-u|--user)\s+['\"]?[^ \t\n\r:'\"]+:)[^ \t\n\r'\"]+"),
]
KEYED_SECRET = re.compile(
    r"(?i)(?<![A-Za-z0-9_.-])("
    r"[A-Za-z0-9_.-]*?(?:api[_-]?key|access[_-]?key|private[_-]?key|secret|token|credentials?|passw(?:or)?d|passcode|"
    r"session[_-]?(?:token|key|secret|cookie)|bw_session)[A-Za-z0-9_-]*?"
    r"|[A-Za-z0-9]+(?:[_-][A-Za-z0-9]+)*[_-](?:pass|pwd|pw)"
    r")(?![A-Za-z0-9_.-])"
    r"((?:[\"'])?[ \t]*(?:[:=]|=>|\bis\b)[ \t]*[\"']?)([^ \t\n\r\"'`,;<>)\]}]+)")
ENV_KEY_SECRET = re.compile(r"(?<![A-Za-z0-9_])([A-Z][A-Z0-9_]*_KEY)(?![A-Za-z0-9_])((?:[\"'])?[ \t]*[:=][ \t]*[\"']?)"
                            r"([^ \t\n\r\"'`,;<>)\]}]+)")
PASSWORD_NAME = re.compile(r"(?i)passw(?:or)?d|passcode|bw_session|[_-](?:pass|pwd|pw)$")
PLACEHOLDERS = {"string", "str", "none", "null", "nil", "true", "false", "undefined", "required", "optional",
                "xxx", "xxxx", "****", "...", "redacted", "<redacted>", "secret", "password", "token"}
REFERENCE = re.compile(r"^(?:[$%{<(\[*/~]|\.\.?/|os\.|process\.|env\.|getenv|secrets\.|vars\.|self\.|this\.|[a-z]+://)|"
                       r"^[A-Za-z_]+(?:\.[A-Za-z_]+)+$|^[A-Z][A-Z_]{2,}$|^[A-Z][A-Z0-9]*(?:_[A-Z0-9]+)+$|\(")


def _mask_keyed(m):
    name, sep, value = m.groups()
    if value.lower() in PLACEHOLDERS or REFERENCE.search(value):
        return m.group(0)
    if PASSWORD_NAME.search(name):
        word = value.rstrip(".!?")
        looks_secret = len(value) >= 4 and ("is" not in sep.lower() or re.search(r"[^A-Za-z]", word))
    else:
        mixed = re.search(r"\d", value) and re.search(r"[A-Za-z]", value)
        looks_secret = (len(value) >= 16 and not value.isdigit()) or (len(value) >= 8 and mixed)
    return f"{name}{sep}[masked]" if looks_secret else m.group(0)


def mask(text):
    for pattern in SECRET_PATTERNS:
        text = pattern.sub("[masked]", text)
    for pattern in PREFIXED_SECRETS:
        text = pattern.sub(lambda m: m.group(1) + "[masked]", text)
    return ENV_KEY_SECRET.sub(_mask_keyed, KEYED_SECRET.sub(_mask_keyed, text))


def clean(text):
    return text.encode("utf-8", "replace").decode("utf-8")


AUTH_NOISE = re.compile(
    r"(?i)(verification code|security code|confirmation code|one[- ]time (?:pass)?code|\botp\b|"
    r"2-step|two[- ]factor|\b2fa\b|sign[- ]in code|login code|log-in code|passcode is|your [\w ]{0,20}passcode|"
    r"magic link|"
    r"reset (?:your )?password|password reset|forgot (?:your )?password|your code is|"
    r"is your [\w ]{0,20}code|use this code|sign in to your account)")


def is_auth_noise(text):
    return bool(AUTH_NOISE.search(text[:600]))


# A code of 4 to 8 digits standing alone, not part of a date, version, decimal or path.
CODE = re.compile(r"(?<![\d./-])\d{4,8}(?![\d/-]|[.,]\d)")


def is_auth_code(text):
    """A message that hands over a code: auth wording and the code itself. Agent sessions drop such turns, but keep
    turns that only talk about codes, as when someone builds a sign-in flow."""
    return is_auth_noise(text) and bool(CODE.search(text[:600]))
