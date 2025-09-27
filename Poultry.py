# app.py
# Poultry (layers) management with AM/PM mortality, temperature, damaged eggs, and water:
# - Data: Farm, House, Flock, DailyRecord, IntraDayRecord (AM/PM)
# - KPIs: HDEP, HHEP, Egg mass, FCR, Daily mortality, Water/hen, Water:Feed ratio, Crack rate
# - Pages: Admin & Setup, Daily Entry, KPIs & Trends, Export

import os
import sqlite3
from datetime import date
from typing import Optional, Tuple

import numpy as np
import pandas as pd
import streamlit as st

DB_PATH = os.path.join(os.path.dirname(__file__), "poultry.db")

# Optional compatibility shim; primary usage is st.rerun per modern API
def safe_rerun():
    if hasattr(st, "rerun"):
        st.rerun()
    elif hasattr(st, "experimental_rerun"):
        st.experimental_rerun()
    else:
        pass

# ---------- Database helpers ----------
def get_conn():
    return sqlite3.connect(DB_PATH, detect_types=sqlite3.PARSE_DECLTYPES | sqlite3.PARSE_COLNAMES)

def init_db():
    with get_conn() as conn:
        cur = conn.cursor()
        cur.execute("""
        CREATE TABLE IF NOT EXISTS farm (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL UNIQUE
        )""")
        cur.execute("""
        CREATE TABLE IF NOT EXISTS house (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            farm_id INTEGER NOT NULL,
            name TEXT NOT NULL,
            UNIQUE(farm_id, name),
            FOREIGN KEY (farm_id) REFERENCES farm(id)
        )""")
        cur.execute("""
        CREATE TABLE IF NOT EXISTS flock (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            farm_id INTEGER NOT NULL,
            house_id INTEGER NOT NULL,
            placed_date DATE NOT NULL,
            hens_housed_start INTEGER NOT NULL,
            notes TEXT,
            FOREIGN KEY (farm_id) REFERENCES farm(id),
            FOREIGN KEY (house_id) REFERENCES house(id)
        )""")
        cur.execute("""
        CREATE TABLE IF NOT EXISTS daily_record (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            farm_id INTEGER NOT NULL,
            house_id INTEGER NOT NULL,
            flock_id INTEGER NOT NULL,
            record_date DATE NOT NULL,
            hens_present_open INTEGER NOT NULL,
            hens_added INTEGER NOT NULL DEFAULT 0,
            hens_removed INTEGER NOT NULL DEFAULT 0,
            deaths INTEGER NOT NULL DEFAULT 0,
            culls INTEGER NOT NULL DEFAULT 0,
            eggs_count INTEGER NOT NULL DEFAULT 0,
            avg_egg_weight_g REAL,
            feed_issued_kg REAL NOT NULL DEFAULT 0,
            UNIQUE(house_id, record_date),
            FOREIGN KEY (farm_id) REFERENCES farm(id),
            FOREIGN KEY (house_id) REFERENCES house(id),
            FOREIGN KEY (flock_id) REFERENCES flock(id)
        )""")
        # New: AM/PM session entries
        cur.execute("""
        CREATE TABLE IF NOT EXISTS intra_day_record (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            farm_id INTEGER NOT NULL,
            house_id INTEGER NOT NULL,
            flock_id INTEGER NOT NULL,
            record_date DATE NOT NULL,
            time_slot TEXT NOT NULL, -- 'AM' or 'PM'
            deaths INTEGER NOT NULL DEFAULT 0,
            culls INTEGER NOT NULL DEFAULT 0,
            temp_c REAL,
            water_used_l REAL NOT NULL DEFAULT 0,
            damaged_eggs INTEGER NOT NULL DEFAULT 0,
            notes TEXT,
            UNIQUE(house_id, record_date, time_slot),
            FOREIGN KEY (farm_id) REFERENCES farm(id),
            FOREIGN KEY (house_id) REFERENCES house(id),
            FOREIGN KEY (flock_id) REFERENCES flock(id)
        )""")
        conn.commit()

