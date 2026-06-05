import pandas as pd
from utils.parser_di import normalizar_codigo, safe_float
from config.defaults import TOLERANCIA_FOB

CAMPOS_DUMPING_DJ = ["I:DUMPR60DECJUR", "I:DUMPR60PAISMAYOR", "I:DUMPADVALPAISTXT"]

def alerta(item, campo, mensaje, nivel="ALERTA"):
    return {"item": item, "campo": campo, "mensaje": mensaje, "nivel": nivel}

def ok(item, campo, mensaje):
    return {"item": item, "campo": campo, "mensaje": mensaje, "nivel": "OK"}


# ─── CM vs DI ─────────────────────────────────────────────────────────────────

def validar_cm_vs_di(df_items: pd.DataFrame, df_subitems: pd.DataFrame, datos_cm: dict) -> list:
    resultados = []

    grupos_cm = {}
    for _, row in df_items.iterrows():
        item = str(row.get("ITEM", "")).strip().zfill(4)
        cm = row.get("D:CERTSM", "").strip()
        if cm:
            if cm not in grupos_cm:
                grupos_cm[cm] = []
            grupos_cm[cm].append(item)

    for numero_cm, items_del_cm in grupos_cm.items():
        if numero_cm not in datos_cm:
            resultados.append(alerta(", ".join(items_del_cm), "CM",
                f"No se encontró PDF del CM: {numero_cm}", "ALERTA"))
            continue

        cm_data = datos_cm[numero_cm]
        if "error" in cm_data:
            resultados.append(alerta(", ".join(items_del_cm), "CM",
                f"Error al leer CM {numero_cm}: {cm_data['error']}", "ERROR"))
            continue

        items_cm = cm_data.get("items", [])

        for item_num in items_del_cm:
            sub = df_subitems[df_subitems["ITEM"].str.zfill(4) == item_num]
            if sub.empty:
                continue

            for _, subrow in sub.iterrows():
                modelo_di = normalizar_codigo(subrow.get("MODELO", ""))
                ncm_di_raw = subrow.get("NCM", "").replace(".", "").strip()
                ncm_di_8 = ncm_di_raw[:8] if len(ncm_di_raw) >= 8 else ncm_di_raw
                cantidad_di = safe_float(subrow.get("CANTIDAD", 0))
                fob_di = safe_float(subrow.get("MONTO FOB", 0))

                item_cm = None
                for ic in items_cm:
                    if normalizar_codigo(ic.get("codigo_parte", "")) == modelo_di:
                        item_cm = ic
                        break
                if not item_cm:
                    for ic in items_cm:
                        if ic.get("ncm_8_digitos", "").replace(".", "")[:8] == ncm_di_8:
                            item_cm = ic
                            break

                if not item_cm:
                    resultados.append(alerta(item_num, "CM",
                        f"No se encontró código '{modelo_di}' ni NCM '{ncm_di_8}' en el CM", "ERROR"))
                    continue

                # NCM
                ncm_cm_8 = item_cm.get("ncm_8_digitos", "").replace(".", "")[:8]
                if ncm_di_8 != ncm_cm_8:
                    resultados.append(alerta(item_num, "NCM", f"DI: {ncm_di_8} — CM: {ncm_cm_8}", "ERROR"))
                else:
                    resultados.append(ok(item_num, "NCM", f"NCM correcto: {ncm_di_8}"))

                # Código de parte
                codigo_cm = normalizar_codigo(item_cm.get("codigo_parte", ""))
                if modelo_di != codigo_cm:
                    resultados.append(alerta(item_num, "MODELO", f"DI: '{modelo_di}' — CM: '{codigo_cm}'", "ERROR"))
                else:
                    resultados.append(ok(item_num, "MODELO", f"Código correcto: {modelo_di}"))

                # Cantidad
                cantidad_cm = safe_float(item_cm.get("cantidad", 0))
                if cantidad_di > cantidad_cm:
                    resultados.append(alerta(item_num, "CANTIDAD",
                        f"DI ({cantidad_di}) supera habilitado en CM ({cantidad_cm})", "ERROR"))
                else:
                    resultados.append(ok(item_num, "CANTIDAD", f"Cantidad OK: {cantidad_di} ≤ {cantidad_cm}"))

                # FOB
                fob_cm = safe_float(item_cm.get("valor_total_fob", 0))
                if abs(fob_di - fob_cm) > TOLERANCIA_FOB:
                    resultados.append(alerta(item_num, "MONTO FOB",
                        f"DI: {fob_di:.2f} — CM: {fob_cm:.2f} (dif: {abs(fob_di-fob_cm):.2f})", "ALERTA"))
                else:
                    resultados.append(ok(item_num, "MONTO FOB", f"FOB correcto: {fob_di:.2f}"))

    return resultados


