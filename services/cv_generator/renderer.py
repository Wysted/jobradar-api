from pathlib import Path
from typing import Any

from reportlab.lib import colors
from reportlab.lib.enums import TA_CENTER, TA_JUSTIFY, TA_LEFT, TA_RIGHT
from reportlab.lib.pagesizes import LETTER
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import cm
from reportlab.platypus import (
    HRFlowable,
    Indenter,
    ListFlowable,
    Paragraph,
    SimpleDocTemplate,
    Spacer,
    Table,
    TableStyle,
)

FONT_BASE = "Times-Roman"
FONT_BOLD = "Times-Bold"
FONT_ITALIC = "Times-Italic"

COLOR_BLACK = colors.HexColor("#000000")

BULLET_INDENT = 18
MAX_PAGES = 2
FIT_SCALES = (1.0, 0.95, 0.92, 0.88)


def build_styles(scale: float = 1.0) -> dict[str, ParagraphStyle]:
    s = scale
    base = getSampleStyleSheet()["Normal"]
    base.fontName = FONT_BASE
    base.fontSize = 10 * s
    base.leading = 13 * s

    return {
        "name": ParagraphStyle(
            "name", parent=base, fontName=FONT_BOLD,
            fontSize=16 * s, alignment=TA_CENTER,
            spaceAfter=6 * s, leading=19 * s,
        ),
        "contact": ParagraphStyle(
            "contact", parent=base, fontSize=10 * s,
            alignment=TA_CENTER, spaceAfter=8 * s, leading=13 * s,
        ),
        "section": ParagraphStyle(
            "section", parent=base, fontName=FONT_BOLD,
            fontSize=11 * s, alignment=TA_LEFT,
            spaceBefore=12 * s, spaceAfter=2 * s,
            textTransform="uppercase",
        ),
        "perfil": ParagraphStyle(
            "perfil", parent=base, fontSize=10 * s,
            alignment=TA_JUSTIFY, leading=13.5 * s,
            spaceBefore=3 * s,
        ),
        "org_left": ParagraphStyle(
            "org_left", parent=base, fontName=FONT_BOLD,
            fontSize=10.5 * s, alignment=TA_LEFT,
            spaceBefore=4 * s, leading=13 * s,
        ),
        "loc_right": ParagraphStyle(
            "loc_right", parent=base, fontName=FONT_ITALIC,
            fontSize=10.5 * s, alignment=TA_RIGHT,
            spaceBefore=4 * s, leading=13 * s,
        ),
        "role_left": ParagraphStyle(
            "role_left", parent=base, fontName=FONT_ITALIC,
            fontSize=10.5 * s, alignment=TA_LEFT, leading=13 * s,
        ),
        "date_right": ParagraphStyle(
            "date_right", parent=base, fontSize=10.5 * s,
            alignment=TA_RIGHT, leading=13 * s,
        ),
        "bullet": ParagraphStyle(
            "bullet", parent=base, fontSize=10 * s,
            leading=13.5 * s, alignment=TA_JUSTIFY,
        ),
        "skill_line": ParagraphStyle(
            "skill_line", parent=base, fontSize=10 * s,
            leading=13.5 * s, alignment=TA_LEFT,
            spaceBefore=2 * s,
        ),
    }


def _contact_line(header: dict[str, Any]) -> str:
    email = header["email"]
    email_link = f'<link href="mailto:{email}"><u>{email}</u></link>'
    parts = [header["location"], header["phone"], email_link]
    for link in header.get("links", []):
        parts.append(
            f'<link href="{link["url"]}"><u>{link["label"]}</u></link>'
        )
    return " | ".join(parts)


def _two_col(
    left: str,
    right: str,
    styles: dict[str, ParagraphStyle],
    left_style: str,
    right_style: str,
) -> Table:
    tbl = Table(
        [[Paragraph(left, styles[left_style]), Paragraph(right, styles[right_style])]],
        colWidths=[11.5 * cm, 5.5 * cm],
    )
    tbl.setStyle(TableStyle([
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("LEFTPADDING", (0, 0), (-1, -1), 0),
        ("RIGHTPADDING", (0, 0), (-1, -1), 0),
        ("TOPPADDING", (0, 0), (-1, -1), 0),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 0),
    ]))
    return tbl


def _section(title: str, styles: dict[str, ParagraphStyle]) -> list:
    return [
        Paragraph(title.upper(), styles["section"]),
        HRFlowable(width="100%", thickness=0.6, color=COLOR_BLACK,
                   spaceBefore=0, spaceAfter=2),
    ]


def _bullets(items: list[str], styles: dict[str, ParagraphStyle]) -> list:
    paragraphs = [Paragraph(text, styles["bullet"]) for text in items]
    lst = ListFlowable(
        paragraphs,
        bulletType="bullet",
        start="•",
        bulletFontSize=7,
        leftIndent=14,
        bulletIndent=2,
        spaceBefore=3,
        spaceAfter=3,
    )
    return [Indenter(left=BULLET_INDENT), lst, Indenter(left=-BULLET_INDENT)]


def build_story(
    header: dict[str, Any],
    content: dict[str, Any],
    styles: dict[str, ParagraphStyle],
) -> list:
    story: list = []

    story.append(Paragraph(header["name"], styles["name"]))
    story.append(Paragraph(_contact_line(header), styles["contact"]))

    if content.get("perfil"):
        story.extend(_section("Perfil", styles))
        story.append(Paragraph(content["perfil"], styles["perfil"]))

    if content.get("experiencia"):
        story.extend(_section("Experiencia", styles))
        for exp in content["experiencia"]:
            story.append(_two_col(
                exp["company"], exp.get("location", ""), styles, "org_left", "loc_right",
            ))
            story.append(_two_col(
                exp["role"], exp["dates"], styles, "role_left", "date_right",
            ))
            story.extend(_bullets(exp["bullets"], styles))
            story.append(Spacer(1, 6))

    if content.get("habilidades"):
        story.extend(_section("Habilidades Técnicas", styles))
        for skill in content["habilidades"]:
            line = f"<b>{skill['category']}:</b> {skill['items']}"
            story.append(Paragraph(line, styles["skill_line"]))

    if content.get("educacion"):
        story.extend(_section("Educación", styles))
        for edu in content["educacion"]:
            story.append(_two_col(
                edu["institution"], edu["location"], styles, "org_left", "loc_right",
            ))
            story.append(_two_col(
                edu["degree"], edu["dates"], styles, "role_left", "date_right",
            ))
            if edu.get("details"):
                story.extend(_bullets(edu["details"], styles))
            story.append(Spacer(1, 6))

    return story


def _build_doc(output: Path, header: dict[str, Any]) -> SimpleDocTemplate:
    return SimpleDocTemplate(
        str(output),
        pagesize=LETTER,
        leftMargin=1.5 * cm,
        rightMargin=1.5 * cm,
        topMargin=1.2 * cm,
        bottomMargin=1.2 * cm,
        title=f"CV - {header['name']}",
        author=header["name"],
    )


def generate_pdf(
    header: dict[str, Any],
    content: dict[str, Any],
    output_path: str | Path,
) -> Path:
    output = Path(output_path)
    output.parent.mkdir(parents=True, exist_ok=True)

    for scale in FIT_SCALES:
        doc = _build_doc(output, header)
        styles = build_styles(scale=scale)
        doc.build(build_story(header, content, styles))
        if doc.page <= MAX_PAGES:
            return output
    return output
