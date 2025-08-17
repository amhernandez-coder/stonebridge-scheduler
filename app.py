
import io
from datetime import datetime
import pandas as pd
import streamlit as st

st.set_page_config(page_title="Stonebridge Scheduler – First Working Version", layout="wide")

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

def norm_cols(df: pd.DataFrame) -> pd.DataFrame:
    df = df.copy()
    df.columns = [str(c).strip().lower() for c in df.columns]
    return df

ALIASES = {
    "site": ["site","location","work location","venue","clinic","office"],
    "date": ["date","shift date","start date","start","day","timesheet date"],
    "modality": ["modality","type","category","mode"],
    "role": ["role","area","position","duty","job","title"],
    "provider": ["provider","employee","employee name","name","staff"],
    "language": ["language","lang"],
}

def pick(low: dict, keys) -> str:
    for k in keys:
        v = low.get(k)
        if v is not None and str(v).strip() != "":
            return str(v).strip()
    return ""

def to_iso_date(val: str) -> str:
    s = (val or "").strip()
    if len(s) >= 10 and s[4:5] == "-" and s[7:8] == "-":
        return s[:10]
    if "/" in s:
        p = s.split()[0].split("/")
        if len(p) >= 3:
            mm = p[0].zfill(2); dd = p[1].zfill(2); yyyy = p[2]
            if len(yyyy) == 2: yyyy = "20" + yyyy
            return f"{yyyy}-{mm}-{dd}"
    try:
        return pd.to_datetime(s, errors="coerce").strftime("%Y-%m-%d")
    except Exception:
        return s[:10]

def normalize_row(row: pd.Series) -> dict:
    low = {str(k).strip().lower(): row.get(k, "") for k in row.index}
    site = pick(low, ALIASES["site"])
    date_raw = pick(low, ALIASES["date"])
    date = to_iso_date(date_raw)
    modality = pick(low, ALIASES["modality"])
    role_guess = pick(low, ALIASES["role"]).lower()
    provider = pick(low, ALIASES["provider"])
    language = (pick(low, ALIASES["language"]) or "English").lower()

    if not modality:
        modality = "Telehealth" if "tele" in (site or "").lower() else "Live"

    if role_guess in ("interviewer","tester","solo"):
        role = role_guess
    else:
        if any(k in role_guess for k in ("tester","lpa","psychometric")):
            role = "tester"
        elif any(k in role_guess for k in ("solo","independent")):
            role = "solo"
        else:
            role = "interviewer"

    return {"site": site, "date": date, "modality": modality, "role": role, "provider": provider, "language": language}

SPANISH_SET = {
    "cintia martinez","liliana pizana","emma thomae","ben aguilar","cesar villarreal",
    "teresa castano","dr. alvarez-sanders","alvarez-sanders","belinda castillo","noemi martinez"
}

def lang_of(name: str) -> str:
    if not name:
        return "english"
    return "spanish" if any(tok in name.lower() for tok in SPANISH_SET) else "english"

def score_pair(i_name: str, t_name: str) -> int:
    i = (i_name or "").lower()
    t = (t_name or "").lower()
    if "lakaii jones" in i and "virginia parker" in t: return 5
    if "lyn mcdonald" in i and "ed howarth" in t: return 5
    if "liliana pizana" in i and "emma thomae" in t: return 4
    if lang_of(i_name) == lang_of(t_name): return 2
    return 0

