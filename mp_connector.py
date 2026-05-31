"""
mp_connector.py
Módulo para conectar con la API de Mercado Pago y traer movimientos de cuenta.
Uso: importar en app.py de la finance app existente.
"""

import os
import requests
import pandas as pd
from datetime import datetime, timedelta


MP_ACCESS_TOKEN = os.environ.get("MERCADOPAGO_ACCESS_TOKEN")
BASE_URL = "https://api.mercadopago.com"

HEADERS = {
    "Authorization": f"Bearer {MP_ACCESS_TOKEN}",
    "Content-Type": "application/json"
}


# ─────────────────────────────────────────────
# CATEGORIZACIÓN DE COMERCIOS
# ─────────────────────────────────────────────

CATEGORIAS = {
    "supermercado / almacén": [
        "dia", "carrefour", "coto", "jumbo", "disco", "vea", "walmart",
        "super", "almacen", "verduleria", "fruteria"
    ],
    "gastronomía": [
        "restaurant", "resto", "cafe", "cafeteria", "bar", "pizzeria",
        "sushi", "burger", "mcdonald", "mostaza", "wendy", "subway",
        "gastronomia", "parrilla", "fish market", "moma", "pato"
    ],
    "salud / farmacia": [
        "farmacia", "farma", "drogueria", "clinica", "medico", "laboratorio",
        "arce", "sanatorio", "hospital", "dental", "optica"
    ],
    "servicios / facturas": [
        "edenor", "edesur", "metrogas", "aysa", "iplan", "fibertel",
        "claro", "personal", "movistar", "telecom", "directv", "flow",
        "osde", "swiss medical", "galeno", "seguro", "expensas"
    ],
    "transporte": [
        "uber", "cabify", "taxi", "remis", "sube", "peaje", "nafta",
        "ypf", "shell", "axion", "estacion", "tren", "subte", "colectivo"
    ],
    "indumentaria": [
        "zara", "h&m", "adidas", "nike", "rapsodia", "legacy", "ropa",
        "calzado", "zapatilla", "zapateria", "indumentaria"
    ],
    "entretenimiento / cultura": [
        "netflix", "spotify", "hbo", "disney", "amazon", "cine", "teatro",
        "libro", "dostoievski", "libreria", "fnac", "steam", "playstation"
    ],
    "banco / finanzas": [
        "debin", "transferencia", "extraccion", "cajero", "banco",
        "caja de seguridad", "caja ahorro", "plazo fijo", "inversion"
    ],
    "otros": []
}


def categorizar(descripcion: str) -> str:
    """Asigna una categoría a una transacción según su descripción."""
    desc = str(descripcion).lower()
    for categoria, keywords in CATEGORIAS.items():
        if any(kw in desc for kw in keywords):
            return categoria
    return "otros"


# ─────────────────────────────────────────────
# 1. PAGOS (compras con QR, débito, online)
# ─────────────────────────────────────────────

def get_payments(days_back: int = 30) -> pd.DataFrame:
    date_from = (datetime.now() - timedelta(days=days_back)).strftime("%Y-%m-%dT00:00:00.000-03:00")
    date_to = datetime.now().strftime("%Y-%m-%dT23:59:59.000-03:00")

    params = {
        "sort": "date_created",
        "criteria": "desc",
        "range": "date_created",
        "begin_date": date_from,
        "end_date": date_to,
        "limit": 100,
        "offset": 0
    }

    all_payments = []
    while True:
        response = requests.get(f"{BASE_URL}/v1/payments/search", headers=HEADERS, params=params)
        response.raise_for_status()
        data = response.json()

        results = data.get("results", [])
        if not results:
            break

        all_payments.extend(results)

        paging = data.get("paging", {})
        total = paging.get("total", 0)
        offset = paging.get("offset", 0) + paging.get("limit", 100)
        if offset >= total:
            break
        params["offset"] = offset

    if not all_payments:
        return pd.DataFrame()

    df = pd.DataFrame([{
        "id": p.get("id"),
        "fecha": pd.to_datetime(p.get("date_created")).tz_localize(None) if p.get("date_created") else None,
        "monto": p.get("transaction_amount", 0),
        "moneda": p.get("currency_id", "ARS"),
        "descripcion": p.get("description") or p.get("statement_descriptor") or "Sin descripción",
        "estado": p.get("status"),
        "tipo": p.get("payment_type_id"),
        "metodo": p.get("payment_method_id"),
        "cuotas": p.get("installments", 1),
        "fuente": "payment"
    } for p in all_payments])

    df = df[df["estado"] == "approved"].copy()
    df["categoria"] = df["descripcion"].apply(categorizar)
    return df.sort_values("fecha", ascending=False).reset_index(drop=True)


# ─────────────────────────────────────────────
# 2. MOVIMIENTOS DE CUENTA
# ─────────────────────────────────────────────

