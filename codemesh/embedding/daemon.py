# mypy: ignore-errors
"""Embedding daemon: persistent HTTP server on Unix socket for model inference.

Keeps EmbeddingModel + CrossEncoderReranker loaded in memory.
CLI queries connect via socket → no model load → <100ms per operation.

Usage:
    python -m codemesh.embedding.daemon          # start daemon
    codemesh serve-embed                          # CLI wrapper
"""

from __future__ import annotations

import json
import logging
import os
import signal
import socket
import struct
import sys
import threading
import time
from http.server import HTTPServer, BaseHTTPRequestHandler
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

SOCKET_PATH = Path.home() / ".cache" / "codemesh" / "embed.sock"
PID_PATH = Path.home() / ".cache" / "codemesh" / "embed.pid"
IDLE_TIMEOUT = 300  # 5 minutes


class DaemonHandler(BaseHTTPRequestHandler):
    """HTTP handler for embedding/reranking requests."""

    def log_message(self, format: str, *args: Any) -> None:
        logger.debug("Daemon: %s", format % args)

    def do_POST(self) -> None:
        content_length = int(self.headers.get("Content-Length", 0))
        body = self.rfile.read(content_length)

        try:
            data = json.loads(body) if body else {}
        except json.JSONDecodeError:
            self._send_error(400, "Invalid JSON")
            return

        try:
            if self.path == "/embed":
                result = self.server.daemon._handle_embed(data)
            elif self.path == "/rerank":
                result = self.server.daemon._handle_rerank(data)
            elif self.path == "/ping":
                result = {"status": "ok"}
            elif self.path == "/shutdown":
                result = {"status": "shutting_down"}
                self._send_json(200, result)
                threading.Thread(target=self.server.shutdown, daemon=True).start()
                return
            else:
                self._send_error(404, f"Unknown endpoint: {self.path}")
                return

            self._send_json(200, result)
        except Exception as e:
            logger.exception("Daemon handler error")
            self._send_error(500, str(e))

    def _send_json(self, code: int, data: dict) -> None:
        body = json.dumps(data).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _send_error(self, code: int, message: str) -> None:
        self._send_json(code, {"error": message})


class UnixHTTPServer:
    """HTTP server that listens on a Unix socket."""

    def __init__(self, socket_path: Path, daemon: EmbeddingDaemon) -> None:
        self.daemon = daemon
        self.socket_path = str(socket_path)
        self.RequestHandlerClass = DaemonHandler
        self.socket = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        self.socket.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self.socket.bind(self.socket_path)
        self.socket.listen(128)
        self.server_address = self.socket.getsockname()
        self._is_shut_down = threading.Event()
        self._is_shut_down.set()

    def serve_forever(self, poll_interval: float = 0.5) -> None:
        self._is_shut_down.clear()
        try:
            while not self._is_shut_down.is_set():
                self._is_shut_down.wait(poll_interval)
                if self._is_shut_down.is_set():
                    break
                try:
                    request, client_address = self.get_request()
                    self.process_request(request, client_address)
                except Exception:
                    pass
        finally:
            self._is_shut_down.set()

    def shutdown(self) -> None:
        self._is_shut_down.set()

    def get_request(self) -> tuple[socket.socket, list]:
        return self.socket.accept()

    def process_request(self, request: socket.socket, client_address: list) -> None:
        try:
            self.finish_request(request, client_address)
        except Exception:
            self.handle_error(request, client_address)
        finally:
            self.shutdown_request(request)

    def finish_request(self, request: socket.socket, client_address: list) -> None:
        self.RequestHandlerClass(request, client_address, self)

    def handle_error(self, request: socket.socket, client_address: list) -> None:
        pass

    def shutdown_request(self, request: socket.socket) -> None:
        try:
            request.shutdown(socket.SHUT_RDWR)
        except OSError:
            pass
        request.close()

    def server_close(self) -> None:
        self.socket.close()
        if os.path.exists(self.socket_path):
            os.unlink(self.socket_path)


