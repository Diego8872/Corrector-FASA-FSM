import anthropic
import time
import base64
import json
import re
import streamlit as st

def get_client():
    api_key = st.secrets.get("ANTHROPIC_API_KEY", "")
    return anthropic.Anthropic(api_key=api_key)

def pdf_to_base64(file_bytes: bytes) -> str:
    return base64.standard_b64encode(file_bytes).decode("utf-8")

def _llamar_claude(system_prompt: str, user_prompt: str, pdfs: list, modelo: str = "claude-sonnet-4-5-20250929", max_tokens: int = 8192) -> str:
    client = get_client()
    content = []
    for pdf in pdfs:
        content.append({
            "type": "document",
            "source": {
                "type": "base64",
                "media_type": "application/pdf",
                "data": pdf_to_base64(pdf),
            }
        })
    content.append({"type": "text", "text": user_prompt})

    for intento in range(3):
        try:
            response = client.messages.create(
                model=modelo,
                max_tokens=max_tokens,
                system=system_prompt,
                messages=[{"role": "user", "content": content}]
            )
            return response.content[0].text
        except Exception as e:
            if "rate_limit" in str(e) and intento < 2:
                time.sleep(15 * (intento + 1))
                continue
            raise

def _parse_json(texto: str) -> dict | list:
    texto = re.sub(r"```json|```", "", texto).strip()
    return json.loads(texto)

# ─── EXTRACCIÓN FACTURA ───────────────────────────────────────────────────────

def extraer_factura(pdf_bytes: bytes) -> dict:
    """Extrae factura CAT usando PyMuPDF + regex. Sin API, sin costo."""
    from utils.parser_factura_cat import extraer_factura_cat
    return extraer_factura_cat(pdf_bytes)


# ─── EXTRACCIÓN FORWARDING INVOICE ───────────────────────────────────────────

def extraer_forwarding(pdf_bytes: bytes) -> dict:
    system = """Sos un experto en comercio exterior argentino.
Analizás forwarding invoices de DHL/Caterpillar y extraés datos con precisión.
Respondé SOLO con JSON válido, sin texto adicional."""

    prompt = """Analizá esta Forwarding Invoice y extraé los datos en formato JSON:
{
  "numero_invoice": "...",
  "fecha": "...",
  "incoterm": "...",
  "flete_total": 0.0,
  "detalle_flete": [
    {"concepto": "...", "monto": 0.0}
  ],
  "seguro_marine_premium": 0.0,
  "seguro_war_premium": 0.0,
  "seguro_otros": [],
  "seguro_total": 0.0,
  "otros_cargos": [],
  "total_invoice_dealer": 0.0,
  "moneda": "USD",
  "alertas": []
}

IMPORTANTE:
- flete_total: es el "Total Charge to Caterpillar" (suma de todos los conceptos de flete/forwarding)
- seguro_total: Marine Premium + War Premium + cualquier otro concepto de seguro
- otros_cargos: cualquier cargo que NO sea flete ni seguro con monto > 0
- alertas: si hay "Finance Charges to Dealer" u "Other Charges" con valor > 0, incluirlos como alerta"""

    try:
        texto = _llamar_claude(system, prompt, [pdf_bytes])
        return _parse_json(texto)
    except Exception as e:
        return {"error": str(e)}


# ─── EXTRACCIÓN BL ───────────────────────────────────────────────────────────

def extraer_bl(pdf_bytes: bytes) -> dict:
    system = """Sos un experto en comercio exterior argentino.
Analizás Bills of Lading y extraés datos con precisión.
Respondé SOLO con JSON válido, sin texto adicional."""

    prompt = """Analizá este Bill of Lading y extraé los datos en formato JSON:
{
  "bl_number": "...",
  "fecha_embarque": "...",
  "itns": [],
  "contenedor": "...",
  "puerto_carga": "...",
  "puerto_descarga": "...",
  "vessel": "...",
  "shipper": "...",
  "consignee": "...",
  "facturas_incluidas": []
}

IMPORTANTE:
- fecha_embarque: buscar "SHIPPED ON BOARD" en el texto del documento
- itns: buscar todos los números que aparezcan como "AES-ITN" en el documento
- bl_number: el número de BL del encabezado (sin código de puerto)
- facturas_incluidas: números de facturas mencionadas en la descripción de la mercadería"""

    try:
        texto = _llamar_claude(system, prompt, [pdf_bytes])
        return _parse_json(texto)
    except Exception as e:
        return {"error": str(e)}


