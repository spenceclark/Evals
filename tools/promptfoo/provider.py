"""Canned-output provider: returns the test's `reply` var verbatim. No LLM involved."""

import sys


def call_api(prompt: str, options: dict, context: dict) -> dict:
    return {"output": context["vars"]["reply"], "metadata": {"python": sys.version}}
