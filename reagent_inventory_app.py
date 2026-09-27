# -*- coding: utf-8 -*-
"""
Orange Lab — Inventory & Follow-up Reagents (Active / Stock)
Streamlit application for tracking chemistry reagent kits on the BIOBASE
BK-280 analyzer: identity, calibration, item settings and stock follow-up.

Run:
    streamlit run reagent_inventory_app.py
"""

import sqlite3
import textwrap
import uuid
from datetime import date, datetime

import pandas as pd
import streamlit as st

# --------------------------------------------------------------------------
# Constants
# --------------------------------------------------------------------------

DB_PATH = "inventory.db"

MANUAL_OPTION = "➕ أخرى (إضافة يدوي)"

BK280_REAGENTS = [
    "Glucose", "Urea (BUN)", "Creatinine", "Uric Acid",
    "Total Cholesterol", "Triglycerides", "HDL-Cholesterol (Direct)",
    "LDL-Cholesterol (Direct)", "Total Protein", "Albumin",
    "Total Bilirubin", "Direct Bilirubin",
    "AST (SGOT)", "ALT (SGPT)", "ALP (Alkaline Phosphatase)",
    "GGT", "Amylase", "Lipase", "CK (CPK)", "CK-MB", "LDH",
    "Calcium", "Phosphorus (Inorganic)", "Magnesium", "Iron",
    "TIBC", "Sodium (Na)", "Potassium (K)", "Chloride (Cl)",
    "CRP", "CRP (Quantitative)", "RF (Rheumatoid Factor)", "ASO",
    "Lactate", "Ammonia (NH3)", "Bicarbonate (CO2)",
    "Ferritin", "Microalbumin", "Cholinesterase", "Homocysteine",
    "HbA1c", "D-Dimer", "Lipase (Colorimetric)", "Pre-Albumin",
    "Transferrin", "Immunoglobulin G (IgG)", "Immunoglobulin A (IgA)",
    "Immunoglobulin M (IgM)", "Complement C3", "Complement C4",
]

COMPANIES = [
    "Biobase", "Zybio", "Spectrum", "Elitech", "Agappe", "Biomed",
    "Ultracare", "Spinreact", "GPL", "Genesis",
]

SPECIMEN_TYPES = ["Serum", "Plasma EDTA", "Plasma Heparin", "Whole Blood"]

CALIBRATION_TYPES = [
    "Factor (K value)", "Two Point End", "Linear (Multi-Standard)",
    "Non-Linear (Multi-Standard)", "Blank Only",
]

STATUS_OPTIONS = ["Active", "Stock"]

CUSTOM_CATEGORIES = ("reagent", "company", "specimen")


# --------------------------------------------------------------------------
# Database helpers
# --------------------------------------------------------------------------

