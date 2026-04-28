import litert_lm
import sys

print("Loading model...")
try:
    engine = litert_lm.Engine(
        model_path="/root/algovibe/models/gemma-4-E2B-it.litertlm",
        backend=litert_lm.Backend.CPU,
    )
    print("Model loaded successfully!")
    
    sampler = litert_lm.SamplerConfig(temperature=0.1, top_k=10)
    with engine.create_conversation(sampler_config=sampler) as conv:
        for chunk in conv.send_message_async("Test"):
            content = chunk.get("content", [])
            if content:
                print(content[0].get("text", ""), end="", flush=True)
    print("\nDone")
except Exception as e:
    print(f"Error: {e}")
