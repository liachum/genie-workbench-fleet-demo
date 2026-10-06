# Databricks notebook source
# MAGIC %md
# MAGIC # Fleet Performance: Genie Workbench demo setup
# MAGIC
# MAGIC This notebook builds a self-contained demo for Genie Workbench in your workspace. It creates:
# MAGIC
# MAGIC 1. A schema of **synthetic** fleet data: about 245 vessels (containerships and PCTCs), 151,000 daily noon reports, charters, off-hire events and CII ratings. All vessel names, charterers and figures are fictional.
# MAGIC 2. Five raw "platinum" tables (`plt_*`) without descriptions, and five governed views with full Unity Catalog descriptions.
# MAGIC 3. Two Genie Agents:
# MAGIC    - **Fleet Performance (as-built)** points at the raw tables, the way an agent is often built quickly.
# MAGIC    - **Fleet Performance (governed)** points at the governed views and includes instructions, joins, SQL measures and filters, example SQL and 25 benchmark questions.
# MAGIC 4. Optionally, the grants that the Genie Workbench app needs to scan and optimize both agents.
# MAGIC
# MAGIC **Requirements:** serverless compute (or any Unity Catalog-enabled cluster), `USE CATALOG` and `CREATE SCHEMA` on the target catalog, a SQL warehouse you can use, and permission to create Genie Agents. Running it takes about two minutes.
# MAGIC
# MAGIC Fill in the widgets at the top, then choose **Run all**. Rerunning the notebook rebuilds the data and updates the same two agents in place.

# COMMAND ----------

dbutils.widgets.text("catalog", "", "1. Catalog (required)")
dbutils.widgets.text("schema", "fleet_genie_demo", "2. Schema")
dbutils.widgets.text("warehouse_id", "", "3. SQL warehouse ID (blank = pick one)")
dbutils.widgets.text("agents_folder", "", "4. Workspace folder for agents (blank = your home)")
dbutils.widgets.text("workbench_app_name", "genie-workbench", "5. Genie Workbench app name (blank = skip grants)")

CATALOG = dbutils.widgets.get("catalog").strip()
SCHEMA = dbutils.widgets.get("schema").strip() or "fleet_genie_demo"
assert CATALOG, "Set the 'catalog' widget to a catalog where you can create a schema."
S = f"{CATALOG}.{SCHEMA}"

from databricks.sdk import WorkspaceClient
w = WorkspaceClient()
ME = w.current_user.me().user_name

WAREHOUSE_ID = dbutils.widgets.get("warehouse_id").strip()
if not WAREHOUSE_ID:
    whs = list(w.warehouses.list())
    whs.sort(key=lambda x: (not bool(x.enable_serverless_compute), str(x.state) != "State.RUNNING"))
    assert whs, "No SQL warehouse found; set the 'warehouse_id' widget."
    WAREHOUSE_ID = whs[0].id
AGENTS_FOLDER = dbutils.widgets.get("agents_folder").strip() or f"/Workspace/Users/{ME}/fleet-genie-demo"
APP_NAME = dbutils.widgets.get("workbench_app_name").strip()
print(f"Schema: {S}\nWarehouse: {WAREHOUSE_ID}\nAgents folder: {AGENTS_FOLDER}\nWorkbench app: {APP_NAME or '(skipped)'}")

# COMMAND ----------

# MAGIC %md
# MAGIC ## 1. Generate the synthetic fleet data
# MAGIC The generator is seeded, so every run produces the same data and the same answers.

# COMMAND ----------

import json
import random
from datetime import date, datetime, timedelta

random.seed(2026)
START, END = date(2025, 1, 1), date(2026, 9, 30)

WORDS = """Aurora Borealis Cascade Coral Delta Ember Fraser Glacier Harbour Horizon Island Juniper
Kelp Lumen Maple Nimbus Orca Pacific Quartz Raven Sequoia Tidal Umber Vantage Willow Yukon Zephyr
Alder Beacon Cedar Dawn Eagle Falcon Granite Heron Inlet Jade Kestrel Lagoon Meridian Nova Osprey
Pinnacle Quill Ridge Salish Thunder Unity Valiant Whistler Spirit Summit Voyager Pioneer Endeavour
Resolute Sentinel Tempest Monarch Regent Sovereign Majestic Orion Polaris Sirius Vega Altair Lyra""".split()
SUFFIX = ["Express", "Star", "Trader", "Spirit", "Venture", "Pride", "Bridge", "Crest", "Wave", "Dawn"]

CLASSES = [  # (design_class, capacity, dwt, count, build_years, shipyard, fuel_options)
    ("CS-2500", 2500, 34000, 18, (2007, 2011), "Jiangsu Yangzijiang", ["VLSFO"]),
    ("CS-4250", 4250, 50000, 34, (2006, 2010), "Samsung Heavy Industries", ["VLSFO", "VLSFO+SCRUBBER"]),
    ("CS-8500", 8500, 99000, 26, (2009, 2012), "Hyundai Heavy Industries", ["VLSFO+SCRUBBER"]),
    ("CS-10000", 10000, 114000, 40, (2014, 2018), "Jiangsu Yangzijiang", ["VLSFO+SCRUBBER", "VLSFO"]),
    ("CS-11000", 11000, 120000, 22, (2016, 2019), "Hanjin Subic", ["VLSFO+SCRUBBER"]),
    ("CS-12000", 12000, 140000, 25, (2019, 2022), "Hyundai Samho", ["VLSFO+SCRUBBER"]),
    ("CS-15000 LNG", 15000, 160000, 30, (2023, 2025), "Hyundai Heavy Industries", ["LNG_DUAL_FUEL"]),
    ("CS-24000", 24000, 228000, 30, (2021, 2024), "Samsung Heavy Industries", ["VLSFO+SCRUBBER", "LNG_DUAL_FUEL"]),
]
PCTC = ("PCTC-7000 LNG", 7000, 21000, 22, (2024, 2026), "China Merchants Heavy Industry", ["LNG_DUAL_FUEL"])
FLAGS = ["Hong Kong", "Marshall Islands", "Liberia", "Panama", "Singapore", "Malta"]
LINERS = ["Aurora Lines", "Kestrel Container Line", "Northstar Ocean Carriers", "Pacifica Shipping",
          "Harbourline Global", "Orient Bridge Lines"]
CAR_CARRIERS = ["AutoWave Logistics", "Velocity Car Carriers"]

used, vessels = set(), []
def name():
    while True:
        n = f"{random.choice(WORDS)} {random.choice(SUFFIX)}"
        if n not in used:
            used.add(n); return n

imo = 9400000
for cls, cap, dwt, cnt, yrs, yard, fuels in CLASSES + [PCTC]:
    for _ in range(cnt):
        imo += random.randint(7, 61)
        is_pctc = cls.startswith("PCTC")
        by = random.randint(*yrs)
        vessels.append(dict(
            imo_number=str(imo), vessel_name=name(),
            vessel_type="PCTC" if is_pctc else "CONTAINERSHIP", design_class=cls,
            capacity_teu="" if is_pctc else cap, capacity_ceu=cap if is_pctc else "",
            dwt=dwt + random.randint(-1500, 1500), build_year=by, shipyard=yard,
            flag_state=random.choice(FLAGS), fuel_system=random.choice(fuels),
            delivery_date=str(date(by, random.randint(1, 12), random.randint(1, 28))),
        ))
