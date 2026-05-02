import litert_lm
import os

model_path = "/home/chan/projects/algovibe/models/gemma-4-E2B-it.litertlm"

print(f"Testing direct response...")
try:
    engine = litert_lm.Engine(model_path=model_path, max_num_tokens=2048)
    sampler = litert_lm.SamplerConfig(temperature=0.0)
    with engine.create_conversation(messages=[], sampler_config=sampler) as conv:
        response = conv.send_message("Hi")
        print(f"TYPE: {type(response)}")
        print(f"RESPONSE: {response}")
except Exception as e:
    print(f"Error: {e}")
