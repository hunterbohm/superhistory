"""OpenCode: a plugin file, kb-context.js, in OpenCode's global plugins folder. On each message the user sends, it runs
`kb hook opencode` and appends what that prints to the message's text. Messages in child sessions (subagents,
other plugins' helper sessions) are skipped."""
import json
import os
import shlex
import shutil

from kb import agents
from kb.agents import Agent, Change

FILE_NAME = "kb-context.js"
MARKER = "// kb-context: written by `kb hooks install opencode`"

# OpenCode calls every function the module exports as a plugin, so only the plugin is exported.
PLUGIN = MARKER + """; `kb hooks uninstall opencode` removes it.
// Appends the knowledge-base records related to each message the user sends to that message. Subagent and other
// child sessions are skipped. Never throws.
import { spawn } from "node:child_process";

const KB = __KB_ARGV__;
const TIMEOUT_MS = __TIMEOUT_MS__;

function ask(payload) {
  return new Promise((resolve) => {
    let out = "";
    let child;
    const done = (text) => {
      clearTimeout(timer);
      resolve(text);
    };
    const timer = setTimeout(() => {
      try {
        child.kill("SIGKILL");
      } catch {}
      done("");
    }, TIMEOUT_MS);
    try {
      child = spawn(KB[0], KB.slice(1), { stdio: ["pipe", "pipe", "ignore"] });
      child.on("error", () => done(""));
      child.stdout.setEncoding("utf8");
      child.stdout.on("data", (chunk) => {
        out += chunk;
      });
      child.on("close", (code) => done(code === 0 ? out : ""));
      child.stdin.on("error", () => {});
      child.stdin.end(JSON.stringify(payload));
    } catch {
      done("");
    }
  });
}

export const KbContext = async () => {
  const children = new Set();
  return {
    event: async ({ event }) => {
      try {
        const info = String(event.type).startsWith("session.") && event.properties && event.properties.info;
        if (info && info.id && info.parentID) children.add(info.id);
      } catch {}
    },
    "chat.message": async (input, output) => {
      try {
        if (children.has(input.sessionID)) return;
        const parts = (output.parts || []).filter(
          (p) => p && p.type === "text" && !p.synthetic && !p.ignored && typeof p.text === "string",
        );
        const message = parts.map((p) => p.text).join("\\n").trim();
        if (!message) return;
        const context = (await ask({ session_id: input.sessionID || "", message })).trim();
        if (context) parts[parts.length - 1].text += "\\n\\n" + context;
      } catch {}
    },
  };
};
"""


def _config_dir(home):
    return os.path.join(os.environ.get("XDG_CONFIG_HOME") or os.path.join(home, ".config"), "opencode")


def _plugin(home):
    return os.path.join(_config_dir(home), "plugins", FILE_NAME)


def _render(kb_command):
    return (PLUGIN.replace("__KB_ARGV__", json.dumps(shlex.split(kb_command)))
            .replace("__TIMEOUT_MS__", str(agents.HOOK_TIMEOUT * 1000)))


def detect(home):
    return os.path.isdir(_config_dir(home)) or bool(shutil.which("opencode"))


def installed(home):
    return MARKER in agents.read(_plugin(home))


def install(home, kb_command):
    path = _plugin(home)
    old, new = agents.read(path), _render(kb_command)
    if old == new:
        return [Change(path, "unchanged")]
    if old and MARKER not in old:
        return [Change(path, "skipped", "a file kb did not write has this name")]
    agents.write(path, new)
    return [Change(path, "updated" if old else "added")]


def uninstall(home):
    path = _plugin(home)
    old = agents.read(path)
    if not old:
        return [Change(path, "unchanged")]
    if MARKER not in old:
        return [Change(path, "skipped", "a file kb did not write has this name")]
    os.remove(path)
    return [Change(path, "removed")]


def request(event):
    """The message and session id the plugin sends; OpenCode gives the plugin no transcript, so no turns before."""
    message = str(event.get("message") or "")
    if not message.strip():
        return None
    return message, [], str(event.get("session_id") or "")


def reply(text):
    return text


AGENT = Agent("OpenCode", detect, installed, install, uninstall, request, reply)
