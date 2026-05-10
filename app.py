import streamlit as st
import anthropic
import pdfplumber
import pandas as pd
import requests
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

# ── Datos IPC automáticos (ArgentinaDatos) ────────────────────────────────────
@st.cache_data(ttl=86400)  # Cache 24 horas
def fetch_ipc():
    try:
        r = requests.get(
            "https://api.argentinadatos.com/v1/finanzas/indices/inflacion",
            timeout=5
        )
        data = r.json()
        # Trae lista de meses, tomamos el último
        ultimo = data[-1]
        penultimo = data[-2] if len(data) >= 2 else None

        texto = f"""
DATOS IPC INDEC — {ultimo.get('fecha', 'último disponible')}:
- Inflación mensual: {ultimo.get('valor', '?')}%
"""
        if penultimo:
            texto += f"- Mes anterior: {penultimo.get('valor', '?')}%\n"

        # Acumulado año
        año_actual = ultimo.get('fecha', '')[:4]
        meses_año = [d for d in data if d.get('fecha', '').startswith(año_actual)]
        if meses_año:
            acumulado = 1
            for m in meses_año:
                acumulado *= (1 + float(m.get('valor', 0)) / 100)
            acumulado = round((acumulado - 1) * 100, 1)
            texto += f"- Acumulado {año_actual}: {acumulado}%\n"

        texto += """
Categorías IPC aproximadas (usar como referencia):
- Alimentos y bebidas: similar a inflación general o levemente superior
- Salud: generalmente por encima de la inflación general
- Transporte: variable según tarifas
- Vivienda y servicios: variable según regulaciones
- Indumentaria: variable estacional
"""
        return texto, ultimo.get('fecha', ''), ultimo.get('valor', '?')

    except Exception:
        # Fallback con datos hardcodeados si la API falla
        return """
DATOS IPC INDEC (referencia — verificar en indec.gob.ar):
- Inflación mensual general: ~3-4%
- Acumulado 2026: ~6%
""", "no disponible", "?"

# ── Sidebar ───────────────────────────────────────────────────────────────────
ipc_texto, ipc_fecha, ipc_valor = fetch_ipc()

with st.sidebar:
    st.header("👤 Usuario")
    usuario = st.text_input("Tu nombre", placeholder="ej: tomson")
    st.markdown("---")
    st.header("📄 Extractos")
    uploaded_files = st.file_uploader(
        "Subí PDFs o Excel de tu banco",
        type=["pdf", "xlsx", "xls"],
        accept_multiple_files=True,
    )
    st.markdown("---")
    st.caption(f"📊 IPC: {ipc_fecha} — {ipc_valor}% mensual")
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

Cuando analizás inflación personal:
- Categorizás los gastos del extracto según las divisiones del IPC del INDEC
- Comparás la variación de gastos del usuario contra la inflación oficial
- Le decís si gastó más o menos que la inflación en cada rubro
- Calculás si su sueldo le ganó o perdió contra la inflación general
- Usás frases concretas: "tu gasto en alimentos subió X% vs inflación de Y%"
- Si hay varios meses de extractos, calculás la variación real entre períodos

Cuando te preguntan sobre inversiones:
- Calculás el excedente real: ingresos menos gastos fijos y variables
- Evaluás liquidez y perfil de riesgo
- Considerás el contexto argentino: inflación, brecha cambiaria, dolarización
- Mencionás opciones concretas: FCI money market, plazo fijo UVA, CEDEARs, ON
- Si hay movimientos con Balanz u otros brokers, los integrás al análisis
- Siempre aclarás que no sos asesor financiero regulado

{ipc}

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

if usuario and usuario != st.session_state.usuario_cargado:
    st.session_state.messages = cargar_historial(usuario)
    st.session_state.usuario_cargado = usuario

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
            "¿Le gané o perdí a la inflación este mes?",
            "¿Cuánto subieron mis gastos vs la inflación?",
            "¿Tengo margen para invertir algo?",
            "¿Qué gastos fijos tengo todos los meses?",
            "¿Mi sueldo le ganó a la inflación?",
        ]
        for i, sug in enumerate(suggestions):
            if cols[i % 2].button(sug, key=f"sug_{i}"):
                st.session_state.messages.append({"role": "user", "content": sug})
                guardar_mensaje(usuario, "user", sug)
                st.rerun()

    for msg in st.session_state.messages:
        with st.chat_message(msg["role"]):
            st.markdown(msg["content"])

    if prompt := st.chat_input("Preguntame sobre tus finanzas o la inflación..."):
        st.session_state.messages.append({"role": "user", "content": prompt})
        guardar_mensaje(usuario, "user", prompt)
        with st.chat_message("user"):
            st.markdown(prompt)

    if st.session_state.messages and st.session_state.messages[-1]["role"] == "user":
        system_prompt = SYSTEM.format(
            ipc=ipc_texto,
            extracto=st.session_state.extracto_text
        )
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
