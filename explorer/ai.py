"""AI synthesis of decompiler outputs.

Given the outputs of several decompilers for the same binary, the model produces
two things, in two separate calls:

* ``best``        - a merge of the decompiler outputs into one listing that stays
                    faithful to them (no interpretation).
* ``interpreted`` - that merge, interpreted: meaningful names, comments and a
                    summary of what the code does.

The provider is Google Gemini, Anthropic Claude or any OpenAI-compatible API,
with the server's key (``.env``) or a key sent by the browser (``AIConfig``).
"""
import gzip
import math
import time
from dataclasses import dataclass
from logging import getLogger
from urllib.parse import urlparse

import requests
from django.conf import settings

logger = getLogger('explorer.ai')

GEMINI_API_URL = 'https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent'

# Gemini statuses after which we move on to the next model. Transient ones
# (model overloaded, ...) are retried in a later round; a retired model (404)
# or an exhausted quota (429) won't recover within the request, so that model
# is skipped from then on.
GEMINI_TRANSIENT_STATUSES = {500, 502, 503, 504}
GEMINI_SKIP_STATUSES = {404, 429}


class AIRateLimitError(RuntimeError):
    """The provider refused the request because a quota/rate limit was hit."""

    def __init__(self, message, retry_after=None):
        super().__init__(message)
        # Whole seconds (or None), ready for a Retry-After header.
        try:
            self.retry_after = math.ceil(float(retry_after)) if retry_after else None
        except ValueError:
            self.retry_after = None


# The synthesis is done in two separate calls, so that the merge stays a pure
# decompiler output and all the interpretation happens in the second pass, on
# top of that merge (never on the raw decompiler outputs).

MERGE_PROMPT = """You are given the outputs of several different decompilers \
(e.g. Ghidra, angr, RetDec, Reko, Snowman, rev.ng) run on the *same* binary. \
Merge them into ONE decompiled listing.

You are a careful editor, NOT an analyst: the result must still be a decompiler \
output, traceable line by line to the inputs. Rules:

- Every function, statement, expression, constant, string literal and type you \
write must appear in at least one of the decompiler outputs. Do not invent code, \
do not guess what a construct "really" does (e.g. keep a raw syscall / swi() / \
inline asm as the decompilers show it) and do not fix what looks like a bug.
- Keep the decompilers' identifiers: symbol names recovered from the binary \
(functions, globals, imports) and the decompiler-generated names of locals and \
parameters (local_1c, uVar1, param_1, v2, ...). NEVER rename anything to a \
"meaningful" name. When decompilers name the same variable differently, pick \
the name used by the decompiler you based that function on, consistently.
- Do not rewrite code into a nicer or more idiomatic form (no turning pointer \
arithmetic into array indexing, no replacing an inlined string loop by strlen, \
no restructuring gotos into loops) unless one of the decompilers already shows \
exactly that form.
- How to choose, function by function: take as base the output that is the most \
complete and correct for that function (sound control flow, no missing \
statements, no garbage), then correct it with details on which the other \
decompilers agree against it (signature, argument count, types, constants, \
string literals, array sizes). Ignore outputs that are clearly broken for that \
function (failed decompilation, raw assembly, empty body).
- Leave out compiler/runtime boilerplate (_start, _init, _fini, frame_dummy, \
__libc_csu_*, register_tm_clones, deregister_tm_clones, __do_global_dtors_aux, \
PLT/import stubs) unless it contains user code.
- No explanatory comments and no summary. The only comments allowed are: one \
line right above each function, `/* from: <decompiler>[, <decompiler> for \
<what>] */`, saying which output(s) it is based on; and, only where the \
decompilers disagree on the behaviour, `/* NOTE: <decompiler> shows <...> */`.

Output ONLY C code - no prose, no markdown fences."""

