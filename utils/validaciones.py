import re
import pandas as pd
from config.defaults import (
    PAISES_PROHIBIDOS, CONCEPTOS_CON_CM, CONCEPTO_SIN_CM_PROHIBIDO,
    CONCEPTO_USADO, KEYWORDS_DUMPING, TOLERANCIA_FOB,
    BANCO_ARGENTINA, IMPOGIRO
)
from utils.parser_di import safe_float, normalizar_codigo

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


def _facturas_que_contienen_codigo(modelo: str, datos_facturas: dict) -> str | None:
    """
    Busca el código de parte (normalizado) en cada factura ya parseada y
    retorna los nombres de las facturas que efectivamente lo contienen.
    Evita listar todas las facturas del despacho cuando no hay CM que
    indique la factura exacta.

    Retorna:
      - "" (string vacío) si no se pudo intentar la búsqueda (sin modelo
        o sin datos_facturas disponibles) — el caller puede caer a otro
        fallback en este caso.
      - None si SÍ se buscó activamente pero el código no se encontró en
        ninguna factura — señal explícita de que no hay que caer al
        fallback de listar todas las facturas, porque ya se confirmó que
        ninguna lo tiene (sería información falsa).
      - El nombre de la(s) factura(s) encontrada(s), si hubo match.
    """
    if not modelo or not datos_facturas:
        return ""
    modelo_norm = normalizar_codigo(modelo)
    encontradas = []
    for nro_factura, fac_data in datos_facturas.items():
        if "error" in fac_data:
            continue
        items_factura = fac_data.get("items", [])
        if any(normalizar_codigo(i.get("codigo_parte", "")) == modelo_norm for i in items_factura):
            nombre_limpio = re.sub(r"\.pdf$", "", nro_factura, flags=re.IGNORECASE)
            encontradas.append(nombre_limpio)
    if not encontradas:
        return None
    return ", ".join(encontradas)


def _build_ref_map(df_items: pd.DataFrame, df_subitems: pd.DataFrame, df_caratula: pd.DataFrame = None,
                    datos_cm: dict = None, datos_facturas: dict = None) -> dict:
    """
    Construye dict {item_zfill4: {modelo, factura, cm}}
    combinando info de Item, Subitem y Carátula.

    La factura se determina con esta prioridad:
      1. Si el ítem tiene CM y ese CM fue procesado (datos_cm), se usa la
         factura declarada en el propio CM (numero_factura) — es la fuente
         más confiable, sin ambigüedad.
      2. Si no hay CM (o no se pudo determinar), se busca el código de
         parte del ítem en cada factura ya parseada (datos_facturas) y se
         listan solo las que realmente lo contienen.
      3. Si no hay datos_facturas disponibles, se cae al campo directo del
         DI (D:NRO-FACTURA / D:FACTURA) o, en última instancia, a la lista
         completa de facturas mencionadas en la Carátula (comportamiento
         legado, menos preciso).
    """
    datos_cm = datos_cm or {}
    datos_facturas = datos_facturas or {}

    # Leer facturas de la Carátula como último fallback
    facturas_caratula = []
    if df_caratula is not None:
        try:
            for _, row in df_caratula.iterrows():
                for val in row.values:
                    s = str(val).strip()
                    if s.startswith("Z9") and len(s) >= 8:
                        facturas_caratula.append(s)
        except Exception:
            pass

    ref = {}
    if df_items is not None:
        for _, row in df_items.iterrows():
            item = str(row.get("ITEM", "")).strip().zfill(4)
            cm = str(row.get("D:CERTSM", "")).strip()
            ref[item] = {"factura": "", "cm": cm, "modelo": ""}

    if df_subitems is not None:
        for _, row in df_subitems.iterrows():
            item = str(row.get("ITEM", "")).strip().zfill(4)
            modelo = str(row.get("MODELO", "")).strip()
            if item in ref:
                if modelo and not ref[item]["modelo"]:
                    ref[item]["modelo"] = modelo
            else:
                ref[item] = {"factura": "", "cm": "", "modelo": modelo}

    # Resolver factura para cada ítem según la prioridad descripta arriba
    for item, info in ref.items():
        cm = info["cm"]
        modelo = info["modelo"]
        factura = ""
        busqueda_sin_resultado = False  # True si se buscó activamente y no se encontró nada

        # 1. Vía CM: usar la factura declarada en el propio CM
        if cm and cm in datos_cm and "error" not in datos_cm[cm]:
            factura = datos_cm[cm].get("numero_factura", "").strip()

        # 2. Vía búsqueda real en facturas parseadas
        if not factura and modelo and datos_facturas:
            resultado_busqueda = _facturas_que_contienen_codigo(modelo, datos_facturas)
            if resultado_busqueda is None:
                # Se buscó el código en todas las facturas y no apareció
                # en ninguna — no caer al fallback de listar todas, sería
                # información falsa (ya se confirmó que ninguna lo tiene).
                busqueda_sin_resultado = True
            else:
                factura = resultado_busqueda

        # 3. Fallback legado: campo directo del DI o lista completa de
        #    carátula. No se aplica si ya se confirmó la ausencia del
        #    código en las facturas (busqueda_sin_resultado).
        if not factura and not busqueda_sin_resultado:
            factura = facturas_caratula and ", ".join(facturas_caratula) or ""

        if not factura and busqueda_sin_resultado:
            factura = "no determinada"

        info["factura"] = factura

    return ref


