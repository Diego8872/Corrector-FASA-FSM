import streamlit as st
import pandas as pd
import io
from collections import defaultdict

from config.defaults import EMPRESAS, DESPACHANTE, CUIT_DESPACHANTE, REGIMENES, ADUANAS
from utils.parser_di import leer_di, safe_float
from utils.validaciones import validar_items, validar_subitems, validar_liquidacion, validar_prorrateo
from utils.extractor_api import extraer_factura, extraer_forwarding, extraer_bl, extraer_cm, extraer_dj_origen, extraer_numero_re_de_ce
from utils.cruce_docs import validar_cm_vs_di, validar_factura_vs_di, validar_caratula_vs_docs, validar_dj_origen

st.set_page_config(
    page_title="Corrector de Despachos FSM",
    page_icon="🔍",
    layout="wide"
)

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
    st.header("⚙️ Configuración del Despacho")
    empresa = st.selectbox("Empresa importadora", list(EMPRESAS.keys()))
    cuit_ie = EMPRESAS[empresa]
    st.caption(f"CUIT: {cuit_ie}")
    regimen = st.selectbox("Régimen", REGIMENES)
    aduana = st.selectbox("Aduana", ADUANAS)
    st.divider()
    st.caption(f"**Despachante:** {DESPACHANTE}")
    st.caption(f"**CUIT DA:** {CUIT_DESPACHANTE}")

config = {"empresa": empresa, "cuit_ie": cuit_ie, "regimen": regimen, "aduana": aduana}

# ─── CARGA DE DOCUMENTOS ─────────────────────────────────────────────────────
st.subheader("📁 Carga de documentos")

col1, col2 = st.columns(2)
with col1:
    di_file = st.file_uploader("📊 Excel del DI (Provisorio)", type=["xlsx", "xls"], key="di")
    facturas = st.file_uploader("🧾 Facturas comerciales (PDF)", type=["pdf"], accept_multiple_files=True, key="facturas")
    forwarding_file = st.file_uploader("🚢 Forwarding Invoice (PDF)", type=["pdf"], key="forwarding")

with col2:
    bl_file = st.file_uploader("📋 Bill of Lading (PDF)", type=["pdf"], key="bl")
    ncm_file = st.file_uploader("📑 Excel de NCM por artículo", type=["xlsx", "xls"], key="ncm")
    dj_origen_files = st.file_uploader(
        "📄 DJ Origen No Preferencial (PDF)",
        type=["pdf"],
        accept_multiple_files=True,
        key="dj_origen",
        help="Declaración Jurada requerida cuando origen = procedencia. Subí uno o más PDFs con número IF."
    )

# ─── CERTIFICADOS MINEROS ────────────────────────────────────────────────────
st.subheader("📜 Certificados Mineros (CM)")
st.info("Para cada CM subí el CE y el RE. Los archivos deben comenzar con CE- o RE- seguido del número.", icon="ℹ️")

cm_files = st.file_uploader(
    "Archivos de CM (CE y RE en PDF — podés subir varios)",
    type=["pdf"],
    accept_multiple_files=True,
    key="cms"
)

# Separar CEs y REs
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

cm_grupos = {}  # se construye durante el análisis con API

# ─── BOTÓN ANALIZAR ──────────────────────────────────────────────────────────
st.divider()
analizar = st.button("🔍 Analizar Despacho", type="primary", use_container_width=True)

