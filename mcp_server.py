import logging
import psycopg2
import requests
from fastapi import FastAPI, HTTPException
from psycopg2.extras import RealDictCursor
from pydantic import BaseModel
from typing import Dict, Any, Optional

DB_CONFIG = {
    "dbname": "ecg_heart_health_monitoring_db",
    "user": "postgres",
    "password": "S@diee11",
    "host": "localhost",
    "port": 5432
}

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("MCP_SERVER")

# Create minimal FastAPI app (avoiding middleware issues)
app = FastAPI()

class AnalyzeRequest(BaseModel):
    packet_id: str
    payload: Dict[str, Any]

def get_conn():
    return psycopg2.connect(**DB_CONFIG)


def get_llm_configs():
    conn = get_conn()
    try:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute("""
                SELECT *
                FROM llm_config
                WHERE enabled = true
            """)
            configs = cur.fetchall()
            logger.info(f"📋 Found {len(configs)} enabled LLM configs")
            for cfg in configs:
                logger.info(f"   - {cfg['provider']}: {cfg['model_name']} (Primary={cfg['is_primary']}, Fallback={cfg['is_fallback']})")
            return configs
    finally:
        conn.close()


def choose_primary_and_fallback(configs):
    primary = next((c for c in configs if c["is_primary"]), None)
    fallback = next((c for c in configs if c["is_fallback"]), None)
    
    if primary:
        logger.info(f"✅ Primary LLM: {primary['provider']} - {primary['model_name']}")
    else:
        logger.warning("⚠️ No primary LLM configured!")
        
    if fallback:
        logger.info(f"✅ Fallback LLM: {fallback['provider']} - {fallback['model_name']}")
    else:
        logger.warning("⚠️ No fallback LLM configured!")
    
    return primary, fallback


def interpret_features(payload: dict) -> dict:
    """
    Convert raw numeric ECG features into a structured medical summary.
    This is injected into LLM prompts so the model receives both raw values
    and pre-computed clinical interpretations — reducing hallucination risk.
    """
    summary = {}

    # ── Heart Rate ────────────────────────────────────────────────────────────
    hr = payload.get("mean_hr")
    if hr is not None:
        summary["heart_rate_bpm"] = round(hr, 1)
        if hr < 60:
            summary["hr_status"] = "bradycardia"
        elif hr <= 100:
            summary["hr_status"] = "normal"
        else:
            summary["hr_status"] = "elevated (tachycardia)"

    # ── HRV Status (SDNN) ─────────────────────────────────────────────────────
    # SDNN < 20ms → very low HRV (high risk)
    # SDNN 20–50ms → reduced HRV (moderate concern)
    # SDNN > 50ms → normal HRV
    sdnn = payload.get("sdnn")
    if sdnn is not None:
        summary["sdnn_ms"] = round(sdnn, 2)
        if sdnn < 20:
            summary["hrv_status"] = "very low — high cardiovascular risk"
        elif sdnn < 50:
            summary["hrv_status"] = "reduced"
        else:
            summary["hrv_status"] = "normal"

    # ── Recovery / Parasympathetic (RMSSD) ────────────────────────────────────
    # RMSSD > 40ms → good vagal tone / recovery
    # RMSSD 20–40ms → moderate
    # RMSSD < 20ms → poor recovery
    rmssd = payload.get("rmssd")
    if rmssd is not None:
        summary["rmssd_ms"] = round(rmssd, 2)
        if rmssd >= 40:
            summary["recovery_status"] = "good"
        elif rmssd >= 20:
            summary["recovery_status"] = "moderate"
        else:
            summary["recovery_status"] = "poor"

    # ── Autonomic Balance (LF/HF ratio) ──────────────────────────────────────
    # LF/HF < 0.5 → parasympathetic dominance (very relaxed / sleep)
    # LF/HF 0.5–2.0 → balanced
    # LF/HF > 2.0 → sympathetic dominance (stress / exertion)
    lf_hf = payload.get("lf_hf_ratio")
    if lf_hf is not None:
        summary["lf_hf_ratio"] = round(lf_hf, 3)
        if lf_hf > 2.0:
            summary["autonomic_balance"] = "sympathetic dominance (stress/exertion)"
            summary["stress_level"] = "high"
        elif lf_hf >= 0.5:
            summary["autonomic_balance"] = "balanced"
            summary["stress_level"] = "low"
        else:
            summary["autonomic_balance"] = "parasympathetic dominance (rest/recovery)"
            summary["stress_level"] = "low"

    # ── Frequency Band Summary ────────────────────────────────────────────────
    hf = payload.get("hf_power")
    lf = payload.get("lf_power")
    if hf is not None and lf is not None:
        summary["hf_power"] = round(hf, 6)
        summary["lf_power"] = round(lf, 6)
        dominant = "HF (parasympathetic)" if hf >= lf else "LF (sympathetic/mixed)"
        summary["dominant_frequency_band"] = dominant

    # ── Mean RR ───────────────────────────────────────────────────────────────
    mean_rr = payload.get("mean_rr")
    if mean_rr is not None:
        summary["mean_rr_ms"] = round(mean_rr, 1)

    # ── pNN50 ─────────────────────────────────────────────────────────────────
    pnn50 = payload.get("pnn50")
    if pnn50 is not None:
        summary["pnn50_percent"] = round(pnn50 * 100, 1)
        summary["vagal_activity"] = "high" if pnn50 > 0.2 else "low"

    logger.info(f"Interpreted summary: {summary}")
    return summary


