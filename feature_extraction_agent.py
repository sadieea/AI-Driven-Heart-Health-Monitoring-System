import logging
import numpy as np
import psycopg2
import pywt
from scipy import stats, signal as sp_signal

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("FEATURE_EXTRACTION")

FS = 250  # Hz

# ─── SIGNAL QUALITY THRESHOLDS ────────────────────────────────────────────────
# Flatline: filtered signal std below this → no real ECG activity
FLATLINE_STD_THRESHOLD      = 10.0    # ADC units (post-DC-removal)

# Amplitude range (max - min) of filtered signal
AMPLITUDE_MIN_THRESHOLD     = 50.0   # below → electrode contact issue
AMPLITUDE_MAX_THRESHOLD     = 3000.0 # above → severe noise / artifact

# RR interval coefficient of variation (std/mean) — extreme irregularity
RR_CV_THRESHOLD             = 0.5    # >50% CV → likely noise, not real rhythm

# Peak count relative to recording duration
MIN_PEAKS_PER_MINUTE        = 20     # below → too few beats detected
MAX_PEAKS_PER_MINUTE        = 220    # above → noise spikes being counted as peaks

DB_CONFIG = {
    "dbname": "ecg_heart_health_monitoring_db",
    "user": "postgres",
    "password": "S@diee11",
    "host": "localhost",
    "port": 5432
}


# ─── DB ───────────────────────────────────────────────────────────────────────

def get_connection():
    return psycopg2.connect(**DB_CONFIG)


def fetch_unprocessed_sessions(conn):
    with conn.cursor() as cur:
        cur.execute("""
            SELECT DISTINCT session_id
            FROM ecg_raw_packet
            WHERE features_extracted = FALSE
            ORDER BY session_id;
        """)
        return [row[0] for row in cur.fetchall()]


def bytes_to_samples(bytea):
    return np.frombuffer(bytea, dtype='<u2')  # uint16 little-endian


def read_ecg_signal(conn, session_id):
    with conn.cursor() as cur:
        cur.execute("""
            SELECT sample_values
            FROM ecg_raw_packet
            WHERE session_id = %s
            ORDER BY packet_sequence_no;
        """, (session_id,))
        packets = [bytes_to_samples(row[0]) for row in cur.fetchall()]

    if not packets:
        raise ValueError(f"No packets found for session {session_id}")

    return np.concatenate(packets)


def insert_features(conn, session_id, features):
    columns = ", ".join(features.keys())
    placeholders = ", ".join(["%s"] * len(features))
    query = f"""
        INSERT INTO ecg_features (session_id, cycle_id, {columns})
        VALUES (%s, NULL, {placeholders});
    """
    with conn.cursor() as cur:
        cur.execute(query, [session_id] + list(features.values()))


def mark_packets_processed(conn, session_id):
    with conn.cursor() as cur:
        cur.execute("""
            UPDATE ecg_raw_packet
            SET features_extracted = TRUE
            WHERE session_id = %s;
        """, (session_id,))


# ─── SIGNAL QUALITY EVALUATION ───────────────────────────────────────────────

