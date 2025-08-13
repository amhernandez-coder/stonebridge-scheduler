# Stonebridge Scheduler – Standalone Streamlit App (FULL updated)
# Features in this version:
# - Works for ANY month/year from uploaded CSVs
# - Strict Year filter + Month/Year multiselect for exports
# - Provider-first Google Calendar CSV (all-day) with Spanish & Private tags
# - Lubbock fixed rules; SA psychometrician cap (1/day)
# - Solo handling; preference pairings
# - Amanda Eberle dual-tester rule
# - Bobby Perez forced LPA, SA-only, never interviewer
# - Gaps & Violations export

import io
import re
import csv
from datetime import datetime
from collections import defaultdict

import pandas as pd
import streamlit as st

# --------------------------- UI HEADER ---------------------------
st.set_page_config(page_title="Stonebridge Scheduler Agent", layout="wide")
st.title("🗓️ Stonebridge Scheduler – Standalone Agent")
st.caption("Upload rosters, apply rules, and export Google Calendar + Gaps/Violations.")

# --------------------------- HELPERS ----------------------------
def norm_role(label: str) -> str:
    r = str(label).strip().lower()
    if r in ["solo", "solo provider", "solo provider ", "solo "]:
        return "solo"
    if "interviewer" in r:
        return "interviewer"
    if "lpa" in r:
        return "lpa"
    if "psychometrician" in r:
        return "psychometrician"
    return r


def sanitize_text(s: str) -> str:
    s = str(s) if pd.notna(s) else ""
    s = s.replace(",", " - ")
    s = re.sub(r"\s+", " ", s).strip()
    return s


def to_mmddyyyy(dt) -> str:
    return pd.to_datetime(dt).strftime("%m/%d/%Y")


def site_short(loc: str) -> str:
    loc = str(loc)
    if "Telemedicine" in loc:
        return "Telehealth"
    if "Lubbock" in loc:
        return "Lubbock Live"
    if "Houston" in loc:
        return "Houston Live"
    if "(SA) Stonebridge Behavioral Health" in loc or "Stonebridge Behavioral" in loc:
        return "San Antonio Live"
    return loc

# Preference pairings (from memory/rules)
PAIRING_PREFERENCES = {
    "Lakaii Jones": "Virginia Parker",
    "Shivani Bhakta": "Bobby Perez",
    "Liliana Pizana": "Emma Thomae",
    "Lyn McDonald": "Ed Howarth",
}

# Spanish-speaking providers (can be edited in sidebar)
DEFAULT_SPANISH_INTERVIEWERS = [
    "Cintia Martinez", "Liliana Pizana", "Emma Thomae", "Ben Aguilar",
    "Cesar Villarreal", "Teresa Castano", "Dr. Alvarez-Sanders",
]
DEFAULT_SPANISH_TESTERS = ["Belinda Castillo", "Noemi Martinez"]

# Private names default
DEFAULT_PRIVATE = ["David Spongberg", "Kaelyn Sheen", "Kaelyn Scheen", "Sharah Heckman"]

# --------------------------- SIDEBAR -----------------------------
with st.sidebar:
    st.header("Inputs")
    st.markdown("**Upload monthly rosters** (CSV) with columns: \n\n- Location\n- Area (Interviewer/LPA/Psychometrician/Solo)\n- Team Member\n- Start Date (YYYY-MM-DD or MM/DD/YYYY)\n\n*You can upload multiple files at once.*")
    roster_files = st.file_uploader("Monthly roster CSV(s)", type=["csv"], accept_multiple_files=True)

    st.markdown("\n**Optional:** Upload an **October Pairings** CSV (already paired) with columns: Date, Location, Interviewer/Solo, LPA/SOLO.")
    october_file = st.file_uploader("October Pairing Schedule CSV (optional)", type=["csv"], accept_multiple_files=False)

    st.divider()
    st.subheader("Private Providers")
    private_names_text = st.text_area("Always mark these names as Private (comma-separated)", ", ".join(DEFAULT_PRIVATE))

    st.subheader("Spanish-speaking Names")
    spanish_interviewers_text = st.text_area("Spanish-speaking Interviewers", ", ".join(DEFAULT_SPANISH_INTERVIEWERS))
    spanish_testers_text = st.text_area("Spanish-speaking Testers", ", ".join(DEFAULT_SPANISH_TESTERS))

    st.divider()
    st.subheader("Export Filters")
    col_y1, col_y2 = st.columns([1,3])
    with col_y1:
        strict_year = st.checkbox("Strict year filter", value=False, help="Exclude rows not in the year you specify.")
    with col_y2:
        year_input = st.text_input("Year (e.g., 2026)", value="", help="Leave blank to include all years.")

    st.divider()
    build_btn = st.button("🚀 Build Schedule")

