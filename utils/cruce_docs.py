import re
import pandas as pd
from utils.parser_di import normalizar_codigo, safe_float
from config.defaults import TOLERANCIA_FOB


def alerta(item, campo, mensaje, nivel="ALERTA"):
    return {"item": item, "campo": campo, "mensaje": mensaje, "nivel": nivel}

def ok(item, campo, mensaje):
    return {"item": item, "campo": campo, "mensaje": mensaje, "nivel": "OK"}


# ── Helpers ───────────────────────────────────────────────────────────────────

def _cargar_codigos_clasificacion(df_clasi: pd.DataFrame) -> set:
    """
    Extrae el set de códigos de parte canónicos del Excel de clasificaciones.
    La columna PART_NUMBER ya trae el código sin guión y sin sufijo de origen.
    Ej: '1K6853', '5417108', '6F8146'
    """
    if df_clasi is None or df_clasi.empty:
        return set()
    col = None
    for c in df_clasi.columns:
        if "PART" in c.upper() and "NUMBER" in c.upper():
            col = c
            break
    if col is None:
        return set()
    return set(df_clasi[col].astype(str).str.strip().str.upper())


def _validar_codigo_en_clasificacion(codigo: str, codigos_clasi: set, item_num: str, nro_factura: str) -> list:
    """
    Segunda validación: verifica que el código extraído de la factura
    exista en el Excel de clasificaciones subido.
    Retorna lista de resultados (ok o alerta).
    """
    if not codigos_clasi:
        return []  # sin clasificación cargada, no validar
    if codigo in codigos_clasi:
        return [ok(item_num, "CÓDIGO EN CLASIFICACIÓN",
                   f"Código '{codigo}' verificado en clasificación | Factura: {nro_factura}")]
    else:
        return [alerta(item_num, "CÓDIGO EN CLASIFICACIÓN",
                       f"Código '{codigo}' NO encontrado en clasificación — verificar parseo | Factura: {nro_factura}",
                       "ALERTA")]


# ── Validación CM vs DI ───────────────────────────────────────────────────────

