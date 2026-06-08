import argparse
import getpass
import os
from pathlib import Path
import sys
import xml.etree.ElementTree as ET

import pandas as pd
import requests

ATOM_NS = "http://www.w3.org/2005/Atom"
M_NS = "http://schemas.microsoft.com/ado/2007/08/dataservices/metadata"


def fetch_xml(url: str, username: str, password: str, timeout: int = 60) -> str:
    headers = {
        "Accept": "application/atom+xml,application/xml,text/xml"
    }
    resp = requests.get(url, auth=(username, password), headers=headers, timeout=timeout)
    resp.raise_for_status()
    return resp.text


def parse_atom_xml_to_rows(xml_text: str) -> tuple[list[str], list[dict]]:
    root = ET.fromstring(xml_text)
    ns = {"atom": ATOM_NS, "m": M_NS}

    entries = root.findall(".//atom:entry", ns)
    if not entries:
        raise ValueError("No <entry> found in OData XML response.")

    columns: list[str] = []
    rows: list[dict] = []

    for entry in entries:
        props = entry.find("atom:content/m:properties", ns)
        if props is None:
            continue

        row_map: dict = {}
        for child in list(props):
            col = child.tag.split("}", 1)[-1]
            is_null = child.attrib.get(f"{{{M_NS}}}null") == "true"
            val = None if is_null else (child.text or "")
            row_map[col] = val
            if col not in columns:
                columns.append(col)

        rows.append(row_map)

    if not rows:
        raise ValueError("No row data parsed from OData XML.")

    normalized_rows = []
    for r in rows:
        normalized_rows.append({c: r.get(c) for c in columns})

    return columns, normalized_rows


def main() -> int:
    parser = argparse.ArgumentParser(description="Fetch SAP OData XML and export to Excel.")
    parser.add_argument(
        "--url",
        default="http://sapbd1app01.cn.schneider-electric.com:8000/sap/opu/odata/sap/ZBW_QUERY_LIST_SRV/LtResultSet",
        help="OData entity URL",
    )
    parser.add_argument("--user", default="", help="SAP username")
    parser.add_argument("--password", default="", help="SAP password")
    parser.add_argument("--out-xlsx", default="LtResultSet.xlsx", help="Output Excel file path")
    parser.add_argument("--out-csv", default="", help="Optional output CSV path")
    parser.add_argument("--timeout", type=int, default=60, help="HTTP timeout in seconds")
    args = parser.parse_args()

    username = args.user or os.environ.get("SAP_USERNAME", "").strip()
    password = args.password or os.environ.get("SAP_PASSWORD", "")

    if not username:
        username = input("SAP Username: ").strip()
    if not password:
        password = getpass.getpass("SAP Password: ")

    if not username:
        print("Error: username is required", file=sys.stderr)
        return 2

    try:
        xml_text = fetch_xml(args.url, username, password, timeout=args.timeout)
        columns, rows = parse_atom_xml_to_rows(xml_text)
    except requests.HTTPError as ex:
        print(f"HTTP error: {ex}", file=sys.stderr)
        return 1
    except Exception as ex:
        print(f"Parse error: {ex}", file=sys.stderr)
        return 1

    df = pd.DataFrame(rows, columns=columns)

    out_xlsx = Path(args.out_xlsx)
    out_xlsx.parent.mkdir(parents=True, exist_ok=True)
    df.to_excel(out_xlsx, index=False)

    if args.out_csv:
        out_csv = Path(args.out_csv)
        out_csv.parent.mkdir(parents=True, exist_ok=True)
        df.to_csv(out_csv, index=False, encoding="utf-8-sig")

    print(f"Rows: {len(df)}")
    print(f"Columns: {len(df.columns)}")
    print(f"Excel: {out_xlsx.resolve()}")
    if args.out_csv:
        print(f"CSV: {Path(args.out_csv).resolve()}")

    preview = df.head(10)
    with pd.option_context("display.max_columns", None, "display.width", 200):
        print("\nPreview (first 10 rows):")
        print(preview.to_string(index=False))

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
