-- ============================================================================
-- Setup script for the Cortex Chat Native App. Runs on install and upgrade.
-- ============================================================================

CREATE APPLICATION ROLE IF NOT EXISTS app_public;

-- The agent list is kept across upgrades, so it lives in a non-versioned schema.
CREATE SCHEMA IF NOT EXISTS config;
GRANT USAGE ON SCHEMA config TO APPLICATION ROLE app_public;

CREATE TABLE IF NOT EXISTS config.agents (
    agent_id    STRING,    -- short id used in the app, e.g. PHARMA
    label       STRING,    -- name shown in the agent picker
    agent_fqn   STRING,    -- DATABASE.SCHEMA.AGENT_NAME in the consumer account
    suggestions ARRAY,     -- starter questions
    sort_order  INT
);
GRANT SELECT ON TABLE config.agents TO APPLICATION ROLE app_public;

CREATE OR ALTER VERSIONED SCHEMA core;
GRANT USAGE ON SCHEMA core TO APPLICATION ROLE app_public;

-- Add or update an agent. suggestions: questions separated by |
CREATE OR REPLACE PROCEDURE core.register_agent(
    agent_id STRING, label STRING, agent_fqn STRING, suggestions STRING, sort_order INT)
RETURNS STRING
LANGUAGE SQL
AS
$$
BEGIN
    MERGE INTO config.agents t
    USING (SELECT UPPER(:agent_id) AS id) s
    ON t.agent_id = s.id
    WHEN MATCHED THEN UPDATE SET
        label = :label, agent_fqn = :agent_fqn,
        suggestions = SPLIT(:suggestions, '|'), sort_order = :sort_order
    WHEN NOT MATCHED THEN INSERT (agent_id, label, agent_fqn, suggestions, sort_order)
        VALUES (s.id, :label, :agent_fqn, SPLIT(:suggestions, '|'), :sort_order);
    RETURN 'Registered agent ' || UPPER(:agent_id);
END;
$$;

CREATE OR REPLACE PROCEDURE core.remove_agent(agent_id STRING)
RETURNS STRING
LANGUAGE SQL
AS
$$
BEGIN
    DELETE FROM config.agents WHERE agent_id = UPPER(:agent_id);
    RETURN 'Removed agent ' || UPPER(:agent_id);
END;
$$;

-- Ask an agent a question. history: earlier turns as [{role, content:[{type:'text', text}]}].
-- Returns {answer, sql, data, chart_spec, suggestions}.
CREATE OR REPLACE PROCEDURE core.ask(agent_id STRING, question STRING, history ARRAY)
RETURNS VARIANT
LANGUAGE PYTHON
RUNTIME_VERSION = '3.11'
PACKAGES = ('snowflake-snowpark-python')
HANDLER = 'ask'
AS
$$
import json

MAX_ROWS = 500


def _rows(result_set):
    cols = [c["name"] for c in result_set.get("resultSetMetaData", {}).get("rowType", [])]
    return [dict(zip(cols, row)) for row in result_set.get("data", [])[:MAX_ROWS]]


def ask(session, agent_id, question, history):
    found = session.sql("SELECT agent_fqn FROM config.agents WHERE agent_id = ?",
                        params=[(agent_id or "").upper()]).collect()
    if not found:
        return {"error": f"Unknown agent '{agent_id}'. Register it with core.register_agent."}
    messages = list(history or []) + [{"role": "user", "content": [{"type": "text", "text": question}]}]
    raw = session.sql("SELECT SNOWFLAKE.CORTEX.DATA_AGENT_RUN(?, ?)",
                      params=[found[0][0], json.dumps({"messages": messages})]).collect()[0][0]
    body = json.loads(raw) if isinstance(raw, str) else raw

    answer, sql, data, chart, suggestions = "", None, None, None, []
    for item in body.get("content", []):
        kind = item.get("type")
        if kind == "text":
            answer += item.get("text", "")
        elif kind == "tool_result":
            for part in item.get("tool_result", {}).get("content", []):
                if part.get("type") == "json":
                    j = part.get("json", {})
                    sql = j.get("sql", sql)
                    if j.get("result_set", {}).get("data"):
                        data = _rows(j["result_set"])
        elif kind == "table" and not data:
            rs = item.get("table", {}).get("result_set") or item.get("result_set", {})
            if rs.get("data"):
                data = _rows(rs)
        elif kind == "chart":
            spec = item.get("chart", {}).get("chart_spec")
            if spec:
                chart = spec if isinstance(spec, str) else json.dumps(spec)
        elif kind == "suggested_queries":
            suggestions += [q["query"] for q in item.get("suggested_queries", []) if q.get("query")]

    return {
        "answer": answer or "The agent returned no text answer.",
        "sql": sql,
        "data": data,
        "chart_spec": chart,
        "suggestions": suggestions,
    }
$$;

GRANT USAGE ON PROCEDURE core.ask(STRING, STRING, ARRAY) TO APPLICATION ROLE app_public;
GRANT USAGE ON PROCEDURE core.register_agent(STRING, STRING, STRING, STRING, INT) TO APPLICATION ROLE app_public;
GRANT USAGE ON PROCEDURE core.remove_agent(STRING) TO APPLICATION ROLE app_public;

CREATE OR REPLACE STREAMLIT core.cortex_chat
    FROM '/streamlit'
    MAIN_FILE = '/chat_app.py';
GRANT USAGE ON STREAMLIT core.cortex_chat TO APPLICATION ROLE app_public;