def evaluate_signal_quality(filtered_signal, peaks, rr, fs=FS):
    """
    Evaluate ECG signal usability before feature extraction.

    Checks (in order of severity):
      1. Flatline — std too low, no real signal
      2. Amplitude range — too small (electrode off) or too large (artifact)
      3. Peak count rate — too few or too many peaks per minute
      4. RR interval consistency — extreme CV indicates noise, not arrhythmia

    Returns:
        dict with keys: quality ("good"|"moderate"|"poor"), reason, confidence (0–1)
    """
    duration_min = len(filtered_signal) / fs / 60.0

    # ── Check 1: Flatline ────────────────────────────────────────────────────
    sig_std = float(np.std(filtered_signal))
    if sig_std < FLATLINE_STD_THRESHOLD:
        logger.warning(f"Signal quality: poor | Reason: flatline (std={sig_std:.2f})")
        return {"quality": "poor", "reason": f"flatline — std={sig_std:.2f}", "confidence": 0.0}

    # ── Check 2: Amplitude range ─────────────────────────────────────────────
    sig_range = float(np.max(filtered_signal) - np.min(filtered_signal))
    if sig_range < AMPLITUDE_MIN_THRESHOLD:
        logger.warning(f"Signal quality: poor | Reason: amplitude too small (range={sig_range:.2f})")
        return {"quality": "poor", "reason": f"amplitude too small — range={sig_range:.2f}", "confidence": 0.1}
    if sig_range > AMPLITUDE_MAX_THRESHOLD:
        logger.warning(f"Signal quality: poor | Reason: amplitude too large (range={sig_range:.2f})")
        return {"quality": "poor", "reason": f"amplitude too large / artifact — range={sig_range:.2f}", "confidence": 0.1}

    # ── Check 3: Peak count rate ─────────────────────────────────────────────
    if duration_min > 0:
        peaks_per_min = len(peaks) / duration_min
        if peaks_per_min < MIN_PEAKS_PER_MINUTE:
            logger.warning(f"Signal quality: poor | Reason: too few peaks ({peaks_per_min:.1f}/min)")
            return {"quality": "poor", "reason": f"too few R-peaks — {peaks_per_min:.1f}/min", "confidence": 0.2}
        if peaks_per_min > MAX_PEAKS_PER_MINUTE:
            logger.warning(f"Signal quality: poor | Reason: too many peaks ({peaks_per_min:.1f}/min)")
            return {"quality": "poor", "reason": f"too many R-peaks (noise) — {peaks_per_min:.1f}/min", "confidence": 0.2}

    # ── Check 4: RR interval consistency ────────────────────────────────────
    if len(rr) >= 3:
        rr_cv = float(np.std(rr) / np.mean(rr))  # coefficient of variation
        if rr_cv > RR_CV_THRESHOLD:
            logger.warning(f"Signal quality: moderate | Reason: irregular RR (CV={rr_cv:.2f})")
            return {"quality": "moderate", "reason": f"high RR irregularity — CV={rr_cv:.2f}", "confidence": 0.5}

    # ── All checks passed ────────────────────────────────────────────────────
    logger.info("Signal quality: good")
    return {"quality": "good", "reason": "all checks passed", "confidence": 1.0}




def preprocess_signal(raw_signal, fs=FS):
    """
    Convert uint16 ECG → filtered float signal.
    1. Cast to float64
    2. Remove DC offset (subtract mean)
    3. Butterworth bandpass 0.5–40 Hz, order 3
    """
    sig = raw_signal.astype(np.float64)
    sig -= np.mean(sig)  # remove DC offset

    nyq = fs / 2.0
    low = 0.5 / nyq
    high = 40.0 / nyq
    b, a = sp_signal.butter(3, [low, high], btype='band')
    filtered = sp_signal.filtfilt(b, a, sig)  # zero-phase filtering

    return filtered


# ─── STEP 2: R-PEAK DETECTION ─────────────────────────────────────────────────

def detect_r_peaks(filtered_signal, fs=FS):
    """
    Detect R-peaks using scipy find_peaks.
    - min distance between peaks = 0.4s (max ~150 BPM)
    - min height = mean + 0.5 * std (avoids noise spikes)
    """
    min_distance = int(fs * 0.4)
    threshold = np.mean(filtered_signal) + 0.5 * np.std(filtered_signal)

    peaks, _ = sp_signal.find_peaks(
        filtered_signal,
        distance=min_distance,
        height=threshold
    )

    logger.info(f"Detected {len(peaks)} R-peaks")
    return peaks


# ─── STEP 3: RR INTERVALS ─────────────────────────────────────────────────────

