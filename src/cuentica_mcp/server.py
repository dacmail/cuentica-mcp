"""
Cuéntica MCP Server
-------------------
MCP server para acceder a la API de Cuéntica desde Claude u otros LLMs.
Requiere la variable de entorno CUENTICA_API_TOKEN.
"""

import os
import base64
import httpx
from typing import Optional
from fastmcp import FastMCP

BASE_URL = "https://api.cuentica.com"

mcp = FastMCP(
    name="cuentica",
    instructions="""
Asistente de contabilidad conectado a Cuéntica (facturas, gastos, ingresos, clientes, proveedores, cuentas, documentos).

El SERVIDOR hace automáticamente (no necesitas gestionarlo):
- Limita page_size a 50 como máximo en todos los listados.
- En list_invoices con issued=False sin fechas, aplica el año en curso automáticamente.
- get_pending_collections itera todas las páginas automáticamente y excluye anuladas/cobradas.
- get_quarterly_vat_summary agrega facturas y gastos del trimestre calculando IVA neto.
- get_balance_summary suma saldos de todas las cuentas.
- mark_invoice_paid y mark_expense_paid combinan GET + PUT en una sola operación.
- summary=True en cualquier listado devuelve solo campos clave (menos tokens).

El MODELO debe gestionar:
- Facturas con register_info.status_description == "voided" están anuladas: exclúyelas de totales y pendientes.
- Para "pendientes de cobro" usa get_pending_collections en vez de iterar list_invoices manualmente.
- Borradores con fecha futura son facturas recurrentes preprogramadas, no son pendientes reales.
- Los resources cuentica://catalog/* contienen catálogos ligeros de clientes, proveedores, cuentas y etiquetas.

Reglas generales:
- Nunca muestres el token completo; solo los últimos 6 caracteres si se pide.
- Operaciones de escritura (crear, modificar, eliminar, cobrar, enviar email, anular) requieren confirmación explícita del usuario antes de ejecutar.
- Importes en € con 2 decimales. Fechas: dd/mm/yyyy al mostrar, yyyy-MM-dd al enviar.
""",
)

# ── HTTP helpers ──────────────────────────────────────────────────────────────

def _client() -> httpx.Client:
    token = os.environ.get("CUENTICA_API_TOKEN")
    if not token:
        raise ValueError("Falta CUENTICA_API_TOKEN. Obtén tu token en app.cuentica.com → Configuración → API.")
    return httpx.Client(base_url=BASE_URL, headers={"X-AUTH-TOKEN": token}, timeout=30)

def _clean(params: dict) -> dict:
    return {k: v for k, v in params.items() if v is not None}

def _raise(r: httpx.Response) -> None:
    """Lanza excepción con mensaje accionable. Nunca incluye el token."""
    try:
        r.raise_for_status()
    except httpx.HTTPStatusError:
        detail = ""
        try:
            body = r.json()
            detail = f" — {body}"
        except Exception:
            if r.text:
                detail = f" — {r.text[:200]}"
        raise RuntimeError(
            f"Cuéntica API error {r.status_code} en {r.request.method} {r.request.url.path}{detail}"
        ) from None

MAX_PAGE_SIZE = 50

def api_get(path: str, params: dict = None):
    p = _clean(params or {})
    if "page_size" in p:
        p["page_size"] = min(int(p["page_size"]), MAX_PAGE_SIZE)
    with _client() as c:
        r = c.get(path, params=p)
        _raise(r)
        return r.json()

def api_get_bytes(path: str) -> bytes:
    with _client() as c:
        r = c.get(path)
        _raise(r)
        return r.content

def api_post(path: str, body: dict = None):
    with _client() as c:
        r = c.post(path, json=body or {})
        _raise(r)
        return r.json()

def api_put(path: str, body):
    with _client() as c:
        r = c.put(path, json=body)
        _raise(r)
        return r.json()

def api_delete(path: str):
    with _client() as c:
        r = c.delete(path)
        _raise(r)
        return r.json() if r.content else None

def _opt(body: dict, **kwargs) -> dict:
    """Añade al dict solo los kwargs que no son None."""
    for k, v in kwargs.items():
        if v is not None:
            body[k] = v
    return body

def _all_pages(path: str, params: dict) -> list:
    """Itera todas las páginas de un endpoint y devuelve la lista completa."""
    results = []
    page = 1
    p = dict(params)
    p["page_size"] = MAX_PAGE_SIZE
    while True:
        p["page"] = page
        batch = api_get(path, p)
        results.extend(batch)
        if len(batch) < MAX_PAGE_SIZE:
            break
        page += 1
    return results

# ── Summary helpers ───────────────────────────────────────────────────────────

def _summary_invoice(inv: dict) -> dict:
    cust = inv.get("customer") or {}
    ri = inv.get("register_info") or {}
    return {
        "id": inv.get("id"),
        "number": inv.get("number"),
        "date": inv.get("date"),
        "customer": cust.get("tradename") or cust.get("business_name"),
        "total": inv.get("total"),
        "status": ri.get("status_description"),
        "paid": ri.get("paid"),
    }

def _summary_expense(exp: dict) -> dict:
    prov = exp.get("provider") or {}
    return {
        "id": exp.get("id"),
        "date": exp.get("date"),
        "provider": prov.get("tradename") or prov.get("business_name"),
        "total": exp.get("total"),
        "draft": exp.get("draft"),
        "document_number": exp.get("document_number"),
    }

