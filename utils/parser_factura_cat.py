"""
Parser PyMuPDF + regex para Facturas Comerciales CAT (CATERPILLAR SARL)
Sin uso de API — extracción local gratuita.

Lógica de normalización de código de parte:
  - Todo numérico (empieza con 3 dígitos): tomar primeros 7 chars → 541-7108( → 5417108
  - Con letra inicial: tomar primeros 6 chars             → 1K-6853CO → 1K6853
  Los sufijos de origen se extraen dinámicamente de la página de totales del invoice.

Casos especiales manejados:
  - Sufijos de origen dinámicos: (, ), J, L, T, N, P, VN, CO, I, etc.
  - Cargo adicional por ítem (SPECIAL PACKING, FREIGHT CHARGE, BO FREIGHT
    CHARGE, EMERGENCY FILL CHARGE, etc.): se suma al subtotal de esa línea
    específica (campo 'cargos_propios'), sin mirar nunca el ORDER TOTAL
    AMOUNT del grupo.
  - Cada línea de la factura = un ítem de DI independiente (sin consolidar),
    incluso si el código de parte y el CUST REF ITEM NO se repiten — esto
    ocurre cuando CAT facturó el mismo material en líneas separadas
    (distintas cajas) pero cada línea tiene su propio FOB declarable.
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


def _extraer_sufijos_origen(texto_pdf: str) -> dict:
    """
    Lee la página de INVOICE TOTALS y extrae el mapa de sufijos de origen dinámicamente.
    Ej: 'PARTS IDENTIFIED BY THE SUFFIX CO WERE MADE IN COLOMBIA' → {'CO': 'COLOMBIA'}
    Siempre incluye USA como fallback para códigos sin sufijo.
    """
    RE_SUFIJO = re.compile(
        r"PARTS IDENTIFIED BY THE SUFFIX\s+(\S+)\s+WERE MADE IN\s+(.+)"
    )
    sufijos = {"": "USA"}  # fallback
    for sufijo, pais in RE_SUFIJO.findall(texto_pdf):
        sufijos[sufijo.strip()] = pais.strip().title()
    return sufijos


def _limpiar_codigo(part_raw: str) -> str:
    """
    Normaliza código de parte CAT usando la regla estructural del catálogo:
      - Empieza con 3 dígitos (todo numérico): tomar primeros 7 chars → '541-7108(' → '5417108'
      - Tiene letra inicial: tomar primeros 6 chars              → '1K-6853CO' → '1K6853'
    Primero quita guiones y espacios, luego recorta.

    IMPORTANTE: no se sacan ceros iniciales. Un cero al principio puede
    ser parte real del código (ej. "0S-0509L" -> "0S0509"), no relleno.
    """
    s = re.sub(r'[-\s]', '', part_raw.strip().upper())
    if re.match(r'^\d{3}', s):
        return s[:7]
    else:
        return s[:6]


def _origen(part_raw: str, sufijos: dict) -> str:
    """
    Determina país de origen buscando el sufijo más largo que matchee al final del código.
    Usa el mapa de sufijos extraído dinámicamente del invoice.
    """
    s = re.sub(r'[-\s]', '', part_raw.strip().upper())
    # Código base (sin sufijo)
    if re.match(r'^\d{3}', s):
        base = s[:7]
    else:
        base = s[:6]
    sufijo_encontrado = s[len(base):]  # lo que sobra después del código base

    # Buscar coincidencia en el mapa (más largo primero para evitar ambigüedad)
    for sufijo in sorted(sufijos.keys(), key=len, reverse=True):
        if sufijo and sufijo_encontrado == sufijo:
            return sufijos[sufijo]

    return sufijos.get("", "USA")


# ── Regex ─────────────────────────────────────────────────────────────────────

# Línea de datos del encabezado:
# '  R06C    Z 95  051485  12MAY26  IC  ...   US DOLLAR'
# '  R06L    Z 95  051479  12MAY26  IC  ...   US DOLLAR'
RE_DATOS_CABECERA = re.compile(
    r"R0[0-9A-Z]{2}\s+([A-Z]\s*\d{2}\s*\d{6})\s+(\d{2}[A-Z]{3}\d{2,4})"
)

# Moneda: última palabra(s) en mayúsculas de la línea de datos, ej. 'US DOLLAR'
RE_MONEDA = re.compile(
    r"\b(US DOLLAR|EURO|EUR|USD)\b"
)

# Incoterm: línea 'SHIP TERMS:' seguida del código (ej. 'CIF - COST, INSURANCE...')
RE_INCOTERM = re.compile(
    r"SHIP TERMS:\s*\n?\s*([A-Z]{3})\b"
)

# Línea de ítem:
# '   5  1,000 AA 541-7108(  QC  HOSE BK  0.07  70.00'
# '  57    100 AA 1K-6853CO  JA  FITTING  1.76  176.00'
RE_ITEM = re.compile(
    r"^\s{2,8}"
    r"(\d{1,3})"              # item#
    r"\s+"
    r"([\d,]+)"               # qty
    r"\s+AA\s+"               # tipo fijo AA
    r"(\S+)"                  # código + cualquier sufijo de origen (letras, (), #, etc.)
    r"\s+"
    r"(?:([A-Z]{2})\s{2,})?"  # sufijo descriptivo opcional (VA, QC, JA...) — a veces no viene
    r"(.+?)"                  # descripción
    r"\s{2,}"
    r"([\d,]+\.\d{2})"       # unit price
    r"\s+"
    r"([\d,]+\.\d{2})"       # extended price
    r"\s*$"
)

RE_CUST_REF    = re.compile(r"CUST REF ITEM NO[:\s]+(\d+)")
RE_INV_TOTAL   = re.compile(r"INVOICE TOTAL\s+([\d,]+\.\d{2})")
RE_SHIPMENT    = re.compile(r"\bSHIPMENT\b.*?(\d{7,})")

# ── Cargo adicional por ítem ──────────────────────────────────────────────────
# Línea de cargo: '<CONCEPTO> ... <pct>%  <monto>' (ej: 'SPECIAL PACKING 1%  1.00 %  15.56')
#
# Estrategia de doble red:
#   1) Conceptos conocidos (lista controlada) → match preciso y rápido.
#   2) Si ninguno matchea, fallback genérico por patrón estructural
#      (texto en mayúsculas + porcentaje + monto), para no perder cargos
#      con nombres que CAT agregue en el futuro (HANDLING FEE, EXPEDITE
#      CHARGE, etc.) sin tener que tocar el código.
CONCEPTOS_CARGO_CONOCIDOS = [
    "BO FREIGHT CHARGE",      # más específico primero, para no perderlo bajo "FREIGHT CHARGE"
    "EMERGENCY FILL CHARGE",
    "SPECIAL PACKING",
    "FREIGHT CHARGE",
    "HANDLING FEE",
    "EXPEDITE CHARGE",
    "CORE DEPOSIT",           # cargo de depósito de núcleo (CORE) en ítems usados/reman.
                              # No trae porcentaje (ej. "CORE DEPOSIT LIAB TO DLR  2,803.93"),
                              # pero igual matchea con RE_CARGO_CONOCIDO porque esa regex
                              # no exige porcentaje, solo concepto + monto al final de línea.
]

RE_CARGO_CONOCIDO = re.compile(
    r"(" + "|".join(re.escape(c) for c in CONCEPTOS_CARGO_CONOCIDOS) + r")"
    r".*?([\d,]+\.\d{2})\s*$"
)

# Fallback: cualquier línea "TEXTO [pct%] ... pct%  monto" que no sea PART WEIGHT,
# ORDER TOTAL, CASE NO, etc. Tolera un porcentaje pegado al nombre (ej. 'PACKING 1%')
# antes del segundo porcentaje con espacio (ej. '1.00 %') que precede al monto.
RE_CARGO_GENERICO = re.compile(
    r"^\s*([A-Z][A-Z\s\-]{2,30}?)\s+[\d.]+%?\s+[\d.]+\s*%\s+([\d,]+\.\d{2})\s*$"
)


def _detectar_cargo_item(linea: str):
    """
    Intenta detectar un cargo adicional por ítem en la línea dada.
    Retorna (concepto, monto) o (None, 0.0) si no matchea ninguna estrategia.
    """
    m = RE_CARGO_CONOCIDO.search(linea)
    if m:
        return m.group(1).strip(), _n(m.group(2))

    m = RE_CARGO_GENERICO.match(linea)
    if m:
        return m.group(1).strip(), _n(m.group(2))

    return None, 0.0


# ── Parser principal ──────────────────────────────────────────────────────────

def extraer_factura_cat(pdf_bytes: bytes) -> dict:
    doc = fitz.open(stream=pdf_bytes, filetype="pdf")

    numero_factura = ""
    fecha          = ""
    incoterm       = ""
    moneda         = "USD"  # fallback si no se detecta explícitamente
    shipment       = ""
    total_factura  = 0.0
    items_raw      = []
    sufijos        = {"": "USA"}  # se reemplaza al encontrar la página de totales

    # Primera pasada: extraer sufijos de origen, incoterm y moneda del texto completo
    texto_completo = "\n".join(page.get_text() for page in doc)
    sufijos = _extraer_sufijos_origen(texto_completo)

    m_incoterm = RE_INCOTERM.search(texto_completo)
    if m_incoterm:
        incoterm = m_incoterm.group(1)

    m_moneda = RE_MONEDA.search(texto_completo)
    if m_moneda:
        moneda_raw = m_moneda.group(1)
        moneda = "USD" if moneda_raw in ("US DOLLAR", "USD") else moneda_raw

    # Segunda pasada: parsear ítems
    doc2 = fitz.open(stream=pdf_bytes, filetype="pdf")
    for page in doc2:
        texto = page.get_text()
        lineas = texto.splitlines()

        # ── Encabezado ──
        if not numero_factura:
            m = RE_DATOS_CABECERA.search(texto)
            if m:
                numero_factura = re.sub(r"\s+", "", m.group(1))
                fecha = m.group(2)

        if not shipment:
            m = RE_SHIPMENT.search(texto)
            if m:
                shipment = m.group(1)

        # ── Total ──
        m = RE_INV_TOTAL.search(texto)
        if m:
            total_factura = _n(m.group(1))

        # ── Ítems ──
        if "ITEM#" not in texto:
            continue

        for i, linea in enumerate(lineas):
            m = RE_ITEM.match(linea)
            if not m:
                continue

            item_num    = int(m.group(1))
            qty         = _n(m.group(2))
            part_raw    = m.group(3).strip()
            sufijo_desc = (m.group(4) or "").strip()
            descripcion = (sufijo_desc + " " + m.group(5)).strip()
            unit_price  = _n(m.group(6))
            extended    = _n(m.group(7))

            codigo_base = _limpiar_codigo(part_raw)
            origen      = _origen(part_raw, sufijos)

            # CUST REF y cargos adicionales en las 5 líneas siguientes
            # (entre el ítem y PART WEIGHT / ORDER TOTAL / próximo ítem)
            # Se acumulan TODOS los cargos que aparezcan en el bloque.
            cust_ref       = ""
            cargo_propio   = 0.0
            concepto_cargo = ""
            for j in range(i + 1, min(i + 6, len(lineas))):
                lj = lineas[j]
                if not cust_ref:
                    mc = RE_CUST_REF.search(lj)
                    if mc:
                        cust_ref = mc.group(1)
                concepto, monto = _detectar_cargo_item(lj)
                if concepto:
                    cargo_propio   += monto   # acumular, puede haber más de uno
                    concepto_cargo  = (concepto_cargo + " | " + concepto).strip(" |") if concepto_cargo else concepto
                if "PART WEIGHT" in lj:
                    break

            subtotal = extended + cargo_propio

            items_raw.append({
                "numero_item":        item_num,
                "codigo_parte":       codigo_base,
                "descripcion":        descripcion,
                "cantidad":           qty,
                "precio_unitario":    unit_price,
                "precio_total_parte": extended,
                "cargos_propios":     cargo_propio,
                "concepto_cargo":     concepto_cargo,
                "subtotal":           subtotal,
                "origen":             origen,
                "_cust_ref":          cust_ref,
            })

    doc.close()
    doc2.close()

    items = _renumerar(items_raw)

    return {
        "numero_factura":  numero_factura,
        "fecha":           fecha,
        "vendedor":        "CATERPILLAR SARL (LATIN AMERICA)",
        "moneda":          moneda,
        "incoterm":        incoterm,
        "shipment":        shipment,
        "items":           items,
        "total_partes":    total_factura,
        "total_cargos":    0.0,
        "total_factura":   total_factura,
        "cargos_globales": 0.0,
        "tipo_cargos":     "por_item",
    }


# ── Renumeración ──────────────────────────────────────────────────────────────

def _renumerar(items_raw: list) -> list:
    """
    NO consolida. Cada línea de la factura corresponde a un ítem distinto
    del DI (relación 1 a 1), incluso si el código de parte y el CUST REF
    ITEM NO se repiten — eso ocurre cuando CAT facturó el mismo material
    en líneas separadas (distintas cajas, distinta disponibilidad, etc.)
    pero cada línea es un ítem de DI independiente con su propio FOB.

    Solo limpia el campo interno _cust_ref y renumera secuencialmente.
    """
    resultado = [item.copy() for item in items_raw]
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
            f"  [{it['numero_item']:2d}] {it['codigo_parte']:<10} "
            f"{it['descripcion']:<25} "
            f"Qty:{it['cantidad']:>8,.0f}  "
            f"U:{it['precio_unitario']:>10,.2f}  "
            f"Tot:{it['precio_total_parte']:>12,.2f}  "
            f"{it['origen']}"
        )
    print(f"\nSuma ítems: USD {sum(i['precio_total_parte'] for i in data['items']):,.2f}")
    print(f"Total fc:   USD {data['total_factura']:,.2f}")
