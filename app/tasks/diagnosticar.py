"""Diagnostica PDFs sueltos: que proveedor detecta el registry y que extrae el parser.

Es el paso previo a agregar un proveedor nuevo (una entrada en PROVIDERS + su
fila en el mantenedor). No toca la base ni Gmail: solo lee PDFs de una carpeta.

Ademas escribe el texto extraido a un .txt al lado de cada PDF, porque cuando
una regex no matchea casi siempre es por como pdfplumber saca el texto (ver la
nota de los `\x00` de Anthropic en CONTEXT.md), y eso solo se ve mirando el
texto crudo.

Uso:  python -m app.tasks.diagnosticar facturas_pendientes
"""
import sys
from pathlib import Path

from app.gmail_sync import _extract_pdf_text
from app.parsers import registry


def diagnosticar(carpeta: str) -> int:
    pdfs = sorted(Path(carpeta).glob("*.pdf"))
    if not pdfs:
        print(f"No hay PDFs en {carpeta}/")
        return 1

    for pdf in pdfs:
        print("=" * 70)
        print(pdf.name)
        print("=" * 70)
        try:
            text = _extract_pdf_text(pdf.read_bytes())
        except Exception as e:
            print(f"  ! no se pudo leer el PDF: {e}\n")
            continue

        salida = pdf.with_suffix(".txt")
        salida.write_text(text, encoding="utf-8")

        cfg = registry.detect_provider(text)
        print(f"  detect_provider : {cfg.key if cfg else 'NINGUNO (cae en generic.py)'}")

        inv = registry.parse_invoice(text)
        for campo in ("proveedor", "provider_key", "rut_proveedor", "numero", "fecha", "moneda",
                      "monto_afecto", "monto_exento", "iva", "total",
                      "revision_manual", "falta_proveedor", "notas"):
            print(f"  {campo:<16}: {getattr(inv, campo)}")

        print(f"  texto extraido  : {salida.name} ({len(text)} chars)")
        print(f"  primeras lineas : {text.splitlines()[:3]}\n")
    return 0


if __name__ == "__main__":
    carpeta = sys.argv[1] if len(sys.argv) > 1 else "facturas_pendientes"
    raise SystemExit(diagnosticar(carpeta))
