#!/usr/bin/env python3
"""Check whether ``thinking: disabled`` is honoured by a relay (makes PAID calls).

Sends the same small translation request through Folith's real text transport
(``core.call_llm``) with thinking left at the provider default and with
``reasoning_mode="thinking_disabled"``, then prints what the provider reported:
completion_tokens, reasoning_tokens (None = not reported), reasoning_chars,
visible output chars and latency.  If the relay honours the control,
reasoning/completion tokens and latency should drop in the "disabled" rows
while the visible output stays comparable.  This script claims nothing itself;
read the table.

    FOLITH_VERIFY_API_KEY=sk-... python3 scripts/verify_thinking_disabled.py \\
        --provider "OpenCode Go" --model deepseek-v4.1-flash --repeats 1

Cost: 2 x --repeats requests (default: 1 thinking-default + 1 disabled).  The API key is
read from the environment only and is never printed.
"""
from __future__ import annotations

import argparse
import os
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

SYSTEM = "You are a professional translator. Translate the user's text into Simplified Chinese. Output only the translation."
USER = ("The committee reviewed the proposal twice before approving it, noting that the "
        "second draft addressed most of the earlier concerns about funding and timing.")


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--provider", default="OpenCode Go")
    ap.add_argument("--model", default="deepseek-v4.1-flash")
    ap.add_argument("--base-url", default=None)
    ap.add_argument("--repeats", type=int, default=1, help="requests per mode")
    args = ap.parse_args()
    key = os.environ.get("FOLITH_VERIFY_API_KEY", "")
    if not key:
        print("Set FOLITH_VERIFY_API_KEY (not echoed).", file=sys.stderr)
        return 2

    import core
    from openai.resources.chat.completions import Completions

    seen = {}
    original = Completions.create

    def spy(self, *a, **kw):
        started = time.perf_counter()
        res = original(self, *a, **kw)
        usage = getattr(res, "usage", None)
        details = getattr(usage, "completion_tokens_details", None)
        message = res.choices[0].message
        reasoning = getattr(message, "reasoning_content", None)
        seen.update(
            sent_extra_body=kw.get("extra_body"),
            completion_tokens=getattr(usage, "completion_tokens", None),
            reasoning_tokens=getattr(details, "reasoning_tokens", None),
            reasoning_chars=len(reasoning) if isinstance(reasoning, str) else None,
            visible_chars=len(message.content or ""),
            latency_s=time.perf_counter() - started)
        return res

    Completions.create = spy
    rows = []
    for i in range(args.repeats):
        for mode in ("provider_default", "thinking_disabled"):
            seen.clear()
            core.call_llm(args.provider, key, args.model, SYSTEM, USER,
                          temperature=0.3, base_url=args.base_url, reasoning_mode=mode)
            rows.append((mode, dict(seen)))

    print(f"{'mode':<18}{'sent extra_body':<34}{'compl_tok':>10}{'reason_tok':>11}"
          f"{'reason_chr':>11}{'visible':>8}{'latency_s':>10}")
    for mode, r in rows:
        print(f"{mode:<18}{str(r.get('sent_extra_body')):<34}{str(r.get('completion_tokens')):>10}"
              f"{str(r.get('reasoning_tokens')):>11}{str(r.get('reasoning_chars')):>11}"
              f"{r.get('visible_chars', 0):>8}{r.get('latency_s', 0):>10.2f}")
    print("\nNone = provider did not report that field (unknown, not zero).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