def validar_items(df_items: pd.DataFrame, df_subitems: pd.DataFrame = None, df_caratula: pd.DataFrame = None,
                   datos_cm: dict = None, datos_facturas: dict = None) -> list:
    ref_map = _build_ref_map(df_items, df_subitems, df_caratula, datos_cm, datos_facturas)
    resultados = []
    hubo_origen_prohibido = False
    items_con_ajuste = 0
    total_items = 0
    items_con_ajuste_lista = []
    # Acumulador para el resumen agrupado por valor de los 3 campos
    # informativos: {campo: {valor: [items]}}
    valores_informativos = {"I:DNRT-EXC-OPC": {}, "I:AUTOPARTESEG-OPC": {}, "I:DNRT-OPC": {}}

    for _, row in df_items.iterrows():
        item = str(row.get("ITEM", "?")).strip().zfill(4)
        r = ref_map.get(item, {})
        suf = _ref(r.get("modelo",""), r.get("factura",""), r.get("cm",""))
        tiene_cm = row.get("D:CERTSM", "").strip() != ""
        total_items += 1

        estado = row.get("ESTADO", "").strip()
        if "NUEVO SIN USO IMPORTADO" not in estado.upper():
            resultados.append(alerta(item, "ESTADO", f"Estado '{estado}' — verificar si es correcto{suf}"))

        origen = row.get("ORIGEN", "").strip()
        if _pais_prohibido(origen):
            hubo_origen_prohibido = True
            resultados.append(alerta(item, "ORIGEN", f"País de origen PROHIBIDO: {origen}{suf}", "ERROR"))

        procedencia = row.get("PROCEDENCIA", "").strip()
        if _pais_prohibido(procedencia):
            resultados.append(alerta(item, "PROCEDENCIA", f"País de procedencia PROHIBIDO: {procedencia}{suf}", "ERROR"))

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

        # (GANANCIASOP3, DSE.MARCA.FRA1, IMPOGIRO-DIV-OPC: ver validar_campos_control)

        for campo in ["I:DNRT-EXC-OPC", "I:AUTOPARTESEG-OPC", "I:DNRT-OPC"]:
            val = row.get(campo, "").strip()
            if val:
                resultados.append(alerta(item, campo, f"Declarado: '{val}' — informativo, verificar{suf}"))
                valores_informativos[campo].setdefault(val, []).append(item)

        # ── Ajuste a Incluir / Ajuste a Deducir ──
        # No se compara contra otro documento; solo se informa si alguno
        # de los dos campos trae un valor distinto de cero, ya que ambos
        # requieren revisión manual del despachante.
        ajuste_incluir = safe_float(row.get("AJUSTE A INCLUIR", 0))
        ajuste_deducir = safe_float(row.get("AJUSTE A DEDUCIR", 0))
        if ajuste_incluir or ajuste_deducir:
            items_con_ajuste += 1
            items_con_ajuste_lista.append(item)
            partes_ajuste = []
            if ajuste_incluir:
                partes_ajuste.append(f"Ajuste a Incluir: {ajuste_incluir:.2f}")
            if ajuste_deducir:
                partes_ajuste.append(f"Ajuste a Deducir: {ajuste_deducir:.2f}")
            resultados.append(alerta(item, "AJUSTES", f"{' | '.join(partes_ajuste)}{suf}"))

    # ── Resumen general de países prohibidos (país de ORIGEN) ──────────────
    # Un solo mensaje a nivel despacho en vez de repetir por ítem: si ningún
    # ítem disparó ERROR de origen prohibido, se informa que se revisó y no
    # se encontró ninguno. Si hubo algún caso, ya quedó informado arriba
    # como ERROR por ítem — no se duplica el resumen en ese caso.
    if not hubo_origen_prohibido:
        resultados.append(ok("GENERAL", "PAÍSES PROHIBIDOS", "Países prohibidos no encontrados"))

    # ── Resumen general de Ajustes a Incluir/Deducir ───────────────────────
    # Estos campos no se comparan contra otro documento, solo se informa si
    # alguno tiene valor (requiere revisión manual). El resumen agrupado va
    # exclusivamente en Revisión General; el detalle real (por ítem) ya
    # quedó arriba como ALERTA, así que esta fila no se duplica ahí.
    if items_con_ajuste:
        items_str = ", ".join(items_con_ajuste_lista)
        resultados.append({
            "item": "GENERAL", "campo": "AJUSTES",
            "mensaje": f"De {total_items} ítems, {items_con_ajuste} con valor en Ajuste a Incluir/Deducir: {items_str} — ver pestaña Alertas",
            "nivel": "ALERTA", "es_resumen": True,
        })
    else:
        resultados.append({
            "item": "GENERAL", "campo": "AJUSTES",
            "mensaje": f"Ningún ítem ({total_items}) con valor en Ajuste a Incluir/Deducir",
            "nivel": "OK", "es_resumen": True,
        })

    # ── Resumen general de campos informativos (DNRT-EXC, AUTOPARTESEG, DNRT) ──
    # Estos 3 campos son declarativos/informativos y suelen repetirse con el
    # mismo valor en muchos ítems — en vez de obligar a leer una fila por
    # ítem, se agrupa por valor único declarado. El detalle real (por
    # ítem) sigue arriba como ALERTA; este resumen es exclusivo de
    # Revisión General.
    for campo, valores in valores_informativos.items():
        if not valores:
            resultados.append({
                "item": "GENERAL", "campo": campo,
                "mensaje": f"Ningún ítem con valor declarado en {campo}",
                "nivel": "OK", "es_resumen": True,
            })
            continue
        for valor, items_lista in valores.items():
            cant = len(items_lista)
            etiqueta_items = "ítem" if cant == 1 else "ítems"
            items_str = ", ".join(items_lista)
            resultados.append({
                "item": "GENERAL", "campo": campo,
                "mensaje": f"Valor '{valor}' declarado en {cant} {etiqueta_items}: {items_str} — ver pestaña Alertas",
                "nivel": "ALERTA", "es_resumen": True,
            })

    return resultados


