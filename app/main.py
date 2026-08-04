import io
import threading
import zipfile
from collections import defaultdict

from fastapi import FastAPI, Request, Depends, Form, BackgroundTasks
from fastapi.responses import HTMLResponse, RedirectResponse, StreamingResponse, PlainTextResponse, JSONResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session
from sqlalchemy import distinct

from app.config import COOKIE_SECURE, NOTIFY_EMAIL, GMAIL_LABEL, BASE_URL
from app.db import get_db, init_db, SessionLocal
from app.models import Purchase, Usuario
from app.auth import (require_login, usuario_actual, create_session_cookie, crear_token_reset,
                      leer_token_reset, COOKIE_NAME, MAX_AGE)
from app import gmail_sync, mantenedor, usuarios
from app.usuarios import Resultado, LARGO_MINIMO_CLAVE
from app.excel_export import build_workbook

app = FastAPI(title="GridWorks Contabilidad")
templates = Jinja2Templates(directory="app/templates")


@app.on_event("startup")
def on_startup():
    init_db()
    db = SessionLocal()
    try:
        mantenedor.ensure_seed(db)
        sembrado = usuarios.sembrar_admin_inicial(db)
        if sembrado:
            print(f"[arranque] Cuenta inicial creada: {sembrado.email}")
    finally:
        db.close()


@app.get("/health")
def health():
    return PlainTextResponse("ok")


def _set_session(resp, u: Usuario):
    resp.set_cookie(COOKIE_NAME, create_session_cookie(u), httponly=True, samesite="lax",
                    secure=COOKIE_SECURE, max_age=MAX_AGE)
    return resp


@app.get("/login", response_class=HTMLResponse)
def login_form(request: Request, db: Session = Depends(get_db)):
    if usuario_actual(request, db):
        return RedirectResponse("/", status_code=303)
    return templates.TemplateResponse("login.html", {"request": request, "usuario": None})


@app.post("/login")
def login_submit(request: Request, email: str = Form(...), password: str = Form(...),
                 db: Session = Depends(get_db)):
    resultado, u = usuarios.autenticar(db, email, password)

    if resultado is Resultado.OK:
        return _set_session(RedirectResponse("/", status_code=303), u)

    if resultado is Resultado.BLOQUEADO:
        error = (f"Demasiados intentos fallidos. Espera {usuarios.BLOQUEO_MINUTOS} minutos "
                 "o restablece tu clave.")
    else:
        # Credenciales malas, correo inexistente y cuenta dada de baja dan el
        # mismo mensaje, para no revelar que cuentas existen.
        error = "Correo o clave incorrectos."

    return templates.TemplateResponse(
        "login.html",
        {"request": request, "usuario": None, "error": error, "email": email},
        status_code=401,
    )


@app.get("/logout")
def logout():
    resp = RedirectResponse("/login", status_code=303)
    resp.delete_cookie(COOKIE_NAME)
    return resp


@app.get("/", response_class=HTMLResponse)
def dashboard(request: Request, mes: str = "", flash: str = "",
              usuario: Usuario = Depends(require_login), db: Session = Depends(get_db)):
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
            "usuario": usuario,
            "purchases": purchases,
            "periodos": periodos,
            "mes_seleccionado": mes,
            "totales": totales,
            "flash": flash,
        },
    )


# --- Sincronizacion en segundo plano con progreso (estado en memoria, 1 instancia) ---
_sync_state = {"running": False, "terminado": False, "total": 0, "procesados": 0,
               "nuevos": 0, "omitidos": 0, "falta_proveedor": 0, "errores": 0, "mensaje": ""}
_sync_lock = threading.Lock()


