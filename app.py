import streamlit as st
import pandas as pd
import io
import re as _re
from concurrent.futures import ThreadPoolExecutor, as_completed

from config.defaults import EMPRESAS, DESPACHANTE, CUIT_DESPACHANTE, REGIMENES, ADUANAS
from utils.parser_di import leer_di, safe_float
from utils.validaciones import validar_items, validar_subitems, validar_liquidacion, validar_prorrateo, validar_ncm_excel, validar_resumen_liquidacion, validar_dumping_marca_dj, validar_items_usados, validar_campos_control, CAMPOS_INFORMATIVOS
from utils.extractor_api import extraer_forwarding, extraer_bl, extraer_cm, extraer_dj_origen, extraer_numero_re_de_ce
from utils.parser_factura_cat import extraer_factura_cat
from utils.cruce_docs import validar_cm_vs_di, validar_factura_vs_di, validar_caratula_vs_docs, validar_caratula_totales, validar_dj_origen, validar_bultos_vs_bl, validar_documentos_declarados, validar_resumen_cm, validar_resumen_dj_origen, validar_resumen_items, validar_config_vs_caratula
from utils.reporte_pdf import generar_reporte_pdf

st.set_page_config(page_title="Corrector FASA/FSM", page_icon="🔍", layout="wide")

# ─── RESETEO (botón "Nuevo análisis") ─────────────────────────────────────────
# Streamlit no vacía un file_uploader solo borrando su key de session_state
# si el archivo ya quedó cargado en el navegador — hace falta que la key
# del widget cambie para que lo trate como un widget nuevo. Por eso se usa
# un contador de versión ("uploader_version"): al resetear, se incrementa,
# y todas las keys de los uploaders (definidas como f"di_{version}", etc.)
# cambian, forzando que vuelvan a aparecer vacíos.
# El caché de CMs (cache_cm) se mantiene a propósito, para no perder
# llamadas a la API ya hechas si se vuelve a analizar un CM repetido.
if "uploader_version" not in st.session_state:
    st.session_state["uploader_version"] = 0

if st.session_state.get("_resetear", False):
    st.session_state["uploader_version"] += 1
    claves_a_borrar = [
        "resultados", "config", "docs_procesados", "referencia", "numero_di",
        "_resetear",
    ]
    for k in claves_a_borrar:
        st.session_state.pop(k, None)

UV = st.session_state["uploader_version"]  # sufijo de key para los uploaders

st.markdown("""
<style>
.titulo { font-size: 1.8rem; font-weight: 700; color: #1F3864; margin-bottom: 0.2rem; }
.subtitulo { font-size: 1rem; color: #595959; margin-bottom: 1.5rem; }
</style>
""", unsafe_allow_html=True)

col_titulo, col_reset = st.columns([5, 1])
with col_titulo:
    st.markdown('<div class="titulo">🔍 Corrector de Despachos FASA/FSM</div>', unsafe_allow_html=True)
    st.markdown('<div class="subtitulo">Validación automática de despachos de importación — Finning Soluciones Mineras</div>', unsafe_allow_html=True)
with col_reset:
    if st.button("🆕 Nuevo análisis", use_container_width=True, help="Limpia documentos cargados y resultados para analizar un despacho nuevo. El caché de CMs ya procesados se mantiene."):
        st.session_state["_resetear"] = True
        st.rerun()

# ─── SIDEBAR ─────────────────────────────────────────────────────────────────
with st.sidebar:
    st.header("⚙️ Configuración")
    empresa = st.selectbox("Empresa importadora", list(EMPRESAS.keys()))
    cuit_ie = EMPRESAS[empresa]
    st.caption(f"CUIT: {cuit_ie}")
    regimen = st.selectbox("Régimen", REGIMENES)
    aduana = st.selectbox("Aduana", list(ADUANAS.keys()))
    aduana_codigo = ADUANAS[aduana]
    st.divider()
    st.caption(f"**Despachante:** {DESPACHANTE}")
    st.caption(f"**CUIT DA:** {CUIT_DESPACHANTE}")

config = {"empresa": empresa, "cuit_ie": cuit_ie, "regimen": regimen, "aduana": aduana, "aduana_codigo": aduana_codigo}

# ─── REFERENCIA ──────────────────────────────────────────────────────────────
referencia = st.text_input("📌 Referencia de la operación a analizar",
    placeholder="Ej: FSM-2026-0612", help="Se usa para nombrar los archivos de salida (PDF / Excel).",
    key=f"referencia_input_{UV}")

