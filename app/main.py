import io
import logging
import re
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
from app.security import verify_password
from app.excel_export import build_workbook

# Se cuelga del logger de uvicorn y no de uno propio: uvicorn le configura un
# handler a nivel INFO, mientras que un logger nuevo hereda el root, que no
# tiene handler y descarta todo lo que sea menor a WARNING. Con un logger
# propio los avisos de arranque (cuenta inicial creada, BASE_URL) no salian por
# ninguna parte, que es justo lo que se mira para verificar un despliegue.
log = logging.getLogger("uvicorn.error")

# Cota superior de la clave enviada en el login: hashear es caro (scrypt) y el
# campo es publico, asi que una entrada absurdamente larga se rechaza antes de
# gastar ese costo.
LARGO_MAXIMO_CLAVE = 200

# 320 es el largo maximo practico de una direccion de correo (RFC 5321). Un
# valor mas largo no puede ser una cuenta real, asi que se corta antes de
# tocar la base.
LARGO_MAXIMO_EMAIL = 320

# El nombre es solo para mostrar en la lista de usuarios: no hay razon para
# aceptar algo mas largo que esto en un campo de texto libre.
LARGO_MAXIMO_NOMBRE = 200

# Chequeo deliberadamente simple (no RFC 5322 completo): alcanza para atajar
# un valor vacio o sin forma de correo antes de crear una cuenta inservible y
# mandar un enlace a una direccion que no es tal. No vale la pena sumar una
# dependencia de validacion para esto.
_PATRON_EMAIL = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")

# /docs, /redoc y /openapi.json quedan desactivados: en un host que sirve
# facturas no tiene sentido publicar toda la superficie de la API (rutas de
# PDF, exportacion, aceptacion) ni dejar una consola "Try it out" abierta.
app = FastAPI(title="GridWorks Contabilidad", docs_url=None, redoc_url=None, openapi_url=None)
templates = Jinja2Templates(directory="app/templates")


@app.on_event("startup")
def on_startup():
    init_db()
    log.info("BASE_URL para los enlaces de correo: %s", BASE_URL)
    db = SessionLocal()
    try:
        mantenedor.ensure_seed(db)
        try:
            sembrado = usuarios.sembrar_admin_inicial(db)
            if sembrado:
                log.info("Cuenta inicial creada: %s", sembrado.email)
        except Exception as e:
            # La siembra es una comodidad de arranque, no una condicion para
            # servir. Si falla, la aplicacion igual tiene que levantar: caerse
            # aqui dejaria el sitio entero abajo por no poder crear una cuenta.
            log.error("No se pudo crear la cuenta inicial: %s", e)
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
    if len(password) > LARGO_MAXIMO_CLAVE:
        # Mismo mensaje generico que cualquier otra falla: no delata que el
        # largo fue el motivo, y se evita el costo de scrypt en una entrada
        # que ya se sabe invalida.
        return templates.TemplateResponse(
            "login.html",
            {"request": request, "usuario": None, "error": "Correo o clave incorrectos.", "email": email},
            status_code=401,
        )

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


MENSAJE_ENLACE = "Si el correo está registrado, te enviamos un enlace para restablecer tu clave."


def _enviar_enlace_reset(email: str, token: str) -> None:
    """Corre DESPUES de responder, no dentro del request. Recibe valores planos
    y no el objeto ORM, porque para cuando esto se ejecuta la sesion de base de
    datos ya se cerro.

    Silencia los errores de SMTP a proposito: si el correo no sale, la persona
    no debe enterarse por un 500, y el aviso queda en el log."""
    cuerpo = (
        f"Para definir tu clave de acceso al Libro de Compras, entra a este enlace:\n\n"
        f"{BASE_URL}/restablecer/{token}\n\n"
        f"El enlace vence en 30 minutos y sirve una sola vez.\n"
        f"Si no pediste esto, ignora el correo: tu clave actual sigue funcionando.\n"
    )
    try:
        gmail_sync.enviar_correo(email, "Restablecer tu clave · Libro de Compras", cuerpo)
    except Exception as e:
        log.error("No se pudo enviar el enlace de recuperacion a %s: %s", email, e)