def validar_subitems(df_subitems: pd.DataFrame, df_items: pd.DataFrame = None, df_caratula: pd.DataFrame = None,
                      datos_cm: dict = None, datos_facturas: dict = None) -> list:
    ref_map = _build_ref_map(df_items, df_subitems, df_caratula, datos_cm, datos_facturas)
    resultados = []
    for _, row in df_subitems.iterrows():
        item = str(row.get("ITEM", "?")).strip().zfill(4)
        r = ref_map.get(item, {})
        suf = _ref(r.get("modelo",""), r.get("factura",""), r.get("cm",""))
        marca = row.get("MARCA", "").strip().upper()
        if marca and "CATERPILLAR" not in marca:
            resultados.append(alerta(item, "MARCA", f"Marca '{marca}' — verificar (se esperaba CATERPILLAR){suf}"))
    return resultados


def validar_liquidacion(df_liq: pd.DataFrame, df_items: pd.DataFrame, df_subitems: pd.DataFrame, df_caratula: pd.DataFrame = None,
                         datos_cm: dict = None, datos_facturas: dict = None) -> list:
    resultados = []
    ref_map = _build_ref_map(df_items, df_subitems, df_caratula, datos_cm, datos_facturas)

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
                    resultados.append(alerta(item, "LIQUIDACIÓN", f"DUMPING detectado: '{c['concepto']}' — revisión urgente{suf}", "ALERTA"))

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
                # Ítem usado CON CM: paga vía Ley Minera (concepto 032,
                # ya exigido arriba para todo ítem con CM), no corresponde
                # el 056 de usados — eso es exclusivo de ítems sin CM.
                if any(CONCEPTO_USADO in c["concepto"] for c in conceptos_item):
                    resultados.append(alerta(item, "LIQUIDACIÓN",
                        f"Ítem USADO con CM no debería tener '056 - D.I. USADOS R.909/94' (va por Ley Minera, concepto 032){suf}",
                        "ERROR"))

            conceptos_esperados_con_cm = ["032", "415", "900", "056", "051", "060"]  # 900 reconocido pero NO validado
            for c in conceptos_item:
                es_esperado = any(cod in c["concepto"] for cod in conceptos_esperados_con_cm)
                es_dumping = any(kw in c["concepto"].upper() for kw in KEYWORDS_DUMPING)
                es_010_011 = "010" in c["concepto"] or "011" in c["concepto"]
                if not es_esperado and not es_dumping and not es_010_011:
                    resultados.append(alerta(item, "LIQUIDACIÓN", f"Concepto no esperado (con CM): '{c['concepto']}' — verificar{suf}"))

        else:
            if any(CONCEPTO_SIN_CM_PROHIBIDO in c["concepto"] for c in conceptos_item):
                resultados.append(alerta(item, "LIQUIDACIÓN", f"Ítem SIN CM tiene concepto '032 - TASA LEY 24196'{suf}", "ERROR"))

            if "USADO" in estado:
                # Ítem usado SIN CM: acá sí corresponde el 056, ya que no
                # hay Ley Minera (concepto 032) que cubra la situación.
                if not any(CONCEPTO_USADO in c["concepto"] for c in conceptos_item):
                    resultados.append(alerta(item, "LIQUIDACIÓN",
                        f"Ítem USADO sin CM pero falta '056 - D.I. USADOS R.909/94'{suf}", "ERROR"))

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