# ─── DOCUMENTOS ──────────────────────────────────────────────────────────────
st.subheader("📁 Carga de documentos")

col1, col2 = st.columns(2)
with col1:
    di_file = st.file_uploader("📊 Excel del DI (Provisorio)", type=["xlsx", "xls"], key=f"di_{UV}")
    facturas = st.file_uploader("🧾 Facturas comerciales (PDF)", type=["pdf"], accept_multiple_files=True, key=f"facturas_{UV}")
    forwarding_file = st.file_uploader("🚢 Forwarding Invoice (PDF)", type=["pdf"], key=f"forwarding_{UV}")

with col2:
    bl_file = st.file_uploader("📋 Bill of Lading (PDF)", type=["pdf"], key=f"bl_{UV}")
    ncm_file = st.file_uploader("📑 Excel de clasificación NCM", type=["xlsx", "xls"], key=f"ncm_{UV}")
    dj_origen_files = st.file_uploader("📄 DJ Origen No Preferencial (PDF)",
        type=["pdf"], accept_multiple_files=True, key=f"dj_origen_{UV}")

# ─── CERTIFICADOS MINEROS ─────────────────────────────────────────────────────
st.subheader("📜 Certificados Mineros (CM)")
st.info("Subí todos los CE y RE juntos. La app los empareja automáticamente.", icon="ℹ️")

cm_files = st.file_uploader("Archivos CM (CE y RE en PDF)",
    type=["pdf"], accept_multiple_files=True, key=f"cms_{UV}")

ce_files = {}
re_files = {}
if cm_files:
    for f in cm_files:
        nombre = f.name.upper()
        if nombre.startswith("CE-"):
            numero = nombre.split("_")[0].replace(".PDF", "")
            ce_files[numero] = f
        elif nombre.startswith("RE-"):
            numero = nombre.split("_")[0].replace(".PDF", "")
            re_files[numero] = f
    st.caption(f"{len(ce_files)} CE(s) y {len(re_files)} RE(s) detectados")

# ─── ANALIZAR ─────────────────────────────────────────────────────────────────
st.divider()
analizar = st.button("🔍 Analizar Despacho", type="primary", use_container_width=True)

