"""CSV rendering shared by ``GET /tickets/export.csv`` and the export job."""

from __future__ import annotations

from tiqora.domain.schemas import TicketListItem


class EchoWriter:
    """File-like shim so ``csv.writer`` yields each row as a string.

    ``csv.writer(target).writerow(...)`` calls ``target.write(row_string)``
    and returns whatever ``write`` returns — echoing the string back turns
    the writer into a per-row string generator instead of one requiring a
    real (buffering) file object, which is what lets the CSV export stream
    row-by-row instead of materializing the whole file in memory.
    """

    def write(self, value: str) -> str:
        return value


CSV_HEADER = [
    "Number",
    "Title",
    "Queue",
    "State",
    "Priority",
    "Owner",
    "Customer",
    "Created",
    "Changed",
]


def ticket_csv_row(item: TicketListItem) -> list[str]:
    return [
        item.tn,
        item.title or "",
        item.queue_name or "",
        item.state or "",
        item.priority or "",
        item.owner_login or item.owner_name or "",
        item.customer_id or "",
        item.create_time.isoformat(),
        item.change_time.isoformat(),
    ]
