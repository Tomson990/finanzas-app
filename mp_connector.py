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
# 1. PAGOS (compras con QR, débito, online)
# ─────────────────────────────────────────────

def get_payments(days_back: int = 90) -> pd.DataFrame:
    """
    Trae los pagos realizados desde tu cuenta en los últimos N días.
    Endpoint: GET /v1/payments/search
    """
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

        # Paginación
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
        "comercio": p.get("merchant_account_id"),
        "fuente": "payment"
    } for p in all_payments])

    # Solo pagos aprobados y salientes
    df = df[df["estado"] == "approved"].copy()
    return df.sort_values("fecha", ascending=False).reset_index(drop=True)


# ─────────────────────────────────────────────
# 2. MOVIMIENTOS DE CUENTA (transferencias, recargas, extracciones)
# ─────────────────────────────────────────────

def get_account_movements(days_back: int = 90) -> pd.DataFrame:
    """
    Trae los movimientos de la cuenta MP (transferencias, recargas, etc).
    Endpoint: GET /v1/account/movements/search
    """
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
            # Endpoint no disponible para esta cuenta
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

    return df.sort_values("fecha", ascending=False).reset_index(drop=True)


# ─────────────────────────────────────────────
# 3. FUNCIÓN PRINCIPAL: combinar todo
# ─────────────────────────────────────────────

def get_all_transactions(days_back: int = 90) -> pd.DataFrame:
    """
    Combina pagos y movimientos en un único DataFrame normalizado.
    Es la función que llama app.py.
    """
    payments = get_payments(days_back)
    movements = get_account_movements(days_back)

    dfs = [df for df in [payments, movements] if not df.empty]
    if not dfs:
        return pd.DataFrame()

    combined = pd.concat(dfs, ignore_index=True)
    combined = combined.sort_values("fecha", ascending=False).reset_index(drop=True)
    return combined


# ─────────────────────────────────────────────
# 4. RESUMEN PARA INYECTAR AL CHAT
# ─────────────────────────────────────────────
def build_mp_context(days_back: int = 90) -> str:
    """
    Genera un texto-resumen de los movimientos de MP para inyectar
    como contexto al sistema prompt de Claude (igual que los PDFs).
    """
    df = get_all_transactions(days_back)

    if df.empty:
        return "No se encontraron movimientos de Mercado Pago para el período solicitado."

    pagos = df[df["tipo"].isin(["regular_payment", "bank_transfer"])] if "tipo" in df.columns else df
    total_gastos = pagos["monto"].sum() if "monto" in df.columns else 0
    total_ingresos = df[~df["tipo"].isin(["regular_payment", "bank_transfer"])]["monto"].sum() if "tipo" in df.columns else 0
    cant_transacciones = len(df)

    # Top descripciones
    top_desc = (
        pagos.groupby("descripcion")["monto"]
        .sum()
        .sort_values(ascending=False)
        .head(10)
        .to_string()
    )

    # Resumen por mes
    df["mes"] = df["fecha"].dt.to_period("M").astype(str)
    monthly = df.groupby("mes")["monto"].sum().to_string()

    context = f"""
=== DATOS DE MERCADO PAGO (últimos {days_back} días) ===

Período: {df['fecha'].min().strftime('%d/%m/%Y')} al {df['fecha'].max().strftime('%d/%m/%Y')}
Total transacciones: {cant_transacciones}
Total gastos: ARS {abs(total_gastos):,.2f}
Total ingresos: ARS {total_ingresos:,.2f}
Balance neto: ARS {(total_ingresos - total_gastos):,.2f}

--- Top 10 gastos por descripción ---
{top_desc}

--- Evolución mensual (suma neta) ---
{monthly}

--- Últimas 20 transacciones ---
{df[['fecha','descripcion','monto','tipo']].head(20).to_string(index=False)}
"""
    return context


# ─────────────────────────────────────────────
# TEST RÁPIDO (correr directamente: python mp_connector.py)
# ─────────────────────────────────────────────
if __name__ == "__main__":
    print("Conectando con Mercado Pago...")
    context = build_mp_context(days_back=30)
    print(context)        "range": "date_created",
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

        # Paginación
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
        "comercio": p.get("merchant_account_id"),
        "fuente": "payment"
    } for p in all_payments])

    # Solo pagos aprobados y salientes
    df = df[df["estado"] == "approved"].copy()
    return df.sort_values("fecha", ascending=False).reset_index(drop=True)


# ─────────────────────────────────────────────
# 2. MOVIMIENTOS DE CUENTA (transferencias, recargas, extracciones)
# ─────────────────────────────────────────────

def get_account_movements(days_back: int = 90) -> pd.DataFrame:
    """
    Trae los movimientos de la cuenta MP (transferencias, recargas, etc).
    Endpoint: GET /v1/account/movements/search
    """
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
            # Endpoint no disponible para esta cuenta
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

    return df.sort_values("fecha", ascending=False).reset_index(drop=True)


# ─────────────────────────────────────────────
# 3. FUNCIÓN PRINCIPAL: combinar todo
# ─────────────────────────────────────────────

def get_all_transactions(days_back: int = 90) -> pd.DataFrame:
    """
    Combina pagos y movimientos en un único DataFrame normalizado.
    Es la función que llama app.py.
    """
    payments = get_payments(days_back)
    movements = get_account_movements(days_back)

    dfs = [df for df in [payments, movements] if not df.empty]
    if not dfs:
        return pd.DataFrame()

    combined = pd.concat(dfs, ignore_index=True)
    combined = combined.sort_values("fecha", ascending=False).reset_index(drop=True)
    return combined


# ─────────────────────────────────────────────
# 4. RESUMEN PARA INYECTAR AL CHAT
# ─────────────────────────────────────────────