for v in vessels:
    d = date.fromisoformat(v["delivery_date"])
    v["first_report"] = max(d, START)
    v["fleet_status"] = "ACTIVE" if d <= END else "ON_ORDER"
vessels = [v for v in vessels if v["fleet_status"] == "ACTIVE"]

# Planted patterns: four hull-fouled CS-10000s and two CS-4250s with repeat main engine failures
foulers = random.sample([v for v in vessels if v["design_class"] == "CS-10000"], 4)
breakdowners = random.sample([v for v in vessels if v["design_class"] == "CS-4250"], 2)
for v in foulers: v["_fuel_mult"] = 1.17
for v in vessels: v.setdefault("_fuel_mult", 1.0)

def noise(prefix):
    return dict(etl_batch_id=f"{prefix}-{random.randint(100000, 999999)}",
                src_record_hash="%032x" % random.getrandbits(128),
                ingest_ts=datetime(2026, 10, 1, random.randint(0, 5), random.randint(0, 59)).isoformat(),
                load_dt="2026-10-01", audit_user="svc_adf_ingest")

rows = {}

# --- vessel master ---
VM_COLS = ["imo_number", "vessel_name", "vessel_type", "design_class", "capacity_teu", "capacity_ceu", "dwt",
           "build_year", "shipyard", "flag_state", "fuel_system", "delivery_date", "fleet_status",
           "etl_batch_id", "src_record_hash", "raw_vms_json", "ingest_ts", "load_dt", "audit_user"]
rows["vessel_master"] = (VM_COLS, [])
for v in vessels:
    row = {k: v[k] for k in VM_COLS if k in v}
    row.update(noise("VM")); row["raw_vms_json"] = json.dumps({"src": "VMS", "imo": v["imo_number"]})
    rows["vessel_master"][1].append(row)

# --- charter contracts ---
rate_base = {"CS-2500": 21000, "CS-4250": 29000, "CS-8500": 41000, "CS-10000": 46000,
             "CS-11000": 48500, "CS-12000": 52000, "CS-15000 LNG": 61000, "CS-24000": 68000,
             "PCTC-7000 LNG": 54000}
liner_premium = {"Aurora Lines": 1.00, "Kestrel Container Line": 1.04, "Northstar Ocean Carriers": 0.97,
                 "Pacifica Shipping": 1.09, "Harbourline Global": 0.99, "Orient Bridge Lines": 1.02,
                 "AutoWave Logistics": 1.00, "Velocity Car Carriers": 1.06}
charters, cid = [], 70000
for v in vessels:
    pool = CAR_CARRIERS if v["vessel_type"] == "PCTC" else LINERS
    s = date.fromisoformat(v["delivery_date"]) if v["vessel_type"] == "PCTC" else date(2022, 1, 1) + timedelta(days=random.randint(0, 900))
    while s <= date(2027, 6, 30):
        yrs = random.choice([3, 5, 7, 10, 12]) if v["vessel_type"] != "PCTC" or s == date.fromisoformat(v["delivery_date"]) else 5
        e = s + timedelta(days=365 * yrs)
        cid += 1
        ch = random.choice(pool)
        status = "ACTIVE" if s <= END <= e else ("EXPIRED" if e < END else "FIXED_FORWARD")
        rate = round(rate_base[v["design_class"]] * liner_premium[ch] * random.uniform(0.93, 1.07), -1)
        charters.append(dict(charter_id=f"CH-{cid}", imo_number=v["imo_number"], charterer_name=ch,
                             charter_type="TIME_CHARTER" if random.random() > 0.08 else "BAREBOAT",
                             start_date=str(s), end_date=str(e), daily_rate_usd=int(rate), contract_status=status))
        s = e + timedelta(days=1)
        if status == "ACTIVE" and random.random() < 0.7:
            break
CC_COLS = list(charters[0].keys()) + ["etl_batch_id", "src_record_hash", "ingest_ts", "load_dt", "audit_user"]
rows["charter_contracts"] = (CC_COLS, [])
for c in charters:
    r = dict(c); r.update(noise("CC")); rows["charter_contracts"][1].append(r)

# --- off-hire events ---
events, eid = [], 5000
reasons = [("ME_BREAKDOWN", 0.25), ("PSC_DETENTION", 0.12), ("CREW_CHANGE_DELAY", 0.18),
           ("WEATHER_DAMAGE", 0.10), ("AUX_MACHINERY", 0.35)]
def pick():
    x, acc = random.random(), 0
    for r, p in reasons:
        acc += p
        if x <= acc: return r
    return reasons[-1][0]
active_rate = {}
for c in charters:
    if c["contract_status"] in ("ACTIVE", "EXPIRED"):
        active_rate[c["imo_number"]] = c["daily_rate_usd"]
offhire_days = {}
for v in vessels:
    span_start = v["first_report"]
    span = (END - span_start).days
    if span < 30: continue
    evs = []
    age = 2026 - v["build_year"]
    if age in (5, 10, 15, 20) or (age > 15 and random.random() < 0.3):  # special survey drydock
        d = span_start + timedelta(days=random.randint(10, max(11, span - 30)))
        evs.append(("DRYDOCK", d, random.randint(14, 26) * 24))
    n = random.choice([0, 0, 1, 1, 2]) + (5 if v in breakdowners else 0)
    for _ in range(n):
        r = "ME_BREAKDOWN" if v in breakdowners else pick()
        d = span_start + timedelta(days=random.randint(0, span - 5))
        hrs = {"ME_BREAKDOWN": random.randint(18, 120), "PSC_DETENTION": random.randint(24, 96),
               "CREW_CHANGE_DELAY": random.randint(6, 36), "WEATHER_DAMAGE": random.randint(12, 72),
               "AUX_MACHINERY": random.randint(4, 30)}[r]
        evs.append((r, d, hrs))
    for r, d, hrs in evs:
        eid += 1
        st = datetime(d.year, d.month, d.day, random.randint(0, 23))
        en = st + timedelta(hours=hrs)
        rate = active_rate.get(v["imo_number"], 40000)
        events.append(dict(event_id=f"OH-{eid}", imo_number=v["imo_number"], start_ts=st.isoformat(),
                           end_ts=en.isoformat(), off_hire_hours=hrs, reason_code=r,
                           lost_hire_usd=int(rate * hrs / 24)))
        for k in range((en.date() - st.date()).days + 1):
            offhire_days.setdefault(v["imo_number"], set()).add(st.date() + timedelta(days=k))
OH_COLS = list(events[0].keys()) + ["etl_batch_id", "ingest_ts", "load_dt", "audit_user"]
rows["off_hire_events"] = (OH_COLS, [])
for e in events:
    r = dict(e); r.update(noise("OH")); r.pop("src_record_hash"); rows["off_hire_events"][1].append(r)

# --- noon reports ---
speed = {"CS-2500": 14.5, "CS-4250": 15.5, "CS-8500": 16.0, "CS-10000": 16.5, "CS-11000": 16.5,
         "CS-12000": 16.8, "CS-15000 LNG": 17.0, "CS-24000": 16.2, "PCTC-7000 LNG": 17.5}
fuel = {"CS-2500": 38, "CS-4250": 52, "CS-8500": 78, "CS-10000": 84, "CS-11000": 88,
        "CS-12000": 92, "CS-15000 LNG": 96, "CS-24000": 120, "PCTC-7000 LNG": 58}