INTERPRET_PROMPT = """You are a senior reverse-engineering expert. You are given \
a decompiled C listing of a binary (merged from several decompilers; the \
`/* from: ... */` lines say which decompiler each function comes from). \
Produce an interpreted version of it for a human analyst:

- Begin with a /* ... */ summary: what the program does, its inputs and \
outputs, and any notable behaviour (crypto, networking, file I/O, \
anti-analysis, and memory-safety issues such as unchecked indexes or buffer \
overflows, naming the function and the line involved).
- Rename every decompiler-generated identifier (local_*, uVar*, param_*, v*, \
...) to a meaningful name. Keep the symbol names recovered from the binary and \
library calls as they are.
- Recover types and structures where the code makes them evident.
- You may rewrite decompiler idioms into readable C (pointer arithmetic into \
indexing, inlined string loops into strlen/strncmp, gotos into loops), but the \
behaviour must stay strictly identical: same checks, same constants, same order \
of operations, same integer widths and wrap-around. In particular:
  - never add a check, guard or bounds test that the listing does not have, \
and never fix a bug - point it out in a comment instead;
  - never invent a value or an initialisation that is not in the listing \
(e.g. keep compiler artefacts such as a stack canary read through \
in_GS_OFFSET as they are, explained by a comment);
  - only turn a raw syscall (swi / int 0x80 / syscall) into the libc call it \
performs if the syscall number is visible in the listing; otherwise keep it \
and say in a comment what cannot be determined;
  - never add helper definitions, stubs or macros (e.g. a fake swi() or \
CONCAT44()) to make the code compile: keep the decompiler pseudo-functions \
as they are.
- Add concise comments explaining each block and anything non-obvious (magic \
values, why a check can or cannot be bypassed, ...). Drop the `/* from: ... */` \
lines.

Output ONLY C code with comments - no prose outside comments, no markdown \
fences."""


def read_decompilation_text(decompilation) -> str:
    """Return the decompiled source text for a Decompilation.

    Stored files may be gzip-compressed (that is how runners upload them), so
    we try to gunzip and fall back to the raw bytes.
    """
    handle = decompilation.decompiled_file.open('rb')
    try:
        raw = handle.read()
    finally:
        handle.close()

    try:
        return gzip.decompress(raw).decode('utf-8', errors='replace')
    except (OSError, gzip.BadGzipFile, EOFError):
        return raw.decode('utf-8', errors='replace')


def _strip_fences(text: str) -> str:
    """Remove a surrounding ```...``` markdown fence if the model added one."""
    text = text.strip()
    if text.startswith('```'):
        lines = text.splitlines()
        # Drop the opening fence (possibly with a language tag like ```c).
        lines = lines[1:]
        if lines and lines[-1].strip().startswith('```'):
            lines = lines[:-1]
        text = '\n'.join(lines).strip()
    return text


def _fit_to_budget(texts, budget):
    """Truncate ``texts`` so their total length stays within ``budget`` chars.

    Short outputs are kept whole and their unused share is handed to the longer
    ones, so a single huge output cannot crowd out every other decompiler.
    """
    fitted = list(texts)
    remaining = budget
    order = sorted(range(len(texts)), key=lambda i: len(texts[i]))
    for count, i in enumerate(order):
        share = remaining // (len(order) - count)
        text = texts[i]
        if len(text) > share:
            fitted[i] = (text[:share] + f"\n/* ... truncated {len(text) - share} "
                         "characters to fit the AI input budget ... */\n")
            text = text[:share]
        remaining -= len(text)
    return fitted


def build_merge_prompt(decompilations) -> str:
    """Assemble the merge prompt from a list of (name, version, text) tuples."""
    texts = _fit_to_budget([text for _, _, text in decompilations],
                           settings.AI_MAX_INPUT_CHARS)
    decompilations = [(name, version, text) for (name, version, _), text
                      in zip(decompilations, texts)]

    parts = [
        "Here are the decompiler outputs for the same binary. Merge them as "
        "instructed.\n"
    ]
    for name, version, text in decompilations:
        header = f"{name} {version}".strip()
        parts.append(f"\n===== Decompiler: {header} =====\n{text}\n")
    return ''.join(parts)




# --- Provider configuration ---------------------------------------------------

PROVIDERS = {
    'gemini': 'Google Gemini',
    'anthropic': 'Anthropic Claude',
    'openai': 'OpenAI-compatible',
}


