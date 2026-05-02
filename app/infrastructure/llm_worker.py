import multiprocessing as mp
import time
import os
import json
import logging
from datetime import datetime

logger = logging.getLogger(__name__)

# ── LiteRT-LM Sampler Config untuk Trading ──────────────────────────────────
# Temperature rendah → output deterministik dan parseable untuk trading logic
LLM_TEMPERATURE  = 0.10   # Hampir greedy: jawaban konsisten, mudah di-parse
LLM_TOP_K        = 10     # Narrow sampling untuk mengurangi hallucination
LLM_MAX_TOKENS   = 300    # Cukup untuk analisis + veto, hemat CPU

# Protocol khusus untuk update consciousness di worker process
_CONSCIOUSNESS_SIGNAL = "__CONSCIOUSNESS__"


def _llm_process_loop(req_queue, res_queue, model_path, cache_dir):
    """
    Background worker process yang memuat LiteRT model ke CPU RAM.
    """
    import litert_lm
    from utils.logger import log_message

    try:
        engine = litert_lm.Engine(
            model_path=model_path,
            backend=litert_lm.Backend.CPU,
            cache_dir=cache_dir,
            max_num_tokens=2048,
        )
        log_message("🧠 LLM Worker: Engine initialized.")
    except Exception as e:
        log_message(f"🧠 LLM Worker: Failed to load model - {e}")
        return

    sampler = litert_lm.SamplerConfig(
        temperature=LLM_TEMPERATURE,
        top_k=LLM_TOP_K,
    )

    _consciousness_messages = [
        {
            "role": "user",
            "content": (
                "Kamu adalah brain dari sebuah quantitative scalping bot di exchange Indodax (Indonesia). "
                "Tugasmu: analisis sinyal trading kripto, beri verdict BUY/HOLD/AVOID, dan pelajari dari "
                "setiap trade yang pernah dilakukan. Kamu punya veto power — jika kamu bilang AVOID, "
                "bot TIDAK AKAN MASUK trade apapun. Jawab selalu ringkas, tepat, dan berdasarkan data."
            )
        },
        {
            "role": "assistant",
            "content": "Siap. Saya akan analisis dengan ketat dan hanya merekomendasikan BUY jika evidence kuat. Veto saya bersifat final."
        }
    ]

    while True:
        try:
            req = req_queue.get()
            if req is None:
                break

            task_id, task_type, prompt, mode = req

            if task_type == _CONSCIOUSNESS_SIGNAL:
                base = _consciousness_messages[:2]
                _consciousness_messages.clear()
                _consciousness_messages.extend(base)
                _consciousness_messages.append({"role": "user", "content": f"[MARKET UPDATE]\n{prompt}"})
                _consciousness_messages.append({"role": "assistant", "content": "Konteks terupdate. Siap untuk analisis."})
                log_message(f"🧠 LLM Consciousness: Updated.")
                continue

            ctx = list(_consciousness_messages)
            log_message(f"🧠 LLM Worker: Starting {mode} inference for {task_id}")

            if mode == "structured":
                result = _run_structured_inference(engine, sampler, ctx, prompt, task_type)
            else:
                result = _run_text_inference(engine, sampler, ctx, prompt)

            res_queue.put((task_id, task_type, result))

        except Exception as e:
            log_message(f"🧠 LLM Worker: Loop error - {e}")


def _parse_engine_response(res) -> str:
    """Helper untuk menangani output engine yang bisa berupa dict atau generator."""
    if isinstance(res, dict):
        content = res.get("content", [])
        if content and isinstance(content, list):
            return content[0].get("text", "").strip()
        return str(res)
    
    # Jika generator (streaming)
    if hasattr(res, '__iter__'):
        full_text = ""
        for chunk in res:
            if isinstance(chunk, dict):
                content = chunk.get("content", [])
                if content:
                    full_text += content[0].get("text", "")
            else:
                full_text += str(chunk)
        return full_text.strip()
    
    return str(res).strip()


