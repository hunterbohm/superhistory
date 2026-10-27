import json
import urllib.error
import urllib.request
from dataclasses import dataclass
from typing import Literal, Optional

from kb.ai import Busy, NotReady, PluginSpec, Refused, Traits
from kb.config import Secret, SecretError

TRAITS = Traits(check_budget=10.0, hedge_after=3.0, parallel=4, max_prompt_chars=600_000)
AUTH_CODES = (401, 403)
SETUP_CODES = AUTH_CODES + (404,)


@dataclass(frozen=True)
class AnthropicOptions:
    key: Optional[Secret] = None
    model: str = "claude-haiku-5-5"
    base_url: str = "https://api.anthropic.com"
    auth: Literal["x-api-key", "bearer"] = "x-api-key"
    effort: Literal["low", "medium", "high", "none"] = "low"


@dataclass(frozen=True)
class CompatibleOptions(AnthropicOptions):
    base_url: str = ""
    auth: Literal["x-api-key", "bearer"] = "bearer"


class Anthropic:
    traits = TRAITS

    def __init__(self, name, options):
        if not options.base_url:
            raise NotReady(f"set base_url under [ai] for {name}")
        self.name, self.options, self.model = name, options, options.model

    def ask(self, prompt, timeout):
        o = self.options
        headers = {"content-type": "application/json", "anthropic-version": "2023-06-01"}
        if o.key:
            try:
                key = o.key.reveal()
            except SecretError as exc:
                raise NotReady(f"[ai] key: {exc}") from None
            headers.update({"authorization": "Bearer " + key} if o.auth == "bearer" else {"x-api-key": key})
        elif self.name == "anthropic":
            raise NotReady('set key under [ai], such as key = "env:ANTHROPIC_API_KEY"')
        body = {"model": o.model, "max_tokens": prompt.max_tokens, "system": prompt.system,
                "messages": [{"role": "user", "content": prompt.user}]}
        if o.effort != "none":
            body["output_config"] = {"effort": o.effort}
        response = post(o.base_url.rstrip("/") + "/v1/messages", body, headers, timeout)
        if response.get("stop_reason") == "refusal":
            raise Refused("the model refused")
        return "".join(b.get("text", "") for b in response.get("content") or [] if b.get("type") == "text")


def post(url, body, headers, timeout):
    """One JSON POST. HTTP and network failures become NotReady, Busy or Refused."""
    request = urllib.request.Request(url, data=json.dumps(body).encode(), headers=headers, method="POST")
    try:
        with urllib.request.urlopen(request, timeout=timeout) as resp:
            return json.load(resp)
    except urllib.error.HTTPError as e:
        detail = e.read()[:300].decode(errors="replace")
        message = f"HTTP {e.code} from {url}: {detail}"
        if e.code in SETUP_CODES:
            raise NotReady(message) from None
        if e.code == 429 or e.code >= 500:
            try:
                retry_after = float(e.headers.get("retry-after") or 0)
            except ValueError:
                retry_after = 0.0
            raise Busy(message, retry_after) from None
        raise Refused(message) from None
    except (urllib.error.URLError, TimeoutError, ConnectionError, OSError) as e:
        raise Busy(f"cannot reach {url}: {getattr(e, 'reason', e)}") from None
    except ValueError as e:
        raise Busy(f"{url} answered with something other than JSON: {e}") from None


PLUGINS = (
    PluginSpec("anthropic", "An Anthropic API key", AnthropicOptions, lambda o: Anthropic("anthropic", o)),
    PluginSpec("anthropic-compatible", "Any server that speaks Anthropic's API, at a base URL you give",
               CompatibleOptions, lambda o: Anthropic("anthropic-compatible", o)),
)
