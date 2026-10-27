"""Plug-ins that run an agent CLI the user has installed and signed in to, one process per call. The CLI uses its own
sign-in; nothing here reads, relays or stores it.

Each call runs in a new temp folder, with the variables that would send the CLI to a paid API key, another server or the
agent session that started kb removed, and with KB_NESTED set, so the user's own prompt hook does not run the check
again inside the call. The CLIs' options that keep no session or transcript are on.
"""
import atexit
import os
import re
import shutil
import signal
import subprocess
import tempfile
from dataclasses import dataclass
from typing import Callable, NamedTuple, Optional
from urllib.parse import quote

from kb.ai import Busy, NotReady, PluginSpec, Traits

NESTED_ENV = "KB_NESTED"
# Session sources leave out sessions whose working folder starts with this, in case a CLI keeps one anyway.
WORKDIR_PREFIX = "kb-ai-"
# A CLI starts in seconds, so the check waits longer and sends no duplicate call.
TRAITS = Traits(check_budget=20.0, hedge_after=None, parallel=2, max_prompt_chars=150_000)
SCRUB = re.compile(r"^(ANTHROPIC_|OPENAI_|CLAUDE_CODE_|CLAUDECODE$|XAI_|GROK_SESSION|CODEX_(?!HOME$))")
SIGNED_OUT = re.compile(r"(?i)not (logged|signed) in|(log|sign) ?in again|please (log|sign) ?in|authenticat|unauthori[sz]ed|"
                        r"session expired|token (has )?expired")
_running = set()


@dataclass(frozen=True)
class CliOptions:
    binary: str = ""    # default: the program found on PATH
    model: str = ""     # default: the CLI's own small model, or its default


class Call(NamedTuple):
    argv: list
    stdin: str
    env: dict
    answer_file: Optional[str] = None   # where the CLI writes its answer, when stdout holds other output


class CliSpec(NamedTuple):
    name: str
    label: str
    program: str
    build: Callable[[str, CliOptions, object, str], Call]   # binary, options, prompt, workdir
    default_model: str = "default"
    cleanup: Optional[Callable[[str], None]] = None          # removes what the CLI left for this workdir
    max_prompt_chars: int = 150_000
    hidden: bool = False


class CliModel:
    def __init__(self, spec, options):
        self.spec, self.options = spec, options
        self.name = spec.name
        self.model = options.model or spec.default_model
        self.traits = TRAITS._replace(max_prompt_chars=spec.max_prompt_chars)

    def ask(self, prompt, timeout):
        binary = self.options.binary or shutil.which(self.spec.program)
        if not binary or not os.access(binary, os.X_OK):
            raise NotReady(f"{self.spec.program} is not installed here; install it, or set binary under [ai]")
        workdir = tempfile.mkdtemp(prefix=WORKDIR_PREFIX)
        try:
            call = self.spec.build(binary, self.options, prompt, workdir)
            output = run(call, workdir, timeout, self.spec.program)
            if call.answer_file:
                try:
                    with open(call.answer_file, encoding="utf-8") as fh:
                        output = fh.read()
                except OSError:
                    raise Busy(f"{self.spec.program} wrote no answer") from None
            return output
        finally:
            if self.spec.cleanup:
                self.spec.cleanup(workdir)
            shutil.rmtree(workdir, ignore_errors=True)


def run(call, workdir, timeout, program):
    env = {k: v for k, v in os.environ.items() if not SCRUB.match(k)}
    env.update(call.env)
    env[NESTED_ENV] = "1"
    proc = subprocess.Popen(call.argv, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                            cwd=workdir, env=env, start_new_session=True, text=True, encoding="utf-8",
                            errors="replace")
    _running.add(proc)
    try:
        out, err = proc.communicate(call.stdin, timeout=max(timeout, 0.1))
    except subprocess.TimeoutExpired:
        _kill(proc)
        raise Busy(f"{program} gave no answer in {timeout:.0f} s") from None
    except BaseException:
        _kill(proc)
        raise
    finally:
        _running.discard(proc)
    if proc.returncode:
        tail = (err.strip() or out.strip())[-300:]
        raise (NotReady if SIGNED_OUT.search(tail) else Busy)(f"{program} exited {proc.returncode}: {tail}")
    return out