if analizar:
    if not di_file:
        st.error("⚠️ Cargá el Excel del DI para continuar.")
        st.stop()

    todos_resultados = []
    pat_num = _re.compile(r"[0-9]{8,}")

    def _num(s):
        m = pat_num.search(s.upper())
        return m.group(0) if m else s

    # Número de DI: se extrae del nombre del archivo Excel subido (ej.
    # "26001IC04605782@.xlsx" -> "26001IC04605782@"), quitando solo la
    # extensión. Es solo informativo para mostrar en el PDF — el nombre
    # del archivo de salida usa la "Referencia" indicada por el usuario,
    # no este número.
    numero_di = _re.sub(r"\.(xlsx|xls)$", "", di_file.name, flags=_re.IGNORECASE)

    with st.status("Analizando despacho...", expanded=True) as status:

        # ── 1. Parsear DI ─────────────────────────────────────────────────
        st.write("📊 Leyendo Excel del DI...")
        try:
            di_data = leer_di(di_file)
            df_items = di_data.get("items", pd.DataFrame())
            df_subitems = di_data.get("subitems", pd.DataFrame())
            df_liq = di_data.get("liquidacion", pd.DataFrame())
            df_bultos = di_data.get("bultos", pd.DataFrame())
            caratula = di_data.get("caratula", {})
            # Leer solapa Carátula como DataFrame para extraer números de factura
            try:
                di_file.seek(0)
                df_caratula = pd.read_excel(di_file, sheet_name="Carátula", header=None, dtype=str)
            except Exception:
                df_caratula = None
            st.write(f"   ✅ {len(df_items)} ítems leídos")
        except Exception as e:
            st.error(f"Error leyendo el DI: {e}")
            st.stop()

        # ── 2. Cálculo de totales (las validaciones que usan df_items/subitems
        #      se ejecutan más abajo, paso 9, una vez que existen datos_cm y
        #      datos_facturas — necesarios para resolver la factura correcta
        #      de cada ítem sin ambigüedad) ──────────────────────────────────
        fob_total = df_items["VALOR FOB"].apply(safe_float).sum() if "VALOR FOB" in df_items.columns else 0
        flete_total_di = df_items["FLETE EN DIV"].apply(safe_float).sum() if "FLETE EN DIV" in df_items.columns else 0
        seguro_total_di = df_items["SEGURO EN DIV"].apply(safe_float).sum() if "SEGURO EN DIV" in df_items.columns else 0

        # (validar_prorrateo y validar_liquidacion también se mueven al paso 9)
        st.write(f"   ✅ Totales calculados: FOB {fob_total:.2f}")

        # ── 3. Excel NCM / Clasificación (solo lectura; validación en paso 9) ──
        # Hay dos formatos posibles de este Excel:
        #   - "Marítimo": trae una fila de encabezados reales (PART_NUMBER,
        #     etc.), con el NCM en la columna sin nombre que sigue.
        #   - "Aéreo" (ej. clasi.xlsx): tabla simple de 2 columnas SIN
        #     encabezado — la primera fila ya es un dato (código de parte,
        #     NCM). Si se lee con el header por defecto, pandas termina
        #     nombrando la primera columna con ese valor (a veces un
        #     número), lo que rompe validar_ncm_excel más adelante.
        # Se detecta cuál es mirando si la primera fila "parece" un
        # encabezado (contiene palabras clave tipo PART/NCM/etc.).
        df_ncm = None
        if ncm_file:
            try:
                probe = pd.read_excel(ncm_file, header=None, dtype=str, nrows=1)
                primera_fila = probe.iloc[0].tolist()
                parece_header = any(
                    isinstance(v, str) and any(k in v.upper() for k in ["PART", "NCM", "PARTE", "CODIGO", "COD_", "MATERIAL"])
                    for v in primera_fila if v is not None
                )
                if parece_header:
                    df_ncm = pd.read_excel(ncm_file, dtype=str)
                else:
                    ncm_file.seek(0)
                    df_ncm = pd.read_excel(ncm_file, header=None, dtype=str)
                    df_ncm = df_ncm.iloc[:, :2]
                    df_ncm.columns = ["PART_NUMBER", "NCM"]
                st.write("   ✅ Excel de clasificación leído")
            except Exception as e:
                st.write(f"   ❌ Error leyendo Excel NCM: {e}")

        # ── 4. Facturas CAT (parser local, sin API) ─────────────────────────
        datos_facturas = {}
        if facturas:
            st.write(f"🧾 Extrayendo {len(facturas)} factura(s) (parser local CAT)...")
            for fac in facturas:
                try:
                    # Por ahora todas las facturas son CAT. Si en el futuro se
                    # suman otros proveedores, acá se puede detectar el tipo
                    # (ej. por nombre de archivo o contenido) y enrutar al
                    # parser correspondiente.
                    datos = extraer_factura_cat(fac.read())
                    datos_facturas[fac.name] = datos
                    st.write(f"   ✅ {fac.name}: {len(datos.get('items', []))} ítems")
                except Exception as e:
                    datos_facturas[fac.name] = {"error": str(e)}
                    st.write(f"   ❌ {fac.name}: {e}")

        # ── 5. API: Forwarding ────────────────────────────────────────────
        datos_forwarding = {}
        if forwarding_file:
            st.write("🚢 Extrayendo Forwarding Invoice...")
            try:
                datos_forwarding = extraer_forwarding(forwarding_file.read())
                st.write(f"   ✅ Flete: {datos_forwarding.get('flete_total')} | Seguro: {datos_forwarding.get('seguro_total')}")
            except Exception as e:
                datos_forwarding = {"error": str(e)}
                st.write(f"   ❌ {e}")

        # ── 6. API: BL ────────────────────────────────────────────────────
        datos_bl = {}
        if bl_file:
            st.write("📋 Extrayendo Bill of Lading...")
            try:
                bl_file.seek(0)
                datos_bl = extraer_bl(bl_file.read())
                st.write(f"   ✅ BL: {datos_bl.get('bl_number')} | Fecha: {datos_bl.get('fecha_embarque')}")
            except Exception as e:
                datos_bl = {"error": str(e)}
                st.write(f"   ❌ {e}")

        # ── 7. API: DJ Origen ─────────────────────────────────────────────
        datos_dj = []
        if dj_origen_files:
            st.write(f"📄 Extrayendo {len(dj_origen_files)} DJ(s) de origen...")
            for dj_file in dj_origen_files:
                try:
                    datos = extraer_dj_origen(dj_file.read())
                    datos_dj.append(datos)
                    st.write(f"   ✅ {dj_file.name}: IF={datos.get('numero_if', '?')}")
                except Exception as e:
                    datos_dj.append({"error": str(e)})
                    st.write(f"   ❌ {dj_file.name}: {e}")

        # ── 8. CMs en paralelo (con caché de sesión) ─────────────────────
        datos_cm = {}
        if ce_files:
            # Inicializar caché en session_state
            if "cache_cm" not in st.session_state:
                st.session_state["cache_cm"] = {}
            cache_cm = st.session_state["cache_cm"]

            st.write(f"📜 Procesando {len(ce_files)} CM(s)...")

            # Preparar pares CE+RE
            pares_cm = {}
            for numero_ce, ce_file in ce_files.items():
                ce_bytes = ce_file.read()
                numero_re_completo = extraer_numero_re_de_ce(ce_bytes)
                num_re = _num(numero_re_completo)
                re_file = next((f for n, f in re_files.items() if _num(n) == num_re), None)
                if re_file:
                    re_bytes = re_file.read()
                    num_ce = _num(numero_ce)
                    numero_completo = next(
                        (v for v in df_items["D:CERTSM"].unique() if _num(v) == num_ce),
                        numero_ce
                    )
                    pares_cm[numero_completo] = (ce_bytes, re_bytes, numero_ce, num_re)
                else:
                    st.write(f"   ⚠️ {numero_ce}: No se encontró el RE ({numero_re_completo})")

            # Separar CMs cacheados vs a procesar
            pares_nuevos = {}
            for numero_completo, datos_par in pares_cm.items():
                if numero_completo in cache_cm:
                    datos_cm[numero_completo] = cache_cm[numero_completo]
                    _, _, numero_ce, num_re = datos_par
                    n_items = len(cache_cm[numero_completo].get("items", []))
                    st.write(f"   ⚡ {numero_ce} (caché) | {n_items} ítems")
                else:
                    pares_nuevos[numero_completo] = datos_par

            # Procesar en paralelo solo los nuevos
            def procesar_cm(args):
                numero_completo, (ce_bytes, re_bytes, numero_ce, num_re) = args
                try:
                    datos = extraer_cm(ce_bytes, re_bytes)
                    return numero_completo, datos, numero_ce, num_re, None
                except Exception as e:
                    return numero_completo, {"error": str(e)}, numero_ce, num_re, str(e)

            if pares_nuevos:
                with ThreadPoolExecutor(max_workers=2) as executor:
                    futures = {executor.submit(procesar_cm, item): item for item in pares_nuevos.items()}
                    for future in as_completed(futures):
                        numero_completo, datos, numero_ce, num_re, error = future.result()
                        datos_cm[numero_completo] = datos
                        if error:
                            st.write(f"   ❌ {numero_ce}: {error}")
                        else:
                            # Guardar en caché solo si fue exitoso
                            cache_cm[numero_completo] = datos
                            st.write(f"   ✅ {numero_ce} → RE: {num_re} | {len(datos.get('items', []))} ítems")

        # ── 9. Validaciones de campos del DI (con factura/CM ya resueltos) ──
        st.write("🔎 Validando campos del DI...")
        # Configuración (Empresa/CUIT, Régimen, Aduana, Despachante)
        # seleccionada en pantalla vs lo declarado en la Carátula del DI —
        # útil para detectar si se está analizando el despacho equivocado
        # cuando se usa este corrector con otros importadores/aduanas.
        todos_resultados.extend(validar_config_vs_caratula(caratula, config))
        todos_resultados.extend(validar_items(df_items, df_subitems, df_caratula, datos_cm, datos_facturas))
        todos_resultados.extend(validar_subitems(df_subitems, df_items, df_caratula, datos_cm, datos_facturas))
        todos_resultados.extend(validar_prorrateo(df_items, fob_total, flete_total_di, seguro_total_di, df_subitems, df_caratula, datos_cm, datos_facturas))
        # Ítems usados: identificación con código/factura para revisión
        # puntual (ej. confirmar CORE DEPOSIT contemplado en el FOB).
        # No depende de la liquidación, corre siempre.
        todos_resultados.extend(validar_items_usados(df_items, df_subitems, df_caratula, datos_cm, datos_facturas))
        # Campos de control declarativos (Items): valores fijos esperados,
        # opcionales u obligatorios según el campo. No depende de la
        # liquidación, corre siempre.
        todos_resultados.extend(validar_campos_control(df_items, df_subitems, df_caratula, datos_cm, datos_facturas))
        if not df_liq.empty:
            resultados_liquidacion = validar_liquidacion(df_liq, df_items, df_subitems, df_caratula, datos_cm, datos_facturas)
            todos_resultados.extend(resultados_liquidacion)
            todos_resultados.extend(validar_resumen_liquidacion(resultados_liquidacion, len(df_items)))
            # Dumping: consistencia entre marca (CATERPILLAR/CUMMINS/DEUTZ
            # exceptúan el pago), DJ de Origen No Preferencial (exime del
            # pago si está declarada) y lo efectivamente liquidado. Corre
            # junto a Liquidación porque necesita la misma solapa (df_liq).
            todos_resultados.extend(validar_dumping_marca_dj(df_items, df_subitems, df_liq, df_caratula, datos_cm, datos_facturas))
        resultados_ncm_excel = []
        if df_ncm is not None:
            resultados_ncm_excel = validar_ncm_excel(df_subitems, df_ncm, df_items, df_caratula, datos_cm, datos_facturas)
            todos_resultados.extend(resultados_ncm_excel)
        st.write(f"   ✅ {len(todos_resultados)} resultados")

        # ── 10. Cruces ────────────────────────────────────────────────────
        st.write("🔀 Cruzando datos...")
        resultados_cm_vs_di = []
        if datos_cm:
            resultados_cm_vs_di = validar_cm_vs_di(df_items, df_subitems, datos_cm)
            todos_resultados.extend(resultados_cm_vs_di)
        resultados_factura_vs_di = []
        if datos_facturas:
            resultados_factura_vs_di = validar_factura_vs_di(df_items, df_subitems, datos_facturas, df_ncm)
            todos_resultados.extend(resultados_factura_vs_di)
            todos_resultados.extend(validar_caratula_totales(caratula, datos_facturas, datos_forwarding, resultados_factura_vs_di))
        if datos_forwarding or datos_bl:
            todos_resultados.extend(validar_caratula_vs_docs(caratula, datos_forwarding, datos_bl, datos_facturas, config))
        resultados_dj_origen = []
        if datos_dj:
            resultados_dj_origen = validar_dj_origen(df_items, df_subitems, datos_dj)
            todos_resultados.extend(resultados_dj_origen)
        if datos_bl and "error" not in datos_bl:
            todos_resultados.extend(validar_bultos_vs_bl(df_bultos, datos_bl))
        if df_caratula is not None and (datos_facturas or datos_forwarding):
            todos_resultados.extend(validar_documentos_declarados(df_caratula, datos_facturas, datos_forwarding))
        # Resúmenes generales de CM, DJ e ítems: corren siempre que el DI
        # declare algo en esos campos, aunque no se haya subido ningún
        # documento (para poder informar el caso "declarado pero no
        # subido"). Sus filas llevan "es_resumen": True y son exclusivas
        # de la pestaña Revisión General — no se duplican en Errores/
        # Alertas/OK, donde vive el detalle real de cada cruce.
        todos_resultados.extend(validar_resumen_cm(df_items, datos_cm, resultados_cm_vs_di))
        todos_resultados.extend(validar_resumen_dj_origen(df_items, datos_dj, resultados_dj_origen))
        todos_resultados.extend(validar_resumen_items(resultados_cm_vs_di, resultados_factura_vs_di, len(df_items), resultados_ncm_excel))

        status.update(label="✅ Análisis completado", state="complete")

    # Armar resumen de documentos procesados
    docs_procesados = {}
    docs_procesados["Excel DI"] = {"ok": True, "detalle": f"{len(df_items)} ítems leídos"}

    if ce_files:
        cms_ok = [k for k,v in datos_cm.items() if "error" not in v]
        cms_err = [k for k,v in datos_cm.items() if "error" in v]
        det = f"{len(cms_ok)} CM(s): {', '.join(cms_ok)}" if cms_ok else "0 CM(s) procesados"
        if cms_err: det += f" | Con error: {', '.join(cms_err)}"
        docs_procesados["Certificados Mineros"] = {"ok": len(cms_err)==0, "detalle": det}
    else:
        docs_procesados["Certificados Mineros"] = {"ok": False, "detalle": "No se subieron CMs"}

    if datos_forwarding and "error" not in datos_forwarding:
        nro_inv = datos_forwarding.get("numero_invoice", "").strip()
        ref_fwd = nro_inv if nro_inv else (forwarding_file.name if forwarding_file else "")
        docs_procesados["Forwarding Invoice"] = {"ok": True, "detalle": f"Invoice: {ref_fwd} | Flete: {datos_forwarding.get('flete_total')} | Seguro: {datos_forwarding.get('seguro_total')}"}
    else:
        docs_procesados["Forwarding Invoice"] = {"ok": False, "detalle": "No procesada"}

    if datos_bl and "error" not in datos_bl:
        docs_procesados["Bill of Lading"] = {"ok": True, "detalle": f"BL: {datos_bl.get('bl_number')} | Embarque: {datos_bl.get('fecha_embarque')}"}
    else:
        docs_procesados["Bill of Lading"] = {"ok": False, "detalle": "No procesado"}

    if datos_facturas:
        facs_ok_nums = [v.get("numero_factura", k) for k, v in datos_facturas.items() if "error" not in v]
        facs_err = [k for k, v in datos_facturas.items() if "error" in v]
        det = f"{len(facs_ok_nums)} factura(s): {', '.join(facs_ok_nums)}" if facs_ok_nums else "0 facturas procesadas"
        if facs_err: det += f" | Con error: {', '.join(facs_err)}"
        docs_procesados["Facturas"] = {"ok": len(facs_ok_nums) > 0, "detalle": det}
    else:
        docs_procesados["Facturas"] = {"ok": False, "detalle": "No se subieron facturas"}

    if datos_dj:
        dj_ok_nums = [d.get("numero_if", "?") for d in datos_dj if "error" not in d]
        dj_err = sum(1 for d in datos_dj if "error" in d)
        det = f"{len(dj_ok_nums)} DJ(s): {', '.join(dj_ok_nums)}" if dj_ok_nums else "0 DJ(s) procesadas"
        if dj_err: det += f" | {dj_err} con error"
        docs_procesados["DJ Origen No Preferencial"] = {"ok": dj_err == 0, "detalle": det}
    else:
        docs_procesados["DJ Origen No Preferencial"] = {"ok": False, "detalle": "No se subió DJ"}

    # Guardar en session_state para persistencia (ordenado por ítem para que
    # el reporte sea legible: todos los resultados de un mismo ítem quedan
    # juntos, en vez de mezclados según el orden interno de cada cruce).
    def _clave_orden(r):
        item = str(r.get("item", ""))
        # GENERAL va primero (panorama global del despacho: carátula,
        # bultos/BL, países prohibidos, etc.); los ítems numéricos
        # (zfill4) van después, ordenados como número. Ítems con múltiples
        # números (ej. "0001, 0002") se ordenan por el primero.
        primero = item.split(",")[0].strip()
        if primero.isdigit():
            return (1, int(primero))
        return (0, primero)

    todos_resultados = sorted(todos_resultados, key=_clave_orden)

    st.session_state["resultados"] = todos_resultados
    st.session_state["config"] = config
    st.session_state["docs_procesados"] = docs_procesados
    st.session_state["referencia"] = referencia
    st.session_state["numero_di"] = numero_di