CO2 = {"VLSFO": 3.151, "VLSFO+SCRUBBER": 3.114, "LNG_DUAL_FUEL": 2.750}
GRADE = {"VLSFO": "VLSFO", "VLSFO+SCRUBBER": "HSFO", "LNG_DUAL_FUEL": "LNG"}
NR_COLS = ["report_id", "imo_number", "report_date", "latitude", "longitude", "sog_kn", "distance_nm",
           "steaming_hours", "me_fuel_mt", "ae_fuel_mt", "fuel_grade", "co2_mt", "wind_bft", "draft_m",
           "voyage_leg", "raw_noon_json", "etl_batch_id", "ingest_ts", "record_hash", "load_dt", "audit_user",
           "vessel_key"]
rows["noon_reports"] = (NR_COLS, [])
rid = 0
for v in vessels:
    lat, lon = random.uniform(-30, 45), random.uniform(-170, 170)
    oh = offhire_days.get(v["imo_number"], set())
    d = v["first_report"]
    leg = 1
    while d <= END:
        rid += 1
        if d in oh:
            sog, hrs = 0.0, 0.0
        else:
            in_port = random.random() < 0.18
            sog = 0.0 if in_port else max(8.0, random.gauss(speed[v["design_class"]], 1.2))
            hrs = 0.0 if in_port else round(random.uniform(22.5, 24.0), 1)
            if in_port: leg += 1
        dist = round(sog * hrs, 1)
        me = 0.0 if hrs == 0 else round(fuel[v["design_class"]] * (sog / speed[v["design_class"]]) ** 3 * v["_fuel_mult"] * random.uniform(0.92, 1.08) * hrs / 24, 2)
        ae = round(random.uniform(3.0, 6.5), 2)
        co2 = round((me + ae) * CO2[v["fuel_system"]], 2)
        lat = max(-60, min(65, lat + random.uniform(-2, 2))); lon = ((lon + random.uniform(-4, 4) + 180) % 360) - 180
        rows["noon_reports"][1].append(dict(report_id=f"NR-{rid}", imo_number=v["imo_number"], report_date=str(d),
                        latitude=round(lat, 4), longitude=round(lon, 4), sog_kn=round(sog, 2), distance_nm=dist,
                        steaming_hours=hrs, me_fuel_mt=me, ae_fuel_mt=ae, fuel_grade=GRADE[v["fuel_system"]],
                        co2_mt=co2, wind_bft=random.choice([2, 3, 3, 4, 4, 5, 5, 6, 7]),
                        draft_m=round(random.uniform(10.5, 15.5), 2), voyage_leg=f"L{leg:03d}",
                        raw_noon_json=json.dumps({"form": "NOON-v4"}), etl_batch_id=f"NR-{d.isoformat()}",
                        ingest_ts=f"{d}T13:{random.randint(10, 59)}:00", record_hash="%032x" % random.getrandbits(128),
                        load_dt=str(d), audit_user="svc_adf_ingest", vessel_key=f"VK{v['imo_number']}"))
        d += timedelta(days=1)

# --- CII ratings ---
CII_COLS = ["imo_number", "rating_year", "attained_cii", "required_cii", "cii_rating", "etl_batch_id", "load_dt"]
rows["cii_ratings"] = (CII_COLS, [])
for v in vessels:
    for yr in (2024, 2025):
        if date.fromisoformat(v["delivery_date"]).year > yr: continue
        req = round(random.uniform(5.5, 9.5), 2)
        ratio = random.gauss(0.97, 0.07) * (1.18 if v in foulers else 1.0) * (1.06 if v["build_year"] < 2010 else 1.0)
        ratio *= 0.85 if v["fuel_system"] == "LNG_DUAL_FUEL" else 1.0
        att = round(req * ratio, 2)
        rating = "A" if ratio < 0.83 else "B" if ratio < 0.94 else "C" if ratio < 1.06 else "D" if ratio < 1.18 else "E"
        rows["cii_ratings"][1].append(dict(imo_number=v["imo_number"], rating_year=yr, attained_cii=att, required_cii=req,
                                           cii_rating=rating, etl_batch_id=f"CII-{yr}", load_dt="2026-01-15"))

print({k: len(v[1]) for k, v in rows.items()})

# COMMAND ----------

# MAGIC %md
# MAGIC ## 2. Write the raw platinum tables
# MAGIC These mimic tables that land straight from source systems: technical column names, ETL audit columns, a one-letter capacity unit code, and no descriptions.

# COMMAND ----------

from pyspark.sql.types import StructType, StructField, StringType

spark.sql(f"CREATE SCHEMA IF NOT EXISTS {S} COMMENT 'Synthetic fleet performance data for the Genie Workbench demo. All vessels, charterers and figures are fictional.'")

def stage(key, chunk=40000):
    cols, data = rows[key]
    schema = StructType([StructField(c, StringType(), True) for c in cols])
    def as_str(x):
        return None if x is None or x == "" else str(x)
    df = None
    for i in range(0, len(data), chunk):
        part = spark.createDataFrame([tuple(as_str(r.get(c)) for c in cols) for r in data[i:i + chunk]], schema)
        df = part if df is None else df.unionByName(part)
    df.createOrReplaceTempView(f"stg_{key}")

for key in rows:
    stage(key)

spark.sql(f"""CREATE OR REPLACE TABLE {S}.plt_vessel_master AS
SELECT CAST(imo_number AS STRING) imo_number, vessel_name, vessel_type, design_class,
  CAST(COALESCE(capacity_teu, capacity_ceu) AS INT) nominal_capacity,
  CASE WHEN capacity_ceu IS NOT NULL AND capacity_ceu <> '' THEN 'C' ELSE 'T' END cap_uom_cd, CAST(dwt AS INT) dwt,
  CAST(build_year AS INT) build_year, shipyard, flag_state, fuel_system, CAST(delivery_date AS DATE) delivery_date,
  fleet_status, etl_batch_id, src_record_hash, raw_vms_json, CAST(ingest_ts AS TIMESTAMP) ingest_ts,
  CAST(load_dt AS DATE) load_dt, audit_user
FROM stg_vessel_master""")
spark.sql(f"""CREATE OR REPLACE TABLE {S}.plt_charter_contracts AS
SELECT charter_id, CAST(imo_number AS STRING) imo_number, charterer_name, charter_type,
  CAST(start_date AS DATE) start_date, CAST(end_date AS DATE) end_date, CAST(daily_rate_usd AS DECIMAL(12,2)) daily_rate_usd,
  contract_status, etl_batch_id, src_record_hash, CAST(ingest_ts AS TIMESTAMP) ingest_ts, CAST(load_dt AS DATE) load_dt, audit_user
FROM stg_charter_contracts""")
spark.sql(f"""CREATE OR REPLACE TABLE {S}.plt_off_hire_events AS
SELECT event_id, CAST(imo_number AS STRING) imo_number, CAST(start_ts AS TIMESTAMP) start_ts, CAST(end_ts AS TIMESTAMP) end_ts,
  CAST(off_hire_hours AS INT) off_hire_hours, reason_code, CAST(lost_hire_usd AS DECIMAL(14,2)) lost_hire_usd,
  etl_batch_id, CAST(ingest_ts AS TIMESTAMP) ingest_ts, CAST(load_dt AS DATE) load_dt, audit_user
FROM stg_off_hire_events""")
spark.sql(f"""CREATE OR REPLACE TABLE {S}.plt_noon_reports AS
SELECT report_id, CAST(imo_number AS STRING) imo_number, CAST(report_date AS DATE) report_date,
  CAST(latitude AS DOUBLE) latitude, CAST(longitude AS DOUBLE) longitude, CAST(sog_kn AS DECIMAL(6,2)) sog_kn,
  CAST(distance_nm AS DECIMAL(8,1)) distance_nm, CAST(steaming_hours AS DECIMAL(4,1)) steaming_hours,
  CAST(me_fuel_mt AS DECIMAL(8,2)) me_fuel_mt, CAST(ae_fuel_mt AS DECIMAL(8,2)) ae_fuel_mt, fuel_grade,
  CAST(co2_mt AS DECIMAL(10,2)) co2_mt, CAST(wind_bft AS INT) wind_bft, CAST(draft_m AS DECIMAL(5,2)) draft_m, voyage_leg,
  raw_noon_json, etl_batch_id, CAST(ingest_ts AS TIMESTAMP) ingest_ts, record_hash, CAST(load_dt AS DATE) load_dt, audit_user, vessel_key
FROM stg_noon_reports""")
spark.sql(f"""CREATE OR REPLACE TABLE {S}.plt_cii_ratings AS
SELECT CAST(imo_number AS STRING) imo_number, CAST(rating_year AS INT) rating_year, CAST(attained_cii AS DECIMAL(6,2)) attained_cii,
  CAST(required_cii AS DECIMAL(6,2)) required_cii, cii_rating, etl_batch_id, CAST(load_dt AS DATE) load_dt
FROM stg_cii_ratings""")
display(spark.sql(f"SELECT vessel_type, cap_uom_cd, COUNT(*) vessels, SUM(nominal_capacity) capacity FROM {S}.plt_vessel_master GROUP BY ALL"))

