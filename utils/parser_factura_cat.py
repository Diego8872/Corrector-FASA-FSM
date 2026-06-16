"""
Parser PyMuPDF + regex para Facturas Comerciales CAT (CATERPILLAR SARL)
Sin uso de API — extracción local gratuita.

Formato real (extraído con PyMuPDF):
  '      1      1 AA 125-4527J  VA          PUMP GP          5,263.06     5,263.06 '
  '                  CUST REF ITEM NO: 0010                                         '

Casos especiales manejados:
  - Mismo código repetido en ítems distintos (cajas distintas, mismo CUST REF ITEM NO)
    → consolidados: se suma cantidad y precio_total, se recalcula precio_unitario
  - Sufijos de origen: J=México, L=Italia, T=Canadá, sin sufijo=USA
  - Página 1 sin SHIPMENT → se ignora automáticamente
  - Fecha e invoice number en línea de datos (no en línea de etiquetas)
"""

import re
import fitz  # PyMuPDF


# ── Utilidades ────────────────────────────────────────────────────────────────

def _n(s: str) -> float:
    """'5,263.06' → 5263.06"""
    try:
        return float(s.replace(",", "").strip())
    except Exception:
        return 0.0


ORIGEN_SUFIJO = {"J": "MEXICO", "L": "ITALY", "T": "CANADA"}

def _origen(part_raw: str) -> str:
    m = re.search(r"([A-Z])$", part_raw.strip())
    if m and m.group(1) in ORIGEN_SUFIJO:
        return ORIGEN_SUFIJO[m.group(1)]
    return "USA"


# ── Regex ─────────────────────────────────────────────────────────────────────

# Línea de datos del encabezado (contiene número factura y fecha):
# '  R06C         Z 95  046355  29APR26     IC  ...'
RE_DATOS_CABECERA = re.compile(
    r"R06C\s+([A-Z]\s*\d{2}\s*\d{6})\s+(\d{2}[A-Z]{3}\d{2,4})"
)

# Línea de ítem:
# '      1      1 AA 125-4527J  VA          PUMP GP          5,263.06     5,263.06 '
# grupos: item_num | qty | tipo(AA) | part | sufijo_desc | descripcion | unit | extended
RE_ITEM = re.compile(
    r"^\s{2,8}"
    r"(\d{1,3})"             # item#
    r"\s+"
    r"([\d,]+)"              # qty
    r"\s+AA\s+"              # tipo fijo AA
    r"([\w\-]+)"             # código de parte (con posible sufijo J/L/T)
    r"\s+"
    r"([A-Z]{2})"            # sufijo descriptivo (VA, QC, JA, CM...)
    r"\s{2,}"
    r"(.+?)"                 # descripción
    r"\s{2,}"
    r"([\d,]+\.\d{2})"      # unit price
    r"\s+"
    r"([\d,]+\.\d{2})"      # extended price
    r"\s*$"
)

# CUST REF ITEM NO: 0010
RE_CUST_REF = re.compile(r"CUST REF ITEM NO[:\s]+(\d+)")

# ORDER TOTAL ... AMOUNT: 1,571.06
RE_ORDER_AMOUNT = re.compile(r"ORDER TOTAL.*?AMOUNT:\s*([\d,]+\.\d{2})")

# INVOICE TOTAL  19,520.68
RE_INV_TOTAL = re.compile(r"INVOICE TOTAL\s+([\d,]+\.\d{2})")

# SHIPMENT: número de shipment (indica página con ítems)
RE_SHIPMENT_LINE = re.compile(r"\bSHIPMENT\b.*?(\d{7,})")


# ── Parser principal ──────────────────────────────────────────────────────────