def _run_sync_bg():
    db = SessionLocal()
    try:
        stats = gmail_sync.sync(db, progress=_sync_state)
        _sync_state["mensaje"] = (
            f"Listo. Nuevos {stats['nuevos']}, ya en base {stats['omitidos_ya_en_base']}, "
            f"sin factura {stats['sin_invoice']}, falta proveedor {stats['falta_proveedor']}, "
            f"errores {stats['errores']}."
        )
    except Exception as e:
        _sync_state["mensaje"] = f"Error al sincronizar: {e}"
    finally:
        db.close()
        _sync_state["running"] = False
        _sync_state["terminado"] = True


@app.post("/sync")
def trigger_sync(request: Request, usuario: Usuario = Depends(require_login)):
    with _sync_lock:
        if _sync_state["running"]:
            return JSONResponse({"running": True, "ya_en_curso": True})
        _sync_state.update({"running": True, "terminado": False, "total": 0, "procesados": 0,
                            "nuevos": 0, "omitidos": 0, "falta_proveedor": 0, "errores": 0,
                            "mensaje": "Conectando a Gmail..."})
    threading.Thread(target=_run_sync_bg, daemon=True).start()
    return JSONResponse({"running": True})


@app.get("/sync/estado")
def sync_estado(request: Request, usuario: Usuario = Depends(require_login)):
    return JSONResponse(_sync_state)


@app.post("/aceptar/{purchase_id}")
def aceptar_uno(request: Request, purchase_id: int, mes: str = Form(""),
                usuario: Usuario = Depends(require_login), db: Session = Depends(get_db)):
    p = db.query(Purchase).get(purchase_id)
    if not p:
        return RedirectResponse("/?flash=Documento no encontrado", status_code=303)
    if p.estado == "aceptada":
        return RedirectResponse(f"/?mes={mes}&flash=Ya estaba aceptada", status_code=303)
    try:
        res = gmail_sync.mover_a_declaradas(p.gmail_message_id)
        p.estado = "aceptada"
        db.commit()
        flash = f"Factura {p.numero or p.id} aceptada y movida a declaradas."
        if res.get("no_encontrados"):
            flash += " (Aviso: no se encontro el correo en Gmail para mover el label.)"
    except Exception as e:
        flash = f"Error al aceptar: {e}"
    return RedirectResponse(f"/?mes={mes}&flash={flash}", status_code=303)


# --- Aceptacion por lote en segundo plano con progreso ---
_accept_state = {"running": False, "terminado": False, "total": 0, "procesados": 0,
                 "movidos": 0, "no_encontrados": 0, "mensaje": ""}
_accept_lock = threading.Lock()


def _pendientes_aceptables(db, mes):
    q = db.query(Purchase).filter(Purchase.estado != "aceptada", Purchase.revision_manual == False)  # noqa: E712
    if mes:
        q = q.filter(Purchase.periodo == mes)
    return q


def _run_aceptar_bg(mes: str):
    db = SessionLocal()
    try:
        pend = _pendientes_aceptables(db, mes).all()
        by_mid = {}
        ids = []
        resumen = []  # para el correo de aviso
        for p in pend:
            if p.gmail_message_id:
                by_mid.setdefault(p.gmail_message_id, []).append(p.id)
                ids.append(p.gmail_message_id)
            resumen.append((p.numero, p.proveedor, float(p.total) if p.total is not None else None, p.moneda))

        def on_result(mid, ok):
            for pid in by_mid.get(mid, []):
                fila = db.query(Purchase).get(pid)
                if fila:
                    fila.estado = "aceptada"
            db.commit()

        stats = gmail_sync.mover_a_declaradas(ids, progress=_accept_state, on_result=on_result)
        _accept_state["mensaje"] = f"{stats['movidos']} facturas aceptadas y movidas a declaradas."
        if stats.get("no_encontrados"):
            _accept_state["mensaje"] += f" {stats['no_encontrados']} sin correo encontrado en Gmail."

        # Aviso por correo a contabilidad (con el Excel adjunto y la etiqueta)
        if stats["movidos"] > 0:
            try:
                _enviar_aviso_declaradas(db, mes, stats["movidos"], resumen)
                _accept_state["mensaje"] += f" Aviso enviado a {NOTIFY_EMAIL}."
            except Exception as e:
                _accept_state["mensaje"] += f" (No se pudo enviar el aviso: {e})"
    except Exception as e:
        _accept_state["mensaje"] = f"Error al aceptar el lote: {e}"
    finally:
        db.close()
        _accept_state["running"] = False
        _accept_state["terminado"] = True


