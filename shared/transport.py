"""
shared/transport.py — Ultra-Low Latency ZMQ Transport Layer
============================================================
Provides ZMQPublisher and ZMQSubscriber backed by Unix Domain Sockets (IPC)
and uvloop for the fastest possible async event loop.

All messages are serialised with Protobuf (binary) for minimum payload size.

IPC socket addresses — each service owns exactly one address:
  IPC_MARKET  ipc:///tmp/market_stream.ipc   (market-pipe  → subscribers)
  IPC_SIGNAL  ipc:///tmp/signal_stream.ipc   (brain-inference → subscribers)
  IPC_EQUITY  ipc:///tmp/equity_stream.ipc   (executive-core → subscribers)

Usage (publisher):
    pub = ZMQPublisher(IPC_MARKET)
    pub.start()
    pub.send(tick_proto_bytes)
    pub.stop()

Usage (subscriber, sync):
    sub = ZMQSubscriber(IPC_MARKET)
    sub.start()
    for raw in sub.iter_messages():
        tick = algovibe_pb2.Tick()
        tick.ParseFromString(raw)
        ...
    sub.stop()

Usage (subscriber, async with uvloop):
    import asyncio, uvloop
    asyncio.set_event_loop_policy(uvloop.EventLoopPolicy())

    sub = ZMQSubscriber(IPC_MARKET)
    sub.start()
    async def consume():
        async for raw in sub.aiter_messages():
            ...
    asyncio.run(consume())
"""

import asyncio
import logging
import threading
import time
from typing import AsyncIterator, Iterator, Optional

import zmq
import zmq.asyncio

try:
    import uvloop
    _UVLOOP_AVAILABLE = True
except ImportError:
    _UVLOOP_AVAILABLE = False

logger = logging.getLogger(__name__)

#  IPC address constants 
IPC_MARKET = "ipc:///tmp/market_stream.ipc"
IPC_SIGNAL = "ipc:///tmp/signal_stream.ipc"
IPC_LLM_SIGNAL = "ipc:///tmp/llm_signal_stream.ipc"
IPC_EQUITY = "ipc:///tmp/equity_stream.ipc"
IPC_COMMAND = "ipc:///tmp/command_stream.ipc"

# Default receive timeout so subscriber threads can exit cleanly (ms)
_RECV_TIMEOUT_MS = 1000


def install_uvloop() -> None:
    """Install uvloop as the default asyncio event-loop policy if available."""
    if _UVLOOP_AVAILABLE:
        asyncio.set_event_loop_policy(uvloop.EventLoopPolicy())
        logger.debug("[transport] uvloop event-loop policy installed.")
    else:
        logger.warning("[transport] uvloop not available; falling back to default asyncio loop.")


#  Publisher 

class ZMQPublisher:
    """
    Thread-safe ZMQ PUB socket bound to an IPC address.

    Serialises messages as raw bytes (Protobuf), no topic framing —
    subscribers receive the complete serialised protobuf message.
    """

    def __init__(self, ipc_address: str, high_water_mark: int = 1000):
        self._address = ipc_address
        self._hwm = high_water_mark
        self._context: Optional[zmq.Context] = None
        self._socket: Optional[zmq.Socket] = None
        self._lock = threading.Lock()
        self._started = False

    #  Lifecycle 

    def start(self) -> None:
        """Bind the PUB socket.  Must be called before send()."""
        if self._started:
            return
        self._context = zmq.Context.instance()
        self._socket = self._context.socket(zmq.PUB)
        self._socket.set_hwm(self._hwm)
        self._socket.bind(self._address)
        self._started = True
        logger.info(f"[ZMQPublisher] Bound to {self._address}")

    def stop(self) -> None:
        """Unbind and close the socket."""
        if not self._started:
            return
        with self._lock:
            self._socket.close(linger=0)
            self._started = False
        logger.info(f"[ZMQPublisher] Stopped ({self._address})")

    #  Send 

    def send(self, proto_bytes: bytes, topic: str = "", latency_ms: float = 0) -> None:
        """
        Publish a single Protobuf-serialised message, optionally with a topic
        and exchange latency metadata.

        Parameters
        ----------
        proto_bytes:
            Result of ``message.SerializeToString()`` for any Protobuf message.
        topic:
            Optional topic string (e.g., "tick", "trade"). If provided, the
            message is sent as a multipart message [topic, data].
        latency_ms:
            Optional exchange latency in milliseconds. If > 0, it is sent as
            an additional frame "latency:<ms>".
        """
        if not self._started:
            raise RuntimeError("ZMQPublisher.send() has not been called.")
        with self._lock:
            try:
                frames = []
                if topic:
                    frames.append(topic.encode())
                
                frames.append(proto_bytes)

                if latency_ms > 0:
                    frames.append(f"latency:{latency_ms:.2f}".encode())

                if len(frames) > 1:
                    self._socket.send_multipart(frames, flags=zmq.NOBLOCK)
                else:
                    self._socket.send(proto_bytes, flags=zmq.NOBLOCK)
            except zmq.Again:
                # Subscriber(s) not yet connected or HWM reached — drop silently
                pass

    #  Context manager 

    def __enter__(self):
        self.start()
        return self

    def __exit__(self, *_):
        self.stop()


