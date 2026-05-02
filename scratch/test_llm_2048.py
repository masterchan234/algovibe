import litert_lm
import os
import sys

model_path = "/home/chan/projects/algovibe/models/gemma-4-E2B-it.litertlm"
cache_dir = "/home/chan/projects/algovibe/models/cache"

print(f"Loading engine with 2048 tokens...")
try:
    engine = litert_lm.Engine(
        model_path=model_path,
        backend=litert_lm.Backend.CPU,
        max_num_tokens=2048,
    )

    sampler = litert_lm.SamplerConfig(temperature=0.0)

    print("Creating conversation...")
    with engine.create_conversation(messages=[], sampler_config=sampler) as conv:
        print("Sending 'Hi'...")
        response = ""
        for chunk in conv.send_message("Hi"):
            print(f"Chunk: {chunk}")
            if isinstance(chunk, dict):
                content = chunk.get("content", [])
                if content:
                    response += content[0].get("text", "")
            else:
                response += chunk
        print(f"Final Response: {response}")
except Exception as e:
    print(f"Error: {e}")
print("Done.")