@dataclass
class AIConfig:
    """The provider, key and model an analysis runs with."""
    provider: str
    api_key: str
    model: str = ''
    base_url: str = ''
    # Whether the key was sent by the browser rather than read from .env.
    from_browser: bool = False

    @property
    def label(self):
        return PROVIDERS[self.provider]

    @property
    def effective_model(self):
        """The model asked for, or the server's default one for the provider."""
        if self.model:
            return self.model
        return {
            'gemini': settings.GEMINI_MODEL,
            'anthropic': settings.ANTHROPIC_MODEL,
            'openai': settings.OPENAI_MODEL,
        }[self.provider]


def server_config():
    """The server's own provider and key (from .env), or None if it has none."""
    provider = settings.AI_PROVIDER
    api_key = {
        'gemini': settings.GEMINI_API_KEY,
        'anthropic': settings.ANTHROPIC_API_KEY,
        'openai': settings.OPENAI_API_KEY,
    }.get(provider, '')
    if not api_key:
        return None
    config = AIConfig(provider, api_key,
                      base_url=settings.OPENAI_BASE_URL if provider == 'openai' else '')
    if not config.effective_model:
        return None
    return config


def config_from_request(data):
    """The config for an API request: the key sent by the browser if there is
    one, else the server's. Returns None when neither is available, and raises
    ``ValueError`` when the browser's settings are not valid."""
    api_key = (data.get('api_key') or '').strip()
    if not api_key:
        return server_config()
    if not settings.AI_ALLOW_BROWSER_KEYS:
        raise ValueError("This server does not accept API keys from the browser.")

    provider = data.get('provider')
    if provider not in PROVIDERS:
        raise ValueError(f"Unknown AI provider {provider!r}.")
    model = (data.get('model') or '').strip()

    base_url = ''
    if provider == 'openai':
        base_url = (data.get('base_url') or '').strip().rstrip('/') or settings.OPENAI_BASE_URL
        if base_url != settings.OPENAI_BASE_URL and not settings.AI_ALLOW_CUSTOM_BASE_URL:
            raise ValueError("This server only accepts its own OpenAI-compatible base URL.")
        if urlparse(base_url).scheme not in ('http', 'https'):
            raise ValueError("The base URL must start with http:// or https://.")
        if not model and not settings.OPENAI_MODEL:
            raise ValueError("Set the model to use with the OpenAI-compatible API.")

    return AIConfig(provider, api_key, model, base_url, from_browser=True)


# --- Providers -----------------------------------------------------------------
# Each one returns ``(text, model)`` and raises ``AIRateLimitError`` when a
# quota / rate limit is hit, or ``RuntimeError`` for any other failure. Error
# messages never contain the API key: it is only ever sent in request headers.

TRUNCATED_NOTE = "\n/* ... output truncated: the AI_MAX_TOKENS limit was reached ... */\n"

# Default max output tokens; Gemini's thinking tokens count against its limit.
DEFAULT_MAX_TOKENS = {'gemini': 65536, 'anthropic': 32000}


def _max_tokens(provider):
    return settings.AI_MAX_TOKENS or DEFAULT_MAX_TOKENS.get(provider)


def _generate_anthropic(config, system, prompt, deadline):
    import anthropic

    model = config.effective_model
    client = anthropic.Anthropic(api_key=config.api_key)
    # Streaming is used because the combined code can be long (large
    # max_tokens would otherwise risk an HTTP timeout).
    try:
        with client.messages.stream(
            model=model,
            max_tokens=_max_tokens('anthropic'),
            system=system,
            messages=[{"role": "user", "content": prompt}],
            timeout=max(deadline - time.monotonic(), 1),
        ) as stream:
            message = stream.get_final_message()
    except anthropic.RateLimitError as exc:
        raise AIRateLimitError(
            f"Claude {model}: rate limit or quota reached.",
            retry_after=exc.response.headers.get('retry-after'),
        ) from exc
    except anthropic.APIStatusError as exc:
        raise RuntimeError(f"Claude {model}: {getattr(exc, 'message', exc)}") from exc
    except anthropic.APIConnectionError as exc:
        raise RuntimeError(f"Claude {model}: could not reach the Anthropic API.") from exc

    text = ''.join(block.text for block in message.content if block.type == 'text')
    if message.stop_reason == 'max_tokens':
        text += TRUNCATED_NOTE
    return text, model


