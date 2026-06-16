import pandas as pd
from config.defaults import (
    PAISES_PROHIBIDOS, CONCEPTOS_CON_CM, CONCEPTO_SIN_CM_PROHIBIDO,
    CONCEPTO_USADO, KEYWORDS_DUMPING, TOLERANCIA_FOB,
    BANCO_ARGENTINA, IMPOGIRO
)
from utils.parser_di import safe_float

CAMPOS_DUMPING_DJ = ["I:DUMPR60DECJUR", "I:DUMPR60PAISMAYOR", "I:DUMPADVALPAISTXT"]

def alerta(item, campo, mensaje, nivel="ALERTA"):
    return {"item": item, "campo": campo, "mensaje": mensaje, "nivel": nivel}

def ok(item, campo, mensaje):
    return {"item": item, "campo": campo, "mensaje": mensaje, "nivel": "OK"}

def _pais_prohibido(pais: str) -> bool:
    pais_upper = pais.upper()
    for p in PAISES_PROHIBIDOS:
        if p.upper() in pais_upper:
            return True
    return False

def _ref(modelo: str = "", factura: str = "", cm: str = "") -> str:
    """Genera sufijo de referencia para mensajes."""
    partes = []
    if modelo:
        partes.append(f"Código: {modelo}")
    if factura:
        partes.append(f"Factura: {factura}")
    if cm:
        partes.append(f"CM: {cm}")
    return (" | " + " | ".join(partes)) if partes else ""

def _build_ref_map(df_items: pd.DataFrame, df_subitems: pd.DataFrame) -> dict:
    """
    Construye dict {item_zfill4: {modelo, factura, cm}} 
    combinando info de Item y Subitem.
    """
    ref = {}
    if df_items is not None:
        for _, row in df_items.iterrows():
            item = str(row.get("ITEM", "")).strip().zfill(4)
            ref[item] = {
                "factura": str(row.get("D:NRO-FACTURA", row.get("D:FACTURA", ""))).strip(),
                "cm":      str(row.get("D:CERTSM", "")).strip(),
                "modelo":  "",
            }
    if df_subitems is not None:
        for _, row in df_subitems.iterrows():
            item = str(row.get("ITEM", "")).strip().zfill(4)
            modelo = str(row.get("MODELO", "")).strip()
            if item in ref:
                if modelo and not ref[item]["modelo"]:
                    ref[item]["modelo"] = modelo
            else:
                ref[item] = {"factura": "", "cm": "", "modelo": modelo}
    return ref