def compute_rr_intervals(peaks, fs=FS):
    """
    Compute RR intervals in ms from peak indices.
    Filter physiologically valid range: 300–2000 ms (30–200 BPM).
    """
    if len(peaks) < 2:
        return np.array([])

    rr = np.diff(peaks) * (1000.0 / fs)

    # Physiological validity filter
    rr_valid = rr[(rr > 300) & (rr < 2000)]

    logger.info(f"RR intervals count: {len(rr_valid)} (raw: {len(rr)})")
    return rr_valid


# ─── STEP 5: HRV FEATURES ─────────────────────────────────────────────────────

def extract_hrv_features(rr):
    """
    Time-domain HRV. Requires at least 2 RR intervals.
    """
    features = {
        "mean_rr": float(np.mean(rr)),
        "mean_hr": float(60000.0 / np.mean(rr)),
    }

    logger.info(f"Mean HR: {features['mean_hr']:.1f} BPM")

    if len(rr) >= 2:
        diff_rr = np.diff(rr)
        nn50 = int(np.sum(np.abs(diff_rr) > 50))
        features.update({
            "sdnn":  float(np.std(rr, ddof=1)),
            "rmssd": float(np.sqrt(np.mean(diff_rr ** 2))),
            "nn50":  nn50,
            "pnn50": float(nn50 / len(diff_rr)),
        })

    return features


# ─── STEP 6: FREQUENCY FEATURES ───────────────────────────────────────────────

def extract_frequency_features(rr):
    """
    Frequency-domain HRV using Welch PSD on RR series.
    Requires at least 8 RR intervals for meaningful results.
    nperseg is capped to avoid Welch crashing on short signals.
    """
    if len(rr) < 8:
        logger.warning(f"Skipping frequency features — only {len(rr)} RR intervals")
        return {}

    rr_sec = rr / 1000.0
    rr_detrended = sp_signal.detrend(rr_sec)

    nperseg = min(len(rr_detrended), 64)
    f, psd = sp_signal.welch(rr_detrended, fs=4.0, nperseg=nperseg)

    def band_power(low, high):
        mask = (f >= low) & (f <= high)
        if not np.any(mask):
            return 0.0
        return float(np.trapz(psd[mask], f[mask]))

    vlf = band_power(0.003, 0.04)
    lf  = band_power(0.04,  0.15)
    hf  = band_power(0.15,  0.4)

    return {
        "vlf_power":       vlf,
        "lf_power":        lf,
        "hf_power":        hf,
        "lf_hf_ratio":     float(lf / hf) if hf > 0 else None,
        "total_power":     vlf + lf + hf,
        "spectral_entropy": float(stats.entropy(psd + 1e-10)),  # avoid log(0)
    }


# ─── STEP 7: WAVELET FEATURES (FIXED) ────────────────────────────────────────

def extract_wavelet_features(ecg_signal):
    """
    DWT using db4 wavelet.
    coeffs layout from pywt.wavedec:
        [cA5, cD5, cD4, cD3, cD2, cD1]
         [0]   [1]  [2]  [3]  [4]  [5]
    So:
        coeffs[-1] = cD1 (highest freq)
        coeffs[-3] = cD3
        coeffs[-4] = cD4
        coeffs[-5] = cD5 (lowest detail freq)

    Minimum signal length for level-5 DWT with db4:
        (filter_len - 1) * (2^level) = 7 * 32 = 224 samples
    """
    min_len = (pywt.Wavelet('db4').filter_bank[0].__len__() - 1) * (2 ** 5)
    level = 5

    if len(ecg_signal) < min_len:
        # Reduce decomposition level to fit signal length
        level = pywt.dwt_max_level(len(ecg_signal), 'db4')
        logger.warning(f"Signal too short for level-5 DWT, reducing to level {level}")

    if level < 1:
        logger.warning("Signal too short for any wavelet decomposition, skipping")
        return {}

    coeffs = pywt.wavedec(ecg_signal, 'db4', level=level)
    energies = [float(np.sum(c ** 2)) for c in coeffs]
    total_energy = sum(energies) or 1.0  # avoid division by zero

    entropy = -sum(
        (e / total_energy) * np.log(e / total_energy)
        for e in energies if e > 0
    )

    result = {"wavelet_entropy": float(entropy)}

    # Safe index access using negative indexing (from highest to lowest freq detail)
    detail_map = {"wavelet_d3_energy": -3, "wavelet_d4_energy": -4, "wavelet_d5_energy": -5}
    for col, idx in detail_map.items():
        if abs(idx) <= len(coeffs) - 1:  # -1 because index 0 is approximation
            result[col] = energies[idx]

    return result


