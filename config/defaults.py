EMPRESAS = {
    "FINNING SOLUCIONES MINERAS SA": "30-68153032-7",
    "FINNING ARGENTINA SOCIEDAD ANO": "30-64722711-9",
}
DESPACHANTE = "MINOYETTI FEDERICO"
CUIT_DESPACHANTE = "20-22824212-9"
REGIMENES = ["IC04", "IC06", "IDA4", "OTRO"]
# Aduanas: nombre -> código numérico tal como figura en la solapa
# Carátula del DI (columna ADUANA, ej. "001 - BS.AS.(CAPITAL)"). El
# código se usa para validar, por substring, que la aduana seleccionada
# en pantalla coincida con la declarada en el DI.
ADUANAS = {
    "BS.AS.(CAPITAL)": "001",
    "EZEIZA": "073",
    "CAMPANA": "008",
}
BANCO_ARGENTINA = "016"
IMPOGIRO = "CGDDIF"
PAISES_PROHIBIDOS = [
    "COREA DEMOCRATICA",
    "COREA DEL NORTE",
    "IRAN",
    "SIRIA",
    "CUBA",
    "CRIMEA",
    "DONETSK",
    "LUHANSK",
    "KERSON",
    "JERSON",
    "ZAPORIYIA",
    "ZAPORIZHZHIA",
]
# Conceptos esperados en liquidación ítem CON CM
CONCEPTOS_CON_CM = {
    "032": {"nombre": "032 - TASA LEY 24196", "porcentaje": 1.0},
    "415": {"nombre": "415 - I.V.A.", "porcentaje": 21.0},
    # 900 - INGRESOS BRUTOS: excluido de la validación para ítems CON CM
}
# Concepto que NO debe aparecer en ítems SIN CM
CONCEPTO_SIN_CM_PROHIBIDO = "032"
# Concepto para ítems USADOS
CONCEPTO_USADO = "056"
# Palabras clave para detectar dumping en liquidación
KEYWORDS_DUMPING = ["DUMP", "ANTIDUM", "060"]  # "DUMP" cubre "DUMPING"/"D.A-DUMP DERESP" (051); "ANTIDUM" cubre "D.ANTIDUM.AD-VALOR" (058)
# Tolerancia de redondeo para comparación de valores FOB
TOLERANCIA_FOB = 0.05
