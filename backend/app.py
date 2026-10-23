import os
import json
import logging
import datetime
from decimal import Decimal
from pathlib import Path
from typing import Optional

import requests
import snowflake.connector
from dotenv import load_dotenv
from fastapi import BackgroundTasks, FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

# .env stays at the project root, shared with tableau_extension/ and snowflake_native_app/.
PROJECT_ROOT = Path(__file__).resolve().parent.parent
load_dotenv(PROJECT_ROOT / ".env")
FRONTEND_DIR = Path(os.getenv("FRONTEND_DIR", PROJECT_ROOT / "frontend"))

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

app = FastAPI(title="Cortex Agent Tableau Middleware")

cors_origins = os.getenv("CORS_ORIGINS", "*").split(",")
app.add_middleware(
    CORSMiddleware,
    allow_origins=cors_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

SNOWFLAKE_ACCOUNT = os.getenv("SNOWFLAKE_ACCOUNT")
SNOWFLAKE_HOST = os.getenv("SNOWFLAKE_HOST")
SNOWFLAKE_USER = os.getenv("SNOWFLAKE_USER")
SNOWFLAKE_PASSWORD = os.getenv("SNOWFLAKE_PASSWORD")
SNOWFLAKE_ROLE = os.getenv("SNOWFLAKE_ROLE", "SYSADMIN")
SNOWFLAKE_WAREHOUSE = os.getenv("SNOWFLAKE_WAREHOUSE", "COMPUTE_WH")
# Trial accounts cannot call Cortex Agents; this mode calls Cortex Analyst
# with the semantic view directly and runs the generated SQL instead.
USE_CORTEX_ANALYST = os.getenv("USE_CORTEX_ANALYST", "false").lower() == "true"
# Saved chat history: 0 turns it off (the chat then behaves exactly as before).
# The table is created by backend/chat_history_setup.sql.
CHAT_HISTORY_DAYS = int(os.getenv("CHAT_HISTORY_DAYS", "0"))
CHAT_HISTORY_TABLE = os.getenv("CHAT_HISTORY_TABLE", "PHARMA_DEMO.CHAT_APP.CHAT_HISTORY")
CHAT_HISTORY_MAX_ROWS = 200


def _load_agent_profiles() -> dict:
    """Read agent profiles from AGENTS=ID1,ID2 and <ID>_DATABASE, <ID>_SCHEMA, ... variables.

    Without AGENTS, a single DEFAULT profile is built from the original
    SNOWFLAKE_DATABASE / SNOWFLAKE_SCHEMA / AGENT_NAME / SEMANTIC_VIEW variables.
    """
    ids = [i.strip().upper() for i in os.getenv("AGENTS", "").split(",") if i.strip()]
    if not ids:
        database = os.getenv("SNOWFLAKE_DATABASE", "TABLEAU_DEMO")
        schema = os.getenv("SNOWFLAKE_SCHEMA", "TABLEAU_AGENT_DEMO")
        return {"DEFAULT": {
            "id": "DEFAULT",
            "label": os.getenv("AGENT_LABEL", "Sales Analytics"),
            "database": database,
            "schema": schema,
            "agent_name": os.getenv("AGENT_NAME", "SALES_AGENT"),
            "semantic_view": os.getenv("SEMANTIC_VIEW", f"{database}.{schema}.SALES_SEMANTIC_VIEW"),
            "warehouse": SNOWFLAKE_WAREHOUSE,
            "suggestions": [],
        }}
    profiles = {}
    for pid in ids:
        def var(name, default=None):
            value = os.getenv(f"{pid}_{name}", default)
            if value is None:
                raise RuntimeError(f"Missing {pid}_{name} in .env (listed in AGENTS)")
            return value
        database, schema = var("DATABASE"), var("SCHEMA")
        profiles[pid] = {
            "id": pid,
            "label": var("LABEL", pid.title()),
            "database": database,
            "schema": schema,
            "agent_name": var("AGENT_NAME"),
            "semantic_view": var("SEMANTIC_VIEW", ""),
            "warehouse": var("WAREHOUSE", SNOWFLAKE_WAREHOUSE),
            "suggestions": [s.strip() for s in var("SUGGESTIONS", "").split("|") if s.strip()],
        }
    return profiles


AGENT_PROFILES = _load_agent_profiles()
DEFAULT_AGENT = next(iter(AGENT_PROFILES))


def _profile(agent_id: Optional[str]) -> dict:
    key = (agent_id or DEFAULT_AGENT).upper()
    if key not in AGENT_PROFILES:
        raise HTTPException(status_code=400, detail=f"Unknown agent '{agent_id}'. Configured: {', '.join(AGENT_PROFILES)}")
    return AGENT_PROFILES[key]


SPCS_TOKEN_PATH = "/snowflake/session/token"


def _is_running_in_spcs() -> bool:
    return os.path.exists(SPCS_TOKEN_PATH)


def _get_spcs_login_token() -> str:
    with open(SPCS_TOKEN_PATH, "r") as f:
        return f.read()


def _open_session(profile: dict):
    """Return (connection, REST host, log tag) for SPCS or local mode."""
    if _is_running_in_spcs():
        conn = snowflake.connector.connect(
            host=SNOWFLAKE_HOST,
            account=SNOWFLAKE_ACCOUNT,
            token=_get_spcs_login_token(),
            authenticator="oauth",
            warehouse=profile["warehouse"],
            database=profile["database"],
            schema=profile["schema"],
        )
        return conn, SNOWFLAKE_HOST, "SPCS"
    conn = snowflake.connector.connect(
        account=SNOWFLAKE_ACCOUNT,
        user=SNOWFLAKE_USER,
        password=SNOWFLAKE_PASSWORD,
        role=SNOWFLAKE_ROLE,
        warehouse=profile["warehouse"],
        database=profile["database"],
        schema=profile["schema"],
        session_parameters={"PYTHON_CONNECTOR_QUERY_RESULT_FORMAT": "json"},
    )
    account_parts = SNOWFLAKE_ACCOUNT.replace("_", "-").split(".")
    return conn, f"{account_parts[0]}.snowflakecomputing.com", "Local"


def _post(conn, url: str, payload: dict, tag: str) -> dict:
    headers = {
        "Authorization": f'Snowflake Token="{conn.rest.token}"',
        "Content-Type": "application/json",
        "Accept": "application/json",
    }
    logger.info(f"[{tag}] Calling {url}")
    resp = requests.post(url, json=payload, headers=headers, timeout=120)
    logger.info(f"[{tag}] Response status: {resp.status_code}")
    if resp.status_code != 200:
        logger.error(f"[{tag}] Error: {resp.text[:1000]}")
        raise HTTPException(status_code=resp.status_code, detail=resp.text)
    return resp.json()


def _call_agent(messages: list, profile: dict) -> dict:
    conn, host, tag = _open_session(profile)
    url = (
        f"https://{host}/api/v2/databases/{profile['database']}"
        f"/schemas/{profile['schema']}/agents/{profile['agent_name']}:run"
    )
    try:
        return _post(conn, url, {"messages": messages, "stream": False}, tag)
    finally:
        conn.close()


def _json_safe(value):
    if isinstance(value, Decimal):
        return float(value)
    if isinstance(value, (datetime.date, datetime.datetime, datetime.time)):
        return value.isoformat()
    return value


def _chat_via_analyst(messages: list, profile: dict) -> "ChatResponse":
    if not profile["semantic_view"]:
        raise HTTPException(status_code=400, detail=f"{profile['id']}_SEMANTIC_VIEW is required for Cortex Analyst mode")
    # Cortex Analyst expects the assistant role to be named "analyst".
    analyst_messages = [
        {**m, "role": "analyst" if m.get("role") == "assistant" else m.get("role")}
        for m in messages
    ]
    conn, host, tag = _open_session(profile)
    try:
        body = _post(
            conn,
            f"https://{host}/api/v2/cortex/analyst/message",
            {"messages": analyst_messages, "semantic_view": profile["semantic_view"]},
            tag,
        )
        answer_parts = []
        sql_text = None
        data_rows = None
        suggestions = []
        for item in body.get("message", {}).get("content", []):
            item_type = item.get("type")
            if item_type == "text":
                answer_parts.append(item.get("text", ""))
            elif item_type == "sql":
                sql_text = item.get("statement")
            elif item_type == "suggestions":
                suggestions.extend(item.get("suggestions", []))
        if sql_text:
            cur = conn.cursor(snowflake.connector.DictCursor)
            rows = cur.execute(sql_text).fetchall()
            data_rows = [{k: _json_safe(v) for k, v in row.items()} for row in rows]
            answer_parts.append(f"Returned {len(data_rows)} row(s).")
    finally:
        conn.close()

    return ChatResponse(
        answer="\n\n".join(answer_parts) or "Cortex Analyst returned no answer.",
        sql=sql_text,
        data=data_rows,
        suggestions=suggestions or None,
    )


class ChatRequest(BaseModel):
    question: str
    conversation_history: Optional[list] = None
    agent: Optional[str] = None
    # Optional, for saved history: the question as the user typed it (question may carry
    # appended dashboard context), plus who/where it was asked.
    display_question: Optional[str] = None
    user_id: Optional[str] = None
    conversation_id: Optional[str] = None
    dashboard: Optional[str] = None


class ChatResponse(BaseModel):
    answer: str
    sql: Optional[str] = None
    data: Optional[list] = None
    chart_spec: Optional[str] = None
    suggestions: Optional[list] = None


@app.post("/api/chat", response_model=ChatResponse)
def chat(req: ChatRequest, background_tasks: BackgroundTasks):
    result = _answer(req)
    if CHAT_HISTORY_DAYS > 0 and req.user_id and req.conversation_id:
        # Runs after the response is sent; a failure is logged and never reaches the user.
        background_tasks.add_task(_save_history, req, result)
    return result


def _save_history(req: ChatRequest, result: ChatResponse):
    try:
        conn, _, _ = _open_session(_profile(req.agent))
        try:
            conn.cursor().execute(
                f"INSERT INTO {CHAT_HISTORY_TABLE} (USER_ID, CONVERSATION_ID, AGENT_ID, DASHBOARD, QUESTION,"
                " ANSWER, SQL_TEXT, RESULT_DATA, CHART_SPEC)"
                " SELECT %s, %s, %s, %s, %s, %s, %s, PARSE_JSON(%s), PARSE_JSON(%s)",
                (
                    req.user_id, req.conversation_id, _profile(req.agent)["id"], req.dashboard,
                    req.display_question or req.question, result.answer, result.sql,
                    json.dumps(result.data[:CHAT_HISTORY_MAX_ROWS], default=str) if result.data else None,
                    result.chart_spec,
                ),
            )
        finally:
            conn.close()
    except Exception as e:
        logger.error(f"Saving chat history failed: {e}")


def _parse_variant(value):
    return json.loads(value) if isinstance(value, str) else value


def _query_history(agent: Optional[str], sql: str, params: tuple) -> list:
    if CHAT_HISTORY_DAYS <= 0:
        raise HTTPException(status_code=404, detail="Chat history is disabled (CHAT_HISTORY_DAYS=0)")
    conn, _, _ = _open_session(_profile(agent))
    try:
        cur = conn.cursor(snowflake.connector.DictCursor)
        return cur.execute(sql, params).fetchall()
    finally:
        conn.close()


@app.get("/api/history/conversations")
def history_conversations(user_id: str, agent: Optional[str] = None):
    """The user's conversations with this agent from the last CHAT_HISTORY_DAYS days, newest first."""
    rows = _query_history(agent, (
        "SELECT CONVERSATION_ID, MIN_BY(QUESTION, CREATED_AT) AS TITLE, MIN(CREATED_AT) AS STARTED_AT,"
        " MAX(CREATED_AT) AS LAST_AT, COUNT(*) AS QUESTIONS"
        f" FROM {CHAT_HISTORY_TABLE}"
        " WHERE USER_ID = %s AND AGENT_ID = %s AND CREATED_AT >= DATEADD(day, -%s, CURRENT_TIMESTAMP())"
        " GROUP BY CONVERSATION_ID ORDER BY LAST_AT DESC LIMIT 50"
    ), (user_id, _profile(agent)["id"], CHAT_HISTORY_DAYS))
    return {"conversations": [{
        "conversation_id": r["CONVERSATION_ID"],
        "title": r["TITLE"],
        "started_at": _json_safe(r["STARTED_AT"]),
        "last_at": _json_safe(r["LAST_AT"]),
        "questions": r["QUESTIONS"],
    } for r in rows]}


@app.get("/api/history/messages")
def history_messages(user_id: str, conversation_id: str, agent: Optional[str] = None):
    rows = _query_history(agent, (
        "SELECT QUESTION, ANSWER, SQL_TEXT, RESULT_DATA, CHART_SPEC, CREATED_AT"
        f" FROM {CHAT_HISTORY_TABLE}"
        " WHERE USER_ID = %s AND CONVERSATION_ID = %s AND CREATED_AT >= DATEADD(day, -%s, CURRENT_TIMESTAMP())"
        " ORDER BY CREATED_AT"
    ), (user_id, conversation_id, CHAT_HISTORY_DAYS))
    return {"messages": [{
        "question": r["QUESTION"],
        "answer": r["ANSWER"],
        "sql": r["SQL_TEXT"],
        "data": _parse_variant(r["RESULT_DATA"]),
        "chart_spec": r["CHART_SPEC"] if isinstance(r["CHART_SPEC"], str) or r["CHART_SPEC"] is None
        else json.dumps(r["CHART_SPEC"]),
        "created_at": _json_safe(r["CREATED_AT"]),
    } for r in rows]}


def _answer(req: ChatRequest) -> ChatResponse:
    messages = []
    if req.conversation_history:
        messages.extend(req.conversation_history)
    messages.append({
        "role": "user",
        "content": [{"type": "text", "text": req.question}],
    })
    profile = _profile(req.agent)

    if USE_CORTEX_ANALYST:
        try:
            return _chat_via_analyst(messages, profile)
        except HTTPException:
            raise
        except Exception as e:
            logger.error(f"Analyst call failed: {e}")
            raise HTTPException(status_code=500, detail=str(e))

    try:
        body = _call_agent(messages, profile)
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Agent call failed: {e}")
        raise HTTPException(status_code=500, detail=str(e))

    logger.info(f"Agent response keys: {list(body.keys())}")
    logger.info(f"Agent response: {json.dumps(body, default=str)[:3000]}")

    answer_text = ""
    sql_text = None
    data_rows = None
    chart_spec = None
    suggestions = []

    for item in body.get("content", []):
        item_type = item.get("type", "")

        if item_type == "text":
            answer_text += item.get("text", "")

        elif item_type == "suggested_queries":
            suggestions.extend(q["query"] for q in item.get("suggested_queries", []) if q.get("query"))

        elif item_type == "tool_result":
            tr = item.get("tool_result", {})
            for c in tr.get("content", []):
                if c.get("type") == "json":
                    j = c.get("json", {})
                    if "sql" in j:
                        sql_text = j["sql"]
                    if "text" in j and not answer_text:
                        answer_text = j["text"]
                    rs = j.get("result_set", {})
                    if rs.get("data"):
                        cols = [
                            col["name"]
                            for col in rs.get("resultSetMetaData", {}).get("rowType", [])
                        ]
                        data_rows = [dict(zip(cols, row)) for row in rs["data"]]

        elif item_type == "chart":
            chart_info = item.get("chart", {})
            raw_spec = chart_info.get("chart_spec")
            if raw_spec:
                chart_spec = raw_spec if isinstance(raw_spec, str) else json.dumps(raw_spec)
                logger.info(f"Chart spec (first 500): {chart_spec[:500]}")

        elif item_type == "table":
            rs = item.get("result_set", {})
            if rs.get("data") and not data_rows:
                cols = [
                    col["name"]
                    for col in rs.get("resultSetMetaData", {}).get("rowType", [])
                ]
                data_rows = [dict(zip(cols, row)) for row in rs["data"]]

    if not answer_text:
        answer_text = "I received a response but couldn't extract a text answer."

    return ChatResponse(
        answer=answer_text,
        sql=sql_text,
        data=data_rows,
        chart_spec=chart_spec,
        suggestions=suggestions or None,
    )


@app.get("/api/health")
def health():
    return {
        "status": "ok",
        "agents": {pid: f"{p['database']}.{p['schema']}.{p['agent_name']}" for pid, p in AGENT_PROFILES.items()},
        "default_agent": DEFAULT_AGENT,
        "mode": "cortex_analyst" if USE_CORTEX_ANALYST else "cortex_agent",
        "running_in_spcs": _is_running_in_spcs(),
    }


@app.get("/api/agents")
def agents():
    return {
        "default": DEFAULT_AGENT,
        "history_days": CHAT_HISTORY_DAYS,
        "agents": [{"id": p["id"], "label": p["label"], "suggestions": p["suggestions"]} for p in AGENT_PROFILES.values()],
    }


@app.post("/api/debug")
def debug(req: ChatRequest):
    messages = [{"role": "user", "content": [{"type": "text", "text": req.question}]}]
    try:
        body = _call_agent(messages, _profile(req.agent))
        return {"status_code": 200, "body": body}
    except HTTPException as e:
        return {"status_code": e.status_code, "body": e.detail}
    except Exception as e:
        return {"status_code": 500, "body": str(e)}


app.mount("/", StaticFiles(directory=FRONTEND_DIR, html=True), name="frontend")
