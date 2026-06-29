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
    from utils_fasa.parser_factura_cat import extraer_factura_cat
    return extraer_factura_cat(pdf_bytes)


# ─── EXTRACCIÓN FORWARDING INVOICE ──────────────────────────────────────────

def extraer_forwarding(pdf_bytes: bytes) -> dict:
    """Extrae Forwarding Invoice CAT/DHL usando PyMuPDF + regex. Sin API."""
    from utils_fasa.parser_forwarding import extraer_forwarding as _extraer
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
  "facturas_incluidas": [],
  "cantidad_contenedores": 0,
  "cantidad_bultos": 0,
  "peso_bruto_kg": 0
}

IMPORTANTE:
- fecha_embarque: buscar "SHIPPED ON BOARD" en el texto del documento
- itns: buscar todos los números que aparezcan como "AES-ITN" en el documento
- bl_number: el número de BL del encabezado (sin código de puerto)
- facturas_incluidas: números de facturas mencionadas en la descripción de la mercadería

El formato y la terminología del BL varían según la naviera (Maersk, MSC, Hapag-Lloyd, CMA CGM,
COSCO, etc.) — no asumas que un término exacto va a estar siempre presente. Para los siguientes
3 campos, razoná sobre la estructura del documento (normalmente hay un cuadro/resumen de carga
con totales, cerca de la descripción de mercadería o del pie del documento) y usá la información
disponible, sea cual sea el rótulo exacto que use esa naviera:

- cantidad_contenedores: cantidad total de contenedores del embarque. Puede figurar como
  "No. of Containers", "Container(s)", "Qty of Containers", "TOTAL CONTAINERS", o simplemente
  inferirse contando los números de contenedor individuales listados (formato tipo ABCD1234567)
  si no hay un total explícito. Si el BL no es de carga en contenedores (ej. carga suelta/break
  bulk), devolver 0.
- cantidad_bultos: cantidad total de bultos/piezas/paquetes sueltos declarados COMO UNIDAD DE
  TRANSPORTE INDEPENDIENTE (es decir, que no están dentro de un contenedor cerrado). Puede
  figurar como "No. of Packages", "PACKAGES", "PKGS", "Number of Packages", "Total Packages".
  OJO: en embarques FCL (contenedor completo, Movement = "FCL/FCL" o similar), suele aparecer
  además un número de piezas/cajas DENTRO de la descripción de mercadería (ej. "61 PIECE",
  "150 CARTONS") — eso es solo el detalle interno del contenido del contenedor, NO es un bulto
  suelto independiente, y en ese caso cantidad_bultos debe ser 0. Usá cantidad_bultos > 0
  únicamente cuando la carga viaja suelta, sin contenedor (carga general / break bulk / LCL con
  piezas sueltas como unidad de transporte).
- peso_bruto_kg: peso bruto TOTAL del embarque, en kilogramos. Puede figurar como
  "GROSS WEIGHT", "G.W.", "Gross Wt", "Weight", "Total Weight". Si el documento expresa el peso
  en otra unidad (ej. libras, toneladas), convertilo a kilogramos antes de devolverlo. Si hay
  varios pesos parciales (por contenedor o por bulto) y no hay un total explícito, sumalos para
  obtener el total. Devolver solo el número, sin unidad ni texto.

  OJO CON EL FORMATO DEL NÚMERO: muchas navieras (ej. Maersk) expresan el peso con 3 dígitos
  decimales reales después del punto, no separador de miles — ej. "9128.100 KGS" significa
  NUEVE MIL CIENTO VEINTIOCHO KILOGRAMOS CON CIEN GRAMOS (9128.1 kg), NO nueve millones cientos
  veintiocho mil cien kilogramos. En el contexto de un Bill of Lading, un peso de varios millones
  de kg para un solo contenedor o embarque chico es prácticamente imposible — si tu lectura da un
  número así de grande, revisalo: lo más probable es que el punto sea decimal, no separador de
  miles. Guiate por la magnitud físicamente razonable del peso (decenas a pocas miles de kg por
  contenedor estándar), no por la cantidad de dígitos después del separador."""

    try:
        texto = _llamar_claude(system, prompt, [pdf_bytes])
        return _parse_json(texto)
    except Exception as e:
        return {"error": str(e)}


# ─── EXTRACCIÓN CM (CE + RE) ──────────────────────────────────────────────────

def extraer_cm(pdf_ce_bytes: bytes, pdf_re_bytes: bytes) -> dict:
    """Extrae CM usando PyMuPDF + regex. Sin API, sin costo."""
    from utils_fasa.parser_cm import extraer_cm as _extraer_cm
    return _extraer_cm(pdf_ce_bytes, pdf_re_bytes)


# ─── EXTRACCIÓN DJ ORIGEN NO PREFERENCIAL ────────────────────────────────────

def extraer_dj_origen(pdf_bytes: bytes) -> dict:
    """Extrae DJ de Origen usando PyMuPDF + regex. Sin API."""
    from utils_fasa.parser_dj_origen import extraer_dj_origen as _extraer
    return _extraer(pdf_bytes)


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
