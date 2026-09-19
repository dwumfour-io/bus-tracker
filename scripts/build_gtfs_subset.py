#!/usr/bin/env python3
"""Build a small, standard GTFS archive for the routes used by the app."""

import argparse
import csv
import io
from pathlib import Path
import zipfile


def read_rows(source_dir, filename):
    path = source_dir / filename
    with path.open(newline="", encoding="utf-8-sig") as handle:
        return list(csv.DictReader(handle))


def write_rows(archive, filename, rows, fieldnames):
    buffer = io.StringIO(newline="")
    writer = csv.DictWriter(buffer, fieldnames=fieldnames, lineterminator="\n")
    writer.writeheader()
    writer.writerows(rows)
    archive.writestr(filename, buffer.getvalue())


def fieldnames(source_dir, filename):
    path = source_dir / filename
    with path.open(newline="", encoding="utf-8-sig") as handle:
        return csv.DictReader(handle).fieldnames


def build_subset(source_dir, output_path, route_ids):
    routes = [row for row in read_rows(source_dir, "routes.txt") if row["route_id"] in route_ids]
    trip_rows = [row for row in read_rows(source_dir, "trips.txt") if row["route_id"] in route_ids]
    trip_ids = {row["trip_id"] for row in trip_rows}
    service_ids = {row["service_id"] for row in trip_rows}

    stop_time_rows = [
        row for row in read_rows(source_dir, "stop_times.txt")
        if row["trip_id"] in trip_ids
    ]
    stop_ids = {row["stop_id"] for row in stop_time_rows}
    stop_rows = [row for row in read_rows(source_dir, "stops.txt") if row["stop_id"] in stop_ids]
    calendar_rows = [
        row for row in read_rows(source_dir, "calendar.txt")
        if row["service_id"] in service_ids
    ]
    calendar_date_rows = [
        row for row in read_rows(source_dir, "calendar_dates.txt")
        if row["service_id"] in service_ids
    ]

    output_path.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(output_path, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        datasets = {
            "routes.txt": routes,
            "stops.txt": stop_rows,
            "trips.txt": trip_rows,
            "stop_times.txt": stop_time_rows,
            "calendar.txt": calendar_rows,
            "calendar_dates.txt": calendar_date_rows,
            "feed_info.txt": read_rows(source_dir, "feed_info.txt"),
            "agency.txt": read_rows(source_dir, "agency.txt"),
        }
        for filename, rows in datasets.items():
            write_rows(archive, filename, rows, fieldnames(source_dir, filename))

    print(
        f"Wrote {output_path} with {len(routes)} routes, "
        f"{len(trip_rows)} trips, and {len(stop_time_rows)} stop times"
    )


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("source_dir", type=Path, help="Directory containing the PRT GTFS text files")
    parser.add_argument(
        "--output",
        type=Path,
        default=Path("gtfs/google_transit.zip"),
        help="Filtered GTFS archive to create",
    )
    parser.add_argument("--routes", nargs="+", default=["8", "13"])
    args = parser.parse_args()
    build_subset(args.source_dir, args.output, set(args.routes))


if __name__ == "__main__":
    main()
