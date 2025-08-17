
import io
from pathlib import Path
from datetime import datetime
import pandas as pd
import streamlit as st

st.set_page_config(page_title="Stonebridge Scheduler – Streamlit", layout="wide")

# ---------- Config / helpers ----------
OUTPUTS_DIR = Path("outputs")
OUTPUTS_DIR.mkdir(exist_ok=True)

def timestamp():
    d = datetime.now()
    return d.strftime("%Y-%m-%d_%H-%M-%S")

def title_abbrev(site: str) -> str:
    if not site:
        return site
    s = site.lower()
    if "san antonio" in s or "san antonio behavioral" in s or s == "sa":
        return "SA"
    return site

def to_google_csv(rows):
    # Google Calendar all-day format
    # Columns: Subject, Start Date, Start Time, End Date, End Time, All Day Event, Description, Location
    cols = ["Subject","Start Date","Start Time","End Date","End Time","All Day Event","Description","Location"]
    df = pd.DataFrame(rows, columns=cols)
    df["All Day Event"] = "True"
    buffer = io.StringIO()
    df.to_csv(buffer, index=False)
    return buffer.getvalue()

def load_tabular(uploaded_file) -> pd.DataFrame:
    name = uploaded_file.name.lower()
    if name.endswith(".xlsx") or name.endswith(".xls"):
        return pd.read_excel(uploaded_file)
    return pd.read_csv(uploaded_file)

def norm_col(df: pd.DataFrame):
    df = df.copy()
    df.columns = [c.strip().lower() for c in df.columns]
    return df

def normalize_roster_row(row: pd.Series):
    get = lambda k: str(row.get(k,"")).strip()
    site = get("site")
    date = get("date")  # expecting YYYY-MM-DD
    modality = get("modality")
    role = get("role").lower()
    provider = get("provider")
    language = get("language").lower() if get("language") else "english"
    return {"site": site, "date": date, "modality": modality, "role": role, "provider": provider, "language": language}

def build_master_maps(master_df: pd.DataFrame):
    # columns: provider, language (English/Spanish), is_spanish (true/false), preferred_tester
    lang_map = {}
    pref_map = {}
    if master_df is None or master_df.empty:
        return lang_map, pref_map

    df = norm_col(master_df)
    for _, r in df.iterrows():
        provider = str(r.get("provider","")).strip()
        if not provider:
            continue
        lang_raw = str(r.get("language","")).strip().lower()
        is_spanish = str(r.get("is_spanish","")).strip().lower()
        lang = "spanish" if (is_spanish == "true" or lang_raw == "spanish") else ("english" if lang_raw=="english" else "")
        if lang:
            lang_map[provider.lower()] = lang
        pref = str(r.get("preferred_tester","")).strip()
        if pref:
            pref_map[provider.lower()] = pref
    return lang_map, pref_map

SPANISH_SET = {
    "cintia martinez","liliana pizana","emma thomae","ben aguilar","cesar villarreal",
    "teresa castano","dr. alvarez-sanders","alvarez-sanders","belinda castillo","noemi martinez"
}

def get_lang(name: str, lang_map: dict):
    if not name:
        return "english"
    n = name.lower()
    if n in lang_map:
        return lang_map[n]
    return "spanish" if any(tok in n for tok in SPANISH_SET) else "english"

def preferred_score(interviewer: str, tester: str, lang_map: dict, pref_map: dict) -> int:
    i = (interviewer or "").lower()
    t = (tester or "").lower()
    # Hard-coded clinic prefs
    if "lakaii jones" in i and "virginia parker" in t: return 5
    if "lyn mcdonald" in i and "ed howarth" in t: return 5
    if "liliana pizana" in i and "emma thomae" in t: return 4

    # Master-file preference
    pref = pref_map.get(i)
    if pref and pref.lower() in t:
        return 4

    # Language match
    lang_i = get_lang(i, lang_map)
    lang_t = get_lang(t, lang_map)
    if lang_i == lang_t:
        return 2

    return 0