# ─── EXTRACCIÓN CM (CE + RE) ──────────────────────────────────────────────────

def extraer_cm(pdf_ce_bytes: bytes, pdf_re_bytes: bytes) -> dict:
    system = """Sos un experto en comercio exterior argentino especializado en 
Certificados Mineros (Ley 24.196). Extraés datos con precisión absoluta.
Respondé SOLO con JSON válido, sin texto adicional."""

    prompt = """Analizá estos dos documentos del Certificado Minero:
1. CE (Certificado de Autorización de Importación)  
2. RE (Solicitud/Resolución con el detalle de mercadería)

Extraé los datos en formato JSON:
{
  "numero_ce": "...",
  "numero_re": "...",
  "empresa": "...",
  "cuit": "...",
  "fecha_emision": "...",
  "validez_dias": 180,
  "numero_factura": "...",
  "fob_total": 0.0,
  "items": [
    {
      "ncm": "...",
      "ncm_8_digitos": "...",
      "descripcion": "...",
      "codigo_parte": "...",
      "cantidad": 0,
      "unidad": "...",
      "valor_unitario_fob": 0.0,
      "valor_total_fob": 0.0,
      "marca": "...",
      "origen": "..."
    }
  ]
}

IMPORTANTE:
- ncm_8_digitos: solo los primeros 8 dígitos del NCM (sin puntos)
- codigo_parte: el código de parte exacto como figura en el documento
- Los datos de los ítems están en el RE, no en el CE
- El CE contiene datos generales (empresa, número, fecha)"""

    try:
        texto = _llamar_claude(system, prompt, [pdf_ce_bytes, pdf_re_bytes],
                               modelo="claude-haiku-4-5", max_tokens=8192)
        return _parse_json(texto)
    except Exception as e:
        return {"error": str(e)}


# ─── EXTRACCIÓN DJ ORIGEN NO PREFERENCIAL ────────────────────────────────────

def extraer_dj_origen(pdf_bytes: bytes) -> dict:
    system = """Sos un experto en comercio exterior argentino.
Analizás Declaraciones Juradas de Origen No Preferencial del sistema GDE/TAD.
Respondé SOLO con JSON válido, sin texto adicional."""

    prompt = """Analizá este PDF de Declaración Jurada de Origen No Preferencial y extraé:
{
  "numero_if": "IF-2026-XXXXXXXX-APN-...",
  "empresa": "...",
  "fecha": "...",
  "descripcion": "..."
}

IMPORTANTE:
- numero_if: el número completo del documento IF que figura en el encabezado
- Si no encontrás un número IF, buscá cualquier número de expediente o referencia del documento"""

    try:
        texto = _llamar_claude(system, prompt, [pdf_bytes])
        return _parse_json(texto)
    except Exception as e:
        return {"error": str(e)}


# ─── EXTRAER NÚMERO RE DEL CE ─────────────────────────────────────────────────

def extraer_numero_re_de_ce(pdf_bytes: bytes) -> str:
    """Extrae el número RE del CE usando PyMuPDF sin gastar API."""
    try:
        import fitz, re as _re
        doc = fitz.open(stream=pdf_bytes, filetype="pdf")
        texto = "".join(page.get_text() for page in doc)
        matches = _re.findall(r"RE-[0-9]{4}-[0-9]+[-\w#]+", texto)
        return matches[0] if matches else ""
    except Exception as e:
        return ""
