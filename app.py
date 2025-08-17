
import io
from datetime import datetime
import pandas as pd
import streamlit as st

st.set_page_config(page_title="Stonebridge Scheduler – Streamlit (Baseline)", layout="wide")

# -------------------- Helpers --------------------
def timestamp() -> str:
    return datetime.now().strftime("%Y-%m-%d_%H-%M-%S")

def title_abbrev(site: str) -> str:
    if not site:
        return site
    s = site.lower()
    if "san antonio" in s or "san antonio behavioral" in s or s == "sa":
        return "SA"
    return site

def to_google_csv(rows):
    # Google Calendar all-day format
    cols = ["Subject","Start Date","Start Time","End Date","End Time","All Day Event","Description","Location"]
    df = pd.DataFrame(rows, columns=cols)
    df["All Day Event"] = "True"
    buf = io.StringIO()
    df.to_csv(buf, index=False)
    return buf.getvalue()

def load_tabular(uploaded_file) -> pd.DataFrame:
    name = uploaded_file.name.lower()
    if name.endswith(".xlsx") or name.endswith(".xls"):
        return pd.read_excel(uploaded_file, dtype=str)
    return pd.read_csv(uploaded_file, dtype=str)

def norm_col(df: pd.DataFrame):
    df = df.copy()
    df.columns = [str(c).strip().lower() for c in df.columns]
    return df

# ---------- Deputy Auto-Mapper ----------
ROSTER_ALIASES = {
    "site": ["site","location","work location","venue","clinic","office"],
    "date": ["date","shift date","start date","start","day","timesheet date"],
    "modality": ["modality","type","category","mode"],
    "role": ["role","area","position","duty","job","title"],
    "provider": ["provider","employee","employee name","name","staff"],
    "language": ["language","lang"],
}

def _pick(low: dict, aliases: list) -> str:
    for a in aliases:
        v = low.get(a)
        if v is not None and str(v).strip() != "":
            return str(v).strip()
    return ""

def _to_iso_date(val: str) -> str:
    s = (val or "").strip()
    # ISO already
    if len(s) >= 10 and s[4] == "-" and s[7] == "-":
        return s[:10]
    # Try MM/DD/YYYY (optionally with time)
    if "/" in s:
        parts = s.split()[0].split("/")
        if len(parts) >= 3:
            mm = parts[0].zfill(2)
            dd = parts[1].zfill(2)
            yyyy = parts[2]
            if len(yyyy) == 2:
                yyyy = "20" + yyyy
            return f"{yyyy}-{mm}-{dd}"
    # Fallback: pandas
    try:
        return pd.to_datetime(s, errors="coerce").strftime("%Y-%m-%d")
    except Exception:
        return s[:10]

def normalize_row_deputy(row: pd.Series) -> dict:
    low = {str(k).strip().lower(): row[k] for k in row.index}
    site = _pick(low, ROSTER_ALIASES["site"])
    date_raw = _pick(low, ROSTER_ALIASES["date"])
    date = _to_iso_date(date_raw)
    modality = _pick(low, ROSTER_ALIASES["modality"])
    role_guess = _pick(low, ROSTER_ALIASES["role"]).lower()
    provider = _pick(low, ROSTER_ALIASES["provider"])
    language = (_pick(low, ROSTER_ALIASES["language"]) or "English").lower()

    # Infer modality if missing
    if not modality:
        modality = "Telehealth" if "tele" in (site or "").lower() else "Live"

    # Normalize role into interviewer|tester|solo
    role = role_guess
    if role not in {"interviewer","tester","solo"}:
        if any(k in role_guess for k in ["tester","lpa","psychometric"]):
            role = "tester"
        elif any(k in role_guess for k in ["solo","independent"]):
            role = "solo"
        else:
            role = "interviewer"

    return {"site": site, "date": date, "modality": modality, "role": role, "provider": provider, "language": language}

# ---------- Language & preferences ----------
SPANISH_SET = {
    "cintia martinez","liliana pizana","emma thomae","ben aguilar","cesar villarreal",
    "teresa castano","dr. alvarez-sanders","alvarez-sanders","belinda castillo","noemi martinez"
}

def get_lang(name: str) -> str:
    if not name:
        return "english"
    return "spanish" if any(tok in name.lower() for tok in SPANISH_SET) else "english"

def preferred_score(interviewer: str, tester: str) -> int:
    i = (interviewer or "").lower()
    t = (tester or "").lower()
    # Hard-coded clinic prefs
    if "lakaii jones" in i and "virginia parker" in t: return 5
    if "lyn mcdonald" in i and "ed howarth" in t: return 5
    if "liliana pizana" in i and "emma thomae" in t: return 4
    # Language match
    if get_lang(i) == get_lang(t): return 2
    return 0

def normalize_roster(df: pd.DataFrame) -> pd.DataFrame:
    # Apply deputy mapping for every row
    mapped = df.apply(normalize_row_deputy, axis=1, result_type="expand")
    # Filter rows that have all required fields
    req = (mapped["site"]!="") & (mapped["date"]!="") & (mapped["modality"]!="") & (mapped["role"]!="") & (mapped["provider"]!="")
    return mapped[req]

