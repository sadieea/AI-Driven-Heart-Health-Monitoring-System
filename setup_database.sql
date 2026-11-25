-- Enable UUID extension
CREATE EXTENSION IF NOT EXISTS "uuid-ossp";

-- Create tables

CREATE TABLE ecg_recording_session (
    session_id            UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    patient_id            UUID,
    device_id             VARCHAR(50),
    lead_type              VARCHAR(20),
    sampling_rate_hz       INT NOT NULL,
    adc_resolution_bits    INT NOT NULL,
    adc_reference_mv       INT NOT NULL,
    start_time_utc         TIMESTAMPTZ NOT NULL,
    end_time_utc           TIMESTAMPTZ,
    recording_mode         VARCHAR(20),
    firmware_version       VARCHAR(20),
    created_at             TIMESTAMPTZ DEFAULT now()
);

CREATE TABLE ecg_raw_packet (
    packet_id             BIGSERIAL PRIMARY KEY,
    session_id             UUID NOT NULL,
    packet_timestamp_utc   TIMESTAMPTZ NOT NULL,
    packet_sequence_no     INT NOT NULL,
    samples_per_packet     INT NOT NULL,
    sample_values          BYTEA NOT NULL,
    packet_duration_ms     FLOAT,
    signal_quality_flag    VARCHAR(20),
    created_at             TIMESTAMPTZ DEFAULT now(),
    CONSTRAINT fk_session_packet
        FOREIGN KEY (session_id)
        REFERENCES ecg_recording_session(session_id)
        ON DELETE CASCADE
);

CREATE TABLE ecg_cycle (
    cycle_id              BIGSERIAL PRIMARY KEY,
    session_id             UUID NOT NULL,
    r_peak_sample_index    BIGINT NOT NULL,
    cycle_start_sample     BIGINT NOT NULL,
    cycle_end_sample       BIGINT NOT NULL,
    rr_interval_ms         FLOAT NOT NULL,
    estimated_bpm          FLOAT NOT NULL,
    created_at             TIMESTAMPTZ DEFAULT now(),
    CONSTRAINT fk_session_cycle
        FOREIGN KEY (session_id)
        REFERENCES ecg_recording_session(session_id)
        ON DELETE CASCADE
);

CREATE TABLE ecg_features (
    feature_id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    session_id UUID NOT NULL,
    cycle_id BIGINT NULL,
    mean_hr                DOUBLE PRECISION,
    mean_rr                DOUBLE PRECISION,
    sdnn                   DOUBLE PRECISION,
    rmssd                  DOUBLE PRECISION,
    nn50                   INTEGER,
    pnn50                  DOUBLE PRECISION,
    p_wave_duration_ms     DOUBLE PRECISION,
    pr_interval_ms         DOUBLE PRECISION,
    qrs_duration_ms        DOUBLE PRECISION,
    qt_interval_ms         DOUBLE PRECISION,
    qtc_interval_ms        DOUBLE PRECISION,
    r_peak_amplitude_mv    DOUBLE PRECISION,
    p_wave_amplitude_mv    DOUBLE PRECISION,
    t_wave_amplitude_mv    DOUBLE PRECISION,
    st_deviation_mv        DOUBLE PRECISION,
    st_slope               DOUBLE PRECISION,
    j_point_mv             DOUBLE PRECISION,
    vlf_power              DOUBLE PRECISION,
    lf_power               DOUBLE PRECISION,
    hf_power               DOUBLE PRECISION,
    lf_hf_ratio            DOUBLE PRECISION,
    total_power            DOUBLE PRECISION,
    spectral_entropy       DOUBLE PRECISION,
    wavelet_d3_energy      DOUBLE PRECISION,
    wavelet_d4_energy      DOUBLE PRECISION,
    wavelet_d5_energy      DOUBLE PRECISION,
    wavelet_entropy        DOUBLE PRECISION,
    signal_mean            DOUBLE PRECISION,
    signal_std             DOUBLE PRECISION,
    signal_variance        DOUBLE PRECISION,
    signal_skewness        DOUBLE PRECISION,
    signal_kurtosis        DOUBLE PRECISION,
    approximate_entropy    DOUBLE PRECISION,
    sample_entropy         DOUBLE PRECISION,
    shannon_entropy        DOUBLE PRECISION,
    hurst_exponent         DOUBLE PRECISION,
    dfa_alpha              DOUBLE PRECISION,
    classification_label   SMALLINT,
    classification_score   DOUBLE PRECISION,
    extra_features         JSONB,
    created_at_utc         TIMESTAMPTZ NOT NULL DEFAULT now(),
    CONSTRAINT fk_ecg_features_session
        FOREIGN KEY (session_id)
        REFERENCES ecg_recording_session(session_id)
        ON DELETE CASCADE,
    CONSTRAINT fk_ecg_features_cycle
        FOREIGN KEY (cycle_id)
        REFERENCES ecg_cycle(cycle_id)
        ON DELETE CASCADE
);

-- Indexes
CREATE INDEX idx_packet_session_time ON ecg_raw_packet (session_id, packet_timestamp_utc);
CREATE INDEX idx_cycle_session ON ecg_cycle (session_id);
CREATE INDEX idx_ecg_features_session ON ecg_features(session_id);
CREATE INDEX idx_ecg_features_cycle ON ecg_features(cycle_id);
CREATE INDEX idx_ecg_features_label ON ecg_features(classification_label);

-- Add column for features_extracted
ALTER TABLE ecg_raw_packet ADD COLUMN features_extracted BOOLEAN NOT NULL DEFAULT FALSE;