def build_mp_context(days_back: int = 90) -> str:
    """
    Genera un texto-resumen de los movimientos de MP para inyectar
    como contexto al sistema prompt de Claude (igual que los PDFs).
    """
    df = get_all_transactions(days_back)

    if df.empty:
        return "No se encontraron movimientos de Mercado Pago para el período solicitado."

    pagos = df[df["tipo"].isin(["regular_payment", "bank_transfer"])] if "tipo" in df.columns else df
    total_gastos = pagos["monto"].sum() if "monto" in df.columns else 0
    total_ingresos = df[~df["tipo"].isin(["regular_payment", "bank_transfer"])]["monto"].sum() if "tipo" in df.columns else 0
    cant_transacciones = len(df)

    # Top descripciones
    top_desc = (
        df[df["monto"] < 0]
        .groupby("descripcion")["monto"]
        .sum()
        .sort_values()
        .head(10)
        .to_string()
    )

    # Resumen por mes
    df["mes"] = df["fecha"].dt.to_period("M").astype(str)
    monthly = df.groupby("mes")["monto"].sum().to_string()

    context = f"""
=== DATOS DE MERCADO PAGO (últimos {days_back} días) ===

Período: {df['fecha'].min().strftime('%d/%m/%Y')} al {df['fecha'].max().strftime('%d/%m/%Y')}
Total transacciones: {cant_transacciones}
Total gastos: ARS {abs(total_gastos):,.2f}
Total ingresos: ARS {total_ingresos:,.2f}
Balance neto: ARS {(total_ingresos + total_gastos):,.2f}

--- Top 10 gastos por descripción ---
{top_desc}

--- Evolución mensual (suma neta) ---
{monthly}

--- Últimas 20 transacciones ---
{df[['fecha','descripcion','monto','tipo']].head(20).to_string(index=False)}
"""
    return context


# ─────────────────────────────────────────────
# TEST RÁPIDO (correr directamente: python mp_connector.py)
# ─────────────────────────────────────────────

if __name__ == "__main__":
    print("Conectando con Mercado Pago...")
    context = build_mp_context(days_back=30)
    print(context)
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

        # Paginación
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
        "comercio": p.get("merchant_account_id"),
        "fuente": "payment"
    } for p in all_payments])

    # Solo pagos aprobados y salientes
    df = df[df["estado"] == "approved"].copy()
    return df.sort_values("fecha", ascending=False).reset_index(drop=True)


# ─────────────────────────────────────────────
# 2. MOVIMIENTOS DE CUENTA (transferencias, recargas, extracciones)
# ─────────────────────────────────────────────

def get_account_movements(days_back: int = 90) -> pd.DataFrame:
    """
    Trae los movimientos de la cuenta MP (transferencias, recargas, etc).
    Endpoint: GET /v1/account/movements/search
    """
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
            # Endpoint no disponible para esta cuenta
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

    return df.sort_values("fecha", ascending=False).reset_index(drop=True)


# ─────────────────────────────────────────────
# 3. FUNCIÓN PRINCIPAL: combinar todo
# ─────────────────────────────────────────────

def get_all_transactions(days_back: int = 90) -> pd.DataFrame:
    """
    Combina pagos y movimientos en un único DataFrame normalizado.
    Es la función que llama app.py.
    """
    payments = get_payments(days_back)
    movements = get_account_movements(days_back)

    dfs = [df for df in [payments, movements] if not df.empty]
    if not dfs:
        return pd.DataFrame()

    combined = pd.concat(dfs, ignore_index=True)
    combined = combined.sort_values("fecha", ascending=False).reset_index(drop=True)
    return combined


# ─────────────────────────────────────────────
# 4. RESUMEN PARA INYECTAR AL CHAT
# ─────────────────────────────────────────────

def build_mp_context(days_back: int = 90) -> str:
    """
    Genera un texto-resumen de los movimientos de MP para inyectar
    como contexto al sistema prompt de Claude (igual que los PDFs).
    """
    df = get_all_transactions(days_back)

    if df.empty:
        return "No se encontraron movimientos de Mercado Pago para el período solicitado."

    total_gastos = df[df["monto"] < 0]["monto"].sum() if "monto" in df.columns else 0
    total_ingresos = df[df["monto"] > 0]["monto"].sum() if "monto" in df.columns else 0
    cant_transacciones = len(df)

    # Top descripciones
    top_desc = (
        df[df["monto"] < 0]
        .groupby("descripcion")["monto"]
        .sum()
        .sort_values()
        .head(10)
        .to_string()
    )

    # Resumen por mes
    df["mes"] = df["fecha"].dt.to_period("M").astype(str)
    monthly = df.groupby("mes")["monto"].sum().to_string()

    context = f"""
=== DATOS DE MERCADO PAGO (últimos {days_back} días) ===

Período: {df['fecha'].min().strftime('%d/%m/%Y')} al {df['fecha'].max().strftime('%d/%m/%Y')}
Total transacciones: {cant_transacciones}
Total gastos: ARS {abs(total_gastos):,.2f}
Total ingresos: ARS {total_ingresos:,.2f}
Balance neto: ARS {(total_ingresos + total_gastos):,.2f}

--- Top 10 gastos por descripción ---
{top_desc}

--- Evolución mensual (suma neta) ---
{monthly}

--- Últimas 20 transacciones ---
{df[['fecha','descripcion','monto','tipo']].head(20).to_string(index=False)}
"""
    return context


# ─────────────────────────────────────────────
# TEST RÁPIDO (correr directamente: python mp_connector.py)
# ─────────────────────────────────────────────

if __name__ == "__main__":
    print("Conectando con Mercado Pago...")
    context = build_mp_context(days_back=30)
    print(context)
