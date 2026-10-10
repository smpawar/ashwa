"""Check everything the experiment needs before spending API calls.

    .venv/bin/python preflight.py --before-seed   # before seed.py (skips the knowledge-base check)
    .venv/bin/python preflight.py
"""

import os
import sys

from common import (DSN, EFFORT, EMBED_DIM, EMBED_MODEL, EMBED_PROVIDER, LLM_PROVIDER, MODEL, TRACE_SOURCE,
                    embed, genai_client, langfuse, sql_untraced)

results = []


def check(label, fn):
    try:
        detail = fn()
        results.append(True)
        print(f"  OK    {label}{f'  ({detail})' if detail else ''}")
    except Exception as e:  # noqa: BLE001
        results.append(False)
        print(f"  FAIL  {label}: {type(e).__name__}: {str(e).splitlines()[0][:200]}")


# --- Claude ---------------------------------------------------------------

def anthropic_credentials():
    if os.environ.get("ANTHROPIC_API_KEY"):
        return "ANTHROPIC_API_KEY set"
    import shutil
    if os.environ.get("ANTHROPIC_AUTH_TOKEN") or shutil.which("ant"):
        # The SDK can also resolve a token or an `ant auth login` profile; the next check proves it.
        return "ANTHROPIC_API_KEY not set; trying ANTHROPIC_AUTH_TOKEN / `ant auth login` profile"
    raise RuntimeError("ANTHROPIC_API_KEY is empty in .env (get a key at https://platform.claude.com/settings/keys)")


def anthropic_model():
    import anthropic
    m = anthropic.Anthropic().models.retrieve(MODEL)
    return f"{m.display_name}, context {getattr(m, 'max_input_tokens', '?')}"


def anthropic_answers():
    import anthropic
    r = anthropic.Anthropic().messages.create(
        model=MODEL, max_tokens=2048, output_config={"effort": "low"},
        messages=[{"role": "user", "content": "Reply with the single word: ok"}],
    )
    if r.stop_reason == "refusal":
        raise RuntimeError("model refused the test prompt")
    text = "".join(b.text for b in r.content if b.type == "text").strip()
    return f"replied {text[:20]!r}, {r.usage.input_tokens}+{r.usage.output_tokens} tokens"


# --- Gemini ---------------------------------------------------------------

def gemini_credentials():
    if os.environ.get("GOOGLE_GENAI_USE_VERTEXAI", "").upper() == "TRUE":
        return "using Vertex AI credentials"
    if not os.environ.get("GOOGLE_API_KEY"):
        raise RuntimeError("GOOGLE_API_KEY is empty in .env")
    return "set"


def gemini_model_available(name):
    def fn():
        models = {m.name.removeprefix("models/") for m in genai_client().models.list()}
        if name not in models:
            close = sorted(m for m in models if "flash" in m or "embed" in m)[:8]
            raise RuntimeError(f"'{name}' not available on this key. Candidates: {', '.join(close)}")
        return "available"
    return fn


def gemini_answers():
    r = genai_client().models.generate_content(model=MODEL, contents="Reply with the single word: ok")
    return f"replied {r.text.strip()[:20]!r}"


# --- Shared ---------------------------------------------------------------

def embeddings_work():
    vec = embed("refund policy", task="RETRIEVAL_QUERY")
    if len(vec) != EMBED_DIM:
        raise RuntimeError(f"expected {EMBED_DIM} dimensions, got {len(vec)}")
    return f"{len(vec)} dimensions"


def langfuse_auth():
    if not langfuse.auth_check():
        raise RuntimeError("auth_check returned False; check LANGFUSE_* in .env and that Langfuse is running")
    return os.environ.get("LANGFUSE_HOST")


def app_db():
    n_orders = sql_untraced("SELECT count(*) AS n FROM orders", fetch="one")["n"]
    rows = sql_untraced("SELECT embed_model, count(*) AS n FROM kb_docs GROUP BY embed_model")
    if not rows:
        raise RuntimeError("knowledge base is empty; run seed.py")
    models = {r["embed_model"] for r in rows}
    if models != {EMBED_MODEL}:
        raise RuntimeError(f"knowledge base was embedded with {sorted(models)} but {EMBED_MODEL} is configured; re-run seed.py")
    return f"{n_orders} orders, {sum(r['n'] for r in rows)} KB articles at {DSN.split('@')[-1]}"


def clickhouse():
    import clickhouse_connect
    ch = clickhouse_connect.get_client(
        host=os.environ.get("CLICKHOUSE_HOST", "localhost"), port=int(os.environ.get("CLICKHOUSE_PORT", 8123)),
        username=os.environ.get("CLICKHOUSE_USER", "clickhouse"), password=os.environ.get("CLICKHOUSE_PASSWORD", "clickhouse"))
    return f"events_core rows: {ch.query('SELECT count() FROM events_core').result_rows[0][0]}"


if __name__ == "__main__":
    before_seed = "--before-seed" in sys.argv
    print(f"Preflight: {LLM_PROVIDER} model {MODEL} (effort {EFFORT}), "
          f"{EMBED_PROVIDER} embeddings {EMBED_MODEL}, trace source {TRACE_SOURCE}")
    if LLM_PROVIDER == "anthropic":
        check("Claude credentials", anthropic_credentials)
        if results[-1]:
            check(f"model {MODEL}", anthropic_model)
        if results[-1]:
            check(f"{MODEL} answers", anthropic_answers)
    else:
        check("Gemini credentials", gemini_credentials)
        if results[-1]:
            check(f"model {MODEL}", gemini_model_available(MODEL))
            check(f"{MODEL} answers", gemini_answers)
    if EMBED_PROVIDER == "gemini" and LLM_PROVIDER != "gemini":
        check("Gemini credentials (embeddings)", gemini_credentials)
    check(f"embeddings ({EMBED_MODEL})", embeddings_work)
    check("Langfuse", langfuse_auth)
    if not before_seed:
        check("app database seeded", app_db)
    check("Langfuse ClickHouse", clickhouse)
    langfuse.flush()
    ok = all(results)
    print("\nAll checks passed." if ok else "\nFix the failures above before running the workload.")
    sys.exit(0 if ok else 1)
