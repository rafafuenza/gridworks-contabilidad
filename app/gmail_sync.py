"""Sincroniza correos de la label configurada: descarga la Invoice de cada compra,
extrae los datos y los guarda en la base de datos. Idempotente: los correos ya
procesados quedan marcados con una sub-label y no se vuelven a tocar."""
import email
import imaplib
import logging
from email.header import decode_header

from sqlalchemy.orm import Session

from app.config import GMAIL_ADDRESS, GMAIL_APP_PASSWORD, GMAIL_LABEL, PROCESSED_LABEL
from app.exchange_rate import get_usd_clp
from app.models import Purchase
from app.parsers.registry import get_parser
from app.parsers import generic as generic_parser

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


def sync(db: Session, max_messages: int = None) -> dict:
    if not GMAIL_ADDRESS or not GMAIL_APP_PASSWORD:
        raise RuntimeError("Faltan GMAIL_ADDRESS / GMAIL_APP_PASSWORD en las variables de entorno.")

    stats = {"revisados": 0, "nuevos": 0, "omitidos_ya_procesados": 0, "sin_invoice": 0, "errores": 0}

    imap = imaplib.IMAP4_SSL("imap.gmail.com")
    imap.login(GMAIL_ADDRESS, GMAIL_APP_PASSWORD)
    try:
        all_mail = _find_all_mail_folder(imap)
        imap.select(f'"{all_mail}"')

        query = f'"label:{GMAIL_LABEL} -label:{PROCESSED_LABEL} has:attachment"'
        typ, data = imap.uid("search", None, "X-GM-RAW", query)
        uids = data[0].split()
        if max_messages:
            uids = uids[:max_messages]

        for uid in uids:
            stats["revisados"] += 1
            try:
                _process_message(imap, db, uid, stats)
            except Exception:
                logger.exception("Error procesando uid=%s", uid)
                stats["errores"] += 1
    finally:
        imap.logout()

    return stats


def _process_message(imap: imaplib.IMAP4_SSL, db: Session, uid: bytes, stats: dict):
    typ, msg_data = imap.uid("fetch", uid, "(RFC822)")
    raw = msg_data[0][1]
    msg = email.message_from_bytes(raw)

    message_id = msg.get("Message-ID") or uid.decode()

    existing = db.query(Purchase).filter_by(gmail_message_id=message_id).first()
    if existing:
        stats["omitidos_ya_procesados"] += 1
        _mark_processed(imap, uid)
        return

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
        _mark_processed(imap, uid)
        return

    filename, part = chosen
    pdf_bytes = part.get_payload(decode=True)

    text = _extract_pdf_text(pdf_bytes)

    parser_fn, domain = get_parser(sender_email)
    if parser_fn:
        parsed = parser_fn(text)
    else:
        parsed = generic_parser.parse(text, sender_name=sender_name, sender_domain=domain)

    tipo_cambio = None
    if parsed.moneda == "USD" and parsed.fecha:
        tipo_cambio = get_usd_clp(parsed.fecha)

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
        pdf_filename=filename,
        pdf_data=pdf_bytes,
        revision_manual=parsed.revision_manual,
        notas=parsed.notas,
    )
    db.add(purchase)
    db.commit()
    stats["nuevos"] += 1

    _mark_processed(imap, uid)


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


def _mark_processed(imap: imaplib.IMAP4_SSL, uid: bytes):
    imap.uid("store", uid, "+X-GM-LABELS", f'("{PROCESSED_LABEL}")')
