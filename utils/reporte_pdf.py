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
MORADO      = colors.HexColor("#5B3FA0")
BLANCO      = colors.white


def generar_reporte_pdf(todos_resultados: list, config: dict, numero_di: str = "", docs_procesados: dict = None, incluir_detalle_ok: bool = False) -> bytes:
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
        textColor=colors.black, leading=10,
        wordWrap="CJK")  # permite partir incluso strings largos sin espacios (ej. listas pegadas)

    estilo_celda_docs = ParagraphStyle("celda_docs",
        fontSize=8, fontName="Helvetica",
        textColor=colors.black, leading=11,
        wordWrap="CJK")

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
    story.append(Spacer(1, 10))

    # ─── DOCUMENTOS PROCESADOS ────────────────────────────────────────────────
    # Usa Paragraph en vez de texto plano para que listas largas (facturas,
    # CMs, DJs) hagan word-wrap dentro de la celda en lugar de desbordar o
    # quedar cortadas.
    if docs_procesados:
        story.append(Paragraph("Documentos procesados", estilo_seccion))
        docs_data = [[
            Paragraph("<b>Estado</b>", estilo_celda_docs),
            Paragraph("<b>Documento</b>", estilo_celda_docs),
            Paragraph("<b>Detalle</b>", estilo_celda_docs),
        ]]
        for nombre_doc, info in docs_procesados.items():
            icono = "OK" if info.get("ok") else "---"
            docs_data.append([
                Paragraph(icono, estilo_celda_docs),
                Paragraph(str(nombre_doc), estilo_celda_docs),
                Paragraph(str(info.get("detalle", "")), estilo_celda_docs),
            ])
        tabla_docs = Table(docs_data, colWidths=[1.6*cm, 4*cm, 12.4*cm], repeatRows=1)
        tabla_docs.setStyle(TableStyle([
            ("BACKGROUND", (0,0), (-1,0), AZUL_OSCURO),
            ("TEXTCOLOR", (0,0), (-1,0), BLANCO),
            ("FONTSIZE", (0,0), (-1,-1), 8),
            ("FONTNAME", (0,0), (-1,-1), "Helvetica"),
            ("ROWBACKGROUNDS", (0,1), (-1,-1), [GRIS_CLARO, BLANCO]),
            ("BOX", (0,0), (-1,-1), 0.5, colors.lightgrey),
            ("INNERGRID", (0,0), (-1,-1), 0.3, colors.lightgrey),
            ("TOPPADDING", (0,0), (-1,-1), 4),
            ("BOTTOMPADDING", (0,0), (-1,-1), 4),
            ("LEFTPADDING", (0,0), (-1,-1), 5),
            ("VALIGN", (0,0), (-1,-1), "TOP"),
        ]))
        story.append(tabla_docs)
        story.append(Spacer(1, 10))

    # ─── SEPARACIÓN GENERAL vs ÍTEMS ──────────────────────────────────────────
    # Revisión General: chequeos a nivel despacho completo (carátula, BL,
    # bultos, países prohibidos, facturas/vendedor declarados, CM, DJ
    # origen, ítems agrupados, etc.), no por ítem individual.
    #
    # Dos tipos de fila GENERAL:
    #   - "es_resumen": True -> generadas por validar_resumen_cm/dj/items.
    #     Ya traen su propio texto agrupado. Son EXCLUSIVAS de Revisión
    #     General — nunca aparecen en Errores/Alertas/OK, para no
    #     duplicar el detalle real (validar_cm_vs_di, validar_dj_origen,
    #     validar_factura_vs_di).
    #   - Sin esa marca -> chequeos generales "directos" (países, FOB,
    #     bultos/BL, ITN, etc.). Sus OK se muestran completos en Revisión
    #     General; si hay ERROR/ALERTA, se agrega solo un resumen por
    #     campo (generado acá) y el detalle real vive en Errores/Alertas.
    es_general = lambda r: str(r.get("item", "")) == "GENERAL"
    es_resumen = lambda r: bool(r.get("es_resumen"))

    resultados_resumen = [r for r in todos_resultados if es_resumen(r)]
    resultados_resto = [r for r in todos_resultados if not es_resumen(r)]

    # ─── RESUMEN EJECUTIVO ────────────────────────────────────────────────────
    errores  = [r for r in resultados_resto if r["nivel"] == "ERROR"]
    alertas  = [r for r in resultados_resto if r["nivel"] == "ALERTA"]
    oks      = [r for r in resultados_resto if r["nivel"] == "OK" and not es_general(r)]
    oks_generales = [r for r in resultados_resto if r["nivel"] == "OK" and es_general(r)]

    errores_generales = [r for r in errores if es_general(r)]
    alertas_generales = [r for r in alertas if es_general(r)]

    story.append(Paragraph("Resumen Ejecutivo", estilo_seccion))

    resumen_data = [
        ["", "Cantidad", "Descripción"],
        ["🌐  GENERAL",   str(len(oks_generales) + len(resultados_resumen)), "Revisión a nivel despacho completo (carátula, BL, bultos, países, CM, DJ, ítems, etc.)"],
        ["❌  ERRORES",   str(len(errores)),  "Inconsistencias críticas que deben corregirse antes de oficializar"],
        ["⚠️  ALERTAS",   str(len(alertas)),  "Situaciones a verificar — pueden ser correctas según el caso"],
        ["✅  OK",        str(len(oks) + len(oks_generales)), "Validaciones superadas correctamente"],
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
        ("BACKGROUND", (0,1), (-1,1), colors.HexColor("#EEE8F7")),  # GENERAL
        ("BACKGROUND", (0,2), (-1,2), colors.HexColor("#FDECEA")),  # ERROR
        ("BACKGROUND", (0,3), (-1,3), colors.HexColor("#FFF8E1")),  # ALERTA
        ("BACKGROUND", (0,4), (-1,4), colors.HexColor("#F1F8E9")),  # OK
        ("TEXTCOLOR", (0,1), (0,1), MORADO),
        ("TEXTCOLOR", (0,2), (0,2), ROJO),
        ("TEXTCOLOR", (0,3), (0,3), NARANJA),
        ("TEXTCOLOR", (0,4), (0,4), VERDE),
        ("ALIGN", (1,0), (1,-1), "CENTER"),
        ("FONTNAME", (1,1), (1,-1), "Helvetica-Bold"),
        ("FONTSIZE", (1,1), (1,-1), 12),
        ("TEXTCOLOR", (1,1), (1,1), MORADO),
        ("TEXTCOLOR", (1,2), (1,2), ROJO),
        ("TEXTCOLOR", (1,3), (1,3), NARANJA),
        ("TEXTCOLOR", (1,4), (1,4), VERDE),
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

        # Columna Ítem y Campo angostas (contenido siempre corto); Mensaje
        # se queda con la mayor parte del ancho disponible porque ahí van
        # las listas largas de facturas/CMs/DJs que necesitan más espacio
        # horizontal para no generar filas excesivamente altas.
        t = Table(rows, colWidths=[1.4*cm, 3.2*cm, 13.4*cm], repeatRows=1)
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

    # ─── REVISIÓN GENERAL ─────────────────────────────────────────────────────
    # Va primero: panorama global del despacho. Muestra primero los
    # resúmenes agrupados de CM/DJ/Ítems (ya con su propio texto), luego
    # el detalle real de los OK directos (países, FOB, bultos/BL, etc.).
    # Si hay ERROR/ALERTA en los chequeos directos (sin función de
    # resumen dedicada, ej. ITN), agrega una fila resumen por campo
    # afectado en vez del detalle, que vive en Errores/Alertas.
    filas_revision_general = list(resultados_resumen) + list(oks_generales)
    if errores_generales or alertas_generales:
        campos_afectados = {}
        for r in errores_generales + alertas_generales:
            campos_afectados.setdefault(r["campo"], {"ERROR": 0, "ALERTA": 0})
            campos_afectados[r["campo"]][r["nivel"]] += 1

        # El campo GENERAL "FOB" (total del despacho) está relacionado
        # con las validaciones por ítem de MONTO FOB (FACTURA) y
        # MONTO FOB (CM) — si hay error en el total, el resumen general
        # también refleja cuántos ítems puntuales tienen diferencia.
        if "FOB" in campos_afectados:
            campos_fob_item = {"MONTO FOB (FACTURA)", "MONTO FOB (CM)"}
            for r in errores + alertas:
                if r["campo"] in campos_fob_item:
                    campos_afectados["FOB"][r["nivel"]] += 1

        for campo, cuenta in campos_afectados.items():
            partes = []
            if cuenta["ERROR"]:
                partes.append(f"{cuenta['ERROR']} error(es)")
            if cuenta["ALERTA"]:
                partes.append(f"{cuenta['ALERTA']} alerta(s)")
            seccion = "Errores" if cuenta["ERROR"] else "Alertas"
            nivel_resumen = "ERROR" if cuenta["ERROR"] else "ALERTA"
            filas_revision_general.append({
                "item": "GENERAL",
                "campo": campo,
                "mensaje": f"Hay {' y '.join(partes)} — ver sección \"{seccion}\"",
                "nivel": nivel_resumen,
            })

    if filas_revision_general:
        story.append(Paragraph("🌐 Revisión General", estilo_seccion))
        tabla_detalle(filas_revision_general, colors.HexColor("#EEE8F7"), MORADO)
        story.append(Spacer(1, 10))

    def _clave_item(r):
        # Mismo criterio usado en toda la app: GENERAL al principio,
        # ítems numéricos ordenados como número.
        item = str(r.get("item", ""))
        primero = item.split(",")[0].strip()
        if primero.isdigit():
            return (1, int(primero))
        return (0, primero)

    # Campos informativos/declarativos de baja prioridad de lectura: van
    # siempre al final de la sección (resto de los campos mantiene orden
    # alfabético entre sí).
    CAMPOS_BAJA_PRIORIDAD = {"I:DNRT-EXC-OPC", "I:AUTOPARTESEG-OPC", "I:DNRT-OPC"}

    def _clave_campo(campo: str):
        campo = str(campo)
        return (1, campo) if campo in CAMPOS_BAJA_PRIORIDAD else (0, campo)

    # ─── ERRORES ──────────────────────────────────────────────────────────────
    # Ordenado por Campo y luego por Ítem (no solo por Ítem) para que las
    # observaciones de un mismo tipo (ej. todas las de LIQUIDACIÓN) queden
    # agrupadas y sean fáciles de ubicar sin tener que revisar fila por
    # fila entre cientos de ítems. Los campos informativos de baja
    # prioridad quedan al final.
    if errores:
        story.append(Paragraph("❌ Errores — Corrección obligatoria", estilo_seccion))
        errores_ordenados = sorted(errores, key=lambda r: (_clave_campo(r.get("campo", "")), _clave_item(r)))
        tabla_detalle(errores_ordenados, colors.HexColor("#FDECEA"), ROJO)
        story.append(Spacer(1, 10))

    # ─── ALERTAS ──────────────────────────────────────────────────────────────
    if alertas:
        story.append(Paragraph("⚠️ Alertas — Verificar antes de oficializar", estilo_seccion))
        alertas_ordenadas = sorted(alertas, key=lambda r: (_clave_campo(r.get("campo", "")), _clave_item(r)))
        tabla_detalle(alertas_ordenadas, colors.HexColor("#FFF8E1"), NARANJA)
        story.append(Spacer(1, 10))

    # ─── OK ───────────────────────────────────────────────────────────────────
    # Por defecto no se incluye el detalle fila por fila de cada ítem OK
    # (puede ser miles de filas en despachos grandes) — el panorama
    # agrupado ya está en Revisión General. Solo se incluye el detalle
    # completo si el usuario lo pidió explícitamente (incluir_detalle_ok).
    if oks:
        story.append(Paragraph("✅ Validaciones correctas", estilo_seccion))
        if incluir_detalle_ok:
            tabla_detalle(oks, colors.HexColor("#F1F8E9"), VERDE)
        else:
            story.append(Paragraph(
                f"{len(oks)} validación(es) por ítem superadas correctamente. "
                f"El detalle agrupado ya está en la sección 'Revisión General'. "
                f"Para ver el detalle completo fila por fila, generar el PDF con la opción "
                f"\"Incluir detalle completo de Validaciones correctas\" activada.",
                estilo_normal))

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
