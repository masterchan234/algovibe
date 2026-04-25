import os
import time
from huggingface_hub import hf_hub_download
import litert_lm

def download_model():
    model_path = "models/gemma-4-E2B-it.litertlm"
    print(f"Using local model: {model_path}")
    return model_path

def test_inference(model_path):
    print("\nInitializing LiteRT-LM Engine (CPU)...")
    
    # Initialize engine
    # By default, without specifying backend it uses CPU.
    # Note: LitertLM requires specific configurations depending on the version, 
    # but basic setup is simple.
    start_load = time.time()
    try:
        engine = litert_lm.Engine(model_path, backend=litert_lm.Backend.CPU)
    except AttributeError:
        # Fallback if backend enum varies in nightly
        engine = litert_lm.Engine(model_path)
        
    print(f"Engine initialized in {time.time() - start_load:.2f} seconds.")
    
    # Test conversation
    test_prompt = "You are an expert crypto analyst. Briefly define what a 'Fake Pump' is in 2 sentences."
    print(f"\nPrompt: '{test_prompt}'")
    print("Response: ", end="", flush=True)
    
    with engine.create_conversation() as conversation:
        start_inf = time.time()
        token_count = 0
        
        for chunk in conversation.send_message_async(test_prompt):
            text = chunk["content"][0]["text"]
            print(text, end="", flush=True)
            token_count += 1
            
        inf_time = time.time() - start_inf
        
    print(f"\n\nMetrics:")
    print(f"Tokens output roughly: {token_count} chunks")
    print(f"Inference Time: {inf_time:.2f} seconds")
    print(f"Speed: {token_count / inf_time:.2f} chunks/sec")

if __name__ == "__main__":
    os.makedirs("models", exist_ok=True)
    m_path = download_model()
    test_inference(m_path)
