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


# ─── EXTRACCIÓN FORWARDING INVOICE ──────────────────────────────────────────

def extraer_forwarding(pdf_bytes: bytes) -> dict:
    """Extrae Forwarding Invoice CAT/DHL usando PyMuPDF + regex. Sin API."""
    from utils.parser_forwarding import extraer_forwarding as _extraer
    return _extraer(pdf_bytes)


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
    """Extrae CM usando PyMuPDF + regex. Sin API, sin costo."""
    from utils.parser_cm import extraer_cm as _extraer_cm
    return _extraer_cm(pdf_ce_bytes, pdf_re_bytes)


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
