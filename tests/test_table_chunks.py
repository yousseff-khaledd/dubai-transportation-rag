import unittest

from app.rag.ingest import PROJECT_DIR, build_chunks, extract_lines
from app.rag.table_chunks import extract_table_chunks


class TableChunkTests(unittest.TestCase):
    def chunks_for(self, document_id):
        pdf_path = PROJECT_DIR / "data" / "pdfs" / f"{document_id}.pdf"
        return extract_table_chunks(pdf_path, document_id)

    def test_fine_row_keeps_title_amount_and_page(self):
        chunks, _, warnings = self.chunks_for("1915")
        self.assertFalse(warnings)
        row = next(chunk for chunk in chunks if "Conducting the Activity without a Permit" in chunk[-1])
        self.assertEqual((row[2], row[4], row[5]), ("table", 11, 11))
        self.assertIn("Schedule (2): Violations and Fines of Establishments", row[-1])
        self.assertIn("Fine: 10,000 dirhams", row[-1])

    def test_nested_parking_headings_are_kept(self):
        chunks, _, _ = self.chunks_for("1817")
        row = next(chunk for chunk in chunks if "Category One" in chunk[-1] and "Duration: One (1) hour" in chunk[-1])
        self.assertIn("On-street during peak hours: 4.00 dirhams", row[-1])
        self.assertIn("Off-street during peak hours: 4.00 dirhams", row[-1])
        self.assertEqual(row[4], 5)

    def test_taxi_matrix_keeps_station_codes_and_does_not_invent_blank_cell(self):
        chunks, _, _ = self.chunks_for("236")
        fare_rows = [chunk for chunk in chunks if chunk[4] == 5]
        self.assertEqual(len(fare_rows), 18)
        self.assertIn("From Al Mamzar (10)", fare_rows[0][-1])
        self.assertIn("Deira Old Souk (20): 150 dirhams", fare_rows[0][-1])
        atlantis = next(chunk[-1] for chunk in fare_rows if "From Atlantis (52)" in chunk[-1])
        self.assertNotIn("Atlantis (52): 0 dirhams", atlantis)

    def test_rows_crossing_pages_keep_full_description_and_locator(self):
        chunks, _, _ = self.chunks_for("1546")
        row = next(chunk for chunk in chunks if "No. 29 |" in chunk[-1])
        self.assertEqual(row[4:6], (11, 12))
        self.assertIn("passengers in one direction during Peak Hours | Fee: 1,304,000.00 dirhams", row[-1])

    def test_missing_fee_serials_are_recovered_from_pdf_text(self):
        chunks, _, _ = self.chunks_for("1709")
        row = next(chunk for chunk in chunks if "No. 3-4 |" in chunk[-1])
        self.assertEqual(row[4], 10)
        self.assertIn("Fee: 30,000.00 dirhams", row[-1])

    def test_prose_outside_table_is_preserved(self):
        pdf_path = PROJECT_DIR / "data" / "pdfs" / "1784.pdf"
        _, table_boxes, _ = extract_table_chunks(pdf_path, "1784")
        lines, _ = extract_lines(pdf_path, table_boxes)
        prose = " ".join(chunk[-1] for chunk in build_chunks("1784", lines))
        self.assertIn("A Permit will be valid for a term of one (1) year", prose)
        self.assertNotIn("Six (6) Months", prose)


if __name__ == "__main__":
    unittest.main()
