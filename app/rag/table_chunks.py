"""Turn the corpus's schedules into page-cited, row-aware chunks."""

import hashlib
import re
from pathlib import Path

import pdfplumber
from pypdf import PdfReader


TABLE_PAGES = {
    "1941": [7], "1915": list(range(9, 16)), "1910": [3, 4], "1784": [9],
    "1782": [9, 10, 11], "1546": list(range(10, 15)),
    "1918": list(range(18, 23)), "1817": [5, 6, 7], "1767": [4],
    "1813": list(range(9, 14)), "1709": list(range(9, 14)),
    "191": [5, 6, 7], "236": [5, 6, 7],
}

TABLE_TITLES = {
    "1941": [(7, "Schedule of Events during which Parking Fees Apply to Public Car Parks Adjacent Thereto")],
    "1915": [(9, "Schedule (1): Fees for Permits and Services"), (11, "Schedule (2): Violations and Fines of Establishments"), (14, "Schedule (3): Violations and Fines of Trainers and Instructors")],
    "1910": [(3, "Schedule (3): Public Transport Violations and Fines")],
    "1784": [(9, "Permit Validity by Type of Work or Activity")],
    "1782": [(9, "Schedule (1): Criteria for Classification of Establishments"), (10, "Schedule (2): Mandatory and Optional Services for Each Category of Establishments")],
    "1546": [(10, "Schedule (4): Certificate Issuance and Service Fees"), (13, "Schedule (6): Violations and Fines Imposed by the Agency")],
    "1918": [(18, "Schedule (1): Fees for Permits and Services"), (20, "Schedule (2): Violations and Fines")],
    "1817": [(5, "Schedule (1): Parking Fees"), (7, "Schedule (2): Parking Fees for Multi-storey and Event-Adjacent Car Parks")],
    "1767": [(4, "Schedule (1): Traffic Toll Charges and Tag Fees")],
    "1813": [(9, "Minimum Safe Manning Matrix")],
    "1709": [(9, "Schedule (1): Fees for Marine Navigational Aids"), (12, "Schedule (2): Violations and Fines Related to Marine Navigational Aids")],
    "191": [(5, "Schedule (1): Violations and Fines Related to Water Bus Operators"), (7, "Schedule (2): Violations and Fines Related to Water Bus Passengers")],
    "236": [(5, "Schedule (1): Water Taxi Fares between Stations"), (6, "Schedule (2): Violations and Fines Related to Water Taxi Operators"), (7, "Schedule (3): Violations and Fines Related to Water Taxi Passengers")],
}


def clean(value: str | None) -> str:
    return " ".join((value or "").split())


def title_for(document_id: str, page_number: int) -> str:
    return next(title for start, title in reversed(TABLE_TITLES[document_id]) if page_number >= start)


def stored_chunk(document_id: str, page_number: int, title: str, text: str) -> tuple:
    content = f"{title}\nPDF page {page_number}\n{text}"
    chunk_id = hashlib.sha256(f"{document_id}:{page_number}:{content}".encode()).hexdigest()[:20]
    return chunk_id, document_id, "table", None, page_number, page_number, content


def table_rows(table) -> list[list[str]]:
    return [[clean(value) for value in row] for row in table.extract()]


def simple_rows(rows: list[list[str]], title: str) -> list[str]:
    label = "Fine" if "Fine" in title or "Violation" in title else "Fee"
    entries = []
    for row in rows:
        cells = [value for value in row if value]
        if not cells:
            continue
        serial_index = next((index for index, value in enumerate(cells[:2])
                             if re.fullmatch(r"\d+(?:-\d+)?", value)), None)
        if serial_index is not None and len(cells) >= serial_index + 3:
            number, description, amount = cells[serial_index:serial_index + 3]
            extra = " | ".join(cells[serial_index + 3:])
            if not re.search(r"\b(?:AED|dirhams?)\b", amount, re.IGNORECASE):
                amount = re.sub(r"^(\d[\d,.]*)(\s+per\b)", r"\1 dirhams\2", amount, flags=re.IGNORECASE)
                if re.fullmatch(r"\d[\d,.]*", amount):
                    amount += " dirhams"
            entry = f"No. {number} | {description} | {label}: {amount}"
            entries.append(f"{entry} | Additional penalty: {extra}" if extra and extra != "-" else entry)
        elif entries and serial_index is None and cells[0] not in ("SN", "S", "N"):
            # A description can continue on the next physical table row.
            if not any(word in cells[0] for word in ("Fee", "Fine", "Schedule", "Violation")):
                entries[-1] += " " + " ".join(cells)
    return entries