def validar_items(df_items: pd.DataFrame, df_subitems: pd.DataFrame = None) -> list:
    ref_map = _build_ref_map(df_items, df_subitems)
    resultados = []
    for _, row in df_items.iterrows():
        item = str(row.get("ITEM", "?")).strip().zfill(4)
        r = ref_map.get(item, {})
        suf = _ref(r.get("modelo",""), r.get("factura",""), r.get("cm",""))
        tiene_cm = row.get("D:CERTSM", "").strip() != ""

        estado = row.get("ESTADO", "").strip()
        if "NUEVO SIN USO IMPORTADO" not in estado.upper():
            resultados.append(alerta(item, "ESTADO", f"Estado '{estado}' — verificar si es correcto{suf}"))

        origen = row.get("ORIGEN", "").strip()
        if _pais_prohibido(origen):
            resultados.append(alerta(item, "ORIGEN", f"País de origen PROHIBIDO: {origen}{suf}", "ERROR"))

        procedencia = row.get("PROCEDENCIA", "").strip()
        if _pais_prohibido(procedencia):
            resultados.append(alerta(item, "PROCEDENCIA", f"País de procedencia PROHIBIDO: {procedencia}{suf}", "ERROR"))

        origen_cod = origen.split("-")[0].strip()
        proced_cod = procedencia.split("-")[0].strip()
        tiene_campo_dumping = any(row.get(c, "").strip() for c in CAMPOS_DUMPING_DJ)
        if origen_cod and proced_cod and origen_cod == proced_cod and tiene_campo_dumping:
            dj = row.get("D:DJ-ORIG-NOPREFER", "").strip()
            if not dj:
                resultados.append(alerta(item, "D:DJ-ORIG-NOPREFER",
                    f"Campos dumping declarados y origen=procedencia ({origen_cod}) — falta número IF en DJ{suf}", "ERROR"))

        if tiene_cm:
            autoliq = row.get("V:AUTOLIQCONTRIMP", "").strip().upper()
            if autoliq != "SI":
                resultados.append(alerta(item, "V:AUTOLIQCONTRIMP", f"Con CM debe ser SI, tiene: '{autoliq}'{suf}", "ERROR"))
            liqman = row.get("I:LIQMANIMPCONT", "").strip().upper()
            if liqman != "LMC-11":
                resultados.append(alerta(item, "I:LIQMANIMPCONT", f"Con CM debe ser LMC-11, tiene: '{liqman}'{suf}", "ERROR"))
        else:
            autoliq = row.get("V:AUTOLIQCONTRIMP", "").strip().upper()
            if autoliq not in ["", "N"]:
                resultados.append(alerta(item, "V:AUTOLIQCONTRIMP", f"Sin CM debe ser N o vacío, tiene: '{autoliq}'{suf}"))
            liqman = row.get("I:LIQMANIMPCONT", "").strip()
            if liqman:
                resultados.append(alerta(item, "I:LIQMANIMPCONT", f"Sin CM debe estar vacío, tiene: '{liqman}'{suf}"))

        ganancia = row.get("I:GANANCIASOP3", "").strip().upper()
        if ganancia and ganancia != "COMERC":
            resultados.append(alerta(item, "I:GANANCIASOP3", f"Debe ser COMERC, tiene: '{ganancia}'{suf}", "ERROR"))

        dse_marca = row.get("I:DSE.MARCA.FRA1", "").strip().upper()
        if dse_marca and dse_marca != "NO_VALIDA":
            resultados.append(alerta(item, "I:DSE.MARCA.FRA1", f"Debe ser NO_VALIDA, tiene: '{dse_marca}'{suf}", "ERROR"))

        impogiro = row.get("I:IMPOGIRO-DIV-OPC", "").strip().upper()
        if impogiro and impogiro != IMPOGIRO:
            resultados.append(alerta(item, "I:IMPOGIRO-DIV-OPC", f"Debe ser CGDDIF, tiene: '{impogiro}'{suf}", "ERROR"))

        for campo in ["I:DNRT-EXC-OPC", "I:AUTOPARTESEG-OPC", "I:DNRT-OPC"]:
            val = row.get(campo, "").strip()
            if val:
                resultados.append(alerta(item, campo, f"Declarado: '{val}' — informativo, verificar{suf}"))

    return resultados


def validar_subitems(df_subitems: pd.DataFrame, df_items: pd.DataFrame = None) -> list:
    ref_map = _build_ref_map(df_items, df_subitems)
    resultados = []
    for _, row in df_subitems.iterrows():
        item = str(row.get("ITEM", "?")).strip().zfill(4)
        r = ref_map.get(item, {})
        suf = _ref(r.get("modelo",""), r.get("factura",""), r.get("cm",""))
        marca = row.get("MARCA", "").strip().upper()
        if marca and "CATERPILLAR" not in marca:
            resultados.append(alerta(item, "MARCA", f"Marca '{marca}' — verificar (se esperaba CATERPILLAR){suf}"))
    return resultados