def _run_text_inference(engine, sampler, context_messages: list, prompt: str) -> str:
    from utils.logger import log_message
    try:
        with engine.create_conversation(
            messages=context_messages,
            sampler_config=sampler
        ) as conv:
            res = conv.send_message(prompt)
            return _parse_engine_response(res)
    except Exception as e:
        log_message(f"🧠 LLM Worker: Text error - {e}")
        return f"Error: {e}"


def _run_structured_inference(engine, sampler, context_messages: list, prompt: str, task_type: str) -> str:
    import litert_lm
    from utils.logger import log_message

    verdict_result = {}

    def submit_verdict(
        verdict: str, confidence: int, reason: str,
        stop_loss_pct: float = 1.2, take_profit_pct: float = 2.0, eta_minutes: int = 15,
    ) -> str:
        nonlocal verdict_result
        verdict_result = {
            "verdict":         str(verdict).upper(),
            "confidence":      max(0, min(100, int(float(str(confidence).replace('%',''))))),
            "reason":          str(reason),
            "stop_loss_pct":   abs(float(stop_loss_pct)),
            "take_profit_pct": abs(float(take_profit_pct)),
            "eta_minutes":     max(1, min(1440, int(eta_minutes))),
        }
        return f"Verdict recorded: {verdict}"

    try:
        with engine.create_conversation(
            messages=context_messages,
            tools=[submit_verdict],
            automatic_tool_calling=True,
            sampler_config=sampler,
        ) as conv:
            res = conv.send_message(prompt)
            response_text = _parse_engine_response(res)
            
            if verdict_result:
                return json.dumps(verdict_result)
            
            return json.dumps({
                "verdict": "HOLD",
                "confidence": 0,
                "reason": f"Tool not called. AI says: {response_text}",
                "stop_loss_pct": 1.2,
                "take_profit_pct": 2.0
            })
    except Exception as e:
        return json.dumps({"verdict": "HOLD", "confidence": 0, "reason": f"error: {e}"})


class LLMWorker:
    _instance = None
    @classmethod
    def get_instance(cls):
        if cls._instance is None: cls._instance = cls()
        return cls._instance

    def __init__(self):
        from config import MODEL_PATH, BASE_PATH
        
        # Use 'spawn' context for everything to avoid SemLock errors
        self._ctx = mp.get_context('spawn')
        self.req_queue = self._ctx.Queue()
        self.res_queue = self._ctx.Queue()
        
        self.model_path = os.path.join(BASE_PATH, MODEL_PATH)
        base_dir = os.path.dirname(self.model_path)
        self.cache_dir = os.path.join(base_dir, "cache")
        if not os.path.exists(self.cache_dir): os.makedirs(self.cache_dir, exist_ok=True)

        if os.path.exists(self.model_path):
            self.worker_process = self._ctx.Process(
                target=_llm_process_loop,
                args=(self.req_queue, self.res_queue, self.model_path, self.cache_dir),
                daemon=True, name="LLMWorker"
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
        # Watchdog logic
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
                self.results[tid] = {"type": ttype, "result": res, "time": datetime.now()}
                if tid == self._current_task_id: self._current_task_id = None
            except: break

    def get_result(self, task_id: int) -> dict | None:
        self.check_results()
        return self.results.pop(task_id, None)

    @staticmethod
    def parse_structured_result(result_str: str) -> dict:
        defaults = {"verdict": "HOLD", "confidence": 0, "reason": "Parsed", "stop_loss_pct": 1.2, "take_profit_pct": 2.0, "eta_minutes": 15}
        if not result_str: return defaults
        try:
            parsed = json.loads(result_str)
            defaults.update(parsed)
            return defaults
        except:
            raw = result_str.upper()
            if "BUY" in raw: defaults.update({"verdict": "BUY", "confidence": 60})
            elif "AVOID" in raw: defaults.update({"verdict": "AVOID", "confidence": 70})
            return defaults