def parking_rows(rows: list[list[str]]) -> list[str]:
    heading = next((cell for row in rows[:2] for cell in row if "Parking Fees for" in cell), "Parking Fees")
    entries = []
    for row in rows[2:]:
        if not row or not row[0] or "Parking" in row[0]:
            continue
        values = [value for value in row[1:] if value]
        if "Multi-storey" in heading or "Event-Adjacent" in heading:
            if values:
                entries.append(f"{heading} | Duration: {row[0]} | Fee: {values[-1]} dirhams")
        elif len(values) >= 6:
            names = ("On-street outside peak hours", "On-street during peak hours", "On-street premium during peak hours",
                     "Off-street outside peak hours", "Off-street during peak hours", "Off-street premium during peak hours")
            fees = " | ".join(f"{name}: {value} dirhams" for name, value in zip(names, values[:6]))
            entries.append(f"{heading} | Duration: {row[0]} | {fees}")
    return entries


def tourist_rows(rows: list[list[str]], page_number: int) -> list[str]:
    entries = []
    if page_number == 9:
        for row in rows[2:]:
            if row[0]:
                values = [value for value in row[1:] if value]
                if len(values) == 3:
                    entries.append(f"Criterion: {row[0]} | Three-star: {values[0]} | Four-star: {values[1]} | Five-star: {values[2]}")
    else:
        for row in rows[2:]:
            if row and re.fullmatch(r"\d+", row[0]) and len(row) > 8:
                entries.append(f"No. {row[0]} | Service: {row[1]} | Three-star: {row[2]} | Four-star: {row[5]} | Five-star: {row[8]}")
    return entries


def toll_rows(rows: list[list[str]]) -> list[str]:
    entries = []
    section = "Traffic Toll Tag Fees"
    day = ""
    for row in rows:
        cells = [value for value in row if value]
        if not cells:
            continue
        if "Traffic Toll Charges" in cells:
            section = "Traffic Toll Charges"
        elif re.fullmatch(r"\d+", cells[0]) and len(cells) >= 3:
            day = cells[1]
            if section == "Traffic Toll Charges" and len(cells) >= 4:
                entries.append(f"{section} | Day: {day} | Period: {cells[2]} | Charge: {cells[3]} dirhams")
            elif section == "Traffic Toll Tag Fees":
                entries.append(f"{section} | Service: {cells[1]} | Fee: {cells[2]} dirhams")
        elif section == "Traffic Toll Charges" and day and len(cells) == 2 and cells[-1].isdigit():
            entries.append(f"{section} | Day: {day} | Period: {cells[0]} | Charge: {cells[1]} dirhams")
    return entries


def taxi_fare_rows(pdf_path: Path) -> list[str]:
    text = PdfReader(pdf_path).pages[4].extract_text()
    legend = text.split("Station Name and Code:", 1)[1].split("Station Code", 1)[0]
    names = {code: clean(name) for code, name in re.findall(r"(\d{2})-\s*(.*?)(?=\s+\d{2}-|\n|$)", legend)}
    code_heading = text.split("Station Code", 1)[1].split("Station Code", 1)[0]
    codes = re.findall(r"\b\d{2}\b", code_heading)
    if len(codes) != 18 or len(names) != 18:
        raise ValueError("water taxi station headings could not be verified")
    entries = []
    for line in text.split("Station Code", 2)[2].splitlines():
        numbers = line.split()
        if numbers and numbers[0] in names and len(numbers) in (18, 19) and all(value.isdigit() for value in numbers[1:]):
            origin = numbers[0]
            destinations = codes if len(numbers) == 19 else [code for code in codes if code != origin]
            if len(destinations) != len(numbers) - 1:
                continue
            fares = " | ".join(f"{names[code]} ({code}): {amount} dirhams"
                               for code, amount in zip(destinations, numbers[1:]))
            entries.append(f"From {names[origin]} ({origin}) | To {fares}")
    if len(entries) != 18:
        raise ValueError("water taxi fare rows could not be verified")
    return entries


