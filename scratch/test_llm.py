import os
import sys
import time
sys.path.append(os.getcwd())
from app.infrastructure.llm_worker import LLMWorker

if __name__ == '__main__':
    print("Testing LLMWorker initialization...")
    worker = LLMWorker.get_instance()
    print(f"Worker is_active: {worker.is_active}")
    if worker.is_active:
        print(f"Worker process: {worker.worker_process}")
        print(f"Worker process alive: {worker.worker_process.is_alive()}")
        
        print("Queueing task...")
        tid = worker.query_async("TEST", "Hello, are you there?")
        print(f"Task ID: {tid}")
        
        for i in range(30):
            time.sleep(1)
            worker.check_results()
            res = worker.get_result(tid)
            if res:
                print(f"Result: {res}")
                break
            print(f"Waiting... {i+1}")
    else:
        print("Worker is NOT active.")
