import litert_lm
import os

model_path = "/home/chan/projects/algovibe/models/gemma-4-E2B-it.litertlm"

print(f"Testing chunk format...")
try:
    engine = litert_lm.Engine(
        model_path=model_path,
        max_num_tokens=2048,
    )
    sampler = litert_lm.SamplerConfig(temperature=0.0)
    with engine.create_conversation(messages=[], sampler_config=sampler) as conv:
        for chunk in conv.send_message("Hi"):
            print(f"TYPE: {type(chunk)} | REPR: {repr(chunk)}")
            # If it's a dict, what's inside?
            if isinstance(chunk, dict):
                print(f"KEYS: {chunk.keys()}")
except Exception as e:
    print(f"Error: {e}")