def validar_cm_vs_di(df_items: pd.DataFrame, df_subitems: pd.DataFrame, datos_cm: dict) -> list:
    resultados = []

    grupos_cm = {}
    for _, row in df_items.iterrows():
        item = str(row.get("ITEM", "")).strip().zfill(4)
        cm   = row.get("D:CERTSM", "").strip()
        if cm:
            grupos_cm.setdefault(cm, []).append(item)

    for numero_cm, items_del_cm in grupos_cm.items():
        if numero_cm not in datos_cm:
            resultados.append(alerta(
                ", ".join(items_del_cm), "CM",
                f"No se encontró PDF del CM: {numero_cm} — no se pudo validar", "ALERTA"
            ))
            continue

        cm_data = datos_cm[numero_cm]
        if "error" in cm_data:
            resultados.append(alerta(
                ", ".join(items_del_cm), "CM",
                f"Error al leer CM {numero_cm}: {cm_data['error']}", "ERROR"
            ))
            continue

        items_cm = cm_data.get("items", [])

        for item_num in items_del_cm:
            sub = df_subitems[df_subitems["ITEM"].str.zfill(4) == item_num]
            if sub.empty:
                resultados.append(alerta(item_num, "SUBITEM",
                    f"No se encontró subitem en el DI para ítem {item_num}"))
                continue

            for _, subrow in sub.iterrows():
                modelo_di  = normalizar_codigo(subrow.get("MODELO", ""))
                if not modelo_di:
                    continue
                ncm_di_raw = subrow.get("NCM", "").replace(".", "").strip()
                ncm_di_8   = ncm_di_raw[:8] if len(ncm_di_raw) >= 8 else ncm_di_raw
                cantidad_di = safe_float(subrow.get("CANTIDAD", 0))
                fob_di      = safe_float(subrow.get("MONTO FOB", 0))

                matches_codigo = [ic for ic in items_cm
                                  if normalizar_codigo(ic.get("codigo_parte", "")) == modelo_di]
                if not matches_codigo:
                    matches_codigo = [ic for ic in items_cm
                                      if ic.get("ncm_8_digitos", "").replace(".", "")[:8] == ncm_di_8]

                if not matches_codigo:
                    resultados.append(alerta(item_num, "CM",
                        f"[CM: {numero_cm}] No se encontró código '{modelo_di}' ni NCM '{ncm_di_8}'",
                        "ERROR"))
                    continue

                item_cm = next(
                    (ic for ic in matches_codigo
                     if abs(safe_float(ic.get("cantidad", 0)) - cantidad_di) < 0.01
                     and abs(safe_float(ic.get("valor_total_fob", 0)) - fob_di) < TOLERANCIA_FOB),
                    matches_codigo[0]
                )

                ncm_cm_8 = item_cm.get("ncm_8_digitos", "").replace(".", "")[:8]
                if ncm_di_8 != ncm_cm_8:
                    resultados.append(alerta(item_num, "NCM",
                        f"[CM: {numero_cm}] NCM DI: {ncm_di_8} — NCM CM: {ncm_cm_8}", "ERROR"))
                else:
                    resultados.append(ok(item_num, "NCM", f"NCM correcto: {ncm_di_8}"))

                codigo_cm = normalizar_codigo(item_cm.get("codigo_parte", ""))
                if modelo_di != codigo_cm:
                    resultados.append(alerta(item_num, "MODELO",
                        f"[CM: {numero_cm}] Código DI: '{modelo_di}' — Código CM: '{codigo_cm}'",
                        "ERROR"))
                else:
                    resultados.append(ok(item_num, "MODELO", f"Código de parte correcto: {modelo_di}"))

                cantidad_cm = safe_float(item_cm.get("cantidad", 0))
                if cantidad_di > cantidad_cm:
                    resultados.append(alerta(item_num, "CANTIDAD",
                        f"[CM: {numero_cm}] Código: {modelo_di} | Cantidad DI ({cantidad_di}) supera habilitado en CM ({cantidad_cm})",
                        "ERROR"))
                elif abs(cantidad_di - cantidad_cm) > 0.01:
                    resultados.append(alerta(item_num, "CANTIDAD",
                        f"[CM: {numero_cm}] Código: {modelo_di} | Cantidad DI ({cantidad_di}) distinta a la habilitada en CM ({cantidad_cm}) — usa solo una parte del cupo, verificar si es intencional",
                        "ALERTA"))
                else:
                    resultados.append(ok(item_num, "CANTIDAD", f"Código: {modelo_di} | Cantidad OK: {cantidad_di} = {cantidad_cm}"))

                fob_cm = safe_float(item_cm.get("valor_total_fob", 0))
                if abs(fob_di - fob_cm) > TOLERANCIA_FOB:
                    resultados.append(alerta(item_num, "MONTO FOB",
                        f"[CM: {numero_cm}] Código: {item_cm.get('codigo_parte','')} | "
                        f"FOB DI: {fob_di:.2f} — CM: {fob_cm:.2f} (dif: {abs(fob_di - fob_cm):.2f})",
                        "ALERTA"))
                else:
                    resultados.append(ok(item_num, "MONTO FOB", f"FOB correcto: {fob_di:.2f}"))

    return resultados


# ── Validación Factura vs DI ──────────────────────────────────────────────────

