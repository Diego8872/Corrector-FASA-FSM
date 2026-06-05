import pandas as pd
import re

def leer_di(file) -> dict:
    """Lee el Excel del DI y retorna un dict con todas las solapas relevantes."""
    xl = pd.ExcelFile(file)
    sheets = {s.lower(): s for s in xl.sheet_names}

    resultado = {}

    # Carátula
    s = _find_sheet(sheets, ["caratula", "carátula"])
    if s:
        resultado["caratula"] = _leer_caratula(xl, s)

    # Item
    s = _find_sheet(sheets, ["item"])
    if s:
        resultado["items"] = _leer_items(xl, s)

    # Subitem
    s = _find_sheet(sheets, ["subitem"])
    if s:
        resultado["subitems"] = _leer_subitems(xl, s)

    # Liquidación ítem — buscar específicamente la solapa por ítem (no la de totales)
    liq_sheet = None
    for k, v in sheets.items():
        if "liquid" in k and ("ítem" in k or "item" in k):
            liq_sheet = v
            break
    if not liq_sheet:
        liq_sheet = _find_sheet(sheets, ["liquid"])
    if liq_sheet:
        resultado["liquidacion"] = _leer_liquidacion(xl, liq_sheet)

    # Bultos
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
    for _, row in df.iterrows():
        for i in range(len(row) - 1):
            key = str(row.iloc[i]).strip() if pd.notna(row.iloc[i]) else ""
            val = str(row.iloc[i+1]).strip() if pd.notna(row.iloc[i+1]) else ""
            if key and key != "nan":
                data[key] = val
    return data


def _leer_items(xl, sheet_name) -> pd.DataFrame:
    df = xl.parse(sheet_name, dtype=str)
    df.columns = [str(c).strip().upper() for c in df.columns]
    df = df.fillna("")
    return df


def _leer_subitems(xl, sheet_name) -> pd.DataFrame:
    df = xl.parse(sheet_name, dtype=str)
    df.columns = [str(c).strip().upper() for c in df.columns]
    df = df.fillna("")
    return df


def _leer_liquidacion(xl, sheet_name) -> pd.DataFrame:
    df = xl.parse(sheet_name, dtype=str)
    df.columns = [str(c).strip().upper() for c in df.columns]
    df = df.fillna("")
    return df


def _leer_bultos(xl, sheet_name) -> pd.DataFrame:
    df = xl.parse(sheet_name, dtype=str)
    df.columns = [str(c).strip().upper() for c in df.columns]
    df = df.fillna("")
    return df


def normalizar_codigo(codigo: str) -> str:
    """Normaliza código de parte: quita guiones, espacios y sufijos de letra al final."""
    codigo = re.sub(r'[-\s]', '', codigo.strip().upper())
    codigo = re.sub(r'[A-Z]$', '', codigo)
    return codigo


def safe_float(val) -> float:
    try:
        return float(str(val).replace(",", ".").strip())
    except:
        return 0.0