# --------------------------- CORE LOGIC -------------------------

def build_pairings_from_roster(roster_df: pd.DataFrame, month_label: str,
                               private_names: set, spanish_interviewers: set, spanish_testers: set):
    roster = roster_df.copy()
    roster = roster.rename(columns={"Role": "Area"})
    if "Start Date" not in roster.columns:
        raise ValueError("Roster missing 'Start Date' column")

    roster["Start Date"] = pd.to_datetime(roster["Start Date"], errors="coerce")
    roster["RoleNorm"] = roster["Area"].apply(norm_role)

    # Force Bobby Perez to LPA (never interviewer)
    mask_bobby = roster["Team Member"].astype(str).str.strip().eq("Bobby Perez")
    roster.loc[mask_bobby, "RoleNorm"] = "lpa"

    staffing = defaultdict(list)
    for _, row in roster.iterrows():
        staffing[(row["Start Date"], str(row["Location"]).strip())].append((row["RoleNorm"], row["Team Member"]))

    final_rows = []
    violations = []

    for (date, loc), entries in staffing.items():
        if pd.isna(date):
            continue

        # SA-only constraint for Bobby Perez; also coerce interviewer->LPA if mislabeled
        if any((r == "lpa" and n == "Bobby Perez" and str(loc).strip() != "(SA) Stonebridge Behavioral Health") for r, n in entries):
            violations.append({
                "Month": month_label,
                "Date": to_mmddyyyy(date),
                "Location": loc,
                "Issue": "Bobby Perez is SA-only LPA; scheduled outside SA"
            })
        entries = [("lpa" if (r == "interviewer" and n == "Bobby Perez") else r, n) for r, n in entries]

        interviewers = [n for r, n in entries if r == "interviewer"]
        lpas        = [n for r, n in entries if r == "lpa"]
        psychs      = [n for r, n in entries if r == "psychometrician"]
        solos       = [n for r, n in entries if r == "solo"]

        # Make absolutely sure Bobby is not treated as an interviewer
        if "Bobby Perez" in interviewers:
            interviewers.remove("Bobby Perez")
            if "Bobby Perez" not in lpas:
                lpas.append("Bobby Perez")

        # Record Solo coverage (no pairing)
        for s in solos:
            final_rows.append({
                "Date": date, "Location": loc, "Interviewer": s, "Tester": "(SOLO)",
                "Notes": "Solo provider—no pairing required", "Month": month_label,
                "PsychometricianPair": False
            })

        if not interviewers:
            continue

        day_name = pd.to_datetime(date).strftime("%A")

        # Lubbock special rules for Gaston
        if str(loc).strip().lower() == "lubbock":
            gaston = next((i for i in interviewers if "Gaston" in i), None)
            if gaston:
                if day_name == "Tuesday":
                    k_match = None
                    for k in ["Kaelyn Sheen", "Kaelyn Scheen"]:
                        if k in psychs:
                            k_match = k; break
                    if k_match:
                        final_rows.append({
                            "Date": date, "Location": loc, "Interviewer": gaston, "Tester": k_match,
                            "Notes": "Lubbock Tuesday exception (psychometrician)", "Month": month_label,
                            "PsychometricianPair": True
                        })
                        psychs.remove(k_match); interviewers.remove(gaston)
                    else:
                        violations.append({"Month": month_label, "Date": to_mmddyyyy(date), "Location": loc,
                                           "Issue": "Lubbock Tuesday: Missing psychometrician (Kaelyn) for Gaston"})
                else:
                    if "Maura Brown" in lpas:
                        final_rows.append({"Date": date, "Location": loc, "Interviewer": gaston, "Tester": "Maura Brown",
                                           "Notes": "Lubbock note: Maura Brown paired with Gaston", "Month": month_label,
                                           "PsychometricianPair": False})
                        lpas.remove("Maura Brown"); interviewers.remove(gaston)
                    elif "Tara Stevens" in lpas:
                        final_rows.append({"Date": date, "Location": loc, "Interviewer": gaston, "Tester": "Tara Stevens",
                                           "Notes": "Lubbock fixed pairing", "Month": month_label,
                                           "PsychometricianPair": False})
                        lpas.remove("Tara Stevens"); interviewers.remove(gaston)
                    else:
                        violations.append({"Month": month_label, "Date": to_mmddyyyy(date), "Location": loc,
                                           "Issue": "Lubbock Mon/Wed: Missing Tara/Maura for Gaston"})

        # ---- Amanda Eberle dual tester rule ----
        if any(i == "Amanda Eberle" for i in interviewers):
            preferred = ["Maura Brown", "Dmitriy Lazarevich"]
            assigned = []
            # preferred first
            for p in preferred:
                if p in lpas and p not in assigned:
                    assigned.append(p); lpas.remove(p)
                if len(assigned) == 2:
                    break
            # fill remaining from any LPAs
            while len(assigned) < 2 and lpas:
                nxt = lpas.pop(0)
                if nxt not in assigned:
                    assigned.append(nxt)
            # emit two rows
            for t in assigned:
                final_rows.append({
                    "Date": date, "Location": loc, "Interviewer": "Amanda Eberle", "Tester": t,
                    "Notes": "Dual tester requirement (Eberle)", "Month": month_label,
                    "PsychometricianPair": False
                })
            # remove Amanda and log violation if short
            try:
                interviewers.remove("Amanda Eberle")
            except ValueError:
                pass
            if len(assigned) < 2:
                missing = 2 - len(assigned)
                violations.append({
                    "Month": month_label, "Date": to_mmddyyyy(date), "Location": loc,
                    "Issue": f"Amanda Eberle requires 2 testers (short {missing})"
                })

        # ---- General pairing (Spanish optional; SA psych cap 1/day) ----
        used_psych = False
        for interviewer in list(interviewers):
            paired = None
            note = ""
            psych_pair = False

            pref = PAIRING_PREFERENCES.get(interviewer)
            if pref and pref in lpas:
                paired = pref; lpas.remove(pref); note = "Preference pairing"
            elif str(loc).strip() == "(SA) Stonebridge Behavioral Health" and psychs and not used_psych:
                priors = [p for p in psychs if p in private_names]
                if priors:
                    paired = priors[0]; psychs.remove(paired)
                else:
                    paired = psychs.pop(0)
                used_psych = True; note = "Psychometrician (SA only; max 1/day)"; psych_pair = True
            elif lpas:
                paired = lpas.pop(0); note = "Standard pairing"

            if paired:
                final_rows.append({
                    "Date": date, "Location": loc, "Interviewer": interviewer, "Tester": paired,
                    "Notes": note, "Month": month_label, "PsychometricianPair": psych_pair
                })
                interviewers.remove(interviewer)
            else:
                violations.append({"Month": month_label, "Date": to_mmddyyyy(date), "Location": loc,
                                   "Issue": f"No tester available for {interviewer}"})

    return pd.DataFrame(final_rows), pd.DataFrame(violations)