def validar_factura_vs_di(
    df_items: pd.DataFrame,
    df_subitems: pd.DataFrame,
    datos_facturas: dict,
    df_clasificacion: pd.DataFrame = None,   # ← nuevo parámetro opcional
) -> list:
    """
    Valida FOB de ítems del DI contra las facturas extraídas.
    Si se pasa df_clasificacion, hace una segunda validación del código
    contra el Excel de clasificaciones.
    """
    resultados    = []
    codigos_clasi = _cargar_codigos_clasificacion(df_clasificacion)
    # Set de líneas de factura ya usadas, por factura: {nro_factura: {id(item), ...}}
    # Evita que dos ítems distintos del DI matcheen contra la misma línea de
    # factura cuando código + cantidad son idénticos en más de una línea.
    usados_por_factura: dict = {}

    for _, subrow in df_subitems.iterrows():
        item_num   = str(subrow.get("ITEM", "")).strip().zfill(4)
        modelo_di  = normalizar_codigo(subrow.get("MODELO", ""))
        fob_di     = safe_float(subrow.get("MONTO FOB", 0))
        cantidad_di = safe_float(subrow.get("CANTIDAD", 0))

        if not modelo_di or not item_num or item_num == "0000":
            continue

        encontrado = False
        for nro_factura, fac_data in datos_facturas.items():
            if "error" in fac_data:
                continue

            items_factura = fac_data.get("items", [])
            usados = usados_por_factura.setdefault(nro_factura, set())

            # Buscar por código + cantidad exacta, excluyendo líneas ya usadas
            match_fac = next(
                (i for i in items_factura
                 if id(i) not in usados
                 and normalizar_codigo(i.get("codigo_parte", "")) == modelo_di
                 and abs(safe_float(i.get("cantidad", 0)) - cantidad_di) < 0.01),
                None
            )
            # Fallback: solo por código, excluyendo usadas
            if not match_fac:
                match_fac = next(
                    (i for i in items_factura
                     if id(i) not in usados
                     and normalizar_codigo(i.get("codigo_parte", "")) == modelo_di),
                    None
                )

            if match_fac:
                encontrado = True
                usados.add(id(match_fac))
                tipo_cargos = fac_data.get("tipo_cargos", "por_item")

                if tipo_cargos == "por_item":
                    fob_esperado = safe_float(match_fac.get("subtotal", 0))
                else:
                    total_partes = safe_float(fac_data.get("total_partes", 0))
                    total_cargos = safe_float(fac_data.get("total_cargos", 0))
                    precio_parte = safe_float(match_fac.get("precio_total_parte", 0))
                    proporcion   = precio_parte / total_partes if total_partes else 0
                    fob_esperado = round(precio_parte + (total_cargos * proporcion), 2)

                codigo_ref = match_fac.get("codigo_parte", modelo_di)

                # Validación FOB
                if abs(fob_di - fob_esperado) > TOLERANCIA_FOB:
                    resultados.append(alerta(
                        item_num, "MONTO FOB (FACTURA)",
                        f"FOB DI: {fob_di:.2f} — FOB factura: {fob_esperado:.2f} "
                        f"(dif: {abs(fob_di - fob_esperado):.2f}) | "
                        f"Código: {codigo_ref} | Factura: {nro_factura}",
                        "ALERTA"
                    ))
                else:
                    resultados.append(ok(
                        item_num, "MONTO FOB (FACTURA)",
                        f"FOB correcto vs factura: {fob_di:.2f} | "
                        f"Código: {codigo_ref} | Factura: {nro_factura}"
                    ))

                # Segunda validación: código en clasificación
                resultados.extend(
                    _validar_codigo_en_clasificacion(modelo_di, codigos_clasi, item_num, nro_factura)
                )
                break

        if not encontrado:
            resultados.append(alerta(
                item_num, "MONTO FOB (FACTURA)",
                f"No se encontró código '{modelo_di}' en ninguna factura subida",
                "ALERTA"
            ))
            # Segunda validación igual: el código puede estar en clasificación aunque no en factura
            resultados.extend(
                _validar_codigo_en_clasificacion(modelo_di, codigos_clasi, item_num, "—")
            )

    return resultados


def _normalizar_moneda(texto: str) -> str:
    """
    Normaliza distintas formas de nombrar la misma moneda a un código corto.
    Ej: 'DOL - DOLAR ESTADOUNIDENSE' -> 'USD', 'US DOLLAR' -> 'USD'
    """
    t = (texto or "").upper()
    if "DOLAR" in t or "DOLLAR" in t or t.strip() == "USD":
        return "USD"
    if "EURO" in t or t.strip() == "EUR":
        return "EUR"
    return t.strip()


def _normalizar_incoterm(texto: str) -> str:
    """Extrae el código de 3 letras de un incoterm, sea cual sea el formato."""
    t = (texto or "").strip().upper()
    m = re.search(r"\b([A-Z]{3})\b", t)
    return m.group(1) if m else t


# ── Validación de Totales de Carátula (FOB, Moneda, Incoterm) ─────────────────

