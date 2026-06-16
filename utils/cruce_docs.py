import pandas as pd
from utils.parser_di import normalizar_codigo, safe_float
from config.defaults import TOLERANCIA_FOB

def alerta(item, campo, mensaje, nivel="ALERTA"):
    return {"item": item, "campo": campo, "mensaje": mensaje, "nivel": nivel}

def ok(item, campo, mensaje):
    return {"item": item, "campo": campo, "mensaje": mensaje, "nivel": "OK"}


def validar_cm_vs_di(df_items: pd.DataFrame, df_subitems: pd.DataFrame, datos_cm: dict) -> list:
    """
    Cruza los ítems del DI contra los datos extraídos del CM.
    datos_cm: dict con número de CM como clave y datos extraídos por la API como valor.
    """
    resultados = []

    # Agrupar ítems por CM
    grupos_cm = {}
    for _, row in df_items.iterrows():
        item = str(row.get("ITEM", "")).strip().zfill(4)
        cm = row.get("D:CERTSM", "").strip()
        if cm:
            if cm not in grupos_cm:
                grupos_cm[cm] = []
            grupos_cm[cm].append(item)

    # Para cada CM, validar sus ítems
    for numero_cm, items_del_cm in grupos_cm.items():
        if numero_cm not in datos_cm:
            resultados.append(alerta(
                ", ".join(items_del_cm), "CM",
                f"No se encontró PDF del CM: {numero_cm} — no se pudo validar",
                "ALERTA"
            ))
            continue

        cm_data = datos_cm[numero_cm]
        if "error" in cm_data:
            resultados.append(alerta(
                ", ".join(items_del_cm), "CM",
                f"Error al leer CM {numero_cm}: {cm_data['error']}",
                "ERROR"
            ))
            continue

        items_cm = cm_data.get("items", [])

        for item_num in items_del_cm:
            # Obtener datos del subitem del DI
            sub = df_subitems[df_subitems["ITEM"].str.zfill(4) == item_num]
            if sub.empty:
                resultados.append(alerta(item_num, "SUBITEM", f"No se encontró subitem en el DI para ítem {item_num}"))
                continue

            for _, subrow in sub.iterrows():
                modelo_di = normalizar_codigo(subrow.get("MODELO", ""))
                ncm_di_raw = subrow.get("NCM", "").replace(".", "").strip()
                ncm_di_8 = ncm_di_raw[:8] if len(ncm_di_raw) >= 8 else ncm_di_raw
                cantidad_di = safe_float(subrow.get("CANTIDAD", 0))
                fob_di = safe_float(subrow.get("MONTO FOB", 0))

                # Buscar TODOS los matches del código en el CM
                matches_codigo = [ic for ic in items_cm 
                                  if normalizar_codigo(ic.get("codigo_parte", "")) == modelo_di]
                if not matches_codigo:
                    matches_codigo = [ic for ic in items_cm 
                                      if ic.get("ncm_8_digitos", "").replace(".", "")[:8] == ncm_di_8]

                if not matches_codigo:
                    resultados.append(alerta(
                        item_num, "CM",
                        f"[CM: {numero_cm}] No se encontró código '{modelo_di}' ni NCM '{ncm_di_8}'",
                        "ERROR"
                    ))
                    continue

                # Buscar match exacto por cantidad Y FOB
                item_cm = None
                for ic in matches_codigo:
                    cant_ic = safe_float(ic.get("cantidad", 0))
                    fob_ic = safe_float(ic.get("valor_total_fob", 0))
                    if abs(cant_ic - cantidad_di) < 0.01 and abs(fob_ic - fob_di) < TOLERANCIA_FOB:
                        item_cm = ic
                        break

                # Si no hay match exacto tomar el primero
                if not item_cm:
                    item_cm = matches_codigo[0]

                # Validar NCM
                ncm_cm_8 = item_cm.get("ncm_8_digitos", "").replace(".", "")[:8]
                if ncm_di_8 != ncm_cm_8:
                    resultados.append(alerta(
                        item_num, "NCM",
                        f"[CM: {numero_cm}] NCM DI: {ncm_di_8} — NCM CM: {ncm_cm_8}",
                        "ERROR"
                    ))
                else:
                    resultados.append(ok(item_num, "NCM", f"NCM correcto: {ncm_di_8}"))

                # Validar código de parte
                codigo_cm = normalizar_codigo(item_cm.get("codigo_parte", ""))
                if modelo_di != codigo_cm:
                    resultados.append(alerta(
                        item_num, "MODELO",
                        f"[CM: {numero_cm}] Código DI: '{modelo_di}' — Código CM: '{codigo_cm}'",
                        "ERROR"
                    ))
                else:
                    resultados.append(ok(item_num, "MODELO", f"Código de parte correcto: {modelo_di}"))

                # Validar cantidad
                cantidad_cm = safe_float(item_cm.get("cantidad", 0))
                if cantidad_di > cantidad_cm:
                    resultados.append(alerta(
                        item_num, "CANTIDAD",
                        f"[CM: {numero_cm}] Cantidad DI ({cantidad_di}) supera habilitado en CM ({cantidad_cm})",
                        "ERROR"
                    ))
                else:
                    resultados.append(ok(item_num, "CANTIDAD", f"Cantidad OK: {cantidad_di} ≤ {cantidad_cm}"))

                # Validar FOB
                fob_cm = safe_float(item_cm.get("valor_total_fob", 0))
                if abs(fob_di - fob_cm) > TOLERANCIA_FOB:
                    resultados.append(alerta(
                        item_num, "MONTO FOB",
                        f"[CM: {numero_cm}] FOB DI: {fob_di:.2f} — CM: {fob_cm:.2f} (dif: {abs(fob_di - fob_cm):.2f})",
                        "ALERTA"
                    ))
                else:
                    resultados.append(ok(item_num, "MONTO FOB", f"FOB correcto: {fob_di:.2f}"))

    return resultados