# COMMAND ----------

# MAGIC %md
# MAGIC ## 3. Create the governed views
# MAGIC The governed layer gives Genie stable, described columns. Capacity is split into `capacity_teu` and `capacity_ceu` with an explicit `capacity_unit`, and ETL columns are not exposed.

# COMMAND ----------

spark.sql(f"""CREATE OR REPLACE VIEW {S}.fleet_vessels (
  imo_number COMMENT 'IMO number. Unique, permanent identifier of the vessel; join key to every other fleet view.',
  vessel_name COMMENT 'Current registered vessel name.',
  vessel_type COMMENT 'CONTAINERSHIP (capacity measured in TEU) or PCTC, a pure car and truck carrier (capacity measured in CEU).',
  design_class COMMENT 'Design class, for example CS-10000 or PCTC-7000 LNG. Vessels in the same class are sister ships and are the right peer group for performance comparisons.',
  capacity_value COMMENT 'Nominal carrying capacity in the unit given by capacity_unit (TEU for containerships, CEU for PCTCs). Never add TEU and CEU together.',
  capacity_unit COMMENT 'Unit of capacity_value: TEU (twenty-foot equivalent unit) for containerships, CEU (car equivalent unit) for PCTCs.',
  capacity_teu COMMENT 'Container capacity in TEU. NULL for PCTCs.',
  capacity_ceu COMMENT 'Vehicle capacity in CEU (car equivalent units). NULL for containerships.',
  dwt COMMENT 'Deadweight tonnage in metric tonnes.',
  build_year COMMENT 'Year the vessel was built.',
  vessel_age_years COMMENT 'Age in whole years as of 2026.',
  shipyard COMMENT 'Shipyard that built the vessel.',
  flag_state COMMENT 'Flag state of registry.',
  fuel_system COMMENT 'Propulsion fuel setup: VLSFO, VLSFO+SCRUBBER (burns HSFO with an exhaust scrubber) or LNG_DUAL_FUEL.',
  delivery_date COMMENT 'Date the vessel was delivered from the yard.')
COMMENT 'Certified fleet register: one row per vessel in the active fleet (synthetic demo data). Use this view for any question about vessel attributes, capacity, class, age or fuel system.'
AS SELECT imo_number, vessel_name, vessel_type, design_class,
  nominal_capacity AS capacity_value,
  CASE WHEN cap_uom_cd = 'C' THEN 'CEU' ELSE 'TEU' END AS capacity_unit,
  CASE WHEN cap_uom_cd = 'T' THEN nominal_capacity END AS capacity_teu,
  CASE WHEN cap_uom_cd = 'C' THEN nominal_capacity END AS capacity_ceu, dwt, build_year, 2026 - build_year AS vessel_age_years,
  shipyard, flag_state, fuel_system, delivery_date
FROM {S}.plt_vessel_master WHERE fleet_status = 'ACTIVE'""")

spark.sql(f"""CREATE OR REPLACE VIEW {S}.charter_book (
  charter_id COMMENT 'Unique charter contract identifier.',
  imo_number COMMENT 'IMO number of the chartered vessel; joins to fleet_vessels.',
  charterer_name COMMENT 'Liner company (containerships) or car carrier operator (PCTCs) that charters the vessel. Fictional names.',
  charter_type COMMENT 'TIME_CHARTER (the owner operates the vessel and earns a daily hire rate) or BAREBOAT (the charterer operates the vessel).',
  start_date COMMENT 'Charter start date.',
  end_date COMMENT 'Charter end date.',
  daily_rate_usd COMMENT 'Contracted daily charter hire rate in USD per day.',
  contract_status COMMENT 'ACTIVE (in force on 2026-09-30), EXPIRED, or FIXED_FORWARD (signed, starts in the future).',
  remaining_term_days COMMENT 'Days left on the charter as of 2026-09-30. Zero for expired charters.')
COMMENT 'Certified charter book: every charter contract on the fleet, past, current and forward-fixed. Use contract_status = ACTIVE for the current book.'
AS SELECT charter_id, imo_number, charterer_name, charter_type, start_date, end_date, daily_rate_usd, contract_status,
  GREATEST(DATEDIFF(end_date, DATE'2026-09-30'), 0) AS remaining_term_days
FROM {S}.plt_charter_contracts""")

spark.sql(f"""CREATE OR REPLACE VIEW {S}.vessel_daily_performance (
  imo_number COMMENT 'IMO number of the reporting vessel; joins to fleet_vessels.',
  report_date COMMENT 'Date of the noon report (one report per vessel per day).',
  latitude COMMENT 'Noon position latitude in decimal degrees.',
  longitude COMMENT 'Noon position longitude in decimal degrees.',
  speed_over_ground_kn COMMENT 'Average speed over ground in knots over the reporting day. Zero when in port or off hire.',
  distance_nm COMMENT 'Distance sailed in nautical miles over the reporting day.',
  steaming_hours COMMENT 'Hours under way during the reporting day.',
  main_engine_fuel_mt COMMENT 'Main engine fuel consumed in metric tonnes.',
  aux_engine_fuel_mt COMMENT 'Auxiliary engine (generator) fuel consumed in metric tonnes.',
  total_fuel_mt COMMENT 'Total fuel consumed in metric tonnes (main plus auxiliary).',
  fuel_grade COMMENT 'Fuel burned: VLSFO, HSFO (scrubber-fitted vessels) or LNG.',
  co2_mt COMMENT 'CO2 emitted in metric tonnes, from fuel consumed times the IMO emission factor of the grade.',
  wind_force_bft COMMENT 'Wind force on the Beaufort scale.',
  draft_m COMMENT 'Mean draft in metres.',
  voyage_leg COMMENT 'Voyage leg code; a new leg starts after each port call.',
  is_at_sea COMMENT 'TRUE when the vessel was under way for the day (steaming_hours > 0).')
COMMENT 'Certified daily vessel performance from captain noon reports (synthetic, Jan 2025 to Sep 2026). Use for speed, distance, fuel consumption and CO2 questions. Compare fuel only within the same design_class.'
AS SELECT imo_number, report_date, latitude, longitude, sog_kn AS speed_over_ground_kn, distance_nm, steaming_hours,
  me_fuel_mt AS main_engine_fuel_mt, ae_fuel_mt AS aux_engine_fuel_mt, me_fuel_mt + ae_fuel_mt AS total_fuel_mt,
  fuel_grade, co2_mt, wind_bft AS wind_force_bft, draft_m, voyage_leg, steaming_hours > 0 AS is_at_sea
FROM {S}.plt_noon_reports""")