def _gemini_error(resp):
    """Return ``(message, retry_after_seconds)`` from a Gemini error response."""
    try:
        error = resp.json()['error']
    except (ValueError, KeyError, TypeError):
        return resp.text[:500], None
    retry_after = None
    for detail in error.get('details', []):
        delay = detail.get('retryDelay', '')
        if delay.endswith('s'):
            retry_after = delay[:-1]
    return error.get('message', ''), retry_after


def _gemini_text(data, model):
    """Extract the answer text (thought summaries excluded) from a response."""
    candidates = data.get('candidates') or []
    if not candidates:
        reason = (data.get('promptFeedback') or {}).get('blockReason', 'no candidates')
        raise RuntimeError(f"Gemini {model} returned no answer ({reason}).")

    candidate = candidates[0]
    finish_reason = candidate.get('finishReason')
    parts = (candidate.get('content') or {}).get('parts', [])
    text = ''.join(part.get('text', '') for part in parts if not part.get('thought'))
    if not text.strip():
        raise RuntimeError(
            f"Gemini {model} returned an empty answer (finishReason={finish_reason})."
        )
    if finish_reason == 'MAX_TOKENS':
        logger.warning("Gemini %s hit AI_MAX_TOKENS, output is truncated", model)
        text += TRUNCATED_NOTE
    return text


def _generate_gemini(config, system, prompt, deadline):
    """The model asked for (or ``GEMINI_MODEL``) is tried first, then each of
    ``GEMINI_FALLBACK_MODELS``; any model failing with a transient error
    (overloaded, ...), a quota error or because it is retired hands over to the
    next one, and models failing transiently are tried again in later rounds.
    Any other error (e.g. a prompt too large) would fail on every model and is
    raised straight away."""
    body = {
        'systemInstruction': {'parts': [{'text': system}]},
        'contents': [{'role': 'user', 'parts': [{'text': prompt}]}],
        'generationConfig': {'maxOutputTokens': _max_tokens('gemini')},
    }
    headers = {'x-goog-api-key': config.api_key}

    models = list(dict.fromkeys([config.effective_model, *settings.GEMINI_FALLBACK_MODELS]))
    failures = {}
    retry_after = None
    for attempt in range(settings.AI_MAX_RETRIES + 1):
        if attempt:
            time.sleep(2 ** attempt)
        for model in models:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                break
            if failures.get(model) in GEMINI_SKIP_STATUSES:
                continue

            started = time.monotonic()
            try:
                resp = requests.post(GEMINI_API_URL.format(model=model), json=body,
                                     headers=headers, timeout=remaining)
            except requests.Timeout:
                logger.warning("Gemini %s: no answer within the AI_REQUEST_TIMEOUT budget", model)
                failures[model] = 'timeout'
                break
            except requests.ConnectionError as exc:
                raise RuntimeError("Could not reach the Gemini API.") from exc
            elapsed = time.monotonic() - started
            if resp.ok:
                logger.info("Gemini %s: answered in %.1fs", model, elapsed)
                return _gemini_text(resp.json(), model), model

            message, delay = _gemini_error(resp)
            logger.warning("Gemini %s: HTTP %s after %.1fs: %s",
                           model, resp.status_code, elapsed, message)
            if resp.status_code not in GEMINI_TRANSIENT_STATUSES | GEMINI_SKIP_STATUSES:
                raise RuntimeError(f"Gemini {model}: {message}")
            failures[model] = resp.status_code
            if resp.status_code == 429:
                retry_after = delay or retry_after

    summary = ', '.join(
        f"{model}: {'timeout' if status == 'timeout' else f'HTTP {status}'}"
        for model, status in failures.items()
    )
    if failures and all(status == 429 for status in failures.values()):
        raise AIRateLimitError(
            f"Gemini quota or rate limit reached on every model ({summary}). "
            "The free tier has small per-minute/per-day limits - wait a bit, "
            "or enable billing on the key.",
            retry_after=retry_after,
        )
    raise RuntimeError(
        f"Gemini is unavailable right now ({summary}). Try again in a moment."
    )


