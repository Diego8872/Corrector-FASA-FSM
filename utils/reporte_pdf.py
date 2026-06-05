from reportlab.lib.pagesizes import A4
from reportlab.lib import colors
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.lib.units import cm
from reportlab.platypus import (
    SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle,
    HRFlowable, PageBreak
)
from reportlab.lib.enums import TA_CENTER, TA_LEFT, TA_RIGHT
from datetime import datetime
import io

# ─── COLORES INTERLOG ────────────────────────────────────────────────────────
AZUL_OSCURO = colors.HexColor("#1F3864")
AZUL_MEDIO  = colors.HexColor("#2E75B6")
AZUL_CLARO  = colors.HexColor("#D6E4F7")
ROJO        = colors.HexColor("#C00000")
NARANJA     = colors.HexColor("#ED7D31")
VERDE       = colors.HexColor("#70AD47")
GRIS_CLARO  = colors.HexColor("#F2F2F2")
GRIS_TEXTO  = colors.HexColor("#595959")
BLANCO      = colors.white


def generar_reporte_pdf(todos_resultados: list, config: dict, numero_di: str = "") -> bytes:
    buffer = io.BytesIO()

    doc = SimpleDocTemplate(
        buffer,
        pagesize=A4,
        rightMargin=1.5*cm,
        leftMargin=1.5*cm,
        topMargin=2*cm,
        bottomMargin=2*cm,
        title=f"Corrector FASA/FSM — {numero_di}",
    )

    styles = getSampleStyleSheet()
    story = []

    # ─── ESTILOS ──────────────────────────────────────────────────────────────
    estilo_titulo = ParagraphStyle("titulo",
        fontSize=18, fontName="Helvetica-Bold",
        textColor=AZUL_OSCURO, spaceAfter=4)

    estilo_subtitulo = ParagraphStyle("subtitulo",
        fontSize=10, fontName="Helvetica",
        textColor=GRIS_TEXTO, spaceAfter=2)

    estilo_seccion = ParagraphStyle("seccion",
        fontSize=12, fontName="Helvetica-Bold",
        textColor=AZUL_OSCURO, spaceBefore=14, spaceAfter=6)

    estilo_normal = ParagraphStyle("normal",
        fontSize=8, fontName="Helvetica",
        textColor=colors.black, leading=11)

    estilo_celda = ParagraphStyle("celda",
        fontSize=7.5, fontName="Helvetica",
        textColor=colors.black, leading=10)

    # ─── ENCABEZADO ───────────────────────────────────────────────────────────
    story.append(Paragraph("🔍 Corrector de Despachos FASA/FSM", estilo_titulo))
    story.append(Paragraph("INTERLOG Comercio Exterior — Reporte de Validación Automática", estilo_subtitulo))
    story.append(HRFlowable(width="100%", thickness=2, color=AZUL_OSCURO, spaceAfter=10))

    # ─── INFO DEL DESPACHO ────────────────────────────────────────────────────
    fecha = datetime.now().strftime("%d/%m/%Y %H:%M")
    empresa = config.get("empresa", "—")
    regimen = config.get("regimen", "—")
    aduana = config.get("aduana", "—")

    info_data = [
        ["Empresa", empresa, "Régimen", regimen],
        ["Aduana", aduana, "Fecha análisis", fecha],
    ]
    if numero_di:
        info_data.insert(0, ["Nº Despacho", numero_di, "", ""])

    tabla_info = Table(info_data, colWidths=[3*cm, 7*cm, 3*cm, 5*cm])
    tabla_info.setStyle(TableStyle([
        ("FONTNAME", (0,0), (-1,-1), "Helvetica"),
        ("FONTSIZE", (0,0), (-1,-1), 8),
        ("FONTNAME", (0,0), (0,-1), "Helvetica-Bold"),
        ("FONTNAME", (2,0), (2,-1), "Helvetica-Bold"),
        ("TEXTCOLOR", (0,0), (0,-1), AZUL_OSCURO),
        ("TEXTCOLOR", (2,0), (2,-1), AZUL_OSCURO),
        ("BACKGROUND", (0,0), (-1,-1), GRIS_CLARO),
        ("ROWBACKGROUNDS", (0,0), (-1,-1), [GRIS_CLARO, BLANCO]),
        ("BOX", (0,0), (-1,-1), 0.5, colors.lightgrey),
        ("INNERGRID", (0,0), (-1,-1), 0.3, colors.lightgrey),
        ("TOPPADDING", (0,0), (-1,-1), 5),
        ("BOTTOMPADDING", (0,0), (-1,-1), 5),
        ("LEFTPADDING", (0,0), (-1,-1), 6),
    ]))
    story.append(tabla_info)
    story.append(Spacer(1, 14))

    # ─── RESUMEN EJECUTIVO ────────────────────────────────────────────────────
    errores  = [r for r in todos_resultados if r["nivel"] == "ERROR"]
    alertas  = [r for r in todos_resultados if r["nivel"] == "ALERTA"]
    oks      = [r for r in todos_resultados if r["nivel"] == "OK"]

    story.append(Paragraph("Resumen Ejecutivo", estilo_seccion))

    resumen_data = [
        ["", "Cantidad", "Descripción"],
        ["❌  ERRORES",   str(len(errores)),  "Inconsistencias críticas que deben corregirse antes de oficializar"],
        ["⚠️  ALERTAS",   str(len(alertas)),  "Situaciones a verificar — pueden ser correctas según el caso"],
        ["✅  OK",        str(len(oks)),       "Validaciones superadas correctamente"],
    ]

    col_widths = [3.5*cm, 2*cm, 12.5*cm]
    tabla_resumen = Table(resumen_data, colWidths=col_widths)
    tabla_resumen.setStyle(TableStyle([
        # Header
        ("BACKGROUND", (0,0), (-1,0), AZUL_OSCURO),
        ("TEXTCOLOR", (0,0), (-1,0), BLANCO),
        ("FONTNAME", (0,0), (-1,0), "Helvetica-Bold"),
        ("FONTSIZE", (0,0), (-1,0), 8),
        # Filas
        ("FONTNAME", (0,1), (0,-1), "Helvetica-Bold"),
        ("FONTSIZE", (0,1), (-1,-1), 8),
        ("BACKGROUND", (0,1), (-1,1), colors.HexColor("#FDECEA")),  # ERROR
        ("BACKGROUND", (0,2), (-1,2), colors.HexColor("#FFF8E1")),  # ALERTA
        ("BACKGROUND", (0,3), (-1,3), colors.HexColor("#F1F8E9")),  # OK
        ("TEXTCOLOR", (0,1), (0,1), ROJO),
        ("TEXTCOLOR", (0,2), (0,2), NARANJA),
        ("TEXTCOLOR", (0,3), (0,3), VERDE),
        ("ALIGN", (1,0), (1,-1), "CENTER"),
        ("FONTNAME", (1,1), (1,-1), "Helvetica-Bold"),
        ("FONTSIZE", (1,1), (1,-1), 12),
        ("TEXTCOLOR", (1,1), (1,1), ROJO),
        ("TEXTCOLOR", (1,2), (1,2), NARANJA),
        ("TEXTCOLOR", (1,3), (1,3), VERDE),
        ("BOX", (0,0), (-1,-1), 0.5, colors.lightgrey),
        ("INNERGRID", (0,0), (-1,-1), 0.3, colors.lightgrey),
        ("TOPPADDING", (0,0), (-1,-1), 6),
        ("BOTTOMPADDING", (0,0), (-1,-1), 6),
        ("LEFTPADDING", (0,0), (-1,-1), 8),
        ("VALIGN", (0,0), (-1,-1), "MIDDLE"),
    ]))
    story.append(tabla_resumen)
    story.append(Spacer(1, 14))

    # ─── FUNCIÓN TABLA DETALLE ────────────────────────────────────────────────
    def tabla_detalle(datos: list, color_fila, color_texto_nivel):
        if not datos:
            story.append(Paragraph("Sin resultados en esta categoría.", estilo_normal))
            return

        header = [
            Paragraph("<b>Ítem</b>", estilo_celda),
            Paragraph("<b>Campo</b>", estilo_celda),
            Paragraph("<b>Mensaje</b>", estilo_celda),
        ]
        rows = [header]
        for r in datos:
            rows.append([
                Paragraph(str(r.get("item", "")), estilo_celda),
                Paragraph(str(r.get("campo", "")), estilo_celda),
                Paragraph(str(r.get("mensaje", "")), estilo_celda),
            ])

        t = Table(rows, colWidths=[1.8*cm, 4.2*cm, 12*cm], repeatRows=1)
        t.setStyle(TableStyle([
            ("BACKGROUND", (0,0), (-1,0), AZUL_OSCURO),
            ("TEXTCOLOR", (0,0), (-1,0), BLANCO),
            ("FONTNAME", (0,0), (-1,0), "Helvetica-Bold"),
            ("FONTSIZE", (0,0), (-1,-1), 7.5),
            ("ROWBACKGROUNDS", (0,1), (-1,-1), [color_fila, BLANCO]),
            ("BOX", (0,0), (-1,-1), 0.5, colors.lightgrey),
            ("INNERGRID", (0,0), (-1,-1), 0.3, colors.lightgrey),
            ("TOPPADDING", (0,0), (-1,-1), 4),
            ("BOTTOMPADDING", (0,0), (-1,-1), 4),
            ("LEFTPADDING", (0,0), (-1,-1), 5),
            ("VALIGN", (0,0), (-1,-1), "TOP"),
        ]))
        story.append(t)

    # ─── ERRORES ──────────────────────────────────────────────────────────────
    if errores:
        story.append(Paragraph("❌ Errores — Corrección obligatoria", estilo_seccion))
        tabla_detalle(errores, colors.HexColor("#FDECEA"), ROJO)
        story.append(Spacer(1, 10))

    # ─── ALERTAS ──────────────────────────────────────────────────────────────
    if alertas:
        story.append(Paragraph("⚠️ Alertas — Verificar antes de oficializar", estilo_seccion))
        tabla_detalle(alertas, colors.HexColor("#FFF8E1"), NARANJA)
        story.append(Spacer(1, 10))

    # ─── OK ───────────────────────────────────────────────────────────────────
    if oks:
        story.append(Paragraph("✅ Validaciones correctas", estilo_seccion))
        tabla_detalle(oks, colors.HexColor("#F1F8E9"), VERDE)

    # ─── PIE DE PÁGINA ────────────────────────────────────────────────────────
    def pie_pagina(canvas, doc):
        canvas.saveState()
        canvas.setFont("Helvetica", 7)
        canvas.setFillColor(GRIS_TEXTO)
        canvas.drawString(1.5*cm, 1.2*cm, f"INTERLOG Comercio Exterior — Corrector FASA/FSM — {fecha}")
        canvas.drawRightString(A4[0] - 1.5*cm, 1.2*cm, f"Página {doc.page}")
        canvas.setStrokeColor(AZUL_OSCURO)
        canvas.setLineWidth(0.5)
        canvas.line(1.5*cm, 1.5*cm, A4[0] - 1.5*cm, 1.5*cm)
        canvas.restoreState()

    doc.build(story, onFirstPage=pie_pagina, onLaterPages=pie_pagina)
    return buffer.getvalue()