spark.sql(f"""CREATE OR REPLACE VIEW {S}.vessel_off_hire (
  event_id COMMENT 'Unique off-hire event identifier.',
  imo_number COMMENT 'IMO number of the vessel that went off hire; joins to fleet_vessels.',
  start_ts COMMENT 'Time the vessel went off hire.',
  end_ts COMMENT 'Time the vessel came back on hire.',
  off_hire_hours COMMENT 'Duration of the off-hire period in hours.',
  off_hire_days COMMENT 'Duration of the off-hire period in days (hours / 24).',
  reason_code COMMENT 'Cause: DRYDOCK (planned special survey), ME_BREAKDOWN (main engine failure), AUX_MACHINERY, PSC_DETENTION (port state control detention), CREW_CHANGE_DELAY, WEATHER_DAMAGE.',
  is_planned COMMENT 'TRUE for planned off hire (DRYDOCK); FALSE for unplanned events.',
  lost_hire_usd COMMENT 'Charter hire revenue lost during the off-hire period, in USD.')
COMMENT 'Certified off-hire events: periods when a vessel was not earning hire. Unplanned off hire is every reason except DRYDOCK.'
AS SELECT event_id, imo_number, start_ts, end_ts, off_hire_hours, ROUND(off_hire_hours / 24.0, 2) AS off_hire_days,
  reason_code, reason_code = 'DRYDOCK' AS is_planned, lost_hire_usd
FROM {S}.plt_off_hire_events""")

spark.sql(f"""CREATE OR REPLACE VIEW {S}.vessel_cii_ratings (
  imo_number COMMENT 'IMO number of the vessel; joins to fleet_vessels.',
  rating_year COMMENT 'Calendar year the Carbon Intensity Indicator was assessed for.',
  attained_cii COMMENT 'Attained annual operational CII (grams CO2 per capacity-mile).',
  required_cii COMMENT 'Required CII for the vessel in that year under IMO MARPOL Annex VI.',
  cii_rating COMMENT 'IMO CII rating A (best) to E (worst). D for three consecutive years or E in any year requires a corrective action plan.',
  cii_ratio COMMENT 'attained_cii divided by required_cii. Above 1.0 means worse than required.')
COMMENT 'Certified IMO Carbon Intensity Indicator (CII) ratings per vessel per year (synthetic).'
AS SELECT imo_number, rating_year, attained_cii, required_cii, cii_rating, ROUND(attained_cii / required_cii, 3) AS cii_ratio
FROM {S}.plt_cii_ratings""")

display(spark.sql(f"SELECT capacity_unit, COUNT(*) vessels, SUM(capacity_value) capacity FROM {S}.fleet_vessels GROUP BY ALL"))

# COMMAND ----------

# MAGIC %md
# MAGIC ## 4. Define the two Genie Agents

# COMMAND ----------

import secrets

def new_id():
    return secrets.token_hex(16)

def sql_lines(sql):
    parts = sql.strip().splitlines()
    return [l + "\n" for l in parts[:-1]] + [parts[-1]]

def by_id(items):
    return sorted(items, key=lambda x: x["id"])

# ---------------------------------------------------------------- as-built agent
ASBUILT_TEXT = """## Fleet Agent instructions (copied from the Fleet, Vessel and Emissions agents)
- Tables: plt_vessel_master, plt_charter_contracts, plt_noon_reports, plt_off_hire_events, plt_cii_ratings.
- plt_vessel_master columns: imo_number, vessel_name, vessel_type, design_class, nominal_capacity, cap_uom_cd, dwt, build_year, shipyard, flag_state, fuel_system, delivery_date, fleet_status, etl_batch_id, src_record_hash, raw_vms_json, ingest_ts, load_dt, audit_user.
- plt_charter_contracts columns: charter_id, imo_number, charterer_name, charter_type, start_date, end_date, daily_rate_usd, contract_status, etl_batch_id, src_record_hash, ingest_ts, load_dt, audit_user.
- plt_noon_reports columns: report_id, imo_number, report_date, latitude, longitude, sog_kn, distance_nm, steaming_hours, me_fuel_mt, ae_fuel_mt, fuel_grade, co2_mt, wind_bft, draft_m, voyage_leg, raw_noon_json, etl_batch_id, ingest_ts, record_hash, load_dt, audit_user, vessel_key.
- plt_off_hire_events columns: event_id, imo_number, start_ts, end_ts, off_hire_hours, reason_code, lost_hire_usd, etl_batch_id, ingest_ts, load_dt, audit_user.
- plt_cii_ratings columns: imo_number, rating_year, attained_cii, required_cii, cii_rating, etl_batch_id, load_dt.
- Vessel capacity is nominal_capacity, in TEU.
- Fleet capacity (TEU): SELECT SUM(nominal_capacity) FROM plt_vessel_master WHERE fleet_status = 'ACTIVE'
- Fuel by vessel: SELECT v.vessel_name, SUM(n.me_fuel_mt) FROM plt_noon_reports n JOIN plt_vessel_master v ON n.imo_number = v.imo_number GROUP BY v.vessel_name
- Off hire: SELECT v.vessel_name, SUM(o.off_hire_hours) FROM plt_off_hire_events o JOIN plt_vessel_master v ON o.imo_number = v.imo_number GROUP BY v.vessel_name
- Charter revenue: SELECT c.charterer_name, SUM(c.daily_rate_usd) FROM plt_charter_contracts c GROUP BY c.charterer_name
- Use fleet_status = 'ACTIVE' for the fleet. Use contract_status = 'ACTIVE' for charters. sog_kn is speed. me_fuel_mt is main engine fuel. ae_fuel_mt is aux fuel. co2_mt is CO2. reason_code is the off hire reason. cii_rating is the CII rating.
- Repeat of Fleet agent notes: vessel_name is the ship name, imo_number is the IMO, design_class is the class, fuel_system is the fuel type, build_year is the build year, shipyard is the yard, flag_state is the flag.
- Repeat of Emissions agent notes: co2_mt is CO2 in tonnes, fuel_grade is the fuel grade, cii_rating is the CII rating, attained_cii is attained, required_cii is required.
- Repeat of Vessel agent notes: nominal_capacity is capacity in TEU, dwt is deadweight, delivery_date is delivery, fleet_status is status.
"""
PLT = ["plt_charter_contracts", "plt_cii_ratings", "plt_noon_reports", "plt_off_hire_events", "plt_vessel_master"]
asbuilt_tables = []
for t in PLT:
    cols = [f.name for f in spark.table(f"{S}.{t}").schema.fields]
    asbuilt_tables.append({"identifier": f"{S}.{t}",
                           "column_configs": sorted(({"column_name": c} for c in cols), key=lambda c: c["column_name"])})
