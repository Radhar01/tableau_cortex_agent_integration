"""Deploy the Cortex Chat Native App to the Snowflake account configured in ../.env.

Creates (or upgrades) application package CORTEX_CHAT_PKG and application CORTEX_CHAT_APP,
grants the app access to every agent listed in AGENTS, and registers those agents in the app.

Usage: python deploy.py [--test "question"]
"""
import argparse
import json
import os
from pathlib import Path

import snowflake.connector
from dotenv import load_dotenv

HERE = Path(__file__).resolve().parent
PACKAGE, APP = "CORTEX_CHAT_PKG", "CORTEX_CHAT_APP"
STAGE = f"{PACKAGE}.STAGE_CONTENT.APP_CODE"
VERSION_DIR = "v1"
FILES = ["manifest.yml", "setup_script.sql", "README.md", "streamlit/chat_app.py", "streamlit/environment.yml"]


def agent_profiles():
    ids = [i.strip().upper() for i in os.getenv("AGENTS", "").split(",") if i.strip()]
    for order, pid in enumerate(ids, start=1):
        db, schema = os.environ[f"{pid}_DATABASE"], os.environ[f"{pid}_SCHEMA"]
        yield {
            "id": pid,
            "order": order,
            "label": os.getenv(f"{pid}_LABEL", pid.title()),
            "database": db,
            "schema": schema,
            "agent": f"{db}.{schema}.{os.environ[f'{pid}_AGENT_NAME']}",
            "semantic_view": os.getenv(f"{pid}_SEMANTIC_VIEW", ""),
            "warehouse": os.getenv(f"{pid}_WAREHOUSE", os.getenv("SNOWFLAKE_WAREHOUSE", "COMPUTE_WH")),
            "suggestions": os.getenv(f"{pid}_SUGGESTIONS", ""),
        }


def run(cur, sql, params=None, quiet=False):
    try:
        cur.execute(sql, params)
        if not quiet:
            print(f"  ok    {sql.splitlines()[0][:110]}")
        return True
    except snowflake.connector.errors.ProgrammingError as e:
        print(f"  FAIL  {sql.splitlines()[0][:110]}\n        {str(e).splitlines()[-1][:200]}")
        return False


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--test", help="ask each agent this question after deploying")
    args = ap.parse_args()
    load_dotenv(HERE.parent / ".env")

    conn = snowflake.connector.connect(
        account=os.environ["SNOWFLAKE_ACCOUNT"], user=os.environ["SNOWFLAKE_USER"],
        password=os.environ["SNOWFLAKE_PASSWORD"], role="ACCOUNTADMIN",
        warehouse=os.getenv("SNOWFLAKE_WAREHOUSE", "COMPUTE_WH"))
    cur = conn.cursor()

    print("1. Application package and stage")
    run(cur, f"CREATE APPLICATION PACKAGE IF NOT EXISTS {PACKAGE}")
    run(cur, f"CREATE SCHEMA IF NOT EXISTS {PACKAGE}.STAGE_CONTENT")
    run(cur, f"CREATE STAGE IF NOT EXISTS {STAGE} FILE_FORMAT = (TYPE = 'CSV' FIELD_DELIMITER = NONE)")

    print("2. Upload app files")
    for rel in FILES:
        src = (HERE / rel).as_posix()
        sub = f"/{os.path.dirname(rel)}" if os.path.dirname(rel) else ""
        run(cur, f"PUT 'file://{src}' @{STAGE}/{VERSION_DIR}{sub} AUTO_COMPRESS = FALSE OVERWRITE = TRUE")

    print("3. Install or upgrade the application")
    cur.execute(f"SHOW APPLICATIONS LIKE '{APP}'")
    if cur.fetchall():
        ok = run(cur, f"ALTER APPLICATION {APP} UPGRADE USING '@{STAGE}/{VERSION_DIR}'")
    else:
        ok = run(cur, f"CREATE APPLICATION {APP} FROM APPLICATION PACKAGE {PACKAGE} USING '@{STAGE}/{VERSION_DIR}'")
    if not ok:
        raise SystemExit("Install failed; fix the error above and re-run.")

    print("4. Grant the app access to Cortex and each agent")
    run(cur, f"GRANT DATABASE ROLE SNOWFLAKE.CORTEX_USER TO APPLICATION {APP}")
    run(cur, f"GRANT DATABASE ROLE SNOWFLAKE.CORTEX_AGENT_USER TO APPLICATION {APP}")
    profiles = list(agent_profiles())
    for p in profiles:
        print(f"  -- {p['id']}")
        run(cur, f"GRANT USAGE ON WAREHOUSE {p['warehouse']} TO APPLICATION {APP}")
        run(cur, f"GRANT USAGE ON DATABASE {p['database']} TO APPLICATION {APP}")
        run(cur, f"GRANT USAGE ON SCHEMA {p['database']}.{p['schema']} TO APPLICATION {APP}")
        run(cur, f"GRANT SELECT ON ALL TABLES IN SCHEMA {p['database']}.{p['schema']} TO APPLICATION {APP}")
        if p["semantic_view"]:
            run(cur, f"GRANT SELECT ON SEMANTIC VIEW {p['semantic_view']} TO APPLICATION {APP}")
        run(cur, f"GRANT USAGE ON AGENT {p['agent']} TO APPLICATION {APP}")

    print("5. Register agents in the app")
    for p in profiles:
        run(cur, f"CALL {APP}.core.register_agent(%s, %s, %s, %s, %s)",
            (p["id"], p["label"], p["agent"], p["suggestions"], p["order"]))

    if args.test:
        print("6. Test")
        for p in profiles:
            cur.execute(f"CALL {APP}.core.ask(%s, %s, ARRAY_CONSTRUCT())", (p["id"], args.test))
            res = json.loads(cur.fetchone()[0])
            if res.get("error"):
                print(f"  {p['id']}: ERROR {res['error']}")
                continue
            print(f"  {p['id']}: rows={len(res.get('data') or [])} chart={bool(res.get('chart_spec'))} "
                  f"follow-ups={len(res.get('suggestions') or [])}")
            print("        " + res["answer"][:220].replace("\n", " "))

    print(f"\nDone. Open it in Snowsight: Data Products > Apps > {APP}")
    conn.close()


if __name__ == "__main__":
    main()