def validar_prorrateo(df_items: pd.DataFrame, fob_total: float, flete_total: float, seguro_total: float, df_subitems: pd.DataFrame = None, df_caratula: pd.DataFrame = None,
                       datos_cm: dict = None, datos_facturas: dict = None) -> list:
    ref_map = _build_ref_map(df_items, df_subitems, df_caratula, datos_cm, datos_facturas)
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
        if round(flete_item, 5) != flete_esperado:
            resultados.append(alerta(item, "FLETE EN DIV", f"Declarado {flete_item:.5f} — esperado {flete_esperado:.5f}{suf}"))
        if round(seguro_item, 5) != seguro_esperado:
            resultados.append(alerta(item, "SEGURO EN DIV", f"Declarado {seguro_item:.5f} — esperado {seguro_esperado:.5f}{suf}"))
    return resultados


def validar_ncm_excel(df_subitems: pd.DataFrame, df_ncm: pd.DataFrame, df_items: pd.DataFrame = None, df_caratula: pd.DataFrame = None,
                       datos_cm: dict = None, datos_facturas: dict = None) -> list:
    ref_map = _build_ref_map(df_items, df_subitems, df_caratula, datos_cm, datos_facturas)
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


# ── Resumen General: Liquidación ──────────────────────────────────────────────

def validar_resumen_liquidacion(resultados_liquidacion: list, total_items: int) -> list:
    """
    Resumen a nivel despacho del detalle de Liquidación: cuántos ítems
    tuvieron alguna observación (ERROR o ALERTA) en su liquidación, y de
    qué tipo (base imponible, alícuota IVA, ingresos brutos, concepto
    faltante, etc. — clasificado por palabras clave dentro del mensaje,
    ya que todos los resultados de validar_liquidacion() comparten el
    mismo campo "LIQUIDACIÓN" sin subcampos).

    `resultados_liquidacion` es la lista ya generada por
    validar_liquidacion() — se reutiliza para no recalcular el cruce.

    La fila que devuelve lleva "es_resumen": True y es exclusiva de la
    pestaña Revisión General — el detalle real por ítem sigue viviendo
    únicamente en Errores/Alertas.
    """
    CAMPO = "LIQUIDACIÓN"

    if not resultados_liquidacion or not total_items:
        return []

    CLASIFICACION = [
        ("base imponible", "base imponible"),
        ("I.V.A.", "alícuota IVA"),
        ("INGRESOS BRUTOS", "ingresos brutos"),
        ("TASA LEY 24196", "tasa Ley 24196"),
        ("DUMPING", "dumping"),
        ("Falta concepto", "concepto faltante"),
        ("no esperado", "concepto no esperado"),
    ]

    def _clasificar(mensaje: str) -> str:
        msg_upper = mensaje.upper()
        for clave, etiqueta in CLASIFICACION:
            if clave.upper() in msg_upper:
                return etiqueta
        return "otro"

    items_con_problema = {}  # item -> {"nivel": "ERROR"|"ALERTA", "motivos": set()}
    for r in resultados_liquidacion:
        if r.get("campo") != CAMPO or r.get("nivel") == "OK":
            continue
        item = str(r.get("item", ""))
        nivel = r.get("nivel")
        motivo = _clasificar(str(r.get("mensaje", "")))
        info = items_con_problema.setdefault(item, {"nivel": "ALERTA", "motivos": set()})
        info["motivos"].add(motivo)
        if nivel == "ERROR":
            info["nivel"] = "ERROR"

    if not items_con_problema:
        return [{
            "item": "GENERAL", "campo": CAMPO,
            "mensaje": f"De {total_items} ítems, todos sin observaciones en liquidación",
            "nivel": "OK", "es_resumen": True,
        }]

    cant_con_problema = len(items_con_problema)
    cant_ok = total_items - cant_con_problema

    motivos_totales = set()
    for info in items_con_problema.values():
        motivos_totales.update(info["motivos"])
    motivos_str = ", ".join(sorted(motivos_totales))

    items_str = ", ".join(sorted(items_con_problema.keys()))

    nivel_general = "ERROR" if any(i["nivel"] == "ERROR" for i in items_con_problema.values()) else "ALERTA"
    pestana = "Errores" if nivel_general == "ERROR" else "Alertas"

    mensaje = (
        f"De {total_items} ítems, {cant_ok} sin observaciones en liquidación — "
        f"{cant_con_problema} ítem(s) con diferencia en {motivos_str}: {items_str} — ver pestaña {pestana}"
    )

    return [{
        "item": "GENERAL", "campo": CAMPO,
        "mensaje": mensaje, "nivel": nivel_general, "es_resumen": True,
    }]