def build_score_prompt(payload, summary):
    return f"""
You are a clinical heart health analysis engine.

Scoring rules:
- Score from 0 to 10
- 10 = clinically normal, healthy heart indicators
- 7–9 = generally healthy with minor concerns
- 4–6 = moderate risk indicators
- 1–3 = high risk indicators
- 0 = severe or multiple dangerous indicators
- Do NOT include explanation, text, or punctuation.

Clinical Interpretation:
{summary}

Raw Heart Health Features:
{payload}
""".strip()


def build_report_prompt(payload, summary):
    return f"""
You are a medical report generator specializing in cardiovascular health.
Write in a professional, patient-friendly tone.

Requirements:
- First, list ALL input features exactly as provided (label and value).
- Then write ONE paragraph (~250 words) summarizing overall heart health.
- Discuss risk factors and protective factors.
- Do NOT provide medical diagnosis.
- Do NOT use bullet points in the paragraph.
- Keep language clear and non-alarming.

Clinical Interpretation:
{summary}

Raw Heart Health Features:
{payload}
""".strip()

def call_openai(api_key, prompt, timeout):
    """
    Call OpenAI API with GPT-4o-mini model
    API Docs: https://platform.openai.com/docs/api-reference/chat
    """
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json"
    }

    body = {
        "model": "gpt-4o-mini",  
        "messages": [
            {"role": "system", "content": "You are a medical analysis engine."},
            {"role": "user", "content": prompt}
        ],
        "temperature": 0.2,
        "max_tokens": 1500
    }

    r = requests.post(
        "https://api.openai.com/v1/chat/completions",
        headers=headers,
        json=body,
        timeout=timeout
    )

    if not r.ok:
        raise RuntimeError(f"OpenAI HTTP {r.status_code}: {r.text}")
    
    return r.json()["choices"][0]["message"]["content"]


def call_gemini(api_key, prompt, timeout):
    """
    Call Google Gemini API with gemini-2.5-flash model
    API Docs: https://ai.google.dev/api/rest/v1beta/models/generateContent
    """
    headers = {
        "Content-Type": "application/json"
    }

    body = {
        "contents": [{
            "parts": [{
                "text": prompt
            }]
        }],
        "generationConfig": {
            "temperature": 0.2,
            "maxOutputTokens": 1500,
            "topP": 0.8,
            "topK": 40
        }
    }

    url = f"https://generativelanguage.googleapis.com/v1beta/models/gemini-2.5-flash:generateContent?key={api_key}"

    r = requests.post(
        url,
        headers=headers,
        json=body,
        timeout=timeout
    )

    if not r.ok:
        raise RuntimeError(f"Gemini HTTP {r.status_code}: {r.text}")
    
    response_data = r.json()
    
    if "candidates" in response_data and len(response_data["candidates"]) > 0:
        candidate = response_data["candidates"][0]
        parts = candidate.get("content", {}).get("parts", [])
        if parts:
            return parts[0].get("text", "")
    
    raise RuntimeError(f"Gemini returned empty/invalid response: {response_data}")


def call_llm(config, prompt):
    provider = config["provider"]
    api_key = config["api_key"]
    timeout = config.get("timeout_seconds", 60)

    logger.info(f"Calling {provider} ({config['model_name']})")

    if provider == "openai":
        return call_openai(api_key, prompt, timeout)
    elif provider in ("gemini", "google"):  # Support both provider names
        return call_gemini(api_key, prompt, timeout)
    else:
        raise RuntimeError(f"Unsupported provider: {provider}")

def generate_score(llm_config, payload, summary):
    prompt = build_score_prompt(payload, summary)
    response = call_llm(llm_config, prompt)

    try:
        score = int(response.strip().split()[0])
    except (ValueError, IndexError):
        raise RuntimeError(f"Invalid score returned: {response}")

    if score < 0 or score > 10:
        raise RuntimeError(f"Score out of range: {score}")

    return score


def generate_report(llm_config, payload, summary):
    prompt = build_report_prompt(payload, summary)
    return call_llm(llm_config, prompt)


@app.post("/analyze")
def analyze(request: AnalyzeRequest):
    payload = request.payload

    if payload is None:
        raise HTTPException(status_code=400, detail="Payload missing")

    configs = get_llm_configs()
    primary, fallback = choose_primary_and_fallback(configs)

    if not primary:
        return {
            "success": False,
            "report": None,
            "error": "No primary LLM configured"
        }

    for llm in [primary, fallback]:
        if not llm:
            continue

        try:
            logger.info(f"🔄 Attempting to call {llm['provider']} ({llm['model_name']})")
            summary = interpret_features(payload)
            score = generate_score(llm, payload, summary)
            report_text = generate_report(llm, payload, summary)

            logger.info(f"✅ Successfully generated report with {llm['provider']}")
            return {
                "success": True,
                "report": {
                    "heart_health_score": score,
                    "report_text": report_text,
                    "llm_provider": llm['provider'],
                    "llm_model": llm['model_name']
                },
                "error": None
            }

        except Exception as e:
            error_details = f"LLM {llm['provider']} ({llm['model_name']}) failed: {type(e).__name__}: {str(e)}"
            logger.error(f"❌ {error_details}")
            import traceback
            logger.error(f"Full traceback:\n{traceback.format_exc()}")

    return {
        "success": False,
        "report": None,
        "error": "Primary and fallback LLMs failed - check server logs for details"
    }


if __name__ == "__main__":
    import uvicorn
    logger.info("Starting MCP Server on http://localhost:8080")
    logger.info("Run with: uvicorn mcp_server:app --host 0.0.0.0 --port 8080")
    uvicorn.run(app, host="0.0.0.0", port=8080, log_level="info")
