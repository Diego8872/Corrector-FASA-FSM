"""
Parser PyMuPDF + regex para CMs (CE + RE) de Inversiones Mineras (Ley 24.196)
Sin uso de API — extracción local gratuita.

CE (Certificado de Autorización de Importación):
  - Número CE: línea "CE-2026-XXXXXXXX-APN-DIMI#MEC"
  - Número RE: en el cuerpo "RE-2026-XXXXXXXX-APN-DGDA#MEC"
  - Fecha: línea con formato "Martes 26 de Mayo de 2026"
  - Empresa y CUIT: en el cuerpo del texto

RE (Solicitud de Importación Actividad Minera):
  - Número RE: línea "RE-2026-XXXXXXXX-APN-DGDA#MEC"
  - FOB total: "Valor FOB TOTAL ... : 27655,12"
  - Número de factura: "Número de Factura: Z95046356"
  - Ítems: bloques repetitivos con campos etiquetados
    Separador de bloque: "Posible cantidad de repetidores (máximo 30)"
    Campos: Descripción, Cantidad, Unidad de Medida, NCM, Valor unitario FOB,
            Valor total FOB, Código de parte, Marca, Modelo de la maquina

Caso especial: mismo código de parte repetido con mismo NCM y mismo FOB
→ se consolidan sumando cantidad y valor_total
"""

import re
import fitz  # PyMuPDF


# ── Utilidades ────────────────────────────────────────────────────────────────

def _n(s: str) -> float:
    """
    '27655,12' o '27655.12' → 27655.12 (también soporta separador de
    miles combinado con decimal, ej. '1.234,56' o '1,234.56').

    El símbolo que aparece MÁS A LA DERECHA es el separador decimal; el
    otro (si aparece) es de miles y se descarta. Si solo aparece un
    símbolo una vez con 1-2 dígitos después, se interpreta como decimal
    directo (no se asume formato fijo, a diferencia de la versión
    anterior que siempre asumía coma decimal y rompía valores con punto
    decimal real, ej. '21098.11' -> 2109811.0 antes de este fix).
    """
    try:
        t = s.strip()
        tiene_coma = "," in t
        tiene_punto = "." in t
        if tiene_coma and tiene_punto:
            if t.rfind(",") > t.rfind("."):
                t = t.replace(".", "").replace(",", ".")
            else:
                t = t.replace(",", "")
        elif tiene_coma:
            t = t.replace(",", ".")
        return float(t)
    except Exception:
        return 0.0

def _limpiar(s: str) -> str:
    return s.strip().rstrip(" \xa0")

def _extraer_valor(linea: str, prefijo: str) -> str:
    """Extrae valor después de 'Prefijo: valor'"""
    if prefijo in linea:
        return _limpiar(linea.split(prefijo, 1)[1].lstrip(": "))
    return ""


# ── Regex ─────────────────────────────────────────────────────────────────────

RE_NUM_CE   = re.compile(r"CE-\d{4}-\d+-APN-DIMI#MEC")
RE_NUM_RE   = re.compile(r"RE-\d{4}-\d+-APN-DGDA#MEC")
RE_FECHA_CE = re.compile(r"(Lunes|Martes|Miércoles|Jueves|Viernes|Sábado|Domingo)\s+\d{1,2}\s+de\s+\w+\s+de\s+\d{4}")
RE_CUIT     = re.compile(r"CUIT\s+N[°º]\s*([\d\-]+)")
RE_FOB_TOT  = re.compile(r"Certificado\):\s*([\d\.,]+)")
RE_NCM      = re.compile(r"Posición Arancelaria - NCM - Seleccionar uno:\s*([\d\.]+)")
RE_SEPARADOR = re.compile(r"Posible cantidad de repetidores")


# ── Parser CE ─────────────────────────────────────────────────────────────────

