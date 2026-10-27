"""Run an OpenAI-compatible Agent on the local OfficeQA Pro corpus.

The evaluator deliberately exposes only the question and corpus tools to the
model.  The answer key remains in the evaluator process and is used only by
``officeqa/reward.py`` after the model returns a prediction.

Before running, copy ``.env.example`` to ``.env`` and fill in at least
``MODEL_API_KEY`` and ``MODEL_NAME``.  ``MODEL_BASE_URL`` can point to a local
vLLM/LM Studio server or any OpenAI-compatible provider.
"""

from __future__ import annotations

import argparse
import ast
import csv
import json
import os
import re
import signal
import sqlite3
import subprocess
import sys
import tempfile
import time
from collections import defaultdict
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path
from typing import Any

from dotenv import load_dotenv
from openai import OpenAI

# The official reward implementation lives in the cloned repository.
from officeqa.reward import score_answer


ROOT = Path(__file__).resolve().parent
# Bump this whenever the deterministic JSON-to-search representation changes;
# it prevents an index made by an older evaluator from silently mixing page
# marker conventions with the current official-corpus adapter.
CORPUS_RENDER_VERSION = "official-parsed-json-v2-element-index"


class TaskTimeoutError(BaseException):
    """Abort one question after its wall-clock budget is exhausted.

    This is deliberately a ``BaseException`` rather than an ``Exception``.
    In particular, it must not derive from the built-in ``TimeoutError``:
    socket/HTTP transports map that type to their own read timeout and the
    OpenAI client may then retry it.  A one-shot SIGALRM would already have
    been consumed, leaving the retried question without a total deadline.

    Treat the deadline like ``KeyboardInterrupt``/``SystemExit`` control flow
    so broad library and tool-level ``except Exception`` handlers cannot turn
    it into an ordinary recoverable error.  Evaluator entry points catch this
    type explicitly and record a retryable ``task_timeout`` result.
    """


def _raise_task_timeout(signum: int, frame: Any) -> None:
    raise TaskTimeoutError("question exceeded the task timeout")


@contextmanager
def task_deadline(seconds: float | None):
    """Enforce a wall-clock deadline around one question on Unix workers.

    Each evaluator worker is a single-threaded process, so SIGALRM interrupts
    a blocked model HTTP request as well as local tool execution. The prior
    signal handler/timer is restored after the question, allowing the worker
    to continue with the next question after recording the timeout.
    """
    if seconds is None or seconds <= 0 or not hasattr(signal, "SIGALRM"):
        yield
        return

    previous_handler = signal.getsignal(signal.SIGALRM)
    previous_timer = signal.setitimer(signal.ITIMER_REAL, 0)
    signal.signal(signal.SIGALRM, _raise_task_timeout)
    signal.setitimer(signal.ITIMER_REAL, float(seconds))
    try:
        yield
    finally:
        signal.setitimer(signal.ITIMER_REAL, 0)
        signal.signal(signal.SIGALRM, previous_handler)
        if previous_timer[0] > 0 or previous_timer[1] > 0:
            signal.setitimer(signal.ITIMER_REAL, previous_timer[0], previous_timer[1])


SYSTEM_PROMPT = """You are an agent answering the provided questions.
You have access to a corpus of historical U.S. government financial documents.
Use search_corpus to locate relevant evidence, then use read_document with
the returned filename, page, or element_id to inspect the exact evidence.
Perform calculations carefully and preserve units and
rounding requested by the question. Do not guess when the corpus is unclear.
Use python_exec for arithmetic, statistics, regressions, Fourier analysis, or
other calculations. Use web_search only when the local corpus is insufficient.
Plan to complete the task efficiently. The evaluator allows up to 200 steps
for this question; track the remaining budget from the system reminders and
stop searching when the evidence is sufficient. Most importantly, the final
turn must call submit_answer: do not end with an explanation, a draft, or a
plain assistant message. Always reserve a final turn for submission before the
200-step limit. For context management, only the most recent 30 conversation
complete assistant interaction steps are retained (the system prompt itself is
always retained); do not assume that older steps remain visible.
Use the tools silently: when you have the answer, call submit_answer. The
submit_answer.answer value must contain only the concise answer. Acceptable
formats are exactly one of: a single number, or a bracketed list. If you use a
list, each element must be either a single word or a number. Numbers must not
use comma thousands separators. Preserve the question's requested units,
rounding, labels, and ordering, but do not include reasoning, citations,
markdown, or XML tags. A normal assistant message containing a long
explanation will not be accepted.

A final ``<FINAL_ANSWER>`` text without a submit_answer tool call is not a
valid completion and will be retried. Do not use that fallback in normal
operation; call submit_answer instead.
"""


TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "search_corpus",
            "description": (
                "Search the local document corpus. Use distinctive "
                "terms, years, agencies, table labels, or document names."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {"type": "string"},
                    "max_results": {
                        "type": "integer",
                        "minimum": 1,
                        "maximum": 20,
                    },
                },
                "required": ["query"],
                "additionalProperties": False,
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "python_exec",
            "description": (
                "Run a short, read-only Python calculation. The environment "
                "includes numpy, pandas, and scipy. Use it for arithmetic, "
                "statistics, regressions, and Fourier analysis."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "code": {"type": "string", "maxLength": 12000},
                    "timeout": {"type": "integer", "minimum": 1, "maximum": 120},
                },
                "required": ["code"],
                "additionalProperties": False,
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "web_search",
            "description": (
                "Search the public web through DuckDuckGo when the local "
                "local document corpus is insufficient. "
                "Results are returned as concise title, URL, and text entries."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {"type": "string"},
                    "max_results": {
                        "type": "integer",
                        "minimum": 1,
                        "maximum": 10,
                    },
                },
                "required": ["query"],
                "additionalProperties": False,
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "submit_answer",
            "description": (
                "Submit the final answer for grading. Call this exactly once. "
                "The answer must contain only the concise answer value. "
                "Acceptable formats are exactly one of: a single number, or a "
                "bracketed list. If you use a list, each element must be either "
                "a single word or a number. Numbers must not use comma "
                "thousands separators. Follow the question's requested units, "
                "rounding, labels, and ordering. Do not include reasoning, "
                "citations, markdown, or <FINAL_ANSWER> tags."
            ),
            "parameters": {
                "type": "object",
                "properties": {"answer": {"type": "string"}},
                "required": ["answer"],
                "additionalProperties": False,
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "read_document",
            "description": (
                "Read a local parsed JSON document. filename may be the .txt "
                "name shown by search_corpus or its .json equivalent. page is "
                "1-based and should be supplied when known; element_id can be "
                "used to read the exact parsed element returned by search_corpus."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "filename": {"type": "string"},
                    "page": {"type": "integer", "minimum": 1},
                    "element_id": {
                        "type": "integer",
                        "minimum": 0,
                    },
                    "max_chars": {
                        "type": "integer",
                        "minimum": 500,
                        "maximum": 30000,
                    },
                },
                "required": ["filename"],
                "additionalProperties": False,
            },
        },
    },
]


# The Responses API uses a flatter function-tool schema than Chat Completions.
RESPONSE_TOOLS = [
    {
        "type": "function",
        "name": tool["function"]["name"],
        "description": tool["function"]["description"],
        "parameters": tool["function"]["parameters"],
    }
    for tool in TOOLS
]


def source_file_names(source_files: str) -> frozenset[str]:
    """Normalize a CSV ``source_files`` value to allowed parsed JSON names."""
    allowed: set[str] = set()
    # Pro uses CRLF-separated source files while Pro V2 uses semicolons.
    # Accept both formats (and bare LF) so source-level isolation works for
    # either benchmark.
    for raw_name in re.split(r"[;\r\n]+", str(source_files or "")):
        name = Path(raw_name.strip()).name
        if not name:
            continue
        if name.lower().endswith(".txt"):
            name = name[:-4] + ".json"
        allowed.add(name)
    return frozenset(allowed)


def jsonable(value: Any) -> Any:
    """Convert an SDK/Pydantic response object into JSON-safe data."""
    if hasattr(value, "model_dump_json"):
        try:
            return json.loads(value.model_dump_json())
        except Exception:
            pass
    if hasattr(value, "model_dump"):
        try:
            return value.model_dump(mode="json")
        except TypeError:
            return value.model_dump()
    if hasattr(value, "dict"):
        return value.dict()
    return value