def _libro_excel(db: Session, mes: str):
    """Genera el Libro de Compras .xlsx para un mes (mismo contenido que /export.xlsx)."""
    query = db.query(Purchase)
    if mes:
        query = query.filter(Purchase.periodo == mes)
    purchases = query.order_by(Purchase.fecha.asc().nullslast()).all()
    content = build_workbook(purchases, mes or "todos los periodos")
    filename = f"Libro Compras {mes or 'todos'}.xlsx"
    return filename, content


def _enviar_aviso_declaradas(db, mes, cantidad, resumen):
    periodo = mes or "todos los periodos"
    lineas = []
    for numero, proveedor, total, moneda in resumen:
        monto = f"{total:,.2f} {moneda}" if total is not None else ""
        lineas.append(f"- {numero or 's/n'} · {proveedor or ''} · {monto}".rstrip())
    asunto = f"Compras declaradas: {cantidad} facturas ({periodo})"
    cuerpo = (
        f"Se aceptaron y movieron a 'Compras Declaradas' {cantidad} facturas "
        f"({periodo}). Se adjunta el Libro de Compras en Excel.\n\n" + "\n".join(lineas) + "\n"
    )
    filename, content = _libro_excel(db, mes)
    adjunto = (filename, content, "vnd.openxmlformats-officedocument.spreadsheetml.sheet")
    gmail_sync.enviar_correo_con_etiqueta(NOTIFY_EMAIL, asunto, cuerpo, GMAIL_LABEL, adjunto=adjunto)


@app.post("/aceptar-mes")
def aceptar_mes(request: Request, mes: str = Form(""),
                usuario: Usuario = Depends(require_login), db: Session = Depends(get_db)):
    with _accept_lock:
        if _accept_state["running"]:
            return JSONResponse({"running": True, "ya_en_curso": True})
        n = _pendientes_aceptables(db, mes).count()
        if n == 0:
            return JSONResponse({"running": False, "vacio": True,
                                 "mensaje": "No hay pendientes aceptables en el periodo."})
        _accept_state.update({"running": True, "terminado": False, "total": n, "procesados": 0,
                              "movidos": 0, "no_encontrados": 0, "mensaje": "Conectando a Gmail..."})
    threading.Thread(target=_run_aceptar_bg, args=(mes,), daemon=True).start()
    return JSONResponse({"running": True})


@app.get("/aceptar-mes/estado")
def aceptar_mes_estado(request: Request, usuario: Usuario = Depends(require_login)):
    return JSONResponse(_accept_state)


@app.get("/export.xlsx")
def export_excel(request: Request, mes: str = "",
                 usuario: Usuario = Depends(require_login), db: Session = Depends(get_db)):
    filename, content = _libro_excel(db, mes)
    return StreamingResponse(
        io.BytesIO(content),
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@app.get("/export/pdfs.zip")
def export_pdfs(request: Request, mes: str = "",
                usuario: Usuario = Depends(require_login), db: Session = Depends(get_db)):
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
def view_pdf(request: Request, purchase_id: int,
             usuario: Usuario = Depends(require_login), db: Session = Depends(get_db)):
    p = db.query(Purchase).get(purchase_id)
    if not p or not p.pdf_data:
        return PlainTextResponse("No encontrado", status_code=404)
    return StreamingResponse(
        io.BytesIO(p.pdf_data),
        media_type="application/pdf",
        headers={"Content-Disposition": f'inline; filename="{p.pdf_filename or "documento.pdf"}"'},
    )
