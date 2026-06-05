# 🔍 Corrector FASA/FSM

Herramienta de validación automática de despachos de importación para **Finning Soluciones Mineras** y **Finning Argentina**, desarrollada por INTERLOG Comercio Exterior.

---

## ¿Qué hace?

Cruza el Excel del despacho provisorio contra la documentación de importación (facturas, certificados mineros, BL, forwarding invoice) y detecta inconsistencias, errores y alertas antes de la oficialización.

### Validaciones incluidas

- **Carátula:** FOB, flete, seguro, ITN, fecha de embarque, número de BL, banco, IMPOGIRO
- **Solapa Item:** estado, origen/procedencia, países prohibidos, campos CM, dumping, valores fijos obligatorios
- **Solapa Subitem:** marca, código de producto, cantidad, FOB por ítem
- **Liquidación ítem:** conceptos obligatorios (032, 415, 900), porcentajes, bases imponibles, ítems usados
- **Cruce CM vs DI:** NCM, código de parte, cantidad y FOB contra el RE del certificado minero
- **Cruce factura vs DI:** FOB por ítem con lógica de prorrateo de cargos
- **Forwarding Invoice:** flete total y seguro (Marine + War Premium)
- **Bill of Lading:** número de BL y fecha de embarque

---

## Estructura del proyecto

```
Corrector-FASA-FSM/
├── app.py                    # App principal Streamlit
├── requirements.txt
├── README.md
├── .gitignore
├── .streamlit/
│   └── secrets.toml         # API key (NO subir a GitHub)
├── config/
│   └── defaults.py          # Configuración: empresas, países prohibidos, conceptos
└── utils/
    ├── parser_di.py          # Lectura y parseo del Excel del DI
    ├── validaciones.py       # Validaciones sin API
    ├── extractor_api.py      # Extracción de PDFs con Claude API
    └── cruce_docs.py         # Cruce CM vs DI y factura vs DI
```

---

## Instalación local

```bash
# Clonar el repo
git clone https://github.com/Diego8872/Corrector-FASA-FSM.git
cd Corrector-FASA-FSM

# Instalar dependencias
pip install -r requirements.txt

# Configurar API key
mkdir -p .streamlit
echo 'ANTHROPIC_API_KEY = "sk-ant-..."' > .streamlit/secrets.toml

# Correr la app
streamlit run app.py
```

---

## Deploy en Streamlit Cloud

1. Ir a [share.streamlit.io](https://share.streamlit.io)
2. Conectar el repo `Diego8872/Corrector-FASA-FSM`
3. Archivo principal: `app.py`
4. En **Settings → Secrets** pegar:

```toml
ANTHROPIC_API_KEY = "sk-ant-TU_API_KEY_AQUI"
```

5. Click en **Deploy**

---

## Uso

### Documentos a cargar

| Documento | Formato | Obligatorio |
|---|---|---|
| Excel del DI (provisorio) | `.xlsx` | ✅ |
| Facturas comerciales | `.pdf` (múltiples) | Recomendado |
| Forwarding Invoice | `.pdf` | Recomendado |
| Bill of Lading | `.pdf` | Recomendado |
| Certificados Mineros (CE + RE) | `.pdf` (múltiples) | Recomendado |
| Excel de NCM por artículo | `.xlsx` | Opcional |

### Nomenclatura de archivos CM

Para que la app identifique automáticamente el CE y el RE de cada CM, los archivos deben comenzar con el número del documento:

```
CE-2026-51445786-APN-DIMI_23MEC.pdf   → detectado como CE
RE-2026-46781596-APN-DGDA_23MEC.pdf   → detectado como RE
```

### Configuración del despacho

En el panel lateral seleccionar:
- **Empresa importadora** (Finning Soluciones Mineras SA / Finning Argentina)
- **Régimen** (IC04 / OTRO)
- **Aduana** (BS.AS. Capital / Ezeiza / OTRO)

---

## Reporte de resultados

| Nivel | Descripción |
|---|---|
| ❌ ERROR | Inconsistencia crítica que debe corregirse antes de oficializar |
| ⚠️ ALERTA | Dato a verificar — puede ser correcto según el caso |
| ✅ OK | Campo validado correctamente |

El reporte se exporta en Excel con tres solapas: Todas las validaciones, Errores y Alertas.

---

## Tecnologías

- [Streamlit](https://streamlit.io) — interfaz web
- [Anthropic Claude](https://anthropic.com) — extracción de datos de PDFs (claude-sonnet-4)
- [Pandas](https://pandas.pydata.org) — procesamiento del Excel del DI
- [OpenPyXL](https://openpyxl.readthedocs.io) — lectura de archivos Excel

---

## Desarrollado por

**INTERLOG Comercio Exterior**  
Portal Grupo 8 — Herramientas internas de gestión aduanera