-- Index for unprocessed packets
CREATE INDEX idx_ecg_raw_packet_unprocessed ON ecg_raw_packet (features_extracted) WHERE features_extracted = FALSE;

-- Sample data inserts from the files

INSERT INTO ecg_recording_session (
    session_id,
    patient_id,
    device_id,
    lead_type,
    sampling_rate_hz,
    adc_resolution_bits,
    adc_reference_mv,
    start_time_utc,
    recording_mode,
    firmware_version
)
VALUES (
    '550e8400-e29b-41d4-a716-446655440000',
    '11111111-2222-3333-4444-555555555555',
    'ESP32_ECG_01',
    'single_lead',
    360,
    12,
    3300,
    '2026-01-28 10:00:00+00',
    'resting',
    'v1.0'
);

INSERT INTO ecg_raw_packet (
    session_id,
    packet_timestamp_utc,
    packet_sequence_no,
    samples_per_packet,
    sample_values,
    packet_duration_ms,
    signal_quality_flag
)
VALUES
(
 '550e8400-e29b-41d4-a716-446655440000',
 '2026-01-28 10:00:00+00',
 1,
 12,
 E'\\x0008100830088008F0084009200A400B400A300930081008',
 33.3,
 'good'
),
(
 '550e8400-e29b-41d4-a716-446655440000',
 '2026-01-28 10:00:00.033+00',
 2,
 12,
 E'\\x100820085008A008100A600B300C100BA009400820082008',
 33.3,
 'good'
);

INSERT INTO ecg_recording_session (
    session_id,
    patient_id,
    device_id,
    lead_type,
    sampling_rate_hz,
    adc_resolution_bits,
    adc_reference_mv,
    start_time_utc,
    recording_mode,
    firmware_version
)
VALUES (
    'a1b2c3d4-e001-4a11-9b01-111111111111',
    '11111111-2222-3333-4444-555555555555',
    'ESP32_ECG_01',
    'single_lead',
    360,
    12,
    3300,
    '2026-01-28 10:05:00+00',
    'walking',
    'v1.0'
);

INSERT INTO ecg_raw_packet (
    session_id,
    packet_timestamp_utc,
    packet_sequence_no,
    samples_per_packet,
    sample_values,
    packet_duration_ms,
    signal_quality_flag
)
VALUES
(
 'a1b2c3d4-e001-4a11-9b01-111111111111',
 '2026-01-28 10:05:00+00',
 1,
 12,
 E'\\x200840088008D008300A900B800C400CD009600830082008',
 33.3,
 'mild_motion_noise'
),
(
 'a1b2c3d4-e001-4a11-9b01-111111111111',
 '2026-01-28 10:05:00.033+00',
 2,
 12,
 E'\\x30086008A008F008500AB00BA00C700CF009800850085008',
 33.3,
 'mild_motion_noise'
);

INSERT INTO ecg_recording_session (
    session_id,
    patient_id,
    device_id,
    lead_type,
    sampling_rate_hz,
    adc_resolution_bits,
    adc_reference_mv,
    start_time_utc,
    recording_mode,
    firmware_version
)
VALUES (
    'b2c3d4e5-e002-4b22-9c02-222222222222',
    '11111111-2222-3333-4444-555555555555',
    'ESP32_ECG_01',
    'single_lead',
    360,
    12,
    3300,
    '2026-01-28 10:10:00+00',
    'running',
    'v1.0'
);

INSERT INTO ecg_raw_packet (
    session_id,
    packet_timestamp_utc,
    packet_sequence_no,
    samples_per_packet,
    sample_values,
    packet_duration_ms,
    signal_quality_flag
)
VALUES
(
 'b2c3d4e5-e002-4b22-9c02-222222222222',
 '2026-01-28 10:10:00+00',
 1,
 12,
 E'\\x5008A00810099009300B000DF00E200D900A400960082008',
 33.3,
 'high_motion_noise'
),
(
 'b2c3d4e5-e002-4b22-9c02-222222222222',
 '2026-01-28 10:10:00.033+00',
 2,
 12,
 E'\\x6008B0083009C009600B300D100F000DC00A800980088008',
 33.3,
 'high_motion_noise'
);

INSERT INTO ecg_cycle (
    session_id,
    r_peak_sample_index,
    cycle_start_sample,
    cycle_end_sample,
    rr_interval_ms,
    estimated_bpm
)
VALUES
(
    '550e8400-e29b-41d4-a716-446655440000',
    360,
    0,
    720,
    920.0,
    65.2
),
(
    '550e8400-e29b-41d4-a716-446655440000',
    720,
    360,
    1080,
    915.0,
    65.6
);

INSERT INTO ecg_cycle (
    session_id,
    r_peak_sample_index,
    cycle_start_sample,
    cycle_end_sample,
    rr_interval_ms,
    estimated_bpm
)
VALUES
(
    'a1b2c3d4-e001-4a11-9b01-111111111111',
    360,
    180,
    540,
    600.0,
    100.0
),
(
    'a1b2c3d4-e001-4a11-9b01-111111111111',
    576,
    396,
    756,
    600.0,
    100.0
);

INSERT INTO ecg_cycle (
    session_id,
    r_peak_sample_index,
    cycle_start_sample,
    cycle_end_sample,
    rr_interval_ms,
    estimated_bpm
)
VALUES
(
    'b2c3d4e5-e002-4b22-9c02-222222222222',
    288,
    144,
    432,
    400.0,
    150.0
),
(
    'b2c3d4e5-e002-4b22-9c02-222222222222',
    432,
    288,
    576,
    400.0,
    150.0
);