def build_gcal_provider_first_from_pairs(pairs_df: pd.DataFrame,
                                         private_names: set,
                                         spanish_interviewers: set,
                                         spanish_testers: set) -> pd.DataFrame:
    records = []
    for _, r in pairs_df.iterrows():
        site = site_short(r["Location"])    
        interviewer = str(r["Interviewer"]) 
        tester = str(r["Tester"])          
        is_solo = tester.upper() == "SOLO" or tester == "(SOLO)" or tester.strip() == ""

        # Spanish tag: add only when BOTH are Spanish-speaking
        both_spanish = (interviewer in spanish_interviewers) and (tester in spanish_testers) if not is_solo else False
        inter_str = f"{interviewer}{' (Spanish)' if both_spanish else ''}"

        # Private tag: explicit flag or either name in private list
        psych_pair_flag = bool(r.get("PsychometricianPair", False))
        private_needed = psych_pair_flag or (interviewer in private_names) or (tester in private_names)

        if is_solo:
            title = f"{inter_str} (SOLO) - {site}"
        else:
            test_str = f"{tester}{' (Spanish)' if both_spanish else ''}"
            middle = " - Private - " if private_needed else " - "
            title = f"{inter_str} + {test_str}{middle}{site}"

        title = sanitize_text(title)
        desc = sanitize_text(str(r.get("Notes", "")))

        records.append({
            "Subject": title,
            "Start Date": to_mmddyyyy(r["Date"]),
            "End Date": to_mmddyyyy(r["Date"]),
            "All Day Event": "True",
            "Description": desc,
            "Location": sanitize_text(str(r["Location"]))
        })
    return pd.DataFrame(records, columns=["Subject","Start Date","End Date","All Day Event","Description","Location"])