def fetch_df(query: str, params: Tuple = ()):
    with get_conn() as conn:
        return pd.read_sql_query(query, conn, params=params)

def exec_sql(query: str, params: Tuple = ()):
    with get_conn() as conn:
        cur = conn.cursor()
        cur.execute(query, params)
        conn.commit()
        return cur.lastrowid

# ---------- CRUD ----------
def list_farms() -> pd.DataFrame:
    return fetch_df("SELECT id, name FROM farm ORDER BY name")

def add_farm(name: str) -> int:
    return exec_sql("INSERT OR IGNORE INTO farm(name) VALUES (?)", (name,))

def list_houses(farm_id: int) -> pd.DataFrame:
    return fetch_df("SELECT id, name FROM house WHERE farm_id=? ORDER BY name", (farm_id,))

def add_house(farm_id: int, name: str) -> int:
    return exec_sql("INSERT OR IGNORE INTO house(farm_id, name) VALUES (?, ?)", (farm_id, name))

def list_flocks(farm_id: int, house_id: int) -> pd.DataFrame:
    return fetch_df("""
        SELECT id, placed_date, hens_housed_start, COALESCE(notes,'') AS notes
        FROM flock WHERE farm_id=? AND house_id=?
        ORDER BY placed_date DESC
    """, (farm_id, house_id))

def add_flock(farm_id: int, house_id: int, placed_date_: date, hens_start: int, notes: str) -> int:
    return exec_sql("""
        INSERT INTO flock(farm_id, house_id, placed_date, hens_housed_start, notes)
        VALUES (?, ?, ?, ?, ?)
    """, (farm_id, house_id, placed_date_.isoformat(), hens_start, notes))

