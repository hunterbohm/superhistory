import json
import urllib.error
import urllib.request
import uuid
from dataclasses import dataclass
from typing import Literal, Optional

from kb import COMMAND, __version__
from kb.ai import Busy, NotReady, PluginSpec, Refused, Traits
from kb.config import Secret, SecretError
from kb.mask import mask

# The plan call's p95 was 2.6 s on the mini (Nov 14, 2026): a fallback after 2 s starts beside about one plan in five.
TRAITS = Traits(check_budget=10.0, hedge_after=3.0, parallel=4, max_prompt_chars=600_000, fallback_after=2.0)
AUTH_CODES = (401, 403)
PAYMENT_REQUIRED = 402
SETUP_CODES = AUTH_CODES + (PAYMENT_REQUIRED, 404)
# Some API hosts sit behind Cloudflare, which refuses Python's default user agent.
USER_AGENT = f"{COMMAND}/{__version__}"


@dataclass(frozen=True)
class AnthropicOptions:
    key: Optional[Secret] = None
    model: str = "claude-haiku-5-5"
    base_url: str = "https://api.anthropic.com"
    auth: Literal["x-api-key", "bearer"] = "x-api-key"
    effort: Literal["low", "medium", "high", "none"] = "low"


@dataclass(frozen=True)
class AnthropicCompatibleOptions(AnthropicOptions):
    base_url: str = ""
    auth: Literal["x-api-key", "bearer"] = "bearer"


@dataclass(frozen=True)
class OpenCodeGoOptions(AnthropicOptions):
    base_url: str = "https://opencode.ai/inference/go/anthropic"


@dataclass(frozen=True)
class OpenAIOptions:
    key: Optional[Secret] = None
    model: str = "gpt-5.4-mini"
    base_url: str = "https://api.openai.com/v1"
    effort: Literal["minimal", "low", "medium", "high", "none"] = "low"


@dataclass(frozen=True)
class OpenRouterOptions(OpenAIOptions):
    model: str = "anthropic/claude-haiku-5.5"
    base_url: str = "https://openrouter.ai/api/v1"
    effort: Literal["minimal", "low", "medium", "high", "none"] = "none"


@dataclass(frozen=True)
class OpenAICompatibleOptions(OpenAIOptions):
    model: str = ""
    base_url: str = ""
    effort: Literal["minimal", "low", "medium", "high", "none"] = "none"


@dataclass(frozen=True)
class GoogleOptions:
    key: Optional[Secret] = None
    model: str = "gemini-3.8-flash"
    base_url: str = "https://generativelanguage.googleapis.com/v1beta"


class _Http:
    traits = TRAITS

    def __init__(self, name, options, key_required=True):
        if not options.base_url:
            raise NotReady(f"set base_url under [ai] for {name}")
        if not options.model:
            raise NotReady(f"set model under [ai] for {name}")
        self.name, self.options, self.model, self.key_required = name, options, options.model, key_required

    def _key(self):
        if self.options.key is None:
            if self.key_required:
                raise NotReady(f'set key under [ai] for {self.name}, such as key = "env:NAME_OF_THE_VARIABLE"')
            return None
        try:
            return self.options.key.reveal()
        except SecretError as exc:
            raise NotReady(f"[ai] key: {exc}") from None

    def _url(self, path):
        return self.options.base_url.rstrip("/") + path


class Anthropic(_Http):
    def _headers(self):
        return {"content-type": "application/json", "anthropic-version": "2023-06-01"}

    def ask(self, prompt, timeout):
        o = self.options
        headers = self._headers()
        key = self._key()
        if key:
            headers.update({"authorization": "Bearer " + key} if o.auth == "bearer" else {"x-api-key": key})
        body = {"model": o.model, "max_tokens": prompt.max_tokens, "system": prompt.system,
                "messages": [{"role": "user", "content": prompt.user}]}
        if o.effort != "none":
            body["output_config"] = {"effort": o.effort}
        response = post(self._url("/v1/messages"), body, headers, timeout)
        if response.get("stop_reason") == "refusal":
            raise Refused("the model refused")
        return "".join(b.get("text", "") for b in response.get("content") or [] if b.get("type") == "text")


