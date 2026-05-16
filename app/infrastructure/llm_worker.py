import os
import time
import queue as _queue
import logging
from datetime import datetime
import multiprocessing

logger = logging.getLogger(__name__)

_CONSCIOUSNESS_SIGNAL = "__CONSCIOUSNESS_UPDATE__"


def _is_session_conflict(err: Exception | str) -> bool:
    raw = str(err or "").upper()
    return "FAILED_PRECONDITION" in raw and "SESSION ALREADY EXISTS" in raw


def _humanize_inference_error(err: Exception | str) -> str:
    raw = str(err or "")
    if _is_session_conflict(raw):
        return "inference error: LLM session conflict. Worker auto-recovered, please retry."
    return f"inference error: {raw}"

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

        # Case 4 (DULU): Dict tunggal — harus dicek SEBELUM iterable
        # karena dict juga iterable dan iterasinya menghasilkan keys, bukan values
        if isinstance(response, dict):
            # Gemma-4 format: {"content": [{"text": "..."}], "role": "model"}
            content_field = response.get("content")
            if isinstance(content_field, list):
                parts = []
                for part in content_field:
                    if isinstance(part, dict):
                        parts.append(part.get("text", ""))
                    elif isinstance(part, str):
                        parts.append(part)
                text = "".join(parts)
                if text:
                    return text
            elif isinstance(content_field, str):
                return content_field
            # Fallback: key "text" langsung
            return response.get("text", str(response))

        # Case 3: Iterable (list atau generator) — dict sudah ditangani di atas
        if hasattr(response, '__iter__'):
            full_text = ""
            for chunk in response:
                if isinstance(chunk, dict):
                    # Gemma-4 chunk format: {"content": [{"text": "..."}], "role": "model"}
                    content_field = chunk.get("content")
                    if isinstance(content_field, list):
                        for part in content_field:
                            if isinstance(part, dict):
                                full_text += part.get("text", "")
                            elif isinstance(part, str):
                                full_text += part
                    elif isinstance(content_field, str):
                        full_text += content_field
                    else:
                        full_text += chunk.get("text", "")
                elif isinstance(chunk, str):
                    full_text += chunk
                else:
                    text_val = getattr(chunk, "text", None)
                    if text_val and isinstance(text_val, str):
                        full_text += text_val
            if full_text:
                return full_text

    except Exception as e:
        logger.error(f"[SYSTEM] Error parsing LLM response: {e}")

    return str(response)

def _llm_process_loop(req_queue, res_queue, model_path, cache_dir):
    try:
        import warnings
        import logging as _logging
        # Suppress unnecessary mel_filterbank warnings from Gemma4DataProcessor
        # (not needed for text-based analysis)
        warnings.filterwarnings("ignore", message=".*mel_filterbank.*")
        _logging.getLogger("Gemma4DataProcessor").setLevel(_logging.ERROR)
        _logging.getLogger("litert_lm").setLevel(_logging.WARNING)

        from utils.logger import log_message
        from litert_lm import Engine
        
        log_message(f"[LLM-WORKER] Initializing engine with {model_path}")
        engine = Engine(model_path, cache_dir=cache_dir)
        
        _consciousness_messages = []
        log_message("[LLM-WORKER] Engine initialized.")

        _TASK_TIMEOUT_SEC = 90  # Per-task hard timeout to prevent hanging

        while True:
            task_id = None
            task_type = None
            try:
                try:
                    task_id, task_type, prompt, mode = req_queue.get(timeout=5)
                except _queue.Empty:
                    # Queue empty — loop back to check for new tasks
                    continue
                
                if task_type == _CONSCIOUSNESS_SIGNAL:
                    _consciousness_messages = [{"role": "user", "content": prompt}]
                    continue

                # Retry otomatis 1x jika session conflict transient muncul.
                last_error = None
                result = None
                import threading as _threading
                _inference_result = [None]
                _inference_error = [None]

                def _run_inference(attempt_num):
                    try:
                        conv_messages = list(_consciousness_messages) if _consciousness_messages else []
                        with engine.create_conversation(messages=conv_messages) as conv:
                            log_message(f"[LLM-WORKER] Starting {mode} inference for {task_id} (attempt {attempt_num}/2)")
                            response = conv.send_message(prompt)
                            _inference_result[0] = _parse_engine_response(response)
                    except Exception as _ie:
                        _inference_error[0] = _ie

                for attempt in range(2):
                    _inference_result[0] = None
                    _inference_error[0] = None
                    t = _threading.Thread(target=_run_inference, args=(attempt + 1,), daemon=True)
                    t.start()
                    t.join(timeout=_TASK_TIMEOUT_SEC)
                    if t.is_alive():
                        # Python does not provide a way to force-terminate threads, so a timed-out
                        # inference thread will remain alive until the worker process is restarted.
                        # The outer LLMWorker.check_results() watchdog handles process-level restart
                        # when needed, bounding the total lifecycle of any leaked thread.
                        log_message(f"[LLM-WORKER] Task {task_id} timed out after {_TASK_TIMEOUT_SEC}s. Aborting.")
                        last_error = TimeoutError(f"Inference timed out after {_TASK_TIMEOUT_SEC}s")
                        break
                    if _inference_error[0] is not None:
                        last_error = _inference_error[0]
                        if _is_session_conflict(_inference_error[0]) and attempt == 0:
                            log_message("[LLM-WORKER] Session conflict detected, retrying task once...")
                            continue
                        break
                    result = _inference_result[0]
                    break

                if result is None and last_error is not None:
                    raise last_error

                log_message(f"[LLM-WORKER] Task {task_id} completed.")
                res_queue.put((task_id, task_type, result))
            except Exception as e:
                friendly_err = _humanize_inference_error(e)
                log_message(f"[LLM-WORKER] Inference error - {friendly_err}")
                if task_id:
                    res_queue.put((task_id, task_type, friendly_err))
    except Exception as e:
        print(f"[CRITICAL] LLM ERROR: {e}")

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
            if (datetime.now() - self._current_task_ts).total_seconds() > 60:
                logger.error(f"[LLM-WORKER] Task {self._current_task_id} HANGING. Restarting...")
                self.worker_process.terminate()
                self.worker_process.join(timeout=3)
                if self.worker_process.is_alive():
                    self.worker_process.kill()
                    self.worker_process.join(timeout=2)
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
        safe_reason = str(result_str)
        upper_reason = safe_reason.upper()
        if "FAILED_PRECONDITION" in upper_reason and "SESSION ALREADY EXISTS" in upper_reason:
            safe_reason = "LLM session conflict detected. Auto-recovered and waiting next analysis cycle."

        defaults = {"verdict": "HOLD", "confidence": 0, "reason": f"Tool not called. AI says: {safe_reason}", "stop_loss_pct": 1.2, "take_profit_pct": 2.0}
        if not result_str: return defaults
        try:
            if "{" in result_str and "}" in result_str:
                start = result_str.find("{")
                end = result_str.rfind("}") + 1
                return json.loads(result_str[start:end])
            return json.loads(result_str)
        except:
            raw = str(result_str).upper()
            if "PUMPALERT" in raw or "PUMP_ALERT" in raw: defaults.update({"verdict": "PUMPALERT", "confidence": 75})
            elif "BUY" in raw: defaults.update({"verdict": "BUY", "confidence": 60})
            elif "AVOID" in raw: defaults.update({"verdict": "AVOID", "confidence": 70})
            elif "HOLD" in raw: defaults.update({"verdict": "HOLD", "confidence": 50})
            return defaults
