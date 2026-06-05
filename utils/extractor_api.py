import anthropic
import base64
import json
import re
import streamlit as st

def get_client():
    api_key = st.secrets.get("ANTHROPIC_API_KEY", "")
    return anthropic.Anthropic(api_key=api_key)

def pdf_to_base64(file_bytes: bytes) -> str:
    return base64.standard_b64encode(file_bytes).decode("utf-8")

def _llamar_claude(system_prompt: str, user_prompt: str, pdfs: list) -> str:
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
    response = client.messages.create(
        model="claude-sonnet-4-20250514",
        max_tokens=4096,
        system=system_prompt,
        messages=[{"role": "user", "content": content}]
    )
    return response.content[0].text

def _parse_json(texto: str) -> dict:
    texto = re.sub(r"```json|```", "", texto).strip()
    return json.loads(texto)


# ─── FACTURA ─────────────────────────────────────────────────────────────────

def extraer_factura(pdf_bytes: bytes) -> dict:
    system = "Sos un experto en comercio exterior argentino. Analizás facturas comerciales de Caterpillar. Respondé SOLO con JSON válido, sin texto adicional."
    prompt = """Extraé los datos de esta factura en JSON:
{
  "numero_factura": "...",
  "fecha": "...",
  "vendedor": "...",
  "moneda": "...",
  "incoterm": "...",
  "items": [
    {
      "numero_item": 1,
      "codigo_parte": "...",
      "descripcion": "...",
      "cantidad": 0,
      "precio_unitario": 0.0,
      "precio_total_parte": 0.0,
      "cargos_propios": 0.0,
      "subtotal": 0.0,
      "origen": "..."
    }
  ],
  "total_partes": 0.0,
  "total_cargos": 0.0,
  "total_factura": 0.0,
  "tipo_cargos": "por_item | global | mixto"
}
IMPORTANTE: codigo_parte sin guiones. tipo_cargos: 'por_item' si cada ítem tiene sus propios cargos, 'global' si el cargo está solo al final, 'mixto' si hay ambos."""
    try:
        return _parse_json(_llamar_claude(system, prompt, [pdf_bytes]))
    except Exception as e:
        return {"error": str(e)}


# ─── FORWARDING INVOICE ───────────────────────────────────────────────────────

def extraer_forwarding(pdf_bytes: bytes) -> dict:
    system = "Sos un experto en comercio exterior argentino. Analizás forwarding invoices. Respondé SOLO con JSON válido."
    prompt = """Extraé los datos en JSON:
{
  "numero_invoice": "...",
  "fecha": "...",
  "incoterm": "...",
  "flete_total": 0.0,
  "detalle_flete": [{"concepto": "...", "monto": 0.0}],
  "seguro_marine_premium": 0.0,
  "seguro_war_premium": 0.0,
  "seguro_otros": [],
  "seguro_total": 0.0,
  "otros_cargos": [],
  "moneda": "USD",
  "alertas": []
}
IMPORTANTE: flete_total = Total Charge to Caterpillar. seguro_total = Marine + War + otros. alertas: cargos con valor > 0 que no sean flete ni seguro."""
    try:
        return _parse_json(_llamar_claude(system, prompt, [pdf_bytes]))
    except Exception as e:
        return {"error": str(e)}


# ─── BILL OF LADING ───────────────────────────────────────────────────────────

def extraer_bl(pdf_bytes: bytes) -> dict:
    system = "Sos un experto en comercio exterior argentino. Analizás Bills of Lading. Respondé SOLO con JSON válido."
    prompt = """Extraé los datos en JSON:
{
  "bl_number": "...",
  "fecha_embarque": "...",
  "itns": [],
  "contenedor": "...",
  "puerto_carga": "...",
  "puerto_descarga": "...",
  "vessel": "...",
  "facturas_incluidas": []
}
IMPORTANTE: fecha_embarque = texto que diga SHIPPED ON BOARD. itns = números AES-ITN del documento. bl_number sin código de puerto."""
    try:
        return _parse_json(_llamar_claude(system, prompt, [pdf_bytes]))
    except Exception as e:
        return {"error": str(e)}


# ─── CM (CE + RE) ─────────────────────────────────────────────────────────────

def extraer_cm(pdf_ce_bytes: bytes, pdf_re_bytes: bytes) -> dict:
    system = "Sos un experto en Certificados Mineros (Ley 24.196). Respondé SOLO con JSON válido."
    prompt = """Analizá el CE y RE del Certificado Minero. Extraé en JSON:
{
  "numero_ce": "...",
  "numero_re": "...",
  "empresa": "...",
  "cuit": "...",
  "fecha_emision": "...",
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
IMPORTANTE: ncm_8_digitos = primeros 8 dígitos sin puntos. Los ítems están en el RE."""
    try:
        return _parse_json(_llamar_claude(system, prompt, [pdf_ce_bytes, pdf_re_bytes]))
    except Exception as e:
        return {"error": str(e)}


# ─── DJ ORIGEN NO PREFERENCIAL ────────────────────────────────────────────────

def extraer_dj_origen(pdf_bytes: bytes) -> dict:
    system = "Sos un experto en comercio exterior argentino. Analizás Declaraciones Juradas de Origen No Preferencial. Respondé SOLO con JSON válido."
    prompt = """Analizá esta DJ de Origen No Preferencial y extraé en JSON:
{
  "numero_if": "...",
  "empresa": "...",
  "fecha": "...",
  "items": [
    {
      "ncm": "...",
      "ncm_8_digitos": "...",
      "codigo_material": "...",
      "origen": "...",
      "cantidad": 0,
      "unidad_medida": "...",
      "valor_cif": 0.0
    }
  ]
}
IMPORTANTE: numero_if = número completo IF-XXXX-XXXXXXXX del documento. ncm_8_digitos = primeros 8 dígitos sin puntos."""
    try:
        return _parse_json(_llamar_claude(system, prompt, [pdf_bytes]))
    except Exception as e:
        return {"error": str(e)}
