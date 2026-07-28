import re
import pandas as pd
from utils.parser_di import normalizar_codigo, safe_float
from config.defaults import TOLERANCIA_FOB, DESPACHANTE, CUIT_DESPACHANTE


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

    Códigos puramente numéricos: cuando Excel interpreta la columna como
    número (no texto), pierde los ceros de relleno a la izquierda — ej.
    '0054173' queda guardado como '54173' (o '54173.0' si además le
    agrega decimales). El código completo de CAT siempre tiene 7 dígitos
    cuando es numérico puro, así que se reconstruye rellenando con ceros
    hasta esa longitud antes de comparar. Los códigos alfanuméricos
    (ej. '0S0509') no se tocan, porque Excel no puede convertirlos a
    número y por lo tanto no pierden el cero.
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

    codigos = set()
    for val in df_clasi[col].astype(str).str.strip().str.upper():
        # Quitar el ".0" que pandas agrega si Excel guardó la celda como
        # número de punto flotante (ej. "54173.0" -> "54173").
        v = re.sub(r"\.0$", "", val)
        if v.isdigit() and len(v) < 7:
            v = v.zfill(7)
        codigos.add(v)
    return codigos


def _validar_codigo_en_clasificacion(codigo: str, codigos_clasi: set, item_num: str, nro_factura: str) -> list:
    """
    Segunda validación: verifica que el código extraído de la factura
    exista en el Excel de clasificaciones subido.
    Retorna lista de resultados (ok o alerta).
    """
    if not codigos_clasi:
        return []  # sin clasificación cargada, no validar
    if codigo in codigos_clasi:
        return [ok(item_num, "CÓDIGO EN CLASIFICACIÓN (EXCEL)",
                   f"Código '{codigo}' verificado en clasificación | Factura: {nro_factura}")]
    else:
        return [alerta(item_num, "CÓDIGO EN CLASIFICACIÓN (EXCEL)",
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
        factura_cm = cm_data.get("numero_factura", "").strip()
        suf_fac = f" | Factura: {factura_cm}" if factura_cm else ""

        for item_num in items_del_cm:
            sub = df_subitems[df_subitems["ITEM"].str.zfill(4) == item_num]
            if sub.empty:
                resultados.append(alerta(item_num, "SUBITEM",
                    f"[CM: {numero_cm}] No se encontró subitem en el DI para ítem {item_num}"))
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
                    resultados.append(alerta(item_num, "NCM (CM)",
                        f"[CM: {numero_cm}] Código: {modelo_di} | NCM DI: {ncm_di_8} — NCM CM: {ncm_cm_8}{suf_fac}", "ERROR"))
                else:
                    resultados.append(ok(item_num, "NCM (CM)", f"Código: {modelo_di} | NCM correcto: {ncm_di_8}{suf_fac}"))

                codigo_cm = normalizar_codigo(item_cm.get("codigo_parte", ""))
                if modelo_di != codigo_cm:
                    resultados.append(alerta(item_num, "MODELO (CM)",
                        f"[CM: {numero_cm}] Código DI: '{modelo_di}' — Código CM: '{codigo_cm}'{suf_fac}",
                        "ERROR"))
                else:
                    resultados.append(ok(item_num, "MODELO (CM)", f"Código de parte correcto: {modelo_di}{suf_fac}"))

                cantidad_cm = safe_float(item_cm.get("cantidad", 0))
                if cantidad_di > cantidad_cm:
                    resultados.append(alerta(item_num, "CANTIDAD (CM)",
                        f"[CM: {numero_cm}] Código: {modelo_di} | Cantidad DI ({cantidad_di}) supera habilitado en CM ({cantidad_cm}){suf_fac}",
                        "ERROR"))
                elif abs(cantidad_di - cantidad_cm) > 0.01:
                    resultados.append(alerta(item_num, "CANTIDAD (CM)",
                        f"[CM: {numero_cm}] Código: {modelo_di} | Cantidad DI ({cantidad_di}) distinta a la habilitada en CM ({cantidad_cm}) — usa solo una parte del cupo, verificar si es intencional{suf_fac}",
                        "ALERTA"))
                else:
                    resultados.append(ok(item_num, "CANTIDAD (CM)", f"Código: {modelo_di} | Cantidad OK: {cantidad_di} = {cantidad_cm}{suf_fac}"))

                fob_cm = safe_float(item_cm.get("valor_total_fob", 0))
                if round(fob_di, 2) != round(fob_cm, 2):
                    resultados.append(alerta(item_num, "MONTO FOB (CM)",
                        f"[CM: {numero_cm}] Código: {item_cm.get('codigo_parte','')} | "
                        f"FOB DI: {fob_di:.2f} — CM: {fob_cm:.2f} (dif: {abs(fob_di - fob_cm):.2f}){suf_fac}",
                        "ALERTA"))
                else:
                    resultados.append(ok(item_num, "MONTO FOB (CM)", f"FOB correcto: {fob_di:.2f}{suf_fac}"))

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

        if not item_num or item_num == "0000":
            continue

        if not modelo_di:
            # Ítem sin código de modelo declarado en el DI — antes se
            # saltaba en silencio (sin generar ninguna fila), por lo que
            # el resumen general contaba estos casos como "no
            # comparable(s)" y mandaba a la pestaña Alertas, pero ahí no
            # había nada que mostrar. Ahora queda explícito, con el FOB
            # y cantidad declarados en el DI como referencia.
            resultados.append(alerta(
                item_num, "CÓDIGO (FACTURA)",
                f"Ítem sin código de modelo declarado en el DI (solapa Subitems) — no se pudo comparar contra factura "
                f"| FOB DI: {fob_di:.2f} | Cantidad DI: {cantidad_di:.0f}",
                "ALERTA"
            ))
            continue

        # Reunir TODOS los candidatos de TODAS las facturas que matcheen
        # código + cantidad, sin cortar en el primero. Si el mismo código
        # aparece en dos facturas con distinto FOB, desempatamos eligiendo
        # el candidato cuyo FOB sea más cercano al declarado en la DI.
        # IMPORTANTE: no llamar setdefault acá — no marcar nada como usado
        # hasta que se elija el candidato final.
        candidatos = []  # lista de (nombre_archivo, fac_data, item_factura)
        for nombre_archivo, fac_data in datos_facturas.items():
            if "error" in fac_data:
                continue
            items_factura = fac_data.get("items", [])
            usados = usados_por_factura.get(nombre_archivo, set())
            # Match por código + cantidad exacta
            for i in items_factura:
                if (id(i) not in usados
                        and normalizar_codigo(i.get("codigo_parte", "")) == modelo_di
                        and abs(safe_float(i.get("cantidad", 0)) - cantidad_di) < 0.01):
                    candidatos.append((nombre_archivo, fac_data, i))
        # Fallback: solo código si no hubo match cantidad
        if not candidatos:
            for nombre_archivo, fac_data in datos_facturas.items():
                if "error" in fac_data:
                    continue
                items_factura = fac_data.get("items", [])
                usados = usados_por_factura.get(nombre_archivo, set())
                for i in items_factura:
                    if (id(i) not in usados
                            and normalizar_codigo(i.get("codigo_parte", "")) == modelo_di):
                        candidatos.append((nombre_archivo, fac_data, i))

        encontrado = False
        if candidatos:
            # Desempate: elegir el candidato con FOB más cercano al DI
            def _fob_candidato(cand):
                _narch, _fdata, _ifac = cand
                tipo = _fdata.get("tipo_cargos", "por_item")
                if tipo == "por_item":
                    return safe_float(_ifac.get("subtotal", 0))
                _tp = safe_float(_fdata.get("total_partes", 0))
                _tc = safe_float(_fdata.get("total_cargos", 0))
                _pp = safe_float(_ifac.get("precio_total_parte", 0))
                _prop = _pp / _tp if _tp else 0
                return round(_pp + (_tc * _prop), 2)

            nombre_archivo, fac_data, match_fac = min(
                candidatos, key=lambda c: abs(_fob_candidato(c) - fob_di)
            )
            nro_factura = fac_data.get("numero_factura", "").strip() or nombre_archivo
            # Marcar el candidato elegido como usado para no reasignarlo
            usados_por_factura.setdefault(nombre_archivo, set()).add(id(match_fac))
            encontrado = True
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
            codigo_ref_norm = normalizar_codigo(codigo_ref)

            if codigo_ref_norm != modelo_di:
                resultados.append(alerta(
                    item_num, "CÓDIGO (FACTURA)",
                    f"Código DI: '{modelo_di}' — Código factura: '{codigo_ref}' | Factura: {nro_factura}",
                    "ALERTA"
                ))
            else:
                resultados.append(ok(
                    item_num, "CÓDIGO (FACTURA)",
                    f"Código DI: {modelo_di} — Código factura: {codigo_ref} | Factura: {nro_factura}"
                ))

            if abs(fob_di - fob_esperado) > TOLERANCIA_FOB:
                fila_diff_fob = alerta(
                    item_num, "MONTO FOB (FACTURA)",
                    f"FOB DI: {fob_di:.2f} — FOB factura: {fob_esperado:.2f} "
                    f"(dif: {abs(fob_di - fob_esperado):.2f}) | "
                    f"Código: {codigo_ref} | Factura: {nro_factura}",
                    "ERROR"
                )
                # DI - factura (con signo): usado por validar_caratula_totales para reconciliar el total
                fila_diff_fob["dif_fob"] = round(fob_di - fob_esperado, 2)
                resultados.append(fila_diff_fob)
            else:
                resultados.append(ok(
                    item_num, "MONTO FOB (FACTURA)",
                    f"FOB correcto vs factura: {fob_di:.2f} | "
                    f"Código: {codigo_ref} | Factura: {nro_factura}"
                ))

            resultados.extend(
                _validar_codigo_en_clasificacion(modelo_di, codigos_clasi, item_num, nro_factura)
            )

        if not encontrado:
            fila_sin_match = alerta(
                item_num, "CÓDIGO (FACTURA)",
                f"No se encontró código '{modelo_di}' en ninguna factura subida | FOB DI: {fob_di:.2f}",
                "ALERTA"
            )
            fila_sin_match["fob_di"] = fob_di  # usado por validar_caratula_totales para reconciliar el total
            resultados.append(fila_sin_match)
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

def validar_caratula_totales(caratula: dict, datos_facturas: dict, datos_forwarding: dict = None,
                              resultados_factura_vs_di: list = None) -> list:
    """
    Valida, contra la carátula del DI:
      - FOB total: suma de total_factura de todas las facturas subidas.
      - Moneda (FOB/Flete/Seguro): coincide con la moneda real de cada
        documento de origen (factura para FOB, forwarding para Flete/Seguro).
      - Incoterm: coincide entre todas las facturas y contra el INCOTERM
        declarado en la carátula. Si una factura difiere de otra, o de
        la carátula, se alerta.
    No requiere llamadas a la API — todo proviene de datos ya extraídos.

    `resultados_factura_vs_di`, si se pasa, es la lista ya generada por
    validar_factura_vs_di() para los mismos datos — se reutiliza solo
    para enriquecer el mensaje de FOB con los ítems específicos que ya
    tienen diferencia detectada (no se recalcula nada).
    """
    resultados = []

    def al(campo, msg, nivel="ALERTA"):
        return {"item": "GENERAL", "campo": campo, "mensaje": msg, "nivel": nivel}
    def ok_(campo, msg):
        return {"item": "GENERAL", "campo": campo, "mensaje": msg, "nivel": "OK"}

    facturas_validas = {k: v for k, v in (datos_facturas or {}).items() if "error" not in v}

    # ── FOB total: suma de facturas vs carátula ──
    if facturas_validas:
        fob_total_facturas = round(sum(safe_float(f.get("total_factura", 0)) for f in facturas_validas.values()), 2)
        fob_di = safe_float(_buscar_caratula(caratula, "FOB") or 0)

        if round(fob_di, 2) != fob_total_facturas:
            # Reconciliar la diferencia del total contra las dos causas que
            # ya detecta validar_factura_vs_di(), en vez de dejar solo el
            # número agregado sin explicación:
            #   1) Ítems que SÍ matchearon contra una factura pero con FOB
            #      distinto → aportan (FOB DI - FOB factura) cada uno.
            #   2) Ítems del DI que no encontraron ninguna factura que los
            #      respalde → aportan su FOB DI completo (típicamente porque
            #      no se subieron todas las facturas de la operación).
            filas_diff = [
                r for r in (resultados_factura_vs_di or [])
                if r.get("campo") == "MONTO FOB (FACTURA)" and r.get("nivel") != "OK"
                and "dif_fob" in r
            ]
            filas_sin_factura = [
                r for r in (resultados_factura_vs_di or [])
                if r.get("campo") == "CÓDIGO (FACTURA)"
                and "No se encontró código" in str(r.get("mensaje", ""))
                and "fob_di" in r
            ]

            aporte_diff = round(sum(r["dif_fob"] for r in filas_diff), 2)
            aporte_sin_factura = round(sum(r["fob_di"] for r in filas_sin_factura), 2)
            aporte_total = round(aporte_diff + aporte_sin_factura, 2)
            diferencia_total = round(fob_di - fob_total_facturas, 2)

            partes_sufijo = []
            if filas_diff:
                items_con_diff = sorted({str(r.get("item", "")) for r in filas_diff})
                partes_sufijo.append(
                    f"Ítems con diferencia de FOB vs factura (aportan {aporte_diff:.2f}): "
                    f"{', '.join(items_con_diff)}"
                )
            if filas_sin_factura:
                items_sin_factura = sorted({str(r.get("item", "")) for r in filas_sin_factura})
                MAX_MOSTRAR = 15
                listado = ', '.join(items_sin_factura[:MAX_MOSTRAR])
                if len(items_sin_factura) > MAX_MOSTRAR:
                    listado += f", … (+{len(items_sin_factura) - MAX_MOSTRAR} más)"
                partes_sufijo.append(
                    f"{len(items_sin_factura)} ítem(s) del DI sin ninguna factura que los respalde, "
                    f"aportan {aporte_sin_factura:.2f} (revisar si faltan facturas por subir): {listado}"
                )

            # Si lo identificado explica la diferencia total (con margen de
            # redondeo), lo dejamos explícito; si no, avisamos que queda un
            # resto sin explicar por estas dos causas (puede haber otro motivo,
            # p.ej. facturas de más, o un cargo no contemplado).
            resto = round(diferencia_total - aporte_total, 2)
            if partes_sufijo:
                if abs(resto) <= TOLERANCIA_FOB:
                    partes_sufijo.append("→ esto explica la diferencia total")
                else:
                    partes_sufijo.append(f"→ resto sin explicar por estas causas: {resto:.2f}")

            sufijo_items = f" | {' | '.join(partes_sufijo)}" if partes_sufijo else ""
            resultados.append(al("FOB", f"DI: {fob_di:.2f} — Suma de facturas: {fob_total_facturas:.2f} "
                                          f"(dif: {abs(diferencia_total):.2f}){sufijo_items}", "ERROR"))
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
        return {"item": "GENERAL", "campo": campo, "mensaje": msg, "nivel": nivel}
    def ok_(campo, msg):
        return {"item": "GENERAL", "campo": campo, "mensaje": msg, "nivel": "OK"}

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

        if round(flete_di, 2) != round(safe_float(flete_doc), 2):
            resultados.append(al("FLETE", f"DI: {flete_di:.2f} — Forwarding: {flete_doc:.2f}", "ERROR"))
        else:
            resultados.append(ok_("FLETE", f"Flete correcto: {flete_di:.2f}"))

        if round(seguro_di, 2) != round(safe_float(seguro_doc), 2):
            resultados.append(al("SEGURO", f"DI: {seguro_di:.2f} — Forwarding: {seguro_doc:.2f}", "ERROR"))
        else:
            resultados.append(ok_("SEGURO", f"Seguro correcto: {seguro_di:.2f}"))

        for a in alertas_fw:
            resultados.append(al("FORWARDING", f"Cargo adicional detectado: {a}", "ALERTA"))

    if datos_bl and "error" not in datos_bl:
        itns_bl  = datos_bl.get("itns", [])

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


# ── Validación Bultos vs BL ───────────────────────────────────────────────────

def validar_bultos_vs_bl(df_bultos: pd.DataFrame, datos_bl: dict) -> list:
    """
    Valida la solapa Bultos del DI (DOCUMENTO, EMBALAJE, TIPO EMBALAJE,
    CANTIDAD, PESO BRUTO) contra los totales extraídos del BL.

    Reglas:
      - DOCUMENTO: el número de BL declarado en la solapa Bultos del DI
        debe coincidir con el bl_number extraído del PDF subido.
      - Filas donde EMBALAJE contiene "CONTENEDOR": se suma su CANTIDAD y se
        compara contra cantidad_contenedores del BL.
      - Filas donde EMBALAJE NO contiene "CONTENEDOR": se suma su CANTIDAD y
        se compara contra cantidad_bultos del BL.
      - PESO BRUTO: se suma de TODAS las filas (sin filtrar) y se compara
        contra peso_bruto_kg del BL.
    Comparación exacta, sin tolerancia (a pedido del usuario).
    No es una validación por ítem del DI, sino de totales del despacho —
    se reporta a nivel "GENERAL", campo "BULTOS" (agrupado junto al resto
    de chequeos globales del despacho).
    """
    resultados = []
    CAMPO = "BULTOS"

    def al(msg, nivel="ERROR"):
        return {"item": "GENERAL", "campo": CAMPO, "mensaje": msg, "nivel": nivel}
    def ok_(msg):
        return {"item": "GENERAL", "campo": CAMPO, "mensaje": msg, "nivel": "OK"}

    if df_bultos is None or df_bultos.empty:
        resultados.append(al("No se encontró la solapa Bultos en el DI — no se pudo validar", "ALERTA"))
        return resultados

    if not datos_bl or "error" in datos_bl:
        resultados.append(al("No se pudo extraer el BL — no se pudo validar bultos/peso", "ALERTA"))
        return resultados

    # ── Número de BL (DOCUMENTO) ──
    bl_bl = str(datos_bl.get("bl_number", "")).strip().upper()
    documentos_di = set()
    if "DOCUMENTO" in df_bultos.columns:
        documentos_di = {str(d).strip().upper() for d in df_bultos["DOCUMENTO"] if str(d).strip()}

    if documentos_di or bl_bl:
        # Coincidencia flexible (substring en cualquier dirección) para
        # tolerar formatos con/sin prefijo de naviera.
        coincide = any(bl_bl and (bl_bl in doc or doc in bl_bl) for doc in documentos_di)
        if documentos_di and bl_bl and not coincide:
            documentos_str = ", ".join(sorted(documentos_di))
            resultados.append(al(f"BL declarado en DI: '{documentos_str}' — BL en documento subido: '{bl_bl}'"))
        elif documentos_di and bl_bl and coincide:
            resultados.append(ok_(f"BL declarado coincide con el subido: {bl_bl}"))

    es_contenedor = df_bultos["EMBALAJE"].str.upper().str.contains("CONTENEDOR", na=False)

    cantidad_contenedores_di = float(df_bultos.loc[es_contenedor, "CANTIDAD"].apply(safe_float).sum() or 0.0)
    cantidad_bultos_di       = float(df_bultos.loc[~es_contenedor, "CANTIDAD"].apply(safe_float).sum() or 0.0)
    peso_bruto_di            = float(df_bultos["PESO BRUTO"].apply(safe_float).sum() or 0.0)

    cantidad_contenedores_bl = safe_float(datos_bl.get("cantidad_contenedores", 0))
    cantidad_bultos_bl       = safe_float(datos_bl.get("cantidad_bultos", 0))
    peso_bruto_bl            = safe_float(datos_bl.get("peso_bruto_kg", 0))

    # ── Contenedores ──
    # Solo se compara si la DI declaró explícitamente un renglón "CONTENEDOR"
    # en Bultos. Si la DI no lo declara (habitual cuando todo se carga como
    # bultos sueltos aunque el BL sea FCL con 1 contenedor), no es un error:
    # son dos formas válidas de declarar la misma carga.
    if cantidad_contenedores_di > 0:
        if cantidad_contenedores_di != cantidad_contenedores_bl:
            resultados.append(al(f"Cantidad de contenedores — DI: {cantidad_contenedores_di:.0f} — BL: {cantidad_contenedores_bl:.0f}"))
        else:
            resultados.append(ok_(f"Cantidad de contenedores OK: {cantidad_contenedores_di:.0f}"))

    # ── Bultos sueltos ──
    # Si la DI ya declaró contenedor(es) (caso típico FCL), el conteo de
    # piezas/bultos que trae el BL (ej. "53 PIECE") describe el contenido
    # DENTRO del contenedor — no es un renglón de "bultos sueltos"
    # adicional a comparar contra la DI, que correctamente declara 0 en
    # ese caso (todo va como 1 CONTENEDOR). Comparar igual generaba un
    # falso "DI: 0 — BL: 53". Incluso si además hay contenedores, solo
    # tiene sentido esta comparación cuando la DI NO declaró ningún
    # contenedor (carga suelta / LCL).
    if cantidad_contenedores_di == 0 and (cantidad_bultos_di > 0 or cantidad_bultos_bl > 0):
        if cantidad_bultos_di != cantidad_bultos_bl:
            resultados.append(al(f"Cantidad de bultos — DI: {cantidad_bultos_di:.0f} — BL: {cantidad_bultos_bl:.0f}"))
        else:
            resultados.append(ok_(f"Cantidad de bultos OK: {cantidad_bultos_di:.0f}"))

    # ── Peso bruto ──
    if peso_bruto_di != peso_bruto_bl:
        resultados.append(al(f"Peso bruto — DI: {peso_bruto_di:.2f} kg — BL: {peso_bruto_bl:.2f} kg"))
    else:
        resultados.append(ok_(f"Peso bruto OK: {peso_bruto_di:.2f} kg"))

    return resultados


# ── Validación Documentos Declarados (Facturas/Forwarding/Vendedor) ──────────

def _normalizar_nombre(texto: str) -> str:
    """
    Normaliza un nombre de empresa/proveedor para comparación tolerante:
    mayúsculas, sin tildes, sin paréntesis (se conserva el contenido de
    adentro), sin espacios extra ni puntuación.
    Ej: 'Caterpillar Sarl (Latin America)' -> 'CATERPILLARSARLLATINAMERICA'
    """
    import unicodedata
    s = (texto or "").upper()
    s = unicodedata.normalize("NFKD", s)
    s = "".join(c for c in s if not unicodedata.combining(c))
    s = s.replace("(", "").replace(")", "")
    s = re.sub(r"[^A-Z0-9]", "", s)
    return s


def _extraer_columna_caratula(df_caratula: "pd.DataFrame", encabezado: str) -> list:
    """
    Busca, dentro de la grilla cruda de la solapa Carátula (leída sin
    header), la celda que contiene el texto `encabezado` (ej. "FACTURAS")
    y devuelve todos los valores no vacíos que aparecen debajo de esa
    celda, en la misma columna, hasta la primera fila vacía.
    """
    if df_caratula is None or df_caratula.empty:
        return []

    encabezado_upper = encabezado.strip().upper()
    for col in df_caratula.columns:
        for fila_idx, valor in enumerate(df_caratula[col]):
            if str(valor).strip().upper() == encabezado_upper:
                valores = []
                for v in df_caratula[col].iloc[fila_idx + 1:]:
                    v_str = str(v).strip()
                    if not v_str or v_str.lower() == "nan":
                        break
                    valores.append(v_str)
                return valores
    return []


def validar_documentos_declarados(df_caratula: "pd.DataFrame", datos_facturas: dict,
                                   datos_forwarding: dict = None) -> list:
    """
    Valida, contra la solapa Carátula del DI:
      - FACTURAS: que todas las facturas declaradas en la columna FACTURAS
        (incluida la Forwarding Invoice, que comparte la misma columna)
        tengan su documento correspondiente subido y procesado.
      - VENDEDOR: que el proveedor declarado coincida con el vendedor que
        figura en cada factura parseada (normalizado, comparación por
        substring para tolerar diferencias de paréntesis/espacios).
    """
    resultados = []
    CAMPO_FAC = "FACTURAS"
    CAMPO_VEND = "VENDEDOR"

    def al(campo, msg, nivel="ERROR"):
        return {"item": "GENERAL", "campo": campo, "mensaje": msg, "nivel": nivel}
    def ok_(campo, msg):
        return {"item": "GENERAL", "campo": campo, "mensaje": msg, "nivel": "OK"}

    facturas_declaradas = _extraer_columna_caratula(df_caratula, "FACTURAS")
    vendedor_declarado   = _extraer_columna_caratula(df_caratula, "VENDEDOR")
    vendedor_declarado   = vendedor_declarado[0] if vendedor_declarado else ""

    # ── Facturas (y Forwarding) declaradas vs subidas ──
    if facturas_declaradas:
        facturas_validas = {k: v for k, v in (datos_facturas or {}).items() if "error" not in v}
        nombres_subidos = set()
        for nro_factura, f in facturas_validas.items():
            nombres_subidos.add(re.sub(r"\.pdf$", "", nro_factura, flags=re.IGNORECASE).strip().upper())
            num_real = str(f.get("numero_factura", "")).strip().upper()
            if num_real:
                nombres_subidos.add(num_real)

        if datos_forwarding and "error" not in datos_forwarding:
            nro_fwd = str(datos_forwarding.get("numero_invoice", "")).strip().upper()
            if nro_fwd:
                nombres_subidos.add(nro_fwd)

        faltantes = [
            f for f in facturas_declaradas
            if f.strip().upper() not in nombres_subidos
        ]

        if faltantes:
            resultados.append(al(CAMPO_FAC,
                f"Declaradas en el DI pero no subidas/encontradas: {', '.join(faltantes)}"))
        else:
            resultados.append(ok_(CAMPO_FAC,
                f"Todas las facturas declaradas en el DI ({len(facturas_declaradas)}) fueron subidas: "
                f"{', '.join(facturas_declaradas)}"))

    # ── Forwarding Invoice: número declarado (mismo campo FACTURAS) vs subido ──
    if facturas_declaradas and datos_forwarding and "error" not in datos_forwarding:
        CAMPO_FWD = "FORWARDING"
        nro_fwd = str(datos_forwarding.get("numero_invoice", "")).strip().upper()
        declarado_fwd = next(
            (f for f in facturas_declaradas if nro_fwd and f.strip().upper() == nro_fwd),
            None
        )
        if nro_fwd and declarado_fwd:
            resultados.append(ok_(CAMPO_FWD,
                f"Invoice declarada en el DI coincide con la subida: {nro_fwd}"))
        elif nro_fwd:
            resultados.append(al(CAMPO_FWD,
                f"Invoice subida ({nro_fwd}) no figura entre las facturas declaradas en el DI: "
                f"{', '.join(facturas_declaradas)}"))

    # ── Vendedor declarado vs vendedor de cada factura ──
    if vendedor_declarado and datos_facturas:
        vendedor_norm = _normalizar_nombre(vendedor_declarado)
        facturas_validas = {k: v for k, v in (datos_facturas or {}).items() if "error" not in v}
        discrepancias = []
        for nro_factura, f in facturas_validas.items():
            vendedor_factura = str(f.get("vendedor", "")).strip()
            if not vendedor_factura:
                continue
            vendedor_factura_norm = _normalizar_nombre(vendedor_factura)
            coincide = vendedor_norm in vendedor_factura_norm or vendedor_factura_norm in vendedor_norm
            if not coincide:
                discrepancias.append(f"{nro_factura} (vendedor: '{vendedor_factura}')")

        if discrepancias:
            resultados.append(al(CAMPO_VEND,
                f"Vendedor declarado en DI: '{vendedor_declarado}' — no coincide con: {', '.join(discrepancias)}"))
        else:
            resultados.append(ok_(CAMPO_VEND,
                f"Vendedor verificado en todas las facturas: {vendedor_declarado}"))

    return resultados


# ── Resumen General: Certificados Mineros ─────────────────────────────────────

def validar_resumen_cm(df_items: pd.DataFrame, datos_cm: dict, resultados_cm_vs_di: list) -> list:
    """
    Resumen a nivel despacho de los Certificados Mineros: cuántos de los
    CM declarados en el DI (columna D:CERTSM de cada ítem) están OK en
    número y contenido (NCM, código, cantidad, FOB), agrupados en una sola
    línea — y una línea aparte solo para cada CM que tenga un problema
    (no se listan los que están OK individualmente, para no generar una
    línea por cada uno de potencialmente decenas de CM).

    Todas las filas que devuelve esta función llevan "es_resumen": True
    y son exclusivas de la sección "Revisión General" — el detalle real
    de cada problema (con ítem, código y factura) vive únicamente en
    Errores/Alertas, generado por validar_cm_vs_di().
    """
    resultados = []
    CAMPO = "CERTIFICADOS MINEROS"

    def al(msg, nivel="ERROR"):
        return {"item": "GENERAL", "campo": CAMPO, "mensaje": msg, "nivel": nivel, "es_resumen": True}
    def ok_(msg):
        return {"item": "GENERAL", "campo": CAMPO, "mensaje": msg, "nivel": "OK", "es_resumen": True}

    if df_items is None or df_items.empty:
        return resultados

    cms_declarados = set()
    for _, row in df_items.iterrows():
        cm = str(row.get("D:CERTSM", "")).strip()
        if cm:
            cms_declarados.add(cm)

    if not cms_declarados:
        return resultados

    datos_cm = datos_cm or {}

    # Niveles encontrados en el detalle (NCM/MODELO/CANTIDAD/MONTO FOB) por
    # CM. El número de CM se infiere del propio mensaje, que siempre
    # empieza con "[CM: <numero>] ..." en validar_cm_vs_di.
    campos_detalle_cm = {"NCM (CM)", "MODELO (CM)", "CANTIDAD (CM)", "MONTO FOB (CM)", "CM", "SUBITEM"}
    nivel_por_cm = {}  # numero_cm -> "ERROR" | "ALERTA" (el más alto encontrado)
    for r in resultados_cm_vs_di or []:
        if r.get("campo") not in campos_detalle_cm or r.get("nivel") == "OK":
            continue
        m = re.search(r"\[CM:\s*([^\]]+)\]", str(r.get("mensaje", "")))
        if not m:
            continue
        numero_cm = m.group(1).strip()
        nivel = r.get("nivel")
        if nivel_por_cm.get(numero_cm) != "ERROR":
            nivel_por_cm[numero_cm] = nivel if nivel == "ERROR" else (nivel_por_cm.get(numero_cm) or nivel)

    total = len(cms_declarados)
    con_problema = []  # (numero_cm, motivo, nivel)

    for numero_cm in sorted(cms_declarados):
        cm_data = datos_cm.get(numero_cm)

        if not cm_data or "error" in cm_data:
            con_problema.append((numero_cm,
                "declarado en el despacho pero no se encontró su PDF subido / no se pudo procesar",
                "ERROR"))
            continue

        nivel_detalle = nivel_por_cm.get(numero_cm)
        if nivel_detalle:
            pestana = "Errores" if nivel_detalle == "ERROR" else "Alertas"
            con_problema.append((numero_cm,
                f"número OK, pero hay diferencias en el contenido — ver pestaña {pestana}",
                nivel_detalle))

    ok_count = total - len(con_problema)
    if con_problema:
        resultados.append(al(
            f"De los {total} CM declarados, {ok_count} están OK en número, NCM, código, cantidad y FOB",
            "ERROR" if any(n == "ERROR" for _, _, n in con_problema) else "ALERTA"))
    else:
        resultados.append(ok_(
            f"De los {total} CM declarados, {ok_count} están OK en número, NCM, código, cantidad y FOB"))

    for numero_cm, motivo, nivel in con_problema:
        resultados.append(al(f"CM {numero_cm}: {motivo}", nivel))

    return resultados


# ── Resumen General: DJ de Origen No Preferencial ─────────────────────────────

def validar_resumen_dj_origen(df_items: pd.DataFrame, datos_dj: list, resultados_dj_origen: list) -> list:
    """
    Resumen a nivel despacho de las DJ de Origen No Preferencial: cuántas
    de las DJ declaradas en el DI (campo D:DJ-ORIG-NOPREFER de cada
    ítem — puede haber más de una en el despacho) están OK en número y
    contenido (NCM, cantidad, país, CIF), agrupadas en una sola línea —
    y una línea aparte solo para cada DJ que tenga un problema.

    Todas las filas que devuelve esta función llevan "es_resumen": True
    y son exclusivas de la sección "Revisión General" — el detalle real
    de cada problema vive únicamente en Errores/Alertas, generado por
    validar_dj_origen().
    """
    resultados = []
    CAMPO = "DJ ORIGEN"

    def al(msg, nivel="ERROR"):
        return {"item": "GENERAL", "campo": CAMPO, "mensaje": msg, "nivel": nivel, "es_resumen": True}
    def ok_(msg):
        return {"item": "GENERAL", "campo": CAMPO, "mensaje": msg, "nivel": "OK", "es_resumen": True}

    if df_items is None or df_items.empty:
        return resultados

    djs_declaradas = set()
    for _, row in df_items.iterrows():
        dj = str(row.get("D:DJ-ORIG-NOPREFER", "")).strip()
        if dj:
            djs_declaradas.add(dj)

    if not djs_declaradas:
        return resultados

    ifs_subidos = {
        str(d.get("numero_if", "")).strip().upper()
        for d in (datos_dj or []) if "error" not in d and d.get("numero_if")
    }

    # Nivel más alto encontrado en el detalle (campos que empiezan con "DJ ")
    # de validar_dj_origen, sin contar el chequeo de D:DJ-ORIG-NOPREFER en sí
    # (ese es justamente el de "declarada vs subida" que ya resolvemos aquí).
    hay_error_detalle = any(
        r.get("nivel") == "ERROR" and str(r.get("campo", "")).startswith("DJ ")
        for r in (resultados_dj_origen or [])
    )
    hay_alerta_detalle = any(
        r.get("nivel") == "ALERTA" and str(r.get("campo", "")).startswith("DJ ")
        for r in (resultados_dj_origen or [])
    )

    total = len(djs_declaradas)
    con_problema = []  # (dj, motivo, nivel)

    for dj_declarada in sorted(djs_declaradas):
        coincide = any(
            dj_declarada.upper() in if_sub or if_sub in dj_declarada.upper()
            for if_sub in ifs_subidos
        )

        if not ifs_subidos or not coincide:
            con_problema.append((dj_declarada,
                "declarada en el despacho pero no se encontró su PDF subido", "ERROR"))
            continue

        if hay_error_detalle:
            con_problema.append((dj_declarada,
                "número OK, pero hay diferencias en el contenido — ver pestaña Errores", "ERROR"))
        elif hay_alerta_detalle:
            con_problema.append((dj_declarada,
                "número OK, pero hay diferencias en el contenido — ver pestaña Alertas", "ALERTA"))

    ok_count = total - len(con_problema)
    if con_problema:
        resultados.append(al(
            f"De las {total} DJ declaradas, {ok_count} están OK en número, NCM, cantidad, país y CIF",
            "ERROR" if any(n == "ERROR" for _, _, n in con_problema) else "ALERTA"))
    else:
        resultados.append(ok_(
            f"De las {total} DJ declaradas, {ok_count} están OK en número, NCM, cantidad, país y CIF"))

    for dj_declarada, motivo, nivel in con_problema:
        resultados.append(al(f"DJ {dj_declarada}: {motivo}", nivel))

    return resultados


# ── Resumen General: Ítems vs CM y vs Factura ─────────────────────────────────

def validar_resumen_items(resultados_cm_vs_di: list, resultados_factura_vs_di: list,
                           total_items_di: int = None,
                           resultados_ncm_excel: list = None) -> list:
    """
    Resumen a nivel despacho del detalle por ítem, agrupado por campo —
    no por ítem, ya que no todos los ítems tienen los mismos campos
    aplicables (CM solo aplica a ítems con CM; Factura solo a ítems que
    matchean alguna línea de factura subida).

    El denominador de cada línea es siempre `total_items_di` (el total
    real de ítems del DI, no solo los que llegaron a evaluarse en ese
    campo) — así nunca hay ambigüedad sobre "de cuántos" se está
    hablando. Si un ítem no llegó a evaluarse en un campo (ej. su código
    no matcheó en ninguna factura, así que nunca se comparó el FOB), se
    cuenta como "no comparable", distinto de "con diferencia" (sí se
    evaluó, y dio mal).

    Genera 2 líneas (si hay datos disponibles):
      "De X ítems del DI: NCM Y/X OK | MODELO Y/X OK | ..."
      "De X ítems del DI: CÓDIGO Y/X OK — N no encontrado(s) en factura | ..."

    El detalle real (ítem, código, factura, CM) sigue viviendo
    únicamente en Errores/Alertas — esta función solo cuenta y redirige,
    sin listar números de ítem.

    Todas las filas que devuelve esta función llevan "es_resumen": True
    y son exclusivas de la sección "Revisión General".
    """
    resultados = []
    CAMPO = "ÍTEMS"

    def al(msg, nivel="ERROR"):
        return {"item": "GENERAL", "campo": CAMPO, "mensaje": msg, "nivel": nivel, "es_resumen": True}
    def ok_(msg):
        return {"item": "GENERAL", "campo": CAMPO, "mensaje": msg, "nivel": "OK", "es_resumen": True}

    if not total_items_di:
        return resultados

    def _resumen_campo(resultados_fuente: list, nombre_campo_origen: str):
        """
        Cuenta, para un campo dado (ej. "NCM (CM)"), cuántos ítems únicos
        tuvieron ese campo evaluado, cuántos dieron OK, y el nivel más
        alto entre los que no dieron OK (None si todos OK o no hubo
        evaluados). El total de evaluados puede ser menor a
        total_items_di — la diferencia es "no comparable" en ese campo.
        """
        items_evaluados = {}  # item -> nivel más alto encontrado para ese campo
        for r in resultados_fuente or []:
            if r.get("campo") != nombre_campo_origen:
                continue
            item = str(r.get("item", ""))
            nivel = r.get("nivel")
            if items_evaluados.get(item) != "ERROR":
                items_evaluados[item] = nivel

        evaluados = len(items_evaluados)
        ok_count = sum(1 for n in items_evaluados.values() if n == "OK")
        no_comparable = total_items_di - evaluados

        nivel_problema = None
        if any(n == "ERROR" for n in items_evaluados.values()):
            nivel_problema = "ERROR"
        elif any(n == "ALERTA" for n in items_evaluados.values()):
            nivel_problema = "ALERTA"

        return evaluados, ok_count, no_comparable, nivel_problema

    def _armar_parte(nombre_mostrar, evaluados, ok_count, no_comparable, nivel_problema,
                      etiqueta_no_comparable, etiqueta_con_diferencia="con diferencia"):
        """Arma el fragmento de texto para un campo, dentro del límite de total_items_di."""
        if evaluados == 0 and no_comparable == 0:
            return None, None  # este campo no corrió en absoluto, no incluir

        if not nivel_problema and no_comparable == 0:
            return f"{nombre_mostrar} {ok_count}/{total_items_di} OK", None

        pestana = "Errores" if nivel_problema == "ERROR" else "Alertas"
        con_diferencia = evaluados - ok_count
        detalle = []
        if no_comparable:
            detalle.append(f"{no_comparable} {etiqueta_no_comparable}")
        if con_diferencia:
            detalle.append(f"{con_diferencia} {etiqueta_con_diferencia}")
        detalle_str = ", ".join(detalle)
        nivel_fragmento = nivel_problema or "ALERTA"  # no_comparable sin nivel_problema -> alerta por defecto
        if not nivel_problema:
            pestana = "Alertas"
        parte = f"{nombre_mostrar} {ok_count}/{total_items_di} OK — {detalle_str} — ver pestaña {pestana}"
        return parte, nivel_fragmento

    # ── Grupo CM ──
    campos_cm = [
        ("NCM (CM)", "NCM vs CM", "sin CM declarado/subido"),
        ("MODELO (CM)", "MODELO vs CM", "sin CM declarado/subido"),
        ("CANTIDAD (CM)", "CANTIDAD vs CM", "sin CM declarado/subido"),
        ("MONTO FOB (CM)", "FOB vs CM", "sin CM declarado/subido"),
    ]
    partes_cm = []
    nivel_general_cm = None
    hubo_cm = False
    items_con_cm = evaluados_cm = 0  # se calcula del primer campo disponible
    for campo_origen, nombre_mostrar, etiqueta_nc in campos_cm:
        evaluados, ok_count, no_comparable, nivel_problema = _resumen_campo(resultados_cm_vs_di, campo_origen)
        if evaluados == 0:
            continue  # ningún ítem tiene CM en este despacho para este campo
        hubo_cm = True
        # Usar "evaluados" como denominador real (solo ítems con CM).
        # Los que no tienen CM simplemente no aplica — se muestra aparte.
        if not evaluados_cm:
            evaluados_cm = evaluados
        sin_cm = total_items_di - evaluados
        sufijo_sin_cm = f" ({sin_cm} sin CM, no aplica)" if sin_cm else ""
        if not nivel_problema:
            partes_cm.append(f"{nombre_mostrar} {ok_count}/{evaluados_cm} OK{sufijo_sin_cm}")
        else:
            pestana = "Errores" if nivel_problema == "ERROR" else "Alertas"
            con_diff = evaluados - ok_count
            partes_cm.append(f"{nombre_mostrar} {ok_count}/{evaluados_cm} OK — {con_diff} con diferencia — ver pestaña {pestana}{sufijo_sin_cm}")
            if nivel_problema == "ERROR":
                nivel_general_cm = "ERROR"
            elif nivel_general_cm != "ERROR":
                nivel_general_cm = "ALERTA"

    if hubo_cm:
        msg = f"De {total_items_di} ítems del DI: " + " | ".join(partes_cm)
        if nivel_general_cm:
            resultados.append(al(msg, nivel_general_cm))
        else:
            resultados.append(ok_(msg))

    # ── Grupo Factura (incluye Clasificación) ──
    # CÓDIGO es la base: si el código no matcheó, MONTO FOB y
    # CLASIFICACIÓN tampoco pudieron evaluarse para ese ítem — por eso
    # ambos usan la misma cuenta de "no comparable" (= ítems sin match
    # de código), distinta de "con diferencia" (matcheó pero dio mal).
    evaluados_cod, ok_cod, no_comp_cod, nivel_cod = _resumen_campo(resultados_factura_vs_di, "CÓDIGO (FACTURA)")
    evaluados_fob, ok_fob, no_comp_fob, nivel_fob = _resumen_campo(resultados_factura_vs_di, "MONTO FOB (FACTURA)")
    evaluados_cla, ok_cla, no_comp_cla, nivel_cla = _resumen_campo(resultados_factura_vs_di, "CÓDIGO EN CLASIFICACIÓN (EXCEL)")

    partes_fac = []
    nivel_general_fac = None

    if evaluados_cod or no_comp_cod:
        parte, nivel_frag = _armar_parte("CÓDIGO vs Factura", evaluados_cod, ok_cod, 0, nivel_cod,
                                          "no encontrado(s) en factura", "no encontrado(s) en factura")
        if parte:
            partes_fac.append(parte)
            if nivel_frag == "ERROR":
                nivel_general_fac = "ERROR"
            elif nivel_frag and nivel_general_fac != "ERROR":
                nivel_general_fac = "ALERTA"

    if evaluados_fob or no_comp_fob:
        parte, nivel_frag = _armar_parte("FOB vs Factura", evaluados_fob, ok_fob, no_comp_fob, nivel_fob,
                                          "no comparable(s) (sin match de código)")
        if parte:
            partes_fac.append(parte)
            if nivel_frag == "ERROR":
                nivel_general_fac = "ERROR"
            elif nivel_frag and nivel_general_fac != "ERROR":
                nivel_general_fac = "ALERTA"

    if evaluados_cla or no_comp_cla:
        parte, nivel_frag = _armar_parte("NCM vs Excel Clasif.", evaluados_cla, ok_cla, no_comp_cla, nivel_cla,
                                          "no comparable(s) (sin match de código)")
        if parte:
            partes_fac.append(parte)
            if nivel_frag == "ERROR":
                nivel_general_fac = "ERROR"
            elif nivel_frag and nivel_general_fac != "ERROR":
                nivel_general_fac = "ALERTA"

    if partes_fac:
        msg = f"De {total_items_di} ítems del DI: " + " | ".join(partes_fac)
        if nivel_general_fac:
            resultados.append(al(msg, nivel_general_fac))
        else:
            resultados.append(ok_(msg))

    # ── NCM vs Excel (comparación NCM DI contra NCM del Excel de clasificación) ──
    if resultados_ncm_excel:
        evaluados_ncm_xl, ok_ncm_xl, _, nivel_ncm_xl = _resumen_campo(resultados_ncm_excel, "NCM vs EXCEL")
        if evaluados_ncm_xl:
            con_diff = evaluados_ncm_xl - ok_ncm_xl
            if not nivel_ncm_xl:
                resultados.append(ok_(f"De {total_items_di} ítems del DI: NCM vs Excel {ok_ncm_xl}/{evaluados_ncm_xl} evaluados OK (resto sin código en Excel)"))
            else:
                pestana = "Errores" if nivel_ncm_xl == "ERROR" else "Alertas"
                resultados.append(al(
                    f"De {total_items_di} ítems del DI: NCM vs Excel {ok_ncm_xl}/{evaluados_ncm_xl} evaluados OK — {con_diff} con NCM distinto — ver pestaña {pestana}",
                    nivel_ncm_xl
                ))

    return resultados


# ── Validación: Configuración seleccionada vs Carátula del DI ────────────────

def _normalizar_cuit(cuit: str) -> str:
    """Quita guiones/espacios y deja solo los dígitos del CUIT, para
    comparar sin importar el formato (con o sin guiones)."""
    return re.sub(r"[^\d]", "", str(cuit or ""))


def validar_config_vs_caratula(caratula: dict, config: dict) -> list:
    """
    Valida que la Empresa importadora, Régimen y Aduana seleccionados en
    pantalla (sidebar de configuración) coincidan con lo declarado en la
    solapa Carátula del DI — comparación por substring en ambos sentidos
    (tolerante a que la Carátula traiga el código + descripción completa,
    ej. "001 - BS.AS.(CAPITAL)" vs el nombre corto seleccionado en
    pantalla). El Despachante (fijo, MINOYETTI FEDERICO) también se
    valida de la misma forma, vía su nombre y CUIT.

    Pensado para cuando este corrector se use con despachos de otros
    importadores/regímenes/aduanas además de los habituales — si algo no
    coincide, conviene saberlo antes de seguir analizando, ya que podría
    indicar que se está revisando el despacho equivocado.
    """
    resultados = []

    def al(campo, msg, nivel="ERROR"):
        return {"item": "GENERAL", "campo": campo, "mensaje": msg, "nivel": nivel}
    def ok_(campo, msg):
        return {"item": "GENERAL", "campo": campo, "mensaje": msg, "nivel": "OK"}

    if not caratula:
        return resultados

    def _valor_caratula(campo: str) -> str:
        campo_upper = campo.upper()
        for k, v in caratula.items():
            if campo_upper in k.upper():
                return str(v).strip()
        return ""

    def _contiene(seleccionado: str, declarado: str) -> bool:
        s = seleccionado.strip().upper()
        d = declarado.strip().upper()
        if not s or not d:
            return False
        return s in d or d in s

    # ── Empresa (razón social + CUIT) ──
    empresa_sel = config.get("empresa", "")
    cuit_sel = config.get("cuit_ie", "")
    empresa_di = _valor_caratula("EMPRESA")
    cuit_di = _valor_caratula("CUIT IE")

    if empresa_di:
        if _contiene(empresa_sel, empresa_di):
            resultados.append(ok_("EMPRESA", f"Empresa seleccionada coincide con el DI: {empresa_di}"))
        else:
            resultados.append(al("EMPRESA", f"Empresa seleccionada: '{empresa_sel}' — DI declara: '{empresa_di}'"))

    if cuit_di:
        if _normalizar_cuit(cuit_sel) == _normalizar_cuit(cuit_di):
            resultados.append(ok_("EMPRESA", f"CUIT IE coincide: {cuit_di}"))
        else:
            resultados.append(al("EMPRESA", f"CUIT IE seleccionado: '{cuit_sel}' — DI declara: '{cuit_di}'"))

    # ── Régimen ──
    regimen_sel = config.get("regimen", "")
    regimen_di = _valor_caratula("REGIMEN")
    if regimen_di:
        if _contiene(regimen_sel, regimen_di):
            resultados.append(ok_("REGIMEN", f"Régimen seleccionado coincide con el DI: {regimen_di}"))
        else:
            resultados.append(al("REGIMEN", f"Régimen seleccionado: '{regimen_sel}' — DI declara: '{regimen_di}'"))

    # ── Aduana ──
    aduana_sel_codigo = config.get("aduana_codigo", "")
    aduana_sel_nombre = config.get("aduana", "")
    aduana_di = _valor_caratula("ADUANA")
    if aduana_di:
        if _contiene(aduana_sel_codigo, aduana_di) or _contiene(aduana_sel_nombre, aduana_di):
            resultados.append(ok_("ADUANA", f"Aduana seleccionada coincide con el DI: {aduana_di}"))
        else:
            resultados.append(al("ADUANA", f"Aduana seleccionada: '{aduana_sel_nombre}' — DI declara: '{aduana_di}'"))

    # ── Despachante (fijo) + CUIT DA ──
    despachante_di = _valor_caratula("DESPACHANTE")
    cuit_da_di = _valor_caratula("CUIT DA")

    if despachante_di:
        if _contiene(DESPACHANTE, despachante_di):
            resultados.append(ok_("DESPACHANTE", f"Despachante coincide con el DI: {despachante_di}"))
        else:
            resultados.append(al("DESPACHANTE", f"Despachante esperado: '{DESPACHANTE}' — DI declara: '{despachante_di}'"))

    if cuit_da_di:
        if _normalizar_cuit(CUIT_DESPACHANTE) == _normalizar_cuit(cuit_da_di):
            resultados.append(ok_("DESPACHANTE", f"CUIT DA coincide: {cuit_da_di}"))
        else:
            resultados.append(al("DESPACHANTE", f"CUIT DA esperado: '{CUIT_DESPACHANTE}' — DI declara: '{cuit_da_di}'"))

    return resultados
