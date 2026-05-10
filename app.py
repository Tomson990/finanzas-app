import streamlit as st
import anthropic
import pdfplumber
import pandas as pd
import io
from supabase import create_client

st.set_page_config(page_title="Mi Asesor Financiero", page_icon="💰", layout="centered")
st.title("💰 Mi Asesor Financiero")
st.caption("Subí tus extractos bancarios y chateá con tus datos.")

# ── Clientes ──────────────────────────────────────────────────────────────────
@st.cache_resource
def get_supabase():
    return create_client(
        st.secrets["SUPABASE_URL"],
        st.secrets["SUPABASE_KEY"]
    )

def get_anthropic():
    return anthropic.Anthropic(api_key=st.secrets["ANTHROPIC_API_KEY"])

# ── Sidebar ───────────────────────────────────────────────────────────────────
with st.sidebar:
    st.header("👤 Usuario")
    usuario = st.text_input("Tu nombre (para recordar tu historial)", placeholder="ej: tomson")
    st.markdown("---")
    st.header("📄 Extractos")
    uploaded_files = st.file_uploader(
        "Subí PDFs o Excel de tu banco",
        type=["pdf", "xlsx", "xls"],
        accept_multiple_files=True,
    )
    st.markdown("---")
    if st.button("🗑️ Limpiar conversación"):
        if usuario:
            get_supabase().table("conversaciones").delete().eq("usuario", usuario).execute()
        st.session_state.messages = []
        st.rerun()

# ── Extracción ────────────────────────────────────────────────────────────────
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

# ── System prompt ─────────────────────────────────────────────────────────────
SYSTEM = """Sos un asesor financiero personal en español rioplatense.
Analizás extractos bancarios argentinos y respondés preguntas sobre gastos, ingresos, transferencias, inversiones y patrones de consumo.

Reglas generales:
- Respondés siempre en español argentino
- Usás pesos con puntos de miles: $1.500.000
- Sos directo y concreto — citás números reales del extracto
- Incluís transferencias, préstamos y retiros de efectivo en el análisis, no solo compras
- Si algo no está en los datos, lo decís claramente
- Detectás patrones y anomalías de forma proactiva

Cuando te preguntan sobre inversiones:
- Primero calculás el excedente real del usuario: ingresos menos todos los gastos fijos y variables
- Evaluás la liquidez: si el usuario queda muy justo a fin de mes, priorizás instrumentos líquidos como FCI money market
- Considerás el contexto argentino: inflación, brecha cambiaria, cobertura en dólares
- Mencionás opciones concretas: FCI money market, plazo fijo UVA, CEDEARs, obligaciones negociables
- Si el usuario ya tiene movimientos con brokers como Balanz, los integrás al análisis
- Siempre aclarás que no sos asesor financiero regulado

EXTRACTOS BANCARIOS:
{extracto}"""

# ── Memoria con Supabase ──────────────────────────────────────────────────────
def cargar_historial(usuario):
    res = get_supabase().table("conversaciones")\
        .select("rol, mensaje")\
        .eq("usuario", usuario)\
        .order("created_at")\
        .execute()
    return [{"role": r["rol"], "content": r["mensaje"]} for r in res.data]

def guardar_mensaje(usuario, rol, mensaje):
    get_supabase().table("conversaciones").insert({
        "usuario": usuario,
        "rol": rol,
        "mensaje": mensaje
    }).execute()

# ── Estado de sesión ──────────────────────────────────────────────────────────
if "messages" not in st.session_state:
    st.session_state.messages = []
if "extracto_text" not in st.session_state:
    st.session_state.extracto_text = ""
if "files_key" not in st.session_state:
    st.session_state.files_key = ()
if "usuario_cargado" not in st.session_state:
    st.session_state.usuario_cargado = ""

# Cargar historial cuando cambia el usuario
if usuario and usuario != st.session_state.usuario_cargado:
    st.session_state.messages = cargar_historial(usuario)
    st.session_state.usuario_cargado = usuario

# Procesar archivos
if uploaded_files:
    files_key = tuple(f.name for f in uploaded_files)
    if files_key != st.session_state.files_key:
        with st.spinner("Leyendo extractos..."):
            st.session_state.extracto_text = extract_all(uploaded_files)
            st.session_state.files_key = files_key
        st.sidebar.success(f"✅ {len(uploaded_files)} archivo(s) cargado(s)")

# ── Chat ──────────────────────────────────────────────────────────────────────
if not usuario:
    st.info("👈 Ingresá tu nombre en el panel lateral para empezar.")
elif not uploaded_files:
    st.info("👈 Subí al menos un extracto bancario para empezar.")
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
                guardar_mensaje(usuario, "user", sug)
                st.rerun()

    for msg in st.session_state.messages:
        with st.chat_message(msg["role"]):
            st.markdown(msg["content"])

    if prompt := st.chat_input("Preguntame sobre tus finanzas..."):
        st.session_state.messages.append({"role": "user", "content": prompt})
        guardar_mensaje(usuario, "user", prompt)
        with st.chat_message("user"):
            st.markdown(prompt)

    if st.session_state.messages and st.session_state.messages[-1]["role"] == "user":
        system_prompt = SYSTEM.format(extracto=st.session_state.extracto_text)
        client = get_anthropic()
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
        guardar_mensaje(usuario, "assistant", answer)
            get_supabase().table("conversaciones").delete().eq("usuario", usuario).execute()
        st.session_state.messages = []
        st.rerun()