def _parsear_ce(pdf_bytes: bytes) -> dict:
    doc = fitz.open(stream=pdf_bytes, filetype="pdf")
    texto = "\n".join(page.get_text() for page in doc)
    doc.close()

    numero_ce = ""
    numero_re = ""
    fecha = ""
    empresa = "FINNING SOLUCIONES MINERAS S.A."
    cuit = ""

    m = RE_NUM_CE.search(texto)
    if m:
        numero_ce = m.group(0)

    m = RE_NUM_RE.search(texto)
    if m:
        numero_re = m.group(0)

    m = RE_FECHA_CE.search(texto)
    if m:
        fecha = m.group(0)

    # CUIT puede estar partido en dos líneas; buscar en texto unificado
    texto_unif = " ".join(texto.split())
    m = re.search(r"CUIT N[°º]?\s*(\d{2}-\d+-\d)", texto_unif)
    if m:
        cuit = m.group(1)

    return {
        "numero_ce": numero_ce,
        "numero_re": numero_re,
        "fecha_emision": fecha,
        "empresa": empresa,
        "cuit": cuit,
        "validez_dias": 180,
    }


# ── Parser RE ─────────────────────────────────────────────────────────────────

def _parsear_re(pdf_bytes: bytes) -> dict:
    doc = fitz.open(stream=pdf_bytes, filetype="pdf")
    # Concatenar todo el texto
    lineas = []
    for page in doc:
        lineas.extend(page.get_text().splitlines())
    doc.close()

    numero_re = ""
    fob_total = 0.0
    numero_factura = ""
    items_raw = []

    # ── Datos generales ──
    for linea in lineas:
        l = linea.strip()
        if not numero_re:
            m = RE_NUM_RE.search(l)
            if m:
                numero_re = m.group(0)
        if not fob_total:
            m = RE_FOB_TOT.search(l)
            if m:
                fob_total = _n(m.group(1))
        if not numero_factura and "Número de Factura:" in l:
            numero_factura = _limpiar(l.split("Número de Factura:", 1)[1])

    # ── Parsear ítems por bloques ──
    # Separador: "Posible cantidad de repetidores (máximo 30)"
    # Cada bloque contiene los campos de un ítem
    bloques = _dividir_bloques(lineas)

    for bloque in bloques:
        item = _parsear_bloque(bloque)
        if item:
            items_raw.append(item)

    items = _consolidar_re(items_raw)

    return {
        "numero_re":      numero_re,
        "numero_factura": numero_factura,
        "fob_total":      fob_total,
        "items":          items,
    }


def _dividir_bloques(lineas: list) -> list:
    """Divide las líneas en bloques usando el separador de ítem."""
    bloques = []
    bloque_actual = []
    en_items = False

    for linea in lineas:
        l = linea.strip()
        if RE_SEPARADOR.search(l):
            if bloque_actual:
                bloques.append(bloque_actual)
            bloque_actual = []
            en_items = True
            continue
        if en_items:
            bloque_actual.append(l)

    if bloque_actual:
        bloques.append(bloque_actual)

    return bloques


def _parsear_bloque(lineas: list) -> dict | None:
    """Extrae los campos de un bloque de ítem."""
    campos = {
        "descripcion":        "",
        "cantidad":           0.0,
        "unidad":             "",
        "ncm":                "",
        "ncm_8_digitos":      "",
        "valor_unitario_fob": 0.0,
        "valor_total_fob":    0.0,
        "codigo_parte":       "",
        "marca":              "CATERPILLAR",
        "origen":             "",
    }

    i = 0
    while i < len(lineas):
        l = lineas[i]

        if "Descripción detallada del bien" in l:
            campos["descripcion"] = _limpiar(l.split("insumo:", 1)[-1]) if "insumo:" in l else ""
        elif l.startswith("Cantidad:"):
            campos["cantidad"] = _n(l.split(":", 1)[1])
        elif "Unidad de Medida:" in l:
            unidad = _limpiar(l.split(":", 1)[1])
            if "Otras" in unidad and i+1 < len(lineas) and "Especificar:" in lineas[i+1]:
                unidad = _limpiar(lineas[i+1].split(":", 1)[1])
            campos["unidad"] = unidad
        elif "Posición Arancelaria - NCM" in l:
            m = RE_NCM.search(l)
            if m:
                ncm_raw = m.group(1).replace(".", "")
                campos["ncm"] = m.group(1)
                campos["ncm_8_digitos"] = ncm_raw[:8]
        elif "Valor unitario/por unidad" in l:
            campos["valor_unitario_fob"] = _n(l.split(":")[-1])
        elif "Valor total de los artículo" in l:
            campos["valor_total_fob"] = _n(l.split(":")[-1])
        elif 'Código de parte' in l:
            val = _limpiar(l.split(")")[-1].lstrip(": ")) if ")" in l else _limpiar(l.split(":")[-1])
            if val.upper() != "NO POSEE":
                campos["codigo_parte"] = val
        elif "Número de Factura:" in l:
            pass  # ya procesado a nivel general
        elif "Clasificación de artículo:" in l:
            pass

        i += 1

    # Ignorar bloques vacíos o sin código de parte y sin NCM
    if not campos["ncm"] and not campos["codigo_parte"]:
        return None

    return campos


