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

    IMPORTANTE: no se sacan ceros iniciales. Un cero al principio puede
    ser parte real del código de catálogo (ej. "0S-0509L" -> "0S0509",
    donde el 0 es el primer carácter del código, no relleno). Si el DI
    declara un código que el cliente sabe que tiene ceros de relleno
    distintos a los de la factura, ese es un caso de declaración
    incorrecta a resolver manualmente, no algo que la normalización deba
    adivinar.
    """
    s = re.sub(r'[-\s]', '', codigo.strip().upper())
    if not s:
        return s
    if re.match(r'^\d{3}', s):
        return s[:7]
    else:
        return s[:6]
def safe_float(val) -> float:
    """
    Convierte un valor numérico a float, sea cual sea el formato de
    separador decimal usado (DI argentino con coma decimal, BL/facturas
    en inglés con punto decimal).

    Regla para desambiguar:
      - Si el string tiene tanto "," como ".": el símbolo que aparece
        MÁS A LA DERECHA es el separador decimal; el otro es de miles
        y se descarta. Ej: "9,148.50" -> 9148.50 | "9.148,50" -> 9148.50
      - Si tiene un solo tipo de símbolo, y aparece más de una vez, son
        separadores de miles repetidos (nunca decimal).
        Ej: "1.234.567" -> 1234567.0
      - Si tiene un solo tipo de símbolo y aparece una sola vez, SIEMPRE
        se interpreta como separador DECIMAL, sin importar cuántos
        dígitos tenga después — no se asume separador de miles a partir
        de la cantidad de dígitos, porque varios campos de este proyecto
        usan más de 2 decimales reales (ej. flete/seguro prorrateado con
        hasta 5 decimales: "1299.575", "9128,1") y esa heurística los
        rompía interpretándolos como miles.
        Ej: "220.59" -> 220.59 | "9.148" -> 9.148 | "1299.575" -> 1299.575
      - Si no tiene ningún símbolo: se interpreta directo como número.
    """
    if val is None:
        return 0.0
    s = str(val).strip()
    if not s or s.lower() == "nan":
        return 0.0

    # Negativos: conservar el signo, trabajar sobre el resto
    negativo = s.startswith("-")
    if negativo or s.startswith("+"):
        s = s[1:]

    tiene_coma = "," in s
    tiene_punto = "." in s

    try:
        if tiene_coma and tiene_punto:
            # El símbolo que aparece más a la derecha es el decimal
            pos_coma = s.rfind(",")
            pos_punto = s.rfind(".")
            if pos_coma > pos_punto:
                # Coma decimal, punto(s) = miles
                s_norm = s.replace(".", "").replace(",", ".")
            else:
                # Punto decimal, coma(s) = miles
                s_norm = s.replace(",", "")
            resultado = float(s_norm)
        elif tiene_coma or tiene_punto:
            simbolo = "," if tiene_coma else "."
            partes = s.split(simbolo)
            # Más de una ocurrencia del símbolo => son separadores de miles
            # repetidos (ej. "1.234.567"), nunca decimal.
            if len(partes) > 2:
                s_norm = s.replace(simbolo, "")
            else:
                # Una sola ocurrencia: siempre decimal, sin importar la
                # cantidad de dígitos después (ver docstring).
                s_norm = s.replace(simbolo, ".")
            resultado = float(s_norm)
        else:
            resultado = float(s)
    except Exception:
        return 0.0

    return -resultado if negativo else resultado