# ── Extracción ────────────────────────────────────────────────────────────────
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

# ── System prompt ─────────────────────────────────────────────────────────────
SYSTEM = """Sos un asesor financiero personal en español rioplatense.
Analizás extractos bancarios argentinos y respondés preguntas sobre gastos, ingresos, transferencias, inversiones y patrones de consumo.

Reglas generales:
- Respondés siempre en español argentino
- Usás pesos con puntos de miles: $1.500.000
- Sos directo y concreto — citás números reales del extracto
- Incluís transferencias, préstamos y retiros de efectivo en el análisis, no solo compras
- Si algo no está en los datos, lo decís claramente
- Detectás patrones y anomalías de forma proactiva

Cuando te preguntan sobre inversiones:
- Primero calculás el excedente real del usuario: ingresos menos todos los gastos fijos y variables
- Evaluás la liquidez: si el usuario queda muy justo a fin de mes, priorizás instrumentos líquidos como FCI money market
- Considerás el contexto argentino: inflación, brecha cambiaria, cobertura en dólares
- Mencionás opciones concretas: FCI money market, plazo fijo UVA, CEDEARs, obligaciones negociables
- Si el usuario ya tiene movimientos con brokers como Balanz, los integrás al análisis
- Siempre aclarás que no sos asesor financiero regulado

EXTRACTOS BANCARIOS:
{extracto}"""

# ── Memoria con Supabase ──────────────────────────────────────────────────────
def cargar_historial(usuario):
    res = get_supabase().table("conversaciones")\
        .select("rol, mensaje")\
        .eq("usuario", usuario)\
        .order("created_at")\
        .execute()
    return [{"role": r["rol"], "content": r["mensaje"]} for r in res.data]

def guardar_mensaje(usuario, rol, mensaje):
    get_supabase().table("conversaciones").insert({
        "usuario": usuario,
        "rol": rol,
        "mensaje": mensaje
    }).execute()

# ── Estado de sesión ──────────────────────────────────────────────────────────
if "messages" not in st.session_state:
    st.session_state.messages = []
if "extracto_text" not in st.session_state:
    st.session_state.extracto_text = ""
if "files_key" not in st.session_state:
    st.session_state.files_key = ()
if "usuario_cargado" not in st.session_state:
    st.session_state.usuario_cargado = ""

# Cargar historial cuando cambia el usuario
if usuario and usuario != st.session_state.usuario_cargado:
    st.session_state.messages = cargar_historial(usuario)
    st.session_state.usuario_cargado = usuario

# Procesar archivos
if uploaded_files:
    files_key = tuple(f.name for f in uploaded_files)
    if files_key != st.session_state.files_key:
        with st.spinner("Leyendo extractos..."):
            st.session_state.extracto_text = extract_all(uploaded_files)
            st.session_state.files_key = files_key
        st.sidebar.success(f"✅ {len(uploaded_files)} archivo(s) cargado(s)")

# ── Chat ──────────────────────────────────────────────────────────────────────
if not usuario:
    st.info("👈 Ingresá tu nombre en el panel lateral para empezar.")
elif not uploaded_files:
    st.info("👈 Subí al menos un extracto bancario para empezar.")
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
                guardar_mensaje(usuario, "user", sug)
                st.rerun()

    for msg in st.session_state.messages:
        with st.chat_message(msg["role"]):
            st.markdown(msg["content"])

    if prompt := st.chat_input("Preguntame sobre tus finanzas..."):
        st.session_state.messages.append({"role": "user", "content": prompt})
        guardar_mensaje(usuario, "user", prompt)
        with st.chat_message("user"):
            st.markdown(prompt)

    if st.session_state.messages and st.session_state.messages[-1]["role"] == "user":
        system_prompt = SYSTEM.format(extracto=st.session_state.extracto_text)
        client = get_anthropic()
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
        guardar_mensaje(usuario, "assistant", answer)
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

SYSTEM = SYSTEM = """Sos un asesor financiero personal en español rioplatense.
Analizás extractos bancarios argentinos y respondés preguntas sobre gastos, ingresos, transferencias, inversiones y patrones de consumo.

Reglas generales:
- Respondés siempre en español argentino
- Usás pesos con puntos de miles: $1.500.000
- Sos directo y concreto — citás números reales del extracto
- Incluís transferencias, préstamos y retiros de efectivo en el análisis, no solo compras
- Si algo no está en los datos, lo decís claramente
- Detectás patrones y anomalías de forma proactiva

Cuando te preguntan sobre inversiones:
- Primero calculás el excedente real del usuario: ingresos menos todos los gastos fijos (alquiler, consorcio, préstamos, servicios) y variables del período
- Evaluás la liquidez: si el usuario queda muy justo a fin de mes, priorizás instrumentos líquidos como FCI money market antes que plazos fijos
- Considerás el contexto argentino: inflación, brecha cambiaria, y que el usuario puede preferir cobertura en dólares
- Mencionás opciones concretas y ordenadas por perfil: FCI money market (liquidez inmediata), plazo fijo UVA (cobertura inflación), CEDEARs (dolarización parcial), obligaciones negociables
- Si el usuario ya tiene movimientos con brokers como Balanz, los mencionás y los integrás al análisis
- Siempre aclarás que no sos asesor financiero regulado y que estas son perspectivas generales

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


if not uploaded_files:
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