def validar_liquidacion(df_liq: pd.DataFrame, df_items: pd.DataFrame, df_subitems: pd.DataFrame) -> list:
    resultados = []
    ref_map = _build_ref_map(df_items, df_subitems)

    cm_por_item = {}
    estado_por_item = {}
    valores_item = {}
    if df_items is not None:
        for _, row in df_items.iterrows():
            item = str(row.get("ITEM", "")).strip().zfill(4)
            cm_por_item[item] = row.get("D:CERTSM", "").strip() != ""
            estado_por_item[item] = row.get("ESTADO", "").strip().upper()
            fob = safe_float(row.get("VALOR FOB", 0))
            flete = safe_float(row.get("FLETE EN DIV", 0))
            seguro = safe_float(row.get("SEGURO EN DIV", 0))
            valores_item[item] = {"fob": fob, "flete": flete, "seguro": seguro}

    liq_por_item = {}
    for _, row in df_liq.iterrows():
        item = str(row.get("ITEM", "")).strip().zfill(4)
        concepto = str(row.get("CONCEPTO", "")).strip()
        porcentaje = safe_float(row.get("PORCENTAJE", 0))
        base = safe_float(row.get("BASE IMPONIBLE", 0))
        importe = safe_float(row.get("IMPORTE", 0))
        if item not in liq_por_item:
            liq_por_item[item] = []
        liq_por_item[item].append({"concepto": concepto, "porcentaje": porcentaje, "base": base, "importe": importe})

    items_unicos = set(list(cm_por_item.keys()) + list(liq_por_item.keys()))
    for item in sorted(items_unicos):
        tiene_cm = cm_por_item.get(item, False)
        estado = estado_por_item.get(item, "")
        conceptos_item = liq_por_item.get(item, [])
        vals = valores_item.get(item, {"fob": 0, "flete": 0, "seguro": 0})
        importe_032 = 0
        r = ref_map.get(item, {})
        suf = _ref(r.get("modelo",""), r.get("factura",""), r.get("cm",""))

        for c in conceptos_item:
            for kw in KEYWORDS_DUMPING:
                if kw in c["concepto"].upper():
                    resultados.append(alerta(item, "LIQUIDACIÓN", f"DUMPING detectado: '{c['concepto']}' — revisión urgente{suf}", "ERROR"))

        if tiene_cm:
            base_032 = vals["fob"] + vals["flete"] + vals["seguro"]

            for cod, info in CONCEPTOS_CON_CM.items():
                nombre = info["nombre"]
                pct_esperado = info["porcentaje"]
                match = next((c for c in conceptos_item if cod in c["concepto"]), None)

                if not match:
                    resultados.append(alerta(item, "LIQUIDACIÓN", f"Falta concepto '{nombre}'{suf}", "ERROR"))
                    continue

                pct_real = match["porcentaje"]
                if cod == "032":
                    if abs(pct_real - pct_esperado) > 0.001:
                        resultados.append(alerta(item, "LIQUIDACIÓN", f"'{nombre}': porcentaje {pct_real}% — se esperaba {pct_esperado}%{suf}", "ERROR"))
                    importe_032 = match["importe"]
                    base_esperada = base_032
                elif cod == "415":
                    if abs(pct_real - 21.0) < 0.001:
                        pass
                    elif abs(pct_real - 10.5) < 0.001:
                        resultados.append(alerta(item, "LIQUIDACIÓN", f"'{nombre}': alícuota reducida 10.5% — verificar que la NCM corresponda{suf}"))
                    else:
                        resultados.append(alerta(item, "LIQUIDACIÓN", f"'{nombre}': porcentaje {pct_real}% — se esperaba 21% o 10.5%{suf}", "ERROR"))
                    base_esperada = base_032 + importe_032
                else:
                    if abs(pct_real - pct_esperado) > 0.001:
                        resultados.append(alerta(item, "LIQUIDACIÓN", f"'{nombre}': porcentaje {pct_real}% — se esperaba {pct_esperado}%{suf}", "ERROR"))
                    base_esperada = base_032 + importe_032

                if abs(match["base"] - base_esperada) > TOLERANCIA_FOB:
                    resultados.append(alerta(item, "LIQUIDACIÓN", f"'{nombre}': base imponible {match['base']:.2f} — esperada {base_esperada:.2f}{suf}"))

            for c in conceptos_item:
                if "010" in c["concepto"] or "011" in c["concepto"]:
                    resultados.append(alerta(item, "LIQUIDACIÓN", f"Ítem CON CM no debería tener '{c['concepto']}'{suf}"))

            if "USADO" in estado:
                if not any(CONCEPTO_USADO in c["concepto"] for c in conceptos_item):
                    resultados.append(alerta(item, "LIQUIDACIÓN", f"Ítem USADO pero falta '056 - D.I. USADOS R.909/94'{suf}", "ERROR"))

            conceptos_esperados_con_cm = ["032", "415", "900", "056", "051", "060"]
            for c in conceptos_item:
                es_esperado = any(cod in c["concepto"] for cod in conceptos_esperados_con_cm)
                es_dumping = any(kw in c["concepto"].upper() for kw in KEYWORDS_DUMPING)
                es_010_011 = "010" in c["concepto"] or "011" in c["concepto"]
                if not es_esperado and not es_dumping and not es_010_011:
                    resultados.append(alerta(item, "LIQUIDACIÓN", f"Concepto no esperado (con CM): '{c['concepto']}' — verificar{suf}"))

        else:
            if any(CONCEPTO_SIN_CM_PROHIBIDO in c["concepto"] for c in conceptos_item):
                resultados.append(alerta(item, "LIQUIDACIÓN", f"Ítem SIN CM tiene concepto '032 - TASA LEY 24196'{suf}", "ERROR"))

            for cod, nombre in [("415", "415 - I.V.A."), ("900", "900 - INGRESOS BRUTOS")]:
                match = next((c for c in conceptos_item if cod in c["concepto"]), None)
                if not match:
                    resultados.append(alerta(item, "LIQUIDACIÓN", f"Ítem SIN CM: falta concepto '{nombre}'{suf}", "ERROR"))
                elif cod == "415":
                    pct_real = match["porcentaje"]
                    if not (abs(pct_real - 21.0) < 0.001 or abs(pct_real - 10.5) < 0.001):
                        resultados.append(alerta(item, "LIQUIDACIÓN", f"'{nombre}': porcentaje {pct_real}% — se esperaba 21% o 10.5%{suf}", "ERROR"))

            conceptos_esperados_sin_cm = ["010", "011", "415", "429", "450", "451", "452", "453",
                                           "454", "455", "456", "457", "458", "459", "460", "900", "056"]
            for c in conceptos_item:
                es_esperado = any(cod in c["concepto"] for cod in conceptos_esperados_sin_cm)
                es_dumping = any(kw in c["concepto"].upper() for kw in KEYWORDS_DUMPING)
                if not es_esperado and not es_dumping:
                    resultados.append(alerta(item, "LIQUIDACIÓN", f"Concepto no esperado (sin CM): '{c['concepto']}' — verificar{suf}"))

    return resultados