def _summary_income(inc: dict) -> dict:
    cust = inc.get("customer") or {}
    return {
        "id": inc.get("id"),
        "date": inc.get("date"),
        "customer": cust.get("tradename") or cust.get("business_name"),
        "total": inc.get("total"),
        "document_number": inc.get("document_number"),
    }

def _summary_customer(c: dict) -> dict:
    return {"id": c.get("id"), "tradename": c.get("tradename"), "cif": c.get("cif"), "email": c.get("email")}

def _summary_provider(p: dict) -> dict:
    return {"id": p.get("id"), "tradename": p.get("tradename"), "cif": p.get("cif"), "email": p.get("email")}

# ── Anotaciones MCP ───────────────────────────────────────────────────────────

_READ       = {"readOnlyHint": True}
_IDEMPOTENT = {"readOnlyHint": True, "idempotentHint": True}
_WRITE      = {"destructiveHint": True}

# ── Empresa ───────────────────────────────────────────────────────────────────

@mcp.tool(annotations=_IDEMPOTENT)
def get_company() -> dict:
    """Datos del negocio: nombre, CIF, dirección, series de facturación, logo."""
    return api_get("/company")

@mcp.tool(annotations=_READ)
def get_invoice_series() -> list:
    """Series de facturación configuradas."""
    return api_get("/company/serie")

# ── Facturas ──────────────────────────────────────────────────────────────────

@mcp.tool(annotations=_READ)
def list_invoices(
    issued: Optional[bool] = None,
    initial_date: Optional[str] = None,
    end_date: Optional[str] = None,
    customer: Optional[int] = None,
    description: Optional[str] = None,
    serie: Optional[str] = None,
    tags: Optional[str] = None,
    min_total_limit: Optional[float] = None,
    max_total_limit: Optional[float] = None,
    sort: Optional[str] = None,
    page: int = 1,
    page_size: int = 25,
    summary: bool = False,
) -> list:
    """Lista facturas. issued=True emitidas, False borradores. sort ej: 'date:desc'. Fechas yyyy-MM-dd.
    Para borradores (issued=False) sin fechas especificadas se aplica automáticamente el año en curso.
    summary=True devuelve solo id, number, date, customer, total, status, paid (menos tokens)."""
    from datetime import date as _date
    if issued is False and initial_date is None and end_date is None:
        year = _date.today().year
        initial_date = f"{year}-01-01"
        end_date = f"{year}-12-31"
    result = api_get("/invoice", {
        "issued": issued, "initial_date": initial_date, "end_date": end_date,
        "customer": customer, "description": description, "serie": serie,
        "tags": tags, "min_total_limit": min_total_limit, "max_total_limit": max_total_limit,
        "sort": sort, "page": page, "page_size": page_size,
    })
    return [_summary_invoice(i) for i in result] if summary else result

@mcp.tool(annotations=_IDEMPOTENT)
def get_invoice(invoice_id: int) -> dict:
    """Detalle completo de una factura por su ID interno."""
    return api_get(f"/invoice/{invoice_id}")

@mcp.tool(annotations=_WRITE)
def create_invoice(
    issued: bool,
    invoice_lines: list[dict],
    charges: list[dict],
    customer: Optional[int] = None,
    description: Optional[str] = None,
    annotations: Optional[str] = None,
    date: Optional[str] = None,
    serie: Optional[str] = None,
    tags: Optional[list[str]] = None,
    number: Optional[int] = None,
    footer: Optional[str] = None,
    irm: Optional[str] = None,
    rectified_id: Optional[int] = None,
    rectification_cause: Optional[str] = None,
) -> dict:
    """
    ⚠️ Crea una factura. Confirmar con usuario antes de ejecutar.

    invoice_lines: [{quantity, concept, amount, discount, tax, retention, sell_type, tax_regime, tax_subjection_code}]
    charges: [{amount, payment_method, destination_account(id), paid, date?}]
    sell_type: "service"|"product"|"supplied_cost"
    tax: 0,4,10,12,21 (IVA) o 0,3,7,9.5,13.5,20 (IGIC)
    tax_regime: "01"|"02"|"08"|"11"|"17"|"18"|"20"
    tax_subjection_code: "S1"–"S2"|"N1"–"N2"|"E1"–"E6"
    payment_method: "cash"|"receipt"|"wire_transfer"|"card"|"promissory_note"|"other"
    """
    body = _opt({"issued": issued, "invoice_lines": invoice_lines, "charges": charges},
                customer=customer, description=description, annotations=annotations,
                date=date, serie=serie, tags=tags, number=number, footer=footer,
                irm=irm, rectified_id=rectified_id, rectification_cause=rectification_cause)
    return api_post("/invoice", body)

@mcp.tool(annotations=_WRITE)
def update_invoice(
    invoice_id: int,
    issued: bool,
    customer: int,
    invoice_lines: list[dict],
    charges: list[dict],
    description: Optional[str] = None,
    annotations: Optional[str] = None,
    date: Optional[str] = None,
    serie: Optional[str] = None,
    tags: Optional[list[str]] = None,
    number: Optional[int] = None,
    footer: Optional[str] = None,
    irm: Optional[str] = None,
) -> dict:
    """
    ⚠️ Actualiza factura. Confirmar con usuario. Líneas/cobros con id=actualiza, sin id=crea, omitidos=se eliminan.
    Ver create_invoice para estructura de invoice_lines y charges.
    """
    body = _opt({"issued": issued, "customer": customer, "invoice_lines": invoice_lines, "charges": charges},
                description=description, annotations=annotations, date=date, serie=serie,
                tags=tags, number=number, footer=footer, irm=irm)
    return api_put(f"/invoice/{invoice_id}", body)

