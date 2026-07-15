import io
import zipfile
from collections import defaultdict

from fastapi import FastAPI, Request, Depends, Form
from fastapi.responses import HTMLResponse, RedirectResponse, StreamingResponse, PlainTextResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session
from sqlalchemy import distinct

from app.config import APP_PASSWORD
from app.db import get_db, init_db
from app.models import Purchase
from app.auth import require_login, is_valid_session, create_session_cookie, COOKIE_NAME
from app import gmail_sync
from app.excel_export import build_workbook

app = FastAPI(title="GridWorks Contabilidad")
templates = Jinja2Templates(directory="app/templates")


@app.on_event("startup")
def on_startup():
    init_db()


@app.get("/health")
def health():
    return PlainTextResponse("ok")


@app.get("/login", response_class=HTMLResponse)
def login_form(request: Request):
    if is_valid_session(request):
        return RedirectResponse("/", status_code=303)
    return templates.TemplateResponse("login.html", {"request": request, "authenticated": False})


@app.post("/login")
def login_submit(request: Request, password: str = Form(...)):
    if APP_PASSWORD and password == APP_PASSWORD:
        resp = RedirectResponse("/", status_code=303)
        resp.set_cookie(COOKIE_NAME, create_session_cookie(), httponly=True, samesite="lax", max_age=60 * 60 * 24 * 30)
        return resp
    return templates.TemplateResponse(
        "login.html", {"request": request, "authenticated": False, "error": "Clave incorrecta"}, status_code=401
    )


@app.get("/logout")
def logout():
    resp = RedirectResponse("/login", status_code=303)
    resp.delete_cookie(COOKIE_NAME)
    return resp


@app.get("/", response_class=HTMLResponse)
def dashboard(request: Request, mes: str = "", flash: str = "", db: Session = Depends(get_db)):
    require_login(request)

    periodos = [p[0] for p in db.query(distinct(Purchase.periodo)).order_by(Purchase.periodo.desc()).all() if p[0]]

    query = db.query(Purchase)
    if mes:
        query = query.filter(Purchase.periodo == mes)
    purchases = query.order_by(Purchase.fecha.desc().nullslast()).all()

    totales = {
        "count": len(purchases),
        "afecto": sum(float(p.monto_afecto or 0) for p in purchases),
        "iva": sum(float(p.iva or 0) for p in purchases),
        "total": sum(float(p.total or 0) for p in purchases),
    }

    return templates.TemplateResponse(
        "index.html",
        {
            "request": request,
            "authenticated": True,
            "purchases": purchases,
            "periodos": periodos,
            "mes_seleccionado": mes,
            "totales": totales,
            "flash": flash,
        },
    )


@app.post("/sync")
def trigger_sync(request: Request, db: Session = Depends(get_db)):
    require_login(request)
    try:
        stats = gmail_sync.sync(db)
        flash = (
            f"Listo. Revisados {stats['revisados']}, nuevos {stats['nuevos']}, "
            f"ya procesados {stats['omitidos_ya_procesados']}, sin factura {stats['sin_invoice']}, "
            f"errores {stats['errores']}."
        )
    except Exception as e:
        flash = f"Error al sincronizar: {e}"
    return RedirectResponse(f"/?flash={flash}", status_code=303)


@app.get("/export.xlsx")
def export_excel(request: Request, mes: str = "", db: Session = Depends(get_db)):
    require_login(request)
    query = db.query(Purchase)
    if mes:
        query = query.filter(Purchase.periodo == mes)
    purchases = query.order_by(Purchase.fecha.asc().nullslast()).all()

    label = mes or "todos los periodos"
    content = build_workbook(purchases, label)
    filename = f"Libro Compras {mes or 'todos'}.xlsx"
    return StreamingResponse(
        io.BytesIO(content),
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@app.get("/export/pdfs.zip")
def export_pdfs(request: Request, mes: str = "", db: Session = Depends(get_db)):
    require_login(request)
    query = db.query(Purchase)
    if mes:
        query = query.filter(Purchase.periodo == mes)
    purchases = query.all()

    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        used_names = defaultdict(int)
        for p in purchases:
            if not p.pdf_data:
                continue
            name = p.pdf_filename or f"{p.numero or p.id}.pdf"
            if used_names[name]:
                name = f"{used_names[name]}_{name}"
            used_names[name] += 1
            zf.writestr(name, p.pdf_data)
    buf.seek(0)

    filename = f"Facturas {mes or 'todos'}.zip"
    return StreamingResponse(
        buf, media_type="application/zip", headers={"Content-Disposition": f'attachment; filename="{filename}"'}
    )


@app.get("/pdf/{purchase_id}")
def view_pdf(request: Request, purchase_id: int, db: Session = Depends(get_db)):
    require_login(request)
    p = db.query(Purchase).get(purchase_id)
    if not p or not p.pdf_data:
        return PlainTextResponse("No encontrado", status_code=404)
    return StreamingResponse(
        io.BytesIO(p.pdf_data),
        media_type="application/pdf",
        headers={"Content-Disposition": f'inline; filename="{p.pdf_filename or "documento.pdf"}"'},
    )
