"""ADK support agent with four database-backed tools."""

import uuid
from datetime import datetime, timezone

from anthropic import AsyncAnthropic
from google.adk.agents import Agent
from google.adk.models import AnthropicGenerateContentConfig, Gemini
from google.adk.models.anthropic_llm import AnthropicLlm
from google.genai import types

from common import EFFORT, LLM_PROVIDER, MODEL, embed, sql
from prompts import PROMPTS


def lookup_order(order_id: str) -> dict:
    """Look up an order by its order number.

    Args:
        order_id: The customer's order number, digits only (e.g. "100101").

    Returns:
        Order status, product, carrier, tracking number and delivery date, or an error.
    """
    row = sql("lookup_order",
              "SELECT order_id, product, amount, status, carrier, tracking_no, ordered_at, delivered_at "
              "FROM orders WHERE order_id = %s",
              (order_id.strip().lstrip("#"),), fetch="one")
    if not row:
        return {"error": f"No order found with number {order_id}."}
    return {k: (v.isoformat() if isinstance(v, datetime) else str(v) if v is not None else None)
            for k, v in row.items()}


def search_knowledge_base(query: str) -> dict:
    """Search the company knowledge base for policies and product information.

    Args:
        query: A short natural-language description of what to look up.

    Returns:
        The three most relevant knowledge-base articles.
    """
    vec = embed(query)
    rows = sql("kb_vector_search",
               "SELECT title, body, 1 - (embedding <=> %s::vector) AS similarity "
               "FROM kb_docs ORDER BY embedding <=> %s::vector LIMIT 3",
               (vec, vec))
    return {"articles": [{"title": r["title"], "body": r["body"],
                          "similarity": round(float(r["similarity"]), 3)} for r in rows]}


def issue_refund(order_id: str, reason: str) -> dict:
    """Issue a refund for a delivered order, if it is eligible.

    Args:
        order_id: The order number to refund.
        reason: Why the customer wants a refund.

    Returns:
        The refund ID and timing, or the reason the refund was refused.
    """
    oid = order_id.strip().lstrip("#")
    row = sql("refund_eligibility",
              "SELECT amount, status, delivered_at FROM orders WHERE order_id = %s", (oid,), fetch="one")
    if not row:
        return {"error": f"No order found with number {order_id}."}
    if row["status"] != "delivered":
        return {"refused": f"Order is {row['status']}; only delivered orders can be refunded."}
    days = (datetime.now(timezone.utc) - row["delivered_at"]).days
    if days > 30:
        return {"refused": f"Delivered {days} days ago, outside the 30-day refund window."}
    existing = sql("existing_refund", "SELECT refund_id FROM refunds WHERE order_id = %s", (oid,), fetch="one")
    if existing:
        return {"refused": f"Order already refunded ({existing['refund_id']})."}
    refund_id = f"RF-{uuid.uuid4().hex[:8].upper()}"
    sql("insert_refund", "INSERT INTO refunds (refund_id, order_id, amount, reason) VALUES (%s, %s, %s, %s)",
        (refund_id, oid, row["amount"], reason), fetch="none")
    return {"refund_id": refund_id, "amount": str(row["amount"]), "eta": "5-7 business days"}


def escalate_to_human(reason: str) -> dict:
    """Hand the conversation to a human support agent.

    Args:
        reason: A one-line summary of why the customer needs a human.

    Returns:
        The ticket number.
    """
    row = sql("insert_ticket", "INSERT INTO tickets (reason) VALUES (%s) RETURNING ticket_id", (reason,), fetch="one")
    return {"ticket": f"T-{row['ticket_id']}", "queue": "tier2"}


def _model_and_config():
    if LLM_PROVIDER == "anthropic":
        # Official Anthropic SDK client; extra retries ride out 429s during the run.
        # No temperature: current Claude models reject sampling parameters, and
        # ADK drops them when effort is set. Effort is set explicitly instead.
        model = AnthropicLlm(model=MODEL, max_tokens=16000, client=AsyncAnthropic(max_retries=6))
        return model, AnthropicGenerateContentConfig(effort=EFFORT)
    model = Gemini(model=MODEL, retry_options=types.HttpRetryOptions(attempts=6, initial_delay=2, max_delay=60))
    return model, types.GenerateContentConfig(temperature=0)


def build_agent(prompt_version: str) -> Agent:
    model, config = _model_and_config()
    return Agent(
        name="support_agent",
        model=model,
        instruction=PROMPTS[prompt_version],
        tools=[lookup_order, search_knowledge_base, issue_refund, escalate_to_human],
        generate_content_config=config,
    )