def validar_factura_vs_di(df_items: pd.DataFrame, df_subitems: pd.DataFrame, datos_facturas: dict) -> list:
    """Valida FOB de ítems del DI contra las facturas extraídas."""
    resultados = []

    for _, subrow in df_subitems.iterrows():
        item_num = str(subrow.get("ITEM", "")).strip().zfill(4)
        modelo_di = normalizar_codigo(subrow.get("MODELO", ""))
        fob_di = safe_float(subrow.get("MONTO FOB", 0))

        # Buscar en todas las facturas
        encontrado = False
        for nro_factura, fac_data in datos_facturas.items():
            if "error" in fac_data:
                continue
            for item_fac in fac_data.get("items", []):
                codigo_fac = normalizar_codigo(item_fac.get("codigo_parte", ""))
                if codigo_fac == modelo_di:
                    encontrado = True
                    tipo_cargos = fac_data.get("tipo_cargos", "por_item")
                    
                    if tipo_cargos == "por_item":
                        fob_esperado = safe_float(item_fac.get("subtotal", 0))
                    else:
                        # Prorrateo global
                        total_partes = safe_float(fac_data.get("total_partes", 0))
                        total_cargos = safe_float(fac_data.get("total_cargos", 0))
                        precio_parte = safe_float(item_fac.get("precio_total_parte", 0))
                        proporcion = precio_parte / total_partes if total_partes else 0
                        fob_esperado = round(precio_parte + (total_cargos * proporcion), 2)

                    if abs(fob_di - fob_esperado) > TOLERANCIA_FOB:
                        resultados.append(alerta(
                            item_num, "MONTO FOB (FACTURA)",
                            f"FOB DI: {fob_di:.2f} — FOB calculado desde factura: {fob_esperado:.2f} (dif: {abs(fob_di - fob_esperado):.2f})",
                            "ALERTA"
                        ))
                    else:
                        resultados.append(ok(item_num, "MONTO FOB (FACTURA)", f"FOB correcto vs factura: {fob_di:.2f}"))
                    break

        if not encontrado:
            resultados.append(alerta(
                item_num, "MONTO FOB (FACTURA)",
                f"No se encontró código '{modelo_di}' en ninguna factura subida",
                "ALERTA"
            ))

    return resultados


