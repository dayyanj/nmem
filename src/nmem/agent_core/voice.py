"""Voice layer — assemble an agent's EXPRESSIVE system prompt from config + prose.

`agent_core.chat.system_prompt(persona)` is the grounded FLOOR (identity + objectives, plain —
the honest floor every agent shares). An agent's VOICE — its tone, character, channel-specific
format — is layered on top from DATA, not code: prose files (core_profile.md, …) concatenated,
plus a knob manifest that renders config'd knob VALUES as prompt directives. This module is that
generic assembler; the knob names, the directive text, the prose, and the values are all the
host's config/content — a new agent writes a `voice.yaml` + prompt files and no Python.

Manifest shape (a `voice:` block; every key optional)::

    prompt_files: [core_profile.md, conversational_persona.md]   # concatenated, in order
    knob_header: "## Expression settings (live knobs — tuned in config)"
    knobs:                             # each rendered only if its value is present
      - {path: sharpness,      channels: [all],   text: "sharpness {v} — 0 gentle .. 1 cutting"}
      - {path: voice.singlish, channels: [voice], indent: 1, text: "singlish {v} (particle density)"}
    channel_note:                      # a fixed directive per channel (rendered before that
      voice:     "channel: VOICE — the burst/particle format applies"   # channel's scoped knobs)
      reasoning: "Register: substantive reasoning prose. …"

`channels: [all]` → every channel; otherwise the knob only renders on a matching `channel`.
`path` is dotted (`voice.singlish` reads values["voice"]["singlish"]). `{v}` is the value.
"""
from __future__ import annotations

import logging
import os
from typing import Any

log = logging.getLogger(__name__)


def _get(values: dict, path: str) -> Any:
    """Dotted-path lookup in the knob VALUES (e.g. 'voice.singlish'). Missing → None."""
    cur: Any = values or {}
    for part in path.split("."):
        if not isinstance(cur, dict) or part not in cur:
            return None
        cur = cur[part]
    return cur


def render_knobs(spec: dict, values: dict, channel: str) -> str:
    """Render the knob manifest against the VALUES for one channel. Returns '' if nothing renders.

    Order matches the host convention: header, then all-channel knobs, then this channel's note,
    then this channel's scoped knobs (indented per their `indent`)."""
    spec = spec or {}
    values = values or {}
    # No live knob values → no knob block at all (matches the host convention: an agent with no
    # `persona:` values gets prose only, not a bare header+note).
    if not values:
        return ""
    knobs = spec.get("knobs") or []
    header = spec.get("knob_header")
    lines: list[str] = []

    def _emit(knob: dict) -> None:
        v = _get(values, knob.get("path", ""))
        if v is None:
            return
        # `omit_if_empty`: suppress a knob whose value is present-but-falsey ("" / False / 0) — for
        # knobs that are only meaningful when set (e.g. a warmth channel or emoji policy). Knobs
        # WITHOUT this flag emit on any non-None value, so a numeric 0 (e.g. validation_budget) shows.
        if knob.get("omit_if_empty") and not v:
            return
        indent = "  " * int(knob.get("indent", 0) or 0)
        try:
            text = str(knob.get("text", "")).format(v=v)
        except Exception:  # noqa: BLE001 — a bad template must not crash prompt assembly
            text = str(knob.get("text", ""))
        lines.append(f"{indent}- {text}")

    # 1. all-channel knobs
    for knob in knobs:
        if "all" in (knob.get("channels") or []):
            _emit(knob)
    # 2. this channel's note (before its scoped knobs)
    note = (spec.get("channel_note") or {}).get(channel)
    if note:
        lines.append(f"- {note}")
    # 3. this channel's scoped knobs
    for knob in knobs:
        chans = knob.get("channels") or []
        if "all" not in chans and channel in chans:
            _emit(knob)

    if not lines:
        return ""
    return "\n".join(([header] if header else []) + lines)


def _read_prose(prompts_dir: str, name: str) -> str:
    try:
        with open(os.path.join(prompts_dir, name)) as f:
            return f.read()
    except FileNotFoundError:
        log.warning("[voice] prompt file missing: %s", name)
        return ""


def build_system_prompt(spec: dict, *, prompts_dir: str, values: dict | None = None,
                        channel: str = "reasoning") -> str:
    """Assemble the expressive system prompt: the manifest's `prompt_files` (concatenated, joined
    by a rule) followed by the rendered knobs for `channel`. `values` is the host's live knob block
    (e.g. agent.yaml `persona:`). Prose + manifest + values are all host config; this is the generic
    mechanism. Empty spec → ''."""
    spec = spec or {}
    parts = [t for t in (_read_prose(prompts_dir, n) for n in (spec.get("prompt_files") or [])) if t]
    body = "\n\n---\n\n".join(parts)
    knobs = render_knobs(spec, values or {}, channel)
    if knobs:
        body = (body + "\n\n" + knobs) if body else knobs
    return body