def validar_caratula_totales(caratula: dict, datos_facturas: dict, datos_forwarding: dict = None) -> list:
    """
    Valida, contra la carátula del DI:
      - FOB total: suma de total_factura de todas las facturas subidas.
      - Moneda (FOB/Flete/Seguro): coincide con la moneda real de cada
        documento de origen (factura para FOB, forwarding para Flete/Seguro).
      - Incoterm: coincide entre todas las facturas y contra el INCOTERM
        declarado en la carátula. Si una factura difiere de otra, o de
        la carátula, se alerta.
    No requiere llamadas a la API — todo proviene de datos ya extraídos.
    """
    resultados = []

    def al(campo, msg, nivel="ALERTA"):
        return {"item": "CARÁTULA", "campo": campo, "mensaje": msg, "nivel": nivel}
    def ok_(campo, msg):
        return {"item": "CARÁTULA", "campo": campo, "mensaje": msg, "nivel": "OK"}

    facturas_validas = {k: v for k, v in (datos_facturas or {}).items() if "error" not in v}

    # ── FOB total: suma de facturas vs carátula ──
    if facturas_validas:
        fob_total_facturas = round(sum(safe_float(f.get("total_factura", 0)) for f in facturas_validas.values()), 2)
        fob_di = safe_float(_buscar_caratula(caratula, "FOB") or 0)

        if abs(fob_di - fob_total_facturas) > TOLERANCIA_FOB:
            resultados.append(al("FOB", f"DI: {fob_di:.2f} — Suma de facturas: {fob_total_facturas:.2f} "
                                          f"(dif: {abs(fob_di - fob_total_facturas):.2f})", "ERROR"))
        else:
            resultados.append(ok_("FOB", f"FOB total correcto: {fob_di:.2f}"))

    # ── Incoterm: entre facturas, y contra carátula ──
    if facturas_validas:
        incoterms_facturas = {}  # incoterm -> [nombres de factura]
        for nro_factura, f in facturas_validas.items():
            ic = _normalizar_incoterm(f.get("incoterm", ""))
            if ic:
                incoterms_facturas.setdefault(ic, []).append(nro_factura)

        if len(incoterms_facturas) > 1:
            detalle = " | ".join(f"{ic}: {', '.join(facs)}" for ic, facs in incoterms_facturas.items())
            resultados.append(al("INCOTERM", f"Las facturas declaran incoterms distintos entre sí — {detalle}", "ERROR"))
        elif incoterms_facturas:
            incoterm_facturas = next(iter(incoterms_facturas))
            incoterm_di = _normalizar_incoterm(_buscar_caratula(caratula, "INCOTERM") or "")
            if incoterm_di and incoterm_di != incoterm_facturas:
                resultados.append(al("INCOTERM", f"DI: {incoterm_di} — Facturas: {incoterm_facturas}", "ERROR"))
            else:
                resultados.append(ok_("INCOTERM", f"Incoterm correcto: {incoterm_facturas}"))

    # ── Moneda FOB: facturas vs carátula ──
    if facturas_validas:
        monedas_facturas = {_normalizar_moneda(f.get("moneda", "")) for f in facturas_validas.values()}
        moneda_fob_di = _normalizar_moneda(_buscar_caratula(caratula, "MONEDA FOB") or "")

        if len(monedas_facturas) > 1:
            resultados.append(al("MONEDA FOB", f"Las facturas declaran monedas distintas entre sí: {monedas_facturas}", "ERROR"))
        elif monedas_facturas:
            moneda_factura = next(iter(monedas_facturas))
            if moneda_fob_di and moneda_fob_di != moneda_factura:
                resultados.append(al("MONEDA FOB", f"DI: {moneda_fob_di} — Factura: {moneda_factura}", "ERROR"))
            else:
                resultados.append(ok_("MONEDA FOB", f"Moneda FOB correcta: {moneda_factura}"))

    # ── Moneda Flete / Seguro: forwarding vs carátula ──
    if datos_forwarding and "error" not in datos_forwarding:
        moneda_flete_fwd = _normalizar_moneda(datos_forwarding.get("moneda_flete", "") or datos_forwarding.get("moneda", ""))
        moneda_seguro_fwd = _normalizar_moneda(datos_forwarding.get("moneda_seguro", "") or datos_forwarding.get("moneda", ""))
        moneda_flete_di = _normalizar_moneda(_buscar_caratula(caratula, "MONEDA FLETE") or "")
        moneda_seg_di = _normalizar_moneda(_buscar_caratula(caratula, "MONEDA SEG") or "")

        if moneda_flete_fwd:
            if moneda_flete_di and moneda_flete_di != moneda_flete_fwd:
                resultados.append(al("MONEDA FLETE", f"DI: {moneda_flete_di} — Forwarding: {moneda_flete_fwd}", "ERROR"))
            else:
                resultados.append(ok_("MONEDA FLETE", f"Moneda flete correcta: {moneda_flete_fwd}"))

        if moneda_seguro_fwd:
            if moneda_seg_di and moneda_seg_di != moneda_seguro_fwd:
                resultados.append(al("MONEDA SEG", f"DI: {moneda_seg_di} — Forwarding: {moneda_seguro_fwd}", "ERROR"))
            else:
                resultados.append(ok_("MONEDA SEG", f"Moneda seguro correcta: {moneda_seguro_fwd}"))

    return resultados


