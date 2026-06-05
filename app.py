import streamlit as st
import pandas as pd
import io

from config.defaults import EMPRESAS, DESPACHANTE, CUIT_DESPACHANTE, REGIMENES, ADUANAS
from utils.parser_di import leer_di, safe_float
from utils.validaciones import validar_items, validar_subitems, validar_liquidacion, validar_prorrateo, validar_ncm_excel
from utils.extractor_api import extraer_factura, extraer_forwarding, extraer_bl, extraer_cm, extraer_dj_origen
from utils.cruce_docs import validar_cm_vs_di, validar_factura_vs_di, validar_caratula_vs_docs, validar_dj_origen
from utils.reporte_pdf import generar_reporte_pdf

st.set_page_config(page_title="Corrector FASA/FSM", page_icon="🔍", layout="wide")

st.markdown("""
<style>
.titulo { font-size: 1.8rem; font-weight: 700; color: #1F3864; margin-bottom: 0.2rem; }
.subtitulo { font-size: 1rem; color: #595959; margin-bottom: 1.5rem; }
</style>
""", unsafe_allow_html=True)

st.markdown('<div class="titulo">🔍 Corrector de Despachos FASA/FSM</div>', unsafe_allow_html=True)
st.markdown('<div class="subtitulo">Validación automática de despachos de importación — Finning Soluciones Mineras</div>', unsafe_allow_html=True)

# ─── SIDEBAR ─────────────────────────────────────────────────────────────────
with st.sidebar:
    st.header("⚙️ Configuración")
    empresa = st.selectbox("Empresa importadora", list(EMPRESAS.keys()))
    cuit_ie = EMPRESAS[empresa]
    st.caption(f"CUIT: {cuit_ie}")
    regimen = st.selectbox("Régimen", REGIMENES)
    aduana = st.selectbox("Aduana", ADUANAS)
    st.divider()
    st.caption(f"**Despachante:** {DESPACHANTE}")
    st.caption(f"**CUIT DA:** {CUIT_DESPACHANTE}")

config = {"empresa": empresa, "cuit_ie": cuit_ie, "regimen": regimen, "aduana": aduana}

# ─── DOCUMENTOS ──────────────────────────────────────────────────────────────
st.subheader("📁 Carga de documentos")

col1, col2 = st.columns(2)
with col1:
    di_file = st.file_uploader("📊 Excel del DI (Provisorio)", type=["xlsx", "xls"], key="di")
    facturas = st.file_uploader("🧾 Facturas comerciales (PDF)", type=["pdf"], accept_multiple_files=True, key="facturas")
    forwarding_file = st.file_uploader("🚢 Forwarding Invoice (PDF)", type=["pdf"], key="forwarding")

with col2:
    bl_file = st.file_uploader("📋 Bill of Lading (PDF)", type=["pdf"], key="bl")
    ncm_file = st.file_uploader("📑 Excel de clasificación NCM", type=["xlsx", "xls"], key="ncm",
        help="Excel con columnas PART_NUMBER y NCM para validar posiciones arancelarias")
    dj_origen_files = st.file_uploader("📄 DJ Origen No Preferencial (PDF)",
        type=["pdf"], accept_multiple_files=True, key="dj_origen",
        help="Requerida cuando hay campos de dumping declarados y origen = procedencia")

# ─── CERTIFICADOS MINEROS ─────────────────────────────────────────────────────
st.subheader("📜 Certificados Mineros (CM)")
st.info("Subí los archivos CE y RE juntos. Los nombres deben comenzar con CE- o RE-.", icon="ℹ️")

cm_files = st.file_uploader("Archivos CM (CE y RE en PDF)",
    type=["pdf"], accept_multiple_files=True, key="cms")