# --------------------------- RUN WHEN CLICKED -------------------
if build_btn:
    private_names = set([s.strip() for s in private_names_text.split(',') if s.strip()])
    spanish_interviewers = set([s.strip() for s in spanish_interviewers_text.split(',') if s.strip()])
    spanish_testers = set([s.strip() for s in spanish_testers_text.split(',') if s.strip()])

    monthly_pairs = []
    monthly_violations = []

    # Handle October (paired CSV)
    if october_file is not None:
        oct_df_raw = pd.read_csv(october_file)
        oct_pairs = pd.DataFrame({
            "Date": pd.to_datetime(oct_df_raw["Date"], errors="coerce"),
            "Location": oct_df_raw["Location"].apply(sanitize_text),
            "Interviewer": oct_df_raw["Interviewer/Solo"].apply(sanitize_text),
            "Tester": oct_df_raw["LPA/SOLO"].apply(sanitize_text),
            "Notes": "",
            "Month": "October",
            "PsychometricianPair": oct_df_raw["LPA/SOLO"].isin(private_names) | oct_df_raw["Interviewer/Solo"].isin(private_names)
        })
        oct_pairs.loc[oct_pairs["Tester"].str.upper().eq("SOLO"), "Tester"] = "(SOLO)"
        monthly_pairs.append(oct_pairs)
        # October violations require a raw roster; skipped here by design

    # Handle uploaded monthly rosters (any months/years)
    for up in roster_files or []:
        df = pd.read_csv(up)
        # infer nice month label from min Start Date
        month_label_dt = pd.to_datetime(df.get("Start Date"), errors="coerce").min()
        month_label = month_label_dt.strftime("%B") if pd.notna(month_label_dt) else "Month"
        pairs_df, viol_df = build_pairings_from_roster(df, month_label, private_names, spanish_interviewers, spanish_testers)
        monthly_pairs.append(pairs_df)
        if not viol_df.empty:
            monthly_violations.append(viol_df)

    if not monthly_pairs:
        st.error("No input files provided. Upload at least one roster or the October pairings.")
        st.stop()

    # Combine all pairs
    all_pairs = pd.concat(monthly_pairs, ignore_index=True)

    # Build GCal events (provider-first) and sort
    gcal_all = build_gcal_provider_first_from_pairs(all_pairs, private_names, spanish_interviewers, spanish_testers)

    # ---- Strict Year prefilter (affects month list) ----
    if year_input.strip():
        try:
            _yr = int(year_input)
            _gd = pd.to_datetime(gcal_all['Start Date'], errors='coerce')
            _gmask = _gd.dt.year == _yr
            if strict_year:
                gcal_all = gcal_all[_gmask]
            elif not _gmask.all():
                st.info(f"Some appointments fall outside {_yr}. Enable 'Strict year filter' to exclude them.")
        except Exception:
            st.warning("Year filter ignored: please enter a 4-digit year (e.g., 2026).")

    # Sort for display
    gcal_all["Start Date Sort"] = pd.to_datetime(gcal_all["Start Date"])
    gcal_all = gcal_all.sort_values(["Start Date Sort", "Subject"]).drop(columns=["Start Date Sort"])

    # Build violations table (same filtering later)
    if monthly_violations:
        viol_all = pd.concat(monthly_violations, ignore_index=True)
    else:
        viol_all = pd.DataFrame(columns=["Month", "Date", "Location", "Issue"])

    # Strict year filter for violations too (before months list)
    if year_input.strip() and not viol_all.empty and 'Date' in viol_all.columns:
        try:
            _yr = int(year_input)
            _vd = pd.to_datetime(viol_all['Date'], errors='coerce')
            _vmask = _vd.dt.year == _yr
            if strict_year:
                viol_all = viol_all[_vmask]
            elif not _vmask.all():
                st.info("Violations include dates outside your year; enable Strict year to exclude.")
        except Exception:
            pass

    # ---- Month/Year multiselect ----
    months_in_data = sorted(pd.to_datetime(gcal_all['Start Date']).dt.to_period('M').astype(str).unique())
    pretty_months = {m: pd.Period(m).to_timestamp().strftime('%b-%Y') for m in months_in_data}
    selected_months = st.multiselect(
        'Filter by Month/Year',
        options=months_in_data,
        format_func=lambda x: pretty_months[x],
        default=months_in_data
    )

    if selected_months:
        mask_periods = pd.to_datetime(gcal_all['Start Date']).dt.to_period('M').astype(str).isin(selected_months)
        gcal_all = gcal_all[mask_periods]
        if not viol_all.empty and 'Date' in viol_all.columns:
            vmask_periods = pd.to_datetime(viol_all['Date'], errors='coerce').dt.to_period('M').astype(str).isin(selected_months)
            viol_all = viol_all[vmask_periods]

    # ---- Dynamic filename tag ----
    months_present = sorted(pd.to_datetime(gcal_all["Start Date"]).dt.to_period("M").astype(str).unique().tolist())
    def pretty(p):
        dt = pd.Period(p).to_timestamp()
        return dt.strftime("%b-%Y")
    month_tag = ",".join(pretty(m) for m in months_present) if months_present else "AllMonths"

    # ---- Outputs ----
    st.subheader("All Appointments – Google Calendar CSV")
    st.dataframe(gcal_all.head(25))
    gcal_bytes = gcal_all.to_csv(index=False, encoding="utf-8", quoting=csv.QUOTE_MINIMAL).encode("utf-8")
    st.download_button(
        label=f"⬇️ Download ALL appointments ({month_tag}) – Google Calendar CSV",
        data=gcal_bytes,
        file_name=f"Stonebridge_ShiftPairings_All_GCal_{month_tag}.csv",
        mime="text/csv"
    )

    st.subheader("Gaps & Violations – CSV")
    # Sort violations for readability
    if not viol_all.empty and 'Date' in viol_all.columns:
        viol_all['DateSort'] = pd.to_datetime(viol_all['Date'], errors='coerce')
        viol_all = viol_all.sort_values(["Month", "DateSort", "Location", "Issue"]).drop(columns=["DateSort"])
    st.dataframe(viol_all.head(25))
    viol_bytes = viol_all.to_csv(index=False, encoding="utf-8", quoting=csv.QUOTE_MINIMAL).encode("utf-8")
    st.download_button(
        label=f"⬇️ Download Gaps & Violations ({month_tag})",
        data=viol_bytes,
        file_name=f"Stonebridge_ShiftPairings_Gaps_Violations_{month_tag}.csv",
        mime="text/csv"
    )

    st.success("Done! Import the ALL appointments CSV into Google Calendar as all-day events.")