# ─── FACTURA vs DI ────────────────────────────────────────────────────────────

def validar_factura_vs_di(df_items: pd.DataFrame, df_subitems: pd.DataFrame, datos_facturas: dict) -> list:
    resultados = []

    for _, subrow in df_subitems.iterrows():
        item_num = str(subrow.get("ITEM", "")).strip().zfill(4)
        modelo_di = normalizar_codigo(subrow.get("MODELO", ""))
        fob_di = safe_float(subrow.get("MONTO FOB", 0))

        encontrado = False
        for nro_factura, fac_data in datos_facturas.items():
            if "error" in fac_data:
                continue
            for item_fac in fac_data.get("items", []):
                if normalizar_codigo(item_fac.get("codigo_parte", "")) == modelo_di:
                    encontrado = True
                    tipo_cargos = fac_data.get("tipo_cargos", "por_item")
                    if tipo_cargos == "por_item":
                        fob_esperado = safe_float(item_fac.get("subtotal", 0))
                    else:
                        total_partes = safe_float(fac_data.get("total_partes", 0))
                        total_cargos = safe_float(fac_data.get("total_cargos", 0))
                        precio_parte = safe_float(item_fac.get("precio_total_parte", 0))
                        proporcion = precio_parte / total_partes if total_partes else 0
                        fob_esperado = round(precio_parte + (total_cargos * proporcion), 2)

                    if abs(fob_di - fob_esperado) > TOLERANCIA_FOB:
                        resultados.append(alerta(item_num, "FOB vs FACTURA",
                            f"DI: {fob_di:.2f} — Factura: {fob_esperado:.2f} (dif: {abs(fob_di-fob_esperado):.2f})"))
                    else:
                        resultados.append(ok(item_num, "FOB vs FACTURA", f"FOB correcto: {fob_di:.2f}"))
                    break

        if not encontrado:
            resultados.append(alerta(item_num, "FOB vs FACTURA",
                f"Código '{modelo_di}' no encontrado en ninguna factura subida"))

    return resultados


# ─── CARÁTULA vs DOCS ─────────────────────────────────────────────────────────

def validar_caratula_vs_docs(caratula: dict, datos_forwarding: dict, datos_bl: dict, datos_facturas: dict, config: dict) -> list:
    resultados = []

    def al(campo, msg, nivel="ALERTA"):
        return {"item": "CARÁTULA", "campo": campo, "mensaje": msg, "nivel": nivel}
    def ok_(campo, msg):
        return {"item": "CARÁTULA", "campo": campo, "mensaje": msg, "nivel": "OK"}

    banco = _buscar(caratula, "I:BANCOSARGENTINA")
    if banco and banco != "016":
        resultados.append(al("I:BANCOSARGENTINA", f"Debe ser 016, tiene: '{banco}'", "ERROR"))

    if datos_forwarding and "error" not in datos_forwarding:
        flete_doc = datos_forwarding.get("flete_total", 0)
        seguro_doc = datos_forwarding.get("seguro_total", 0)
        alertas_fw = datos_forwarding.get("alertas", [])

        flete_di = safe_float(_buscar(caratula, "FLETE") or 0)
        seguro_di = safe_float(_buscar(caratula, "SEGURO") or 0)

        if abs(flete_di - flete_doc) > TOLERANCIA_FOB:
            resultados.append(al("FLETE", f"DI: {flete_di:.2f} — Forwarding: {flete_doc:.2f}", "ERROR"))
        if abs(seguro_di - seguro_doc) > TOLERANCIA_FOB:
            resultados.append(al("SEGURO", f"DI: {seguro_di:.2f} — Forwarding: {seguro_doc:.2f}", "ERROR"))

        for a in alertas_fw:
            resultados.append(al("FORWARDING", f"Cargo adicional: {a}"))

    if datos_bl and "error" not in datos_bl:
        bl_doc = datos_bl.get("bl_number", "").strip().upper()
        itns_bl = datos_bl.get("itns", [])
        itn_di = _buscar(caratula, "I:ITN-EEUU") or ""

        for itn in itns_bl:
            if itn.upper() not in itn_di.upper():
                resultados.append(al("I:ITN-EEUU", f"ITN '{itn}' del BL no figura en el DI"))

        femb = datos_bl.get("fecha_embarque", "")


    return resultados


# ─── DJ ORIGEN ────────────────────────────────────────────────────────────────