# ─── STATISTICAL FEATURES ─────────────────────────────────────────────────────

def extract_statistical_features(ecg_signal):
    return {
        "signal_mean":     float(np.mean(ecg_signal)),
        "signal_std":      float(np.std(ecg_signal)),
        "signal_variance": float(np.var(ecg_signal)),
        "signal_skewness": float(stats.skew(ecg_signal)),
        "signal_kurtosis": float(stats.kurtosis(ecg_signal)),
    }


# ─── MAIN PIPELINE ────────────────────────────────────────────────────────────

def process_session(conn, session_id):
    logger.info(f"── Processing session {session_id}")

    raw_signal = read_ecg_signal(conn, session_id)
    logger.info(f"Raw signal length: {len(raw_signal)} samples ({len(raw_signal)/FS:.1f}s)")

    # Step 1 — preprocess
    filtered = preprocess_signal(raw_signal)

    # Step 2 — R-peaks
    peaks = detect_r_peaks(filtered)

    # Step 3 — RR intervals
    rr = compute_rr_intervals(peaks)

    # Signal quality gate — must pass before any feature extraction
    quality = evaluate_signal_quality(filtered, peaks, rr)
    logger.info(f"Signal quality: {quality['quality']} | Reason: {quality['reason']}")

    if quality["quality"] == "poor":
        logger.warning(f"Skipping feature extraction for session {session_id} — poor signal quality")
        mark_packets_processed(conn, session_id)  # mark so it doesn't loop forever
        conn.commit()
        return

    features = {}

    # Step 4 — RR count validation
    if len(rr) < 20:
        logger.warning(f"Insufficient data: only {len(rr)} RR intervals (need ≥20) — skipping advanced features")
        features.update({
            "mean_rr": float(np.mean(rr)) if len(rr) >= 1 else None,
            "mean_hr": float(60000.0 / np.mean(rr)) if len(rr) >= 1 else None,
            "extra_features": {"status": "insufficient_data", "rr_count": len(rr)},
        })
        features.update(extract_statistical_features(filtered))
        features.update(extract_wavelet_features(filtered))
        insert_features(conn, session_id, features)
        mark_packets_processed(conn, session_id)
        conn.commit()
        logger.info(f"Session {session_id} marked as insufficient_data")
        return
    else:
        # Step 5 — HRV
        features.update(extract_hrv_features(rr))
        # Step 6 — Frequency
        features.update(extract_frequency_features(rr))

    # Step 7 — Wavelet (always attempted on filtered signal)
    features.update(extract_wavelet_features(filtered))

    # Statistical
    features.update(extract_statistical_features(filtered))

    logger.info(f"Computed {len(features)} features for session {session_id}")

    insert_features(conn, session_id, features)
    mark_packets_processed(conn, session_id)
    conn.commit()

    logger.info(f"✔ Session {session_id} done")


def process_ecg_sessions():
    conn = get_connection()
    try:
        sessions = fetch_unprocessed_sessions(conn)
        logger.info(f"Found {len(sessions)} unprocessed session(s)")

        for session_id in sessions:
            try:
                process_session(conn, session_id)
            except Exception as e:
                conn.rollback()
                logger.error(f"✘ Session {session_id} failed: {e}")
                continue  # keep going with remaining sessions

    finally:
        conn.close()


if __name__ == "__main__":
    process_ecg_sessions()