@mcp.tool(annotations=_WRITE)
def delete_invoice(invoice_id: int) -> dict:
    """⚠️ Elimina una factura (irreversible). Confirmar con usuario."""
    return api_delete(f"/invoice/{invoice_id}")

@mcp.tool(annotations=_IDEMPOTENT)
def get_invoice_public_link(invoice_id: int) -> dict:
    """Link público de la factura para compartir con el cliente (incluye botón de pago Stripe)."""
    return api_get(f"/invoice/{invoice_id}/public")

@mcp.tool(annotations=_IDEMPOTENT)
def get_invoice_pdf(invoice_id: int) -> str:
    """Descarga el PDF de la factura. Devuelve el contenido en Base64."""
    return base64.b64encode(api_get_bytes(f"/invoice/{invoice_id}/pdf")).decode()

@mcp.tool(annotations=_WRITE)
def update_invoice_charges(invoice_id: int, charges: list[dict]) -> dict:
    """
    ⚠️ Actualiza cobros de una factura (ej: marcarla como cobrada). Confirmar con usuario.
    charges: [{id?, paid, amount, date, payment_method, destination_account}]
    Con id=actualiza, sin id=crea, omitidos=eliminados.
    """
    return api_put(f"/invoice/{invoice_id}/charges", {"charges": charges})

@mcp.tool(annotations=_WRITE)
def mark_invoice_paid(
    invoice_id: int,
    payment_date: str,
    payment_method: Optional[str] = None,
) -> dict:
    """
    ⚠️ Marca todos los cobros de una factura como cobrados. Más rápido que update_invoice_charges.
    Obtiene la factura automáticamente, marca todos los cobros como paid=True y actualiza.
    payment_date en formato yyyy-MM-dd. Confirmar con usuario.
    """
    invoice = api_get(f"/invoice/{invoice_id}")
    charges = invoice.get("charges", [])
    updated = []
    for c in charges:
        dest = c.get("destination_account")
        if isinstance(dest, dict):
            dest = dest.get("id")
        updated.append({
            "id": c.get("id"),
            "amount": c.get("amount"),
            "payment_method": payment_method or c.get("payment_method"),
            "destination_account": dest,
            "paid": True,
            "date": payment_date,
        })
    return api_put(f"/invoice/{invoice_id}/charges", {"charges": updated})

@mcp.tool(annotations=_WRITE)
def send_invoice_email(
    invoice_id: int,
    to: list[str],
    reply_to: str,
    subject: str,
    body: str,
    cc: Optional[list[str]] = None,
    cc_me: bool = False,
    show_card_payment: bool = False,
    include_pdf: bool = False,
) -> dict:
    """⚠️ Envía la factura por email. Confirmar con usuario antes de ejecutar."""
    payload = {"to": to, "reply_to": reply_to, "subject": subject, "body": body,
               "cc_me": cc_me, "show_card_payment": show_card_payment, "include_pdf": include_pdf}
    if cc:
        payload["cc"] = cc
    return api_post(f"/invoice/{invoice_id}/email", payload)

@mcp.tool(annotations=_WRITE)
def void_invoice(invoice_id: int) -> dict:
    """⚠️ Anula factura Verifactu (irreversible). Solo facturas Verifactu. Confirmar con usuario."""
    return api_post(f"/invoice/{invoice_id}/void")

# ── Gastos ────────────────────────────────────────────────────────────────────

@mcp.tool(annotations=_READ)
def list_expenses(
    initial_date: Optional[str] = None,
    end_date: Optional[str] = None,
    provider: Optional[int] = None,
    expense_type: Optional[str] = None,
    investment_type: Optional[str] = None,
    draft: Optional[bool] = None,
    tags: Optional[str] = None,
    min_total_limit: Optional[float] = None,
    max_total_limit: Optional[float] = None,
    sort: Optional[str] = None,
    page: int = 1,
    page_size: int = 25,
    summary: bool = False,
) -> list:
    """Lista gastos. draft=True borradores, False confirmados. sort ej: 'date:desc'.
    summary=True devuelve solo id, date, provider, total, draft, document_number (menos tokens)."""
    result = api_get("/expense", {
        "initial_date": initial_date, "end_date": end_date, "provider": provider,
        "expense_type": expense_type, "investment_type": investment_type, "draft": draft,
        "tags": tags, "min_total_limit": min_total_limit, "max_total_limit": max_total_limit,
        "sort": sort, "page": page, "page_size": page_size,
    })
    return [_summary_expense(e) for e in result] if summary else result

@mcp.tool(annotations=_IDEMPOTENT)
def get_expense(expense_id: int) -> dict:
    """Detalle completo de un gasto."""
    return api_get(f"/expense/{expense_id}")

@mcp.tool(annotations=_WRITE)
def create_expense(
    date: str,
    draft: bool,
    provider: int,
    document_type: str,
    expense_lines: list[dict],
    payments: list[dict],
    document_number: Optional[str] = None,
    annotations: Optional[str] = None,
    tags: Optional[list[str]] = None,
    vat_eu: Optional[bool] = None,
) -> dict:
    """
    ⚠️ Crea un gasto. Confirmar con usuario.

    document_type: "invoice"|"ticket"
    expense_lines: [{description, base, tax, retention, imputation, expense_type(código contable ej:"621"), investment?, investment_data?}]
    payments: [{amount, payment_method, paid, origin_account(id), date?, destination_account?}]
    vat_eu: True si el CIF del proveedor empieza por "EU".
    """
    body = _opt({"date": date, "draft": draft, "provider": provider, "document_type": document_type,
                 "expense_lines": expense_lines, "payments": payments},
                document_number=document_number, annotations=annotations, tags=tags, vat_eu=vat_eu)
    return api_post("/expense", body)

