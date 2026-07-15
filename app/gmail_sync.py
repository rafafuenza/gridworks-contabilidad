"""Sincroniza correos de la label de entrada: descarga la Invoice de cada compra,
extrae los datos y los guarda en la base como 'pendiente'. NO mueve labels: eso
ocurre solo cuando el usuario acepta la factura en la web (mover_a_declaradas).
Idempotente: los correos que ya estan en la base se saltan (por Message-ID)."""
import email
import imaplib
import logging
import smtplib
import time
from email.header import decode_header
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from email.mime.application import MIMEApplication
from email.utils import make_msgid, formatdate

from sqlalchemy.orm import Session

from app.config import GMAIL_ADDRESS, GMAIL_APP_PASSWORD, GMAIL_LABEL, GMAIL_DECLARED_LABEL
from app.exchange_rate import get_usd_clp
from app.models import Purchase
from app.parsers import registry
from app import mantenedor

logger = logging.getLogger("gmail_sync")


def _decode(value: str) -> str:
    if not value:
        return ""
    decoded, enc = decode_header(value)[0]
    if isinstance(decoded, bytes):
        return decoded.decode(enc or "utf-8", errors="ignore")
    return decoded


def _find_all_mail_folder(imap: imaplib.IMAP4_SSL) -> str:
    typ, folders = imap.list()
    for f in folders:
        decoded = f.decode(errors="ignore")
        if "\\All" in decoded:
            return decoded.split(' "/" ')[-1].strip().strip('"')
    return "[Gmail]/All Mail"


def _extract_sender_email(msg) -> str:
    from email.utils import parseaddr
    _, addr = parseaddr(msg.get("From", ""))
    return addr.lower()


def sync(db: Session, max_messages: int = None, progress: dict = None) -> dict:
    if not GMAIL_ADDRESS or not GMAIL_APP_PASSWORD:
        raise RuntimeError("Faltan GMAIL_ADDRESS / GMAIL_APP_PASSWORD en las variables de entorno.")

    mantenedor.ensure_seed(db)
    stats = {"revisados": 0, "nuevos": 0, "omitidos_ya_en_base": 0, "sin_invoice": 0,
             "falta_proveedor": 0, "errores": 0}

    def _reportar():
        if progress is not None:
            progress["procesados"] = stats["revisados"]
            progress["nuevos"] = stats["nuevos"]
            progress["omitidos"] = stats["omitidos_ya_en_base"]
            progress["falta_proveedor"] = stats["falta_proveedor"]
            progress["errores"] = stats["errores"]

    imap = imaplib.IMAP4_SSL("imap.gmail.com")
    imap.login(GMAIL_ADDRESS, GMAIL_APP_PASSWORD)
    try:
        all_mail = _find_all_mail_folder(imap)
        imap.select(f'"{all_mail}"')

        # Todo lo que este en el label de entrada; los ya guardados se saltan luego.
        query = f'"label:{GMAIL_LABEL} has:attachment"'
        typ, data = imap.uid("search", None, "X-GM-RAW", query)
        uids = data[0].split()
        if max_messages:
            uids = uids[:max_messages]

        if progress is not None:
            progress["total"] = len(uids)

        for uid in uids:
            stats["revisados"] += 1
            try:
                _process_message(imap, db, uid, stats)
            except Exception:
                logger.exception("Error procesando uid=%s", uid)
                stats["errores"] += 1
            _reportar()
    finally:
        imap.logout()

    return stats


def _message_id_de_uid(imap: imaplib.IMAP4_SSL, uid: bytes) -> str:
    """Fetch liviano solo del header Message-ID, para saltar los ya guardados sin
    bajar el correo completo (importante: los pendientes quedan en el label)."""
    typ, data = imap.uid("fetch", uid, "(BODY.PEEK[HEADER.FIELDS (MESSAGE-ID)])")
    for part in data:
        if isinstance(part, tuple) and part[1]:
            hdr = email.message_from_bytes(part[1])
            mid = hdr.get("Message-ID")
            if mid:
                return mid.strip()
    return uid.decode()


