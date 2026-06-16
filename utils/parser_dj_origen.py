"""
Parser PyMuPDF + regex para DJ de Origen No Preferencial (GDE/TAD).
Sin API — extracción local gratuita.

Formato: "Declaración Jurada de Productos - Aceptación"
Número IF: línea "IF-2026-XXXXXXXX-APN-SICYPYME#MEC"
Productos: bloques repetitivos separados por "Nombre del producto:"
"""
import re
import fitz


def _limpiar(s: str) -> str:
    return s.strip().rstrip(" \xa0")

def _valor(linea: str, prefijo: str) -> str:
    if prefijo in linea:
        return _limpiar(linea.split(prefijo, 1)[1].lstrip(": "))
    return ""

def _n(s: str) -> float:
    try:
        return float(s.strip().replace(",", "."))
    except:
        return 0.0

RE_IF = re.compile(r"IF-\d{4}-\d+-APN-[\w#]+")


def extraer_dj_origen(pdf_bytes: bytes) -> dict:
    doc = fitz.open(stream=pdf_bytes, filetype="pdf")
    lineas = []
    for page in doc:
        lineas.extend(page.get_text().splitlines())
    doc.close()

    numero_if = ""
    empresa = ""
    fecha = ""
    productos = []

    # ── Encabezado ──
    for i, l in enumerate(lineas):
        s = l.strip()
        if s.startswith("RAZÓN SOCIAL:") and not empresa:
            empresa = _valor(s, "RAZÓN SOCIAL:")
        if not fecha:
            mf = re.search(r"(Lunes|Martes|Miércoles|Jueves|Viernes|Sábado|Domingo)\s+\d{1,2}\s+de\s+\w+\s+de\s+\d{4}", s)
            if mf:
                fecha = mf.group(0)
        # IF correcto: línea IF que NO está en DOCUMENTO DE REFERENCIA
        # y que aparece seguida de "CIUDAD DE BUENOS AIRES"
        m = RE_IF.search(s)
        if m and not s.startswith("DOCUMENTO DE REFERENCIA"):
            siguiente = lineas[i+1].strip() if i+1 < len(lineas) else ""
            if "CIUDAD DE BUENOS AIRES" in siguiente or not numero_if:
                numero_if = m.group(0)

    # ── Productos: dividir por "Nombre del producto:" ──
    bloques = []
    bloque_actual = []
    for l in lineas:
        s = l.strip()
        if s.startswith("Nombre del producto:"):
            if bloque_actual:
                bloques.append(bloque_actual)
            bloque_actual = [s]
        elif bloque_actual:
            bloque_actual.append(s)
    if bloque_actual:
        bloques.append(bloque_actual)

    for bloque in bloques:
        prod = _parsear_producto(bloque)
        if prod:
            productos.append(prod)

    return {
        "numero_if": numero_if,
        "empresa":   empresa,
        "fecha":     fecha,
        "productos": productos,
    }


def _parsear_producto(lineas: list) -> dict | None:
    p = {
        "codigo_parte":     "",
        "descripcion":      "",
        "ncm_8_digitos":    "",
        "ncm_sim_3":        "",
        "pais_origen":      "",
        "unidad_medida":    "",
        "cantidad":         0.0,
        "valor_cif_unit":   0.0,
    }
    for l in lineas:
        s = l.strip()
        if s.startswith("Nombre del producto:"):
            p["codigo_parte"] = _valor(s, "Nombre del producto:")
        elif s.startswith("Descripción del producto:"):
            p["descripcion"] = _valor(s, "Descripción del producto:")
        elif s.startswith("Clasificación arancelaria a 8 dígitos:"):
            p["ncm_8_digitos"] = _valor(s, "Clasificación arancelaria a 8 dígitos:")
        elif s.startswith("Últimos 3 dígitos de la posición SIM:"):
            p["ncm_sim_3"] = _valor(s, "Últimos 3 dígitos de la posición SIM:")
        elif s.startswith("País de origen:"):
            p["pais_origen"] = _valor(s, "País de origen:")
        elif s.startswith("Unidad de medida:"):
            p["unidad_medida"] = _valor(s, "Unidad de medida:")
        elif s.startswith("Cantidad importada:"):
            p["cantidad"] = _n(_valor(s, "Cantidad importada:"))
        elif s.startswith("Valor CIF unitario:"):
            p["valor_cif_unit"] = _n(_valor(s, "Valor CIF unitario:"))

    if not p["codigo_parte"]:
        return None
    return p


if __name__ == "__main__":
    import sys, json
    with open(sys.argv[1], "rb") as f:
        data = extraer_dj_origen(f.read())
    print(json.dumps(data, indent=2, ensure_ascii=False))
