import litert_lm
import os

model_path = "/home/chan/projects/algovibe/models/gemma-4-E2B-it.litertlm"
cache_dir = "/home/chan/projects/algovibe/models/cache"

print(f"Loading engine...")
engine = litert_lm.Engine(
    model_path=model_path,
    backend=litert_lm.Backend.CPU,
    max_num_tokens=2048,
)

sampler = litert_lm.SamplerConfig(temperature=0.0)

# Simulate Consciousness Context
messages = [
    {"role": "user", "content": "You are a crypto bot."},
    {"role": "assistant", "content": "Understood."},
    {"role": "user", "content": "[MARKET UPDATE] BTC is pumping."}
]

print("Creating conversation with context...")
try:
    with engine.create_conversation(messages=messages, sampler_config=sampler) as conv:
        print("Sending 'What is the market status?'...")
        res = conv.send_message("What is the market status?")
        print(f"Response: {res}")
except Exception as e:
    print(f"CRASHED: {e}")