# ── Validación Dumping: 5 escenarios (NCM/marca, DJ, origen/procedencia) ──────

MARCAS_EXCEPTUADAS_DUMPING = ["CATERPILLAR", "CUMMINS", "DEUTZ"]
NCM_EXCEPTUADA_DUMPING = "84133090100H"  # 8413.30.90.100H sin puntos


def validar_dumping_marca_dj(df_items: pd.DataFrame, df_subitems: pd.DataFrame, df_liq: pd.DataFrame,
                              df_caratula: pd.DataFrame = None, datos_cm: dict = None,
                              datos_facturas: dict = None) -> list:
    """
    Verifica, para cada ítem que lleva dumping (algún campo de
    I:DUMPR60DECJUR / I:DUMPR60PAISMAYOR / I:DUMPADVALPAISTXT con
    información), si corresponde o no pagarlo, y si eso es consistente
    con lo efectivamente liquidado. 5 escenarios:

      1. NCM 8413.30.90.100H + marca CATERPILLAR/CUMMINS/DEUTZ
         -> NO paga (firme). Si el concepto de dumping SÍ está en la
            liquidación -> ERROR (se liquidó algo que no correspondía).
      2. Origen = Procedencia + DJ de Origen No Preferencial declarada
         -> NO paga (firme). Si SÍ está liquidado -> ERROR.
      3. Origen = Procedencia + SIN DJ
         -> no se puede determinar con certeza. Si NO está liquidado
            -> ALERTA (verificar si corresponde el pago).
      4. Origen ≠ Procedencia, sin la excepción de marca/NCM del caso 1
         -> paga (firme). Si NO está liquidado -> ERROR (falta liquidar).
      5. DJ declarada pero Origen ≠ Procedencia
         -> ERROR por sí mismo (la DJ está mal aplicada); no se evalúa
            si corresponde o no el pago en este caso.

    El ítem solo entra a este análisis si lleva dumping (paso 1). Si no
    lleva dumping, no genera ninguna fila.

    Genera detalle por ítem (con código/factura/CM vía el mismo sufijo
    de referencia que el resto de las validaciones) y un resumen
    exclusivo de Revisión General con la cantidad de ítems con dumping
    y cuántos tienen inconsistencia.
    """
    resultados = []
    CAMPO = "DUMPING"

    if df_items is None or df_items.empty:
        return resultados

    ref_map = _build_ref_map(df_items, df_subitems, df_caratula, datos_cm, datos_facturas)

    # Marca y NCM por ítem (de la solapa Subitems)
    marca_por_item = {}
    ncm_por_item = {}
    if df_subitems is not None:
        for _, row in df_subitems.iterrows():
            item = str(row.get("ITEM", "")).strip().zfill(4)
            marca = row.get("MARCA", "").strip().upper()
            if marca and item not in marca_por_item:
                marca_por_item[item] = marca
            ncm_raw = str(row.get("NCM", "")).replace(".", "").strip().upper()
            if ncm_raw and item not in ncm_por_item:
                ncm_por_item[item] = ncm_raw

    # Conceptos de dumping presentes en la liquidación, por ítem
    items_con_dumping_en_liq = set()
    if df_liq is not None and not df_liq.empty:
        for _, row in df_liq.iterrows():
            item = str(row.get("ITEM", "")).strip().zfill(4)
            concepto = str(row.get("CONCEPTO", "")).strip().upper()
            if any(kw in concepto for kw in KEYWORDS_DUMPING):
                items_con_dumping_en_liq.add(item)

    items_con_dumping = []  # todos los ítems que llevan dumping (paso 1)
    items_con_problema = []

    for _, row in df_items.iterrows():
        item = str(row.get("ITEM", "")).strip().zfill(4)

        # ── Paso 1: ¿lleva dumping? ──
        lleva_dumping = any(row.get(c, "").strip() for c in CAMPOS_DUMPING_DJ)
        if not lleva_dumping:
            continue
        items_con_dumping.append(item)

        origen = row.get("ORIGEN", "").strip().upper()
        procedencia = row.get("PROCEDENCIA", "").strip().upper()
        origen_cod = origen.split("-")[0].strip()
        proced_cod = procedencia.split("-")[0].strip()
        origen_igual_procedencia = bool(origen_cod) and origen_cod == proced_cod

        tiene_dj = bool(row.get("D:DJ-ORIG-NOPREFER", "").strip())
        tiene_dumping_liq = item in items_con_dumping_en_liq

        marca = marca_por_item.get(item, "")
        ncm = ncm_por_item.get(item, "")
        marca_exceptuada = any(m in marca for m in MARCAS_EXCEPTUADAS_DUMPING)
        ncm_exceptuada = ncm[:len(NCM_EXCEPTUADA_DUMPING)] == NCM_EXCEPTUADA_DUMPING

        r = ref_map.get(item, {})
        suf = _ref(r.get("modelo", ""), r.get("factura", ""), r.get("cm", ""))

        # ── Paso 2: clasificación de los 5 casos ──
        if ncm_exceptuada and marca_exceptuada:
            # Caso 1: NCM + marca exceptuada -> no paga (firme)
            if tiene_dumping_liq:
                items_con_problema.append(item)
                resultados.append(alerta(item, CAMPO,
                    f"NCM {NCM_EXCEPTUADA_DUMPING} con marca exceptuada ({marca}) — no correspondía dumping pero está liquidado{suf}",
                    "ERROR"))

        elif tiene_dj and origen_igual_procedencia:
            # Caso 2: DJ + origen=procedencia -> no paga (firme)
            if tiene_dumping_liq:
                items_con_problema.append(item)
                resultados.append(alerta(item, CAMPO,
                    f"DJ de Origen declarada y origen=procedencia ({origen_cod}) — no correspondía dumping pero está liquidado{suf}",
                    "ERROR"))

        elif tiene_dj and not origen_igual_procedencia:
            # Caso 5: DJ declarada pero origen≠procedencia -> error en sí mismo
            items_con_problema.append(item)
            resultados.append(alerta(item, CAMPO,
                f"DJ de Origen declarada pero origen ('{origen}') y procedencia ('{procedencia}') no coinciden{suf}",
                "ERROR"))

        elif not tiene_dj and origen_igual_procedencia:
            # Caso 3: origen=procedencia sin DJ -> no determinado, solo
            # alertar si NO está liquidado (si paga, no hay nada que avisar)
            if not tiene_dumping_liq:
                items_con_problema.append(item)
                resultados.append(alerta(item, CAMPO,
                    f"Origen=Procedencia ({origen_cod}) sin DJ de Origen declarada y sin concepto de dumping liquidado — verificar si corresponde el pago{suf}",
                    "ALERTA"))

        else:
            # Caso 4: origen≠procedencia, sin excepción de marca/NCM -> paga (firme)
            if not tiene_dumping_liq:
                items_con_problema.append(item)
                resultados.append(alerta(item, CAMPO,
                    f"Origen ('{origen}') ≠ Procedencia ('{procedencia}'), sin excepción de marca/NCM — correspondía dumping y no está liquidado{suf}",
                    "ERROR"))

    # ── Resumen general ──
    total = len(items_con_dumping)
    if total:
        if items_con_problema:
            cant = len(items_con_problema)
            hay_error = any(r["nivel"] == "ERROR" for r in resultados if r["item"] != "GENERAL")
            pestana = "Errores" if hay_error else "Alertas"
            nivel_resumen = "ERROR" if hay_error else "ALERTA"
            resultados.append({
                "item": "GENERAL", "campo": CAMPO,
                "mensaje": f"De {total} ítem(s) con dumping declarado, {cant} con inconsistencia — ver pestaña {pestana}",
                "nivel": nivel_resumen, "es_resumen": True,
            })
        else:
            resultados.append({
                "item": "GENERAL", "campo": CAMPO,
                "mensaje": f"De {total} ítem(s) con dumping declarado, ninguno con inconsistencia",
                "nivel": "OK", "es_resumen": True,
            })

    return resultados