def _openai_error(resp):
    try:
        error = resp.json().get('error')
        if isinstance(error, dict):
            return error.get('message') or str(error)
        if error:
            return str(error)
    except (ValueError, AttributeError):
        pass
    return resp.text[:500]


def _generate_openai(config, system, prompt, deadline):
    """Any OpenAI-compatible chat completions API (OpenAI, Mistral, OpenRouter,
    Groq, DeepSeek, a local Ollama or vLLM server, ...)."""
    model = config.effective_model
    url = f"{config.base_url}/chat/completions"
    body = {
        'model': model,
        'messages': [
            {'role': 'system', 'content': system},
            {'role': 'user', 'content': prompt},
        ],
    }
    if _max_tokens('openai'):
        body['max_tokens'] = _max_tokens('openai')
    started = time.monotonic()
    try:
        resp = requests.post(url, json=body, timeout=max(deadline - started, 1),
                             headers={'Authorization': f'Bearer {config.api_key}'})
    except requests.Timeout as exc:
        raise RuntimeError(f"{model}: no answer within the AI_REQUEST_TIMEOUT budget.") from exc
    except requests.ConnectionError as exc:
        raise RuntimeError(f"Could not reach {config.base_url}.") from exc

    elapsed = time.monotonic() - started
    if not resp.ok:
        message = _openai_error(resp)
        logger.warning("%s at %s: HTTP %s after %.1fs: %s",
                       model, config.base_url, resp.status_code, elapsed, message)
        if resp.status_code == 429:
            raise AIRateLimitError(f"{model}: {message}",
                                   retry_after=resp.headers.get('retry-after'))
        raise RuntimeError(f"{model} (HTTP {resp.status_code}): {message}")

    try:
        choice = resp.json()['choices'][0]
        content = choice['message']['content'] or ''
    except (ValueError, KeyError, IndexError, TypeError) as exc:
        raise RuntimeError(f"{model}: unexpected response from {config.base_url}.") from exc
    if isinstance(content, list):
        content = ''.join(part.get('text', '') for part in content if isinstance(part, dict))
    if not content.strip():
        raise RuntimeError(f"{model} returned an empty answer.")
    if choice.get('finish_reason') == 'length':
        content += TRUNCATED_NOTE
    logger.info("%s at %s: answered in %.1fs", model, config.base_url, elapsed)
    return content, model


_GENERATORS = {
    'gemini': _generate_gemini,
    'anthropic': _generate_anthropic,
    'openai': _generate_openai,
}


# --- Synthesis steps ------------------------------------------------------------
# Every function below raises ``AIRateLimitError`` when the provider's quota is
# exhausted, and ``RuntimeError`` (or the HTTP client's exceptions) for any
# other failure.

def new_deadline():
    # Budget for one HTTP request, so we answer before gunicorn kills the worker.
    return time.monotonic() + settings.AI_REQUEST_TIMEOUT


def _generate(config, system, prompt, deadline):
    started = time.monotonic()
    text, model = _GENERATORS[config.provider](config, system, prompt, deadline)
    return _strip_fences(text), model, time.monotonic() - started


def generate_merge(config, decompilations, deadline=None):
    """Merge a list of ``(name, version, text)`` decompiler outputs.

    Returns ``{'best': str, 'model': str, 'seconds': float}``.
    """
    best, model, seconds = _generate(config, MERGE_PROMPT, build_merge_prompt(decompilations),
                                     deadline or new_deadline())
    return {'best': best, 'model': model, 'seconds': seconds}


def generate_interpretation(config, merged, deadline=None):
    """Interpret a merged listing.

    Returns ``{'interpreted': str, 'model': str, 'seconds': float}``.
    """
    interpreted, model, seconds = _generate(config, INTERPRET_PROMPT, merged,
                                            deadline or new_deadline())
    return {'interpreted': interpreted, 'model': model, 'seconds': seconds}


def test_connection(config):
    """Send a tiny prompt to check the key and the model. Returns the model."""
    _, model, _ = _generate(config, "You check that an API key works.",
                            "Reply with the single word OK.", time.monotonic() + 90)
    return model