def validar_prorrateo(df_items: pd.DataFrame, fob_total: float, flete_total: float, seguro_total: float, df_subitems: pd.DataFrame = None) -> list:
    ref_map = _build_ref_map(df_items, df_subitems)
    resultados = []
    if not fob_total:
        return resultados
    for _, row in df_items.iterrows():
        item = str(row.get("ITEM", "?")).strip().zfill(4)
        r = ref_map.get(item, {})
        suf = _ref(r.get("modelo",""), r.get("factura",""), r.get("cm",""))
        fob_item = safe_float(row.get("VALOR FOB", 0))
        flete_item = safe_float(row.get("FLETE EN DIV", 0))
        seguro_item = safe_float(row.get("SEGURO EN DIV", 0))
        proporcion = fob_item / fob_total if fob_total else 0
        flete_esperado = round(flete_total * proporcion, 5)
        seguro_esperado = round(seguro_total * proporcion, 5)
        if abs(flete_item - flete_esperado) > TOLERANCIA_FOB:
            resultados.append(alerta(item, "FLETE EN DIV", f"Declarado {flete_item:.5f} — esperado {flete_esperado:.5f}{suf}"))
        if abs(seguro_item - seguro_esperado) > TOLERANCIA_FOB:
            resultados.append(alerta(item, "SEGURO EN DIV", f"Declarado {seguro_item:.5f} — esperado {seguro_esperado:.5f}{suf}"))
    return resultados


def validar_ncm_excel(df_subitems: pd.DataFrame, df_ncm: pd.DataFrame, df_items: pd.DataFrame = None) -> list:
    ref_map = _build_ref_map(df_items, df_subitems)
    resultados = []
    if df_ncm is None or df_ncm.empty:
        return resultados

    col_parte = None
    col_ncm = None
    for col in df_ncm.columns:
        if "PART_NUMBER" in col.upper() or "PARTE" in col.upper():
            col_parte = col
        if col.upper() in ["NCM", "POSICION", "ARANCEL"]:
            col_ncm = col

    if not col_ncm and col_parte:
        cols = list(df_ncm.columns)
        idx = cols.index(col_parte)
        if idx + 1 < len(cols):
            col_ncm = cols[idx + 1]

    if not col_parte or not col_ncm:
        return [alerta("GENERAL", "NCM EXCEL", "No se pudo identificar columnas en el Excel de clasificación")]

    from utils.parser_di import normalizar_codigo
    mapa_ncm = {}
    for _, row in df_ncm.iterrows():
        parte = normalizar_codigo(str(row.get(col_parte, "")))
        ncm = str(row.get(col_ncm, "")).strip()
        if parte and ncm and ncm != "nan":
            mapa_ncm[parte] = ncm.replace(".", "")[:8]

    for _, row in df_subitems.iterrows():
        item = str(row.get("ITEM", "?")).strip().zfill(4)
        r = ref_map.get(item, {})
        suf = _ref(r.get("modelo",""), r.get("factura",""), r.get("cm",""))
        modelo = normalizar_codigo(str(row.get("MODELO", "")))
        ncm_di_raw = str(row.get("NCM", "")).replace(".", "").strip()
        ncm_di_8 = ncm_di_raw[:8]
        if not modelo or modelo == "NAN":
            continue
        if modelo in mapa_ncm:
            ncm_excel_8 = mapa_ncm[modelo]
            if ncm_di_8 != ncm_excel_8:
                resultados.append(alerta(item, "NCM vs EXCEL",
                    f"NCM DI {ncm_di_8} — NCM Excel {ncm_excel_8}{suf}", "ERROR"))

    return resultados