def generate_pairings(roster_df: pd.DataFrame, lang_map: dict, pref_map: dict):
    """Core pairing logic. Returns:
    events (list for calendar), violations (list of dicts), gaps (list of dicts),
    provider_shift_counts (Series), site_shift_counts (Series)
    """
    df = norm_col(roster_df)
    need_cols = {"site","date","modality","role","provider"}
    missing = need_cols - set(df.columns)
    if missing:
        raise ValueError(f"Roster missing required columns: {', '.join(sorted(missing))}")

    df = df.apply(normalize_roster_row, axis=1, result_type="expand")
    df_grouped = df.groupby(["site","date","modality"], dropna=False)

    events = []
    violations = []
    gaps = []

    site_shift_counts = df.groupby("site")["provider"].count()
    provider_shift_counts = df.groupby("provider")["site"].count()

    for (site, date, modality), grp in df_grouped:
        interviewers = [r["provider"] for _, r in grp[grp["role"]=="interviewer"].iterrows()]
        testers = [r["provider"] for _, r in grp[grp["role"]=="tester"].iterrows()]
        solos = [r["provider"] for _, r in grp[grp["role"]=="solo"].iterrows()]

        available_testers = set(testers)

        for i_name in interviewers:
            best_t = None
            best_score = -1
            for t_name in list(available_testers):
                score = preferred_score(i_name, t_name, lang_map, pref_map)
                if score > best_score:
                    best_score = score
                    best_t = t_name

            if best_t:
                available_testers.remove(best_t)
                subject = f"{title_abbrev(site)} | Pairing: {i_name} + {best_t}"
                desc = f"Dyad pairing for {date} {modality}."
                if best_score >= 4:
                    desc += " Preference satisfied."
                elif best_score == 2:
                    desc += " Language-matched."
                events.append({
                    "Subject": subject,
                    "Start Date": date,
                    "Start Time": "",
                    "End Date": date,
                    "End Time": "",
                    "All Day Event": "True",
                    "Description": desc,
                    "Location": site
                })
                if best_score < 4 and (
                    "lakaii jones" in i_name.lower()
                    or "lyn mcdonald" in i_name.lower()
                    or get_lang(i_name, lang_map) == "spanish"
                ):
                    violations.append({
                        "site": site, "date": date, "modality": modality,
                        "type": "Preference Not Met", "interviewer": i_name, "tester": best_t
                    })
            else:
                subject = f"{title_abbrev(site)} | GAP: {i_name} (no tester)"
                events.append({
                    "Subject": subject,
                    "Start Date": date,
                    "Start Time": "",
                    "End Date": date,
                    "End Time": "",
                    "All Day Event": "True",
                    "Description": f"Unpaired interviewer. Needs tester for {modality}.",
                    "Location": site
                })
                gaps.append({"site": site, "date": date, "modality": modality, "interviewer": i_name})
                violations.append({
                    "site": site, "date": date, "modality": modality,
                    "type": "Unpaired Interviewer", "interviewer": i_name
                })

        for t_name in available_testers:
            subject = f"{title_abbrev(site)} | GAP: {t_name} (tester unassigned)"
            events.append({
                "Subject": subject,
                "Start Date": date,
                "Start Time": "",
                "End Date": date,
                "End Time": "",
                "All Day Event": "True",
                "Description": "Tester not assigned.",
                "Location": site
            })
            gaps.append({"site": site, "date": date, "modality": modality, "tester": t_name})

        for s_name in solos:
            subject = f"{title_abbrev(site)} | SOLO: {s_name}"
            events.append({
                "Subject": subject,
                "Start Date": date,
                "Start Time": "",
                "End Date": date,
                "End Time": "",
                "All Day Event": "True",
                "Description": f"Solo provider working {modality}.",
                "Location": site
            })

    return events, violations, gaps, provider_shift_counts, site_shift_counts

# ---------- UI ----------
st.title("Stonebridge Scheduler – Streamlit")
st.caption("Upload roster (CSV/XLSX), optionally Provider Master (CSV/XLSX). Generates Google Calendar all-day CSV with pairings, gaps, and violations. Abbreviates San Antonio → “SA” in titles.")

colA, colB = st.columns(2)
with colA:
    roster_file = st.file_uploader("1) Upload roster (CSV or XLSX)", type=["csv","xlsx","xls"])
with colB:
    master_file = st.file_uploader("Optional: Upload Provider Master (CSV/XLSX)", type=["csv","xlsx","xls"])

run = st.button("Run pairings", type="primary", disabled=not roster_file)

if run and roster_file:
    try:
        roster = norm_col(load_tabular(roster_file))
        master_df = norm_col(load_tabular(master_file)) if master_file else None
        lang_map, pref_map = build_master_maps(master_df)

        events, violations, gaps, cnt_by_provider, cnt_by_site = generate_pairings(roster, lang_map, pref_map)
        csv_text = to_google_csv(events)
        fname = f"Stonebridge_Pairings_{timestamp()}.csv"
        (OUTPUTS_DIR / fname).write_text(csv_text, encoding="utf-8")  # local save

        st.success(f"Generated {len(events)} calendar rows. Saved as {OUTPUTS_DIR / fname}")
        st.download_button("Download Google Calendar CSV", data=csv_text.encode("utf-8"),
                           file_name=fname, mime="text/csv")

        st.subheader("Summary")
        c1, c2 = st.columns(2)
        with c1:
            st.markdown("**Shifts per site**")
            st.dataframe(cnt_by_site.reset_index().rename(columns={"index":"site","site":"count","provider":"count"}))
        with c2:
            st.markdown("**Shifts per provider**")
            st.dataframe(cnt_by_provider.reset_index().rename(columns={"index":"provider","provider":"count","site":"count"}).sort_values("site", ascending=False))

        st.subheader("Violations")
        if violations:
            st.dataframe(pd.DataFrame(violations))
        else:
            st.info("No violations detected.")

        st.subheader("Gaps")
        if gaps:
            st.dataframe(pd.DataFrame(gaps))
        else:
            st.info("No gaps detected.")

        st.session_state["last_csv"] = {"name": fname, "text": csv_text}

    except Exception as e:
        st.error(f"Error: {e}")

if "last_csv" in st.session_state:
    st.caption(f"Most recent file in this session: {st.session_state['last_csv']['name']}")
    st.download_button("Download last CSV again",
                       data=st.session_state["last_csv"]["text"].encode("utf-8"),
                       file_name=st.session_state["last_csv"]["name"],
                       mime="text/csv")