# ── Validación: ítems usados (identificación para revisión) ──────────────────

def validar_items_usados(df_items: pd.DataFrame, df_subitems: pd.DataFrame = None,
                          df_caratula: pd.DataFrame = None, datos_cm: dict = None,
                          datos_facturas: dict = None) -> list:
    """
    Identifica los ítems declarados como USADO (campo ESTADO) y los
    informa en Alertas con su código de material y factura asociada
    (mismo sufijo de referencia que el resto de las validaciones), para
    que el despachante los revise puntualmente — por ejemplo, para
    confirmar que el cargo de CORE DEPOSIT (si corresponde) esté bien
    contemplado en el FOB, o cualquier otra particularidad de usados.

    No determina si el ítem usado está bien o mal declarado en sí mismo
    (eso ya lo cubren otras validaciones, como la del concepto 056/032
    en validar_liquidacion) — esta función es puramente de
    identificación, para que ningún ítem usado pase desapercibido.

    Genera un resumen exclusivo de Revisión General ("es_resumen": True)
    con la cantidad total, y el detalle por ítem vive en Alertas.
    """
    resultados = []
    CAMPO = "ÍTEM USADO"

    if df_items is None or df_items.empty:
        return resultados

    ref_map = _build_ref_map(df_items, df_subitems, df_caratula, datos_cm, datos_facturas)

    items_usados = []
    for _, row in df_items.iterrows():
        item = str(row.get("ITEM", "?")).strip().zfill(4)
        estado = row.get("ESTADO", "").strip().upper()
        if "USADO" in estado:
            items_usados.append(item)
            r = ref_map.get(item, {})
            suf = _ref(r.get("modelo", ""), r.get("factura", ""), r.get("cm", ""))
            resultados.append(alerta(item, CAMPO, f"Ítem declarado USADO — revisar{suf}", "ALERTA"))

    if items_usados:
        resultados.append({
            "item": "GENERAL", "campo": CAMPO,
            "mensaje": f"{len(items_usados)} ítem(s) declarado(s) USADO — ver pestaña Alertas",
            "nivel": "ALERTA", "es_resumen": True,
        })
    else:
        resultados.append({
            "item": "GENERAL", "campo": CAMPO,
            "mensaje": "Ningún ítem declarado USADO",
            "nivel": "OK", "es_resumen": True,
        })

    return resultados