@mcp.tool(annotations=_WRITE)
def update_expense(
    expense_id: int,
    date: str,
    draft: bool,
    provider: int,
    document_type: str,
    expense_lines: list[dict],
    payments: list[dict],
    document_number: Optional[str] = None,
    annotations: Optional[str] = None,
    tags: Optional[list[str]] = None,
    vat_eu: Optional[bool] = None,
) -> dict:
    """⚠️ Actualiza gasto. Confirmar con usuario. Líneas/pagos: con id=actualiza, sin id=crea, omitidos=eliminados."""
    body = _opt({"date": date, "draft": draft, "provider": provider, "document_type": document_type,
                 "expense_lines": expense_lines, "payments": payments},
                document_number=document_number, annotations=annotations, tags=tags, vat_eu=vat_eu)
    return api_put(f"/expense/{expense_id}", body)

@mcp.tool(annotations=_WRITE)
def delete_expense(expense_id: int) -> dict:
    """⚠️ Elimina un gasto (irreversible). Confirmar con usuario."""
    return api_delete(f"/expense/{expense_id}")

@mcp.tool(annotations=_WRITE)
def update_expense_payments(expense_id: int, payments: list[dict]) -> dict:
    """⚠️ Actualiza pagos de un gasto (ej: marcarlo como pagado). payments: [{id?, amount, payment_method, paid, origin_account, date?}]"""
    return api_put(f"/expense/{expense_id}/payments", {"payments": payments})

@mcp.tool(annotations=_WRITE)
def mark_expense_paid(
    expense_id: int,
    payment_date: str,
    payment_method: Optional[str] = None,
) -> dict:
    """
    ⚠️ Marca todos los pagos de un gasto como pagados. Más rápido que update_expense_payments.
    Obtiene el gasto automáticamente, marca todos los pagos como paid=True y actualiza.
    payment_date en formato yyyy-MM-dd. Confirmar con usuario.
    """
    expense = api_get(f"/expense/{expense_id}")
    payments = expense.get("payments", [])
    updated = []
    for p in payments:
        orig = p.get("origin_account")
        if isinstance(orig, dict):
            orig = orig.get("id")
        updated.append({
            "id": p.get("id"),
            "amount": p.get("amount"),
            "payment_method": payment_method or p.get("payment_method"),
            "origin_account": orig,
            "paid": True,
            "date": payment_date,
        })
    return api_put(f"/expense/{expense_id}/payments", {"payments": updated})

@mcp.tool(annotations=_IDEMPOTENT)
def get_expense_attachment(expense_id: int) -> dict:
    """Adjunto de un gasto en Base64 ({filename, data, mimetype})."""
    return api_get(f"/expense/{expense_id}/attachment")

@mcp.tool(annotations=_WRITE)
def update_expense_attachment(expense_id: int, filename: str, data: str) -> dict:
    """⚠️ Actualiza el adjunto de un gasto. data en Base64. Confirmar con usuario."""
    return api_put(f"/expense/{expense_id}/attachment", {"filename": filename, "data": data})

@mcp.tool(annotations=_WRITE)
def delete_expense_attachment(expense_id: int) -> dict:
    """⚠️ Elimina el adjunto de un gasto (irreversible). Confirmar con usuario."""
    return api_delete(f"/expense/{expense_id}/attachment")

# ── Ingresos ──────────────────────────────────────────────────────────────────

@mcp.tool(annotations=_READ)
def list_income(
    initial_date: Optional[str] = None,
    end_date: Optional[str] = None,
    customer: Optional[int] = None,
    tags: Optional[str] = None,
    min_total_limit: Optional[float] = None,
    max_total_limit: Optional[float] = None,
    sort: Optional[str] = None,
    page: int = 1,
    page_size: int = 25,
    summary: bool = False,
) -> list:
    """Lista ingresos (no facturas). sort ej: 'date:desc'.
    summary=True devuelve solo id, date, customer, total, document_number (menos tokens)."""
    result = api_get("/income", {
        "initial_date": initial_date, "end_date": end_date, "customer": customer,
        "tags": tags, "min_total_limit": min_total_limit, "max_total_limit": max_total_limit,
        "sort": sort, "page": page, "page_size": page_size,
    })
    return [_summary_income(i) for i in result] if summary else result

@mcp.tool(annotations=_IDEMPOTENT)
def get_income(income_id: int) -> dict:
    """Detalle completo de un ingreso."""
    return api_get(f"/income/{income_id}")

@mcp.tool(annotations=_WRITE)
def create_income(
    customer: int,
    income_lines: list[dict],
    charges: list[dict],
    date: Optional[str] = None,
    document_type: Optional[str] = None,
    document_number: Optional[str] = None,
    annotations: Optional[str] = None,
    tags: Optional[list[str]] = None,
) -> dict:
    """
    ⚠️ Crea un ingreso. Confirmar con usuario.

    income_lines: [{concept, amount, tax, retention, imputation, income_type?, tax_regime, tax_subjection_code}]
    charges: [{amount, payment_method, destination_account(id), paid, date?}]
    document_type: "other_invoice"|"cash_statement"|"interest_settlement"|"bank_doc"|"contract"|"resolution"|"other_doc"
    """
    body = _opt({"customer": customer, "income_lines": income_lines, "charges": charges},
                date=date, document_type=document_type, document_number=document_number,
                annotations=annotations, tags=tags)
    return api_post("/income", body)