ASBUILT = {
    "version": 2,
    "config": {"sample_questions": by_id([
        {"id": new_id(), "question": ["What is our total fleet capacity?"]},
        {"id": new_id(), "question": ["Which vessels had the most off hire?"]},
    ])},
    "data_sources": {"tables": sorted(asbuilt_tables, key=lambda t: t["identifier"])},
    "instructions": {"text_instructions": [{"id": new_id(), "content": [l + "\n" for l in ASBUILT_TEXT.strip().splitlines()]}]},
}

# ---------------------------------------------------------------- governed agent
def col(name, synonyms=None, match=False, fmt=True):
    c = {"column_name": name, "enable_format_assistance": fmt or match, "enable_entity_matching": match}
    if synonyms:
        c["synonyms"] = synonyms
    return c

gov_tables = [
    {"identifier": f"{S}.charter_book",
     "description": ["Certified charter book: every charter contract on the fleet, past, current and forward-fixed. Use contract_status = ACTIVE for the current book."],
     "column_configs": [col("charter_type", match=True), col("charterer_name", ["liner", "charterer", "customer"], match=True),
                        col("contract_status", match=True), col("daily_rate_usd", ["hire rate", "charter rate", "day rate"]),
                        col("end_date", ["expiry", "expiry date"]), col("start_date")]},
    {"identifier": f"{S}.fleet_vessels",
     "description": ["Certified fleet register: one row per active vessel. Use for vessel attributes, capacity (TEU for containerships, CEU for PCTCs), class, age and fuel system."],
     "column_configs": [col("capacity_ceu", ["CEU", "car capacity", "vehicle capacity"]),
                        col("capacity_teu", ["TEU", "container capacity", "box capacity"]),
                        col("capacity_unit", match=True), col("design_class", ["class", "vessel class", "sister ships"], match=True),
                        col("fuel_system", ["fuel type", "propulsion"], match=True), col("shipyard", ["yard"], match=True),
                        col("vessel_name", ["ship", "vessel", "ship name"], match=True),
                        col("vessel_type", ["ship type", "segment"], match=True)]},
    {"identifier": f"{S}.vessel_cii_ratings",
     "description": ["Certified IMO Carbon Intensity Indicator (CII) ratings per vessel per year, A (best) to E (worst)."],
     "column_configs": [col("cii_rating", ["carbon rating", "CII grade"], match=True), col("rating_year")]},
    {"identifier": f"{S}.vessel_daily_performance",
     "description": ["Certified daily vessel performance from captain noon reports: speed, distance, fuel and CO2. Compare fuel only within the same design_class and on days at sea."],
     "column_configs": [col("co2_mt", ["emissions", "carbon emissions"]), col("fuel_grade", match=True),
                        col("main_engine_fuel_mt", ["ME fuel", "main engine consumption", "bunker consumption"]),
                        col("report_date", ["noon report date", "date"]),
                        col("speed_over_ground_kn", ["speed", "SOG"])]},
    {"identifier": f"{S}.vessel_off_hire",
     "description": ["Certified off-hire events: periods when a vessel was not earning hire. Unplanned off hire is every reason except DRYDOCK."],
     "column_configs": [col("lost_hire_usd", ["lost revenue", "lost hire"]), col("off_hire_days", ["downtime days", "days off hire"]),
                        col("reason_code", ["off hire reason", "cause"], match=True), col("start_ts")]},
]
for t in gov_tables:
    t["column_configs"].sort(key=lambda c: c["column_name"])

GOVERNED_TEXT = """## PURPOSE
- Answer fleet performance questions for fleet managers and commercial analysts: vessel register, charter book, daily noon-report performance, off hire and CII ratings. Data is synthetic and runs from January 2025 to 30 September 2026.
## DISAMBIGUATION
- Containership capacity is measured in TEU and PCTC capacity in CEU. Never add TEU and CEU together, and never report PCTC capacity in TEU. If a question asks for capacity without naming a vessel type, report each vessel type separately with its unit.
- Off hire with no qualifier means all reasons. Unplanned off hire excludes DRYDOCK.
- Current or active charters means contract_status ACTIVE.
- This year means calendar 2026, and the data ends on 30 September 2026.
- Compare fuel consumption only between vessels of the same design class, and only on days at sea.
## Instructions you must follow when providing summaries
- Always state the unit: TEU, CEU, USD per day, tonnes or days.
- Round USD to whole dollars and fuel to one decimal place.
"""

joins = [{"id": new_id(), "left": {"identifier": f"{S}.{t}", "alias": t},
          "right": {"identifier": f"{S}.fleet_vessels", "alias": "fleet_vessels"},
          "sql": [f"`{t}`.`imo_number` = `fleet_vessels`.`imo_number`", "--rt=FROM_RELATIONSHIP_TYPE_MANY_TO_ONE--"],
          "comment": [f"Each {t} row belongs to one vessel"],
          "instruction": [f"Join {t} to fleet_vessels to get vessel name, class or type"]}
         for t in ["charter_book", "vessel_daily_performance", "vessel_off_hire", "vessel_cii_ratings"]]

examples = [
    {"id": new_id(), "question": ["Which vessels have the most unplanned off-hire days in 2026?"],
     "sql": sql_lines(f"""SELECT v.vessel_name, v.design_class, ROUND(SUM(o.off_hire_days), 1) AS unplanned_off_hire_days
FROM {S}.vessel_off_hire o
JOIN {S}.fleet_vessels v ON o.imo_number = v.imo_number
WHERE o.is_planned = FALSE AND YEAR(o.start_ts) = 2026
GROUP BY v.vessel_name, v.design_class
ORDER BY unplanned_off_hire_days DESC
LIMIT 10"""),
     "usage_guidance": ["Use for ranking vessels by unplanned downtime; DRYDOCK is planned and excluded"]},
    {"id": new_id(), "question": ["Show average main engine fuel per day at sea by design class"],
     "sql": sql_lines(f"""SELECT v.design_class, ROUND(AVG(p.main_engine_fuel_mt), 1) AS avg_me_fuel_mt_per_sea_day
FROM {S}.vessel_daily_performance p
JOIN {S}.fleet_vessels v ON p.imo_number = v.imo_number
WHERE p.is_at_sea = TRUE
GROUP BY v.design_class
ORDER BY v.design_class"""),
     "usage_guidance": ["Use for fuel efficiency comparisons; always restrict to days at sea and group by design class"]},
    {"id": new_id(), "question": ["What is the fleet capacity by vessel type?"],
     "sql": sql_lines(f"""SELECT vessel_type, capacity_unit, COUNT(*) AS vessels, SUM(capacity_value) AS total_capacity
FROM {S}.fleet_vessels
GROUP BY vessel_type, capacity_unit"""),
     "usage_guidance": ["Use for any capacity question that does not name a single vessel type; TEU and CEU are reported separately"]},
]
snippets = {
    "filters": by_id([
        {"id": new_id(), "display_name": "unplanned off hire", "sql": ["vessel_off_hire.is_planned = FALSE"],
         "synonyms": ["unplanned downtime", "breakdowns"], "instruction": ["Apply when users ask about unplanned off hire or downtime"]},
        {"id": new_id(), "display_name": "active charters", "sql": ["charter_book.contract_status = 'ACTIVE'"],
         "synonyms": ["current charters", "charter book"], "instruction": ["Apply for questions about the current charter book"]},
        {"id": new_id(), "display_name": "days at sea", "sql": ["vessel_daily_performance.is_at_sea = TRUE"],
         "synonyms": ["sea days", "steaming days"], "instruction": ["Apply for speed and fuel efficiency questions"]},
    ]),
    "measures": by_id([
        {"id": new_id(), "display_name": "Lost hire (USD)", "sql": ["SUM(vessel_off_hire.lost_hire_usd)"],
         "synonyms": ["lost revenue", "off-hire cost"], "instruction": ["Use for revenue lost to off hire"]},
        {"id": new_id(), "display_name": "CO2 emitted (tonnes)", "sql": ["SUM(vessel_daily_performance.co2_mt)"],
         "synonyms": ["emissions", "carbon"], "instruction": ["Use for CO2 totals"]},
    ]),
    "expressions": by_id([
        {"id": new_id(), "display_name": "Report year", "sql": ["YEAR(vessel_daily_performance.report_date)"],
         "synonyms": ["year"], "instruction": ["Use to group noon-report metrics by calendar year"]},
    ]),
}