def _validar_clave_nueva(password: str, password2: str):
    """Devuelve el mensaje de error, o None si esta bien."""
    if password != password2:
        return "Las dos claves no coinciden."
    if len(password) < LARGO_MINIMO_CLAVE:
        return f"La clave debe tener al menos {LARGO_MINIMO_CLAVE} caracteres."
    if len(password) > LARGO_MAXIMO_CLAVE:
        return "Esa clave es demasiado larga."
    return None


@app.get("/olvide-clave", response_class=HTMLResponse)
def olvide_clave_form(request: Request):
    return templates.TemplateResponse("olvide_clave.html", {"request": request, "usuario": None})


@app.post("/olvide-clave", response_class=HTMLResponse)
def olvide_clave_submit(request: Request, tareas: BackgroundTasks, email: str = Form(...),
                        db: Session = Depends(get_db)):
    # Una entrada mas larga que un correo real no puede tener cuenta, asi que
    # se corta antes de consultar la base. No se distingue esta salida de las
    # demas: mismo mensaje, misma forma de respuesta.
    if len(email) <= LARGO_MAXIMO_EMAIL:
        u = usuarios.por_email(db, email)
        # El turno se toma AQUI, dentro del request, y el envio se agenda para
        # despues de responder. Si el correo se mandara aqui mismo, un correo
        # con cuenta tardaria lo que tarda el SMTP y uno sin cuenta contestaria
        # al instante: el mensaje seria el mismo pero el tiempo delataria
        # cuales existen, que es justo lo que este flujo trata de no revelar.
        if u and u.activo and usuarios.reclamar_envio_reset(db, u):
            tareas.add_task(_enviar_enlace_reset, u.email, crear_token_reset(u))
    return templates.TemplateResponse(
        "olvide_clave.html", {"request": request, "usuario": None, "mensaje": MENSAJE_ENLACE}
    )


@app.get("/restablecer/{token}", response_class=HTMLResponse)
def restablecer_form(request: Request, token: str, db: Session = Depends(get_db)):
    u = leer_token_reset(token, db)
    if u is None:
        return templates.TemplateResponse(
            "restablecer.html", {"request": request, "usuario": None, "invalido": True},
            status_code=400,
        )
    return templates.TemplateResponse(
        "restablecer.html", {"request": request, "usuario": None, "token": token, "email": u.email}
    )


@app.post("/restablecer/{token}", response_class=HTMLResponse)
def restablecer_submit(request: Request, token: str, password: str = Form(...),
                       password2: str = Form(...), db: Session = Depends(get_db)):
    u = leer_token_reset(token, db)
    if u is None:
        return templates.TemplateResponse(
            "restablecer.html", {"request": request, "usuario": None, "invalido": True},
            status_code=400,
        )

    error = _validar_clave_nueva(password, password2)
    if error:
        return templates.TemplateResponse(
            "restablecer.html",
            {"request": request, "usuario": None, "token": token, "email": u.email, "error": error},
            status_code=400,
        )

    # cambiar_clave devuelve False si otra peticion redimio el mismo enlace
    # primero. En ese caso el enlace ya no sirve y hay que decirlo, no fingir
    # que se guardo.
    if not usuarios.cambiar_clave(db, u, password):
        return templates.TemplateResponse(
            "restablecer.html", {"request": request, "usuario": None, "invalido": True},
            status_code=400,
        )
    return RedirectResponse("/login", status_code=303)


@app.get("/cuenta", response_class=HTMLResponse)
def cuenta_form(request: Request, ok: str = "", usuario: Usuario = Depends(require_login)):
    mensaje = "Tu clave quedó cambiada. Las sesiones en otros dispositivos se cerraron." if ok else None
    return templates.TemplateResponse(
        "cuenta.html", {"request": request, "usuario": usuario, "mensaje": mensaje}
    )