# ── Validación Carátula vs Docs ───────────────────────────────────────────────

def validar_caratula_vs_docs(caratula: dict, datos_forwarding: dict, datos_bl: dict,
                              datos_facturas: dict, config: dict) -> list:
    resultados = []

    def al(campo, msg, nivel="ALERTA"):
        return {"item": "CARÁTULA", "campo": campo, "mensaje": msg, "nivel": nivel}
    def ok_(campo, msg):
        return {"item": "CARÁTULA", "campo": campo, "mensaje": msg, "nivel": "OK"}

    banco = _buscar_caratula(caratula, "I:BANCOSARGENTINA")
    if banco and banco != "016":
        resultados.append(al("I:BANCOSARGENTINA", f"Debe ser 016, tiene: '{banco}'", "ERROR"))
    elif banco:
        resultados.append(ok_("I:BANCOSARGENTINA", "Banco correcto: 016"))

    impogiro = _buscar_caratula(caratula, "I:IMPOGIRO-DIV-OPC")
    if impogiro and impogiro != "CGDDIF":
        resultados.append(al("I:IMPOGIRO-DIV-OPC", f"Debe ser CGDDIF, tiene: '{impogiro}'", "ERROR"))

    if datos_forwarding and "error" not in datos_forwarding:
        flete_doc  = datos_forwarding.get("flete_total", 0)
        seguro_doc = datos_forwarding.get("seguro_total", 0)
        alertas_fw = datos_forwarding.get("alertas", [])
        flete_di   = safe_float(_buscar_caratula(caratula, "FLETE") or 0)
        seguro_di  = safe_float(_buscar_caratula(caratula, "SEGURO") or 0)

        if abs(flete_di - flete_doc) > TOLERANCIA_FOB:
            resultados.append(al("FLETE", f"DI: {flete_di:.2f} — Forwarding: {flete_doc:.2f}", "ERROR"))
        else:
            resultados.append(ok_("FLETE", f"Flete correcto: {flete_di:.2f}"))

        if abs(seguro_di - seguro_doc) > TOLERANCIA_FOB:
            resultados.append(al("SEGURO", f"DI: {seguro_di:.2f} — Forwarding: {seguro_doc:.2f}", "ERROR"))
        else:
            resultados.append(ok_("SEGURO", f"Seguro correcto: {seguro_di:.2f}"))

        for a in alertas_fw:
            resultados.append(al("FORWARDING", f"Cargo adicional detectado: {a}", "ALERTA"))

    if datos_bl and "error" not in datos_bl:
        bl_doc   = datos_bl.get("bl_number", "").strip().upper()
        itns_bl  = datos_bl.get("itns", [])
        bl_di    = _buscar_caratula(caratula, "DOCUMENTO") or ""
        if bl_di:
            if bl_doc and bl_doc not in bl_di.upper() and bl_di.upper() not in bl_doc:
                resultados.append(al("BL", f"BL en DI: '{bl_di}' — BL en documento: '{bl_doc}'", "ERROR"))
            else:
                resultados.append(ok_("BL", f"BL correcto: {bl_doc}"))

        itn_di = _buscar_caratula(caratula, "I:ITN-EEUU") or ""
        for itn in itns_bl:
            if itn.upper() not in itn_di.upper():
                resultados.append(al("I:ITN-EEUU", f"ITN del BL '{itn}' no figura en el DI"))

    return resultados