def validar_caratula_vs_docs(caratula: dict, datos_forwarding: dict, datos_bl: dict, datos_facturas: dict, config: dict) -> list:
    """Valida campos de la carátula contra documentos y defaults."""
    resultados = []

    def al(campo, msg, nivel="ALERTA"):
        return {"item": "CARÁTULA", "campo": campo, "mensaje": msg, "nivel": nivel}
    def ok_(campo, msg):
        return {"item": "CARÁTULA", "campo": campo, "mensaje": msg, "nivel": "OK"}

    # Empresa seleccionada
    empresa_config = config.get("empresa", "")
    cuit_config = config.get("cuit_ie", "")

    # Banco Argentina
    banco = _buscar_caratula(caratula, "I:BANCOSARGENTINA")
    if banco and banco != "016":
        resultados.append(al("I:BANCOSARGENTINA", f"Debe ser 016, tiene: '{banco}'", "ERROR"))
    elif banco:
        resultados.append(ok_("I:BANCOSARGENTINA", "Banco correcto: 016"))

    # IMPOGIRO
    impogiro = _buscar_caratula(caratula, "I:IMPOGIRO-DIV-OPC")
    if impogiro and impogiro != "CGDDIF":
        resultados.append(al("I:IMPOGIRO-DIV-OPC", f"Debe ser CGDDIF, tiene: '{impogiro}'", "ERROR"))

    # Forwarding: flete y seguro
    if datos_forwarding and "error" not in datos_forwarding:
        flete_doc = datos_forwarding.get("flete_total", 0)
        seguro_doc = datos_forwarding.get("seguro_total", 0)
        alertas_fw = datos_forwarding.get("alertas", [])

        flete_di = safe_float(_buscar_caratula(caratula, "FLETE") or 0)
        seguro_di = safe_float(_buscar_caratula(caratula, "SEGURO") or 0)

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

    # BL: número y fecha embarque
    if datos_bl and "error" not in datos_bl:
        bl_doc = datos_bl.get("bl_number", "").strip().upper()
        fecha_emb = datos_bl.get("fecha_embarque", "")
        itns_bl = datos_bl.get("itns", [])

        bl_di = _buscar_caratula(caratula, "DOCUMENTO") or ""
        if bl_di:
            if bl_doc and bl_doc not in bl_di.upper() and bl_di.upper() not in bl_doc:
                resultados.append(al("BL", f"BL en DI: '{bl_di}' — BL en documento: '{bl_doc}'", "ERROR"))
            else:
                resultados.append(ok_("BL", f"BL correcto: {bl_doc}"))

        # ITN
        itn_di = _buscar_caratula(caratula, "I:ITN-EEUU") or ""
        for itn in itns_bl:
            if itn.upper() not in itn_di.upper():
                resultados.append(al("I:ITN-EEUU", f"ITN del BL '{itn}' no figura en el DI"))

        # Fecha embarque
        femb_di = _buscar_caratula(caratula, "I:FEMB-ORIGEN") or ""
        if fecha_emb and femb_di:
            resultados.append(ok_("I:FEMB-ORIGEN", f"Fecha embarque: {fecha_emb}"))

    return resultados


def _buscar_caratula(caratula: dict, campo: str) -> str | None:
    campo_upper = campo.upper()
    for k, v in caratula.items():
        if campo_upper in k.upper():
            return str(v).strip()
    return None