@app.post("/cuenta", response_class=HTMLResponse)
def cuenta_submit(request: Request, actual: str = Form(...), password: str = Form(...),
                  password2: str = Form(...), usuario: Usuario = Depends(require_login),
                  db: Session = Depends(get_db)):
    if not verify_password(actual, usuario.password_hash):
        error = "Tu clave actual no es correcta."
    else:
        error = _validar_clave_nueva(password, password2)

    if error:
        return templates.TemplateResponse(
            "cuenta.html", {"request": request, "usuario": usuario, "error": error}, status_code=400
        )

    if not usuarios.cambiar_clave(db, usuario, password):
        return templates.TemplateResponse(
            "cuenta.html",
            {"request": request, "usuario": usuario,
             "error": "Tu clave cambió desde otra pestaña. Vuelve a intentarlo."},
            status_code=409,
        )
    # cambiar_clave subio token_version: hay que reemitir la cookie propia para
    # no quedar afuera junto con las sesiones de los otros dispositivos.
    resp = RedirectResponse("/cuenta?ok=1", status_code=303)
    return _set_session(resp, usuario)


def _validar_invitacion(email: str, nombre: str):
    """Devuelve el mensaje de error, o None si esta bien. email debe venir ya
    normalizado (usuarios.normalizar_email), asi el mensaje y la creacion
    posterior usan el mismo valor."""
    if len(email) > LARGO_MAXIMO_EMAIL:
        return "Ese correo es demasiado largo."
    if not _PATRON_EMAIL.match(email):
        return "Ingresa un correo válido."
    if len(nombre) > LARGO_MAXIMO_NOMBRE:
        return "Ese nombre es demasiado largo."
    return None


def _resolver_objetivo(db: Session, usuario_id: str):
    """Devuelve el Usuario del id recibido en el form, o None si el valor no
    es un id valido o no existe. usuario_id llega como texto de un
    <input type="hidden">, asi que puede venir vacio, no numerico, o un
    numero gigante: ninguno de esos casos debe tumbar la ruta con un 500 (el
    mismo tope que app/auth.py aplica al id de la cookie, porque un entero
    valido en Python pero mas grande que un INTEGER de la base revienta el
    driver con OverflowError/DataError)."""
    try:
        objetivo_id = int(usuario_id)
    except ValueError:
        return None
    if not 0 < objetivo_id < 2 ** 31:
        return None
    return db.query(Usuario).filter(Usuario.id == objetivo_id).first()


def _pantalla_usuarios(request, db, usuario, mensaje=None, error=None, status=200):
    filas = db.query(Usuario).order_by(Usuario.email).all()
    return templates.TemplateResponse(
        "usuarios.html",
        {"request": request, "usuario": usuario, "usuarios": filas,
         "mensaje": mensaje, "error": error},
        status_code=status,
    )


@app.get("/usuarios", response_class=HTMLResponse)
def usuarios_lista(request: Request, usuario: Usuario = Depends(require_login),
                   db: Session = Depends(get_db)):
    return _pantalla_usuarios(request, db, usuario)