def get_conn():
    conn = sqlite3.connect(DB_PATH, check_same_thread=False)
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS reagents (
            id TEXT PRIMARY KEY,
            reagent_name TEXT,
            company_name TEXT,
            procedure TEXT,
            specimen_type TEXT,
            lot_no TEXT,
            rec_date TEXT,
            opening_date TEXT,
            calibration_type TEXT,
            factor_value TEXT,
            sample_vol TEXT,
            r1_vol TEXT,
            r2_vol TEXT,
            pri_start TEXT,
            sub_start TEXT,
            pri_end TEXT,
            sub_end TEXT,
            kit_expiry_date TEXT,
            expected_tests TEXT,
            stock_next_kit TEXT,
            status TEXT,
            pri_wavelength TEXT,
            sub_wavelength TEXT,
            created_at TEXT,
            updated_at TEXT
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS custom_options (
            category TEXT,
            value TEXT,
            UNIQUE(category, value)
        )
        """
    )
    # migration for databases created before wavelength fields existed
    existing_cols = {row[1] for row in conn.execute("PRAGMA table_info(reagents)")}
    for col in ("pri_wavelength", "sub_wavelength"):
        if col not in existing_cols:
            conn.execute(f"ALTER TABLE reagents ADD COLUMN {col} TEXT")
    conn.commit()
    return conn


conn = get_conn()


def add_custom_option(category: str, value: str):
    value = (value or "").strip()
    if not value:
        return
    conn.execute(
        "INSERT OR IGNORE INTO custom_options (category, value) VALUES (?, ?)",
        (category, value),
    )
    conn.commit()


def get_custom_options(category: str):
    rows = conn.execute(
        "SELECT value FROM custom_options WHERE category = ? ORDER BY value",
        (category,),
    ).fetchall()
    return [r[0] for r in rows]


def options_for(category: str, base_list):
    return base_list + get_custom_options(category) + [MANUAL_OPTION]


def save_record(data: dict, record_id: str = None):
    now = datetime.now().isoformat(timespec="seconds")
    if record_id:
        data["updated_at"] = now
        cols = ", ".join(f"{k} = ?" for k in data)
        conn.execute(
            f"UPDATE reagents SET {cols} WHERE id = ?",
            (*data.values(), record_id),
        )
    else:
        record_id = str(uuid.uuid4())
        data["id"] = record_id
        data["created_at"] = now
        data["updated_at"] = now
        cols = ", ".join(data.keys())
        placeholders = ", ".join("?" for _ in data)
        conn.execute(
            f"INSERT INTO reagents ({cols}) VALUES ({placeholders})",
            tuple(data.values()),
        )
    conn.commit()
    return record_id


def delete_record(record_id: str):
    conn.execute("DELETE FROM reagents WHERE id = ?", (record_id,))
    conn.commit()


def load_all():
    df = pd.read_sql_query(
        "SELECT * FROM reagents ORDER BY reagent_name, status, lot_no", conn
    )
    return df


def get_record(record_id: str):
    row = conn.execute(
        "SELECT * FROM reagents WHERE id = ?", (record_id,)
    ).fetchone()
    if not row:
        return None
    cols = [d[0] for d in conn.execute("SELECT * FROM reagents LIMIT 0").description]
    return dict(zip(cols, row))


# --------------------------------------------------------------------------
# UI helpers
# --------------------------------------------------------------------------

def picker(label, category, base_list, key_prefix, current_value=""):
    """A selectbox with a manual-entry fallback. Returns the final text value."""
    opts = options_for(category, base_list)
    default_idx = 0
    if current_value and current_value in opts:
        default_idx = opts.index(current_value)
    elif current_value:
        default_idx = len(opts) - 1  # treat as manual
    choice = st.selectbox(label, opts, index=default_idx, key=f"{key_prefix}_select")
    if choice == MANUAL_OPTION:
        manual_default = current_value if current_value not in base_list else ""
        manual = st.text_input(
            f"{label} (إضافة يدوي)", value=manual_default, key=f"{key_prefix}_manual"
        )
        return manual.strip()
    return choice


def d(value):
    """Parse a stored ISO date string back into a date object, or today."""
    if value:
        try:
            return datetime.fromisoformat(value).date()
        except ValueError:
            pass
    return date.today()


# --------------------------------------------------------------------------
# Add / Edit form
# --------------------------------------------------------------------------

def render_form(edit_id=None):
    existing = get_record(edit_id) if edit_id else {}

    st.subheader("بيانات الكيت" if not edit_id else "تعديل بيانات الكيت")

    reagent_name = picker(
        "1) Reagent name", "reagent", BK280_REAGENTS, "reagent",
        existing.get("reagent_name", ""),
    )
    st.divider()

    company_name = picker(
        "2) Company name", "company", COMPANIES, "company",
        existing.get("company_name", ""),
    )
    procedure = st.text_area(
        "Procedure", value=existing.get("procedure", ""), key="procedure_input",
        height=90,
    )
    st.divider()

    specimen_type = picker(
        "3) Specimen Type", "specimen", SPECIMEN_TYPES, "specimen",
        existing.get("specimen_type", ""),
    )
    st.divider()

    col1, col2, col3 = st.columns(3)
    with col1:
        lot_no = st.text_input("4) Lot no.", value=existing.get("lot_no", ""))
    with col2:
        rec_date = st.date_input("5) Rec. Date", value=d(existing.get("rec_date")))
    with col3:
        opening_date = st.date_input(
            "6) Opening date", value=d(existing.get("opening_date"))
        )
    st.divider()

    col1, col2 = st.columns([2, 1])
    with col1:
        calibration_type = st.selectbox(
            "7) Calibration type", CALIBRATION_TYPES,
            index=CALIBRATION_TYPES.index(existing["calibration_type"])
            if existing.get("calibration_type") in CALIBRATION_TYPES else 0,
        )
    with col2:
        needs_factor = calibration_type in ("Factor (K value)", "Two Point End")
        factor_value = st.text_input(
            "Factor value", value=existing.get("factor_value", ""),
            disabled=not needs_factor,
            help="مطلوب فقط لو النوع Factor أو Two Point End — القيمة خاصة بالـ Lot no. المذكور أعلاه",
        )
        if not needs_factor:
            factor_value = ""
    st.divider()

    st.markdown("**8) Sample/Reagent ratio**")
    col1, col2, col3 = st.columns(3)
    with col1:
        sample_vol = st.text_input("Sample vol", value=existing.get("sample_vol", ""))
    with col2:
        r1_vol = st.text_input("R1 Vol", value=existing.get("r1_vol", ""))
    with col3:
        r2_vol = st.text_input("R2 Vol", value=existing.get("r2_vol", ""))
    st.divider()

    st.markdown("**9) Item setting**")
    wc1, wc2 = st.columns(2)
    with wc1:
        pri_wavelength = st.text_input(
            "Pri- Wavelength", value=existing.get("pri_wavelength", "")
        )
    with wc2:
        sub_wavelength = st.text_input(
            "Sub- Wavelength", value=existing.get("sub_wavelength", "")
        )

    st.markdown("**Reading points**")
    hc1, hc2 = st.columns(2)
    hc1.markdown("&nbsp;", unsafe_allow_html=True)
    hc2.markdown("&nbsp;", unsafe_allow_html=True)
    rc0, rc1, rc2 = st.columns([1, 1, 1])
    rc0.markdown(" ")
    rc1.markdown("**Pri. point**")
    rc2.markdown("**Sub points**")

    rc0, rc1, rc2 = st.columns([1, 1, 1])
    rc0.markdown("Start point")
    pri_start = rc1.text_input(
        "pri_start", value=existing.get("pri_start", ""), label_visibility="collapsed"
    )
    sub_start = rc2.text_input(
        "sub_start", value=existing.get("sub_start", ""), label_visibility="collapsed"
    )

    rc0, rc1, rc2 = st.columns([1, 1, 1])
    rc0.markdown("End points")
    pri_end = rc1.text_input(
        "pri_end", value=existing.get("pri_end", ""), label_visibility="collapsed"
    )
    sub_end = rc2.text_input(
        "sub_end", value=existing.get("sub_end", ""), label_visibility="collapsed"
    )
    st.divider()

    col1, col2, col3 = st.columns(3)
    with col1:
        kit_expiry_date = st.date_input(
            "10) Kit Expiry Date", value=d(existing.get("kit_expiry_date"))
        )
    with col2:
        expected_tests = st.text_input(
            "11) Expected no. of tests", value=existing.get("expected_tests", "")
        )
    with col3:
        status = st.selectbox(
            "Status", STATUS_OPTIONS,
            index=STATUS_OPTIONS.index(existing["status"])
            if existing.get("status") in STATUS_OPTIONS else 0,
        )

    stock_next_kit = st.text_input(
        "12) Stock — اسم/لوت الكيت المتاح بعد انتهاء الكيت الحالي",
        value=existing.get("stock_next_kit", ""),
    )

    st.markdown("---")
    save_col, cancel_col = st.columns([1, 1])
    save_clicked = save_col.button(
        "💾 حفظ" if not edit_id else "💾 حفظ التعديلات", type="primary",
        use_container_width=True,
    )
    cancel_clicked = False
    if edit_id:
        cancel_clicked = cancel_col.button("إلغاء", use_container_width=True)

    if save_clicked:
        if not reagent_name or not company_name or not specimen_type:
            st.error("من فضلك أكمل Reagent name و Company name و Specimen Type.")
            return

        add_custom_option("reagent", reagent_name) if reagent_name not in BK280_REAGENTS else None
        add_custom_option("company", company_name) if company_name not in COMPANIES else None
        add_custom_option("specimen", specimen_type) if specimen_type not in SPECIMEN_TYPES else None

        data = dict(
            reagent_name=reagent_name,
            company_name=company_name,
            procedure=procedure,
            specimen_type=specimen_type,
            lot_no=lot_no,
            rec_date=rec_date.isoformat(),
            opening_date=opening_date.isoformat(),
            calibration_type=calibration_type,
            factor_value=factor_value,
            sample_vol=sample_vol,
            r1_vol=r1_vol,
            r2_vol=r2_vol,
            pri_wavelength=pri_wavelength,
            sub_wavelength=sub_wavelength,
            pri_start=pri_start,
            sub_start=sub_start,
            pri_end=pri_end,
            sub_end=sub_end,
            kit_expiry_date=kit_expiry_date.isoformat(),
            expected_tests=expected_tests,
            stock_next_kit=stock_next_kit,
            status=status,
        )
        save_record(data, record_id=edit_id)
        st.success("تم الحفظ ✅")
        st.session_state.pop("edit_id", None)
        st.rerun()

    if cancel_clicked:
        st.session_state.pop("edit_id", None)
        st.rerun()


# --------------------------------------------------------------------------
# Records table (view / edit / delete)
# --------------------------------------------------------------------------

def render_records():
    df = load_all()
    if df.empty:
        st.info("لا توجد بيانات مسجلة بعد. اذهب لتبويب «➕ إضافة» لإضافة أول كيت.")
        return

    st.subheader(f"كل السجلات ({len(df)})")
    search = st.text_input("🔍 بحث بالاسم / الشركة / اللوت", "")
    view = df.copy()
    if search:
        s = search.strip().lower()
        mask = view.apply(
            lambda r: s in str(r["reagent_name"]).lower()
            or s in str(r["company_name"]).lower()
            or s in str(r["lot_no"]).lower(),
            axis=1,
        )
        view = view[mask]

    for reagent_name, group in view.groupby("reagent_name", sort=True):
        with st.expander(f"🧪 {reagent_name}  ·  {len(group)} كيت", expanded=False):
            for _, row in group.iterrows():
                badge = "🟢 Active" if row["status"] == "Active" else "📦 Stock"
                c1, c2, c3, c4 = st.columns([3, 2, 1, 1])
                c1.markdown(
                    f"**{row['company_name']}** — Lot: `{row['lot_no'] or '—'}`"
                )
                c2.markdown(f"{badge}  ·  Exp: {row['kit_expiry_date'] or '—'}")
                if c3.button("✏️", key=f"edit_{row['id']}", help="تعديل"):
                    st.session_state["edit_id"] = row["id"]
                    st.session_state["active_tab"] = "add"
                    st.rerun()
                if c4.button("🗑️", key=f"del_{row['id']}", help="حذف"):
                    delete_record(row["id"])
                    st.rerun()

    st.markdown("---")
    csv = df.to_csv(index=False).encode("utf-8-sig")
    st.download_button(
        "⬇️ تصدير كل البيانات CSV", csv, file_name="reagent_inventory_export.csv",
        mime="text/csv",
    )


# --------------------------------------------------------------------------
# Printable view
# --------------------------------------------------------------------------

PRINT_CSS = """
<style>
@media print {
    header, .stSidebar, [data-testid="stToolbar"], [data-testid="stHeader"],
    .no-print { display: none !important; }
    .block-container { padding-top: 0 !important; }
}
.kit-card {
    border: 1px solid #e0a15a;
    border-radius: 10px;
    padding: 14px 18px;
    margin-bottom: 14px;
    page-break-inside: avoid;
    background: #fffaf4;
}
.kit-card h4 { margin: 0 0 6px 0; color: #c9601b; }
.kit-card table { width: 100%; border-collapse: collapse; font-size: 0.92rem; }
.kit-card td { padding: 3px 6px; vertical-align: top; }
.kit-card .label { color: #7a5230; font-weight: 600; width: 30%; }
.group-title {
    background: #f5822a; color: white; padding: 8px 14px; border-radius: 8px;
    margin-top: 22px; margin-bottom: 10px; font-size: 1.15rem; font-weight: 700;
}
.status-active { color: #1a8f3c; font-weight: 700; }
.status-stock { color: #7a5230; font-weight: 700; }
</style>
"""


def kit_card_html(row) -> str:
    status_class = "status-active" if row["status"] == "Active" else "status-stock"
    html = f"""
    <div class="kit-card">
        <h4>{row['company_name']} &nbsp;|&nbsp; Lot: {row['lot_no'] or '—'}
            &nbsp;·&nbsp; <span class="{status_class}">{row['status']}</span></h4>
        <table>
            <tr><td class="label">Specimen Type</td><td>{row['specimen_type']}</td>
                <td class="label">Rec. Date</td><td>{row['rec_date']}</td></tr>
            <tr><td class="label">Opening date</td><td>{row['opening_date']}</td>
                <td class="label">Kit Expiry Date</td><td>{row['kit_expiry_date']}</td></tr>
            <tr><td class="label">Calibration type</td><td>{row['calibration_type']}</td>
                <td class="label">Factor value</td><td>{row['factor_value'] or '—'}</td></tr>
            <tr><td class="label">Sample vol</td><td>{row['sample_vol']}</td>
                <td class="label">R1 / R2 Vol</td><td>{row['r1_vol']} / {row['r2_vol']}</td></tr>
            <tr><td class="label">Pri / Sub Wavelength</td>
                <td colspan="3">{row['pri_wavelength'] or '—'} / {row['sub_wavelength'] or '—'}</td></tr>
            <tr><td class="label">Reading points</td>
                <td colspan="3">
                    Pri: Start {row['pri_start'] or '—'} → End {row['pri_end'] or '—'}
                    &nbsp;|&nbsp;
                    Sub: Start {row['sub_start'] or '—'} → End {row['sub_end'] or '—'}
                </td></tr>
            <tr><td class="label">Expected no. of tests</td><td>{row['expected_tests']}</td>
                <td class="label">Stock (next kit)</td><td>{row['stock_next_kit'] or '—'}</td></tr>
            <tr><td class="label">Procedure</td><td colspan="3">{row['procedure'] or '—'}</td></tr>
        </table>
    </div>
    """
    # collapse to a single unindented block so Streamlit's markdown parser
    # treats it as one continuous raw-HTML block instead of splitting on
    # whitespace-only lines (which Markdown reinterprets as code blocks)
    lines = [line.strip() for line in textwrap.dedent(html).strip().splitlines()]
    return " ".join(line for line in lines if line)


def render_print_view():
    df = load_all()
    if df.empty:
        st.info("لا توجد بيانات لعرضها.")
        return

    st.markdown(PRINT_CSS, unsafe_allow_html=True)
    st.markdown(
        '<div class="no-print">اضغط <b>Ctrl/Cmd + P</b> من المتصفح للطباعة، '
        'أو استخدم زر الطباعة تحت.</div>',
        unsafe_allow_html=True,
    )
    st.markdown(
        "<h2 style='text-align:center;color:#c9601b;'>Reagent Inventory &amp; Lot "
        "Follow-up Sheet<br><span style='font-size:0.6em;color:#7a5230;'>(Active / "
        "Stock)</span></h2>",
        unsafe_allow_html=True,
    )
    st.caption(f"تاريخ الطباعة: {date.today().isoformat()}")

    for reagent_name, group in df.groupby("reagent_name", sort=True):
        group = group.sort_values(by="status")
        html = f'<div class="group-title">🧪 {reagent_name}</div>'
        for _, row in group.iterrows():
            html += " " + kit_card_html(row)
        st.markdown(html, unsafe_allow_html=True)

    st.markdown(
        '<div style="margin-top:40px;display:flex;justify-content:flex-end;">'
        '<div style="text-align:center;">'
        '<div style="border-top:1px solid #333;width:230px;margin:0 0 6px auto;"></div>'
        '<div style="font-weight:700;color:#333;">Dr. Hussein Ali</div>'
        "</div></div>",
        unsafe_allow_html=True,
    )

    st.markdown(
        "<button class='no-print' onclick='window.print()' "
        "style='padding:8px 18px;border-radius:6px;border:none;"
        "background:#f5822a;color:white;font-weight:700;cursor:pointer;'>"
        "🖨️ طباعة</button>",
        unsafe_allow_html=True,
    )


# --------------------------------------------------------------------------
# Main
# --------------------------------------------------------------------------

def main():
    st.set_page_config(
        page_title="Orange Lab — Reagent Inventory", page_icon="🧪", layout="wide"
    )
    st.markdown(
        "<h1 style='color:#c9601b;'>🧪 Reagent Inventory &amp; Lot Follow-up Sheet "
        "<span style='font-size:0.55em;color:#7a5230;'>(Active / Stock)</span></h1>",
        unsafe_allow_html=True,
    )
    st.caption("Orange Lab — BIOBASE BK-280 Chemistry Reagents Tracking")

    edit_id = st.session_state.get("edit_id")
    default_tab = 0 if not edit_id else 0

    tab_add, tab_records, tab_print = st.tabs(
        ["➕ إضافة / تعديل", "📋 كل السجلات", "🖨️ عرض للطباعة"]
    )

    with tab_add:
        render_form(edit_id=edit_id)

    with tab_records:
        render_records()

    with tab_print:
        render_print_view()


if __name__ == "__main__":
    main()