if analizar:
    if not di_file:
        st.error("⚠️ Cargá el Excel del DI para continuar.")
        st.stop()

    todos_resultados = []

    with st.status("Analizando despacho...", expanded=True) as status:

        # ── 1. Parsear DI ──────────────────────────────────────────────────
        st.write("📊 Leyendo Excel del DI...")
        try:
            di_data = leer_di(di_file)
            df_items = di_data.get("items", pd.DataFrame())
            df_subitems = di_data.get("subitems", pd.DataFrame())
            df_liq = di_data.get("liquidacion", pd.DataFrame())
            df_bultos = di_data.get("bultos", pd.DataFrame())
            caratula = di_data.get("caratula", {})
            st.write(f"   ✅ DI leído: {len(df_items)} ítems")
        except Exception as e:
            st.error(f"Error leyendo el DI: {e}")
            st.stop()

        # ── 2. Validaciones sin API ────────────────────────────────────────
        st.write("🔎 Validando campos del DI...")
        res_items = validar_items(df_items)
        res_sub = validar_subitems(df_subitems)

        fob_total = df_items["VALOR FOB"].apply(safe_float).sum() if "VALOR FOB" in df_items.columns else 0
        flete_total_di = df_items["FLETE EN DIV"].apply(safe_float).sum() if "FLETE EN DIV" in df_items.columns else 0
        seguro_total_di = df_items["SEGURO EN DIV"].apply(safe_float).sum() if "SEGURO EN DIV" in df_items.columns else 0

        res_prorrateo = validar_prorrateo(df_items, fob_total, flete_total_di, seguro_total_di)
        res_liq = validar_liquidacion(df_liq, df_items, df_subitems) if not df_liq.empty else []

        todos_resultados.extend(res_items + res_sub + res_prorrateo + res_liq)
        st.write(f"   ✅ Validaciones locales OK: {len(todos_resultados)} resultados")

        # ── 3. API: Facturas ───────────────────────────────────────────────
        datos_facturas = {}
        if facturas:
            st.write(f"🧾 Extrayendo {len(facturas)} factura(s) con IA...")
            for fac in facturas:
                try:
                    datos = extraer_factura(fac.read())
                    datos_facturas[fac.name] = datos
                    st.write(f"   ✅ {fac.name}: {len(datos.get('items', []))} ítems")
                except Exception as e:
                    datos_facturas[fac.name] = {"error": str(e)}
                    st.write(f"   ❌ {fac.name}: {e}")

        # ── 4. API: Forwarding Invoice ─────────────────────────────────────
        datos_forwarding = {}
        if forwarding_file:
            st.write("🚢 Extrayendo Forwarding Invoice con IA...")
            try:
                datos_forwarding = extraer_forwarding(forwarding_file.read())
                st.write(f"   ✅ Flete: {datos_forwarding.get('flete_total', '?')} | Seguro: {datos_forwarding.get('seguro_total', '?')}")
            except Exception as e:
                datos_forwarding = {"error": str(e)}
                st.write(f"   ❌ Error: {e}")

        # ── 5. API: BL ─────────────────────────────────────────────────────
        datos_bl = {}
        if bl_file:
            st.write("📋 Extrayendo Bill of Lading con IA...")
            try:
                datos_bl = extraer_bl(bl_file.read())
                st.write(f"   ✅ BL: {datos_bl.get('bl_number', '?')} | Fecha: {datos_bl.get('fecha_embarque', '?')}")
            except Exception as e:
                datos_bl = {"error": str(e)}
                st.write(f"   ❌ Error: {e}")

        # ── 6. API: DJ Origen No Preferencial ─────────────────────────────
        datos_dj_origen = []
        if dj_origen_files:
            st.write(f"📄 Extrayendo {len(dj_origen_files)} DJ(s) de origen con IA...")
            for dj_file in dj_origen_files:
                try:
                    datos = extraer_dj_origen(dj_file.read())
                    datos_dj_origen.append(datos)
                    st.write(f"   ✅ {dj_file.name}: IF={datos.get('numero_if', '?')}")
                except Exception as e:
                    datos_dj_origen.append({"error": str(e), "archivo": dj_file.name})
                    st.write(f"   ❌ {dj_file.name}: {e}")

        # ── 7. API: CMs ────────────────────────────────────────────────────
        datos_cm = {}
        if cm_grupos:
            st.write(f"📜 Extrayendo {len(cm_grupos)} CM(s) con IA...")
            for numero_cm, archivos in cm_grupos.items():
                if "CE" in archivos and "RE" in archivos:
                    try:
                        ce_bytes = archivos["CE"].read()
                        re_bytes = archivos["RE"].read()
                        datos = extraer_cm(ce_bytes, re_bytes)
                        numero_completo = next(
                            (v for v in df_items["D:CERTSM"].unique() if numero_cm in v.upper()),
                            numero_cm
                        )
                        datos_cm[numero_completo] = datos
                        st.write(f"   ✅ {numero_cm}: {len(datos.get('items', []))} ítems")
                    except Exception as e:
                        datos_cm[numero_cm] = {"error": str(e)}
                        st.write(f"   ❌ {numero_cm}: {e}")
                else:
                    faltante = "RE" if "CE" in archivos else "CE"
                    st.write(f"   ⚠️ {numero_cm}: Falta el archivo {faltante}")

        # ── 8. Cruces contra documentos ────────────────────────────────────
        st.write("🔀 Cruzando datos contra documentos...")

        if datos_cm:
            todos_resultados.extend(validar_cm_vs_di(df_items, df_subitems, datos_cm))

        if datos_facturas:
            todos_resultados.extend(validar_factura_vs_di(df_items, df_subitems, datos_facturas))

        if datos_forwarding or datos_bl:
            todos_resultados.extend(validar_caratula_vs_docs(caratula, datos_forwarding, datos_bl, datos_facturas, config))

        if datos_dj_origen:
            todos_resultados.extend(validar_dj_origen(df_items, datos_dj_origen))

        status.update(label="✅ Análisis completado", state="complete")

    # ─── RESUMEN ─────────────────────────────────────────────────────────────
    errores = [r for r in todos_resultados if r["nivel"] == "ERROR"]
    alertas = [r for r in todos_resultados if r["nivel"] == "ALERTA"]
    oks = [r for r in todos_resultados if r["nivel"] == "OK"]

    st.subheader("📊 Resumen del análisis")
    c1, c2, c3 = st.columns(3)
    with c1:
        st.metric("❌ Errores", len(errores))
    with c2:
        st.metric("⚠️ Alertas", len(alertas))
    with c3:
        st.metric("✅ OK", len(oks))

    # ─── DETALLE ─────────────────────────────────────────────────────────────
    st.subheader("📋 Detalle de validaciones")
    tabs = st.tabs(["❌ Errores", "⚠️ Alertas", "✅ OK", "📄 Todo"])

    def mostrar_tabla(lista):
        if not lista:
            st.info("Sin resultados en esta categoría.")
            return
        df = pd.DataFrame(lista)
        df.columns = ["Ítem", "Campo", "Mensaje", "Nivel"]
        st.dataframe(df, use_container_width=True, hide_index=True)

    with tabs[0]: mostrar_tabla(errores)
    with tabs[1]: mostrar_tabla(alertas)
    with tabs[2]: mostrar_tabla(oks)
    with tabs[3]: mostrar_tabla(todos_resultados)

    # ─── EXPORT ──────────────────────────────────────────────────────────────
    df_export = pd.DataFrame(todos_resultados)
    if not df_export.empty:
        df_export.columns = ["Ítem", "Campo", "Mensaje", "Nivel"]
        output = io.BytesIO()
        with pd.ExcelWriter(output, engine="openpyxl") as writer:
            df_export.to_excel(writer, index=False, sheet_name="Validaciones")
            df_export[df_export["Nivel"] == "ERROR"].to_excel(writer, index=False, sheet_name="Errores")
            df_export[df_export["Nivel"] == "ALERTA"].to_excel(writer, index=False, sheet_name="Alertas")
        st.download_button(
            "📥 Descargar reporte Excel",
            data=output.getvalue(),
            file_name="reporte_corrector_FSM.xlsx",
            mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            use_container_width=True
        )
