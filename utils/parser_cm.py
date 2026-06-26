"""
Parser PyMuPDF + regex para Forwarding Invoice CAT.
Sin API — extracción local gratuita.

Soporta 2 formatos distintos de proveedor, detectados automáticamente:
  - DHL: texto lineal, etiqueta seguida de su valor en la línea siguiente
    (ej. "Invoice No" / "ZQFLI261200"). Layout de texto plano simple.
  - DB Schenker: layout de tabla/formulario — las etiquetas de
    encabezado salen todas juntas primero (SAILING DATE, E.T.A., ...) y
    sus valores salen en otro bloque más abajo, en el mismo orden
    relativo, no en líneas consecutivas a la etiqueta.
"""
import re
import fitz

def _n(s):
    try:
        return float(re.sub(r"[^\d.]", "", s.strip()))
    except:
        return 0.0


def _detectar_moneda(lineas: list) -> str:
    """
    Detecta la moneda real del Forwarding Invoice buscando códigos conocidos
    en el texto (columna VAT CUR de cada línea, o 'Insured Value: ... USD').
    Fallback a USD si no se encuentra ninguno explícito.
    """
    texto = "\n".join(lineas).upper()
    for codigo in ("USD", "EUR", "ARS"):
        if re.search(rf"\b{codigo}\b", texto):
            return codigo
    return "USD"


def _resultado_base(moneda_detectada: str) -> dict:
    return {
        "numero_invoice":        "",
        "fecha":                 "",
        "bl_number":             "",
        "incoterm":              "CIF",
        "flete_total":           0.0,
        "detalle_flete":         [],
        "seguro_marine_premium": 0.0,
        "seguro_war_premium":    0.0,
        "seguro_otros":          [],
        "seguro_total":          0.0,
        "otros_cargos":          [],
        "total_invoice_dealer":  0.0,
        "moneda":                moneda_detectada,
        "moneda_flete":          moneda_detectada,
        "moneda_seguro":         moneda_detectada,
        "alertas":               [],
    }


# ── Formato DHL: texto lineal, etiqueta + valor en línea siguiente ───────────

def _extraer_forwarding_dhl(lineas: list, moneda_detectada: str) -> dict:
    resultado = _resultado_base(moneda_detectada)

    SKIP = {"DESCRIPTION", "AMOUNT", "VAT", "CUR", "Remarks:", "USD", "USD "}

    for i, linea in enumerate(lineas):
        s = linea.strip()
        if not s:
            continue

        # ── Campos de encabezado ──
        if s == "Invoice No" and i+1 < len(lineas):
            resultado["numero_invoice"] = lineas[i+1].strip()
        elif s == "Date" and i+1 < len(lineas):
            resultado["fecha"] = lineas[i+1].strip()
        elif s == "Bill of Lading Number" and i+1 < len(lineas):
            resultado["bl_number"] = lineas[i+1].strip()

        # ── Flete total ──
        elif s.startswith("Total Charge to Caterpillar"):
            # Puede estar en misma línea o en la siguiente
            m = re.search(r"([\d,]+\.\d{2})", s)
            if m:
                resultado["flete_total"] = _n(m.group(1))
            elif i+1 < len(lineas):
                resultado["flete_total"] = _n(lineas[i+1])

        # ── Seguros ──
        elif s == "Marine Premium" and i+1 < len(lineas):
            resultado["seguro_marine_premium"] = _n(lineas[i+1])
        elif s == "War Premium" and i+1 < len(lineas):
            resultado["seguro_war_premium"] = _n(lineas[i+1])

        # ── Total dealer ──
        elif s.startswith("Total Invoice to Dealer"):
            m = re.search(r"([\d,]+\.\d{2})", s)
            if m:
                resultado["total_invoice_dealer"] = _n(m.group(1))

        # ── Alertas ──
        elif s == "Finance Charges to Dealer" and i+1 < len(lineas):
            v = _n(lineas[i+1])
            if v > 0:
                resultado["alertas"].append(f"Finance Charges to Dealer: USD {v:.2f}")
        elif s == "Other Charges" and i+1 < len(lineas):
            v = _n(lineas[i+1])
            if v > 0:
                resultado["alertas"].append(f"Other Charges: USD {v:.2f}")

    # ── Detalle flete: entre DESCRIPTION y Total Charge ──
    # Formato: línea de concepto (con monto embebido "Base Rate USD X.XX")
    # seguida de línea con solo el monto
    en_detalle = False
    i = 0
    while i < len(lineas):
        s = lineas[i].strip()
        if s == "DESCRIPTION":
            en_detalle = True
            i += 1
            continue
        if s.startswith("Total Charge to Caterpillar"):
            en_detalle = False
        if en_detalle and s and s not in SKIP:
            # Es una línea de concepto si la siguiente es solo un número
            siguiente = lineas[i+1].strip() if i+1 < len(lineas) else ""
            if re.fullmatch(r"[\d,]+\.\d{2}", siguiente):
                resultado["detalle_flete"].append({
                    "concepto": s,
                    "monto": _n(siguiente)
                })
                i += 2
                continue
        i += 1

    resultado["seguro_total"] = (
        resultado["seguro_marine_premium"] + resultado["seguro_war_premium"]
    )

    return resultado


