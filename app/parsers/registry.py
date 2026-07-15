from app.parsers import anthropic, generic

# Mapeo de dominio del remitente -> funcion de parseo dedicada
DOMAIN_PARSERS = {
    "mail.anthropic.com": anthropic.parse,
    "anthropic.com": anthropic.parse,
}


def get_parser(sender_email: str):
    domain = sender_email.split("@")[-1].lower() if "@" in sender_email else ""
    return DOMAIN_PARSERS.get(domain), domain