def upsert_daily_record(
    farm_id: int, house_id: int, flock_id: int, record_date_: date,
    hens_present_open: int, hens_added: int, hens_removed: int,
    deaths: int, culls: int, eggs_count: int, avg_egg_weight_g: Optional[float],
    feed_issued_kg: float
):
    with get_conn() as conn:
        cur = conn.cursor()
        cur.execute("""
            UPDATE daily_record
               SET farm_id=?, house_id=?, flock_id=?, hens_present_open=?, hens_added=?, hens_removed=?,
                   deaths=?, culls=?, eggs_count=?, avg_egg_weight_g=?, feed_issued_kg=?
             WHERE house_id=? AND record_date=?
        """, (farm_id, house_id, flock_id, hens_present_open, hens_added, hens_removed,
              deaths, culls, eggs_count, avg_egg_weight_g, feed_issued_kg, house_id, record_date_.isoformat()))
        if cur.rowcount == 0:
            cur.execute("""
                INSERT INTO daily_record(
                    farm_id, house_id, flock_id, record_date, hens_present_open, hens_added, hens_removed,
                    deaths, culls, eggs_count, avg_egg_weight_g, feed_issued_kg
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (farm_id, house_id, flock_id, record_date_.isoformat(), hens_present_open, hens_added, hens_removed,
                  deaths, culls, eggs_count, avg_egg_weight_g, feed_issued_kg))
        conn.commit()

def upsert_intra_day_record(
    farm_id: int, house_id: int, flock_id: int, record_date_: date, time_slot: str,
    deaths: int, culls: int, temp_c: Optional[float], water_used_l: float, damaged_eggs: int, notes: str = ""
):
    time_slot = time_slot.upper()
    assert time_slot in ("AM", "PM")
    with get_conn() as conn:
        cur = conn.cursor()
        cur.execute("""
            UPDATE intra_day_record
               SET farm_id=?, house_id=?, flock_id=?, deaths=?, culls=?, temp_c=?, water_used_l=?, damaged_eggs=?, notes=?
             WHERE house_id=? AND record_date=? AND time_slot=?
        """, (farm_id, house_id, flock_id, deaths, culls, temp_c, water_used_l, damaged_eggs, notes,
              house_id, record_date_.isoformat(), time_slot))
        if cur.rowcount == 0:
            cur.execute("""
                INSERT INTO intra_day_record(
                    farm_id, house_id, flock_id, record_date, time_slot, deaths, culls, temp_c, water_used_l, damaged_eggs, notes
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (farm_id, house_id, flock_id, record_date_.isoformat(), time_slot, deaths, culls, temp_c, water_used_l, damaged_eggs, notes))
        conn.commit()

def get_daily_records(farm_id: int, house_id: int, flock_id: int) -> pd.DataFrame:
    # Base daily
    df_daily = fetch_df("""
        SELECT dr.*, f.hens_housed_start
        FROM daily_record dr
        JOIN flock f ON f.id = dr.flock_id
        WHERE dr.farm_id=? AND dr.house_id=? AND dr.flock_id=?
        ORDER BY record_date ASC
    """, (farm_id, house_id, flock_id))

    # Aggregate AM/PM sessions into daily totals
    df_sess = fetch_df("""
        SELECT
          record_date,
          SUM(deaths) AS deaths_sess,
          SUM(culls) AS culls_sess,
          SUM(water_used_l) AS water_used_l_sess,
          SUM(damaged_eggs) AS damaged_eggs_sess,
          MAX(CASE WHEN time_slot='AM' THEN temp_c END) AS temp_am_c,
          MAX(CASE WHEN time_slot='PM' THEN temp_c END) AS temp_pm_c
        FROM intra_day_record
        WHERE farm_id=? AND house_id=? AND flock_id=?
        GROUP BY record_date
        ORDER BY record_date ASC
    """, (farm_id, house_id, flock_id))

    # Merge (outer in case someone captured AM/PM before daily)
    df = pd.merge(df_daily, df_sess, on="record_date", how="outer", suffixes=("", "_sessjoin"))

    # Carry identifiers for rows coming only from sessions (set IDs nulls properly)
    if "farm_id" not in df.columns:
        df["farm_id"] = farm_id
    if "house_id" not in df.columns:
        df["house_id"] = house_id
    if "flock_id" not in df.columns:
        df["flock_id"] = flock_id

    # If hens_housed_start missing in session-only rows, fetch once
    if df["hens_housed_start"].isna().any():
        base = fetch_df("SELECT hens_housed_start FROM flock WHERE id=?", (flock_id,))
        if not base.empty:
            df["hens_housed_start"] = df["hens_housed_start"].fillna(int(base["hens_housed_start"].iloc[0]))

    # Ensure expected numeric columns exist
    for c in ["hens_present_open","hens_added","hens_removed","deaths","culls","eggs_count","avg_egg_weight_g","feed_issued_kg"]:
        if c not in df.columns:
            df[c] = np.nan
    return df

# ---------- KPI calculations ----------
def compute_kpis(df: pd.DataFrame) -> pd.DataFrame:
    if df.empty:
        return df.copy()
    x = df.copy()

    # Prefer AM/PM totals when available
    deaths_eff = np.where(x.get("deaths_sess").notna(), x["deaths_sess"], x.get("deaths", 0))
    culls_eff  = np.where(x.get("culls_sess").notna(),  x["culls_sess"],  x.get("culls", 0))
    x["deaths_eff"] = deaths_eff.astype(float)
    x["culls_eff"]  = culls_eff.astype(float)

    # Session totals and temps
    x["water_used_l_total"] = x.get("water_used_l_sess", 0).fillna(0.0)
    x["damaged_eggs_total"] = x.get("damaged_eggs_sess", 0).fillna(0.0)
    temp_am = x.get("temp_am_c", pd.Series(np.nan, index=x.index))
    temp_pm = x.get("temp_pm_c", pd.Series(np.nan, index=x.index))
    x["temp_max_c"] = np.nanmax(np.vstack([temp_am, temp_pm]), axis=0)

    # Hen-days approximation
    x["hen_days"] = x["hens_present_open"] - 0.5 * (x["deaths_eff"] + x["culls_eff"])

    # HDEP %
    x["hdep_pct"] = np.where(x["hen_days"] > 0, (x["eggs_count"] / x["hen_days"]) * 100.0, np.nan)

    # HHEP % (daily)
    x["hhep_pct"] = np.where(x["hens_housed_start"] > 0, (x["eggs_count"] / x["hens_housed_start"]) * 100.0, np.nan)

    # Egg kg and FCR
    x["egg_kg"] = np.where(x["avg_egg_weight_g"].notna(), x["eggs_count"] * x["avg_egg_weight_g"] / 1000.0, np.nan)
    x["fcr_per_kg_egg"] = np.where(x["egg_kg"] > 0, x["feed_issued_kg"] / x["egg_kg"], np.nan)
    x["fcr_per_dozen"]  = np.where(x["eggs_count"] > 0, (x["feed_issued_kg"] * 12.0) / x["eggs_count"], np.nan)

    # Daily mortality %
    x["daily_mortality_pct"] = np.where(x["hens_present_open"] > 0, (x["deaths_eff"] / x["hens_present_open"]) * 100.0, np.nan)

    # Egg mass per hen per day (g): HDEP% * avg egg weight
    x["egg_mass_g_per_hen_day"] = np.where(x["hdep_pct"].notna() & x["avg_egg_weight_g"].notna(),
                                           x["hdep_pct"] * x["avg_egg_weight_g"], np.nan)

    # Roll-forward hens present (not stored)
    x["hens_present_next"] = x["hens_present_open"] + x["hens_added"] - x["hens_removed"] - x["deaths_eff"] - x["culls_eff"]

    # Water KPIs and crack rate
    x["water_per_hen_l"]     = np.where(x["hens_present_open"] > 0, x["water_used_l_total"] / x["hens_present_open"], np.nan)
    x["water_feed_ratio"]    = np.where(x["feed_issued_kg"] > 0, x["water_used_l_total"] / x["feed_issued_kg"], np.nan)
    x["saleable_eggs_count"] = np.maximum(0, (x["eggs_count"].fillna(0) - x["damaged_eggs_total"].fillna(0)))
    x["crack_rate_pct"]      = np.where(x["eggs_count"] > 0, (x["damaged_eggs_total"] / x["eggs_count"]) * 100.0, np.nan)

    return x

# ---------- UI pages ----------
def page_admin_setup():
    st.header("Admin & Setup")

    st.subheader("Farms")
    farms = list_farms()
    cols = st.columns(2)
    with cols[0]:
        st.dataframe(farms, use_container_width=True)
    with cols[1]:
        farm_name = st.text_input("New farm name")
        if st.button("Add farm") and farm_name.strip():
            add_farm(farm_name.strip())
            st.success("Farm added")
            st.rerun()  # updated

    st.subheader("Houses")
    farms = list_farms()
    farm_sel = st.selectbox("Select farm for house", farms["name"].tolist() if not farms.empty else [])
    if farm_sel:
        farm_id = int(farms.set_index("name").loc[farm_sel, "id"])
        houses = list_houses(farm_id)
        c2 = st.columns(2)
        with c2[0]:
            st.dataframe(houses, use_container_width=True)
        with c2[1]:
            house_name = st.text_input("New house name")
            if st.button("Add house") and house_name.strip():
                add_house(farm_id, house_name.strip())
                st.success("House added")
                st.rerun()  # updated

    st.subheader("Flocks")
    if not farms.empty:
        farm_sel2 = st.selectbox("Farm", farms["name"].tolist(), key="flock_farm")
        farm_id2 = int(farms.set_index("name").loc[farm_sel2, "id"])
        houses2 = list_houses(farm_id2)
        if not houses2.empty:
            house_sel2 = st.selectbox("House", houses2["name"].tolist(), key="flock_house")
            house_id2 = int(houses2.set_index("name").loc[house_sel2, "id"])
            flocks = list_flocks(farm_id2, house_id2)
            st.dataframe(flocks, use_container_width=True)
            with st.form("new_flock"):
                placed_date_ = st.date_input("Placed date", value=date.today())
                hens_start = st.number_input("Hens housed (start)", min_value=0, step=1)
                notes = st.text_input("Notes", value="")
                submitted = st.form_submit_button("Add flock")
                if submitted:
                    if hens_start <= 0:
                        st.error("Hens housed start must be > 0")
                    else:
                        add_flock(farm_id2, house_id2, placed_date_, int(hens_start), notes)
                        st.success("Flock added")
                        st.rerun()  # updated

def _selection_row():
    farms = list_farms()
    if farms.empty:
        st.info("Add a farm first in Admin & Setup.")
        return None, None, None
    farm_name = st.selectbox("Farm", farms["name"].tolist())
    farm_id = int(farms.set_index("name").loc[farm_name, "id"])
    houses = list_houses(farm_id)
    if houses.empty:
        st.info("Add a house for this farm in Admin & Setup.")
        return None, None, None
    house_name = st.selectbox("House", houses["name"].tolist())
    house_id = int(houses.set_index("name").loc[house_name, "id"])
    flocks = list_flocks(farm_id, house_id)
    if flocks.empty:
        st.info("Add a flock for this house in Admin & Setup.")
        return None, None, None
    flocks["label"] = flocks.apply(lambda r: f'{r["placed_date"]} | start={r["hens_housed_start"]}', axis=1)
    label = st.selectbox("Flock", flocks["label"].tolist())
    flock_id = int(flocks.loc[flocks["label"] == label, "id"].iloc[0])
    return farm_id, house_id, flock_id

def page_daily_entry():
    st.header("Daily Entry")
    sel = _selection_row()
    if sel[0] is None:
        return
    farm_id, house_id, flock_id = sel

    with st.form("daily"):
        record_date = st.date_input("Record date", value=date.today())
        st.markdown("Daily totals")
        col1, col2, col3 = st.columns(3)
        with col1:
            hens_present_open = st.number_input("Hens present (start of day)", min_value=0, step=1)
            hens_added = st.number_input("Hens added", min_value=0, step=1, value=0)
            hens_removed = st.number_input("Hens removed", min_value=0, step=1, value=0)
        with col2:
            eggs_count = st.number_input("Eggs collected (count)", min_value=0, step=1)
            avg_egg_weight_g = st.number_input("Average egg weight (g)", min_value=0.0, step=0.5, value=60.0)
        with col3:
            feed_issued_kg = st.number_input("Feed issued (kg)", min_value=0.0, step=0.1)
            # Optional legacy daily deaths/culls (overridden by sessions if present)
            deaths = st.number_input("Deaths (daily total; optional)", min_value=0, step=1, value=0)
            culls = st.number_input("Culls (daily total; optional)", min_value=0, step=1, value=0)

        st.markdown("AM / PM session entries")
        with st.expander("Morning (AM) session"):
            deaths_am = st.number_input("AM deaths", min_value=0, step=1, value=0)
            culls_am = st.number_input("AM culls", min_value=0, step=1, value=0)
            temp_am_c = st.number_input("AM temperature (°C)", min_value=0.0, max_value=60.0, step=0.5, value=26.0)
            water_am_l = st.number_input("AM water used (L)", min_value=0.0, step=0.5, value=0.0)
            damaged_am = st.number_input("AM damaged/cracked eggs", min_value=0, step=1, value=0)
        with st.expander("Evening (PM) session"):
            deaths_pm = st.number_input("PM deaths", min_value=0, step=1, value=0)
            culls_pm = st.number_input("PM culls", min_value=0, step=1, value=0)
            temp_pm_c = st.number_input("PM temperature (°C)", min_value=0.0, max_value=60.0, step=0.5, value=28.0)
            water_pm_l = st.number_input("PM water used (L)", min_value=0.0, step=0.5, value=0.0)
            damaged_pm = st.number_input("PM damaged/cracked eggs", min_value=0, step=1, value=0)

        calc_btn = st.form_submit_button("Compute KPIs (no save)")
        save_btn = st.form_submit_button("Save")

    # Get hens_housed_start of this flock
    flock_df = fetch_df("SELECT hens_housed_start FROM flock WHERE id=?", (flock_id,))
    hens_housed_start = int(flock_df["hens_housed_start"].iloc[0]) if not flock_df.empty else 0

    def compute_preview():
        data = {
            "hens_present_open": [hens_present_open],
            "hens_added": [hens_added],
            "hens_removed": [hens_removed],
            "deaths": [deaths],
            "culls": [culls],
            "eggs_count": [eggs_count],
            "avg_egg_weight_g": [avg_egg_weight_g],
            "feed_issued_kg": [feed_issued_kg],
            "hens_housed_start": [hens_housed_start],
            # Session totals
            "deaths_sess": [deaths_am + deaths_pm],
            "culls_sess": [culls_am + culls_pm],
            "water_used_l_sess": [water_am_l + water_pm_l],
            "damaged_eggs_sess": [damaged_am + damaged_pm],
            "temp_am_c": [temp_am_c],
            "temp_pm_c": [temp_pm_c],
        }
        df = pd.DataFrame(data)
        return compute_kpis(df)

    if calc_btn or save_btn:
        k = compute_preview()
        st.subheader("Computed KPIs (preview)")
        st.dataframe(k.T.rename(columns={0:"value"}))

        # Alerts guided by temperature and expected W:F ranges
        alerts = []
        val = k.iloc[0]
        temp_max = float(val.get("temp_max_c")) if pd.notnull(val.get("temp_max_c")) else None
        wfr = float(val.get("water_feed_ratio")) if pd.notnull(val.get("water_feed_ratio")) else None
        crack_rate = float(val.get("crack_rate_pct")) if pd.notnull(val.get("crack_rate_pct")) else None

        # HDEP/FCR/mortality thresholds
        if pd.notnull(val.get("hdep_pct")) and val["hdep_pct"] < 80:
            alerts.append(f"Low HDEP: {val['hdep_pct']:.1f}% (< 80%)")
        if pd.notnull(val.get("fcr_per_kg_egg")) and val["fcr_per_kg_egg"] > 2.5:
            alerts.append(f"High FCR per kg egg: {val['fcr_per_kg_egg']:.2f} (> 2.5)")
        if pd.notnull(val.get("daily_mortality_pct")) and val["daily_mortality_pct"] > 0.5:
            alerts.append(f"High daily mortality: {val['daily_mortality_pct']:.2f}% (> 0.5%)")

        # Water:Feed ratio alerts by temperature bands (examples)
        if temp_max is not None and wfr is not None:
            if temp_max <= 24 and (wfr < 1.4 or wfr > 2.4):
                alerts.append(f"Unusual water:feed ratio {wfr:.2f} at {temp_max:.1f}°C (expect ~1.6–2.0)")
            if temp_max >= 30 and wfr < 1.6:
                alerts.append(f"Low water:feed ratio {wfr:.2f} under heat ({temp_max:.1f}°C)")
            if temp_max >= 35 and wfr > 6.0:
                alerts.append(f"Very high water:feed ratio {wfr:.2f}; verify leaks/meters")

        # Crack rate and heat
        if temp_max is not None and temp_max >= 30 and crack_rate is not None and crack_rate > 0.5:
            alerts.append(f"Crack rate {crack_rate:.2f}% with heat ({temp_max:.1f}°C): check cooling and Ca supply")

        if alerts:
            for a in alerts:
                st.warning(a)

    if save_btn:
        # Save daily totals
        upsert_daily_record(
            farm_id, house_id, flock_id, record_date,
            int(hens_present_open), int(hens_added), int(hens_removed),
            int(deaths), int(culls), int(eggs_count),
            float(avg_egg_weight_g) if avg_egg_weight_g else None,
            float(feed_issued_kg),
        )
        # Save sessions
        upsert_intra_day_record(
            farm_id, house_id, flock_id, record_date, "AM",
            int(deaths_am), int(culls_am),
            float(temp_am_c) if temp_am_c is not None else None,
            float(water_am_l), int(damaged_am), ""
        )
        upsert_intra_day_record(
            farm_id, house_id, flock_id, record_date, "PM",
            int(deaths_pm), int(culls_pm),
            float(temp_pm_c) if temp_pm_c is not None else None,
            float(water_pm_l), int(damaged_pm), ""
        )
        st.success("Daily and AM/PM records saved")

def page_kpis_trends():
    st.header("KPIs & Trends")
    sel = _selection_row()
    if sel[0] is None:
        return
    farm_id, house_id, flock_id = sel

    df = get_daily_records(farm_id, house_id, flock_id)
    df_calc = compute_kpis(df)
    st.subheader("Daily records with KPIs")
    st.dataframe(df_calc, use_container_width=True)

    if not df_calc.empty:
        st.subheader("Production & welfare trends")

        # Convert record_date to datetime and set sorted DatetimeIndex for time-based rolling
        tmp = df_calc.copy()
        tmp["record_date"] = pd.to_datetime(tmp["record_date"])
        tmp = tmp.set_index("record_date").sort_index()

        # Plots of daily metrics
        chart_df = tmp[["hdep_pct", "fcr_per_kg_egg", "daily_mortality_pct"]].copy()
        st.line_chart(chart_df, height=250)

        # 30-day rolling averages using a time-based window
        roll = chart_df.rolling(window="30D", min_periods=1).mean()
        st.line_chart(roll, height=250)

        # Water and cracks
        chart2 = tmp[["water_feed_ratio", "water_per_hen_l", "crack_rate_pct"]].copy()
        st.line_chart(chart2, height=250)

        # Cumulative mortality (simple running sum of daily %)
        tmp["cdmr_pct"] = tmp["daily_mortality_pct"].fillna(0).cumsum()
        st.caption("Cumulative daily mortality (running sum of daily %)")

        st.line_chart(tmp[["cdmr_pct"]], height=200)

def page_export():
    st.header("Export")
    farms = list_farms()
    if farms.empty:
        st.info("No data to export")
        return
    farm_name = st.selectbox("Farm", farms["name"].tolist())
    farm_id = int(farms.set_index("name").loc[farm_name, "id"])
    houses = list_houses(farm_id)
    if houses.empty:
        st.info("Add a house first")
        return
    house_name = st.selectbox("House", houses["name"].tolist())
    house_id = int(houses.set_index("name").loc[house_name, "id"])
    flocks = list_flocks(farm_id, house_id)
    if flocks.empty:
        st.info("Add a flock first")
        return
    flocks["label"] = flocks.apply(lambda r: f'{r["placed_date"]} | start={r["hens_housed_start"]}', axis=1)
    label = st.selectbox("Flock", flocks["label"].tolist())
    flock_id = int(flocks.loc[flocks["label"] == label, "id"].iloc[0])

    df = get_daily_records(farm_id, house_id, flock_id)
    df_calc = compute_kpis(df)
    csv = df_calc.to_csv(index=False).encode("utf-8")
    st.download_button("Download CSV", csv, file_name=f"poultry_export_flock_{flock_id}.csv", mime="text/csv")
    st.dataframe(df_calc.tail(10), use_container_width=True)

# ---------- App entry ----------
def main():
    st.set_page_config(page_title="Poultry Management", layout="wide")
    init_db()
    st.title("Poultry Management (Layers)")

    page = st.sidebar.radio(
        "Pages",
        options=["Daily Entry", "KPIs & Trends", "Export", "Admin & Setup"],
        index=0
    )

    if page == "Admin & Setup":
        page_admin_setup()
    elif page == "Daily Entry":
        page_daily_entry()
    elif page == "KPIs & Trends":
        page_kpis_trends()
    elif page == "Export":
        page_export()

if __name__ == "__main__":
    main()