def generate_pairings(df_raw: pd.DataFrame):
    df = norm_col(df_raw)
    df = normalize_roster(df)

    if df.empty:
        raise ValueError("No valid rows after normalization. Check that your Deputy file includes Location / Employee / Start Date columns.")

    grouped = df.groupby(["site","date","modality"], dropna=False)

    events = []
    violations = []
    gaps = []

    site_shift_counts = df.groupby("site")["provider"].count()
    provider_shift_counts = df.groupby("provider")["site"].count()

    for (site, date, modality), grp in grouped:
        interviewers = [r["provider"] for _, r in grp[grp["role"]=="interviewer"].iterrows()]
        testers = [r["provider"] for _, r in grp[grp["role"]=="tester"].iterrows()]
        solos = [r["provider"] for _, r in grp[grp["role"]=="solo"].iterrows()]

        available_testers = set(testers)

        for i_name in interviewers:
            best_t = None
            best_score = -1
            for t_name in list(available_testers):
                score = preferred_score(i_name, t_name)
                if score > best_score:
                    best_score = score
                    best_t = t_name
            if best_t:
                available_testers.remove(best_t)
                subject = f"{title_abbrev(site)} | Pairing: {i_name} + {best_t}"
                desc = f"Dyad pairing for {date} {modality}."
                if best_score >= 4: desc += " Preference satisfied."
                elif best_score == 2: desc += " Language-matched."
                events.append({
                    "Subject": subject, "Start Date": date, "Start Time": "", "End Date": date, "End Time": "",
                    "All Day Event": "True", "Description": desc, "Location": site
                })
                if best_score < 4 and ("lakaii jones" in i_name.lower() or "lyn mcdonald" in i_name.lower() or get_lang(i_name) == "spanish"):
                    violations.append({"site": site, "date": date, "modality": modality, "type": "Preference Not Met", "interviewer": i_name, "tester": best_t})
            else:
                subject = f"{title_abbrev(site)} | GAP: {i_name} (no tester)"
                events.append({
                    "Subject": subject, "Start Date": date, "Start Time": "", "End Date": date, "End Time": "",
                    "All Day Event": "True", "Description": f"Unpaired interviewer. Needs tester for {modality}.", "Location": site
                })
                gaps.append({"site": site, "date": date, "modality": modality, "interviewer": i_name})
                violations.append({"site": site, "date": date, "modality": modality, "type": "Unpaired Interviewer", "interviewer": i_name})

        for t_name in available_testers:
            subject = f"{title_abbrev(site)} | GAP: {t_name} (tester unassigned)"
            events.append({
                "Subject": subject, "Start Date": date, "Start Time": "", "End Date": date, "End Time": "",
                "All Day Event": "True", "Description": "Tester not assigned.", "Location": site
            })
            gaps.append({"site": site, "date": date, "modality": modality, "tester": t_name})

        for s_name in solos:
            subject = f"{title_abbrev(site)} | SOLO: {s_name}"
            events.append({
                "Subject": subject, "Start Date": date, "Start Time": "", "End Date": date, "End Time": "",
                "All Day Event": "True", "Description": f"Solo provider working {modality}.", "Location": site
            })

    return pd.DataFrame(events), pd.DataFrame(violations), pd.DataFrame(gaps), provider_shift_counts, site_shift_counts

# -------------------- UI --------------------
st.title("Stonebridge Scheduler – Streamlit (Baseline)")
st.caption("Upload Deputy roster (CSV/XLSX). Auto-maps Location/Employee/Start Date, infers modality, and outputs Google Calendar all-day CSV (SA abbreviation).")

roster_file = st.file_uploader("1) Upload Deputy roster (CSV or XLSX)", type=["csv","xlsx","xls"])

if st.button("Run pairings", type="primary", disabled=not roster_file) and roster_file:
    try:
        roster_raw = load_tabular(roster_file)
        events_df, violations_df, gaps_df, cnt_by_provider, cnt_by_site = generate_pairings(roster_raw)
        csv_text = to_google_csv(events_df.to_dict(orient="records"))
        fname = f"Stonebridge_Pairings_{timestamp()}.csv"

        st.success(f"Generated {len(events_df)} calendar rows.")
        st.download_button("Download Google Calendar CSV", data=csv_text.encode("utf-8"), file_name=fname, mime="text/csv")

        st.subheader("Summary")
        s1, s2 = st.columns(2)
        site_counts = pd.DataFrame({"site": cnt_by_site.index, "count": cnt_by_site.values})
        provider_counts = pd.DataFrame({"provider": cnt_by_provider.index, "count": cnt_by_provider.values}).sort_values("count", ascending=False)
        with s1:
            st.markdown("**Shifts per site**")
            st.dataframe(site_counts)
        with s2:
            st.markdown("**Shifts per provider**")
            st.dataframe(provider_counts)

        st.subheader("Violations")
        if not violations_df.empty: st.dataframe(violations_df)
        else: st.info("No violations detected.")

        st.subheader("Gaps")
        if not gaps_df.empty: st.dataframe(gaps_df)
        else: st.info("No gaps detected.")

    except Exception as e:
        st.error(f"Error: {e}")