# ─── MOSTRAR RESULTADOS (persisten tras descarga) ─────────────────────────────
if "resultados" in st.session_state:
    todos_resultados = st.session_state["resultados"]
    config_actual = st.session_state.get("config", config)
    docs_procesados = st.session_state.get("docs_procesados", {})

    # Revisión General: chequeos a nivel despacho completo (carátula, BL,
    # bultos, países prohibidos, facturas/vendedor declarados, CM, DJ
    # origen, ítems agrupados, etc.), no por ítem individual.
    #
    # Dos tipos de fila GENERAL:
    #   - "es_resumen": True -> generadas por validar_resumen_cm/dj/items.
    #     Ya traen su propio texto agrupado ("De los X declarados, Y OK",
    #     o "CM tal: ... — ver pestaña Errores"). Son EXCLUSIVAS de esta
    #     pestaña — nunca aparecen en Errores/Alertas/OK, para no
    #     duplicar el detalle real que generan validar_cm_vs_di(),
    #     validar_dj_origen() y validar_factura_vs_di().
    #   - Sin esa marca -> chequeos generales "directos" (países, FOB,
    #     bultos/BL, ITN, etc.) que no tienen una función de resumen
    #     dedicada. Sus OK se muestran completos en Revisión General; si
    #     hay ERROR/ALERTA, esta pestaña muestra solo un resumen por
    #     campo (generado automáticamente acá) y el detalle real vive en
    #     Errores/Alertas junto con los de cada ítem.
    es_general = lambda r: str(r.get("item", "")) == "GENERAL"
    es_resumen = lambda r: bool(r.get("es_resumen"))

    resultados_resumen = [r for r in todos_resultados if es_resumen(r)]
    resultados_resto = [r for r in todos_resultados if not es_resumen(r)]

    errores = [r for r in resultados_resto if r["nivel"] == "ERROR"]
    alertas_list = [r for r in resultados_resto if r["nivel"] == "ALERTA"]
    oks = [r for r in resultados_resto if r["nivel"] == "OK" and not es_general(r)]
    oks_generales = [r for r in resultados_resto if r["nivel"] == "OK" and es_general(r)]

    errores_generales = [r for r in errores if es_general(r)]
    alertas_generales = [r for r in alertas_list if es_general(r)]

    st.subheader("📊 Resumen")
    c1, c2, c3 = st.columns(3)
    with c1: st.metric("❌ Errores", len(errores))
    with c2: st.metric("⚠️ Alertas", len(alertas_list))
    with c3: st.metric("✅ OK", len(oks) + len(oks_generales))

    st.subheader("📋 Detalle")
    tabs = st.tabs(["🌐 Revisión General", "❌ Errores", "⚠️ Alertas", "✅ OK", "📄 Todo"])

    def mostrar(lista):
        if not lista:
            st.info("Sin resultados.")
            return
        df = pd.DataFrame(lista)[["item", "campo", "mensaje", "nivel"]]
        df.columns = ["Ítem", "Campo", "Mensaje", "Nivel"]
        st.dataframe(
            df,
            use_container_width=True,
            hide_index=True,
            column_config={
                "Ítem": st.column_config.TextColumn(width="small"),
                "Campo": st.column_config.TextColumn(width="medium"),
                # Mensaje suele incluir listas largas (facturas, CMs, etc.)
                # — ancho amplio para que no se corte el texto.
                "Mensaje": st.column_config.TextColumn(width="large"),
                "Nivel": st.column_config.TextColumn(width="small"),
            },
        )

    def _clave_item(r):
        # Mismo criterio usado al guardar los resultados: GENERAL al
        # principio, ítems numéricos ordenados como número.
        item = str(r.get("item", ""))
        primero = item.split(",")[0].strip()
        if primero.isdigit():
            return (1, int(primero))
        return (0, primero)

    # Campos informativos/declarativos de baja prioridad de lectura: van
    # siempre al final de la pestaña, después del resto de los campos
    # (que mantienen orden alfabético entre sí), ya que no es tan
    # relevante revisarlos ítem por ítem como Liquidación, FOB, etc.
    CAMPOS_BAJA_PRIORIDAD = {"I:DNRT-EXC-OPC", "I:AUTOPARTESEG-OPC", "I:DNRT-OPC"} | set(CAMPOS_INFORMATIVOS)

    def _clave_campo(campo: str):
        campo = str(campo)
        return (1, campo) if campo in CAMPOS_BAJA_PRIORIDAD else (0, campo)

    def mostrar_por_campo(lista):
        # Para Errores/Alertas: agrupa primero por Campo (para encontrar
        # rápido todas las observaciones de un mismo tipo, ej. todas las
        # de LIQUIDACIÓN juntas) y, dentro de cada campo, ordena por Ítem.
        # Los campos informativos de baja prioridad quedan al final.
        if not lista:
            st.info("Sin resultados.")
            return
        lista_ordenada = sorted(lista, key=lambda r: (_clave_campo(r.get("campo", "")), _clave_item(r)))
        mostrar(lista_ordenada)

    def mostrar_revision_general():
        # 1) Filas de resumen (CM/DJ/Ítems agrupados): ya vienen con su
        #    propio texto, se muestran tal cual, en todos sus niveles.
        filas = list(resultados_resumen)
        # 2) Chequeos generales directos: OK completos + resumen por
        #    campo para ERROR/ALERTA (igual que antes).
        filas.extend(oks_generales)
        if errores_generales or alertas_generales:
            campos_afectados = {}
            for r in errores_generales + alertas_generales:
                campos_afectados.setdefault(r["campo"], {"ERROR": 0, "ALERTA": 0})
                campos_afectados[r["campo"]][r["nivel"]] += 1

            # El campo GENERAL "FOB" (total del despacho) está relacionado
            # con las validaciones por ítem de MONTO FOB (FACTURA) y
            # MONTO FOB (CM) — si hay error en el total, conviene que el
            # resumen general también refleje cuántos ítems puntuales
            # tienen diferencia, para dar el panorama completo de una vez.
            if "FOB" in campos_afectados:
                campos_fob_item = {"MONTO FOB (FACTURA)", "MONTO FOB (CM)"}
                for r in errores + alertas_list:
                    if r["campo"] in campos_fob_item:
                        campos_afectados["FOB"][r["nivel"]] += 1

            for campo, cuenta in campos_afectados.items():
                partes = []
                if cuenta["ERROR"]:
                    partes.append(f"{cuenta['ERROR']} error(es)")
                if cuenta["ALERTA"]:
                    partes.append(f"{cuenta['ALERTA']} alerta(s)")
                pestana = "❌ Errores" if cuenta["ERROR"] else "⚠️ Alertas"
                nivel_resumen = "ERROR" if cuenta["ERROR"] else "ALERTA"
                filas.append({
                    "item": "GENERAL",
                    "campo": campo,
                    "mensaje": f"Hay {' y '.join(partes)} — ver pestaña \"{pestana}\"",
                    "nivel": nivel_resumen,
                })
        mostrar(filas)

    with tabs[0]: mostrar_revision_general()
    with tabs[1]: mostrar_por_campo(errores)
    with tabs[2]: mostrar_por_campo(alertas_list)
    with tabs[3]:
        st.success(f"✅ {len(oks)} validación(es) por ítem superadas correctamente. "
                   f"El detalle agrupado ya está en la pestaña 'Revisión General'.")
        with st.expander(f"Ver detalle completo de OK ({len(oks)} filas)", expanded=False):
            mostrar(oks)
    with tabs[4]: mostrar(todos_resultados)


    # ─── EXPORT ───────────────────────────────────────────────────────────────
    st.subheader("📥 Exportar reporte")

    incluir_detalle_ok = st.checkbox(
        "Incluir detalle completo de Validaciones correctas en el PDF",
        value=False,
        help="Por defecto el PDF muestra el resumen agrupado (Revisión General) y el detalle "
             "completo de Errores/Alertas, sin el detalle fila por fila de cada ítem OK. "
             "Marcá esto si querés revisar casos puntuales que dieron OK."
    )

    col_pdf, col_xlsx = st.columns(2)

    referencia_actual = st.session_state.get("referencia", "").strip()
    numero_di_actual = st.session_state.get("numero_di", "")
    # Sufijo del nombre de archivo: la Referencia indicada por el usuario,
    # tal cual la escribió (sin limpiar caracteres). Si no se indicó
    # ninguna, el archivo queda con el nombre genérico de siempre.
    sufijo_archivo = f"_{referencia_actual}" if referencia_actual else ""

    with col_pdf:
        try:
            pdf_bytes = generar_reporte_pdf(todos_resultados, config_actual, numero_di=numero_di_actual,
                docs_procesados=docs_procesados, incluir_detalle_ok=incluir_detalle_ok)
            st.download_button("📄 Descargar reporte PDF",
                data=pdf_bytes,
                file_name=f"reporte_corrector_FSM{sufijo_archivo}.pdf",
                mime="application/pdf",
                use_container_width=True,
                type="primary")
        except Exception as e:
            st.error(f"Error generando PDF: {e}")

    with col_xlsx:
        df_export = pd.DataFrame(todos_resultados)
        if not df_export.empty:
            df_export = df_export[["item", "campo", "mensaje", "nivel"]]
            df_export.columns = ["Ítem", "Campo", "Mensaje", "Nivel"]
            output = io.BytesIO()
            with pd.ExcelWriter(output, engine="openpyxl") as writer:
                df_export.to_excel(writer, index=False, sheet_name="Validaciones")
                df_export[df_export["Nivel"] == "ERROR"].to_excel(writer, index=False, sheet_name="Errores")
                df_export[df_export["Nivel"] == "ALERTA"].to_excel(writer, index=False, sheet_name="Alertas")
            st.download_button("📊 Descargar Excel",
                data=output.getvalue(),
                file_name=f"reporte_corrector_FSM{sufijo_archivo}.xlsx",
                mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                use_container_width=True)
