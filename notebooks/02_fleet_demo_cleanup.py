# Databricks notebook source
# MAGIC %md
# MAGIC # Fleet Performance: demo cleanup
# MAGIC
# MAGIC Removes everything the setup notebook created: the two demo Genie Agents and the demo schema with its tables and views. Use the same `catalog` and `schema` values you used for setup. Set `confirm` to `yes` before running.

# COMMAND ----------

dbutils.widgets.text("catalog", "", "1. Catalog")
dbutils.widgets.text("schema", "fleet_genie_demo", "2. Schema")
dbutils.widgets.dropdown("confirm", "no", ["no", "yes"], "3. Delete the demo agents and schema?")

CATALOG = dbutils.widgets.get("catalog").strip()
SCHEMA = dbutils.widgets.get("schema").strip() or "fleet_genie_demo"
assert CATALOG, "Set the 'catalog' widget."
assert dbutils.widgets.get("confirm") == "yes", "Set the 'confirm' widget to 'yes' to delete the demo."
S = f"{CATALOG}.{SCHEMA}"

from databricks.sdk import WorkspaceClient
w = WorkspaceClient()

agents = spark.table(f"{S}._demo_agents").collect() if spark.catalog.tableExists(f"{S}._demo_agents") else []
for a in agents:
    try:
        w.api_client.do("DELETE", f"/api/2.0/genie/spaces/{a.space_id}")
        print(f"Deleted agent {a.title} ({a.space_id})")
    except Exception as e:
        print(f"Could not delete agent {a.title} ({a.space_id}): {e}")

spark.sql(f"DROP SCHEMA IF EXISTS {S} CASCADE")
print(f"Dropped schema {S}")
