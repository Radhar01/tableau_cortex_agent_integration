"""Cortex Chat: Streamlit front end for the Native App (port of static/index.html)."""
import json

import pandas as pd
import streamlit as st
from snowflake.snowpark.context import get_active_session

st.set_page_config(page_title="Cortex Chat", layout="wide")
session = get_active_session()


@st.cache_data(ttl=300)
def load_agents():
    rows = session.sql(
        "SELECT agent_id, label, suggestions FROM config.agents ORDER BY sort_order, agent_id"
    ).collect()
    return [
        {
            "id": r["AGENT_ID"],
            "label": r["LABEL"],
            "suggestions": [s for s in json.loads(r["SUGGESTIONS"] or "[]") if s],
        }
        for r in rows
    ]


agents = load_agents()
if not agents:
    st.warning("No agents are configured yet. As the app owner, run:\n\n"
               "`CALL CORTEX_CHAT_APP.core.register_agent('SALES', 'Sales Analytics', "
               "'DB.SCHEMA.AGENT', 'question 1|question 2', 1);`")
    st.stop()

by_id = {a["id"]: a for a in agents}
with st.sidebar:
    agent_id = st.selectbox("Agent", list(by_id), format_func=lambda i: by_id[i]["label"])
    if st.button("New conversation", use_container_width=True):
        st.session_state.setdefault("chats", {})[agent_id] = []
    st.caption("Each agent keeps its own conversation.")
agent = by_id[agent_id]
chat = st.session_state.setdefault("chats", {}).setdefault(agent_id, [])

st.title(f"{agent['label']} Agent")
st.caption("Powered by Snowflake Cortex")


def ask(question):
    history = [{"role": m["role"], "content": [{"type": "text", "text": m["text"]}]} for m in chat]
    with st.spinner("Thinking..."):
        result = session.call("core.ask", agent_id, question, history)
    result = json.loads(result) if isinstance(result, str) else result
    chat.append({"role": "user", "text": question})
    chat.append({"role": "assistant", "text": result.get("error") or result.get("answer", ""), "result": result})


def queue(question):
    st.session_state["pending"] = question


def render_result(result, key):
    if result.get("chart_spec"):
        try:
            st.vega_lite_chart(json.loads(result["chart_spec"]), use_container_width=True)
        except Exception as exc:  # a bad chart spec should not hide the answer
            st.caption(f"Chart could not be drawn: {exc}")
    if result.get("data"):
        st.dataframe(pd.DataFrame(result["data"]), use_container_width=True, hide_index=True)
    if result.get("sql"):
        with st.expander("SQL query"):
            st.code(result["sql"], language="sql")


for i, msg in enumerate(chat):
    with st.chat_message(msg["role"]):
        st.markdown(msg["text"])
        if msg.get("result"):
            render_result(msg["result"], i)

asked = {m["text"] for m in chat if m["role"] == "user"}
if not chat:
    starters = agent["suggestions"]
    label = "Try asking"
else:
    last = chat[-1].get("result", {})
    starters = [q for q in (last.get("suggestions") or []) if q not in asked] or \
               [q for q in agent["suggestions"] if q not in asked]
    label = "Suggested follow-ups"
if starters:
    st.caption(label)
    for j, q in enumerate(starters[:3]):
        st.button(q, key=f"sugg-{agent_id}-{len(chat)}-{j}", on_click=queue, args=(q,))

typed = st.chat_input("Ask a question about your data...")
question = typed or st.session_state.pop("pending", None)
if question:
    ask(question)
    st.rerun()