@app.post("/usuarios", response_class=HTMLResponse)
def usuarios_accion(request: Request, tareas: BackgroundTasks, accion: str = Form(...),
                    email: str = Form(""), nombre: str = Form(""), usuario_id: str = Form(""),
                    usuario: Usuario = Depends(require_login), db: Session = Depends(get_db)):
    if accion == "invitar":
        correo = usuarios.normalizar_email(email)
        error = _validar_invitacion(correo, nombre)
        if error:
            return _pantalla_usuarios(request, db, usuario, error=error, status=400)

        if usuarios.por_email(db, correo):
            return _pantalla_usuarios(request, db, usuario,
                                      error=f"{correo} ya tiene cuenta.", status=400)
        nuevo = usuarios.crear(db, correo, nombre=nombre)
        usuarios.reclamar_envio_reset(db, nuevo)  # cuenta recien creada: siempre gana el turno
        tareas.add_task(_enviar_enlace_reset, nuevo.email, crear_token_reset(nuevo))
        return _pantalla_usuarios(
            request, db, usuario,
            mensaje=(f"Cuenta creada. Le enviaremos a {nuevo.email} un enlace para definir su clave. "
                     "Si no le llega, puede pedirlo con 'Olvidé mi clave'.")
        )

    if accion == "desactivar":
        objetivo = _resolver_objetivo(db, usuario_id)
        if objetivo is None:
            return _pantalla_usuarios(request, db, usuario, error="Cuenta no encontrada.", status=404)
        if objetivo.id == usuario.id:
            return _pantalla_usuarios(request, db, usuario,
                                      error="No puedes dar de baja tu propia cuenta.", status=400)
        usuarios.desactivar(db, objetivo)
        return _pantalla_usuarios(request, db, usuario, mensaje=f"{objetivo.email} quedó sin acceso.")

    if accion == "reactivar":
        objetivo = _resolver_objetivo(db, usuario_id)
        if objetivo is None:
            return _pantalla_usuarios(request, db, usuario, error="Cuenta no encontrada.", status=404)
        # No hace falta subir token_version de nuevo: desactivar() ya lo subio,
        # asi que las sesiones y enlaces de antes de la baja siguen muertos.
        # La clave anterior sigue funcionando (cambiar_clave no se llama aqui),
        # pero se manda igual un enlace nuevo: es la opcion amable y no cuesta nada.
        objetivo.activo = True
        db.commit()
        tareas.add_task(_enviar_enlace_reset, objetivo.email, crear_token_reset(objetivo))
        return _pantalla_usuarios(
            request, db, usuario,
            mensaje=(f"{objetivo.email} recuperó el acceso. Le enviaremos un enlace para definir su "
                     "clave. Si no le llega, puede pedirlo con 'Olvidé mi clave'.")
        )

    return _pantalla_usuarios(request, db, usuario, error="Acción desconocida.", status=400)


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


# --- Re-parseo de los PDF ya guardados (mismo patron que el sync) ---
# Existe como boton y no solo como comando porque `railway run` corre en la
# maquina de quien lo invoca, y ahi el DATABASE_URL apunta a un host de la red
# privada de Railway que no resuelve desde afuera. Desde este endpoint corre
# dentro del contenedor, que es donde si resuelve.
_reparse_state = {"running": False, "terminado": False, "total": 0, "procesados": 0,
                  "revision_manual": 0, "falta_proveedor": 0, "sin_pdf": 0, "errores": 0,
                  "tc_consultados": 0, "mensaje": ""}
_reparse_lock = threading.Lock()


def _run_reparse_bg():
    from app.tasks.reparse import reparse_all

    try:
        stats = reparse_all(progress=_reparse_state)
        _reparse_state["mensaje"] = (
            f"Listo. Total {stats['total']}, en revision {stats['revision_manual']}, "
            f"falta proveedor {stats['falta_proveedor']}, sin pdf {stats['sin_pdf']}, "
            f"errores {stats['errores']}."
        )
    except Exception as e:
        _reparse_state["mensaje"] = f"Error al re-parsear: {e}"
    finally:
        _reparse_state["running"] = False
        _reparse_state["terminado"] = True


@app.post("/reparse")
def trigger_reparse(request: Request, usuario: Usuario = Depends(require_login)):
    with _reparse_lock:
        if _reparse_state["running"]:
            return JSONResponse({"running": True, "ya_en_curso": True})
        _reparse_state.update({"running": True, "terminado": False, "total": 0, "procesados": 0,
                               "revision_manual": 0, "falta_proveedor": 0, "sin_pdf": 0,
                               "errores": 0, "tc_consultados": 0,
                               "mensaje": "Leyendo los PDF guardados..."})
    threading.Thread(target=_run_reparse_bg, daemon=True).start()
    return JSONResponse({"running": True})


@app.get("/reparse/estado")
def reparse_estado(request: Request, usuario: Usuario = Depends(require_login)):
    return JSONResponse(_reparse_state)


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
