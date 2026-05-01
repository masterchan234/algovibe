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

    Protocol req_queue:
      - Text   : (task_id, task_type, prompt, "text")
      - Struct : (task_id, task_type, prompt, "structured")
      - Context: (None, "__CONSCIOUSNESS__", context_str, "context")
      - Shutdown: None

    [Fix BUG #3]  Gunakan cache_dir agar XNNPACK artifacts di-reuse (752MB).
    [Fix ISSUE #7] SamplerConfig temperature rendah → deterministic output.
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
        log_message("🧠 LLM Worker: Gemma-4-E2B Engine initialized (cache reused).")
    except Exception as e:
        log_message(f"🧠 LLM Worker: Failed to load model - {e}")
        return

    sampler = litert_lm.SamplerConfig(
        temperature=LLM_TEMPERATURE,
        top_k=LLM_TOP_K,
    )

    # ── Consciousness: konteks pasar yang di-inject ke setiap conversation ──
    # Format: list of {"role": "...", "content": "..."}
    # Di-update secara periodik oleh Oracle via _CONSCIOUSNESS_SIGNAL
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
                break  # Shutdown signal

            task_id, task_type, prompt, mode = req

            # ── Consciousness Update ─────────────────────────────────────────
            if task_type == _CONSCIOUSNESS_SIGNAL:
                # Update context yang dipakai di semua inferensi berikutnya
                base = _consciousness_messages[:2]  # Pertahankan system identity
                _consciousness_messages.clear()
                _consciousness_messages.extend(base)
                # Inject new market awareness
                _consciousness_messages.append({"role": "user", "content": f"[MARKET UPDATE]\n{prompt}"})
                _consciousness_messages.append({"role": "assistant", "content": "Konteks terupdate. Siap untuk analisis."})
                log_message(f"🧠 LLM Consciousness: Updated ({len(prompt)} chars).")
                continue

            # ── Inference ───────────────────────────────────────────────────
            ctx = list(_consciousness_messages)  # snapshot agar aman di-pass

            if mode == "structured":
                result = _run_structured_inference(engine, sampler, ctx, prompt, task_type)
            else:
                result = _run_text_inference(engine, sampler, ctx, prompt)

            res_queue.put((task_id, task_type, result))

        except Exception as e:
            log_message(f"🧠 LLM Worker: Inference error - {e}")


def _run_text_inference(engine, sampler, context_messages: list, prompt: str) -> str:
    """Inferensi teks biasa dengan consciousness context."""
    response_text = ""
    with engine.create_conversation(
        messages=context_messages,
        sampler_config=sampler
    ) as conv:
        for chunk in conv.send_message_async(prompt):
            content = chunk.get("content", [])
            if content and isinstance(content, list):
                response_text += content[0].get("text", "")
    return response_text.strip()


def _run_structured_inference(engine, sampler, context_messages: list, prompt: str, task_type: str) -> str:
    """
    Inferensi dengan Function Calling untuk output JSON terstruktur.
    Model memanggil submit_verdict() → langsung dapat dict tanpa string parsing.
    """
    import litert_lm

    verdict_result = {}

    def submit_verdict(
        verdict: str,
        confidence: int,
        reason: str,
        stop_loss_pct: float = 1.2,
        take_profit_pct: float = 2.0,
        eta_minutes: int = 15,
    ) -> str:
        """
        Submit trading verdict setelah analisis mendalam.
        Args:
            verdict: BUY jika layak masuk, HOLD jika tunggu, AVOID jika berbahaya/veto
            confidence: Keyakinan 0-100. Di bawah 55 dianggap HOLD
            reason: Satu kalimat ringkas alasan keputusan
            stop_loss_pct: SL dalam % dari harga entry (default 1.2%)
            take_profit_pct: TP dalam % dari harga entry (default 2.0%)
            eta_minutes: Estimasi waktu mencapai target dalam menit (default 15)
        """
        nonlocal verdict_result
        clean_verdict = str(verdict).replace('<|"|>', '').replace('<', '').replace('>', '').replace('"', '').replace('|', '').strip().upper()
        clean_reason = str(reason).replace('<|"|>', '').replace('<', '').replace('>', '').replace('|', '').strip()
        verdict_result = {
            "verdict":         clean_verdict,
            "confidence":      max(0, min(100, int(confidence))),
            "reason":          clean_reason,
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
            # Harus di-iterasi sampai habis agar C++ engine tidak deadlock
            response_text = ""
            for chunk in conv.send_message(prompt):
                if isinstance(chunk, dict):
                    content = chunk.get("content", [])
                    if content and isinstance(content, list):
                        response_text += content[0].get("text", "")
                elif isinstance(chunk, str):
                    response_text += chunk

        if verdict_result:
            return json.dumps(verdict_result)
        else:
            logger.warning(f"[LLMWorker] Structured ({task_type}): tool not called, fallback to plain text.")
            return json.dumps({
                "verdict": "HOLD",
                "confidence": 0,
                "reason": f"Tool not called. AI says: {response_text.strip()}",
                "stop_loss_pct": 1.2,
                "take_profit_pct": 2.0
            })

    except Exception as e:
        logger.error(f"[LLMWorker] Structured inference error ({task_type}): {e}")
        return json.dumps({
            "verdict": "HOLD", "confidence": 0,
            "reason": f"inference error: {e}",
            "stop_loss_pct": 1.2, "take_profit_pct": 2.0,
        })


class LLMWorker:
    """
    Singleton manager untuk berinteraksi dengan background LLM process.

    Fitur:
    - query_async()       → text inference (commentary, post-mortem)
    - query_structured()  → structured JSON via Function Calling (veto decisions)
    - update_consciousness() → inject market context ke semua inferensi berikutnya
    """
    _instance = None

    @classmethod
    def get_instance(cls):
        if cls._instance is None:
            cls._instance = cls()
        return cls._instance

    def __init__(self):
        from config import MODEL_PATH, BASE_PATH

        self.req_queue = mp.Queue()
        self.res_queue = mp.Queue()

        # [Fix BUG #3] Path dari config, absolutized dengan BASE_PATH
        self.model_path = os.path.join(BASE_PATH, MODEL_PATH)
        # Path cache: gunakan subfolder 'cache' di dalam folder model jika tidak ditentukan
        # Ini penting agar kita bisa mount folder cache saja sebagai writeable di filesystem read-only.
        base_dir = os.path.dirname(self.model_path)
        self.cache_dir = os.path.join(base_dir, "cache")
        
        # Pastikan folder cache ada
        if not os.path.exists(self.cache_dir):
            try:
                os.makedirs(self.cache_dir, exist_ok=True)
            except Exception:
                pass # Akan gagal jika filesystem read-only, tapi Docker tmpfs harusnya sudah menyiapkannya

        if os.path.exists(self.model_path):
            self.worker_process = mp.Process(
                target=_llm_process_loop,
                args=(self.req_queue, self.res_queue, self.model_path, self.cache_dir),
                daemon=True,
                name="LLMWorker",
            )
            self.worker_process.start()
            self.is_active = True
            logger.info(f"[LLMWorker] Started. Model: {self.model_path}, Cache: {self.cache_dir}")
        else:
            self.is_active = False
            logger.warning(f"[LLMWorker] Model tidak ditemukan: {self.model_path}")

        self.task_counter = 0
        self.results      = {}

    # ── Public API ────────────────────────────────────────────────────────────

    def query_async(self, task_type: str, prompt: str) -> int | None:
        """Kirim prompt teks biasa (commentary, regime, post-mortem)."""
        if not self.is_active:
            return None
        self.task_counter += 1
        self.req_queue.put((self.task_counter, task_type, prompt, "text"))
        return self.task_counter

    def query_structured(self, task_type: str, prompt: str) -> int | None:
        """
        Kirim prompt untuk keputusan trading (ANALYSIS/PUMP_ALERT).
        Hasil: JSON string → parse dengan parse_structured_result().
        """
        if not self.is_active:
            return None
        self.task_counter += 1
        self.req_queue.put((self.task_counter, task_type, prompt, "structured"))
        return self.task_counter

    def update_consciousness(self, context_str: str):
        """
        Update market context yang di-inject ke setiap conversation berikutnya.
        Dipanggil oleh Oracle setiap ~30 menit atau saat regime shift.
        context_str: string berisi regime, top movers, recent PnL, lessons learned.
        """
        if not self.is_active:
            return
        # Special signal — task_id=None, task_type=__CONSCIOUSNESS__
        self.req_queue.put((None, _CONSCIOUSNESS_SIGNAL, context_str, "context"))

    def check_results(self):
        """Poll response queue dan simpan ke self.results. Monitor kesehatan proses."""
        if not self.is_active:
            return

        # ── Health Check: Restart jika mati (ZOMBIE RECOVERY) ──
        if self.worker_process and not self.worker_process.is_alive():
            logger.warning("🧠 [LLMWorker] Process is dead! Attempting recovery...")
            # Reap zombie
            self.worker_process.join(timeout=1)
            
            # Jika mati berkali-kali, mungkin cache korup. Coba hapus cache.
            if hasattr(self, '_crash_count'):
                self._crash_count += 1
            else:
                self._crash_count = 1
                
            if self._crash_count >= 2:
                logger.error(f"🧠 [LLMWorker] Repeated crashes ({self._crash_count}). Clearing XNNPACK cache...")
                try:
                    for f in os.listdir(self.cache_dir):
                        if ".xnnpack_cache" in f:
                            os.remove(os.path.join(self.cache_dir, f))
                            logger.info(f"🗑️ Deleted cache file: {f}")
                except Exception as e:
                    logger.error(f"Failed to clear cache: {e}")
                self._crash_count = 0 # Reset after clearing
            
            # Restart process
            self.worker_process = mp.Process(
                target=_llm_process_loop,
                args=(self.req_queue, self.res_queue, self.model_path, self.cache_dir),
                daemon=True,
                name="LLMWorker",
            )
            self.worker_process.start()
            logger.info("🧠 [LLMWorker] Process restarted.")

        while not self.res_queue.empty():
            try:
                task_id, task_type, result = self.res_queue.get_nowait()
                self.results[task_id] = {
                    "type":   task_type,
                    "result": result,
                    "time":   datetime.now(),
                }
            except Exception:
                break

    def get_result(self, task_id: int) -> dict | None:
        self.check_results()
        if task_id in self.results:
            return self.results.pop(task_id)
        return None

    def get_latest_result_by_type(self, task_type: str) -> str | None:
        self.check_results()
        matches = {k: v for k, v in self.results.items() if v["type"] == task_type}
        if matches:
            latest_id = max(matches.keys())
            return self.results.pop(latest_id)["result"]
        return None

    @staticmethod
    def parse_structured_result(result_str: str) -> dict:
        """
        Parse JSON string dari query_structured().
        Handle fallback jika model output teks (e.g. "**VERDICT: BUY**").
        """
        defaults = {
            "verdict":         "HOLD",
            "confidence":      0,
            "reason":          "LLM output parsed from text",
            "stop_loss_pct":   1.2,
            "take_profit_pct": 2.0,
            "eta_minutes":     15,
        }
        
        if not result_str:
            return defaults
            
        try:
            # 1. Try standard JSON parse
            parsed = json.loads(result_str)
            defaults.update(parsed)
            defaults["reason"] = "LLM structured decision"
            return defaults
        except (json.JSONDecodeError, TypeError) as e:
            # 2. Heuristic fallback for plain text output
            raw_upper = result_str.upper()
            if "VERDICT: BUY" in raw_upper or "BUY" in raw_upper[:30]:
                defaults["verdict"] = "BUY"
                defaults["confidence"] = 60 # Default moderate confidence for text BUY
                defaults["reason"] = f"Text-Fallback: {result_str[:60]}"
            elif "VERDICT: AVOID" in raw_upper or "AVOID" in raw_upper[:30]:
                defaults["verdict"] = "AVOID"
                defaults["confidence"] = 70
                defaults["reason"] = f"Text-Fallback: {result_str[:60]}"
            elif "VERDICT: HOLD" in raw_upper or "HOLD" in raw_upper[:30]:
                defaults["verdict"] = "HOLD"
                defaults["confidence"] = 40
                defaults["reason"] = f"Text-Fallback: {result_str[:60]}"
            else:
                logger.warning(f"[LLMWorker] JSON Parse Error: {e} | Raw: {result_str[:200]}...")
                defaults["reason"] = f"Parse Error: {str(e)}"
            
            return defaults