class OpenCodeGo(Anthropic):
    """OpenCode Go routes by a session id; each call here is a conversation of its own."""

    def _headers(self):
        return {**super()._headers(), "x-opencode-session": str(uuid.uuid4())}


class OpenAI(_Http):
    def __init__(self, name, options, key_required=True, token_field="max_completion_tokens"):
        super().__init__(name, options, key_required)
        self.token_field = token_field

    def ask(self, prompt, timeout):
        o = self.options
        headers = {"content-type": "application/json"}
        key = self._key()
        if key:
            headers["authorization"] = "Bearer " + key
        body = {"model": o.model, self.token_field: prompt.max_tokens,
                "messages": [{"role": "system", "content": prompt.system}, {"role": "user", "content": prompt.user}]}
        if o.effort != "none":
            body["reasoning_effort"] = o.effort
        choice = (post(self._url("/chat/completions"), body, headers, timeout).get("choices") or [{}])[0]
        if choice.get("finish_reason") == "content_filter":
            raise Refused("the model refused")
        content = (choice.get("message") or {}).get("content") or ""
        if isinstance(content, list):
            content = "".join(part.get("text", "") for part in content if isinstance(part, dict))
        return content


class Google(_Http):
    def ask(self, prompt, timeout):
        o = self.options
        headers = {"content-type": "application/json", "x-goog-api-key": self._key()}
        body = {"systemInstruction": {"parts": [{"text": prompt.system}]},
                "contents": [{"role": "user", "parts": [{"text": prompt.user}]}],
                "generationConfig": {"maxOutputTokens": prompt.max_tokens}}
        response = post(self._url(f"/models/{o.model}:generateContent"), body, headers, timeout)
        if (response.get("promptFeedback") or {}).get("blockReason"):
            raise Refused("the model refused")
        candidate = (response.get("candidates") or [{}])[0]
        if candidate.get("finishReason") in ("SAFETY", "PROHIBITED_CONTENT", "BLOCKLIST"):
            raise Refused("the model refused")
        return "".join(p.get("text", "") for p in (candidate.get("content") or {}).get("parts") or []
                       if not p.get("thought"))


def post(url, body, headers, timeout):
    """One JSON POST. HTTP and network failures become NotReady, Busy or Refused."""
    try:
        request = urllib.request.Request(url, data=json.dumps(body).encode(),
                                         headers={**headers, "user-agent": USER_AGENT}, method="POST")
    except ValueError as e:
        raise NotReady(f"{url} is not a usable address: {e}") from None
    try:
        with urllib.request.urlopen(request, timeout=timeout) as resp:
            return json.load(resp)
    except urllib.error.HTTPError as e:
        body = e.read(65537)
        # A body cut at the 65,536 bytes read could end in part of a secret mask() cannot know, so it is left out.
        detail = ("(longer than 64 KB, left out)" if len(body) > 65536
                  else " ".join(mask(body.decode(errors="replace")).split())[:300])
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
    except Exception as e:
        raise Busy(f"{url} failed: {type(e).__name__}: {e}") from None


PLUGINS = (
    PluginSpec("anthropic", "An Anthropic API key", AnthropicOptions, lambda o: Anthropic("anthropic", o)),
    PluginSpec("openai", "An OpenAI API key", OpenAIOptions, lambda o: OpenAI("openai", o)),
    PluginSpec("openrouter", "An OpenRouter API key", OpenRouterOptions,
               lambda o: OpenAI("openrouter", o, token_field="max_tokens")),
    PluginSpec("opencode-go", "An OpenCode Go API key", OpenCodeGoOptions, lambda o: OpenCodeGo("opencode-go", o)),
    PluginSpec("google", "A Google AI Studio (Gemini) API key", GoogleOptions, lambda o: Google("google", o)),
    PluginSpec("anthropic-compatible", "Any server that speaks Anthropic's API, at a base URL you give",
               AnthropicCompatibleOptions, lambda o: Anthropic("anthropic-compatible", o, key_required=False)),
    PluginSpec("openai-compatible", "Any server that speaks OpenAI's chat API, at a base URL you give",
               OpenAICompatibleOptions,
               lambda o: OpenAI("openai-compatible", o, key_required=False, token_field="max_tokens")),
)
