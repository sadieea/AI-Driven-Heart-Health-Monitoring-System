import time
import logging
import uuid
import psycopg2
import requests
from psycopg2.extras import RealDictCursor

DB_CONFIG = {
    "dbname": "ecg_heart_health_monitoring_db",
    "user": "postgres",
    "password": "S@diee11",
    "host": "localhost",
    "port": 5432
}

MCP_ENDPOINT = "http://localhost:8080/analyze"
POLL_SLEEP_SECONDS = 5
AGENT_ID = f"llm-agent-{uuid.uuid4()}"

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("LLM_ANALYSIS_AGENT")

def get_conn():
    return psycopg2.connect(**DB_CONFIG)


def ensure_llm_report_table():
    """Create llm_report table if it doesn't exist"""
    conn = get_conn()
    try:
        with conn.cursor() as cur:
            cur.execute("""
                CREATE TABLE IF NOT EXISTS llm_report (
                    report_id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
                    feature_id UUID REFERENCES ecg_features(feature_id),
                    session_id UUID REFERENCES ecg_recording_session(session_id),
                    heart_health_score INTEGER,
                    report_text TEXT,
                    llm_provider VARCHAR(50),
                    llm_model VARCHAR(100),
                    generated_at TIMESTAMPTZ DEFAULT NOW()
                )
            """)
            conn.commit()
            logger.info("✅ llm_report table verified/created")
    except Exception as e:
        conn.rollback()
        logger.error(f"Error creating llm_report table: {e}")
    finally:
        conn.close()


def fetch_unprocessed_feature():
    """Fetch one unprocessed ECG feature record"""
    conn = get_conn()
    try:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute("""
                SELECT 
                    feature_id,
                    session_id,
                    cycle_id,
                    mean_hr, mean_rr, sdnn, rmssd, nn50, pnn50,
                    vlf_power, lf_power, hf_power, lf_hf_ratio, 
                    total_power, spectral_entropy,
                    wavelet_d3_energy, wavelet_d4_energy, wavelet_d5_energy, wavelet_entropy,
                    signal_mean, signal_std, signal_variance, signal_skewness, signal_kurtosis
                FROM ecg_features
                WHERE report_generated = FALSE
                  AND cycle_id IS NULL
                ORDER BY created_at_utc
                LIMIT 1
            """)
            feature = cur.fetchone()
            return dict(feature) if feature else None
    finally:
        conn.close()

def call_mcp(payload, feature_id):
    """
    Call MCP server which decides:
    - primary LLM
    - fallback LLM
    - retries
    """
    try:
        response = requests.post(
            MCP_ENDPOINT,
            json={
                "packet_id": str(feature_id),
                "payload": payload
            },
            timeout=120
        )

        if response.status_code != 200:
            return False, None, response.text

        data = response.json()
        return data["success"], data.get("report"), data.get("error")

    except Exception as e:
        return False, None, str(e)


def save_report(feature_id, session_id, report_data):
    """Save LLM report to llm_report table and update ecg_features"""
    conn = get_conn()
    try:
        with conn.cursor() as cur:
            # Insert report into llm_report table
            cur.execute("""
                INSERT INTO llm_report (
                    feature_id,
                    session_id,
                    heart_health_score,
                    report_text,
                    llm_provider,
                    llm_model
                )
                VALUES (%s, %s, %s, %s, %s, %s)
            """, (
                feature_id,
                session_id,
                report_data.get("heart_health_score"),
                report_data.get("report_text"),
                report_data.get("llm_provider", "unknown"),
                report_data.get("llm_model", "unknown")
            ))
            
            # Mark feature as processed
            cur.execute("""
                UPDATE ecg_features
                SET report_generated = TRUE
                WHERE feature_id = %s
            """, (feature_id,))
            
        conn.commit()
        logger.info(f"✅ Report saved for feature {feature_id}")
    except Exception as e:
        conn.rollback()
        logger.error(f"Error saving report: {e}")
        raise
    finally:
        conn.close()


def mark_failed(feature_id, error_msg):
    """Log failure (keeping report_generated = FALSE for retry)"""
    logger.error(f"❌ Failed to process feature {feature_id}: {error_msg}")

def run():
    logger.info(f"🚀 Starting LLM Analysis Agent [{AGENT_ID}]")
    

    ensure_llm_report_table()

    while True:
        try:
            feature = fetch_unprocessed_feature()

            if not feature:
                logger.info("No unprocessed features found, sleeping...")
                time.sleep(POLL_SLEEP_SECONDS)
                continue

            feature_id = feature["feature_id"]
            session_id = feature["session_id"]
            
            payload = {k: v for k, v in feature.items() 
                      if k not in ["feature_id", "session_id", "cycle_id"]}

            # Clean NaN/Inf values (not JSON compliant)
            import math
            cleaned_payload = {}
            for k, v in payload.items():
                if isinstance(v, float):
                    if math.isnan(v) or math.isinf(v):
                        cleaned_payload[k] = None  
                    else:
                        cleaned_payload[k] = v
                else:
                    cleaned_payload[k] = v
            
            payload = cleaned_payload
            logger.info(f"📊 Processing feature {feature_id} from session {session_id}")

            # Call MCP
            success, report, error = call_mcp(payload, feature_id)

            if success and report:
                save_report(feature_id, session_id, report)
                logger.info(f"✅ Successfully processed feature {feature_id}")
            else:
                mark_failed(feature_id, error or "Unknown error")
                time.sleep(POLL_SLEEP_SECONDS)

        except KeyboardInterrupt:
            logger.info("🛑 Agent stopped by user")
            break
        except Exception as e:
            logger.error(f"❌ Unexpected error: {e}")
            time.sleep(POLL_SLEEP_SECONDS)


if __name__ == "__main__":
    run()