def get_account_movements(days_back: int = 30) -> pd.DataFrame:
    date_from = (datetime.now() - timedelta(days=days_back)).strftime("%Y-%m-%dT00:00:00.000-03:00")
    date_to = datetime.now().strftime("%Y-%m-%dT23:59:59.000-03:00")

    params = {
        "limit": 100,
        "offset": 0,
        "range": "date_created",
        "begin_date": date_from,
        "end_date": date_to
    }

    all_movements = []
    while True:
        response = requests.get(f"{BASE_URL}/v1/account/movements/search", headers=HEADERS, params=params)
        if response.status_code == 404:
            break
        response.raise_for_status()
        data = response.json()

        results = data.get("results", [])
        if not results:
            break

        all_movements.extend(results)

        paging = data.get("paging", {})
        total = paging.get("total", 0)
        offset = paging.get("offset", 0) + paging.get("limit", 100)
        if offset >= total:
            break
        params["offset"] = offset

    if not all_movements:
        return pd.DataFrame()

    df = pd.DataFrame([{
        "id": m.get("id"),
        "fecha": pd.to_datetime(m.get("date_created")).tz_localize(None) if m.get("date_created") else None,
        "monto": m.get("amount", 0),
        "moneda": m.get("currency_id", "ARS"),
        "descripcion": m.get("description") or m.get("type") or "Movimiento",
        "tipo": m.get("type"),
        "estado": "approved",
        "fuente": "movement"
    } for m in all_movements])

    df["categoria"] = df["descripcion"].apply(categorizar)
    return df.sort_values("fecha", ascending=False).reset_index(drop=True)


# ─────────────────────────────────────────────
# 3. FUNCIÓN PRINCIPAL: combinar todo
# ─────────────────────────────────────────────

def get_all_transactions(days_back: int = 30) -> pd.DataFrame:
    payments = get_payments(days_back)
    movements = get_account_movements(days_back)

    dfs = [df for df in [payments, movements] if not df.empty]
    if not dfs:
        return pd.DataFrame()

    combined = pd.concat(dfs, ignore_index=True)
    combined = combined.sort_values("fecha", ascending=False).reset_index(drop=True)
    return combined


# ─────────────────────────────────────────────
# 4. RESUMEN CATEGORIZADO PARA EL CHAT
# ─────────────────────────────────────────────

def build_mp_context(days_back: int = 30) -> str:
    df = get_all_transactions(days_back)

    if df.empty:
        return "No se encontraron movimientos de Mercado Pago para el período solicitado."

    tipos_gasto = ["regular_payment", "bank_transfer"]
    pagos = df[df["tipo"].isin(tipos_gasto)] if "tipo" in df.columns else df
    ingresos_df = df[~df["tipo"].isin(tipos_gasto)] if "tipo" in df.columns else pd.DataFrame()

    total_gastos = pagos["monto"].sum() if not pagos.empty else 0
    total_ingresos = ingresos_df["monto"].sum() if not ingresos_df.empty else 0
    cant_transacciones = len(df)

    # Resumen por categoría
    if not pagos.empty and "categoria" in pagos.columns:
        por_categoria = (
            pagos.groupby("categoria")["monto"]
            .sum()
            .sort_values(ascending=False)
        )
        cat_texto = "\n".join([
            f"  - {cat}: ARS {monto:,.0f}"
            for cat, monto in por_categoria.items()
        ])
    else:
        cat_texto = "  Sin datos de categorías"

    # Top 10 comercios
    if not pagos.empty:
        top_comercios = (
            pagos.groupby("descripcion")["monto"]
            .sum()
            .sort_values(ascending=False)
            .head(10)
        )
        comercios_texto = "\n".join([
            f"  - {desc}: ARS {monto:,.0f}"
            for desc, monto in top_comercios.items()
        ])
    else:
        comercios_texto = "  Sin datos"

    # Evolución mensual
    df["mes"] = df["fecha"].dt.to_period("M").astype(str)
    monthly = df.groupby("mes")["monto"].sum()
    monthly_texto = "\n".join([f"  - {mes}: ARS {monto:,.0f}" for mes, monto in monthly.items()])

    # Últimas 20 transacciones
    cols_show = [c for c in ["fecha", "descripcion", "monto", "categoria"] if c in df.columns]
    ultimas = df[cols_show].head(20).to_string(index=False)

    context = f"""
=== DATOS DE MERCADO PAGO (últimos {days_back} días) ===

Período: {df['fecha'].min().strftime('%d/%m/%Y')} al {df['fecha'].max().strftime('%d/%m/%Y')}
Total transacciones: {cant_transacciones}
Total gastos: ARS {total_gastos:,.0f}
Total ingresos/acreditaciones: ARS {total_ingresos:,.0f}
Balance neto: ARS {(total_ingresos - total_gastos):,.0f}

--- Gastos por categoría ---
{cat_texto}

--- Top 10 comercios / descripciones ---
{comercios_texto}

--- Evolución mensual ---
{monthly_texto}

--- Últimas 20 transacciones ---
{ultimas}
"""
    return context


# ─────────────────────────────────────────────
# TEST RÁPIDO
# ─────────────────────────────────────────────

if __name__ == "__main__":
    print("Conectando con Mercado Pago...")
    context = build_mp_context(days_back=30)
    print(context)