def _kill(proc, reap=True):
    """Stops the CLI and everything it started, so a call that ran out of time leaves nothing running."""
    try:
        os.killpg(proc.pid, signal.SIGKILL)
    except OSError:
        pass
    if reap:
        proc.communicate()


atexit.register(lambda: [_kill(proc, reap=False) for proc in list(_running)])


def _claude(binary, o, prompt, workdir):
    return Call([binary, "-p", "--model", o.model or "haiku", "--tools", "", "--no-session-persistence",
                 "--system-prompt", prompt.system, "--strict-mcp-config", "--disable-slash-commands",
                 "--output-format", "text"], prompt.user, {})


def _codex(binary, o, prompt, workdir):
    answer = os.path.join(workdir, "answer.txt")
    argv = [binary, "exec", "--ephemeral", "--skip-git-repo-check", "--ignore-user-config", "--sandbox", "read-only",
            "--color", "never", "-c", "model_reasoning_effort=low", "--output-last-message", answer]
    if o.model:
        argv += ["--model", o.model]
    return Call(argv + ["-"], f"{prompt.system}\n\n{prompt.user}", {}, answer)


def _grok(binary, o, prompt, workdir):
    prompt_file = os.path.join(workdir, "prompt.txt")
    with open(prompt_file, "w", encoding="utf-8") as fh:
        fh.write(prompt.user)
    argv = [binary, "--prompt-file", prompt_file, "--rules", prompt.system, "--output-format", "plain",
            "--max-turns", "1", "--disable-web-search", "--no-auto-update"]
    if o.model:
        argv += ["--model", o.model]
    return Call(argv, "", {})


def _grok_cleanup(workdir):
    """Grok keeps every session in a folder named after its working folder; this one is the call's own."""
    shutil.rmtree(os.path.join(os.path.expanduser("~/.grok/sessions"), quote(os.path.realpath(workdir), safe="")),
                  ignore_errors=True)


def _copilot(binary, o, prompt, workdir):
    home = os.path.join(workdir, "copilot-home")
    os.makedirs(home)
    argv = [binary, "-p", f"{prompt.system}\n\n{prompt.user}", "-s", "--no-ask-user"]
    if o.model:
        argv += ["--model", o.model]
    return Call(argv, "", {"COPILOT_HOME": home})


def _cursor(binary, o, prompt, workdir):
    argv = [binary, "-p", f"{prompt.system}\n\n{prompt.user}", "--output-format", "text"]
    if o.model:
        argv += ["--model", o.model]
    return Call(argv, "", {})


SPECS = (
    CliSpec("claude-code", "Your own Claude Code (claude -p, your login)", "claude", _claude, default_model="haiku"),
    CliSpec("chatgpt", "Sign in with ChatGPT (codex exec, your login)", "codex", _codex),
    CliSpec("grok", "Grok (grok -p, your login)", "grok", _grok, cleanup=_grok_cleanup),
    # The prompt goes on the command line, which Linux caps at 128 KiB for one argument.
    CliSpec("copilot", "GitHub Copilot (copilot -p, your login)", "copilot", _copilot, max_prompt_chars=100_000),
    # Off until Cursor confirms its rules allow this use; `allow_unconfirmed = true` under [ai] turns it on.
    CliSpec("cursor", "Cursor (cursor-agent -p, your login)", "cursor-agent", _cursor, max_prompt_chars=100_000,
            hidden=True),
)
PLUGINS = tuple(PluginSpec(spec.name, spec.label, CliOptions, lambda o, spec=spec: CliModel(spec, o), spec.hidden)
                for spec in SPECS)
