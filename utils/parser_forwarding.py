"""
Parser PyMuPDF + regex para Forwarding Invoice CAT/DHL.
Sin API — extracción local gratuita.
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


def extraer_forwarding(pdf_bytes: bytes) -> dict:
    doc = fitz.open(stream=pdf_bytes, filetype="pdf")
    lineas = [l for page in doc for l in page.get_text().splitlines()]
    doc.close()

    moneda_detectada = _detectar_moneda(lineas)

    resultado = {
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
        "moneda_flete":          moneda_detectada,  # misma columna VAT CUR que el resto del doc
        "moneda_seguro":         moneda_detectada,  # idem
        "alertas":               [],
    }

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


if __name__ == "__main__":
    import sys, json
    with open(sys.argv[1], "rb") as f:
        data = extraer_forwarding(f.read())
    print(json.dumps(data, indent=2, ensure_ascii=False))
