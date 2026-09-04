"""Generates the downloadable/emailed PDF diagnostic report for a Prediction.

Kept dependency-light on purpose: reportlab is pure-Python (no system libs
like wkhtmltopdf/weasyprint need), so it installs cleanly on both Windows dev
machines and Railway's Linux build.
"""
import os
from io import BytesIO

from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.lib.units import cm
from reportlab.platypus import (
    SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle, Image, HRFlowable
)

BRAND_BLUE = colors.HexColor("#1a237e")
DANGER_RED = colors.HexColor("#c62828")
SAFE_GREEN = colors.HexColor("#2e7d32")
LIGHT_GREY = colors.HexColor("#666666")


def generate_prediction_pdf(prediction, patient):
    """Build the PDF report for one Prediction and return it as bytes.

    `prediction` and `patient` are the SQLAlchemy ORM rows from app_fixed.py
    (Prediction / Patient). Never raises for a missing/unreadable image - the
    report is still generated, just without the embedded photo.
    """
    buffer = BytesIO()
    doc = SimpleDocTemplate(
        buffer, pagesize=A4,
        topMargin=2 * cm, bottomMargin=2 * cm,
        leftMargin=2 * cm, rightMargin=2 * cm,
    )

    styles = getSampleStyleSheet()
    title_style = ParagraphStyle(
        "ReportTitle", parent=styles["Title"], textColor=BRAND_BLUE, fontSize=20,
    )
    subtitle_style = ParagraphStyle(
        "ReportSubtitle", parent=styles["Normal"], textColor=LIGHT_GREY, fontSize=10,
    )
    section_style = ParagraphStyle(
        "Section", parent=styles["Heading2"], textColor=BRAND_BLUE, fontSize=13,
        spaceBefore=14, spaceAfter=6,
    )
    body_style = styles["Normal"]

    is_malaria = (prediction.result == "Malaria Detected")
    result_color = DANGER_RED if is_malaria else SAFE_GREEN

    story = []

    story.append(Paragraph("Malaria Detection System", title_style))
    story.append(Paragraph("AI-Assisted Diagnostic Report", subtitle_style))
    story.append(Spacer(1, 4))
    story.append(HRFlowable(width="100%", color=BRAND_BLUE, thickness=1.2))
    story.append(Spacer(1, 12))

    # ---- Patient details ----
    story.append(Paragraph("Patient Information", section_style))
    patient_rows = [
        ["Name", patient.name if patient else "N/A"],
        ["Age", str(patient.age) if patient and patient.age else "N/A"],
        ["Gender", patient.gender if patient and patient.gender else "N/A"],
        ["Report ID", f"PRD-{prediction.id:06d}"],
    ]
    patient_table = Table(patient_rows, colWidths=[4 * cm, 10 * cm])
    patient_table.setStyle(TableStyle([
        ("FONTNAME", (0, 0), (0, -1), "Helvetica-Bold"),
        ("TEXTCOLOR", (0, 0), (0, -1), LIGHT_GREY),
        ("FONTSIZE", (0, 0), (-1, -1), 10),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 6),
        ("TOPPADDING", (0, 0), (-1, -1), 2),
    ]))
    story.append(patient_table)

    # ---- Result ----
    story.append(Paragraph("Test Result", section_style))
    result_style = ParagraphStyle(
        "ResultLine", parent=styles["Heading1"], textColor=result_color, fontSize=16,
        spaceAfter=8,
    )
    story.append(Paragraph(prediction.result or "N/A", result_style))

    score_rows = [
        ["Overall confidence", f"{prediction.probability:.1f}%" if prediction.probability is not None else "N/A"],
        ["Malaria score", f"{prediction.malaria_score:.1f}%" if prediction.malaria_score is not None else "N/A"],
        ["No-malaria score", f"{prediction.no_malaria_score:.1f}%" if prediction.no_malaria_score is not None else "N/A"],
        ["Tested by", prediction.staff_name or "N/A"],
        ["Date", prediction.created_at.strftime("%Y-%m-%d %H:%M") if prediction.created_at else "N/A"],
    ]
    score_table = Table(score_rows, colWidths=[4 * cm, 10 * cm])
    score_table.setStyle(TableStyle([
        ("FONTNAME", (0, 0), (0, -1), "Helvetica-Bold"),
        ("TEXTCOLOR", (0, 0), (0, -1), LIGHT_GREY),
        ("FONTSIZE", (0, 0), (-1, -1), 10),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 6),
        ("TOPPADDING", (0, 0), (-1, -1), 2),
    ]))
    story.append(score_table)

    if prediction.symptoms:
        story.append(Paragraph("Reported Symptoms", section_style))
        story.append(Paragraph(prediction.symptoms, body_style))

    # ---- Blood smear image ----
    if prediction.image_path and os.path.isfile(prediction.image_path):
        try:
            story.append(Paragraph("Analyzed Blood Smear Image", section_style))
            story.append(Image(prediction.image_path, width=6 * cm, height=6 * cm, kind="proportional"))
        except Exception:
            pass  # corrupt/unreadable image - skip rather than fail the whole report

    # ---- Footer disclaimer ----
    story.append(Spacer(1, 24))
    story.append(HRFlowable(width="100%", color=colors.HexColor("#cccccc"), thickness=0.6))
    story.append(Spacer(1, 8))
    disclaimer_style = ParagraphStyle(
        "Disclaimer", parent=styles["Normal"], textColor=LIGHT_GREY, fontSize=8,
    )
    story.append(Paragraph(
        "This report is generated by an AI-assisted screening tool and is intended to support, "
        "not replace, clinical judgment. All results must be reviewed and confirmed by a "
        "licensed medical professional before any diagnosis or treatment decision is made.",
        disclaimer_style,
    ))

    doc.build(story)
    return buffer.getvalue()