@mcp.tool(annotations=_WRITE)
def update_income(
    income_id: int,
    customer: int,
    income_lines: list[dict],
    charges: list[dict],
    date: Optional[str] = None,
    document_type: Optional[str] = None,
    document_number: Optional[str] = None,
    annotations: Optional[str] = None,
    tags: Optional[list[str]] = None,
) -> dict:
    """⚠️ Actualiza ingreso. Confirmar con usuario. Líneas/cobros: con id=actualiza, sin id=crea, omitidos=eliminados."""
    body = _opt({"customer": customer, "income_lines": income_lines, "charges": charges},
                date=date, document_type=document_type, document_number=document_number,
                annotations=annotations, tags=tags)
    return api_put(f"/income/{income_id}", body)

@mcp.tool(annotations=_WRITE)
def delete_income(income_id: int) -> dict:
    """⚠️ Elimina un ingreso (irreversible). Confirmar con usuario."""
    return api_delete(f"/income/{income_id}")

@mcp.tool(annotations=_WRITE)
def update_income_charges(income_id: int, charges: list[dict]) -> dict:
    """⚠️ Actualiza cobros de un ingreso. charges: [{id?, paid, amount, date, payment_method, destination_account}]"""
    return api_put(f"/income/{income_id}/charges", {"charges": charges})

@mcp.tool(annotations=_IDEMPOTENT)
def get_income_attachment(income_id: int) -> dict:
    """Adjunto de un ingreso en Base64."""
    return api_get(f"/income/{income_id}/attachment")

@mcp.tool(annotations=_WRITE)
def update_income_attachment(income_id: int, filename: str, data: str) -> dict:
    """⚠️ Actualiza el adjunto de un ingreso. data en Base64. Confirmar con usuario."""
    return api_put(f"/income/{income_id}/attachment", {"filename": filename, "data": data})

@mcp.tool(annotations=_WRITE)
def delete_income_attachment(income_id: int) -> dict:
    """⚠️ Elimina el adjunto de un ingreso (irreversible). Confirmar con usuario."""
    return api_delete(f"/income/{income_id}/attachment")

# ── Clientes ──────────────────────────────────────────────────────────────────

@mcp.tool(annotations=_READ)
def list_customers(
    q: Optional[str] = None,
    page: int = 1,
    page_size: int = 25,
    summary: bool = False,
) -> list:
    """Lista clientes. q busca en razón social, dirección, CIF, teléfono o email.
    summary=True devuelve solo id, tradename, cif, email (menos tokens)."""
    result = api_get("/customer", {"q": q, "page": page, "page_size": page_size})
    return [_summary_customer(c) for c in result] if summary else result

@mcp.tool(annotations=_IDEMPOTENT)
def get_customer(customer_id: int) -> dict:
    """Detalle de un cliente."""
    return api_get(f"/customer/{customer_id}")

@mcp.tool(annotations=_WRITE)
def create_customer(
    business_type: str,
    region: str,
    address: str,
    postal_code: str,
    town: str,
    tradename: str,
    business_name: Optional[str] = None,
    name: Optional[str] = None,
    surname_1: Optional[str] = None,
    surname_2: Optional[str] = None,
    cif: Optional[str] = None,
    tax_id_type: Optional[str] = None,
    country_code: str = "ES",
    email: Optional[str] = None,
    phone: Optional[str] = None,
    web: Optional[str] = None,
    default_payment_method: Optional[str] = None,
    default_invoice_language: Optional[str] = None,
    has_surcharge: Optional[bool] = None,
    contact_person: Optional[str] = None,
    personal_comment: Optional[str] = None,
) -> dict:
    """
    ⚠️ Crea un cliente. Confirmar con usuario.

    business_type: "individual"|"company"|"others"
    Para individual: name y surname_1 obligatorios. Para company/others: business_name obligatorio.
    default_invoice_language: "default"|"es"|"eu"|"ca"|"en"
    tax_id_type: "nif"|"vat_id"|"passport"|"country_document"|"residence_certificate"|"other"|"not_registered"|"unidentified"
    """
    body = _opt({"business_type": business_type, "region": region, "address": address,
                 "postal_code": postal_code, "town": town, "tradename": tradename, "country_code": country_code},
                business_name=business_name, name=name, surname_1=surname_1, surname_2=surname_2,
                cif=cif, tax_id_type=tax_id_type, email=email, phone=phone, web=web,
                default_payment_method=default_payment_method, default_invoice_language=default_invoice_language,
                has_surcharge=has_surcharge, contact_person=contact_person, personal_comment=personal_comment)
    return api_post("/customer", body)

