# stonebridge_scheduler_app.py
# -------------------------------------------------------------
# Stonebridge Scheduling Agent – Single-file Streamlit MVP
# -------------------------------------------------------------
# What it does
# - Upload monthly rosters (CSV) and/or an October pairings CSV
# - Applies Stonebridge rules to maximize interviewer–tester pairings
# - Handles Lubbock fixed pairings, psychometrician constraints, Spanish tags
# - Generates Google Calendar all-day CSVs (per month + combined)
# - Generates Gaps & Violations report
# - Lets you mark specific names as "Private" (e.g., psychometricians)
#
# How to run locally
#   1) pip install streamlit pandas python-dateutil
#   2) streamlit run stonebridge_scheduler_app.py
#   3) Upload your CSVs and click "Build Schedule"
# -------------------------------------------------------------

import io
import re
import csv
import json
from datetime import datetime
from collections import defaultdict

import pandas as pd
import streamlit as st

# ---------------------- UI ----------------------
st.set_page_config(page_title="Stonebridge Scheduler Agent", layout="wide")
st.title("🗓️ Stonebridge Scheduler – Standalone Agent (MVP)")
st.caption("Upload rosters, apply scheduling logic, and export Google Calendar + Gaps/Violations.")

with st.sidebar:
    st.header("Inputs")
    st.markdown("**Upload monthly rosters** (CSV). Use the same columns as your November/December files:\n\n- Location\n- Area (values: Interviewer, LPA, Psychometrician, Solo/SOLO/ Solo Provider)\n- Team Member\n- Start Date (YYYY-MM-DD or MM/DD/YYYY)\n\n*You can upload multiple files at once.*")
    roster_files = st.file_uploader("Monthly roster CSV(s)", type=["csv"], accept_multiple_files=True)

    st.markdown("\n**Optional:** Upload an **October Pairings** CSV (already paired), with columns:\n- Date, Location, Interviewer/Solo, LPA/SOLO")
    october_file = st.file_uploader("October Pairing Schedule CSV (optional)", type=["csv"], accept_multiple_files=False)

    st.divider()
    st.subheader("Private Providers")
    default_private = ["David Spongberg", "Kaelyn Sheen", "Kaelyn Scheen", "Sharah Heckman"]
    private_names_text = st.text_area(
        "Always mark these names as Private (comma-separated)",
        ", ".join(default_private)
    )

    st.subheader("Spanish-speaking Names (optional edits)")
    default_spanish_interviewers = [
        "Cintia Martinez", "Liliana Pizana", "Emma Thomae", "Ben Aguilar",
        "Cesar Villarreal", "Teresa Castano", "Dr. Alvarez-Sanders"
    ]
    default_spanish_testers = ["Belinda Castillo", "Noemi Martinez"]
    spanish_interviewers_text = st.text_area("Spanish-speaking Interviewers", ", ".join(default_spanish_interviewers))
    spanish_testers_text = st.text_area("Spanish-speaking Testers", ", ".join(default_spanish_testers))

    st.divider()
    build_btn = st.button("🚀 Build Schedule")

# ---------------------- Helpers ----------------------

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
    # Some files call it "SOLO" under Area
    if r == "solo":
        return "solo"
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

# ---------------------- Core Logic ----------------------

PAIRING_PREFERENCES = {
    "Lakaii Jones": "Virginia Parker",
    "Shivani Bhakta": "Bobby Perez",
    "Liliana Pizana": "Emma Thomae",
    "Lyn McDonald": "Ed Howarth",
}

# Master rules (encoded per your memory instructions):
# - Sites: SA Live, Houston Live, Lubbock Live, Telehealth
# - Dyads: interviewer + LPA (or psychometrician in SA, max 1/day)
# - Solo providers remain unpaired
# - Lubbock fixed: Gaston + Tara (Mon/Wed), Gaston + Kaelyn Sheen (psychometrician) Tuesdays; allow Maura override if she’s present
# - Spanish is preferred but NOT required; priority: same site -> same day -> same modality
# - Psychometricians: treated like LPAs but only in SA, max one per day (except Lubbock Tuesday exception with Kaelyn)