def matrix_rows(rows: list[list[str]]) -> list[str]:
    # Preserve the role and symbols without guessing which nested vessel heading a symbol belongs to.
    return ["Matrix row: " + " | ".join(f"column {index + 1}: {value}" for index, value in enumerate(row) if value)
            for row in rows if any("●" in value or "Total Number" in value for value in row)]


def extend_previous_row(chunks: list[tuple], page_number: int, continuation: str) -> None:
    previous = chunks.pop()
    before_amount, separator, after_amount = previous[-1].rpartition(" | Fee:")
    if not separator:
        before_amount, separator, after_amount = previous[-1].rpartition(" | Fine:")
    content = f"{before_amount} {continuation}{separator}{after_amount}"
    content = content.replace(f"PDF page {page_number - 1}", f"PDF pages {page_number - 1}-{page_number}", 1)
    chunk_id = hashlib.sha256(f"{previous[1]}:{page_number - 1}:{content}".encode()).hexdigest()[:20]
    chunks.append((chunk_id, previous[1], previous[2], None, page_number - 1, page_number, content))


def extract_table_chunks(pdf_path: Path, document_id: str) -> tuple[list[tuple], dict[int, list[tuple]], list[str]]:
    chunks = []
    table_boxes = {}
    warnings = []
    if document_id not in TABLE_PAGES:
        return chunks, table_boxes, warnings
    with pdfplumber.open(pdf_path) as pdf:
        for page_number in TABLE_PAGES[document_id]:
            page = pdf.pages[page_number - 1]
            tables = [table for table in page.find_tables()
                      if table.bbox[2] - table.bbox[0] > 150 and table.bbox[3] - table.bbox[1] > 35]
            if not tables:
                warnings.append(f"{pdf_path.name} page {page_number}: table was not detected")
                continue
            table_boxes[page_number] = [table.bbox for table in tables]
            title = title_for(document_id, page_number)
            for table in tables:
                rows = table_rows(table)
                if (document_id, page_number) in {("1915", 13), ("1546", 12)} and rows and chunks:
                    first_cells = [value for value in rows[0] if value]
                    if len(first_cells) == 1 and title_for(document_id, page_number - 1) == title:
                        extend_previous_row(chunks, page_number, first_cells[0])
                if document_id == "1817":
                    entries = parking_rows(rows)
                elif document_id == "1782":
                    entries = tourist_rows(rows, page_number)
                elif document_id == "1784":
                    entries = [f"Type of work or activity: {row[0]} | Permit validity: {next((value for value in row[1:] if value), '')}"
                               for row in rows[1:] if row[0]]
                elif document_id == "1767":
                    entries = toll_rows(rows)
                elif document_id == "1813":
                    entries = matrix_rows(rows)
                elif document_id == "1941":
                    entries = [f"Event no. {row[1]}: {row[-1]}" for row in rows[1:] if len(row) > 3 and row[1].isdigit()]
                elif document_id == "236" and page_number == 5:
                    entries = taxi_fare_rows(pdf_path)
                else:
                    entries = simple_rows(rows, title)
                if document_id == "1709" and page_number in (10, 11):
                    missing_numbers = ("3-4",) if page_number == 10 else ("3-5", "3-6")
                    page_text = PdfReader(pdf_path).pages[page_number - 1].extract_text()
                    for number in missing_numbers:
                        match = re.search(rf"(?ms)^{number}\s+(.+?)\s+(\d[\d,]*\.\d{{2}})", page_text)
                        if not match:
                            raise ValueError(f"schedule row {number} could not be read")
                        entries.append(f"No. {number} | {clean(match.group(1))} | Fee: {match.group(2)} dirhams")
                if not entries:
                    warnings.append(f"{pdf_path.name} page {page_number}: no table rows were parsed")
                for entry in entries:
                    chunks.append(stored_chunk(document_id, page_number, title, entry))
    return chunks, table_boxes, warnings