class EmbeddingDaemon:
    """Persistent daemon that keeps embedding + reranker models warm."""

    def __init__(self) -> None:
        self._embed_model: object | None = None
        self._reranker_model: object | None = None
        self._reranker_tokenizer: object | None = None
        self._last_activity = time.time()

    def _load_embed(self) -> None:
        if self._embed_model is not None:
            return
        from codemesh.embedding.model import EmbeddingModel
        self._embed_model = EmbeddingModel()

    def _load_reranker(self) -> None:
        if self._reranker_model is not None:
            return
        from codemesh.embedding.model import CrossEncoderReranker
        rr = CrossEncoderReranker()
        rr._load_model()
        self._reranker_model = rr._model
        self._reranker_tokenizer = rr._tokenizer

    def _handle_embed(self, data: dict) -> dict:
        self._last_activity = time.time()
        texts = data.get("texts", [])
        if not texts:
            return {"embeddings": []}

        self._load_embed()
        embeddings = self._embed_model.encode(texts)
        return {"embeddings": embeddings}

    def _handle_rerank(self, data: dict) -> dict:
        self._last_activity = time.time()
        query = data.get("query", "")
        documents = data.get("documents", [])
        threshold = data.get("threshold", 0.3)
        top_k = data.get("top_k")

        if not documents:
            return {"results": []}

        self._load_reranker()
        import torch

        pairs = [(query, doc.get("text", str(doc))) for doc in documents]
        inputs = self._reranker_tokenizer(
            [p[0] for p in pairs],
            [p[1] for p in pairs],
            padding=True,
            truncation=True,
            max_length=128,
            return_tensors="pt",
        )
        input_ids = inputs["input_ids"]
        attention_mask = inputs["attention_mask"]

        with torch.no_grad():
            try:
                logits = self._reranker_model(
                    input_ids=input_ids, attention_mask=attention_mask
                ).logits.squeeze(-1)
            except TypeError:
                logits = self._reranker_model(**inputs).logits.squeeze(-1)
            scores = torch.sigmoid(logits).cpu().tolist()

        results = [
            [doc.get("id", str(i)), float(score)]
            for i, (doc, score) in enumerate(zip(documents, scores))
            if score >= threshold
        ]
        results.sort(key=lambda x: x[1], reverse=True)
        if top_k:
            results = results[:top_k]

        return {"results": results}

    def run(self) -> None:
        """Start the daemon, eagerly load models, and serve forever."""
        SOCKET_PATH.parent.mkdir(parents=True, exist_ok=True)

        # Clean up stale socket
        if SOCKET_PATH.exists():
            SOCKET_PATH.unlink()

        server = UnixHTTPServer(SOCKET_PATH, self)
        PID_PATH.parent.mkdir(parents=True, exist_ok=True)
        PID_PATH.write_text(str(os.getpid()))

        # Set socket permissions
        os.chmod(SOCKET_PATH, 0o600)

        logger.info("Daemon loading models (this takes ~10s)...")
        load_t0 = time.time()
        self._load_embed()
        logger.info("  Embedding model loaded in %.1fs", time.time() - load_t0)
        load_t1 = time.time()
        self._load_reranker()
        logger.info("  Reranker model loaded in %.1fs", time.time() - load_t1)
        logger.info("Daemon ready on %s (pid=%d)", SOCKET_PATH, os.getpid())

        # Idle timeout checker
        def check_idle() -> None:
            while True:
                time.sleep(30)
                idle = time.time() - self._last_activity
                if idle > IDLE_TIMEOUT:
                    logger.info("Idle timeout (%.0fs), shutting down", idle)
                    server.shutdown()
                    return

        idle_thread = threading.Thread(target=check_idle, daemon=True)
        idle_thread.start()

        # Signal handlers
        def handle_signal(signum: int, frame: Any) -> None:
            logger.info("Received signal %d, shutting down", signum)
            server.shutdown()

        signal.signal(signal.SIGTERM, handle_signal)
        signal.signal(signal.SIGINT, handle_signal)

        try:
            server.serve_forever()
        finally:
            server.server_close()
            PID_PATH.unlink(missing_ok=True)
            logger.info("Daemon stopped")


def is_daemon_running() -> bool:
    """Check if the daemon is currently running."""
    if not PID_PATH.exists():
        return False
    try:
        pid = int(PID_PATH.read_text().strip())
        os.kill(pid, 0)  # Check if process exists
        return True
    except (ValueError, ProcessLookupError, PermissionError):
        return False


def start_daemon() -> None:
    """Start the daemon (foreground)."""
    daemon = EmbeddingDaemon()
    daemon.run()


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(levelname)s %(message)s")
    start_daemon()
