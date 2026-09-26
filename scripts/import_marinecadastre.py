from pathlib import Path
from io import StringIO
import os
import sys
import time

import pandas as pd
import psycopg


# ============================================================
# Configuration
# ============================================================

SCRIPT_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = SCRIPT_DIR.parent
DATA_DIR = PROJECT_ROOT / "data" / "raw"

POSTGRES_DSN = os.getenv(
    "POSTGRES_DSN",
    "postgresql://ghast:ghast@localhost:5432/ghast",
)

# East Coast / Mid-Atlantic training region
MIN_LAT = 25.0
MAX_LAT = 42.0
MIN_LON = -82.0
MAX_LON = -65.0

# Number of CSV rows pandas reads at a time.
CHUNK_SIZE = 100_000

# Expected MarineCadastre columns
REQUIRED_COLUMNS = [
    "mmsi",
    "base_date_time",
    "longitude",
    "latitude",
    "sog",
    "cog",
    "heading",
    "vessel_name",
    "imo",
    "call_sign",
    "vessel_type",
    "status",
    "length",
    "width",
    "draft",
    "cargo",
    "transceiver",
]

COPY_COLUMNS = [
    "received_at",
    "mmsi",
    "ship_name",
    "message_type",
    "latitude",
    "longitude",
    "position",
    "sog_knots",
    "cog_deg",
    "true_heading_deg",
    "rate_of_turn",
    "navigational_status",
    "raim",
    "position_accuracy",
]


# ============================================================
# Logging
# ============================================================

def log(message=""):
    print(message, flush=True)


def section(title):
    log()
    log("=" * 70)
    log(title)
    log("=" * 70)


def elapsed(start):
    return f"{time.perf_counter() - start:.2f}s"


# ============================================================
# Database
# ============================================================

def connect_database():
    log("[DB] Connecting to PostgreSQL...")
    start = time.perf_counter()

    conn = psycopg.connect(POSTGRES_DSN)

    log(f"[DB] Connected successfully in {elapsed(start)}")

    return conn


