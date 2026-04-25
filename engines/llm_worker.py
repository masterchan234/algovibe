import multiprocessing as mp
import time
import os
import logging
from datetime import datetime

logger = logging.getLogger(__name__)

def _llm_process_loop(req_queue, res_queue, model_path):
    """
    Background worker process that loads the LiteRT model into CPU RAM.
    It reads prompts from req_queue, processes them, and puts results into res_queue.
    """
    import litert_lm
    from utils.logger import log_message
    
    try:
        engine = litert_lm.Engine(model_path, backend=litert_lm.Backend.CPU)
        log_message("🧠 LLM Worker: Gemma-4-E2B Engine initialized successfully.")
    except Exception as e:
        log_message(f"🧠 LLM Worker: Failed to load model - {e}")
        return

    while True:
        try:
            req = req_queue.get()
            if req is None:
                # Shutdown signal
                break
            
            task_id, task_type, prompt = req
            
            response_text = ""
            with engine.create_conversation() as conversation:
                for chunk in conversation.send_message_async(prompt):
                    response_text += chunk["content"][0]["text"]
            
            res_queue.put((task_id, task_type, response_text.strip()))
            
        except Exception as e:
            log_message(f"🧠 LLM Worker: Inference error - {e}")

class LLMWorker:
    """
    Singleton manager to interact with the background LLM process.
    """
    _instance = None
    
    @classmethod
    def get_instance(cls):
        if cls._instance is None:
            cls._instance = cls()
        return cls._instance
        
    def __init__(self):
        self.req_queue = mp.Queue()
        self.res_queue = mp.Queue()
        self.model_path = os.path.join(os.path.dirname(__file__), "..", "models", "gemma-4-E2B-it.litertlm")
        
        # Start only if model exists
        if os.path.exists(self.model_path):
            self.worker_process = mp.Process(
                target=_llm_process_loop, 
                args=(self.req_queue, self.res_queue, self.model_path), 
                daemon=True
            )
            self.worker_process.start()
            self.is_active = True
        else:
            self.is_active = False
            logger.warning(f"LLMWorker: Model not found at {self.model_path}")
            
        self.task_counter = 0
        self.results = {}
        
    def query_async(self, task_type, prompt):
        """Send a prompt to the worker."""
        if not self.is_active:
            return None
        self.task_counter += 1
        self.req_queue.put((self.task_counter, task_type, prompt))
        return self.task_counter
        
    def check_results(self):
        """Poll the response queue."""
        if not self.is_active: return
        while not self.res_queue.empty():
            task_id, task_type, result = self.res_queue.get_nowait()
            self.results[task_id] = {"type": task_type, "result": result, "time": datetime.now()}
            
    def get_result(self, task_id):
        self.check_results()
        if task_id in self.results:
            return self.results.pop(task_id)
        return None
        
    def get_latest_result_by_type(self, task_type):
        self.check_results()
        # Find the most recent result for a specific type
        matches = {k: v for k, v in self.results.items() if v["type"] == task_type}
        if matches:
            latest_id = max(matches.keys())
            return self.results.pop(latest_id)["result"]
        return None
