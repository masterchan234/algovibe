import os
import time
import logging
from datetime import datetime
import multiprocessing

logger = logging.getLogger(__name__)

_CONSCIOUSNESS_SIGNAL = "__CONSCIOUSNESS_UPDATE__"

def _parse_engine_response(response):
    """
    Tangguh terhadap berbagai tipe kembalian litert_lm (Response, list, dict, str).
    """
    try:
        # Case 1: Sudah string
        if isinstance(response, str):
            return response
            
        # Case 2: Response object dengan .text (Gemma-2 style)
        if hasattr(response, 'text') and not callable(response.text):
            return response.text
            
        # Case 3: Iterable (list atau generator)
        if hasattr(response, '__iter__'):
            full_text = ""
            for chunk in response:
                if isinstance(chunk, dict):
                    full_text += chunk.get("text", chunk.get("content", ""))
                elif isinstance(chunk, str):
                    full_text += chunk
                else:
                    # Mungkin ini adalah object Part
                    full_text += getattr(chunk, "text", str(chunk))
            if full_text: return full_text

        # Case 4: Dict tunggal
        if isinstance(response, dict):
            return response.get("text", response.get("content", str(response)))
            
    except Exception as e:
        logger.error(f"Error parsing LLM response: {e}")
        
    return str(response)

def _llm_process_loop(req_queue, res_queue, model_path, cache_dir):
    try:
        from utils.logger import log_message
        from litert_lm import Engine
        
        log_message(f"🧠 LLM Worker: Initializing engine with {model_path}")
        engine = Engine(model_path, cache_dir=cache_dir)
        
        _consciousness_messages = []
        _current_conv = None
        log_message("🧠 LLM Worker: Engine initialized.")

        while True:
            try:
                task_id, task_type, prompt, mode = req_queue.get()
                
                if task_type == _CONSCIOUSNESS_SIGNAL:
                    _consciousness_messages = [{"role": "user", "content": prompt}]
                    continue

                # Hapus session sebelumnya agar tidak terjadi FAILED_PRECONDITION
                if _current_conv is not None:
                    try:
                        del _current_conv
                    except Exception:
                        pass
                    _current_conv = None

                conv = engine.create_conversation()
                _current_conv = conv
                if _consciousness_messages:
                    conv.messages = list(_consciousness_messages)

                log_message(f"🧠 LLM Worker: Starting {mode} inference for {task_id}")
                response = conv.send_message(prompt)
                result = _parse_engine_response(response)

                log_message(f"🧠 LLM Worker: Task {task_id} completed.")
                res_queue.put((task_id, task_type, result))
            except Exception as e:
                log_message(f"🧠 LLM Worker: Inference error - {e}")
                if task_id:
                    res_queue.put((task_id, task_type, f"inference error: {e}"))
    except Exception as e:
        print(f"CRITICAL LLM ERROR: {e}")

class LLMWorker:
    _instance = None

    @classmethod
    def get_instance(cls):
        if cls._instance is None:
            cls._instance = cls()
        return cls._instance

    def __init__(self):
        from config import MODEL_PATH, MODEL_CACHE_DIR
        self.model_path = MODEL_PATH
        self.cache_dir = MODEL_CACHE_DIR
        
        if not os.path.exists(self.cache_dir):
            os.makedirs(self.cache_dir, exist_ok=True)
        
        self._ctx = multiprocessing.get_context('spawn')
        self.req_queue = self._ctx.Queue()
        self.res_queue = self._ctx.Queue()
        
        if os.path.exists(self.model_path):
            self.worker_process = self._ctx.Process(
                target=_llm_process_loop, 
                args=(self.req_queue, self.res_queue, self.model_path, self.cache_dir),
                daemon=True
            )
            self.worker_process.start()
            self.is_active = True
        else:
            self.is_active = False

        self.task_counter = 0
        self.results = {}
        self._current_task_id = None
        self._current_task_ts = None

    def query_async(self, task_type: str, prompt: str) -> int | None:
        if not self.is_active: return None
        self.task_counter += 1
        self._current_task_id = self.task_counter
        self._current_task_ts = datetime.now()
        self.req_queue.put((self.task_counter, task_type, prompt, "text"))
        return self.task_counter

    def query_structured(self, task_type: str, prompt: str) -> int | None:
        if not self.is_active: return None
        self.task_counter += 1
        self._current_task_id = self.task_counter
        self._current_task_ts = datetime.now()
        self.req_queue.put((self.task_counter, task_type, prompt, "structured"))
        return self.task_counter

    def update_consciousness(self, context_str: str):
        if self.is_active: self.req_queue.put((None, _CONSCIOUSNESS_SIGNAL, context_str, "context"))

    def check_results(self):
        if not self.is_active: return
        
        if self.worker_process and self.worker_process.is_alive() and self._current_task_id:
            if (datetime.now() - self._current_task_ts).total_seconds() > 120:
                logger.error(f"🧠 [LLMWorker] Task {self._current_task_id} HANGING. Restarting...")
                self.worker_process.terminate()
                self.worker_process.join(timeout=1)
                self.worker_process = self._ctx.Process(target=_llm_process_loop, args=(self.req_queue, self.res_queue, self.model_path, self.cache_dir), daemon=True)
                self.worker_process.start()
                self._current_task_id = None

        while not self.res_queue.empty():
            try:
                tid, ttype, res = self.res_queue.get_nowait()
                if tid:
                    self.results[tid] = {"type": ttype, "result": res, "time": datetime.now()}
                if tid == self._current_task_id:
                    self._current_task_id = None
            except:
                break

    def get_result(self, task_id: int) -> dict | None:
        self.check_results()
        return self.results.pop(task_id, None)

    def get_latest_result_by_type(self, task_type: str) -> str | None:
        """Ambil hasil terakhir berdasarkan tipe task."""
        self.check_results()
        matching_tids = [tid for tid, data in self.results.items() if data["type"] == task_type]
        if not matching_tids:
            return None
        latest_tid = max(matching_tids)
        return self.results[latest_tid]["result"]

    @staticmethod
    def parse_structured_result(result_str: str) -> dict:
        import json
        defaults = {"verdict": "HOLD", "confidence": 0, "reason": f"Tool not called. AI says: {result_str}", "stop_loss_pct": 1.2, "take_profit_pct": 2.0}
        if not result_str: return defaults
        try:
            if "{" in result_str and "}" in result_str:
                start = result_str.find("{")
                end = result_str.rfind("}") + 1
                return json.loads(result_str[start:end])
            return json.loads(result_str)
        except:
            raw = str(result_str).upper()
            if "BUY" in raw: defaults.update({"verdict": "BUY", "confidence": 60})
            elif "AVOID" in raw: defaults.update({"verdict": "AVOID", "confidence": 70})
            elif "HOLD" in raw: defaults.update({"verdict": "HOLD", "confidence": 50})
            return defaults
