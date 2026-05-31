#!/usr/bin/env python3
"""
Minimal 9Router client helpers.

This module implements a tiny OpenAI-compatible client for 9Router
gateways. It reads `NINEROUTER_URL` and optional `NINEROUTER_KEY` from
the environment.
"""
from typing import Any, Dict, List, Optional
import os
import requests


def _base_url() -> str:
    return os.getenv("NINEROUTER_URL", "http://localhost:20128").rstrip("/")


def _headers() -> Dict[str, str]:
    headers: Dict[str, str] = {"Accept": "application/json"}
    key = os.getenv("NINEROUTER_KEY")
    if key:
        headers["Authorization"] = f"Bearer {key}"
    return headers


def health_check(timeout: float = 5.0) -> Dict[str, Any]:
    url = f"{_base_url()}/api/health"
    r = requests.get(url, headers=_headers(), timeout=timeout)
    r.raise_for_status()
    return r.json()


def list_models(kind: Optional[str] = None, timeout: float = 10.0) -> Dict[str, Any]:
    url = f"{_base_url()}/v1/models"
    if kind:
        url = f"{url}/{kind}"
    r = requests.get(url, headers=_headers(), timeout=timeout)
    r.raise_for_status()
    return r.json()


def chat_create(model: str, messages: List[Dict[str, str]], temperature: float = 0.0, timeout: float = 60.0) -> Dict[str, Any]:
    """
    Create a chat completion using an OpenAI-compatible endpoint.

    messages should be a list of dicts like:
      [{"role": "user", "content": "Hello"}]
    """
    url = f"{_base_url()}/v1/chat/completions"
    payload = {"model": model, "messages": messages, "temperature": temperature}
    r = requests.post(url, headers=_headers(), json=payload, timeout=timeout)
    r.raise_for_status()
    return r.json()


def create_embedding(model: str, input_text: str, timeout: float = 30.0) -> Dict[str, Any]:
    url = f"{_base_url()}/v1/embeddings"
    payload = {"model": model, "input": input_text}
    r = requests.post(url, headers=_headers(), json=payload, timeout=timeout)
    r.raise_for_status()
    return r.json()


__all__ = ["health_check", "list_models", "chat_create", "create_embedding"]