@mcp.tool(annotations=_WRITE)
def update_customer(
    customer_id: int,
    business_type: str,
    region: str,
    address: str,
    postal_code: str,
    town: str,
    tradename: str,
    business_name: Optional[str] = None,
    name: Optional[str] = None,
    surname_1: Optional[str] = None,
    surname_2: Optional[str] = None,
    cif: Optional[str] = None,
    tax_id_type: Optional[str] = None,
    country_code: str = "ES",
    email: Optional[str] = None,
    phone: Optional[str] = None,
    web: Optional[str] = None,
    default_payment_method: Optional[str] = None,
    default_invoice_language: Optional[str] = None,
    has_surcharge: Optional[bool] = None,
    contact_person: Optional[str] = None,
    personal_comment: Optional[str] = None,
) -> dict:
    """⚠️ Actualiza un cliente. Confirmar con usuario. Ver create_customer para valores válidos."""
    body = _opt({"business_type": business_type, "region": region, "address": address,
                 "postal_code": postal_code, "town": town, "tradename": tradename, "country_code": country_code},
                business_name=business_name, name=name, surname_1=surname_1, surname_2=surname_2,
                cif=cif, tax_id_type=tax_id_type, email=email, phone=phone, web=web,
                default_payment_method=default_payment_method, default_invoice_language=default_invoice_language,
                has_surcharge=has_surcharge, contact_person=contact_person, personal_comment=personal_comment)
    return api_put(f"/customer/{customer_id}", body)

@mcp.tool(annotations=_WRITE)
def delete_customer(customer_id: int) -> dict:
    """⚠️ Elimina un cliente (irreversible). Confirmar con usuario."""
    return api_delete(f"/customer/{customer_id}")

# ── Proveedores ───────────────────────────────────────────────────────────────

@mcp.tool(annotations=_READ)
def list_providers(
    q: Optional[str] = None,
    page: int = 1,
    page_size: int = 25,
    summary: bool = False,
) -> list:
    """Lista proveedores. q busca en razón social, dirección, CIF, teléfono o email.
    summary=True devuelve solo id, tradename, cif, email (menos tokens)."""
    result = api_get("/provider", {"q": q, "page": page, "page_size": page_size})
    return [_summary_provider(p) for p in result] if summary else result

@mcp.tool(annotations=_IDEMPOTENT)
def get_provider(provider_id: int) -> dict:
    """Detalle de un proveedor."""
    return api_get(f"/provider/{provider_id}")

@mcp.tool(annotations=_WRITE)
def create_provider(
    business_type: str,
    region: str,
    address: str,
    postal_code: str,
    town: str,
    tradename: str,
    business_name: Optional[str] = None,
    name: Optional[str] = None,
    surname_1: Optional[str] = None,
    surname_2: Optional[str] = None,
    cif: Optional[str] = None,
    tax_id_type: Optional[str] = None,
    country_code: str = "ES",
    email: Optional[str] = None,
    phone: Optional[str] = None,
    web: Optional[str] = None,
    default_payment_method: Optional[str] = None,
    default_retention: Optional[float] = None,
    default_expense_type: Optional[str] = None,
    contact_person: Optional[str] = None,
    personal_comment: Optional[str] = None,
) -> dict:
    """
    ⚠️ Crea un proveedor. Confirmar con usuario.
    default_expense_type: código contable ej "600","621","629". Ver Swagger para lista completa.
    Ver create_customer para business_type, tax_id_type y demás valores válidos.
    """
    body = _opt({"business_type": business_type, "region": region, "address": address,
                 "postal_code": postal_code, "town": town, "tradename": tradename, "country_code": country_code},
                business_name=business_name, name=name, surname_1=surname_1, surname_2=surname_2,
                cif=cif, tax_id_type=tax_id_type, email=email, phone=phone, web=web,
                default_payment_method=default_payment_method, default_retention=default_retention,
                default_expense_type=default_expense_type, contact_person=contact_person,
                personal_comment=personal_comment)
    return api_post("/provider", body)

@mcp.tool(annotations=_WRITE)
def update_provider(
    provider_id: int,
    business_type: str,
    region: str,
    address: str,
    postal_code: str,
    town: str,
    tradename: str,
    business_name: Optional[str] = None,
    name: Optional[str] = None,
    surname_1: Optional[str] = None,
    surname_2: Optional[str] = None,
    cif: Optional[str] = None,
    tax_id_type: Optional[str] = None,
    country_code: str = "ES",
    email: Optional[str] = None,
    phone: Optional[str] = None,
    web: Optional[str] = None,
    default_payment_method: Optional[str] = None,
    default_retention: Optional[float] = None,
    default_expense_type: Optional[str] = None,
    contact_person: Optional[str] = None,
    personal_comment: Optional[str] = None,
) -> dict:
    """⚠️ Actualiza un proveedor. Confirmar con usuario. Ver create_provider para valores válidos."""
    body = _opt({"business_type": business_type, "region": region, "address": address,
                 "postal_code": postal_code, "town": town, "tradename": tradename, "country_code": country_code},
                business_name=business_name, name=name, surname_1=surname_1, surname_2=surname_2,
                cif=cif, tax_id_type=tax_id_type, email=email, phone=phone, web=web,
                default_payment_method=default_payment_method, default_retention=default_retention,
                default_expense_type=default_expense_type, contact_person=contact_person,
                personal_comment=personal_comment)
    return api_put(f"/provider/{provider_id}", body)

@mcp.tool(annotations=_WRITE)
def delete_provider(provider_id: int) -> dict:
    """⚠️ Elimina un proveedor (irreversible). Confirmar con usuario."""
    return api_delete(f"/provider/{provider_id}")

# ── Cuentas bancarias ─────────────────────────────────────────────────────────

@mcp.tool(annotations=_READ)
def list_accounts() -> list:
    """Lista cuentas bancarias, tarjetas y cuentas de socios. type: cash|bank|card|associate."""
    return api_get("/account")

@mcp.tool(annotations=_IDEMPOTENT)
def get_account(account_id: int) -> dict:
    """Detalle de una cuenta bancaria."""
    return api_get(f"/account/{account_id}")

# ── Documentos ────────────────────────────────────────────────────────────────

