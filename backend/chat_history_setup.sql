-- One-off setup for saved chat history (CHAT_HISTORY_DAYS in .env).
-- Run as the role the app uses (SNOWFLAKE_ROLE, default SYSADMIN). Works on trial accounts:
-- a standard table plus a daily task on the existing warehouse. The app itself only ever
-- reads the last CHAT_HISTORY_DAYS days, so the purge task is a storage backstop.

CREATE SCHEMA IF NOT EXISTS PHARMA_DEMO.CHAT_APP;

CREATE TABLE IF NOT EXISTS PHARMA_DEMO.CHAT_APP.CHAT_HISTORY (
  MESSAGE_ID      STRING DEFAULT UUID_STRING(),
  USER_ID         STRING,         -- per-browser ID from the chat UI
  CONVERSATION_ID STRING,
  AGENT_ID        STRING,         -- e.g. PHARMA / SALES
  DASHBOARD       STRING,         -- Tableau dashboard name, when running as an extension
  QUESTION        STRING,
  ANSWER          STRING,
  SQL_TEXT        STRING,
  RESULT_DATA     VARIANT,        -- capped at the first 200 rows
  CHART_SPEC      VARIANT,
  CREATED_AT      TIMESTAMP_LTZ DEFAULT CURRENT_TIMESTAMP()
) DATA_RETENTION_TIME_IN_DAYS = 1;

CREATE TASK IF NOT EXISTS PHARMA_DEMO.CHAT_APP.PURGE_CHAT_HISTORY
  WAREHOUSE = COMPUTE_WH
  SCHEDULE = 'USING CRON 0 2 * * * UTC'
AS
  DELETE FROM PHARMA_DEMO.CHAT_APP.CHAT_HISTORY
  WHERE CREATED_AT < DATEADD(day, -7, CURRENT_TIMESTAMP());

ALTER TASK PHARMA_DEMO.CHAT_APP.PURGE_CHAT_HISTORY RESUME;

-- If the objects above were created as ACCOUNTADMIN instead, let the app's role use them
-- (run as ACCOUNTADMIN; replace SYSADMIN if SNOWFLAKE_ROLE is different):
-- GRANT USAGE ON SCHEMA PHARMA_DEMO.CHAT_APP TO ROLE SYSADMIN;
-- GRANT SELECT, INSERT ON TABLE PHARMA_DEMO.CHAT_APP.CHAT_HISTORY TO ROLE SYSADMIN;
