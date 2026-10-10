"""Shared plumbing: settings, Langfuse client, app-database access, embeddings.

Every SQL statement and every embedding call is wrapped in its own Langfuse
span, so a trace shows the database side of each agent turn (ASHWA's
"retrieval time" and "Top SQL" sections need this).
"""

import hashlib
import os
from contextlib import contextmanager

from dotenv import load_dotenv

load_dotenv(os.path.join(os.path.dirname(__file__), ".env"))

import psycopg
from langfuse import Langfuse
from pgvector.psycopg import register_vector
from psycopg.rows import dict_row

# --- Agent model -----------------------------------------------------------
#   anthropic (default): Claude through ADK's AnthropicLlm (official Anthropic SDK; ANTHROPIC_API_KEY)
#   gemini:              Gemini through ADK's Gemini class (GOOGLE_API_KEY)
LLM_PROVIDER = os.environ.get("ASHWA_LLM_PROVIDER", "anthropic")
if LLM_PROVIDER not in ("anthropic", "gemini"):
    raise ValueError(f"ASHWA_LLM_PROVIDER must be 'anthropic' or 'gemini', not {LLM_PROVIDER!r}")
MODEL = os.environ.get("ASHWA_MODEL") or {"anthropic": "claude-opus-5-5", "gemini": "gemini-2.5-flash"}[LLM_PROVIDER]
# Claude reasoning effort. Set explicitly: Claude Opus 5.5 defaults to "medium",
# other Claude models to "high", and the experiment must not depend on defaults.
EFFORT = os.environ.get("ASHWA_EFFORT", "medium")

# --- Embeddings ------------------------------------------------------------
# Anthropic has no embeddings API.
#   local (default): fastembed, runs on this machine, no key (model downloads once, ~130 MB)
#   gemini:          Gemini embeddings (GOOGLE_API_KEY)
# Changing provider or model changes the vectors: re-run seed.py afterwards.
EMBED_PROVIDER = os.environ.get("ASHWA_EMBED_PROVIDER", "local")
if EMBED_PROVIDER not in ("local", "gemini"):
    raise ValueError(f"ASHWA_EMBED_PROVIDER must be 'local' or 'gemini', not {EMBED_PROVIDER!r}")
EMBED_MODEL = os.environ.get("ASHWA_EMBED_MODEL") or {"local": "BAAI/bge-small-en-v1.5",
                                                      "gemini": "gemini-embedding-001"}[EMBED_PROVIDER]
EMBED_DIM = {"local": 384, "gemini": 768}[EMBED_PROVIDER]

DSN = os.environ.get("APP_DB_DSN", "postgresql://ashwa:ashwa@localhost:5433/support")

# Exactly one source of ADK spans, or LLM and tool calls are counted twice.
#   openinference (default): the OpenInference instrumentor. It disables ADK's
#                            built-in tracer itself and re-emits LLM/tool spans.
#   adk:                     ADK's built-in tracing only (scope "gcp.vertex.agent").
TRACE_SOURCE = os.environ.get("ASHWA_TRACE_SOURCE", "openinference")
if TRACE_SOURCE not in ("openinference", "adk"):
    raise ValueError(f"ASHWA_TRACE_SOURCE must be 'openinference' or 'adk', not {TRACE_SOURCE!r}")

langfuse = Langfuse()


def instrument_adk():
    if TRACE_SOURCE == "openinference":
        from openinference.instrumentation.google_adk import GoogleADKInstrumentor
        GoogleADKInstrumentor().instrument()


_genai = None
_local_embedder = None
_conn = None


def genai_client():
    global _genai
    if _genai is None:
        from google import genai
        _genai = genai.Client()
    return _genai


def local_embedder():
    global _local_embedder
    if _local_embedder is None:
        from fastembed import TextEmbedding
        _local_embedder = TextEmbedding(model_name=EMBED_MODEL)
    return _local_embedder


def conn() -> psycopg.Connection:
    global _conn
    if _conn is None or _conn.closed:
        _conn = psycopg.connect(DSN, autocommit=True, row_factory=dict_row)
        _conn.execute("CREATE EXTENSION IF NOT EXISTS vector")
        register_vector(_conn)
    return _conn


def _run(statement: str, params, fetch: str):
    with conn().cursor() as cur:
        cur.execute(statement, params)
        rows = None
        if fetch == "all" and cur.description:
            rows = cur.fetchall()
        elif fetch == "one" and cur.description:
            rows = cur.fetchone()
        return rows, cur.rowcount


def sql_untraced(statement: str, params=None, fetch: str = "all"):
    """For the test harness (evaluation, resets): not part of the agent's workload."""
    return _run(statement, params, fetch)[0]


def sql(name: str, statement: str, params=None, fetch: str = "all"):
    """Run one SQL statement inside a span named sql:<name>."""
    with langfuse.start_as_current_observation(
        as_type="retriever" if statement.lstrip().upper().startswith("SELECT") else "span",
        name=f"sql:{name}", metadata={"db.system": "postgresql", "db.statement": statement},
    ) as span:
        rows, rowcount = _run(statement, params, fetch)
        span.update(metadata={"db.rowcount": rowcount})
        return rows


def content_hash(text: str) -> str:
    return hashlib.sha256(text.encode()).hexdigest()[:16]


@contextmanager
def _embedding_span(text: str, task: str):
    with langfuse.start_as_current_observation(
        as_type="embedding",
        name="embed",
        model=EMBED_MODEL,
        input=text[:500],
        metadata={"content_hash": content_hash(text), "task_type": task, "embed_provider": EMBED_PROVIDER},
    ) as gen:
        yield gen


def embed(text: str, task: str = "RETRIEVAL_QUERY") -> list[float]:
    """Embed one text (task: RETRIEVAL_QUERY or RETRIEVAL_DOCUMENT). The content
    hash is recorded so ASHWA can find repeated embedding of unchanged content
    (the "hard parse" analogue)."""
    with _embedding_span(text, task) as gen:
        if EMBED_PROVIDER == "local":
            fn = local_embedder().query_embed if task == "RETRIEVAL_QUERY" else local_embedder().passage_embed
            vec = next(iter(fn(text))).tolist()
        else:
            from google.genai import types
            resp = genai_client().models.embed_content(
                model=EMBED_MODEL, contents=text,
                config=types.EmbedContentConfig(task_type=task, output_dimensionality=EMBED_DIM),
            )
            vec = resp.embeddings[0].values
        gen.update(usage_details={"input": max(1, len(text) // 4)}, output=f"vector[{EMBED_DIM}]")
        return vec