def extraer_factura_cat(pdf_bytes: bytes) -> dict:
    doc = fitz.open(stream=pdf_bytes, filetype="pdf")

    numero_factura = ""
    fecha = ""
    incoterm = ""
    shipment = ""
    total_factura = 0.0
    items_raw = []

    for page in doc:
        texto = page.get_text()
        lineas = texto.splitlines()

        # ── Encabezado ──
        if not numero_factura:
            m = RE_DATOS_CABECERA.search(texto)
            if m:
                numero_factura = re.sub(r"\s+", "", m.group(1))  # "Z95046355"
                fecha = m.group(2)                                # "29APR26"

        if not incoterm:
            if "CIF" in texto:
                incoterm = "CIF"
            elif "FOB" in texto:
                incoterm = "FOB"

        if not shipment:
            m = RE_SHIPMENT_LINE.search(texto)
            if m:
                shipment = m.group(1)

        # ── Total ──
        m = RE_INV_TOTAL.search(texto)
        if m:
            total_factura = _n(m.group(1))

        # ── Ítems (solo páginas con columna ITEM#) ──
        if "ITEM#" not in texto:
            continue

        for i, linea in enumerate(lineas):
            m = RE_ITEM.match(linea)
            if not m:
                continue

            item_num   = int(m.group(1))
            qty        = _n(m.group(2))
            part_raw   = m.group(3).strip()
            descripcion = (m.group(4) + " " + m.group(5)).strip()
            unit_price  = _n(m.group(6))
            extended    = _n(m.group(7))

            # Buscar CUST REF ITEM NO en las 3 líneas siguientes
            cust_ref = ""
            for j in range(i + 1, min(i + 4, len(lineas))):
                mc = RE_CUST_REF.search(lineas[j])
                if mc:
                    cust_ref = mc.group(1)
                    break

            items_raw.append({
                "numero_item":        item_num,
                "codigo_parte":       part_raw,
                "descripcion":        descripcion,
                "cantidad":           qty,
                "precio_unitario":    unit_price,
                "precio_total_parte": extended,
                "cargos_propios":     0.0,
                "subtotal":           extended,
                "origen":             _origen(part_raw),
                "_cust_ref":          cust_ref,  # campo interno para consolidar
            })

    doc.close()

    items = _consolidar(items_raw)

    return {
        "numero_factura":  numero_factura,
        "fecha":           fecha,
        "vendedor":        "CATERPILLAR SARL (LATIN AMERICA)",
        "moneda":          "USD",
        "incoterm":        incoterm,
        "shipment":        shipment,
        "items":           items,
        "total_partes":    total_factura,
        "total_cargos":    0.0,
        "total_factura":   total_factura,
        "cargos_globales": 0.0,
        "tipo_cargos":     "global",
    }


# ── Consolidación de duplicados ───────────────────────────────────────────────

def _consolidar(items_raw: list) -> list:
    """
    NO consolida — preserva cada entrada de la factura como ítem separado.
    El mismo código puede aparecer varias veces con distintas cantidades
    porque corresponde a distintos ítems del DI (distintas cajas).
    Solo elimina duplicados exactos (mismo código + misma cantidad + mismo subtotal).
    Consolida únicamente cuando el CUST REF ITEM NO es el mismo (mismo ítem de orden).
    """
    # Paso 1: consolidar solo por CUST REF ITEM NO (mismo ítem de la orden CAT)
    grupos: dict = {}
    orden: list = []

    for item in items_raw:
        key = (item["codigo_parte"].upper(), item["_cust_ref"])
        if key not in grupos:
            grupos[key] = item.copy()
            orden.append(key)
        else:
            g = grupos[key]
            g["cantidad"]           += item["cantidad"]
            g["precio_total_parte"] += item["precio_total_parte"]
            g["subtotal"]           += item["subtotal"]
            g["cargos_propios"]     += item["cargos_propios"]
            if g["cantidad"] > 0:
                g["precio_unitario"] = g["precio_total_parte"] / g["cantidad"]

    resultado = [grupos[k] for k in orden]
    for idx, it in enumerate(resultado, 1):
        it["numero_item"] = idx
        it.pop("_cust_ref", None)

    return resultado


# ── Test CLI ──────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    import sys
    path = sys.argv[1] if len(sys.argv) > 1 else "factura.pdf"
    with open(path, "rb") as f:
        data = extraer_factura_cat(f.read())

    print(f"\n=== {data['numero_factura']} | {data['fecha']} | {data['incoterm']} ===")
    print(f"Shipment: {data['shipment']} | Total: USD {data['total_factura']:,.2f}")
    print(f"Ítems: {len(data['items'])}\n")
    for it in data["items"]:
        print(
            f"  [{it['numero_item']:2d}] {it['codigo_parte']:<15} "
            f"{it['descripcion']:<25} "
            f"Qty:{it['cantidad']:>8,.0f}  "
            f"U:{it['precio_unitario']:>10,.2f}  "
            f"Tot:{it['precio_total_parte']:>12,.2f}  "
            f"{it['origen']}"
        )
    print(f"\nSuma ítems: USD {sum(i['precio_total_parte'] for i in data['items']):,.2f}")
    print(f"Total fc:   USD {data['total_factura']:,.2f}")