J = f"JOIN {S}.fleet_vessels v ON {{a}}.imo_number = v.imo_number"
BENCHMARKS = [
    ("How many vessels are in the fleet?", f"SELECT COUNT(*) AS vessels FROM {S}.fleet_vessels"),
    ("How many PCTCs are in the fleet?", f"SELECT COUNT(*) AS pctc_vessels FROM {S}.fleet_vessels WHERE vessel_type = 'PCTC'"),
    ("How many vessels have an LNG dual-fuel system?", f"SELECT COUNT(*) AS vessels FROM {S}.fleet_vessels WHERE fuel_system = 'LNG_DUAL_FUEL'"),
    ("What is the total capacity of the PCTC fleet?", f"SELECT SUM(capacity_ceu) AS total_capacity_ceu FROM {S}.fleet_vessels WHERE vessel_type = 'PCTC'"),
    ("What is the total TEU capacity of the containership fleet?", f"SELECT SUM(capacity_teu) AS total_capacity_teu FROM {S}.fleet_vessels WHERE vessel_type = 'CONTAINERSHIP'"),
    ("What is the total deadweight tonnage of the fleet?", f"SELECT SUM(dwt) AS total_dwt FROM {S}.fleet_vessels"),
    ("What is the average age of the containership fleet in years?", f"SELECT ROUND(AVG(vessel_age_years), 1) AS avg_age_years FROM {S}.fleet_vessels WHERE vessel_type = 'CONTAINERSHIP'"),
    ("How many vessels in the fleet were built by each shipyard?", f"SELECT shipyard, COUNT(*) AS vessels FROM {S}.fleet_vessels GROUP BY shipyard ORDER BY vessels DESC, shipyard ASC"),
    ("Which charterer has the most vessels on active charter?", f"SELECT charterer_name, COUNT(DISTINCT imo_number) AS vessels FROM {S}.charter_book WHERE contract_status = 'ACTIVE' GROUP BY charterer_name ORDER BY vessels DESC, charterer_name ASC LIMIT 1"),
    ("Which charterer pays the highest average daily rate on active charters?", f"WITH ranked AS (SELECT charterer_name, ROUND(AVG(daily_rate_usd), 0) AS avg_daily_rate_usd FROM {S}.charter_book WHERE contract_status = 'ACTIVE' GROUP BY charterer_name)\nSELECT charterer_name, avg_daily_rate_usd FROM ranked WHERE avg_daily_rate_usd = (SELECT MAX(avg_daily_rate_usd) FROM ranked) ORDER BY charterer_name ASC"),
    ("What is the average daily charter rate on active CS-10000 charters?", f"SELECT ROUND(AVG(c.daily_rate_usd), 0) AS avg_daily_rate_usd FROM {S}.charter_book c {J.format(a='c')} WHERE c.contract_status = 'ACTIVE' AND v.design_class = 'CS-10000'"),
    ("What is the average daily charter rate on active PCTC charters?", f"SELECT ROUND(AVG(c.daily_rate_usd), 0) AS avg_daily_rate_usd FROM {S}.charter_book c {J.format(a='c')} WHERE c.contract_status = 'ACTIVE' AND v.vessel_type = 'PCTC'"),
    ("How many active charters end on or before 30 September 2027?", f"SELECT COUNT(*) AS charters FROM {S}.charter_book WHERE contract_status = 'ACTIVE' AND end_date <= DATE'2027-09-30'"),
    ("What is the average remaining term in days of active charters?", f"SELECT ROUND(AVG(remaining_term_days), 0) AS avg_remaining_term_days FROM {S}.charter_book WHERE contract_status = 'ACTIVE'"),
    ("Which 5 vessels lost the most hire revenue to unplanned off hire in 2026?", f"SELECT v.vessel_name, SUM(o.lost_hire_usd) AS lost_hire_usd FROM {S}.vessel_off_hire o {J.format(a='o')} WHERE o.is_planned = FALSE AND YEAR(o.start_ts) = 2026 GROUP BY v.vessel_name ORDER BY lost_hire_usd DESC, v.vessel_name ASC LIMIT 5"),
    ("What are the total unplanned off-hire days by reason code?", f"SELECT reason_code, ROUND(SUM(off_hire_days), 1) AS off_hire_days FROM {S}.vessel_off_hire WHERE is_planned = FALSE GROUP BY reason_code ORDER BY off_hire_days DESC, reason_code ASC"),
    ("Which vessels are tied for the most main engine breakdowns?", f"WITH c AS (SELECT v.vessel_name, COUNT(*) AS me_breakdowns FROM {S}.vessel_off_hire o {J.format(a='o')} WHERE o.reason_code = 'ME_BREAKDOWN' GROUP BY v.vessel_name)\nSELECT vessel_name, me_breakdowns FROM c WHERE me_breakdowns = (SELECT MAX(me_breakdowns) FROM c) ORDER BY vessel_name ASC"),
    ("What was the total lost hire in USD from all off-hire events that started in 2025?", f"SELECT SUM(lost_hire_usd) AS lost_hire_usd FROM {S}.vessel_off_hire WHERE YEAR(start_ts) = 2025"),
    ("How many drydock off-hire events started in 2026?", f"SELECT COUNT(*) AS drydock_events FROM {S}.vessel_off_hire WHERE reason_code = 'DRYDOCK' AND YEAR(start_ts) = 2026"),
    ("What was total CO2 emitted by the fleet in 2025 by fuel system?", f"SELECT v.fuel_system, ROUND(SUM(p.co2_mt), 0) AS co2_tonnes FROM {S}.vessel_daily_performance p {J.format(a='p')} WHERE YEAR(p.report_date) = 2025 GROUP BY v.fuel_system ORDER BY co2_tonnes DESC, v.fuel_system ASC"),
    ("What was the total distance sailed by the fleet in 2025 in nautical miles?", f"SELECT ROUND(SUM(distance_nm), 0) AS distance_nm FROM {S}.vessel_daily_performance WHERE YEAR(report_date) = 2025"),
    ("What was the average speed at sea of LNG dual-fuel vessels in 2026?", f"SELECT ROUND(AVG(p.speed_over_ground_kn), 2) AS avg_speed_kn FROM {S}.vessel_daily_performance p {J.format(a='p')} WHERE v.fuel_system = 'LNG_DUAL_FUEL' AND p.is_at_sea = TRUE AND YEAR(p.report_date) = 2026"),
    ("Across all noon reports, which CS-10000 vessels burn more than 10 percent above their class average main engine fuel per day at sea?",
     f"WITH per_vessel AS (SELECT v.vessel_name, AVG(p.main_engine_fuel_mt) AS avg_me_fuel FROM {S}.vessel_daily_performance p {J.format(a='p')} WHERE v.design_class = 'CS-10000' AND p.is_at_sea = TRUE GROUP BY v.vessel_name)\nSELECT vessel_name, ROUND(avg_me_fuel, 1) AS avg_me_fuel_mt FROM per_vessel WHERE avg_me_fuel > 1.1 * (SELECT AVG(avg_me_fuel) FROM per_vessel) ORDER BY avg_me_fuel DESC, vessel_name ASC"),
    ("How many vessels had a CII rating of D or E in 2025?", f"SELECT COUNT(DISTINCT imo_number) AS vessels FROM {S}.vessel_cii_ratings WHERE rating_year = 2025 AND cii_rating IN ('D', 'E')"),
    ("How many vessels received each CII rating in 2025?", f"SELECT cii_rating, COUNT(*) AS vessels FROM {S}.vessel_cii_ratings WHERE rating_year = 2025 GROUP BY cii_rating ORDER BY cii_rating ASC"),
]

