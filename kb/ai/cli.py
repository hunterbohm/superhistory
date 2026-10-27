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

from kb.ai import Busy, NotReady, PluginSpec, Refused, Traits

NESTED_ENV = "KB_NESTED"
WORKDIR_PREFIX = "kb-ai-"
TRAITS = Traits(check_budget=20.0, hedge_after=None, parallel=2, max_prompt_chars=150_000)
# Variables that would point a CLI at a paid key, another server or the agent session that started kb. Any *_API_KEY or
# *_AUTH_TOKEN goes too: a call runs with no tools or MCP servers, so nothing in it needs one.
SCRUB = re.compile(r"^(ANTHROPIC_|OPENAI_|CLAUDE_CODE_|CLAUDECODE$|XAI_|GROK_SESSION|CODEX_(?!HOME$))|_API_KEY$|_AUTH_TOKEN$")
SIGNED_OUT = re.compile(r"(?i)not (logged|signed) in|(log|sign) ?in again|please (log|sign) ?in|authenticat|unauthori[sz]ed|"
                        r"session expired|token (has )?expired")
_running = set()
# Linux caps one command-line argument at 128 KiB. A character takes up to 4 bytes, and the system prompt needs room.
ARG_BYTES = 128 * 1024 - 1024
ARG_CHARS = 30_000
_in_flight = {}


@dataclass(frozen=True)
class CliOptions:
    binary: str = ""
    model: str = ""


class Call(NamedTuple):
    argv: list
    stdin: str
    env: dict
    answer_file: Optional[str] = None


class CliSpec(NamedTuple):
    name: str
    label: str
    program: str
    build: Callable[[str, CliOptions, object, str], Call]
    default_model: str = "default"
    cleanup: Optional[Callable[[str], None]] = None
    max_prompt_chars: int = 150_000
    check_budget: float = TRAITS.check_budget
    hidden: bool = False


class CliModel:
    def __init__(self, spec, options):
        self.spec, self.options = spec, options
        self.name = spec.name
        self.model = options.model or spec.default_model
        self.traits = TRAITS._replace(max_prompt_chars=spec.max_prompt_chars, check_budget=spec.check_budget)

    def ask(self, prompt, timeout):
        binary = self.options.binary or shutil.which(self.spec.program)
        if not binary or not os.access(binary, os.X_OK):
            raise NotReady(f"{self.spec.program} is not installed here; install it, or set binary under [ai]")
        workdir = tempfile.mkdtemp(prefix=WORKDIR_PREFIX)
        _in_flight[workdir] = self.spec.cleanup
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
            _clean(workdir)


def run(call, workdir, timeout, program):
    env = {k: v for k, v in os.environ.items() if not SCRUB.search(k)}
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
    try:
        os.killpg(proc.pid, signal.SIGKILL)
    except OSError:
        pass
    if reap:
        proc.communicate()


def _clean(workdir):
    cleanup = _in_flight.pop(workdir, None)
    if cleanup:
        cleanup(workdir)
    shutil.rmtree(workdir, ignore_errors=True)


@atexit.register
def _stop_everything():
    for proc in list(_running):
        _kill(proc, reap=False)
    for workdir in list(_in_flight):
        _clean(workdir)


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
    return Call([binary, "--prompt-file", prompt_file, "--rules", prompt.system, "--output-format", "plain",
                 "--max-turns", "1", "--disable-web-search", "--no-auto-update", "--disallowed-tools", GROK_TOOLS,
                 "--model", o.model or "grok-4.7-build-fast"], "", {})


GROK_TOOLS = "read_file,list_dir,grep,web_search,web_fetch,todo_write,run_terminal_cmd,write,search_replace,task"


def _grok_cleanup(workdir):
    """Grok keeps every session in a folder named after its working folder; this one is the call's own."""
    home = os.environ.get("GROK_HOME") or os.path.expanduser("~/.grok")
    shutil.rmtree(os.path.join(home, "sessions", quote(os.path.realpath(workdir), safe="")), ignore_errors=True)


def _one_argument(text, program):
    """A prompt passed on the command line, which Linux caps at 128 KiB for one argument."""
    if len(text.encode("utf-8")) > ARG_BYTES:
        raise Refused(f"the prompt is too long for {program}'s command line")
    return text


def _copilot(binary, o, prompt, workdir):
    home = os.path.join(workdir, "copilot-home")
    os.makedirs(home)
    argv = [binary, "-p", _one_argument(f"{prompt.system}\n\n{prompt.user}", "copilot"), "-s", "--no-ask-user"]
    if o.model:
        argv += ["--model", o.model]
    return Call(argv, "", {"COPILOT_HOME": home})


def _cursor(binary, o, prompt, workdir):
    argv = [binary, "-p", _one_argument(f"{prompt.system}\n\n{prompt.user}", "cursor-agent"), "--output-format", "text"]
    if o.model:
        argv += ["--model", o.model]
    return Call(argv, "", {})


SPECS = (
    CliSpec("claude-code", "Your own Claude Code (claude -p, your login)", "claude", _claude, default_model="haiku"),
    CliSpec("chatgpt", "Sign in with ChatGPT (codex exec, your login)", "codex", _codex),
    # Measured on 2026-10-27: about 10 s a call on its fastest model, so the check gets the longest budget allowed.
    CliSpec("grok", "Grok (grok -p, your login)", "grok", _grok, default_model="grok-4.7-build-fast",
            cleanup=_grok_cleanup, check_budget=25.0),
    # The prompt goes on the command line, which Linux caps at 128 KiB for one argument.
    CliSpec("copilot", "GitHub Copilot (copilot -p, your login)", "copilot", _copilot, max_prompt_chars=ARG_CHARS),
    CliSpec("cursor", "Cursor (cursor-agent -p, your login)", "cursor-agent", _cursor, max_prompt_chars=ARG_CHARS,
            hidden=True),
)
PLUGINS = tuple(PluginSpec(spec.name, spec.label, CliOptions, lambda o, spec=spec: CliModel(spec, o), spec.hidden)
                for spec in SPECS)
