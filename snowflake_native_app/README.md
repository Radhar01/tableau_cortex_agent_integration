# Cortex Chat

Chat with Snowflake Cortex Agents from inside Snowflake. Pick an agent in the sidebar, ask a
question in plain English, and get an answer with the SQL, a data table, a chart when the agent
returns one, and suggested follow-up questions.

## Setup after installing

The app needs read access to each agent and the data behind it. The `deploy.py` script in the
source folder does this for the agents listed in `.env`. To add an agent by hand, as the app owner:

```sql
GRANT USAGE ON DATABASE <db> TO APPLICATION CORTEX_CHAT_APP;
GRANT USAGE ON SCHEMA <db>.<schema> TO APPLICATION CORTEX_CHAT_APP;
GRANT SELECT ON ALL TABLES IN SCHEMA <db>.<schema> TO APPLICATION CORTEX_CHAT_APP;
GRANT SELECT ON SEMANTIC VIEW <db>.<schema>.<semantic_view> TO APPLICATION CORTEX_CHAT_APP;
GRANT USAGE ON AGENT <db>.<schema>.<agent> TO APPLICATION CORTEX_CHAT_APP;

CALL CORTEX_CHAT_APP.core.register_agent('MYAGENT', 'My Agent', '<db>.<schema>.<agent>',
                                         'First question?|Second question?', 3);
```

To let other roles open the app:

```sql
GRANT APPLICATION ROLE CORTEX_CHAT_APP.app_public TO ROLE <role>;
```