def validar_dj_origen(df_items: pd.DataFrame, df_subitems: pd.DataFrame, datos_dj: list) -> list:
    """
    Cruza DJ de Origen No Preferencial contra ítems del DI.
    Por cada producto de la DJ busca el ítem del DI con mismo código de parte
    y valida: NCM 8 dígitos, últimos 3 SIM, país de origen, unidad, cantidad, CIF unitario.
    """
    from utils.parser_di import normalizar_codigo, safe_float
    TOLERANCIA_CIF = 0.10  # 10 centavos de tolerancia

    resultados = []

    # Mapeo de países DJ → códigos ORIGEN del DI
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

    def pais_coincide(pais_dj: str, origen_di: str) -> bool:
        pais_dj = pais_dj.upper().strip()
        origen_di = origen_di.upper().strip()
        for _, variantes in PAISES.items():
            if any(v in pais_dj for v in variantes):
                if any(v in origen_di for v in variantes):
                    return True
        # Fallback: texto directo
        return pais_dj in origen_di or origen_di in pais_dj

    def unidad_coincide(unidad_dj: str, unidad_di: str) -> bool:
        """UNIDAD → 07 - UNIDAD, KILOGRAMO → 01 - KILOGRAMO, etc."""
        ud = unidad_dj.upper().strip()
        udi = unidad_di.upper().strip()
        return ud in udi or udi.split("- ")[-1].strip() in ud

    # Extraer números IF de los PDFs subidos
    ifs_subidos = []
    for dj in datos_dj:
        if "error" not in dj and dj.get("numero_if"):
            ifs_subidos.append(dj["numero_if"].strip().upper())

    # ── Verificar que cada ítem con DJ tenga su PDF subido ──
    for _, row in df_items.iterrows():
        item = str(row.get("ITEM", "")).strip().zfill(4)
        dj_campo = row.get("D:DJ-ORIG-NOPREFER", "").strip()
        if not dj_campo:
            continue

        dj_upper = dj_campo.upper()
        coincide = any(dj_upper in if_sub or if_sub in dj_upper for if_sub in ifs_subidos)

        if not ifs_subidos or not coincide:
            msg = (f"DJ declarada '{dj_campo}' pero no se subió ningún PDF de DJ"
                   if not ifs_subidos else
                   f"DJ '{dj_campo}' no coincide con ningún PDF subido ({', '.join(ifs_subidos)})")
            resultados.append({"item": item, "campo": "D:DJ-ORIG-NOPREFER",
                                "mensaje": msg, "nivel": "ERROR"})

    # ── Cruce detallado por producto de la DJ ──
    for dj_data in datos_dj:
        if "error" in dj_data:
            continue
        numero_if = dj_data.get("numero_if", "")
        for prod in dj_data.get("productos", []):
            codigo_dj = prod["codigo_parte"].strip()
            ncm8_dj   = prod["ncm_8_digitos"].strip().replace(".", "")
            sim3_dj   = prod["ncm_sim_3"].strip()
            pais_dj   = prod["pais_origen"].strip()
            unidad_dj = prod["unidad_medida"].strip()
            qty_dj    = prod["cantidad"]
            cif_dj    = prod["valor_cif_unit"]

            # Buscar ítems del DI con este código de parte que tengan la DJ
            items_match = []
            for _, irow in df_items.iterrows():
                dj_campo = irow.get("D:DJ-ORIG-NOPREFER", "").strip()
                if not dj_campo or numero_if.upper() not in dj_campo.upper():
                    continue
                item_num = str(irow.get("ITEM", "")).strip().zfill(4)
                sub = df_subitems[df_subitems["ITEM"].str.zfill(4) == item_num]
                for _, srow in sub.iterrows():
                    modelo_di = normalizar_codigo(str(srow.get("MODELO", "")))
                    codigo_dj_norm = normalizar_codigo(codigo_dj)
                    if modelo_di == codigo_dj_norm:
                        # Matchear también por cantidad para evitar cruces erróneos
                        # cuando el mismo código aparece en varios ítems
                        qty_item = safe_float(irow.get("CANTIDAD", 0))
                        if qty_item == qty_dj:
                            items_match.append((item_num, irow, srow))

            if not items_match:
                resultados.append({
                    "item": "GENERAL", "campo": "DJ-ORIG",
                    "mensaje": f"[DJ {numero_if}] Código '{codigo_dj}' no encontrado en ningún ítem del DI con esta DJ",
                    "nivel": "ERROR"
                })
                continue

            for item_num, irow, srow in items_match:
                # ── NCM 8 dígitos ──
                ncm_di = str(srow.get("NCM", "")).replace(".", "").strip()
                ncm8_di = ncm_di[:8]
                if ncm8_di != ncm8_dj:
                    resultados.append({"item": item_num, "campo": "DJ NCM 8D",
                        "mensaje": f"NCM DJ ({ncm8_dj}) ≠ DI ({ncm8_di})", "nivel": "ERROR"})
                else:
                    resultados.append({"item": item_num, "campo": "DJ NCM 8D",
                        "mensaje": f"NCM 8 dígitos OK: {ncm8_dj}", "nivel": "OK"})

                # ── Últimos 3 dígitos SIM ──
                sim3_di = ncm_di[8:] if len(ncm_di) > 8 else ""
                # Formato DI: "8414902 0120Z" → últimos chars después del NCM base
                # Tomamos los últimos 4 chars del NCM completo del DI
                ncm_full_di = str(srow.get("NCM", "")).replace(".", "").strip()
                sim3_di_ext = ncm_full_di[-4:] if len(ncm_full_di) >= 4 else ncm_full_di
                if sim3_dj.upper() not in sim3_di_ext.upper() and sim3_di_ext.upper() not in sim3_dj.upper():
                    resultados.append({"item": item_num, "campo": "DJ SIM 3D",
                        "mensaje": f"Últimos 3 SIM DJ ({sim3_dj}) ≠ DI ({sim3_di_ext})", "nivel": "ALERTA"})
                else:
                    resultados.append({"item": item_num, "campo": "DJ SIM 3D",
                        "mensaje": f"SIM 3 dígitos OK: {sim3_dj}", "nivel": "OK"})

                # ── País de origen ──
                origen_di = str(irow.get("ORIGEN", "")).strip()
                if not pais_coincide(pais_dj, origen_di):
                    resultados.append({"item": item_num, "campo": "DJ PAÍS ORIGEN",
                        "mensaje": f"País DJ ({pais_dj}) ≠ DI ({origen_di})", "nivel": "ERROR"})
                else:
                    resultados.append({"item": item_num, "campo": "DJ PAÍS ORIGEN",
                        "mensaje": f"País origen OK: {pais_dj}", "nivel": "OK"})

                # ── Unidad de medida ──
                unidad_di = str(srow.get("UNIDAD DECLARADA", "")).strip()
                if not unidad_coincide(unidad_dj, unidad_di):
                    resultados.append({"item": item_num, "campo": "DJ UNIDAD",
                        "mensaje": f"Unidad DJ ({unidad_dj}) ≠ DI ({unidad_di})", "nivel": "ALERTA"})
                else:
                    resultados.append({"item": item_num, "campo": "DJ UNIDAD",
                        "mensaje": f"Unidad OK: {unidad_dj}", "nivel": "OK"})

                # ── Cantidad ──
                qty_di = safe_float(irow.get("CANTIDAD", 0))
                if qty_di != qty_dj:
                    resultados.append({"item": item_num, "campo": "DJ CANTIDAD",
                        "mensaje": f"Cantidad DJ ({qty_dj:.0f}) ≠ DI ({qty_di:.0f})", "nivel": "ERROR"})
                else:
                    resultados.append({"item": item_num, "campo": "DJ CANTIDAD",
                        "mensaje": f"Cantidad OK: {qty_dj:.0f}", "nivel": "OK"})

                # ── Valor CIF unitario ──
                fob = safe_float(irow.get("VALOR FOB", 0))
                flete = safe_float(irow.get("FLETE EN DIV", 0))
                seguro = safe_float(irow.get("SEGURO EN DIV", 0))
                qty_di2 = safe_float(irow.get("CANTIDAD", 1)) or 1
                cif_di = round((fob + flete + seguro) / qty_di2, 2)
                diff = abs(cif_di - cif_dj)
                if diff > TOLERANCIA_CIF:
                    resultados.append({"item": item_num, "campo": "DJ CIF UNIT",
                        "mensaje": f"CIF unitario DJ ({cif_dj:.2f}) ≠ DI ({cif_di:.2f}) | diff: {diff:.2f}",
                        "nivel": "ERROR"})
                else:
                    resultados.append({"item": item_num, "campo": "DJ CIF UNIT",
                        "mensaje": f"CIF unitario OK: {cif_dj:.2f}", "nivel": "OK"})

    return resultados