@mcp.tool(annotations=_READ)
def list_documents(
    initial_date: Optional[str] = None,
    end_date: Optional[str] = None,
    keyword: Optional[str] = None,
    assigned: Optional[bool] = None,
    extension: Optional[str] = None,
    hash: Optional[str] = None,
    sort: Optional[str] = None,
    page: int = 1,
    page_size: int = 25,
) -> list:
    """Lista documentos del buzón. assigned=False pendientes de asignar a gasto. extension ej: '!pdf,jpg'."""
    return api_get("/document", {
        "initial_date": initial_date, "end_date": end_date, "keyword": keyword,
        "assigned": assigned, "extension": extension, "hash": hash,
        "sort": sort, "page": page, "page_size": page_size,
    })

@mcp.tool(annotations=_IDEMPOTENT)
def get_document(document_id: int) -> dict:
    """Detalle de un documento."""
    return api_get(f"/document/{document_id}")

@mcp.tool(annotations=_WRITE)
def create_document(filename: str, data: str, date: Optional[str] = None, expense_id: Optional[int] = None) -> dict:
    """⚠️ Sube documento al buzón. data en Base64. Confirmar con usuario."""
    body = _opt({"attachment": {"filename": filename, "data": data}}, date=date, expense_id=expense_id)
    return api_post("/document", body)

@mcp.tool(annotations=_WRITE)
def update_document(document_id: int, expense_id: Optional[int] = None, date: Optional[str] = None) -> dict:
    """⚠️ Actualiza documento: asignar a gasto y/o cambiar fecha. Confirmar con usuario."""
    return api_put(f"/document/{document_id}", _opt({}, expense_id=expense_id, date=date))

@mcp.tool(annotations=_WRITE)
def delete_document(document_id: int) -> dict:
    """⚠️ Elimina un documento (irreversible). Confirmar con usuario."""
    return api_delete(f"/document/{document_id}")

@mcp.tool(annotations=_IDEMPOTENT)
def get_document_attachment(document_id: int) -> dict:
    """Contenido del adjunto de un documento en Base64."""
    return api_get(f"/document/{document_id}/attachment")

# ── Etiquetas ─────────────────────────────────────────────────────────────────

@mcp.tool(annotations=_READ)
def list_tags() -> list:
    """Lista todas las etiquetas disponibles."""
    return api_get("/tag")

# ── Traspasos ─────────────────────────────────────────────────────────────────

@mcp.tool(annotations=_READ)
def list_transfers(
    origin_account: Optional[int] = None,
    destination_account: Optional[int] = None,
    initial_date: Optional[str] = None,
    end_date: Optional[str] = None,
    min_total_limit: Optional[float] = None,
    max_total_limit: Optional[float] = None,
    payment_method: Optional[str] = None,
    sort: Optional[str] = None,
    page: int = 1,
    page_size: int = 25,
) -> list:
    """Lista traspasos entre cuentas. payment_method: cash|wire_transfer|promissory_note."""
    return api_get("/transfer", {
        "origin_account": origin_account, "destination_account": destination_account,
        "initial_date": initial_date, "end_date": end_date,
        "min_total_limit": min_total_limit, "max_total_limit": max_total_limit,
        "payment_method": payment_method, "sort": sort, "page": page, "page_size": page_size,
    })

@mcp.tool(annotations=_IDEMPOTENT)
def get_transfer(transfer_id: int) -> dict:
    """Detalle de un traspaso."""
    return api_get(f"/transfer/{transfer_id}")

@mcp.tool(annotations=_WRITE)
def create_transfer(
    amount: float,
    concept: str,
    origin_account: int,
    destination_account: int,
    date: Optional[str] = None,
    payment_method: Optional[str] = None,
) -> dict:
    """⚠️ Crea traspaso entre cuentas. payment_method: cash|wire_transfer|promissory_note. Confirmar con usuario."""
    body = _opt({"amount": amount, "concept": concept,
                 "origin_account": origin_account, "destination_account": destination_account},
                date=date, payment_method=payment_method)
    return api_post("/transfer", body)

@mcp.tool(annotations=_WRITE)
def update_transfer(
    transfer_id: int,
    amount: float,
    concept: str,
    origin_account: int,
    destination_account: int,
    date: Optional[str] = None,
    payment_method: Optional[str] = None,
) -> dict:
    """⚠️ Actualiza un traspaso. Confirmar con usuario."""
    body = _opt({"amount": amount, "concept": concept,
                 "origin_account": origin_account, "destination_account": destination_account},
                date=date, payment_method=payment_method)
    return api_put(f"/transfer/{transfer_id}", body)

@mcp.tool(annotations=_WRITE)
def delete_transfer(transfer_id: int) -> dict:
    """⚠️ Elimina un traspaso (irreversible). Confirmar con usuario."""
    return api_delete(f"/transfer/{transfer_id}")

# ── Agregaciones ──────────────────────────────────────────────────────────────