def _buscar_caratula(caratula: dict, campo: str) -> str | None:
    campo_upper = campo.upper()
    for k, v in caratula.items():
        if campo_upper in k.upper():
            return str(v).strip()
    return None


# ── Validación DJ de Origen ───────────────────────────────────────────────────

def validar_dj_origen(df_items: pd.DataFrame, df_subitems: pd.DataFrame, datos_dj: list) -> list:
    from utils.parser_di import normalizar_codigo, safe_float
    TOLERANCIA_CIF = 0.10

    resultados = []

    PAISES = {
        "ESTADOS UNIDOS": ["212", "ESTADOS UNIDOS"],
        "CHINA":          ["156", "CHINA"],
        "MEXICO":         ["484", "MEXICO", "MÉXICO"],
        "ITALIA":         ["380", "ITALIA"],
        "CANADA":         ["124", "CANADA", "CANADÁ"],
        "INDIA":          ["356", "INDIA"],
        "ALEMANIA":       ["276", "ALEMANIA"],
        "JAPON":          ["392", "JAPON", "JAPÓN"],
        "BRASIL":         ["076", "BRASIL"],
    }

    def pais_coincide(pais_dj, origen_di):
        pais_dj   = pais_dj.upper().strip()
        origen_di = origen_di.upper().strip()
        for _, variantes in PAISES.items():
            if any(v in pais_dj for v in variantes):
                if any(v in origen_di for v in variantes):
                    return True
        return pais_dj in origen_di or origen_di in pais_dj

    def unidad_coincide(unidad_dj, unidad_di):
        ud  = unidad_dj.upper().strip()
        udi = unidad_di.upper().strip()
        return ud in udi or udi.split("- ")[-1].strip() in ud

    ifs_subidos = [dj["numero_if"].strip().upper()
                   for dj in datos_dj if "error" not in dj and dj.get("numero_if")]

    for _, row in df_items.iterrows():
        item      = str(row.get("ITEM", "")).strip().zfill(4)
        dj_campo  = row.get("D:DJ-ORIG-NOPREFER", "").strip()
        if not dj_campo:
            continue
        dj_upper  = dj_campo.upper()
        coincide  = any(dj_upper in if_sub or if_sub in dj_upper for if_sub in ifs_subidos)
        if not ifs_subidos or not coincide:
            msg = (f"DJ declarada '{dj_campo}' pero no se subió ningún PDF de DJ"
                   if not ifs_subidos else
                   f"DJ '{dj_campo}' no coincide con ningún PDF subido ({', '.join(ifs_subidos)})")
            resultados.append({"item": item, "campo": "D:DJ-ORIG-NOPREFER",
                                "mensaje": msg, "nivel": "ERROR"})

    for dj_data in datos_dj:
        if "error" in dj_data:
            continue
        numero_if = dj_data.get("numero_if", "")
        for prod in dj_data.get("productos", []):
            codigo_dj  = prod["codigo_parte"].strip()
            ncm8_dj    = prod["ncm_8_digitos"].strip().replace(".", "")
            sim3_dj    = prod["ncm_sim_3"].strip()
            pais_dj    = prod["pais_origen"].strip()
            unidad_dj  = prod["unidad_medida"].strip()
            qty_dj     = prod["cantidad"]
            cif_dj     = prod["valor_cif_unit"]

            items_match = []
            for _, irow in df_items.iterrows():
                dj_campo = irow.get("D:DJ-ORIG-NOPREFER", "").strip()
                if not dj_campo or numero_if.upper() not in dj_campo.upper():
                    continue
                item_num = str(irow.get("ITEM", "")).strip().zfill(4)
                sub = df_subitems[df_subitems["ITEM"].str.zfill(4) == item_num]
                for _, srow in sub.iterrows():
                    if normalizar_codigo(str(srow.get("MODELO", ""))) == normalizar_codigo(codigo_dj):
                        if safe_float(irow.get("CANTIDAD", 0)) == qty_dj:
                            items_match.append((item_num, irow, srow))

            if not items_match:
                resultados.append({"item": "GENERAL", "campo": "DJ-ORIG",
                    "mensaje": f"[DJ {numero_if}] Código '{codigo_dj}' no encontrado en ningún ítem del DI con esta DJ",
                    "nivel": "ERROR"})
                continue

            for item_num, irow, srow in items_match:
                ncm_di      = str(srow.get("NCM", "")).replace(".", "").strip()
                ncm8_di     = ncm_di[:8]
                ncm_full_di = ncm_di
                sim3_di_ext = ncm_full_di[-4:] if len(ncm_full_di) >= 4 else ncm_full_di

                if ncm8_di != ncm8_dj:
                    resultados.append({"item": item_num, "campo": "DJ NCM 8D",
                        "mensaje": f"NCM DJ ({ncm8_dj}) ≠ DI ({ncm8_di})", "nivel": "ERROR"})
                else:
                    resultados.append({"item": item_num, "campo": "DJ NCM 8D",
                        "mensaje": f"NCM 8 dígitos OK: {ncm8_dj}", "nivel": "OK"})

                if sim3_dj.upper() not in sim3_di_ext.upper() and sim3_di_ext.upper() not in sim3_dj.upper():
                    resultados.append({"item": item_num, "campo": "DJ SIM 3D",
                        "mensaje": f"Últimos 3 SIM DJ ({sim3_dj}) ≠ DI ({sim3_di_ext})", "nivel": "ALERTA"})
                else:
                    resultados.append({"item": item_num, "campo": "DJ SIM 3D",
                        "mensaje": f"SIM 3 dígitos OK: {sim3_dj}", "nivel": "OK"})

                origen_di = str(irow.get("ORIGEN", "")).strip()
                if not pais_coincide(pais_dj, origen_di):
                    resultados.append({"item": item_num, "campo": "DJ PAÍS ORIGEN",
                        "mensaje": f"País DJ ({pais_dj}) ≠ DI ({origen_di})", "nivel": "ERROR"})
                else:
                    resultados.append({"item": item_num, "campo": "DJ PAÍS ORIGEN",
                        "mensaje": f"País origen OK: {pais_dj}", "nivel": "OK"})

                unidad_di = str(srow.get("UNIDAD DECLARADA", "")).strip()
                if not unidad_coincide(unidad_dj, unidad_di):
                    resultados.append({"item": item_num, "campo": "DJ UNIDAD",
                        "mensaje": f"Unidad DJ ({unidad_dj}) ≠ DI ({unidad_di})", "nivel": "ALERTA"})
                else:
                    resultados.append({"item": item_num, "campo": "DJ UNIDAD",
                        "mensaje": f"Unidad OK: {unidad_dj}", "nivel": "OK"})

                qty_di = safe_float(irow.get("CANTIDAD", 0))
                if qty_di != qty_dj:
                    resultados.append({"item": item_num, "campo": "DJ CANTIDAD",
                        "mensaje": f"Cantidad DJ ({qty_dj:.0f}) ≠ DI ({qty_di:.0f})", "nivel": "ERROR"})
                else:
                    resultados.append({"item": item_num, "campo": "DJ CANTIDAD",
                        "mensaje": f"Cantidad OK: {qty_dj:.0f}", "nivel": "OK"})

                fob    = safe_float(irow.get("VALOR FOB", 0))
                flete  = safe_float(irow.get("FLETE EN DIV", 0))
                seguro = safe_float(irow.get("SEGURO EN DIV", 0))
                qty2   = safe_float(irow.get("CANTIDAD", 1)) or 1
                cif_di = round((fob + flete + seguro) / qty2, 2)
                diff   = abs(cif_di - cif_dj)
                if diff > TOLERANCIA_CIF:
                    resultados.append({"item": item_num, "campo": "DJ CIF UNIT",
                        "mensaje": f"CIF unitario DJ ({cif_dj:.2f}) ≠ DI ({cif_di:.2f}) | diff: {diff:.2f}",
                        "nivel": "ERROR"})
                else:
                    resultados.append({"item": item_num, "campo": "DJ CIF UNIT",
                        "mensaje": f"CIF unitario OK: {cif_dj:.2f}", "nivel": "OK"})

    return resultados
