import logging
from datetime import datetime, timezone
from typing import List, Optional
import uuid

import psycopg2
from psycopg2.extras import execute_values, RealDictCursor
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, field_validator

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("ECG_INGEST_API")

DB_CONFIG = {
    "dbname": "ecg_heart_health_monitoring_db",
    "user": "postgres",
    "password": "S@diee11",
    "host": "localhost",
    "port": 5432
}

app = FastAPI()


# ─── Models ───────────────────────────────────────────────────────────────────

class ECGPacket(BaseModel):
    timestamp_ms: int
    samples: List[int]

    @field_validator("samples")
    @classmethod
    def validate_samples(cls, v):
        if len(v) != 12:
            raise ValueError(f"Each packet must have exactly 12 samples, got {len(v)}")
        for s in v:
            if not (0 <= s <= 4095):
                raise ValueError(f"Sample value {s} out of range 0–4095")
        return v


class ECGIngestRequest(BaseModel):
    session_id: str
    packets: List[ECGPacket]


# ─── Encoding ─────────────────────────────────────────────────────────────────

def encode_samples(samples: List[int]) -> bytes:
    """Convert list of uint16 samples to little-endian BYTEA (24 bytes for 12 samples)."""
    buf = bytearray()
    for sample in samples:
        buf.append(sample & 0xFF)           # low byte
        buf.append((sample >> 8) & 0xFF)    # high byte
    encoded = bytes(buf)
    logger.info(f"Encoded packet size: {len(encoded)} bytes")
    return encoded


# ─── DB Helpers ───────────────────────────────────────────────────────────────

def get_conn():
    return psycopg2.connect(**DB_CONFIG)


def get_next_sequence_no(cur, session_id: str) -> int:
    """Get the next packet_sequence_no for this session."""
    cur.execute(
        "SELECT COALESCE(MAX(packet_sequence_no), 0) FROM ecg_raw_packet WHERE session_id = %s",
        (session_id,)
    )
    return cur.fetchone()[0] + 1


def ms_to_timestamp(ts_ms: int) -> datetime:
    return datetime.fromtimestamp(ts_ms / 1000.0, tz=timezone.utc)


# ─── Route ────────────────────────────────────────────────────────────────────

@app.post("/ecg/packets")
def ingest_packets(request: ECGIngestRequest):
    logger.info(f"Received {len(request.packets)} packets for session {request.session_id}")

    conn = get_conn()
    try:
        with conn.cursor() as cur:
            # Verify session exists
            cur.execute(
                "SELECT session_id FROM ecg_recording_session WHERE session_id = %s",
                (request.session_id,)
            )
            if cur.fetchone() is None:
                raise HTTPException(status_code=404, detail=f"Session {request.session_id} not found")

            next_seq = get_next_sequence_no(cur, request.session_id)

            rows = []
            for i, packet in enumerate(request.packets):
                blob = encode_samples(packet.samples)
                rows.append((
                    request.session_id,
                    ms_to_timestamp(packet.timestamp_ms),
                    next_seq + i,
                    12,                         # samples_per_packet
                    psycopg2.Binary(blob),
                    None,                       # packet_duration_ms
                    "unknown",                  # signal_quality_flag
                    False                       # features_extracted
                ))

            execute_values(cur, """
                INSERT INTO ecg_raw_packet (
                    session_id,
                    packet_timestamp_utc,
                    packet_sequence_no,
                    samples_per_packet,
                    sample_values,
                    packet_duration_ms,
                    signal_quality_flag,
                    features_extracted
                ) VALUES %s
            """, rows)

        conn.commit()
        logger.info(f"Inserted into DB: {len(rows)} packets")

    except HTTPException:
        raise
    except Exception as e:
        conn.rollback()
        logger.error(f"DB error: {e}")
        raise HTTPException(status_code=500, detail=str(e))
    finally:
        conn.close()

    return {"status": "success", "packets_inserted": len(request.packets)}


# ─── Report Response Models ───────────────────────────────────────────────────

class ReportReadyResponse(BaseModel):
    status: str = "ready"
    heart_health_score: int
    report_text: str
    llm_provider: Optional[str]
    llm_model: Optional[str]
    created_at: datetime


class ReportStatusResponse(BaseModel):
    status: str  # "processing" | "not_found"


# ─── GET /ecg/report/{session_id} ────────────────────────────────────────────

@app.get("/ecg/report/{session_id}")
def get_report(session_id: str):
    logger.info(f"Fetching report for session {session_id}")

    # Validate UUID format before hitting the DB
    try:
        uuid.UUID(session_id)
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid session_id — must be a valid UUID")

    conn = get_conn()
    try:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:

            # ── Check llm_report first (indexed on session_id) ───────────────
            cur.execute("""
                SELECT
                    heart_health_score,
                    report_text,
                    llm_provider,
                    llm_model,
                    generated_at AS created_at
                FROM llm_report
                WHERE session_id = %s
                ORDER BY generated_at DESC
                LIMIT 1
            """, (session_id,))
            report = cur.fetchone()

            if report:
                logger.info(f"Report found for session {session_id}")
                return ReportReadyResponse(
                    heart_health_score=report["heart_health_score"],
                    report_text=report["report_text"],
                    llm_provider=report["llm_provider"],
                    llm_model=report["llm_model"],
                    created_at=report["created_at"]
                )

            # ── No report yet — check if session has raw packets ─────────────
            cur.execute("""
                SELECT 1
                FROM ecg_raw_packet
                WHERE session_id = %s
                LIMIT 1
            """, (session_id,))
            exists = cur.fetchone()

            if exists:
                logger.info(f"Session {session_id} is still processing")
                return ReportStatusResponse(status="processing")

            # ── Session not found anywhere ────────────────────────────────────
            logger.info(f"Session {session_id} not found")
            return ReportStatusResponse(status="not_found")

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"DB error fetching report for {session_id}: {e}")
        raise HTTPException(status_code=500, detail=str(e))
    finally:
        conn.close()


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000, log_level="info")
