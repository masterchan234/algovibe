import litert_lm
import os
import json

model_path = "/home/chan/projects/algovibe/models/gemma-4-E2B-it.litertlm"

verdict_result = {}
def submit_verdict(verdict: str, confidence: int, reason: str, **kwargs) -> str:
    global verdict_result
    verdict_result = {"verdict": verdict, "confidence": confidence, "reason": reason}
    return "OK"

print(f"Testing structured response...")
try:
    engine = litert_lm.Engine(model_path=model_path, max_num_tokens=2048)
    sampler = litert_lm.SamplerConfig(temperature=0.0)
    with engine.create_conversation(
        messages=[], 
        tools=[submit_verdict], 
        automatic_tool_calling=True,
        sampler_config=sampler
    ) as conv:
        # Try SYNC first
        print("Trying SYNC send_message...")
        res = conv.send_message("Analyze BTC and call submit_verdict with BUY, 80, 'Bullish'")
        print(f"Sync Result: {res}")
        print(f"Verdict Result: {verdict_result}")
        
        # Reset
        verdict_result = {}
        
        # Try ASYNC (iterating)
        print("\nTrying ASYNC send_message (iterating)...")
        for chunk in conv.send_message("Analyze ETH and call submit_verdict with AVOID, 90, 'Bearish'"):
            pass # just iterate
        print(f"Verdict Result: {verdict_result}")

except Exception as e:
    print(f"Error: {e}")