def validar_dj_origen(df_items: pd.DataFrame, df_subitems: pd.DataFrame, datos_dj: list) -> list:
    resultados = []

    # Números IF disponibles en los PDFs subidos
    ifs_subidos = []
    items_dj = {}  # numero_if → lista de ítems del PDF
    for dj in datos_dj:
        if "error" not in dj and dj.get("numero_if"):
            nif = dj["numero_if"].strip().upper()
            ifs_subidos.append(nif)
            items_dj[nif] = dj.get("items", [])

    for _, row in df_items.iterrows():
        item = str(row.get("ITEM", "")).strip().zfill(4)
        dj_campo = row.get("D:DJ-ORIG-NOPREFER", "").strip()

        # Solo validar si el campo tiene info
        if not dj_campo:
            continue

        # Verificar que el PDF esté subido
        if not ifs_subidos:
            resultados.append(alerta(item, "D:DJ-ORIG-NOPREFER",
                f"DJ '{dj_campo}' declarada pero no se subió ningún PDF de DJ de origen", "ERROR"))
            continue

        dj_upper = dj_campo.upper()
        nif_match = next((n for n in ifs_subidos if dj_upper in n or n in dj_upper), None)

        if not nif_match:
            resultados.append(alerta(item, "D:DJ-ORIG-NOPREFER",
                f"DJ '{dj_campo}' no coincide con PDFs subidos ({', '.join(ifs_subidos)})", "ERROR"))
            continue

        resultados.append(ok(item, "D:DJ-ORIG-NOPREFER", f"DJ encontrada: {nif_match}"))

        # Cruzar campos del ítem contra la DJ
        items_en_dj = items_dj.get(nif_match, [])
        if not items_en_dj:
            resultados.append(alerta(item, "DJ - CONTENIDO",
                "No se pudieron extraer ítems del PDF de la DJ"))
            continue

        # Obtener datos del subitem del DI
        sub = df_subitems[df_subitems["ITEM"].str.zfill(4) == item]
        if sub.empty:
            continue

        for _, subrow in sub.iterrows():
            modelo_di = normalizar_codigo(subrow.get("MODELO", ""))
            ncm_di_raw = subrow.get("NCM", "").replace(".", "").strip()
            ncm_di_8 = ncm_di_raw[:8]
            cantidad_di = safe_float(subrow.get("CANTIDAD", 0))

            # Buscar en la DJ por código de material o NCM
            item_dj = None
            for idj in items_en_dj:
                if normalizar_codigo(idj.get("codigo_material", "")) == modelo_di:
                    item_dj = idj
                    break
            if not item_dj:
                for idj in items_en_dj:
                    if idj.get("ncm_8_digitos", "")[:8] == ncm_di_8:
                        item_dj = idj
                        break

            if not item_dj:
                resultados.append(alerta(item, "DJ - CÓDIGO",
                    f"Código '{modelo_di}' no encontrado en la DJ", "ERROR"))
                continue

            # NCM
            ncm_dj_8 = item_dj.get("ncm_8_digitos", "")[:8]
            if ncm_di_8 != ncm_dj_8:
                resultados.append(alerta(item, "DJ - NCM",
                    f"DI: {ncm_di_8} — DJ: {ncm_dj_8}", "ERROR"))
            else:
                resultados.append(ok(item, "DJ - NCM", f"NCM OK: {ncm_di_8}"))

            # Origen
            origen_di = row.get("ORIGEN", "").strip().upper()
            origen_dj = item_dj.get("origen", "").strip().upper()
            if origen_dj and origen_di and origen_dj not in origen_di and origen_di not in origen_dj:
                resultados.append(alerta(item, "DJ - ORIGEN",
                    f"DI: '{origen_di}' — DJ: '{origen_dj}'", "ERROR"))
            elif origen_dj:
                resultados.append(ok(item, "DJ - ORIGEN", f"Origen OK: {origen_dj}"))

            # Cantidad
            cantidad_dj = safe_float(item_dj.get("cantidad", 0))
            if cantidad_dj and abs(cantidad_di - cantidad_dj) > TOLERANCIA_FOB:
                resultados.append(alerta(item, "DJ - CANTIDAD",
                    f"DI: {cantidad_di} — DJ: {cantidad_dj}", "ALERTA"))

            # Valor CIF
            valor_cif_dj = safe_float(item_dj.get("valor_cif", 0))
            if valor_cif_dj:
                resultados.append(alerta(item, "DJ - VALOR CIF",
                    f"Valor CIF en DJ: {valor_cif_dj:.2f} — verificar contra base imponible del ítem"))

    return resultados


def _buscar(caratula: dict, campo: str) -> str | None:
    campo_upper = campo.upper()
    for k, v in caratula.items():
        if campo_upper in k.upper():
            return str(v).strip()
    return None