# ── Validación: campos de control declarativos (Items) ───────────────────────

# Deben tener siempre exactamente el valor esperado — vacío también es error.
CAMPOS_OBLIGATORIOS_FIJOS = {
    "I:IVAADICIONAL1": "NO_VALIDA",
    "I:GANANCIASOP3": "COMERC",
    "I:DSE.PAIS.OPC": "430",
    "I:TERRITORIOTEX-1": "NO",
    "I:PAISEMIT-FACTCOM": "SI",
    "I:IMPOGIRO-DIV-OPC": "CGDDIF",
}

# El resto: en teoría deberían venir vacíos. No se compara contra un valor
# esperado (evita tener que adivinar "NO" vs "NO_VALIDA" campo por campo) —
# simplemente se informa cualquier valor no vacío que traigan, agrupado por
# campo+valor a nivel despacho (para no listar cientos de ítems sueltos
# cuando el mismo valor se repite en muchos), con el detalle real ítem por
# ítem en Alertas.
CAMPOS_INFORMATIVOS = [
    "I:LEY26184ART6", "I:ARRISEMITEX", "I:ARN-TXT", "I:FTALATOS-TXT",
    "I:DSE.MARCA.FRA1", "I:RES451-19ANEXOI", "I:ESDES", "I:GAS1TXT",
    "I:0N116_09_TXT1", "I:SEDRONARACSULTXT", "I:DSE.SINMARCA.OPC",
    "I:ENV-UTEN-ALIM", "I:SEMITEX-TXT", "I:MERCURES75_TXT",
    "I:PRECURSORMIXOPC", "I:FLORA-R1766-TXT1", "I:R92-NO-D613-TXT",
    "I:PRODFAUSILVETEX", "I:CODADV-GTZIA-TXT", "I:INVO-D1/10-TXT",
    "I:REGLAGRALI2AOPC", "I:ASBESTOTXT", "I:EXPLOARMASQUIMTX",
    "I:CODADV-POSEE-TXT",
]