def _consolidar_re(items_raw: list) -> list:
    """
    NO consolida — preserva cada entrada del RE como ítem separado.
    El mismo código puede aparecer varias veces con distintas cantidades/valores
    porque corresponde a distintos ítems del DI.
    Solo elimina duplicados exactos (mismo código + misma cantidad + mismo valor_total).
    """
    vistos = set()
    resultado = []

    for item in items_raw:
        key = (
            item["codigo_parte"].upper(),
            item["ncm_8_digitos"],
            item["cantidad"],
            item["valor_total_fob"],
        )
        if key not in vistos:
            vistos.add(key)
            resultado.append(item.copy())

    for idx, it in enumerate(resultado, 1):
        it["numero_item"] = idx

    return resultado


# ── Parser CM completo (CE + RE) ──────────────────────────────────────────────

def extraer_cm(pdf_ce_bytes: bytes, pdf_re_bytes: bytes) -> dict:
    """
    Reemplaza extraer_cm() de extractor_api.py.
    Retorna dict compatible con el formato anterior.
    """
    ce = _parsear_ce(pdf_ce_bytes)
    re_ = _parsear_re(pdf_re_bytes)

    # Mapear ítems al formato esperado por cruce_docs.py
    items_mapeados = []
    for it in re_["items"]:
        items_mapeados.append({
            "numero_item":       it.get("numero_item", 0),
            "ncm":               it["ncm"],
            "ncm_8_digitos":     it["ncm_8_digitos"],
            "descripcion":       it["descripcion"],
            "codigo_parte":      it["codigo_parte"],
            "cantidad":          it["cantidad"],
            "unidad":            it["unidad"],
            "valor_unitario_fob": it["valor_unitario_fob"],
            "valor_total_fob":   it["valor_total_fob"],
            "marca":             it.get("marca", "CATERPILLAR"),
            "origen":            it.get("origen", ""),
        })

    return {
        "numero_ce":     ce["numero_ce"],
        "numero_re":     ce["numero_re"] or re_["numero_re"],
        "empresa":       ce["empresa"],
        "cuit":          ce["cuit"],
        "fecha_emision": ce["fecha_emision"],
        "validez_dias":  180,
        "numero_factura": re_["numero_factura"],
        "fob_total":     re_["fob_total"],
        "items":         items_mapeados,
    }


# ── Test CLI ──────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    import sys

    ce_path = sys.argv[1]
    re_path = sys.argv[2]

    with open(ce_path, "rb") as f:
        ce_bytes = f.read()
    with open(re_path, "rb") as f:
        re_bytes = f.read()

    data = extraer_cm(ce_bytes, re_bytes)

    print(f"\n=== CM ===")
    print(f"CE:      {data['numero_ce']}")
    print(f"RE:      {data['numero_re']}")
    print(f"Empresa: {data['empresa']} | CUIT: {data['cuit']}")
    print(f"Fecha:   {data['fecha_emision']}")
    print(f"Factura: {data['numero_factura']} | FOB Total: USD {data['fob_total']:,.2f}")
    print(f"Ítems:   {len(data['items'])}\n")
    for it in data["items"]:
        print(
            f"  [{it.get('numero_item','-'):2}] {it['codigo_parte']:<12} "
            f"NCM:{it['ncm']:<12} "
            f"Qty:{it['cantidad']:>8,.0f}  "
            f"U:{it['valor_unitario_fob']:>10,.2f}  "
            f"Tot:{it['valor_total_fob']:>12,.2f}"
        )
    print(f"\nSuma ítems: USD {sum(i['valor_total_fob'] for i in data['items']):,.2f}")
    print(f"FOB total:  USD {data['fob_total']:,.2f}")
