import streamlit as st
import anthropic
import pdfplumber
import pandas as pd
import io

st.set_page_config(page_title="Mi Asesor Financiero", page_icon="💰", layout="centered")
st.title("💰 Mi Asesor Financiero")
st.caption("Subí tus extractos de Santander y chateá con tus datos.")

with st.sidebar:
    st.header("Configuración")
    api_key = st.text_input("API Key de Anthropic", type="password", help="console.anthropic.com")
    st.markdown("---")
    st.header("Extractos")
    uploaded_files = st.file_uploader(
        "Subí PDFs o Excel de Santander",
        type=["pdf", "xlsx", "xls"],
        accept_multiple_files=True,
    )
    st.markdown("---")
    if st.button("🗑️ Limpiar conversación"):
        st.session_state.messages = []
        st.rerun()

SKIP_KEYWORDS = [
    "legales", "intercambio de información ocde", "seguro sobre saldo deudor",
    "acuerdo de giro en descubierto", "fondos comunes de inversión",
    "así usaste tu dinero", "ley 24.485", "superintendencia de seguros",
    "operá con seguridad", "llamanos al 0810"
]

def extract_pdf(f):
    pages = []
    with pdfplumber.open(io.BytesIO(f.read())) as pdf:
        for page in pdf.pages:
            text = page.extract_text()
            if not text:
                continue
            if any(kw in text.lower() for kw in SKIP_KEYWORDS):
                continue
            pages.append(text)
    return "\n".join(pages)

def extract_excel(f):
    xl = pd.ExcelFile(io.BytesIO(f.read()))
    parts = []
    for sheet in xl.sheet_names:
        df = xl.parse(sheet)
        parts.append(f"Hoja: {sheet}\n{df.to_string(index=False)}")
    return "\n\n".join(parts)

def extract_all(files):
    results = []
    for f in files:
        name = f.name.lower()
        if name.endswith(".pdf"):
            text = extract_pdf(f)
        elif name.endswith((".xlsx", ".xls")):
            text = extract_excel(f)
        else:
            text = "(formato no soportado)"
        results.append(f"=== {f.name} ===\n{text}")
    return "\n\n".join(results)

SYSTEM = """Sos un asesor financiero personal en español rioplatense.
Analizás extractos bancarios de Santander Argentina y respondés preguntas sobre gastos, ingresos, transferencias, inversiones y patrones de consumo.

Reglas:
- Respondés siempre en español argentino
- Usás pesos con puntos de miles: $1.500.000
- Sos directo y concreto — citás números reales
- Incluís transferencias, préstamos y retiros de efectivo en el análisis, no solo compras
- Si algo no está en los datos, lo decís claramente
- Para inversiones, ofrecés perspectivas pero aclarás que no sos asesor regulado
- Detectás patrones y anomalías de forma proactiva

EXTRACTOS BANCARIOS:
{extracto}"""

if "messages" not in st.session_state:
    st.session_state.messages = []
if "extracto_text" not in st.session_state:
    st.session_state.extracto_text = ""
if "files_key" not in st.session_state:
    st.session_state.files_key = ()

if uploaded_files:
    files_key = tuple(f.name for f in uploaded_files)
    if files_key != st.session_state.files_key:
        with st.spinner("Leyendo extractos..."):
            st.session_state.extracto_text = extract_all(uploaded_files)
            st.session_state.files_key = files_key
            st.session_state.messages = []
        st.sidebar.success(f"✅ {len(uploaded_files)} archivo(s) cargado(s)")

if not api_key:
    st.info("👈 Ingresá tu API Key de Anthropic para empezar.")
elif not uploaded_files:
    st.info("👈 Subí al menos un extracto de Santander para empezar.")
else:
    if not st.session_state.messages:
        st.markdown("**Preguntas para arrancar:**")
        cols = st.columns(2)
        suggestions = [
            "¿En qué gasté más plata?",
            "¿Cuánto gané y cuánto gasté?",
            "¿Cuánto gasto en comida y delivery?",
            "¿Tengo margen para invertir algo?",
            "¿Qué gastos fijos tengo todos los meses?",
            "¿En qué mes gasté más?",
        ]
        for i, sug in enumerate(suggestions):
            if cols[i % 2].button(sug, key=f"sug_{i}"):
                st.session_state.messages.append({"role": "user", "content": sug})
                st.rerun()

    for msg in st.session_state.messages:
        with st.chat_message(msg["role"]):
            st.markdown(msg["content"])

    if prompt := st.chat_input("Preguntame sobre tus finanzas..."):
        st.session_state.messages.append({"role": "user", "content": prompt})
        with st.chat_message("user"):
            st.markdown(prompt)

    if st.session_state.messages and st.session_state.messages[-1]["role"] == "user":
        system_prompt = SYSTEM.format(extracto=st.session_state.extracto_text)
        client = anthropic.Anthropic(api_key=api_key)
        with st.chat_message("assistant"):
            with st.spinner("Analizando..."):
                response = client.messages.create(
                    model="claude-sonnet-4-20250514",
                    max_tokens=1500,
                    system=system_prompt,
                    messages=st.session_state.messages,
                )
                answer = response.content[0].text
                st.markdown(answer)
        st.session_state.messages.append({"role": "assistant", "content": answer})