def validar_campos_control(df_items: pd.DataFrame, df_subitems: pd.DataFrame = None,
                            df_caratula: pd.DataFrame = None, datos_cm: dict = None,
                            datos_facturas: dict = None) -> list:
    """
    Controla un set de campos declarativos de la solapa Items, en 2
    modalidades (ver listas/dict arriba):

      - CAMPOS_OBLIGATORIOS_FIJOS: siempre el valor esperado (vacío
        también es error) -> ERROR por ítem.
      - CAMPOS_INFORMATIVOS: en teoría vacíos. No se comparan contra un
        valor esperado — cualquier valor no vacío se informa (ALERTA),
        agrupado por campo+valor a nivel despacho.

    Genera detalle por ítem (mismo sufijo de referencia que el resto de
    las validaciones) y un resumen exclusivo de Revisión General: una
    línea por cada (campo obligatorio) con diferencias, y una línea por
    cada (campo informativo, valor) encontrado.
    """
    resultados = []

    if df_items is None or df_items.empty:
        return resultados

    ref_map = _build_ref_map(df_items, df_subitems, df_caratula, datos_cm, datos_facturas)
    total_items = 0

    items_obligatorio_mal = set()

    # {campo: {valor: [items]}}
    valores_informativos = {c: {} for c in CAMPOS_INFORMATIVOS}

    for _, row in df_items.iterrows():
        item = str(row.get("ITEM", "?")).strip().zfill(4)
        r = ref_map.get(item, {})
        suf = _ref(r.get("modelo", ""), r.get("factura", ""), r.get("cm", ""))
        total_items += 1

        for campo, esperado in CAMPOS_OBLIGATORIOS_FIJOS.items():
            val = str(row.get(campo, "") or "").strip()
            if val.upper() != esperado.upper():
                items_obligatorio_mal.add(item)
                resultados.append(alerta(item, campo,
                    f"Debe ser '{esperado}', tiene: '{val or '(vacío)'}'{suf}", "ERROR"))

        for campo in CAMPOS_INFORMATIVOS:
            val = str(row.get(campo, "") or "").strip()
            if val:
                resultados.append(alerta(item, campo, f"Declarado: '{val}' — informativo, verificar{suf}"))
                valores_informativos[campo].setdefault(val, []).append(item)

    # ── Resumen: campos obligatorios ──
    if items_obligatorio_mal:
        cant = len(items_obligatorio_mal)
        resultados.append({
            "item": "GENERAL", "campo": "CAMPOS DE CONTROL (obligatorios)",
            "mensaje": f"{cant} de {total_items} ítems con algún campo obligatorio fuera de lo esperado — ver pestaña Errores",
            "nivel": "ERROR", "es_resumen": True,
        })
    else:
        resultados.append({
            "item": "GENERAL", "campo": "CAMPOS DE CONTROL (obligatorios)",
            "mensaje": f"Los {total_items} ítems tienen los campos obligatorios según lo esperado",
            "nivel": "OK", "es_resumen": True,
        })

    # ── Resumen: campos informativos (agrupado por campo + valor) ──
    campos_con_valores = {c: v for c, v in valores_informativos.items() if v}
    if not campos_con_valores:
        resultados.append({
            "item": "GENERAL", "campo": "CAMPOS DE CONTROL (informativos)",
            "mensaje": f"Ningún ítem con valor declarado en los {len(CAMPOS_INFORMATIVOS)} campos informativos revisados",
            "nivel": "OK", "es_resumen": True,
        })
    else:
        for campo, valores in campos_con_valores.items():
            for valor, items_lista in valores.items():
                cant = len(items_lista)
                resultados.append({
                    "item": "GENERAL", "campo": campo,
                    "mensaje": f"Valor '{valor}' declarado en {cant} de {total_items} ítems — ver pestaña Alertas",
                    "nivel": "ALERTA", "es_resumen": True,
                })
        campos_vacios = [c for c in CAMPOS_INFORMATIVOS if c not in campos_con_valores]
        if campos_vacios:
            resultados.append({
                "item": "GENERAL", "campo": "CAMPOS DE CONTROL (informativos)",
                "mensaje": f"Otros {len(campos_vacios)} campos informativos sin valores declarados: {', '.join(campos_vacios)}",
                "nivel": "OK", "es_resumen": True,
            })

    return resultados