# ── Formato DB Schenker: layout de tabla/formulario ───────────────────────────
#
# Las etiquetas de encabezado (SAILING DATE, E.T.A., PLACE OF ACCEPTANCE,
# OUR REFERENCE (STT NUMBER), ACCOUNT NUMBER, Vessel/Voyage No., PORT OF
# LOADING, BILL OF LANDING No., INVOICE NUMBER, ...) salen todas juntas
# primero en el texto extraído; sus valores aparecen en otro bloque más
# abajo, en el mismo orden relativo de aparición. Por eso no se puede usar
# "etiqueta seguida de su valor" — hay que ubicar el dato por su formato
# característico (regex) en vez de por posición relativa a la etiqueta.
# La descripción (cargos) sí aparece en formato lineal normal:
# "CONCEPTO [%] MONEDA MONTO".

RE_MONTO_2DEC = re.compile(r"^[\d,]+\.\d{2}$")


def _extraer_forwarding_db_schenker(lineas: list, moneda_detectada: str) -> dict:
    resultado = _resultado_base(moneda_detectada)

    texto_completo = "\n".join(lineas)

    # ── Número de invoice: identificado por su formato típico
    # (letras+dígitos, ej. ZQFZP305993, ZQFLI261200) — más confiable que
    # depender del orden de bloques de la tabla. ──
    m = re.search(r"\b([A-Z]{2,5}\d{5,9})\b", texto_completo)
    if m:
        resultado["numero_invoice"] = m.group(1)

    # ── BL number: patrón típico de solo dígitos, 6-9 caracteres. En el
    # bloque de valores del encabezado, el BL number aparece en la línea
    # inmediatamente anterior al numero_invoice (ej. "721469023" seguido
    # de "ZQFZP305993") — se busca relativo a esa posición en vez de a
    # "antes de DESCRIPTION", porque el bloque de valores puede estar
    # ubicado en cualquier parte del documento según el proveedor. ──
    if resultado["numero_invoice"]:
        idx_invoice = next((i for i, l in enumerate(lineas) if l.strip() == resultado["numero_invoice"]), None)
        if idx_invoice is not None:
            for j in range(max(0, idx_invoice - 3), idx_invoice):
                s = lineas[j].strip()
                if re.fullmatch(r"\d{6,9}", s):
                    resultado["bl_number"] = s
                    break

    # ── Fecha: formato DD/MM/YY suelto en el texto ──
    m_fecha = re.search(r"\b(\d{2}/\d{2}/\d{2})\b", texto_completo)
    if m_fecha:
        resultado["fecha"] = m_fecha.group(1)

    # ── Cargos de flete: líneas "CONCEPTO [PORCENTAJE%] MONEDA MONTO"
    # Ej: "DB SCHENKER HANDLING AND PROCESSING" / "USD" / "175.00"
    #     "DISBURSEMENT FEE" / "2.50%" / "USD" / "58.15"
    # Se acumulan todos los conceptos hasta "TOTAL CHARGES TO CATERPILLAR".
    i = 0
    en_detalle = False
    while i < len(lineas):
        s = lineas[i].strip()
        if s == "DESCRIPTION":
            en_detalle = True
            i += 1
            continue
        if s.startswith("TOTAL CHARGES TO CATERPILLAR"):
            en_detalle = False
            m_tot = re.search(r"([\d,]+\.\d{2})", s)
            if m_tot:
                resultado["flete_total"] = _n(m_tot.group(1))
            else:
                for j in range(i+1, min(i+4, len(lineas))):
                    if RE_MONTO_2DEC.match(lineas[j].strip()):
                        resultado["flete_total"] = _n(lineas[j])
                        break
            i += 1
            continue
        if en_detalle and s and s not in ("CURRENCY", "AMOUNT", moneda_detectada):
            es_monto = bool(RE_MONTO_2DEC.match(s))
            es_porcentaje = bool(re.fullmatch(r"[\d.]+%", s))
            if not es_monto and not es_porcentaje:
                for j in range(i+1, min(i+4, len(lineas))):
                    candidato = lineas[j].strip()
                    if RE_MONTO_2DEC.match(candidato):
                        resultado["detalle_flete"].append({
                            "concepto": s,
                            "monto": _n(candidato)
                        })
                        break
                    if not re.fullmatch(r"[\d.]+%", candidato) and candidato not in ("CURRENCY", "AMOUNT", moneda_detectada):
                        break
        i += 1

    # ── Seguro: "INSURANCE PREMIUM" seguido de porcentaje, moneda y monto ──
    for i, linea in enumerate(lineas):
        s = linea.strip()
        if s == "INSURANCE PREMIUM":
            for j in range(i+1, min(i+4, len(lineas))):
                candidato = lineas[j].strip()
                if RE_MONTO_2DEC.match(candidato):
                    resultado["seguro_marine_premium"] = _n(candidato)
                    break
            break

    # ── Total a dealer ──
    for i, linea in enumerate(lineas):
        s = linea.strip()
        if s.startswith("TOTAL INVOICE TO DEALER"):
            for j in range(i+1, min(i+4, len(lineas))):
                candidato = lineas[j].strip()
                if RE_MONTO_2DEC.match(candidato):
                    resultado["total_invoice_dealer"] = _n(candidato)
                    break
            break

    resultado["seguro_total"] = (
        resultado["seguro_marine_premium"] + resultado["seguro_war_premium"]
    )

    return resultado


# ── Router: detecta el formato y delega a la función correspondiente ─────────

def extraer_forwarding(pdf_bytes: bytes) -> dict:
    doc = fitz.open(stream=pdf_bytes, filetype="pdf")
    lineas = [l for page in doc for l in page.get_text().splitlines()]
    doc.close()

    moneda_detectada = _detectar_moneda(lineas)
    texto_completo = "\n".join(lineas).upper()

    # DB Schenker se identifica por su frase distintiva de pie de página
    # o por usar "TOTAL CHARGES TO CATERPILLAR" (con "S"), distinto del
    # "Total Charge to Caterpillar" (sin "S") del formato DHL.
    es_db_schenker = (
        "DB SCHENKER" in texto_completo
        or "TOTAL CHARGES TO CATERPILLAR" in texto_completo
    )

    if es_db_schenker:
        return _extraer_forwarding_db_schenker(lineas, moneda_detectada)
    return _extraer_forwarding_dhl(lineas, moneda_detectada)


if __name__ == "__main__":
    import sys, json
    with open(sys.argv[1], "rb") as f:
        data = extraer_forwarding(f.read())
    print(json.dumps(data, indent=2, ensure_ascii=False))