GOVERNED = {
    "version": 2,
    "config": {"sample_questions": by_id([
        {"id": new_id(), "question": ["What is our fleet capacity by vessel type?"]},
        {"id": new_id(), "question": ["Which vessels have the most unplanned off hire this year?"]},
        {"id": new_id(), "question": ["Which vessels are burning more fuel than their sister ships?"]},
    ])},
    "data_sources": {"tables": gov_tables},
    "instructions": {
        "text_instructions": [{"id": new_id(), "content": [l + "\n" for l in GOVERNED_TEXT.strip().splitlines()]}],
        "example_question_sqls": by_id(examples),
        "join_specs": by_id(joins),
        "sql_snippets": snippets,
    },
    "benchmarks": {"questions": by_id([
        {"id": new_id(), "question": [q], "answer": [{"format": "SQL", "content": sql_lines(sql)}]} for q, sql in BENCHMARKS])},
}

# Check that every benchmark answer runs before creating the agents
for q, sql in BENCHMARKS:
    assert spark.sql(sql).count() > 0, f"Benchmark returned no rows: {q}"
print(f"{len(BENCHMARKS)} benchmark answers validated")

# COMMAND ----------

# MAGIC %md
# MAGIC ## 5. Create or update the agents

# COMMAND ----------

AGENTS = [
    ("asbuilt", "Fleet Performance (as-built)", "Fleet data", ASBUILT),
    ("governed", "Fleet Performance (governed)",
     "Answers fleet performance questions about the vessel register, charter book, daily noon-report performance, off hire and IMO CII ratings, "
     "for fleet managers and commercial analysts. Built on the certified fleet views; synthetic demo data from January 2025 to September 2026.",
     GOVERNED),
]

# The agent IDs are kept in a small table so reruns update the same agents and the cleanup notebook can find them.
spark.sql(f"CREATE TABLE IF NOT EXISTS {S}._demo_agents (agent_key STRING, space_id STRING, title STRING) COMMENT 'Genie Agents created by the fleet demo setup notebook'")
existing = {r.agent_key: r.space_id for r in spark.table(f"{S}._demo_agents").collect()}

w.workspace.mkdirs(AGENTS_FOLDER)
created = {}
for key, title, desc, cfg in AGENTS:
    sid = existing.get(key)
    if sid:
        try:
            w.api_client.do("PATCH", f"/api/2.0/genie/spaces/{sid}", body={
                "title": title, "description": desc, "warehouse_id": WAREHOUSE_ID, "serialized_space": json.dumps(cfg)})
            created[key] = sid
            print(f"Updated {title}: {sid}")
            continue
        except Exception as e:
            print(f"Existing agent {sid} not usable ({e}); creating a new one")
    r = w.api_client.do("POST", "/api/2.0/genie/spaces", body={
        "title": title, "description": desc, "parent_path": AGENTS_FOLDER, "warehouse_id": WAREHOUSE_ID,
        "serialized_space": json.dumps(cfg)})
    created[key] = r.get("space_id") or r.get("id")
    print(f"Created {title}: {created[key]}")

spark.createDataFrame([(k, created[k], t) for k, t, _, _ in AGENTS], "agent_key STRING, space_id STRING, title STRING") \
    .write.mode("overwrite").saveAsTable(f"{S}._demo_agents")

# COMMAND ----------

# MAGIC %md
# MAGIC ## 6. Grant the Genie Workbench app access (optional)
# MAGIC Genie Workbench scans agents as the signed-in user, but Auto-Optimize runs as the app's service principal. It needs `CAN_MANAGE` on each agent and read access to the data. The grants below are limited to this demo schema and the two demo agents.

# COMMAND ----------

app_url = None
if APP_NAME:
    try:
        app = w.api_client.do("GET", f"/api/2.0/apps/{APP_NAME}")  # REST call works on every SDK version
        sp = app["service_principal_client_id"]
        app_url = app.get("url")
        spark.sql(f"GRANT USE CATALOG ON CATALOG `{CATALOG}` TO `{sp}`")
        spark.sql(f"GRANT USE SCHEMA, SELECT, MODIFY ON SCHEMA {S} TO `{sp}`")
        for sid in created.values():
            # PATCH adds the grant without replacing anyone else's access to the agent
            w.api_client.do("PATCH", f"/api/2.0/permissions/genie/{sid}", body={"access_control_list": [
                {"service_principal_name": sp, "permission_level": "CAN_MANAGE"}]})
        print(f"Granted the '{APP_NAME}' app service principal ({sp}) access to the demo schema and both agents.")
    except Exception as e:
        print(f"Skipped Workbench grants: {e}\nInstall Genie Workbench first, then rerun this notebook or grant access manually.")

# COMMAND ----------

# MAGIC %md
# MAGIC ## 7. Links

# COMMAND ----------

host = w.config.host.rstrip("/")
lines = []
for key, title, _, _ in AGENTS:
    sid = created[key]
    lines.append(f"<li><b>{title}</b>: <a href='{host}/genie/rooms/{sid}' target='_blank'>open in Genie</a>"
                 + (f" &middot; <a href='{app_url}/?view=detail&space={sid}&tab=score' target='_blank'>open in Genie Workbench</a>" if app_url else "")
                 + "</li>")
displayHTML("<h3>Fleet demo agents</h3><ul>" + "".join(lines) + "</ul>"
            "<p>Next: open the as-built agent in Genie Workbench and choose <b>Run IQ Scan</b>.</p>")