def resolve_path(value: str, default: Path) -> Path:
    path = Path(value) if value else default
    if not path.is_absolute():
        path = ROOT / path
    return path.expanduser().resolve()


def env_int(name: str, default: int) -> int:
    value = os.getenv(name)
    if value in (None, ""):
        return default
    try:
        return int(value)
    except ValueError as exc:
        raise SystemExit(f"环境变量 {name} 必须是整数，当前值为 {value!r}") from exc


def env_float(name: str, default: float | None) -> float | None:
    value = os.getenv(name)
    if value in (None, ""):
        return default
    try:
        return float(value)
    except ValueError as exc:
        raise SystemExit(f"环境变量 {name} 必须是数字，当前值为 {value!r}") from exc


def parse_args() -> argparse.Namespace:
    # Parse the environment-file selector first, then load it before resolving
    # any path defaults below. This lets two independent processes use .env
    # and .env2 without sharing model or result configuration.
    bootstrap = argparse.ArgumentParser(add_help=False)
    bootstrap.add_argument("--env-file", default=str(ROOT / ".env"))
    bootstrap_args, _ = bootstrap.parse_known_args()
    env_file = Path(bootstrap_args.env_file).expanduser()
    if not env_file.is_absolute():
        env_file = ROOT / env_file
    env_file = env_file.resolve()
    if env_file.exists():
        load_dotenv(env_file, override=True)
    elif bootstrap_args.env_file != str(ROOT / ".env"):
        raise SystemExit(f"找不到环境配置文件：{env_file}")

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--env-file",
        type=Path,
        default=env_file,
        help=f"环境变量文件（默认：{env_file}）",
    )
    parser.add_argument(
        "--dataset",
        type=Path,
        default=resolve_path(
            os.getenv("OFFICEQA_DATASET", ""), ROOT / "data/officeqa_pro.csv"
        ),
        help="题目 CSV 路径",
    )
    parser.add_argument(
        "--corpus",
        type=Path,
        default=resolve_path(
            os.getenv("OFFICEQA_CORPUS", ""),
            ROOT / "corpus/jsons",
        ),
        help="解析 JSON 语料目录",
    )
    parser.add_argument(
        "--results",
        type=Path,
        default=resolve_path(os.getenv("OFFICEQA_RESULTS", ""), ROOT / "results"),
        help="结果根目录；每题写入 traj/<model>-prob<uid>-<timestamp>.jsonl",
    )
    parser.add_argument(
        "--model",
        default=os.getenv("MODEL_NAME", ""),
        help="覆盖环境文件中的模型名",
    )
    parser.add_argument(
        "--reasoning-effort",
        default=os.getenv("MODEL_REASONING_EFFORT", ""),
        help="可选的 OpenAI 风格思考等级，例如 high 或 xhigh",
    )
    parser.add_argument(
        "--extra-body",
        default=os.getenv("MODEL_EXTRA_BODY", ""),
        help="以 JSON 传递供应商自定义请求字段",
    )
    parser.add_argument(
        "--api-mode",
        choices=("chat", "responses"),
        default=os.getenv("MODEL_API_MODE", "chat"),
        help="模型接口模式；GPT 推理模型可用 responses",
    )
    parser.add_argument("--limit", type=int, default=None, help="只评测前 N 道题")
    parser.add_argument("--offset", type=int, default=0, help="从第几道题开始")
    parser.add_argument(
        "--workers",
        type=int,
        default=1,
        help="将选中的题目按 worker 数量分片；配合 --worker-index 实现并发",
    )
    parser.add_argument(
        "--worker-index",
        type=int,
        default=0,
        help="当前分片编号，从 0 到 workers-1",
    )
    parser.add_argument(
        "--no-resume",
        action="store_true",
        help="忽略已有结果，不跳过已经完成的 uid",
    )
    parser.add_argument(
        "--check-only",
        action="store_true",
        help="只检查路径和配置，不调用模型",
    )
    parser.add_argument(
        "--tolerance",
        type=float,
        default=env_float("OFFICEQA_TOLERANCE", 0.05),
        help=(
            "传给官方 officeqa.reward.score_answer 的相对误差容忍度；默认 0.05（5%%）"
        ),
    )
    parser.add_argument(
        "--task-timeout",
        type=float,
        default=env_float("OFFICEQA_TASK_TIMEOUT", 900.0),
        help=(
            "每道题的最大墙钟时间（秒）；超时会停止当前题并写入 "
            "termination_status=abnormal、termination_reason=task_timeout。"
            "默认 900 秒（15 分钟）"
        ),
    )
    return parser.parse_args()


def load_questions(path: Path) -> list[dict[str, str]]:
    if not path.is_file():
        raise SystemExit(f"找不到题目文件：{path}")
    with path.open(newline="", encoding="utf-8") as handle:
        rows = list(csv.DictReader(handle))
    required = {"uid", "question", "answer", "source_files"}
    missing = required - set(rows[0]) if rows else required
    if missing:
        raise SystemExit(f"题目 CSV 缺少字段：{', '.join(sorted(missing))}")
    return rows


def corpus_signature(files: list[Path]) -> str:
    total_size = sum(path.stat().st_size for path in files)
    newest_mtime = max((path.stat().st_mtime_ns for path in files), default=0)
    return f"{CORPUS_RENDER_VERSION}:{len(files)}:{total_size}:{newest_mtime}"


def parsed_json_chunks(path: Path) -> list[dict[str, Any]]:
    """Load official parsed JSON and expose one searchable row per element/page.

    Pro V2 is distributed as parsed JSON rather than a pre-transformed TXT
    corpus.  The JSON schema is the official corpus representation
    (``document.elements[*].content`` and zero-based ``bbox[*].page_id``).
    The returned rows keep that content verbatim; the page and element fields
    are indexing metadata, not a rewrite of the benchmark corpus.
    """
    with path.open(encoding="utf-8") as handle:
        payload = json.load(handle)

    document = payload.get("document") if isinstance(payload, dict) else None
    if not isinstance(document, dict):
        raise ValueError(f"官方 OfficeQA JSON 缺少 document 对象：{path}")
    elements = document.get("elements")
    if not isinstance(elements, list):
        raise ValueError(f"官方 OfficeQA JSON 缺少 document.elements 列表：{path}")

    chunks: list[dict[str, Any]] = []
    for element_index, element in enumerate(elements):
        if not isinstance(element, dict):
            continue
        raw_content = element.get("content")
        content = str(raw_content) if raw_content else ""
        element_type = str(element.get("type") or "unknown")
        # Some figure elements have no OCR text but do carry a useful
        # description.  Index that description without pretending it is PDF
        # text; this keeps figure metadata discoverable in element search.
        if not content.strip() and element.get("description"):
            content = f"[{element_type} description] {element['description']}"
        if not content.strip():
            continue

        page_ids: set[int] = set()
        for box in element.get("bbox", []) or []:
            if not isinstance(box, dict) or "page_id" not in box:
                continue
            try:
                page_ids.add(int(box["page_id"]))
            except (TypeError, ValueError):
                continue
        if not page_ids:
            page_ids = {-1}

        raw_element_id = element.get("id", element_index)
        try:
            element_id = int(raw_element_id)
        except (TypeError, ValueError):
            element_id = element_index
        for page_id in sorted(page_ids):
            chunks.append(
                {
                    "filename": path.name,
                    "element_id": element_id,
                    "element_index": element_index,
                    "element_type": element_type,
                    "json_page_id": None if page_id < 0 else page_id,
                    "pdf_page": None if page_id < 0 else page_id + 1,
                    "content": content,
                }
            )
    return chunks


def chunks_to_text(chunks: list[dict[str, Any]]) -> str:
    """Render indexed element rows with explicit page markers for read_document."""
    pages: dict[int, list[str]] = defaultdict(list)
    no_page: list[str] = []
    for chunk in chunks:
        page_id = chunk["json_page_id"]
        if page_id is None:
            no_page.append(chunk["content"])
        else:
            pages[int(page_id)].append(chunk["content"])

    rendered_pages = []
    for page_id in sorted(pages):
        # The official parsed corpus uses zero-based JSON page_id values,
        # while source_docs/pdf_page_number is one-based.  Keep both visible
        # so that the model and read_document use the same unambiguous page
        # convention as the dataset card.
        rendered_pages.append(
            f"[PDF_PAGE {page_id + 1} | JSON_PAGE_ID {page_id}]\n"
            + "\n".join(pages[page_id])
        )
    if no_page:
        rendered_pages.append(
            "[PDF_PAGE UNKNOWN | JSON_PAGE_ID UNKNOWN]\n" + "\n".join(no_page)
        )
    return "\n\n".join(rendered_pages)


