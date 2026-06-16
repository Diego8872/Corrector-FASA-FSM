import pandas as pd
import re

def leer_di(file) -> dict:
    """Lee el Excel del DI y retorna un dict con todas las solapas relevantes."""
    xl = pd.ExcelFile(file)
    sheets = {s.lower(): s for s in xl.sheet_names}
    resultado = {}

    s = _find_sheet(sheets, ["caratula", "carátula"])
    if s:
        resultado["caratula"] = _leer_caratula(xl, s)

    s = _find_sheet(sheets, ["item"])
    if s:
        resultado["items"] = _leer_items(xl, s)

    s = _find_sheet(sheets, ["subitem"])
    if s:
        resultado["subitems"] = _leer_subitems(xl, s)

    liq_sheet = None
    for k, v in sheets.items():
        if "liquid" in k and ("ítem" in k or "item" in k):
            liq_sheet = v
            break
    if not liq_sheet:
        liq_sheet = _find_sheet(sheets, ["liquid"])
    if liq_sheet:
        resultado["liquidacion"] = _leer_liquidacion(xl, liq_sheet)

    s = _find_sheet(sheets, ["bulto"])
    if s:
        resultado["bultos"] = _leer_bultos(xl, s)

    return resultado


def _find_sheet(sheets: dict, keywords: list):
    for kw in keywords:
        for k, v in sheets.items():
            if kw in k:
                return v
    return None


def _leer_caratula(xl, sheet_name) -> dict:
    df = xl.parse(sheet_name, header=None)
    data = {}
    if len(df) >= 2:
        headers = df.iloc[0]
        valores  = df.iloc[1]
        for i in range(len(headers)):
            key = str(headers.iloc[i]).strip() if pd.notna(headers.iloc[i]) else ""
            val = str(valores.iloc[i]).strip()  if pd.notna(valores.iloc[i])  else ""
            if key and key != "nan":
                data[key] = val if val != "nan" else ""
    return data


def _leer_items(xl, sheet_name) -> pd.DataFrame:
    df = xl.parse(sheet_name, dtype=str)
    df.columns = [str(c).strip().upper() for c in df.columns]
    df = df.fillna("")
    if "ITEM" in df.columns:
        df = df[df["ITEM"].str.strip() != ""]
    return df


def _leer_subitems(xl, sheet_name) -> pd.DataFrame:
    df = xl.parse(sheet_name, dtype=str)
    df.columns = [str(c).strip().upper() for c in df.columns]
    return df.fillna("")


def _leer_liquidacion(xl, sheet_name) -> pd.DataFrame:
    df = xl.parse(sheet_name, dtype=str)
    df.columns = [str(c).strip().upper() for c in df.columns]
    return df.fillna("")


def _leer_bultos(xl, sheet_name) -> pd.DataFrame:
    df = xl.parse(sheet_name, dtype=str)
    df.columns = [str(c).strip().upper() for c in df.columns]
    return df.fillna("")


def normalizar_codigo(codigo: str) -> str:
    """
    Normaliza código de parte para comparación:
      1. Quita guiones y espacios
      2. Convierte a mayúsculas
      3. Aplica regla estructural CAT (misma que el parser de factura):
         - Empieza con 3 dígitos → tomar primeros 7 chars
         - Tiene letra inicial   → tomar primeros 6 chars
    Esto elimina cualquier sufijo de origen (J, VN, CO, (, ), etc.)
    sin necesidad de conocerlos de antemano.
    """
    s = re.sub(r'[-\s]', '', codigo.strip().upper())
    if not s:
        return s
    if re.match(r'^\d{3}', s):
        return s[:7]
    else:
        return s[:6]


def safe_float(val) -> float:
    try:
        return float(str(val).replace(",", ".").strip())
    except Exception:
        return 0.0
