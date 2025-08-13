# Updated Stonebridge Scheduler App with Month/Year filter, Strict Year filter, and Bobby Perez fix
# Changes:
# 1. Added UI controls to filter results by month/year after uploading.
# 2. Added Strict Year filter option.
# 3. Added logic to ensure Bobby Perez is always treated as an LPA in SA Behavioral Health, never as an interviewer, and flagged if outside SA.

def build_pairings_from_roster(roster, month_label, private_names, spanish_interviewers, spanish_testers):
    # Normalize roles
    roster["RoleNorm"] = roster["Area"].apply(norm_role)
    # Force Bobby Perez to LPA (never interviewer)
    mask_bobby = roster["Team Member"].astype(str).str.strip().eq("Bobby Perez")
    roster.loc[mask_bobby, "RoleNorm"] = "lpa"
    
    staffing = defaultdict(list)
    for _, row in roster.iterrows():
        staffing[(row['Start Date'], str(row['Location']).strip())].append((row['RoleNorm'], row['Team Member']))
    
    final_rows = []
    violations = []
    for (date, loc), entries in staffing.items():
        if pd.isna(date):
            continue
        # SA-only constraint for Bobby Perez
        if any((r == "lpa" and n == "Bobby Perez" and str(loc).strip() != "(SA) Stonebridge Behavioral Health") for r, n in entries):
            violations.append({
                "Month": month_label,
                "Date": to_mmddyyyy(date),
                "Location": loc,
                "Issue": "Bobby Perez is SA-only LPA; scheduled outside SA"
            })
        # Ensure Bobby not in interviewer pool
        entries = [("lpa" if (r == "interviewer" and n == "Bobby Perez") else r, n) for r, n in entries]
        interviewers = [n for r, n in entries if r == "interviewer"]
        lpas = [n for r, n in entries if r == "lpa"]
        psychs = [n for r, n in entries if r == "psychometrician"]
        solos = [n for r, n in entries if r == "solo"]
        if "Bobby Perez" in interviewers:
            interviewers.remove("Bobby Perez")
            if "Bobby Perez" not in lpas:
                lpas.append("Bobby Perez")
        # ...rest of pairing logic...
    return pd.DataFrame(final_rows), pd.DataFrame(violations)

# After generating gcal_all and viol_all, add month/year and strict year filter UI
st.subheader("Export Filters")
year_input = st.text_input("Year (optional, e.g., 2026)", value="")
strict_year = st.checkbox("Strict year filter (only include rows in above year)", value=False)

# --- Strict year controls inserted ---
col_y1, col_y2 = st.columns([1,3])
with col_y1:
    strict_year = st.checkbox("Strict year filter", value=False, help="Exclude rows not in the year you specify.")
with col_y2:
    year_input = st.text_input("Year (e.g., 2026)", value="", help="Leave blank to include all years.")

# Pre-filter gcal_all (and viol_all if present) by year so month list reflects chosen year
if year_input.strip():
    try:
        _yr = int(year_input)
        _gd = pd.to_datetime(gcal_all['Start Date'], errors='coerce')
        _gmask = _gd.dt.year == _yr
        if strict_year:
            gcal_all = gcal_all[_gmask]
        elif not _gmask.all():
            st.info(f"Some appointments fall outside {_yr}. Enable 'Strict year filter' to exclude them.")
        if 'viol_all' in locals() and not viol_all.empty and 'Date' in viol_all.columns:
            _vd = pd.to_datetime(viol_all['Date'], errors='coerce')
            _vmask = _vd.dt.year == _yr
            if strict_year:
                viol_all = viol_all[_vmask]
            elif not _vmask.all():
                st.info("Violations include dates outside your year; enable Strict year to exclude.")
    except Exception:
        st.warning("Year filter ignored: please enter a 4-digit year (e.g., 2026).")

# --- Month/Year multiselect ---
months_in_data = sorted(pd.to_datetime(gcal_all['Start Date']).dt.to_period('M').astype(str).unique())
pretty_months = {m: pd.Period(m).to_timestamp().strftime('%b-%Y') for m in months_in_data}
selected_months = st.multiselect(
    'Filter by Month/Year',
    options=months_in_data,
    format_func=lambda x: pretty_months[x],
    default=months_in_data
)

# Apply year filter if given
if year_input.strip():
    try:
        year_val = int(year_input)
        mask_year = pd.to_datetime(gcal_all['Start Date']).dt.year == year_val
        if strict_year:
            gcal_all = gcal_all[mask_year]
            if not viol_all.empty and 'Date' in viol_all.columns:
                viol_all = viol_all[pd.to_datetime(viol_all['Date'], errors='coerce').dt.year == year_val]
        else:
            if not mask_year.all():
                st.warning(f"Some rows are outside {year_val}. Enable strict filter to exclude them.")
    except ValueError:
        st.warning("Year filter ignored. Please enter a 4-digit year.")

# Apply month filter
if selected_months:
    gcal_all = gcal_all[pd.to_datetime(gcal_all['Start Date']).dt.to_period('M').astype(str).isin(selected_months)]
    if not viol_all.empty and 'Date' in viol_all.columns:
        viol_all = viol_all[pd.to_datetime(viol_all['Date'], errors='coerce').dt.to_period('M').astype(str).isin(selected_months)]