def json_to_text(path: Path) -> str:
    """Render official parsed JSON content for the document-level read view."""
    return chunks_to_text(parsed_json_chunks(path))


class CorpusStore:
    """Element-level FTS search with page/document reads over parsed JSON."""

    def __init__(
        self,
        corpus_dir: Path,
        index_path: Path,
        max_tool_chars: int,
        *,
        ensure_index: bool = True,
    ):
        if not corpus_dir.is_dir():
            raise SystemExit(f"找不到语料目录：{corpus_dir}")
        self.corpus_dir = corpus_dir
        self.index_path = index_path
        self.max_tool_chars = max_tool_chars
        # Worker processes open an already-built index in read mode.  Avoid
        # rescanning all corpus files in every worker; the controller validates
        # and (if needed) builds the index once before the pool starts.
        self.files = sorted(corpus_dir.rglob("*.json")) if ensure_index else []
        if ensure_index and not self.files:
            raise SystemExit(f"语料目录中没有 JSON 文件：{corpus_dir}")
        self.index_path.parent.mkdir(parents=True, exist_ok=True)
        self.connection = sqlite3.connect(str(index_path), timeout=60)
        self.connection.row_factory = sqlite3.Row
        if ensure_index:
            self._ensure_index()

    def close(self) -> None:
        self.connection.close()

    def _ensure_index(self) -> None:
        signature = corpus_signature(self.files)
        self.connection.execute(
            "CREATE TABLE IF NOT EXISTS metadata (key TEXT PRIMARY KEY, value TEXT NOT NULL)"
        )
        existing = self.connection.execute(
            "SELECT value FROM metadata WHERE key = 'corpus_signature'"
        ).fetchone()
        count = self.connection.execute(
            "SELECT name FROM sqlite_master WHERE type = 'table' AND name = 'documents'"
        ).fetchone()
        fts_count = self.connection.execute(
            "SELECT name FROM sqlite_master WHERE type = 'table' AND name = 'chunks_fts'"
        ).fetchone()
        chunk_count = self.connection.execute(
            "SELECT name FROM sqlite_master WHERE type = 'table' AND name = 'chunks'"
        ).fetchone()
        if (
            existing
            and existing[0] == signature
            and count
            and chunk_count
            and fts_count
        ):
            return

        print(
            f"正在建立本地检索索引（{len(self.files)} 份文档，首次运行需要一些时间）..."
        )
        self.connection.execute("DROP TABLE IF EXISTS chunks_fts")
        self.connection.execute("DROP TABLE IF EXISTS chunks")
        self.connection.execute("DROP TABLE IF EXISTS documents_fts")
        self.connection.execute("DROP TABLE IF EXISTS documents")
        self.connection.execute(
            "CREATE TABLE documents (id INTEGER PRIMARY KEY, filename TEXT UNIQUE NOT NULL, content TEXT NOT NULL)"
        )
        self.connection.execute(
            "CREATE TABLE chunks ("
            "id INTEGER PRIMARY KEY, filename TEXT NOT NULL, "
            "element_id INTEGER NOT NULL, element_index INTEGER NOT NULL, "
            "element_type TEXT NOT NULL, json_page_id INTEGER, pdf_page INTEGER, "
            "content TEXT NOT NULL)"
        )
        # External-content FTS keeps the searchable index separate from the
        # element text stored in ``chunks``. Filename and element type remain
        # searchable; page numbers are returned as structured metadata.
        self.connection.execute(
            "CREATE VIRTUAL TABLE chunks_fts USING fts5("
            "filename, element_id UNINDEXED, element_index UNINDEXED, "
            "element_type, json_page_id UNINDEXED, pdf_page UNINDEXED, content, "
            "content='chunks', content_rowid='id', "
            "tokenize='unicode61')"
        )
        self.connection.execute("PRAGMA synchronous = OFF")
        self.connection.execute("BEGIN")
        try:
            for index, path in enumerate(self.files, start=1):
                parsed_chunks = parsed_json_chunks(path)
                content = chunks_to_text(parsed_chunks)
                filename = path.name
                self.connection.execute(
                    "INSERT INTO documents(filename, content) VALUES (?, ?)",
                    (filename, content),
                )
                for chunk in parsed_chunks:
                    chunk_cursor = self.connection.execute(
                        "INSERT INTO chunks("
                        "filename, element_id, element_index, element_type, "
                        "json_page_id, pdf_page, content) VALUES (?, ?, ?, ?, ?, ?, ?)",
                        (
                            chunk["filename"],
                            chunk["element_id"],
                            chunk["element_index"],
                            chunk["element_type"],
                            chunk["json_page_id"],
                            chunk["pdf_page"],
                            chunk["content"],
                        ),
                    )
                    chunk_id = chunk_cursor.lastrowid
                    self.connection.execute(
                        "INSERT INTO chunks_fts("
                        "rowid, filename, element_id, element_index, element_type, "
                        "json_page_id, pdf_page, content) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                        (
                            chunk_id,
                            chunk["filename"],
                            chunk["element_id"],
                            chunk["element_index"],
                            chunk["element_type"],
                            chunk["json_page_id"],
                            chunk["pdf_page"],
                            chunk["content"],
                        ),
                    )
                if index % 100 == 0:
                    self.connection.commit()
                    self.connection.execute("BEGIN")
                    print(f"  已索引 {index}/{len(self.files)}")
            self.connection.commit()
        except Exception:
            self.connection.rollback()
            raise
        self.connection.execute(
            "INSERT OR REPLACE INTO metadata(key, value) VALUES ('corpus_signature', ?)",
            (signature,),
        )
        self.connection.commit()
        print(f"检索索引已保存：{self.index_path}")

    @staticmethod
    def _fts_query(query: str, operator: str = "OR") -> str:
        # Quoted alphanumeric tokens avoid FTS syntax errors from punctuation.
        tokens = re.findall(r"[A-Za-z0-9][A-Za-z0-9_'-]{1,}", query.lower())
        unique = list(dict.fromkeys(token.strip("'-") for token in tokens))
        unique = [token for token in unique if len(token) >= 2]
        if operator not in {"AND", "OR"}:
            raise ValueError(f"不支持的 FTS 运算符：{operator}")
        return f" {operator} ".join(
            f'"{token.replace(chr(34), "")}"' for token in unique[:32]
        )

    @staticmethod
    def _snippet(content: str, query: str, width: int = 900) -> str:
        terms = [term.lower() for term in re.findall(r"[A-Za-z0-9]{2,}", query)]
        lower = content.lower()
        positions = [lower.find(term) for term in terms if lower.find(term) >= 0]
        start = max(0, (min(positions) if positions else 0) - width // 3)
        end = min(len(content), start + width)
        prefix = "..." if start else ""
        suffix = "..." if end < len(content) else ""
        return prefix + content[start:end] + suffix

    def search(
        self,
        query: str,
        max_results: int = 8,
        allowed_files: frozenset[str] | set[str] | None = None,
    ) -> str:
        and_query = self._fts_query(query, "AND")
        if not and_query:
            return "The query contains no searchable keywords."
        max_results = max(1, min(int(max_results), 20))

        normalized_allowed = (
            None
            if allowed_files is None
            else source_file_names(";".join(str(name) for name in allowed_files))
        )
        if normalized_allowed is not None and not normalized_allowed:
            return "This question has no available source_files."

        def fetch(query_text: str):
            where = ["chunks_fts MATCH ?"]
            params: list[Any] = [query_text]
            if normalized_allowed is not None:
                placeholders = ",".join("?" for _ in normalized_allowed)
                where.append(f"chunks.filename IN ({placeholders})")
                params.extend(sorted(normalized_allowed))
            params.append(max_results)
            return self.connection.execute(
                "SELECT chunks.filename, chunks.element_id, chunks.element_index, "
                "chunks.element_type, chunks.json_page_id, chunks.pdf_page, chunks.content "
                "FROM chunks JOIN chunks_fts ON chunks.id = chunks_fts.rowid "
                f"WHERE {' AND '.join(where)} ORDER BY bm25(chunks_fts) LIMIT ?",
                params,
            ).fetchall()

        rows = fetch(and_query)
        match_mode = "AND"
        if not rows:
            or_query = self._fts_query(query, "OR")
            rows = fetch(or_query)
            match_mode = "OR fallback"
        if not rows:
            return (
                "No matching documents were found. Try a more specific year, "
                "agency name, table title, or keyword."
            )

        output = []
        for row in rows:
            page = row["pdf_page"]
            page_label = str(page) if page is not None else "UNKNOWN"
            json_page = row["json_page_id"]
            json_page_label = str(json_page) if json_page is not None else "UNKNOWN"
            output.append(
                f"MATCH_MODE: {match_mode}\n"
                f"FILE: {row['filename']}\n"
                f"PDF_PAGE: {page_label}\n"
                f"JSON_PAGE_ID: {json_page_label}\n"
                f"ELEMENT_ID: {row['element_id']}\n"
                f"ELEMENT_INDEX: {row['element_index']}\n"
                f"ELEMENT_TYPE: {row['element_type']}\n"
                f"{self._snippet(row['content'], query, width=1200)}"
            )
        return self._truncate("\n\n---\n\n".join(output))

    def read(
        self,
        filename: str,
        page: int | None = None,
        max_chars: int | None = None,
        element_id: int | None = None,
        allowed_files: frozenset[str] | set[str] | None = None,
    ) -> str:
        safe_name = Path(filename).name
        if safe_name.lower().endswith(".txt"):
            safe_name = safe_name[:-4] + ".json"
        if allowed_files is not None:
            normalized_allowed = source_file_names(
                ";".join(str(name) for name in allowed_files)
            )
            if safe_name not in normalized_allowed:
                return (
                    f"Access denied for document {safe_name}: it is not in this "
                    "question's allowed source_files."
                )

        if element_id is not None:
            try:
                element_id = int(element_id)
            except (TypeError, ValueError):
                return f"element_id is not a valid integer: {element_id}"
            element_sql = (
                "SELECT filename, element_id, element_index, element_type, "
                "json_page_id, pdf_page, content FROM chunks "
                "WHERE filename = ? AND element_id = ?"
            )
            params: list[Any] = [safe_name, element_id]
            if page is not None:
                element_sql += " AND pdf_page = ?"
                params.append(int(page))
            element_sql += " ORDER BY element_index, pdf_page"
            element_rows = self.connection.execute(element_sql, params).fetchall()
            if not element_rows:
                return (
                    f"Could not find element_id={element_id} in document {safe_name}"
                    + (f" on page {page}" if page is not None else "")
                    + ". Use search_corpus first to obtain a valid element ID."
                )
            output = []
            for element_row in element_rows:
                pdf_page = element_row["pdf_page"]
                json_page = element_row["json_page_id"]
                output.append(
                    f"FILE: {element_row['filename']}\n"
                    f"PDF_PAGE: {pdf_page if pdf_page is not None else 'UNKNOWN'}\n"
                    f"JSON_PAGE_ID: {json_page if json_page is not None else 'UNKNOWN'}\n"
                    f"ELEMENT_ID: {element_row['element_id']}\n"
                    f"ELEMENT_INDEX: {element_row['element_index']}\n"
                    f"ELEMENT_TYPE: {element_row['element_type']}\n"
                    f"{element_row['content']}"
                )
            return self._truncate("\n\n---\n\n".join(output), max_chars)

        row = self.connection.execute(
            "SELECT filename, content FROM documents WHERE filename = ?", (safe_name,)
        ).fetchone()
        if row is None:
            return (
                f"Document not found: {filename}. Use search_corpus first to "
                "search for the filename."
            )

        content = row["content"]
        if page is not None:
            # ``json_to_text`` writes the official zero-based page_id and its
            # one-based PDF equivalent in one marker.  Accept the old marker
            # as well so an index created by an earlier run remains readable.
            page_number = int(page)
            marker = f"[PDF_PAGE {page_number} | JSON_PAGE_ID {page_number - 1}]"
            start = content.find(marker)
            if start < 0:
                marker = f"[PAGE {page_number}]"
                start = content.find(marker)
            if start < 0:
                return f"Page {page} was not found in document {safe_name}."
            next_marker = re.search(
                r"\n\[(?:PDF_PAGE \d+ \| JSON_PAGE_ID \d+|PAGE \d+|PDF_PAGE UNKNOWN \| JSON_PAGE_ID UNKNOWN)\]",
                content[start + len(marker) :],
            )
            end = (
                start + len(marker) + next_marker.start()
                if next_marker
                else len(content)
            )
            content = content[start:end].strip()
        return self._truncate(content, max_chars)

    def _truncate(self, text: str, max_chars: int | None = None) -> str:
        limit = max_chars or self.max_tool_chars
        limit = max(500, min(int(limit), self.max_tool_chars))
        if len(text) <= limit:
            return text
        return text[:limit] + f"\n...[output truncated; {len(text)} characters total]"


class OfficeQAAgent:
    def __init__(
        self,
        client: OpenAI,
        model: str,
        store: CorpusStore,
        reasoning_effort: str | None = None,
        extra_body: dict[str, Any] | None = None,
        api_mode: str = "chat",
    ):
        self.client = client
        self.model = model
        self.store = store
        self.reasoning_effort = reasoning_effort or None
        self.extra_body = extra_body or {}
        self.api_mode = api_mode
        self.restrict_source_files = os.getenv(
            "OFFICEQA_RESTRICT_SOURCE_FILES", "1"
        ).strip().lower() not in {"0", "false", "no", "off"}
        self.tools = [
            tool
            for tool in TOOLS
            if not (
                self.restrict_source_files and tool["function"]["name"] == "web_search"
            )
        ]
        self.response_tools = [
            tool
            for tool in RESPONSE_TOOLS
            if not (self.restrict_source_files and tool["name"] == "web_search")
        ]
        self.system_prompt = SYSTEM_PROMPT
        if self.restrict_source_files:
            self.system_prompt += (
                "\n\nThis run is source-scoped. Local corpus tools may return content "
                "only from the current question's source_files. Do not attempt to "
                "read any other filename. web_search is unavailable."
            )
        self.temperature = env_float("MODEL_TEMPERATURE", 0.0)
        self.max_tokens = env_int("MODEL_MAX_TOKENS", 2048)
        # Keep a generous per-question ceiling while constraining the request
        # context to the most recent messages.  Both values remain tunable for
        # experiments, but these are the default processing rules.
        self.max_steps = env_int("OFFICEQA_MAX_STEPS", 200)
        self.context_window = max(1, env_int("OFFICEQA_CONTEXT_WINDOW", 30))
        self.search_results = env_int("OFFICEQA_SEARCH_RESULTS", 8)
        self.python_timeout = env_int("OFFICEQA_PYTHON_TIMEOUT", 30)
        self.web_timeout = max(1, int(env_float("WEB_SEARCH_TIMEOUT", 30.0) or 30.0))
        self.web_region = os.getenv("WEB_SEARCH_REGION", "us-en")
        self.web_safesearch = os.getenv("WEB_SEARCH_SAFESEARCH", "moderate")
        # The official OfficeQA custom-agent report uses DuckDuckGo. Keep the
        # backend explicit instead of DDGS's ``auto`` mode so this evaluator
        # does not silently switch to another search engine.
        self.web_backend = os.getenv("WEB_SEARCH_BACKEND", "duckduckgo")
        self.web_proxy = os.getenv("WEB_SEARCH_PROXY") or os.getenv("DDGS_PROXY")
        self.last_trace: list[dict[str, Any]] = []

    def _effective_allowed_files(
        self, allowed_files: frozenset[str] | set[str] | None
    ) -> frozenset[str] | None:
        if not self.restrict_source_files:
            return None
        return frozenset(allowed_files or ())

    def _scoped_question(
        self, question: str, allowed_files: frozenset[str] | set[str] | None
    ) -> str:
        if not self.restrict_source_files:
            return question
        names = ", ".join(sorted(allowed_files or ())) or "(none)"
        return (
            f"{question}\n\n"
            "[source_files scope]\n"
            "The only local parsed documents allowed for this question are: "
            f"{names}\n"
            "Do not attempt to read or infer content from any other local documents."
        )

    def _window_messages(self, messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
        """Keep the system prompt and the most recent complete assistant steps."""
        assistant_indices = [
            index
            for index, message in enumerate(messages)
            if message.get("role") == "assistant"
        ]
        if len(assistant_indices) <= self.context_window:
            return messages
        # A step starts with assistant and includes all following tool results
        # (and retry user messages) until the next assistant message.
        start = assistant_indices[-self.context_window]
        # Retain the original question as well as the system prompt, since it
        # remains the task anchor after older interaction steps are evicted.
        prefix = messages[:2] if len(messages) > 1 else messages[:1]
        return [*prefix, *messages[start:]]

    def _window_input(self, items: list[Any]) -> list[Any]:
        """Keep the most recent complete Responses function-call steps."""

        def item_type(item: Any) -> str | None:
            return (
                item.get("type")
                if isinstance(item, dict)
                else getattr(item, "type", None)
            )

        call_indices = [
            index
            for index, item in enumerate(items)
            if item_type(item) == "function_call"
        ]
        if len(call_indices) <= self.context_window:
            return items
        start = call_indices[-self.context_window]
        # The initial user question is the task anchor for every later step.
        prefix = items[:1] if items else []
        return [*prefix, *items[start:]]

    @staticmethod
    def _validate_python_code(code: str) -> str | None:
        """Reject obvious filesystem, process, and network access attempts."""
        blocked_modules = {
            "builtins",
            "ctypes",
            "fcntl",
            "ftplib",
            "glob",
            "http",
            "importlib",
            "io",
            "multiprocessing",
            "os",
            "pathlib",
            "pickle",
            "platform",
            "psutil",
            "resource",
            "shutil",
            "signal",
            "socket",
            "sqlite3",
            "subprocess",
            "sys",
            "tempfile",
            "urllib",
            "webbrowser",
        }
        blocked_calls = {
            "__import__",
            "breakpoint",
            "compile",
            "eval",
            "exec",
            "getattr",
            "globals",
            "help",
            "input",
            "locals",
            "open",
            "setattr",
            "vars",
        }
        blocked_attributes = {
            "connect",
            "genfromtxt",
            "glob",
            "listdir",
            "load",
            "loadtxt",
            "open",
            "read_csv",
            "read_excel",
            "read_json",
            "read_pickle",
            "read_text",
            "read_bytes",
            "rglob",
            "scandir",
            "save",
            "savetxt",
            "system",
            "to_csv",
            "to_json",
            "to_pickle",
            "unlink",
            "walk",
            "write_text",
            "write_bytes",
        }
        try:
            tree = ast.parse(code, mode="exec")
        except SyntaxError as exc:
            return f"python_exec code has a syntax error: {exc}"

        for node in ast.walk(tree):
            if isinstance(node, (ast.Import, ast.ImportFrom)):
                names = node.names
                for alias in names:
                    root = alias.name.split(".", 1)[0]
                    if root in blocked_modules:
                        return f"python_exec is not allowed to import module: {root}"
            elif isinstance(node, ast.Call):
                if isinstance(node.func, ast.Name) and node.func.id in blocked_calls:
                    return f"python_exec is not allowed to call: {node.func.id}"
                if (
                    isinstance(node.func, ast.Attribute)
                    and node.func.attr in blocked_attributes
                ):
                    return (
                        "python_exec is not allowed to perform file or system "
                        f"operation: {node.func.attr}"
                    )
        return None

    def _python_exec(self, args: dict[str, Any]) -> str:
        code = str(args.get("code", ""))
        if not code.strip():
            return "python_exec requires code."
        if len(code) > 12000:
            return "python_exec code exceeds 12,000 characters."
        validation_error = self._validate_python_code(code)
        if validation_error:
            return validation_error
        requested_timeout = args.get("timeout", self.python_timeout)
        try:
            timeout = max(1, min(int(requested_timeout), 120))
        except (TypeError, ValueError):
            timeout = self.python_timeout

        # Run in a temporary working directory with a minimal environment.
        # The child deliberately receives no corpus path, dataset path, index
        # path, or model configuration. The bootstrap also blocks filesystem,
        # process, and network APIs after preloading calculation libraries.
        child_env = {
            "PATH": os.getenv("PATH", ""),
            "LANG": os.getenv("LANG", "C.UTF-8"),
            "LC_ALL": os.getenv("LC_ALL", "C.UTF-8"),
        }
        sandbox_runner = r"""
import builtins
import math
import statistics
import sys

# Preload the calculation libraries before the audit hook is installed. This
# keeps normal numpy/pandas/scipy arithmetic available without allowing user
# code to open files or spawn processes.
import numpy  # noqa: F401
import pandas  # noqa: F401
import scipy  # noqa: F401

_user_code = compile(sys.argv[1], "<python_exec>", "exec")

_blocked_imports = {
    "builtins", "ctypes", "fcntl", "ftplib", "glob", "http", "importlib",
    "io", "multiprocessing", "os", "pathlib", "pickle", "platform", "psutil",
    "resource", "shutil", "signal", "socket", "sqlite3", "subprocess", "sys",
    "tempfile", "urllib", "webbrowser",
}
_real_import = builtins.__import__

def _safe_import(name, globals=None, locals=None, fromlist=(), level=0):
    root = name.split(".", 1)[0]
    if root in _blocked_imports:
        raise PermissionError(f"python_exec is not allowed to import module: {root}")
    return _real_import(name, globals, locals, fromlist, level)

def _audit(event, args):
    if (
        event == "open"
        or event.startswith(("os.", "subprocess.", "socket.", "ctypes."))
    ):
        raise PermissionError(
            f"python_exec is not allowed to perform system operation: {event}"
        )

builtins.__import__ = _safe_import
sys.addaudithook(_audit)
exec(_user_code, {"__name__": "__main__"})
"""
        try:
            with tempfile.TemporaryDirectory(prefix="officeqa-python-") as workdir:
                completed = subprocess.run(
                    [sys.executable, "-c", sandbox_runner, code],
                    cwd=workdir,
                    env=child_env,
                    capture_output=True,
                    text=True,
                    timeout=timeout,
                    check=False,
                )
        except TaskTimeoutError:
            raise
        except subprocess.TimeoutExpired:
            return (
                f"python_exec timed out after {timeout} seconds. Shorten the code "
                "or split the calculation."
            )
        except Exception as exc:
            return f"python_exec failed: {type(exc).__name__}: {exc}"

        output_parts = []
        if completed.stdout.strip():
            output_parts.append(f"STDOUT:\n{completed.stdout.strip()}")
        if completed.stderr.strip():
            output_parts.append(f"STDERR:\n{completed.stderr.strip()}")
        if not output_parts:
            output_parts.append("(no output)")
        output_parts.append(f"Exit code: {completed.returncode}")
        return self.store._truncate("\n\n".join(output_parts))

    def _web_search(self, args: dict[str, Any]) -> str:
        query = str(args.get("query", "")).strip()
        if not query:
            return "web_search requires a query."
        try:
            from ddgs import DDGS
        except ImportError:
            return (
                "The ddgs package required by web_search is not installed. "
                "Run `pip install -r requirements.txt` first."
            )
        try:
            max_results = max(1, min(int(args.get("max_results", 5)), 10))
        except (TypeError, ValueError):
            max_results = 5
        try:
            searcher = DDGS(proxy=self.web_proxy, timeout=self.web_timeout)
            results = searcher.text(
                query,
                region=self.web_region,
                safesearch=self.web_safesearch,
                max_results=max_results,
                backend=self.web_backend,
            )
        except TaskTimeoutError:
            raise
        except Exception as exc:
            return f"web_search failed: {type(exc).__name__}: {exc}"

        output = []
        for index, item in enumerate(results or [], start=1):
            title = item.get("title", "")
            url = item.get("href") or item.get("url", "")
            body = (
                item.get("body") or item.get("description") or item.get("content", "")
            )
            output.append(f"[{index}] {title}\nURL: {url}\n{body}")
        return self.store._truncate(
            "\n\n---\n\n".join(output) or "No web results were found."
        )

    def _tool_result(
        self,
        name: str,
        arguments: str,
        remaining_steps: int | None = None,
        allowed_files: frozenset[str] | set[str] | None = None,
    ) -> str:
        """Execute a tool and append a machine-readable budget reminder."""
        try:
            args = json.loads(arguments or "{}")
        except json.JSONDecodeError as exc:
            result = f"Tool arguments are not valid JSON: {exc}"
        else:
            try:
                if name == "search_corpus":
                    result = self.store.search(
                        str(args.get("query", "")),
                        int(args.get("max_results", self.search_results)),
                        allowed_files=allowed_files,
                    )
                elif name == "read_document":
                    result = self.store.read(
                        str(args.get("filename", "")),
                        page=args.get("page"),
                        max_chars=args.get("max_chars"),
                        element_id=args.get("element_id"),
                        allowed_files=allowed_files,
                    )
                elif name == "python_exec":
                    result = self._python_exec(args)
                elif name == "web_search":
                    if self.restrict_source_files:
                        result = (
                            "web_search is unavailable while source_files "
                            "restriction mode is enabled."
                        )
                    else:
                        result = self._web_search(args)
                elif name == "submit_answer":
                    result = "Answer submitted."
                else:
                    result = f"Unknown tool: {name}"
            except TaskTimeoutError:
                raise
            except Exception as exc:
                result = f"Tool execution failed: {type(exc).__name__}: {exc}"
        if remaining_steps is not None:
            result = (
                f"{result}\n\n<system_reminder>"
                f"This question has {max(0, remaining_steps)} steps remaining; "
                "finish promptly and call submit_answer. "
                "The current context retains only the 30 most recent complete "
                "interaction steps (the system prompt is always retained); older "
                "steps will no longer be provided."
                "</system_reminder>"
            )
        return result

    def _response_output_text(self, response: Any) -> str:
        text = getattr(response, "output_text", "") or ""
        if text:
            return str(text)
        parts: list[str] = []
        for item in getattr(response, "output", []) or []:
            if getattr(item, "type", None) != "message":
                continue
            for content in getattr(item, "content", []) or []:
                if getattr(content, "type", None) == "output_text":
                    value = getattr(content, "text", "")
                    if value:
                        parts.append(str(value))
        return "\n".join(parts)

    def _response_reasoning(self, response: Any) -> str | None:
        parts: list[str] = []
        for item in getattr(response, "output", []) or []:
            if getattr(item, "type", None) != "reasoning":
                continue
            summary = getattr(item, "summary", None) or []
            for entry in summary:
                value = getattr(entry, "text", None)
                if value:
                    parts.append(str(value))
        return "\n".join(parts) or None

    def _answer_responses(
        self,
        question: str,
        allowed_files: frozenset[str] | set[str] | None,
    ) -> tuple[str, int, list[dict[str, Any]]]:
        """Run the agent loop through the OpenAI Responses API.

        This mode is needed by gateways that reject function tools together
        with reasoning_effort on Chat Completions (for example gpt-5.6-sol).
        Prior Responses output items are kept locally because some gateways do
        not persist ``previous_response_id`` state.
        """
        scoped_question = self._scoped_question(question, allowed_files)
        trace: list[dict[str, Any]] = [
            {"step": 0, "role": "system", "content": self.system_prompt},
            {"step": 0, "role": "user", "content": scoped_question},
        ]
        self.last_trace = trace
        # Some OpenAI-compatible gateways do not persist Responses state for
        # ``previous_response_id``. Keep the exchange locally and resend the
        # prior output items together with each function result instead.
        conversation_input: list[Any] = [{"role": "user", "content": scoped_question}]
        final_retry_used = False

        for step in range(1, self.max_steps + 1):
            kwargs: dict[str, Any] = {
                "model": self.model,
                "instructions": self.system_prompt,
                "tools": self.response_tools,
                "tool_choice": "auto",
                "max_output_tokens": max(16, self.max_tokens),
                "input": self._window_input(conversation_input),
            }
            if self.reasoning_effort:
                kwargs["reasoning"] = {"effort": self.reasoning_effort}
            if self.extra_body:
                kwargs["extra_body"] = self.extra_body

            # Responses reasoning models generally reject temperature; retain
            # it only when no explicit reasoning level is requested.
            if self.temperature is not None and not self.reasoning_effort:
                kwargs["temperature"] = self.temperature
            try:
                response = self.client.responses.create(**kwargs)
            except Exception as exc:
                trace.append(
                    {
                        "step": step,
                        "role": "api_error",
                        "error": f"{type(exc).__name__}: {exc}",
                    }
                )
                self.last_trace = trace
                raise

            output_items = list(getattr(response, "output", []) or [])
            # Only function-call items are needed to continue the tool loop.
            # Raw Responses output items contain response-only fields such as
            # ``status`` and ``id`` that this gateway rejects when echoed as
            # input; normalize the accepted function-call shape explicitly.
            for item in output_items:
                if getattr(item, "type", None) != "function_call":
                    continue
                conversation_input.append(
                    {
                        "type": "function_call",
                        "call_id": getattr(item, "call_id", None)
                        or getattr(item, "id", None),
                        "name": getattr(item, "name", ""),
                        "arguments": getattr(item, "arguments", ""),
                    }
                )
            function_calls = [
                item
                for item in output_items
                if getattr(item, "type", None) == "function_call"
            ]
            content = self._response_output_text(response)
            reasoning = self._response_reasoning(response)
            serialized_calls = [
                {
                    "id": getattr(item, "call_id", None),
                    "type": "function",
                    "function": {
                        "name": getattr(item, "name", ""),
                        "arguments": getattr(item, "arguments", ""),
                    },
                }
                for item in function_calls
            ]
            trace.append(
                {
                    "step": step,
                    "role": "assistant",
                    "content": content,
                    "reasoning": reasoning,
                    "tool_calls": serialized_calls,
                    "finish_reason": getattr(response, "status", None),
                    "assistant_response": jsonable(response),
                }
            )

            if not function_calls:
                if "<FINAL_ANSWER>" in content.upper():
                    return content, step, trace
                if not final_retry_used:
                    final_retry_used = True
                    retry_message = (
                        "Your previous response was not a submission. "
                        "Call submit_answer now with only the concise final answer; "
                        "do not explain your reasoning."
                    )
                    trace.append(
                        {"step": step, "role": "user", "content": retry_message}
                    )
                    conversation_input.append(
                        {"role": "user", "content": retry_message}
                    )
                    continue
                return content, step, trace

            outputs: list[dict[str, Any]] = []
            for call in function_calls:
                call_id = getattr(call, "call_id", None) or getattr(call, "id", None)
                name = getattr(call, "name", "")
                arguments = getattr(call, "arguments", "")
                result = self._tool_result(
                    name,
                    arguments,
                    remaining_steps=self.max_steps - step,
                    allowed_files=allowed_files,
                )
                trace.append(
                    {
                        "step": step,
                        "role": "tool",
                        "tool_call_id": call_id,
                        "name": name,
                        "arguments": arguments,
                        "content": result,
                    }
                )
                if name == "submit_answer":
                    try:
                        submitted = json.loads(arguments or "{}").get("answer", "")
                    except json.JSONDecodeError:
                        submitted = ""
                    if submitted:
                        return f"<FINAL_ANSWER>{submitted}</FINAL_ANSWER>", step, trace
                outputs.append(
                    {
                        "type": "function_call_output",
                        "call_id": call_id,
                        "output": result,
                    }
                )
            # Send all tool outputs together on the next Responses turn.
            conversation_input.extend(outputs)
        return "", self.max_steps, trace

    def answer(
        self,
        question: str,
        allowed_files: frozenset[str] | set[str] | None = None,
    ) -> tuple[str, int, list[dict[str, Any]]]:
        effective_allowed_files = self._effective_allowed_files(allowed_files)
        if self.api_mode == "responses":
            return self._answer_responses(question, effective_allowed_files)
        return self._answer_chat(question, effective_allowed_files)

    def _answer_chat(
        self,
        question: str,
        allowed_files: frozenset[str] | set[str] | None,
    ) -> tuple[str, int, list[dict[str, Any]]]:
        scoped_question = self._scoped_question(question, allowed_files)
        messages: list[dict[str, Any]] = [
            {"role": "system", "content": self.system_prompt},
            {"role": "user", "content": scoped_question},
        ]
        trace: list[dict[str, Any]] = [
            {"step": 0, "role": "system", "content": self.system_prompt},
            {"step": 0, "role": "user", "content": scoped_question},
        ]
        self.last_trace = trace
        last_content = ""
        final_retry_used = False
        for step in range(1, self.max_steps + 1):
            kwargs: dict[str, Any] = {
                "model": self.model,
                "messages": self._window_messages(messages),
                "tools": self.tools,
                "tool_choice": "auto",
                "max_tokens": self.max_tokens,
            }
            # Several reasoning deployments (for example Claude Opus 5 on
            # Bedrock) reject temperature entirely.  Match Responses mode and
            # omit it whenever an explicit reasoning level is configured.
            if self.temperature is not None and not self.reasoning_effort:
                kwargs["temperature"] = self.temperature
            if self.reasoning_effort:
                kwargs["reasoning_effort"] = self.reasoning_effort
            if self.extra_body:
                kwargs["extra_body"] = self.extra_body
            try:
                response = self.client.chat.completions.create(**kwargs)
            except Exception as exc:
                trace.append(
                    {
                        "step": step,
                        "role": "api_error",
                        "error": f"{type(exc).__name__}: {exc}",
                    }
                )
                self.last_trace = trace
                raise
            if not response.choices:
                error = RuntimeError("模型没有返回 choices")
                trace.append({"step": step, "role": "api_error", "error": str(error)})
                self.last_trace = trace
                raise error
            message = response.choices[0].message
            last_content = message.content or ""
            tool_calls = message.tool_calls or []
            reasoning = getattr(message, "reasoning", None)
            if reasoning is None:
                reasoning = getattr(message, "reasoning_content", None)
            serialized_calls = [
                {
                    "id": call.id,
                    "type": call.type,
                    "function": {
                        "name": call.function.name,
                        "arguments": call.function.arguments,
                    },
                }
                for call in tool_calls
            ]
            trace.append(
                {
                    "step": step,
                    "role": "assistant",
                    "content": last_content,
                    "reasoning": reasoning,
                    "tool_calls": serialized_calls,
                    "finish_reason": response.choices[0].finish_reason,
                    # Preserve the complete SDK message, including provider-
                    # specific fields that may not be exposed above.
                    "assistant_response": jsonable(message),
                }
            )
            if not tool_calls:
                if "<FINAL_ANSWER>" in last_content.upper():
                    return last_content, step, trace
                if not final_retry_used:
                    final_retry_used = True
                    retry_message = (
                        "Your previous response was not a submission. "
                        "Call submit_answer now with only the concise final answer; "
                        "do not explain your reasoning."
                    )
                    messages.append(
                        {"role": "assistant", "content": last_content or None}
                    )
                    messages.append(
                        {
                            "role": "user",
                            "content": retry_message,
                        }
                    )
                    trace.append(
                        {"step": step, "role": "user", "content": retry_message}
                    )
                    continue
                return last_content, step, trace

            assistant_message: dict[str, Any] = {
                "role": "assistant",
                "content": message.content or None,
                "tool_calls": [],
            }
            for call in tool_calls:
                assistant_message["tool_calls"].append(
                    {
                        "id": call.id,
                        "type": "function",
                        "function": {
                            "name": call.function.name,
                            "arguments": call.function.arguments,
                        },
                    }
                )
            messages.append(assistant_message)
            for call in tool_calls:
                result = self._tool_result(
                    call.function.name,
                    call.function.arguments,
                    remaining_steps=self.max_steps - step,
                    allowed_files=allowed_files,
                )
                trace.append(
                    {
                        "step": step,
                        "role": "tool",
                        "tool_call_id": call.id,
                        "name": call.function.name,
                        "arguments": call.function.arguments,
                        "content": result,
                    }
                )
                if call.function.name == "submit_answer":
                    try:
                        submitted = json.loads(call.function.arguments or "{}").get(
                            "answer", ""
                        )
                    except json.JSONDecodeError:
                        submitted = ""
                    if submitted:
                        return f"<FINAL_ANSWER>{submitted}</FINAL_ANSWER>", step, trace
                messages.append(
                    {
                        "role": "tool",
                        "tool_call_id": call.id,
                        "content": result,
                    }
                )
        return last_content, self.max_steps, trace


def safe_filename_component(value: str) -> str:
    """Make a model name or uid safe for use in a result filename."""
    component = re.sub(r"[^A-Za-z0-9._-]+", "_", str(value)).strip("._")
    return component[:120] or "unknown"


def result_file_path(results_root: Path, model: str, problem_num: str) -> Path:
    """Create the per-problem trajectory path at the moment of writing."""
    trajectory_dir = results_root / "traj"
    trajectory_dir.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now().strftime("%Y%m%d-%H%M%S-%f")
    filename = (
        f"{safe_filename_component(model)}-prob"
        f"{safe_filename_component(problem_num)}-{timestamp}.jsonl"
    )
    return trajectory_dir / filename


def submitted_answer_from_trace(trace: list[dict[str, Any]]) -> str | None:
    """Return the non-empty answer passed to ``submit_answer``, if any.

    A plain ``<FINAL_ANSWER>`` assistant message is deliberately not treated
    as a successful completion.  The retry protocol requires the model to use
    the submission tool so that an answer cannot be accidentally lost between
    the model response and the scorer.
    """
    for event in trace or []:
        if not isinstance(event, dict):
            continue
        if event.get("role") != "tool" or event.get("name") != "submit_answer":
            continue
        try:
            arguments = json.loads(event.get("arguments") or "{}")
        except (TypeError, json.JSONDecodeError):
            continue
        answer = arguments.get("answer") if isinstance(arguments, dict) else None
        if answer is not None and str(answer).strip():
            return str(answer).strip()
    return None


def record_is_success(record: dict[str, Any]) -> bool:
    """Whether a saved trace is safe to skip during checkpoint resume."""
    if record.get("trace_version") != 2:
        return False
    if record.get("termination_status") != "completed":
        return False
    if record.get("submission_received") is True:
        return True
    # Backward compatibility for traces written before the explicit field was
    # added: inspect the trajectory rather than trusting ``termination_status``.
    return submitted_answer_from_trace(record.get("trajectory") or []) is not None


def existing_uids(results_root: Path, model: str) -> set[str]:
    """Find completed per-problem trace files for this model.

    Legacy aggregate JSONL files are intentionally ignored because they do not
    contain the new complete trajectory field.
    """
    return set(successful_records(results_root, model))


def successful_records(
    results_root: Path,
    model: str,
    allowed_uids: set[str] | None = None,
) -> dict[str, dict[str, Any]]:
    """Return one valid submitted record per UID for ``model``.

    A result directory can contain several attempts for the same UID because
    abnormal attempts are intentionally checkpointed.  For cumulative resume
    statistics we only want valid submissions, deduplicated by UID.  When old
    runs contain more than one valid submission, the lexically latest result
    filename wins because filenames include a timestamp.
    """
    if not results_root.is_dir():
        return {}
    records: dict[str, dict[str, Any]] = {}
    prefix = f"{safe_filename_component(model)}-prob"
    for path in results_root.rglob("*.jsonl"):
        try:
            relative_parts = path.relative_to(results_root).parts
        except ValueError:
            continue
        # Resume archives are historical snapshots, not active attempts.
        if "resume_archive" in relative_parts:
            continue
        if not path.name.startswith(prefix):
            continue
        try:
            with path.open(encoding="utf-8") as handle:
                record = json.loads(handle.readline())
        except (OSError, json.JSONDecodeError):
            continue
        uid = record.get("uid")
        if uid is None or not record_is_success(record):
            continue
        uid = str(uid)
        if allowed_uids is not None and uid not in allowed_uids:
            continue
        previous = records.get(uid)
        if previous is None or str(path) > str(previous.get("_result_path", "")):
            # Keep the path only internally while selecting the latest record;
            # callers receive ordinary JSON-compatible result dictionaries.
            selected = dict(record)
            selected["_result_path"] = str(path)
            records[uid] = selected
    for record in records.values():
        record.pop("_result_path", None)
    return records


def main() -> None:
    args = parse_args()
    if args.workers < 1:
        raise SystemExit("--workers 必须是正整数。")
    if args.worker_index < 0 or args.worker_index >= args.workers:
        raise SystemExit("--worker-index 必须位于 0 到 workers-1 之间。")
    questions = load_questions(args.dataset)
    json_files = sorted(args.corpus.rglob("*.json")) if args.corpus.is_dir() else []
    # ``OFFICEQA_RESULTS`` historically pointed to one aggregate JSONL file.
    # Keep that configuration usable by treating its parent as the new result
    # root; new runs always write one trace file per problem below that root.
    results_root = args.results
    if results_root.suffix.lower() == ".jsonl":
        results_root = results_root.parent
    results_root = results_root.expanduser().resolve()
    print(f"题目: {args.dataset}（{len(questions)} 条）")
    print(f"语料: {args.corpus}（{len(json_files)} 份 JSON）")
    print(f"结果目录: {results_root}")

    if args.check_only:
        if not json_files:
            raise SystemExit(f"语料目录为空或不存在：{args.corpus}")
        print("路径检查通过。使用 `cp .env.example .env` 后填写模型配置即可运行。")
        return

    api_key = os.getenv("MODEL_API_KEY") or os.getenv("OPENAI_API_KEY")
    model = args.model or os.getenv("MODEL_NAME")
    if not api_key:
        raise SystemExit("缺少 MODEL_API_KEY（或 OPENAI_API_KEY）。")
    if not model:
        raise SystemExit("缺少 MODEL_NAME，例如 gpt-4.1 或你的本地模型名。")

    extra_body: dict[str, Any] = {}
    if args.extra_body:
        try:
            parsed_extra_body = json.loads(args.extra_body)
        except json.JSONDecodeError as exc:
            raise SystemExit(f"--extra-body 必须是有效 JSON：{exc}") from exc
        if not isinstance(parsed_extra_body, dict):
            raise SystemExit("--extra-body 必须是 JSON 对象。")
        extra_body = parsed_extra_body

    base_url = os.getenv("MODEL_BASE_URL", "https://api.openai.com/v1")
    client = OpenAI(
        api_key=api_key,
        base_url=base_url,
        timeout=env_float("MODEL_TIMEOUT", 180.0),
    )
    index_path = resolve_path(
        os.getenv("OFFICEQA_INDEX", ""), ROOT / "corpus/.officeqa_index.sqlite3"
    )
    store = CorpusStore(
        args.corpus,
        index_path,
        env_int("OFFICEQA_MAX_TOOL_CHARS", 18000),
    )
    agent = OfficeQAAgent(
        client,
        model,
        store,
        reasoning_effort=args.reasoning_effort,
        extra_body=extra_body,
        api_mode=args.api_mode,
    )

    results_root.mkdir(parents=True, exist_ok=True)
    done = set() if args.no_resume else existing_uids(results_root, model)
    selected = questions[args.offset :]
    if args.limit is not None:
        selected = selected[: args.limit]
    # Shard the selected questions across independent evaluator processes.
    # For example, workers=5 and worker-index=2 runs uid positions 2, 7, 12...
    selected = selected[args.worker_index :: args.workers]
    selected = [row for row in selected if str(row["uid"]) not in done]
    worker_label = (
        f"；分片: {args.worker_index + 1}/{args.workers}" if args.workers > 1 else ""
    )
    if args.tolerance < 0:
        raise SystemExit("--tolerance 必须大于或等于 0。")
    if args.task_timeout <= 0:
        raise SystemExit("--task-timeout 必须大于 0。")
    print(
        f"本次待评测: {len(selected)} 条；模型: {model}；接口: {base_url}"
        f"；官方评分 tolerance={args.tolerance:g}"
        f"；单题超时={args.task_timeout:g}s{worker_label}"
    )

    attempted_scores: list[float] = []
    try:
        for number, row in enumerate(selected, start=1):
            started = time.monotonic()
            record: dict[str, Any] = {
                "trace_version": 2,
                "uid": row["uid"],
                "problem_num": row["uid"],
                "model": model,
                "question": row["question"],
                "ground_truth": row["answer"],
                "source_files": row["source_files"],
                "allowed_source_files": sorted(source_file_names(row["source_files"])),
                "prediction": "",
                "score": 0.0,
                "scorer": "officeqa.reward.score_answer",
                "tolerance": args.tolerance,
                "planned_steps": agent.max_steps,
                "max_steps_allowed": agent.max_steps,
                "submission_received": False,
                "trajectory": [],
            }
            try:
                with task_deadline(args.task_timeout):
                    prediction, steps, trajectory = agent.answer(
                        row["question"],
                        allowed_files=source_file_names(row["source_files"]),
                    )
                record.update(
                    {
                        "prediction": prediction,
                        "steps": steps,
                        "trajectory": trajectory,
                    }
                )
                submitted_answer = submitted_answer_from_trace(trajectory)
                # Reaching the final permitted turn without a submission is
                # an abnormal max-step termination.  A valid submit_answer on
                # the boundary remains scoreable; it did not exceed the cap.
                reached_step_limit = (
                    steps >= agent.max_steps and submitted_answer is None
                )
                abnormal_reasons: list[str] = []
                if submitted_answer is None:
                    abnormal_reasons.append("no_final_submission")
                if reached_step_limit:
                    abnormal_reasons.append("max_steps_reached")

                if abnormal_reasons:
                    # Do not score an incomplete attempt.  It remains on disk
                    # as an abnormal checkpoint and is selected by the next
                    # retry round because ``existing_uids`` only accepts
                    # traces with a real submit_answer call.
                    reason = "+".join(abnormal_reasons)
                    record.update(
                        {
                            "score": 0.0,
                            "termination_status": "abnormal",
                            "termination_reason": reason,
                            "termination_reasons": abnormal_reasons,
                        }
                    )
                    attempted_scores.append(0.0)
                    print(
                        f"[{number}/{len(selected)}] uid={row['uid']} "
                        f"ABNORMAL_{reason} time={time.monotonic() - started:.1f}s"
                    )
                else:
                    # This is the reference scorer shipped by databricks/officeqa;
                    # no local normalization or alternative metric is applied.
                    score = score_answer(
                        row["answer"], prediction, tolerance=args.tolerance
                    )
                    record.update(
                        {
                            "score": score,
                            "submission_received": True,
                            "termination_status": "completed",
                        }
                    )
                    attempted_scores.append(float(score))
                    print(
                        f"[{number}/{len(selected)}] uid={row['uid']} "
                        f"score={score:.0f} time={time.monotonic() - started:.1f}s"
                    )
            except TaskTimeoutError:
                record.update(
                    {
                        "error": (
                            f"TaskTimeoutError: question exceeded {args.task_timeout:g} seconds"
                        ),
                        "termination_status": "abnormal",
                        "termination_reason": "task_timeout",
                        "task_timeout_seconds": args.task_timeout,
                        "trajectory": agent.last_trace,
                    }
                )
                print(
                    f"[{number}/{len(selected)}] uid={row['uid']} "
                    f"ABNORMAL_TIMEOUT time={time.monotonic() - started:.1f}s"
                )
            except Exception as exc:
                record.update(
                    {
                        "error": f"{type(exc).__name__}: {exc}",
                        "termination_status": "abnormal",
                        "termination_reason": "exception",
                        "trajectory": agent.last_trace,
                    }
                )
                print(f"[{number}/{len(selected)}] uid={row['uid']} ERROR: {exc}")
            record["steps"] = len(
                {
                    event.get("step")
                    for event in record["trajectory"]
                    if event.get("step")
                }
            )
            record["elapsed_seconds"] = round(time.monotonic() - started, 3)
            record["finished_at"] = (
                datetime.now().astimezone().isoformat(timespec="seconds")
            )
            output_path = result_file_path(results_root, model, str(row["uid"]))
            record["result_file"] = str(output_path)
            with output_path.open("w", encoding="utf-8") as handle:
                handle.write(json.dumps(record, ensure_ascii=False) + "\n")
            print(f"  轨迹已写入: {output_path}")
    finally:
        store.close()

    if attempted_scores:
        print(
            f"本次准确率（official reward, tolerance={args.tolerance:g}）："
            f"{sum(attempted_scores) / len(attempted_scores):.4f}"
        )
    print(f"结果目录: {results_root}")


if __name__ == "__main__":
    main()