def verify_database(conn):
    log("[DB] Verifying vessel_position table...")

    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT
                COUNT(*),
                MIN(received_at),
                MAX(received_at)
            FROM vessel_position
            """
        )

        count, min_time, max_time = cur.fetchone()

    log(f"[DB] Existing rows: {count:,}")

    if min_time is not None:
        log(f"[DB] Existing time range: {min_time} -> {max_time}")
    else:
        log("[DB] vessel_position is currently empty")


# ============================================================
# CSV / Data processing
# ============================================================

def validate_columns(columns, file_path):
    missing = [column for column in REQUIRED_COLUMNS if column not in columns]

    if missing:
        raise RuntimeError(
            f"Missing required columns in {file_path.name}: {missing}"
        )


def process_chunk(df, chunk_number):
    input_rows = len(df)

    log(f"    [CHUNK {chunk_number}] Read {input_rows:,} rows")

    # --------------------------------------------------------
    # Convert numeric fields
    # --------------------------------------------------------

    log("    [CHUNK] Converting numeric fields...")

    df["mmsi"] = pd.to_numeric(
        df["mmsi"],
        errors="coerce",
    ).astype("Int64")

    df["latitude"] = pd.to_numeric(
        df["latitude"],
        errors="coerce",
    )

    df["longitude"] = pd.to_numeric(
        df["longitude"],
        errors="coerce",
    )

    df["sog"] = pd.to_numeric(
        df["sog"],
        errors="coerce",
    )

    df["cog"] = pd.to_numeric(
        df["cog"],
        errors="coerce",
    )

    # IMPORTANT:
    # PostgreSQL INTEGER columns require values like 3,
    # not 3.0. Nullable Int64 handles NaN correctly.
    df["heading"] = pd.to_numeric(
        df["heading"],
        errors="coerce",
    ).astype("Int64")

    df["status"] = pd.to_numeric(
        df["status"],
        errors="coerce",
    ).astype("Int64")

    # --------------------------------------------------------
    # Parse timestamps
    # --------------------------------------------------------

    log("    [CHUNK] Parsing timestamps...")

    df["base_date_time"] = pd.to_datetime(
        df["base_date_time"],
        errors="coerce",
        utc=True,
    )

    # --------------------------------------------------------
    # Remove invalid records
    # --------------------------------------------------------

    before_invalid = len(df)

    df = df.dropna(
        subset=[
            "mmsi",
            "base_date_time",
            "latitude",
            "longitude",
        ]
    )

    invalid_removed = before_invalid - len(df)

    if invalid_removed:
        log(
            f"    [CHUNK] Removed {invalid_removed:,} rows "
            f"with missing/invalid required fields"
        )

    # --------------------------------------------------------
    # Geographic filter
    # --------------------------------------------------------

    before_geo = len(df)

    df = df[
        (df["latitude"] >= MIN_LAT)
        & (df["latitude"] <= MAX_LAT)
        & (df["longitude"] >= MIN_LON)
        & (df["longitude"] <= MAX_LON)
    ]

    outside_region = before_geo - len(df)

    log(
        f"    [CHUNK] Geographic filter: "
        f"{len(df):,} kept / {outside_region:,} outside region"
    )

    if df.empty:
        log("    [CHUNK] Nothing to insert")
        return None, 0

    # --------------------------------------------------------
    # Build target dataframe
    # --------------------------------------------------------

    log("    [CHUNK] Building vessel_position records...")

    output = pd.DataFrame()

    output["received_at"] = df["base_date_time"]
    output["mmsi"] = df["mmsi"]
    output["ship_name"] = df["vessel_name"]
    output["message_type"] = "historical"

    output["latitude"] = df["latitude"]
    output["longitude"] = df["longitude"]

    # PostGIS geography input.
    # Coordinate order MUST be longitude latitude.
    output["position"] = (
        "SRID=4326;POINT("
        + df["longitude"].astype(str)
        + " "
        + df["latitude"].astype(str)
        + ")"
    )

    output["sog_knots"] = df["sog"]
    output["cog_deg"] = df["cog"]
    output["true_heading_deg"] = df["heading"]

    # These fields are not present in MarineCadastre CSV.
    output["rate_of_turn"] = pd.NA
    output["navigational_status"] = df["status"]
    output["raim"] = pd.NA
    output["position_accuracy"] = pd.NA

    # Ensure exact column order.
    output = output[COPY_COLUMNS]

    log(f"    [CHUNK] Prepared {len(output):,} database rows")

    return output, len(output)


# ============================================================
# PostgreSQL COPY
# ============================================================

def copy_chunk(conn, df):
    log(f"    [COPY] Preparing {len(df):,} rows for PostgreSQL...")

    buffer = StringIO()

    df.to_csv(
        buffer,
        index=False,
        header=False,
        na_rep="\\N",
    )

    buffer.seek(0)

    start = time.perf_counter()

    log("    [COPY] Sending rows to PostgreSQL...")

    with conn.cursor() as cur:
        with cur.copy(
            """
            COPY vessel_position (
                received_at,
                mmsi,
                ship_name,
                message_type,
                latitude,
                longitude,
                position,
                sog_knots,
                cog_deg,
                true_heading_deg,
                rate_of_turn,
                navigational_status,
                raim,
                position_accuracy
            )
            FROM STDIN
            WITH (
                FORMAT CSV,
                NULL '\\N'
            )
            """
        ) as copy:
            copy.write(buffer.read())

    conn.commit()

    log(
        f"    [COPY] Successfully inserted "
        f"{len(df):,} rows in {elapsed(start)}"
    )

    return len(df)


# ============================================================
# Process one file
# ============================================================

def process_file(conn, file_path, file_number, total_files):
    section(
        f"[{file_number}/{total_files}] {file_path.name}"
    )

    file_start = time.perf_counter()

    file_size_mb = file_path.stat().st_size / (1024 * 1024)

    log(f"[FILE] Path: {file_path}")
    log(f"[FILE] Size: {file_size_mb:,.1f} MB")
    log(f"[FILE] Chunk size: {CHUNK_SIZE:,} rows")

    total_read = 0
    total_region = 0
    total_inserted = 0
    chunk_number = 0

    log("[FILE] Opening CSV with pandas...")

    try:
        reader = pd.read_csv(
            file_path,
            chunksize=CHUNK_SIZE,
            low_memory=False,
        )

        for df in reader:
            chunk_number += 1

            chunk_start = time.perf_counter()

            output, inserted_count = process_chunk(
                df,
                chunk_number,
            )

            total_read += len(df)

            if output is not None:
                total_region += inserted_count

                inserted = copy_chunk(
                    conn,
                    output,
                )

                total_inserted += inserted

            log(
                f"    [CHUNK {chunk_number}] "
                f"Finished in {elapsed(chunk_start)}"
            )

            log(
                f"    [PROGRESS] "
                f"Read: {total_read:,} | "
                f"Prepared: {total_region:,} | "
                f"Inserted: {total_inserted:,}"
            )

    except Exception:
        log()
        log(
            f"[ERROR] Failed while processing "
            f"{file_path.name}"
        )
        log(
            f"[ERROR] Rows read before failure: "
            f"{total_read:,}"
        )
        log(
            f"[ERROR] Rows inserted before failure: "
            f"{total_inserted:,}"
        )
        raise

    log()
    log(f"[FILE] Completed: {file_path.name}")
    log(f"[FILE] CSV rows read:       {total_read:,}")
    log(f"[FILE] Rows in region:      {total_region:,}")
    log(f"[FILE] Rows inserted:       {total_inserted:,}")
    log(f"[FILE] Chunks processed:    {chunk_number:,}")
    log(f"[FILE] Processing time:     {elapsed(file_start)}")

    return total_inserted


# ============================================================
# Main
# ============================================================

def main():
    overall_start = time.perf_counter()

    section("GHAST MarineCadastre historical AIS importer")

    log(f"[CONFIG] Project root:     {PROJECT_ROOT}")
    log(f"[CONFIG] Data directory:   {DATA_DIR}")
    log(f"[CONFIG] PostgreSQL DSN:    {POSTGRES_DSN}")
    log(
        f"[CONFIG] Region:            "
        f"lat {MIN_LAT}..{MAX_LAT}, "
        f"lon {MIN_LON}..{MAX_LON}"
    )
    log(f"[CONFIG] Chunk size:        {CHUNK_SIZE:,}")

    # --------------------------------------------------------
    # Find files
    # --------------------------------------------------------

    log()
    log("[DISCOVERY] Searching for MarineCadastre CSV files...")

    files = sorted(
        DATA_DIR.glob("ais-2026-04-*.csv")
    )

    if not files:
        log("[ERROR] No files found.")
        log(f"[ERROR] Expected files under: {DATA_DIR}")
        sys.exit(1)

    log(f"[DISCOVERY] Files found: {len(files)}")

    for index, file_path in enumerate(files, start=1):
        size_mb = file_path.stat().st_size / (1024 * 1024)

        log(
            f"    {index:02d}. "
            f"{file_path.name} "
            f"({size_mb:,.1f} MB)"
        )

    # --------------------------------------------------------
    # Database connection
    # --------------------------------------------------------

    conn = None

    try:
        conn = connect_database()

        verify_database(conn)

        # ----------------------------------------------------
        # Import files
        # ----------------------------------------------------

        section("STARTING IMPORT")

        total_inserted = 0

        for file_number, file_path in enumerate(
            files,
            start=1,
        ):
            inserted = process_file(
                conn,
                file_path,
                file_number,
                len(files),
            )

            total_inserted += inserted

            log()
            log(
                f"[TOTAL] Cumulative inserted rows: "
                f"{total_inserted:,}"
            )

        # ----------------------------------------------------
        # Final verification
        # ----------------------------------------------------

        section("IMPORT COMPLETE")

        log(
            f"[RESULT] Total rows inserted: "
            f"{total_inserted:,}"
        )

        log(
            f"[RESULT] Total execution time: "
            f"{elapsed(overall_start)}"
        )

        log()
        log("[DB] Running final database verification...")

        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT
                    COUNT(*),
                    MIN(received_at),
                    MAX(received_at)
                FROM vessel_position
                """
            )

            count, min_time, max_time = cur.fetchone()

        log(f"[DB] vessel_position rows: {count:,}")
        log(f"[DB] Earliest timestamp:    {min_time}")
        log(f"[DB] Latest timestamp:      {max_time}")

        log()
        log("=" * 70)
        log("SUCCESS")
        log("=" * 70)

    except KeyboardInterrupt:
        log()
        log("[STOP] Import interrupted by user.")
        sys.exit(130)

    except Exception as exc:
        log()
        log("=" * 70)
        log("IMPORT FAILED")
        log("=" * 70)
        log(f"[ERROR] {type(exc).__name__}: {exc}")
        log(
            f"[ERROR] Total runtime before failure: "
            f"{elapsed(overall_start)}"
        )
        sys.exit(1)

    finally:
        if conn is not None:
            conn.close()
            log("[DB] Connection closed.")


if __name__ == "__main__":
    main()