def _process_message(imap: imaplib.IMAP4_SSL, db: Session, uid: bytes, stats: dict):
    # 1) chequeo barato: si ya esta en la base, saltar sin bajar el cuerpo
    message_id = _message_id_de_uid(imap, uid)
    if db.query(Purchase).filter_by(gmail_message_id=message_id).first():
        stats["omitidos_ya_en_base"] += 1
        return

    # 2) recien ahora bajamos el correo completo
    typ, msg_data = imap.uid("fetch", uid, "(RFC822)")
    raw = msg_data[0][1]
    msg = email.message_from_bytes(raw)
    message_id = msg.get("Message-ID") or message_id

    sender_email = _extract_sender_email(msg)
    sender_name = _decode(msg.get("From", "")).split("<")[0].strip()

    # Busca el adjunto de la factura (Invoice); si no hay ninguno con ese patron, toma el primer PDF
    invoice_part = None
    fallback_part = None
    for part in msg.walk():
        if part.get_content_disposition() != "attachment":
            continue
        filename = _decode(part.get_filename() or "")
        if not filename.lower().endswith(".pdf"):
            continue
        if "invoice" in filename.lower() or "factura" in filename.lower():
            invoice_part = (filename, part)
            break
        if fallback_part is None:
            fallback_part = (filename, part)

    chosen = invoice_part or fallback_part
    if not chosen:
        stats["sin_invoice"] += 1
        return

    filename, part = chosen
    pdf_bytes = part.get_payload(decode=True)

    text = _extract_pdf_text(pdf_bytes)

    domain = sender_email.split("@")[-1].lower() if "@" in sender_email else ""
    parsed = registry.parse_invoice(text, sender_name=sender_name, sender_domain=domain)
    mantenedor.aplicar(db, parsed)  # completa RUT/tratamiento y marca falta_proveedor

    tipo_cambio = None
    tipo_cambio_fecha = None
    if parsed.moneda == "USD" and parsed.fecha:
        tipo_cambio, tipo_cambio_fecha = get_usd_clp(parsed.fecha)

    purchase = Purchase(
        gmail_message_id=message_id,
        gmail_thread_id=msg.get("Thread-Index") or "",
        numero=parsed.numero,
        fecha=parsed.fecha,
        periodo=parsed.fecha.strftime("%Y-%m") if parsed.fecha else None,
        rut_proveedor=parsed.rut_proveedor,
        proveedor=parsed.proveedor,
        descripcion=parsed.descripcion,
        moneda=parsed.moneda,
        monto_afecto=parsed.monto_afecto,
        monto_exento=parsed.monto_exento,
        iva=parsed.iva,
        total=parsed.total,
        tipo_cambio=tipo_cambio,
        tipo_cambio_fecha=tipo_cambio_fecha,
        pdf_filename=filename,
        pdf_data=pdf_bytes,
        revision_manual=parsed.revision_manual,
        falta_proveedor=parsed.falta_proveedor,
        estado="pendiente",
        notas=parsed.notas,
    )
    db.add(purchase)
    db.commit()
    stats["nuevos"] += 1
    if parsed.falta_proveedor:
        stats["falta_proveedor"] += 1


def _extract_pdf_text(pdf_bytes: bytes) -> str:
    import io
    import pdfplumber

    text_parts = []
    with pdfplumber.open(io.BytesIO(pdf_bytes)) as pdf:
        for page in pdf.pages:
            text_parts.append(page.extract_text() or "")
    text = "\n".join(text_parts)
    # Algunos PDFs (fuentes custom) traen NUL en vez de espacios/guiones
    text = text.replace("\x00", " ")
    return text


