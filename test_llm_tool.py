import litert_lm
import sys

print("Loading model...")
try:
    engine = litert_lm.Engine(
        model_path="/root/algovibe/models/gemma-4-E2B-it.litertlm",
        backend=litert_lm.Backend.CPU,
    )
    print("Model loaded successfully!")
    
    verdict_result = {}
    def submit_verdict(verdict: str):
        global verdict_result
        verdict_result = {"verdict": verdict}
        return "recorded"
        
    sampler = litert_lm.SamplerConfig(temperature=0.1, top_k=10)
    with engine.create_conversation(tools=[submit_verdict], automatic_tool_calling=True, sampler_config=sampler) as conv:
        # test send_message
        res = conv.send_message("Please call the submit_verdict tool and say BUY.")
        
        # In litert API, send_message returns a generator.
        # Let's see what happens if we just call it and do nothing:
        print("Result from send_message generator:", res)
        # We must iterate it!
        for chunk in res:
            pass
            
        print("Verdict result:", verdict_result)
        
except Exception as e:
    print(f"Error: {e}")
