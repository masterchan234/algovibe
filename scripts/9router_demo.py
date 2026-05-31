#!/usr/bin/env python3
"""
Simple CLI to exercise the 9Router client (health, models, chat, embeddings).

Usage examples:
  rtk python scripts/9router_demo.py health
  rtk python scripts/9router_demo.py models --kind embedding
  rtk python scripts/9router_demo.py chat --model openai/gpt-5 --message "Hello"
  rtk python scripts/9router_demo.py embed --model tavily/embedding --input "hello world"
"""
import argparse
import json
import sys

from utils import ninerouter_client as client


def main() -> None:
    parser = argparse.ArgumentParser(description="9Router demo CLI")
    sub = parser.add_subparsers(dest="cmd")

    sub.add_parser("health", help="Run health check")

    p_models = sub.add_parser("models", help="List models")
    p_models.add_argument("--kind", help="Optional model kind (image/tts/embedding/web/stt/image-to-text)")

    p_chat = sub.add_parser("chat", help="Create chat completion")
    p_chat.add_argument("--model", required=True, help="Model id from /v1/models")
    p_chat.add_argument("--message", required=True, help="User message text")
    p_chat.add_argument("--temperature", type=float, default=0.0)

    p_embed = sub.add_parser("embed", help="Create embedding")
    p_embed.add_argument("--model", required=True)
    p_embed.add_argument("--input", required=True)

    args = parser.parse_args()
    if not args.cmd:
        parser.print_help()
        return

    try:
        if args.cmd == "health":
            print(json.dumps(client.health_check(), indent=2))
        elif args.cmd == "models":
            print(json.dumps(client.list_models(kind=getattr(args, "kind", None)), indent=2))
        elif args.cmd == "chat":
            messages = [{"role": "user", "content": args.message}]
            print(json.dumps(client.chat_create(args.model, messages, temperature=args.temperature), indent=2))
        elif args.cmd == "embed":
            print(json.dumps(client.create_embedding(args.model, args.input), indent=2))
        else:
            parser.print_help()
    except Exception as e:
        print("Error:", e, file=sys.stderr)
        sys.exit(2)


if __name__ == "__main__":
    main()
