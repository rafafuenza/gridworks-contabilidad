"""Genera el Libro de Compras en .xlsx para un periodo (mes) dado."""
import io
from typing import Sequence

from openpyxl import Workbook
from openpyxl.styles import Font, Alignment
from openpyxl.utils import get_column_letter

from app.models import Purchase

HEADERS = ["Index", "NUMERO", "FECHA", "RUT", "DESCRIPCION", "AFECTO", "EXENTO", "IVA", "TOTAL"]


def build_workbook(purchases: Sequence[Purchase], periodo_label: str) -> bytes:
    wb = Workbook()
    ws = wb.active
    ws.title = "Pag1"

    ws.merge_cells("E1:I1")
    title_cell = ws["E1"]
    title_cell.value = f"LIBRO COMPRA - {periodo_label}"
    title_cell.font = Font(bold=True, size=14)
    title_cell.alignment = Alignment(horizontal="center")

    header_row = 3
    for col, header in enumerate(HEADERS, start=1):
        cell = ws.cell(row=header_row, column=col, value=header)
        cell.font = Font(bold=True)

    row = header_row + 1
    for idx, p in enumerate(purchases, start=1):
        ws.cell(row=row, column=1, value=idx)
        ws.cell(row=row, column=2, value=p.numero)
        ws.cell(row=row, column=3, value=p.fecha.strftime("%d-%m-%Y") if p.fecha else None)
        ws.cell(row=row, column=4, value=p.rut_proveedor)
        descripcion = p.proveedor or ""
        if p.descripcion:
            descripcion = f"{descripcion} - {p.descripcion}"
        ws.cell(row=row, column=5, value=descripcion)
        ws.cell(row=row, column=6, value=float(p.monto_afecto) if p.monto_afecto is not None else None)
        ws.cell(row=row, column=7, value=float(p.monto_exento) if p.monto_exento is not None else None)
        ws.cell(row=row, column=8, value=float(p.iva) if p.iva is not None else None)
        ws.cell(row=row, column=9, value=float(p.total) if p.total is not None else None)
        row += 1

    last_data_row = row - 1
    total_row = row + 1
    ws.cell(row=total_row, column=2, value="Total :")
    ws.cell(row=total_row, column=4, value=f"{len(purchases)} Documentos")
    ws.cell(row=total_row, column=5, value="Total General")
    if last_data_row >= header_row + 1:
        for col in (6, 7, 8, 9):
            letter = get_column_letter(col)
            ws.cell(row=total_row, column=col,
                     value=f"=SUM({letter}{header_row + 1}:{letter}{last_data_row})")

    widths = [7, 16, 12, 14, 40, 12, 12, 12, 12]
    for i, w in enumerate(widths, start=1):
        ws.column_dimensions[get_column_letter(i)].width = w

    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()