def build_pairings_from_roster(roster_df: pd.DataFrame, month_label: str,
                               private_names: set, spanish_interviewers: set, spanish_testers: set):
    roster = roster_df.copy()
    # Normalize columns
    roster = roster.rename(columns={"Role": "Area"})
    if "Start Date" not in roster.columns:
        raise ValueError("Roster missing 'Start Date' column")

    roster["Start Date"] = pd.to_datetime(roster["Start Date"], errors="coerce")
    roster["RoleNorm"] = roster["Area"].apply(norm_role)

    staffing = defaultdict(list)
    for _, row in roster.iterrows():
        staffing[(row["Start Date"], str(row["Location"]).strip())].append((row["RoleNorm"], row["Team Member"]))

    final_rows = []
    violations = []

    for (date, loc), entries in staffing.items():
        if pd.isna(date):
            continue
        interviewers = [n for r, n in entries if r == "interviewer"]
        lpas = [n for r, n in entries if r == "lpa"]
        psychs = [n for r, n in entries if r == "psychometrician"]
        solos = [n for r, n in entries if r == "solo"]

        # Solo coverage
        for s in solos:
            final_rows.append({
                "Date": date, "Location": loc, "Interviewer": s, "Tester": "(SOLO)",
                "Notes": "Solo provider—no pairing required", "Month": month_label,
                "PsychometricianPair": False
            })

        if not interviewers:
            continue

        day_name = pd.to_datetime(date).strftime("%A")

        # Lubbock fixed rules for Gaston Rougeaux-Burnes
        if loc.lower() == "lubbock":
            gaston = next((i for i in interviewers if "Gaston" in i), None)
            if gaston:
                if day_name == "Tuesday":
                    # Prefer Kaelyn (psychometrician) on Tuesdays
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
                    # Mon/Wed default Tara; allow Maura override if present
                    if "Maura Brown" in lpas:
                        final_rows.append({
                            "Date": date, "Location": loc, "Interviewer": gaston, "Tester": "Maura Brown",
                            "Notes": "Lubbock note: Maura Brown paired with Gaston", "Month": month_label,
                            "PsychometricianPair": False
                        })
                        lpas.remove("Maura Brown"); interviewers.remove(gaston)
                    elif "Tara Stevens" in lpas:
                        final_rows.append({
                            "Date": date, "Location": loc, "Interviewer": gaston, "Tester": "Tara Stevens",
                            "Notes": "Lubbock fixed pairing", "Month": month_label,
                            "PsychometricianPair": False
                        })
                        lpas.remove("Tara Stevens"); interviewers.remove(gaston)
                    else:
                        violations.append({"Month": month_label, "Date": to_mmddyyyy(date), "Location": loc,
                                           "Issue": "Lubbock Mon/Wed: Missing Tara/Maura for Gaston"})

        # Dual‑tester rule for Amanda Eberle (enforced whenever she's scheduled; preference: Maura Brown + Dmitriy Lazarevich)
        if any(i == "Amanda Eberle" for i in interviewers):
            preferred = ["Maura Brown", "Dmitriy Lazarevich"]
            assigned = []
            # Try preferred first
            for p in preferred:
                if p in lpas and p not in assigned:
                    assigned.append(p)
                    lpas.remove(p)
                if len(assigned) == 2:
                    break
            # Fill remaining from any available LPAs on this site/day
            while len(assigned) < 2 and lpas:
                nxt = lpas.pop(0)
                if nxt not in assigned:
                    assigned.append(nxt)
            # Emit rows (two tester events for Eberle)
            for t in assigned:
                final_rows.append({
                    "Date": date, "Location": loc, "Interviewer": "Amanda Eberle", "Tester": t,
                    "Notes": "Dual tester requirement (Eberle)", "Month": month_label,
                    "PsychometricianPair": False
                })
            # Remove Amanda from pool and log violation if short
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

        # General pairing rules (Spanish optional; SA psychometrician cap = 1/day)
        used_psych = False
        for interviewer in list(interviewers):
            paired = None
            note = ""
            psych_pair = False

            pref = PAIRING_PREFERENCES.get(interviewer)
            if pref and pref in lpas:
                paired = pref; lpas.remove(pref); note = "Preference pairing"
            elif loc == "(SA) Stonebridge Behavioral Health" and psychs and not used_psych:
                # Prefer psychometricians who are in private list if present
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

        # Spanish tag: only when BOTH are Spanish-speaking
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

# ---------------------- Run when clicked ----------------------
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
        # October violations are not recomputed unless a raw roster is also provided

    # Handle uploaded monthly rosters (e.g., November, December)
    for up in roster_files or []:
        df = pd.read_csv(up)
        # Heuristic month label from min Start Date
        month_label = pd.to_datetime(df["Start Date"], errors="coerce").min()
        month_label = month_label.strftime("%B") if pd.notna(month_label) else "Month"
        pairs_df, viol_df = build_pairings_from_roster(df, month_label, private_names, spanish_interviewers, spanish_testers)
        monthly_pairs.append(pairs_df)
        if not viol_df.empty:
            monthly_violations.append(viol_df)

    if not monthly_pairs:
        st.error("No input files provided. Upload at least one roster or the October pairings.")
        st.stop()

    # Combine
    all_pairs = pd.concat(monthly_pairs, ignore_index=True)

    # Build GCal all-day events (provider-first + tags)
    gcal_all = build_gcal_provider_first_from_pairs(all_pairs, private_names, spanish_interviewers, spanish_testers)

    # Sort and offer downloads
    gcal_all["Start Date Sort"] = pd.to_datetime(gcal_all["Start Date"])
    gcal_all = gcal_all.sort_values(["Start Date Sort", "Subject"]).drop(columns=["Start Date Sort"])    

    st.subheader("All Appointments – Google Calendar CSV")
    st.dataframe(gcal_all.head(25))

    gcal_bytes = gcal_all.to_csv(index=False, encoding="utf-8", quoting=csv.QUOTE_MINIMAL).encode("utf-8")
    st.download_button(
        label="⬇️ Download ALL appointments (Oct–Dec) – Google Calendar CSV",
        data=gcal_bytes,
        file_name="Stonebridge_ShiftPairings_All_GCal.csv",
        mime="text/csv"
    )

    # Violations
    if monthly_violations:
        viol_all = pd.concat(monthly_violations, ignore_index=True)
        viol_all = viol_all.sort_values(["Month", "Date", "Location", "Issue"]) if not viol_all.empty else viol_all
    else:
        viol_all = pd.DataFrame(columns=["Month", "Date", "Location", "Issue"])    

    st.subheader("Gaps & Violations – CSV")
    st.dataframe(viol_all.head(25))
    viol_bytes = viol_all.to_csv(index=False, encoding="utf-8", quoting=csv.QUOTE_MINIMAL).encode("utf-8")
    st.download_button(
        label="⬇️ Download Gaps & Violations (Oct–Dec)",
        data=viol_bytes,
        file_name="Stonebridge_ShiftPairings_Gaps_Violations_OctNovDec.csv",
        mime="text/csv"
    )

    st.success("Done! Import the ALL appointments CSV into Google Calendar as all-day events.")