cm_grupos = {}
if cm_files:
    for f in cm_files:
        nombre = f.name.upper()
        tipo = "CE" if nombre.startswith("CE-") else "RE" if nombre.startswith("RE-") else None
        numero = nombre.split("_")[0].replace(".PDF", "") if tipo else None
        if tipo and numero:
            if numero not in cm_grupos:
                cm_grupos[numero] = {}
            cm_grupos[numero][tipo] = f
        else:
            st.warning(f"No se pudo identificar '{f.name}' como CE o RE")
    if cm_grupos:
        st.caption(f"CMs detectados: {', '.join(cm_grupos.keys())}")

# ─── ANALIZAR ─────────────────────────────────────────────────────────────────
st.divider()
analizar = st.button("🔍 Analizar Despacho", type="primary", use_container_width=True)

if analizar:
    if not di_file:
        st.error("⚠️ Cargá el Excel del DI para continuar.")
        st.stop()

    todos_resultados = []

    with st.status("Analizando despacho...", expanded=True) as status:

        # ── 1. Parsear DI ─────────────────────────────────────────────────
        st.write("📊 Leyendo Excel del DI...")
        try:
            di_data = leer_di(di_file)
            df_items = di_data.get("items", pd.DataFrame())
            df_subitems = di_data.get("subitems", pd.DataFrame())
            df_liq = di_data.get("liquidacion", pd.DataFrame())
            caratula = di_data.get("caratula", {})
            st.write(f"   ✅ {len(df_items)} ítems leídos")
        except Exception as e:
            st.error(f"Error leyendo el DI: {e}")
            st.stop()

        # ── 2. Validaciones sin API ───────────────────────────────────────
        st.write("🔎 Validando campos del DI...")
        todos_resultados.extend(validar_items(df_items))
        todos_resultados.extend(validar_subitems(df_subitems))

        fob_total = df_items["VALOR FOB"].apply(safe_float).sum() if "VALOR FOB" in df_items.columns else 0
        flete_total_di = df_items["FLETE EN DIV"].apply(safe_float).sum() if "FLETE EN DIV" in df_items.columns else 0
        seguro_total_di = df_items["SEGURO EN DIV"].apply(safe_float).sum() if "SEGURO EN DIV" in df_items.columns else 0

        todos_resultados.extend(validar_prorrateo(df_items, fob_total, flete_total_di, seguro_total_di))

        if not df_liq.empty:
            todos_resultados.extend(validar_liquidacion(df_liq, df_items, df_subitems))

        st.write(f"   ✅ Validaciones locales: {len(todos_resultados)} resultados")

        # ── 3. Excel NCM ──────────────────────────────────────────────────
        if ncm_file:
            st.write("📑 Validando NCM contra Excel de clasificación...")
            try:
                df_ncm = pd.read_excel(ncm_file, dtype=str)
                res_ncm = validar_ncm_excel(df_subitems, df_ncm)
                todos_resultados.extend(res_ncm)
                errores_ncm = len([r for r in res_ncm if r["nivel"] == "ERROR"])
                st.write(f"   ✅ NCM validados: {errores_ncm} inconsistencias encontradas")
            except Exception as e:
                st.write(f"   ❌ Error leyendo Excel NCM: {e}")

        # ── 4. API: Facturas ──────────────────────────────────────────────
        datos_facturas = {}
        if facturas:
            st.write(f"🧾 Extrayendo {len(facturas)} factura(s)...")
            for fac in facturas:
                try:
                    datos = extraer_factura(fac.read())
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

        # ── 8. API: CMs ───────────────────────────────────────────────────
        datos_cm = {}
        if cm_grupos:
            st.write(f"📜 Extrayendo {len(cm_grupos)} CM(s)...")
            for numero_cm, archivos in cm_grupos.items():
                if "CE" in archivos and "RE" in archivos:
                    try:
                        datos = extraer_cm(archivos["CE"].read(), archivos["RE"].read())
                        # Normalizar match: extraer número central del CM
                        import re as _re
                        pat = _re.compile(r"[0-9]{8,}")
                        def _extraer_num(s):
                            m = pat.search(s)
                            return m.group(0) if m else s
                        num_archivo = _extraer_num(numero_cm)
                        numero_completo = next(
                            (v for v in df_items["D:CERTSM"].unique() 
                             if _extraer_num(v.upper()) == num_archivo),
                            numero_cm
                        )
                        datos_cm[numero_completo] = datos
                        st.write(f"   ✅ {numero_cm}: {len(datos.get('items', []))} ítems")
                    except Exception as e:
                        datos_cm[numero_cm] = {"error": str(e)}
                        st.write(f"   ❌ {numero_cm}: {e}")
                else:
                    faltante = "RE" if "CE" in archivos else "CE"
                    st.write(f"   ⚠️ {numero_cm}: Falta el {faltante}")

        # ── 9. Cruces ─────────────────────────────────────────────────────
        st.write("🔀 Cruzando datos...")
        if datos_cm:
            todos_resultados.extend(validar_cm_vs_di(df_items, df_subitems, datos_cm))
        if datos_facturas:
            todos_resultados.extend(validar_factura_vs_di(df_items, df_subitems, datos_facturas))
        if datos_forwarding or datos_bl:
            todos_resultados.extend(validar_caratula_vs_docs(caratula, datos_forwarding, datos_bl, datos_facturas, config))
        if datos_dj:
            todos_resultados.extend(validar_dj_origen(df_items, df_subitems, datos_dj))

        status.update(label="✅ Análisis completado", state="complete")

    # ─── RESUMEN ──────────────────────────────────────────────────────────────
    errores = [r for r in todos_resultados if r["nivel"] == "ERROR"]
    alertas_list = [r for r in todos_resultados if r["nivel"] == "ALERTA"]
    oks = [r for r in todos_resultados if r["nivel"] == "OK"]

    st.subheader("📊 Resumen")
    c1, c2, c3 = st.columns(3)
    with c1: st.metric("❌ Errores", len(errores))
    with c2: st.metric("⚠️ Alertas", len(alertas_list))
    with c3: st.metric("✅ OK", len(oks))

    st.subheader("📋 Detalle")
    tabs = st.tabs(["❌ Errores", "⚠️ Alertas", "✅ OK", "📄 Todo"])

    def mostrar(lista):
        if not lista:
            st.info("Sin resultados.")
            return
        df = pd.DataFrame(lista)
        df.columns = ["Ítem", "Campo", "Mensaje", "Nivel"]
        st.dataframe(df, use_container_width=True, hide_index=True)

    with tabs[0]: mostrar(errores)
    with tabs[1]: mostrar(alertas_list)
    with tabs[2]: mostrar(oks)
    with tabs[3]: mostrar(todos_resultados)

    # ─── EXPORT ───────────────────────────────────────────────────────────────
    st.subheader("📥 Exportar reporte")
    col_pdf, col_xlsx = st.columns(2)

    # PDF profesional
    with col_pdf:
        try:
            pdf_bytes = generar_reporte_pdf(todos_resultados, config)
            st.download_button(
                "📄 Descargar reporte PDF",
                data=pdf_bytes,
                file_name="reporte_corrector_FSM.pdf",
                mime="application/pdf",
                use_container_width=True,
                type="primary"
            )
        except Exception as e:
            st.error(f"Error generando PDF: {e}")

    # Excel de respaldo
    with col_xlsx:
        df_export = pd.DataFrame(todos_resultados)
        if not df_export.empty:
            df_export.columns = ["Ítem", "Campo", "Mensaje", "Nivel"]
            output = io.BytesIO()
            with pd.ExcelWriter(output, engine="openpyxl") as writer:
                df_export.to_excel(writer, index=False, sheet_name="Validaciones")
                df_export[df_export["Nivel"] == "ERROR"].to_excel(writer, index=False, sheet_name="Errores")
                df_export[df_export["Nivel"] == "ALERTA"].to_excel(writer, index=False, sheet_name="Alertas")
            st.download_button(
                "📊 Descargar Excel",
                data=output.getvalue(),
                file_name="reporte_corrector_FSM.xlsx",
                mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                use_container_width=True
            )