def generate(roster_df: pd.DataFrame):
    df = norm_cols(roster_df)
    mapped = df.apply(normalize_row, axis=1, result_type="expand")
    req = (mapped["site"]!="") & (mapped["date"]!="") & (mapped["modality"]!="") & (mapped["role"]!="") & (mapped["provider"]!="")
    mapped = mapped[req]
    if mapped.empty:
        raise ValueError("No valid rows after normalization. Ensure your file has Location/Employee/Start Date headers.")

    events, violations, gaps = [], [], []
    site_counts = mapped.groupby("site")["provider"].count()
    provider_counts = mapped.groupby("provider")["site"].count()

    for (site, date, modality), grp in mapped.groupby(["site","date","modality"], dropna=False):
        interviewers = [r["provider"] for _, r in grp[grp["role"]=="interviewer"].iterrows()]
        testers = [r["provider"] for _, r in grp[grp["role"]=="tester"].iterrows()]
        solos = [r["provider"] for _, r in grp[grp["role"]=="solo"].iterrows()]
        available = set(testers)
        for i_name in interviewers:
            best_t, best_s = None, -1
            for t_name in list(available):
                sc = score_pair(i_name, t_name)
                if sc > best_s:
                    best_s, best_t = sc, t_name
            if best_t:
                available.remove(best_t)
                desc = f"Dyad pairing for {date} {modality}."
                if best_s >= 4: desc += " Preference satisfied."
                elif best_s == 2: desc += " Language-matched."
                events.append({"Subject": f"{title_abbrev(site)} | Pairing: {i_name} + {best_t}","Start Date": date,"Start Time":"","End Date": date,"End Time":"","All Day Event":"True","Description": desc,"Location": site})
                if best_s < 4 and ("lakaii jones" in i_name.lower() or "lyn mcdonald" in i_name.lower() or lang_of(i_name)=="spanish"):
                    violations.append({"site": site,"date": date,"modality": modality,"type":"Preference Not Met","interviewer": i_name,"tester": best_t})
            else:
                events.append({"Subject": f"{title_abbrev(site)} | GAP: {i_name} (no tester)","Start Date": date,"Start Time":"","End Date": date,"End Time":"","All Day Event":"True","Description": f"Unpaired interviewer. Needs tester for {modality}.","Location": site})
                gaps.append({"site": site,"date": date,"modality": modality,"interviewer": i_name})
                violations.append({"site": site,"date": date,"modality": modality,"type":"Unpaired Interviewer","interviewer": i_name})
        for t_name in available:
            events.append({"Subject": f"{title_abbrev(site)} | GAP: {t_name} (tester unassigned)","Start Date": date,"Start Time":"","End Date": date,"EndTime":"","All Day Event":"True","Description":"Tester not assigned.","Location": site})
            gaps.append({"site": site,"date": date,"modality": modality,"tester": t_name})
        for s_name in solos:
            events.append({"Subject": f"{title_abbrev(site)} | SOLO: {s_name}","Start Date": date,"Start Time":"","End Date": date,"End Time":"","All Day Event":"True","Description": f"Solo provider working {modality}.","Location": site})
    return pd.DataFrame(events), pd.DataFrame(violations), pd.DataFrame(gaps), provider_counts, site_counts

st.title("Stonebridge Scheduler – First Working Version")
st.caption("Upload Deputy CSV/XLSX. Auto-maps Location/Employee/Start Date, infers modality, outputs Google Calendar all-day CSV.")

file = st.file_uploader("Upload Deputy roster (CSV or XLSX)", type=["csv","xlsx","xls"])
if st.button("Run pairings", type="primary", disabled=not file) and file:
    try:
        roster = load_tabular(file)
        events_df, violations_df, gaps_df, provider_counts, site_counts = generate(roster)
        csv_text = to_google_csv(events_df.to_dict(orient="records"))
        fname = f"Stonebridge_Pairings_{timestamp()}.csv"
        st.success(f"Generated {len(events_df)} calendar rows.")
        st.download_button("Download Google Calendar CSV", data=csv_text.encode("utf-8"), file_name=fname, mime="text/csv")
        st.subheader("Summary")
        c1, c2 = st.columns(2)
        with c1: st.table(pd.DataFrame({"site": site_counts.index,"count": site_counts.values}))
        with c2: st.table(pd.DataFrame({"provider": provider_counts.index,"count": provider_counts.values}).sort_values("count",ascending=False))
        st.subheader("Violations")
        if not violations_df.empty: st.dataframe(violations_df)
        else: st.info("No violations detected.")
        st.subheader("Gaps")
        if not gaps_df.empty: st.dataframe(gaps_df)
        else: st.info("No gaps detected.")
    except Exception as e:
        st.error(f"Error: {e}")