@mcp.tool(annotations=_READ)
def get_pending_collections(year: Optional[int] = None) -> dict:
    """
    Resumen de facturas emitidas pendientes de cobro. Itera todas las páginas automáticamente.
    Excluye facturas anuladas (voided) y ya cobradas (paid=True).
    year: filtra por año (ej: 2026). Sin year devuelve todos los pendientes.
    Devuelve: {count, total_pending, invoices: [{id, number, date, customer, total, status}]}
    """
    params: dict = {"issued": True}
    if year:
        params["initial_date"] = f"{year}-01-01"
        params["end_date"] = f"{year}-12-31"
    all_invoices = _all_pages("/invoice", params)

    pending = []
    for inv in all_invoices:
        ri = inv.get("register_info") or {}
        if ri.get("status_description") == "voided":
            continue
        if ri.get("paid"):
            continue
        cust = inv.get("customer") or {}
        pending.append({
            "id": inv.get("id"),
            "number": inv.get("number"),
            "date": inv.get("date"),
            "customer": cust.get("tradename") or cust.get("business_name"),
            "total": inv.get("total"),
            "status": ri.get("status_description"),
        })

    total = sum((p.get("total") or 0) for p in pending)
    return {"count": len(pending), "total_pending": round(total, 2), "invoices": pending}

@mcp.tool(annotations=_READ)
def get_quarterly_vat_summary(year: int, quarter: int) -> dict:
    """
    Resumen de IVA de un trimestre (quarter: 1-4). Una sola llamada en vez de iterar manualmente.
    Agrega todas las facturas emitidas y gastos del período, calcula IVA repercutido y soportado.
    Devuelve: {period, invoices: {count, taxable_base, vat_charged, retention}, expenses: {count, taxable_base, vat_deductible}, result: {net_vat_payable}}
    """
    q_ranges = {
        1: ("01-01", "03-31"),
        2: ("04-01", "06-30"),
        3: ("07-01", "09-30"),
        4: ("10-01", "12-31"),
    }
    if quarter not in q_ranges:
        raise ValueError("quarter debe ser 1, 2, 3 o 4")
    start_suffix, end_suffix = q_ranges[quarter]
    start = f"{year}-{start_suffix}"
    end   = f"{year}-{end_suffix}"

    invoices = _all_pages("/invoice", {"issued": True, "initial_date": start, "end_date": end})
    expenses = _all_pages("/expense", {"draft": False, "initial_date": start, "end_date": end})

    inv_base = inv_vat = inv_retention = 0.0
    valid_invoices = 0
    for inv in invoices:
        ri = inv.get("register_info") or {}
        if ri.get("status_description") == "voided":
            continue
        valid_invoices += 1
        for line in inv.get("invoice_lines") or []:
            qty      = line.get("quantity") or 1
            price    = line.get("amount") or 0
            disc_pct = (line.get("discount") or 0) / 100
            base     = qty * price * (1 - disc_pct)
            tax_pct  = (line.get("tax") or 0) / 100
            ret_pct  = (line.get("retention") or 0) / 100
            inv_base      += base
            inv_vat       += base * tax_pct
            inv_retention += base * ret_pct

    exp_base = exp_vat = 0.0
    for exp in expenses:
        for line in exp.get("expense_lines") or []:
            base    = line.get("base") or 0
            tax_pct = (line.get("tax") or 0) / 100
            exp_base += base
            exp_vat  += base * tax_pct

    return {
        "period": f"T{quarter}/{year}",
        "invoices": {
            "count": valid_invoices,
            "taxable_base": round(inv_base, 2),
            "vat_charged": round(inv_vat, 2),
            "retention": round(inv_retention, 2),
        },
        "expenses": {
            "count": len(expenses),
            "taxable_base": round(exp_base, 2),
            "vat_deductible": round(exp_vat, 2),
        },
        "result": {
            "net_vat_payable": round(inv_vat - exp_vat, 2),
            "note": "IVA repercutido − IVA soportado. No incluye ingresos (income). Verifica con tu asesor.",
        },
    }

@mcp.tool(annotations=_READ)
def get_balance_summary() -> dict:
    """
    Resumen de saldos de todas las cuentas bancarias, tarjetas y efectivo. Una sola llamada.
    Devuelve: {total_balance, accounts: [{id, name, type, balance}]}
    """
    accounts = api_get("/account")
    result = []
    total = 0.0
    for acc in accounts:
        balance = acc.get("balance") or 0
        total += balance
        result.append({
            "id": acc.get("id"),
            "name": acc.get("name"),
            "type": acc.get("type"),
            "balance": balance,
        })
    return {"total_balance": round(total, 2), "accounts": result}

# ── MCP Resources — catálogos ligeros ─────────────────────────────────────────

@mcp.resource("cuentica://catalog/customers")
def catalog_customers() -> list:
    """Catálogo de clientes: id, tradename, cif, email. Útil para buscar IDs antes de crear facturas."""
    data = api_get("/customer", {"page": 1, "page_size": MAX_PAGE_SIZE})
    return [_summary_customer(c) for c in data]

@mcp.resource("cuentica://catalog/providers")
def catalog_providers() -> list:
    """Catálogo de proveedores: id, tradename, cif, email. Útil para buscar IDs antes de crear gastos."""
    data = api_get("/provider", {"page": 1, "page_size": MAX_PAGE_SIZE})
    return [_summary_provider(p) for p in data]

@mcp.resource("cuentica://catalog/accounts")
def catalog_accounts() -> list:
    """Catálogo de cuentas bancarias: id, name, type, balance. Útil para obtener IDs de cuentas."""
    accounts = api_get("/account")
    return [{"id": a.get("id"), "name": a.get("name"), "type": a.get("type"), "balance": a.get("balance")} for a in accounts]

@mcp.resource("cuentica://catalog/tags")
def catalog_tags() -> list:
    """Catálogo de etiquetas disponibles: id, name."""
    tags = api_get("/tag")
    return [{"id": t.get("id"), "name": t.get("name")} for t in tags]

# ── Entry point ───────────────────────────────────────────────────────────────

def main():
    mcp.run()

if __name__ == "__main__":
    main()