def mover_a_declaradas(message_ids, progress: dict = None, on_result=None) -> dict:
    """Mueve uno o varios correos (por Message-ID) del label de entrada al de
    declaradas: quita GMAIL_LABEL y agrega GMAIL_DECLARED_LABEL. Devuelve stats.

    progress: dict opcional que se va actualizando (total/procesados/movidos/...).
    on_result: callback opcional on_result(message_id, ok) tras cada correo, para
    que el llamador marque el estado en la base a medida que avanza."""
    if isinstance(message_ids, str):
        message_ids = [message_ids]
    if not GMAIL_ADDRESS or not GMAIL_APP_PASSWORD:
        raise RuntimeError("Faltan GMAIL_ADDRESS / GMAIL_APP_PASSWORD en las variables de entorno.")

    stats = {"movidos": 0, "no_encontrados": 0}
    imap = imaplib.IMAP4_SSL("imap.gmail.com")
    imap.login(GMAIL_ADDRESS, GMAIL_APP_PASSWORD)
    try:
        all_mail = _find_all_mail_folder(imap)
        imap.select(f'"{all_mail}"')
        if progress is not None:
            progress["total"] = len(message_ids)
        for mid in message_ids:
            uid = _uid_de_message_id(imap, mid)
            ok = False
            if uid:
                imap.uid("store", uid, "+X-GM-LABELS", f'("{GMAIL_DECLARED_LABEL}")')
                imap.uid("store", uid, "-X-GM-LABELS", f'("{GMAIL_LABEL}")')
                stats["movidos"] += 1
                ok = True
            else:
                stats["no_encontrados"] += 1
            if on_result:
                try:
                    on_result(mid, ok)
                except Exception:
                    logger.exception("on_result fallo para %s", mid)
            if progress is not None:
                progress["procesados"] = stats["movidos"] + stats["no_encontrados"]
                progress["movidos"] = stats["movidos"]
                progress["no_encontrados"] = stats["no_encontrados"]
    finally:
        imap.logout()
    return stats


def _uid_de_message_id(imap: imaplib.IMAP4_SSL, message_id: str):
    typ, data = imap.uid("search", None, "HEADER", "Message-ID", message_id)
    if typ == "OK" and data and data[0].split():
        return data[0].split()[0]
    return None


def enviar_correo_con_etiqueta(to: str, asunto: str, cuerpo: str, etiqueta: str, adjunto=None) -> str:
    """Envia un correo por SMTP (Gmail) y le aplica una etiqueta de Gmail a la copia
    guardada (en Enviados). Devuelve el Message-ID usado.

    adjunto: opcional, tupla (filename, bytes, subtipo_mime)."""
    if not GMAIL_ADDRESS or not GMAIL_APP_PASSWORD:
        raise RuntimeError("Faltan GMAIL_ADDRESS / GMAIL_APP_PASSWORD en las variables de entorno.")

    msg = MIMEMultipart()
    msg["Subject"] = asunto
    msg["From"] = GMAIL_ADDRESS
    msg["To"] = to
    msg["Date"] = formatdate(localtime=True)
    mid = make_msgid()
    msg["Message-ID"] = mid
    msg.attach(MIMEText(cuerpo, "plain", "utf-8"))

    if adjunto:
        filename, data, subtipo = adjunto
        part = MIMEApplication(data, _subtype=subtipo)
        part.add_header("Content-Disposition", "attachment", filename=filename)
        msg.attach(part)

    with smtplib.SMTP("smtp.gmail.com", 587, timeout=30) as s:
        s.starttls()
        s.login(GMAIL_ADDRESS, GMAIL_APP_PASSWORD)
        s.sendmail(GMAIL_ADDRESS, [to], msg.as_string())

    _etiquetar_mensaje(mid, etiqueta)
    return mid


def _etiquetar_mensaje(message_id: str, etiqueta: str, intentos: int = 6) -> bool:
    """Busca el correo recien enviado (por Message-ID) y le agrega la etiqueta.
    Reintenta unos segundos porque la copia en Enviados puede tardar en indexarse."""
    imap = imaplib.IMAP4_SSL("imap.gmail.com")
    imap.login(GMAIL_ADDRESS, GMAIL_APP_PASSWORD)
    try:
        all_mail = _find_all_mail_folder(imap)
        imap.select(f'"{all_mail}"')
        for _ in range(intentos):
            uid = _uid_de_message_id(imap, message_id)
            if uid:
                imap.uid("store", uid, "+X-GM-LABELS", f'("{etiqueta}")')
                return True
            time.sleep(1)
    finally:
        imap.logout()
    return False