#  Subscriber 

class ZMQSubscriber:
    """
    ZMQ SUB socket that connects to a publisher's IPC address.

    Provides both a synchronous iterator (iter_messages) and an async
    generator (aiter_messages) for use with uvloop.
    """

    def __init__(self, ipc_address: str):
        self._address = ipc_address
        self._context: Optional[zmq.Context] = None
        self._socket: Optional[zmq.Socket] = None
        self._running = False

    #  Lifecycle 

    def start(self) -> None:
        """Connect the SUB socket."""
        self._context = zmq.Context.instance()
        self._socket = self._context.socket(zmq.SUB)
        # Subscribe to ALL messages (empty filter = no topic framing)
        self._socket.setsockopt(zmq.SUBSCRIBE, b"")
        self._socket.RCVTIMEO = _RECV_TIMEOUT_MS
        self._socket.connect(self._address)
        self._running = True
        logger.info(f"[ZMQSubscriber] Connected to {self._address}")

    def stop(self) -> None:
        """Disconnect and close the socket."""
        self._running = False
        if self._socket:
            self._socket.close(linger=0)
        logger.info(f"[ZMQSubscriber] Stopped ({self._address})")

    #  Synchronous iterator 

    def iter_messages(self) -> Iterator[bytes]:
        """
        Yield raw Protobuf bytes.  Blocks until a message arrives or the
        subscriber is stopped.

        Example
        -------
        for raw in sub.iter_messages():
            msg = Tick(); msg.ParseFromString(raw)
        """
        while self._running:
            try:
                yield self._socket.recv_multipart()[-1]
            except zmq.Again:
                # Timeout — check _running flag and retry
                continue
            except zmq.ZMQError as exc:
                if self._running:
                    logger.error(f"[ZMQSubscriber] ZMQError: {exc}")
                break

    #  Asynchronous iterator (uvloop-friendly) 

    async def aiter_messages(self) -> AsyncIterator[bytes]:
        """
        Async-generator version of iter_messages.  Use with uvloop for
        sub-millisecond event-loop overhead.

        Example
        -------
        async for raw in sub.aiter_messages():
            msg = Tick(); msg.ParseFromString(raw)
        """
        async_ctx = zmq.asyncio.Context.instance()
        async_sock = async_ctx.socket(zmq.SUB)
        async_sock.setsockopt(zmq.SUBSCRIBE, b"")
        async_sock.connect(self._address)
        try:
            while self._running:
                try:
                    raw = await asyncio.wait_for(async_sock.recv_multipart(), timeout=1.0)
                    yield raw[-1]
                except asyncio.TimeoutError:
                    continue
        finally:
            async_sock.close(linger=0)

    #  Context manager 

    def __enter__(self):
        self.start()
        return self

    def __exit__(self, *_):
        self.stop()
