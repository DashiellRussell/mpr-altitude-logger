"""
Test metadata — rich descriptions, pass criteria, and context for TUI display.

Each test gets a dict with:
  desc:        What the test does (one sentence)
  criteria:    List of pass/fail criteria (what assertions must hold)
  config_keys: Related config.py values (shown with current value in detail panel)
  category:    Subsystem badge
  scenario:    Motor/mass/fault details for integration tests (optional)
  tags:        Freeform tags for filtering (optional)
"""

# ── Config Validation ────────────────────────────────────────

CONFIG_DEFAULTS = {
    "desc": "Validates that all default config.py values pass validation without errors",
    "criteria": [
        "config.validate() does not raise ValueError",
    ],
    "config_keys": ["KALMAN_Q_ALT", "KALMAN_Q_VEL", "KALMAN_R_ALT", "SAMPLE_RATE_HZ"],
    "category": "Config",
    "tags": ["smoke", "config"],
}

CONFIG_VALIDATION = {
    "test_negative_q_alt": {
        "desc": "Rejects negative Kalman altitude process noise",
        "criteria": [
            "config.validate() raises ValueError when KALMAN_Q_ALT = -1.0",
            "Error message contains 'KALMAN_Q_ALT'",
        ],
        "config_keys": ["KALMAN_Q_ALT"],
        "category": "Config",
    },
    "test_negative_q_vel": {
        "desc": "Rejects negative Kalman velocity process noise",
        "criteria": [
            "config.validate() raises ValueError when KALMAN_Q_VEL = -0.5",
            "Error message contains 'KALMAN_Q_VEL'",
        ],
        "config_keys": ["KALMAN_Q_VEL"],
        "category": "Config",
    },
    "test_negative_r_alt": {
        "desc": "Rejects zero/negative barometric measurement noise",
        "criteria": [
            "config.validate() raises ValueError when KALMAN_R_ALT = 0",
            "Error message contains 'KALMAN_R_ALT'",
        ],
        "config_keys": ["KALMAN_R_ALT"],
        "category": "Config",
    },
    "test_sample_rate_too_high": {
        "desc": "Rejects sample rate above 100 Hz (RP2040 can't keep up)",
        "criteria": [
            "config.validate() raises ValueError when SAMPLE_RATE_HZ = 200",
            "Error message contains 'SAMPLE_RATE_HZ'",
        ],
        "config_keys": ["SAMPLE_RATE_HZ"],
        "category": "Config",
    },
    "test_sample_rate_zero": {
        "desc": "Rejects zero sample rate (division by zero in timing)",
        "criteria": [
            "config.validate() raises ValueError when SAMPLE_RATE_HZ = 0",
        ],
        "config_keys": ["SAMPLE_RATE_HZ"],
        "category": "Config",
    },
    "test_pin_conflict_detected": {
        "desc": "Detects GPIO pin assignment conflicts (two peripherals on same pin)",
        "criteria": [
            "config.validate() raises ValueError when LED_PIN == I2C_SDA",
            "Error message contains 'Pin conflict'",
        ],
        "config_keys": ["LED_PIN", "I2C_SDA"],
        "category": "Config",
    },
}

# ── Kalman Filter ────────────────────────────────────────────

KALMAN_BASICS = {
    "test_initial_update_sets_state": {
        "desc": "First update with dt=0 initializes altitude to measurement, velocity to zero",
        "criteria": [
            "altitude == 100.0 (measurement value)",
            "velocity == 0.0 (no prior data to estimate velocity)",
        ],
        "config_keys": ["KALMAN_Q_ALT", "KALMAN_R_ALT"],
        "category": "Kalman",
        "tags": ["initialization"],
    },
    "test_dt_zero_noop": {
        "desc": "Zero time delta does not change filter state (avoid division by zero)",
        "criteria": [
            "altitude stays at 50.0 (reset value)",
            "velocity stays at 0.0",
        ],
        "config_keys": [],
        "category": "Kalman",
        "tags": ["edge-case"],
    },
    "test_dt_negative_noop": {
        "desc": "Negative time delta is treated as no-op (clock rollback protection)",
        "criteria": [
            "altitude stays at 50.0",
            "velocity stays at 0.0",
        ],
        "config_keys": [],
        "category": "Kalman",
        "tags": ["edge-case"],
    },
    "test_reset_clears_state": {
        "desc": "reset() zeroes both state variables and lets filter re-initialize",
        "criteria": [
            "kf.altitude == 0.0 after reset",
            "kf.velocity == 0.0 after reset",
        ],
        "config_keys": [],
        "category": "Kalman",
    },
}

KALMAN_CONVERGENCE = {
    "test_constant_altitude_convergence": {
        "desc": "Filter converges to steady-state measurement within 200 iterations",
        "criteria": [
            "|altitude - 100.0| < 0.5m after 200 updates at 25 Hz",
            "|velocity| < 0.5 m/s (should settle to zero)",
        ],
        "config_keys": ["KALMAN_Q_ALT", "KALMAN_Q_VEL", "KALMAN_R_ALT"],
        "category": "Kalman",
        "tags": ["convergence"],
    },
    "test_step_response_tracking": {
        "desc": "Filter tracks a sudden 0→100m step change in altitude",
        "criteria": [
            "|altitude - 100.0| < 1.0m after 200 updates post-step",
        ],
        "config_keys": ["KALMAN_Q_ALT", "KALMAN_R_ALT"],
        "category": "Kalman",
        "tags": ["convergence", "tracking"],
    },
    "test_simulated_flight_profile": {
        "desc": "Ascent→coast→descent profile produces physically sensible filter estimates",
        "criteria": [
            "altitude > 50m during ascent (tracking upward motion)",
            "velocity > 20 m/s during ascent (detecting climb)",
            "velocity < 20 m/s during coast (deceleration detected)",
            "velocity < 0 during descent (falling detected)",
        ],
        "config_keys": ["KALMAN_Q_ALT", "KALMAN_Q_VEL", "KALMAN_R_ALT"],
        "category": "Kalman",
        "tags": ["flight-profile"],
    },
}

KALMAN_STABILITY = {
    "test_negative_covariance_prevention": {
        "desc": "Extreme ±1e6 alternating inputs don't produce negative covariance",
        "criteria": [
            "p00 (altitude variance) >= 0 after 1000 extreme updates",
            "p11 (velocity variance) >= 0 after 1000 extreme updates",
        ],
        "config_keys": [],
        "category": "Kalman",
        "tags": ["numerical", "stability"],
    },
    "test_very_small_dt": {
        "desc": "Tiny time delta (1e-8) doesn't cause overflow or NaN",
        "criteria": [
            "|altitude| < 1e10 (no overflow)",
            "|velocity| < 1e10 (no overflow)",
        ],
        "config_keys": [],
        "category": "Kalman",
        "tags": ["numerical", "edge-case"],
    },
}

# ── Data Logger ──────────────────────────────────────────────

FRAME_PACKING = {
    "test_pack_into_matches_pack": {
        "desc": "Pre-allocated buffer packing (pack_into) produces identical bytes to struct.pack",
        "criteria": [
            "bytes(buf) == FRAME_HEADER + struct.pack(FRAME_FORMAT, *args)",
            "Sync header 0xAA55 present at offset 0",
        ],
        "config_keys": [],
        "category": "DataLog",
        "tags": ["binary-format"],
    },
    "test_frame_size_correct": {
        "desc": "Frame data payload is exactly 32 bytes (documented wire format)",
        "criteria": [
            "FRAME_SIZE == 32",
        ],
        "config_keys": [],
        "category": "DataLog",
        "tags": ["binary-format"],
    },
    "test_error_flags_encoding": {
        "desc": "Error flag bit (0x08) is correctly encoded in the last byte of the frame",
        "criteria": [
            "buf[-1] == 0x08 when flags=0x08",
        ],
        "config_keys": [],
        "category": "DataLog",
        "tags": ["binary-format", "error-handling"],
    },
}

MKDIR_HANDLING = {
    "test_dir_exists_true_for_existing": {
        "desc": "Filesystem helper correctly identifies existing directories",
        "criteria": ["_dir_exists(tmpdir) returns True"],
        "config_keys": [],
        "category": "DataLog",
    },
    "test_dir_exists_false_for_missing": {
        "desc": "Filesystem helper correctly identifies missing directories",
        "criteria": ["_dir_exists(nonexistent) returns False"],
        "config_keys": [],
        "category": "DataLog",
    },
    "test_file_exists_helper": {
        "desc": "Filesystem helper correctly identifies file existence",
        "criteria": [
            "_file_exists(existing_file) returns True",
            "_file_exists(nonexistent) returns False",
        ],
        "config_keys": [],
        "category": "DataLog",
    },
}

WRITE_RETRY = {
    "test_logger_init_preallocates_buffer": {
        "desc": "FlightLogger pre-allocates a write buffer with sync header to avoid hot-loop allocation",
        "criteria": [
            "len(logger._write_buf) == 2 + FRAME_SIZE (34 bytes)",
            "buf[0] == 0xAA (sync byte 1)",
            "buf[1] == 0x55 (sync byte 2)",
        ],
        "config_keys": [],
        "category": "DataLog",
        "tags": ["performance", "initialization"],
    },
    "test_sd_failed_flag_starts_false": {
        "desc": "SD failure flag initializes to False (assume card is working until proven otherwise)",
        "criteria": ["logger.sd_failed is False"],
        "config_keys": [],
        "category": "DataLog",
    },
}

# ── State Machine ────────────────────────────────────────────

FULL_FLIGHT_SEQ = {
    "test_complete_flight": {
        "desc": "Manually drives FSM through all 7 states with synthetic inputs",
        "criteria": [
            "PAD at startup (alt=0, vel=0)",
            "PAD→BOOST: alt=20m, vel=15 m/s sustained >500ms",
            "BOOST→COAST: velocity drop >5 m/s from peak (max_vel=100, current=80)",
            "COAST→APOGEE: velocity <2 m/s for APOGEE_CONFIRM_COUNT frames",
            "APOGEE→DROGUE: after APOGEE_DWELL_FRAMES",
            "DROGUE→MAIN: altitude < MAIN_CHUTE_FRACTION × max AGL",
            "MAIN→LANDED: |vel| < 0.5 m/s for LANDED_CONFIRM_SECONDS",
        ],
        "config_keys": [
            "LAUNCH_ALT_THRESHOLD", "LAUNCH_VEL_THRESHOLD", "LAUNCH_DETECT_WINDOW",
            "COAST_VEL_THRESHOLD", "APOGEE_VEL_THRESHOLD", "APOGEE_CONFIRM_COUNT",
            "APOGEE_DWELL_FRAMES", "MAIN_CHUTE_FRACTION",
            "LANDED_VEL_THRESHOLD", "LANDED_CONFIRM_SECONDS",
        ],
        "category": "StateMachine",
        "tags": ["full-sequence"],
    },
}

APOGEE_DWELL = {
    "test_stays_in_apogee_during_dwell": {
        "desc": "APOGEE holds for exactly APOGEE_DWELL_FRAMES before transitioning to DROGUE",
        "criteria": [
            "State == APOGEE for dwell-1 frames",
            "State == DROGUE on the dwell-th frame",
        ],
        "config_keys": ["APOGEE_DWELL_FRAMES"],
        "category": "StateMachine",
    },
}

BOOST_RECOVERY = {
    "test_single_frame_below_no_reset": {
        "desc": "Single frame below recovery altitude doesn't reset BOOST (prevents jitter-induced resets)",
        "criteria": [
            "State stays BOOST after one frame with alt=5m, vel=2 m/s",
        ],
        "config_keys": ["BOOST_RECOVERY_ALT", "BOOST_RECOVERY_COUNT"],
        "category": "StateMachine",
        "tags": ["robustness"],
    },
    "test_sustained_below_resets_to_pad": {
        "desc": "BOOST_RECOVERY_COUNT consecutive frames below threshold resets to PAD (false launch recovery)",
        "criteria": [
            "State == PAD after BOOST_RECOVERY_COUNT frames at alt=5m",
        ],
        "config_keys": ["BOOST_RECOVERY_ALT", "BOOST_RECOVERY_COUNT", "BOOST_RECOVERY_WINDOW"],
        "category": "StateMachine",
    },
    "test_intermittent_above_resets_counter": {
        "desc": "Recovery counter resets when altitude briefly goes above threshold",
        "criteria": [
            "State stays BOOST when below-above-below pattern has fewer consecutive lows than threshold",
        ],
        "config_keys": ["BOOST_RECOVERY_COUNT"],
        "category": "StateMachine",
        "tags": ["robustness"],
    },
}

COAST_TIMEOUT_META = {
    "test_coast_timeout_forces_apogee": {
        "desc": "If stuck in COAST for COAST_TIMEOUT seconds (e.g. sensor issue), force transition to APOGEE",
        "criteria": [
            "State == APOGEE when time exceeds COAST_TIMEOUT despite velocity > apogee threshold",
        ],
        "config_keys": ["COAST_TIMEOUT"],
        "category": "StateMachine",
        "tags": ["timeout", "safety"],
    },
}

LANDING_DETECTION = {
    "test_landing_requires_sustained_zero_vel": {
        "desc": "LANDED requires near-zero velocity sustained for LANDED_CONFIRM_SECONDS (not just one frame)",
        "criteria": [
            "State stays MAIN for 5 frames of low velocity (not enough)",
            "State transitions to LANDED after LANDED_CONFIRM_SECONDS of |vel| < 0.5 m/s",
        ],
        "config_keys": ["LANDED_VEL_THRESHOLD", "LANDED_CONFIRM_SECONDS"],
        "category": "StateMachine",
    },
    "test_velocity_spike_resets_counter": {
        "desc": "A velocity spike during near-landing resets the confirmation counter",
        "criteria": [
            "Almost enough quiet time, then spike → counter resets",
            "State stays MAIN (not LANDED) after spike + 1 frame",
        ],
        "config_keys": ["LANDED_VEL_THRESHOLD", "LANDED_CONFIRM_SECONDS"],
        "category": "StateMachine",
        "tags": ["robustness"],
    },
}

# ── Integration Tests ────────────────────────────────────────

NORMAL_FLIGHT = {
    "test_h100_all_states_in_order": {
        "desc": "H100 motor through full pipeline — all 7 states in exact order",
        "criteria": [
            "states_visited == [PAD, BOOST, COAST, APOGEE, DROGUE, MAIN, LANDED]",
        ],
        "config_keys": ["LAUNCH_ALT_THRESHOLD", "LAUNCH_VEL_THRESHOLD"],
        "category": "Integration",
        "scenario": {"motor": "Cesaroni_H100", "mass": "2.5 kg", "cd": "0.45", "diameter": "54mm"},
    },
    "test_h100_reasonable_apogee": {
        "desc": "H100 apogee is within physically expected range (100-800m)",
        "criteria": [
            "100m < max_altitude < 800m",
        ],
        "config_keys": [],
        "category": "Integration",
        "scenario": {"motor": "Cesaroni_H100", "mass": "2.5 kg"},
    },
    "test_g40_all_states": {
        "desc": "Smaller G40 motor still completes all major flight phases",
        "criteria": [
            "Reached BOOST (launch detected)",
            "Reached APOGEE (apogee detected)",
            "Reached LANDED (landing confirmed)",
        ],
        "config_keys": [],
        "category": "Integration",
        "scenario": {"motor": "Cesaroni_G40", "mass": "1.5 kg", "diameter": "54mm"},
    },
    "test_i218_all_states": {
        "desc": "Higher-impulse I218 motor — all states in correct order",
        "criteria": [
            "states_visited == [PAD, BOOST, COAST, APOGEE, DROGUE, MAIN, LANDED]",
        ],
        "config_keys": [],
        "category": "Integration",
        "scenario": {"motor": "Cesaroni_I218", "mass": "3.0 kg", "diameter": "54mm"},
    },
    "test_e12_all_states": {
        "desc": "Mid-range E12 motor — reaches at least BOOST and APOGEE",
        "criteria": ["Reached BOOST", "Reached APOGEE"],
        "config_keys": [],
        "category": "Integration",
        "scenario": {"motor": "Estes_E12", "mass": "0.5 kg", "diameter": "29mm"},
    },
    "test_f32_all_states": {
        "desc": "Fast-burn F32 — at least detects BOOST",
        "criteria": ["Reached BOOST"],
        "config_keys": [],
        "category": "Integration",
        "scenario": {"motor": "Cesaroni_F32", "mass": "1.0 kg", "diameter": "38mm"},
    },
    "test_flight_has_positive_max_velocity": {
        "desc": "Peak velocity exceeds 10 m/s during flight (sanity check on Kalman)",
        "criteria": ["max_velocity > 10.0 m/s"],
        "config_keys": [],
        "category": "Integration",
        "scenario": {"motor": "Cesaroni_H100"},
    },
    "test_flight_duration_reasonable": {
        "desc": "Total flight duration is between 5-300 seconds",
        "criteria": ["5.0 < flight_duration_s < 300.0"],
        "config_keys": [],
        "category": "Integration",
        "scenario": {"motor": "Cesaroni_H100"},
    },
    "test_no_error_frames_in_clean_flight": {
        "desc": "Clean flight with no faults produces zero error frames",
        "criteria": ["len(error_frames()) == 0"],
        "config_keys": [],
        "category": "Integration",
        "scenario": {"motor": "Cesaroni_H100"},
    },
    "test_transitions_have_timestamps": {
        "desc": "Every state transition has a valid timestamp and distinct from/to states",
        "criteria": [
            "len(transitions) > 0",
            "All timestamps >= 0",
            "from_state != to_state for every transition",
        ],
        "config_keys": [],
        "category": "Integration",
    },
    "test_all_altitudes_finite": {
        "desc": "All frames have finite (non-NaN, non-Inf) altitude and velocity",
        "criteria": [
            "math.isfinite(alt_filtered) for every frame",
            "math.isfinite(vel_filtered) for every frame",
        ],
        "config_keys": [],
        "category": "Integration",
        "tags": ["numerical"],
    },
    "test_state_at_helper": {
        "desc": "state_at() helper returns PAD at t=0 and LANDED at flight end",
        "criteria": [
            "state_at(0.0) == PAD",
            "state_at(flight_duration_s) == LANDED",
        ],
        "config_keys": [],
        "category": "Integration",
    },
}

IDEAL_FLIGHT = {
    "test_ideal_h100_full_sequence": {
        "desc": "Clean H100 — all states, zero errors, perfect conditions",
        "criteria": [
            "states_visited == [PAD..LANDED]",
            "error_frames() == 0",
        ],
        "config_keys": [],
        "category": "Integration",
        "scenario": {"motor": "Cesaroni_H100", "mass": "2.5 kg"},
    },
    "test_ideal_i218_full_sequence": {
        "desc": "Clean I218 — all states, zero errors",
        "criteria": [
            "states_visited == [PAD..LANDED]",
            "error_frames() == 0",
        ],
        "config_keys": [],
        "category": "Integration",
        "scenario": {"motor": "Cesaroni_I218", "mass": "3.0 kg"},
    },
    "test_ideal_apogee_matches_sim": {
        "desc": "Ideal flight apogee is deterministic — two runs produce identical max altitude",
        "criteria": [
            "|run1.max_altitude - run2.max_altitude| < 0.01m",
        ],
        "config_keys": [],
        "category": "Integration",
        "tags": ["determinism"],
    },
    "test_ideal_monotonic_altitude_during_boost": {
        "desc": "During BOOST phase, filtered altitude increases monotonically (no dips)",
        "criteria": [
            "Each BOOST frame altitude >= previous - 0.5m",
        ],
        "config_keys": ["KALMAN_Q_ALT", "KALMAN_R_ALT"],
        "category": "Integration",
        "tags": ["kalman-quality"],
    },
}

NOISY_FLIGHT = {
    "test_moderate_noise_all_states": {
        "desc": "100 Pa barometer noise — Kalman filter still detects all major states",
        "criteria": [
            "Reached BOOST", "Reached APOGEE", "Reached MAIN",
        ],
        "config_keys": ["KALMAN_Q_ALT", "KALMAN_R_ALT"],
        "category": "Integration",
        "scenario": {"motor": "Cesaroni_H100", "noise": "100 Pa σ"},
        "tags": ["robustness", "kalman-quality"],
    },
    "test_pad_noise_no_false_trigger": {
        "desc": "200 Pa noise on pad (extreme) — must NOT false-trigger launch",
        "criteria": [
            "All 500 frames remain in PAD state",
        ],
        "config_keys": ["LAUNCH_ALT_THRESHOLD", "LAUNCH_VEL_THRESHOLD", "LAUNCH_DETECT_WINDOW"],
        "category": "Integration",
        "scenario": {"noise": "200 Pa σ", "duration": "500 frames on pad"},
        "tags": ["safety", "false-positive"],
    },
    "test_light_noise_completes_descent": {
        "desc": "20 Pa noise (realistic) — completes through MAIN descent",
        "criteria": ["Reached MAIN"],
        "config_keys": [],
        "category": "Integration",
        "scenario": {"motor": "Cesaroni_H100", "noise": "20 Pa σ"},
    },
    "test_noise_with_temperature_variation": {
        "desc": "Noise on both pressure (80 Pa) and temperature (2°C) channels",
        "criteria": ["Reached BOOST", "Reached APOGEE"],
        "config_keys": [],
        "category": "Integration",
        "scenario": {"motor": "Cesaroni_H100", "noise": "80 Pa + 2°C σ"},
    },
}

ANGLED_FLIGHT = {
    "test_angled_70pct_still_detects_states": {
        "desc": "Rocket tips to 70% effective altitude — still detects major states",
        "criteria": ["Reached BOOST", "Reached APOGEE"],
        "config_keys": [],
        "category": "Integration",
        "scenario": {"motor": "Cesaroni_H100", "effective_fraction": "70%"},
    },
    "test_angled_50pct_lower_apogee": {
        "desc": "50% effective altitude produces lower apogee than nominal flight",
        "criteria": [
            "angled.max_altitude < nominal.max_altitude",
        ],
        "config_keys": [],
        "category": "Integration",
        "scenario": {"motor": "Cesaroni_H100", "effective_fraction": "50%"},
    },
    "test_angled_severe_still_no_crash": {
        "desc": "Even 30% effective altitude doesn't crash the pipeline",
        "criteria": [
            "len(frames) > 0 (pipeline completed)",
            "Reached BOOST",
        ],
        "config_keys": [],
        "category": "Integration",
        "scenario": {"motor": "Cesaroni_H100", "effective_fraction": "30%"},
        "tags": ["edge-case"],
    },
}

FALSE_LAUNCH = {
    "test_brief_altitude_spike_recovers_to_pad": {
        "desc": "Brief pressure drop (~19m spike) then back to ground → recovers to PAD",
        "criteria": [
            "Final state == PAD (not stuck in BOOST)",
        ],
        "config_keys": ["BOOST_RECOVERY_ALT", "BOOST_RECOVERY_COUNT", "BOOST_RECOVERY_WINDOW"],
        "category": "Integration",
        "scenario": {"type": "pressure sequence", "spike": "~19m for 15 frames"},
        "tags": ["safety", "false-positive"],
    },
    "test_walking_upstairs_no_launch": {
        "desc": "Walking up stairs (~3m slow altitude gain) stays on PAD",
        "criteria": [
            "Final state == PAD",
        ],
        "config_keys": ["LAUNCH_ALT_THRESHOLD", "LAUNCH_VEL_THRESHOLD"],
        "category": "Integration",
        "scenario": {"type": "pressure sequence", "altitude_gain": "~3m over 75 frames"},
        "tags": ["safety", "false-positive"],
    },
}

SENSOR_DROPOUT = {
    "test_dropout_during_coast_produces_error_frames": {
        "desc": "10 frames of SENSOR_FAULT during coast produce exactly 10 error frames",
        "criteria": ["len(error_frames) == 10"],
        "config_keys": [],
        "category": "Integration",
        "scenario": {"motor": "Cesaroni_H100", "fault": "10-frame dropout at 1/3 flight"},
    },
    "test_system_recovers_after_dropout": {
        "desc": "System recovers after sensor dropout and reaches LANDED",
        "criteria": ["Reached LANDED"],
        "config_keys": [],
        "category": "Integration",
        "scenario": {"motor": "Cesaroni_H100", "fault": "10-frame dropout at 1/4 flight"},
    },
    "test_dropout_frames_have_error_flag": {
        "desc": "Error frames have flags byte set to 0x08 (bit 3 = error)",
        "criteria": ["f['flags'] == 0x08 for every error frame"],
        "config_keys": [],
        "category": "Integration",
        "tags": ["binary-format"],
    },
    "test_dropout_on_pad_stays_on_pad": {
        "desc": "Sensor dropout while on pad doesn't trigger any state change",
        "criteria": ["All frames have state == PAD"],
        "config_keys": [],
        "category": "Integration",
        "scenario": {"type": "constant pad", "fault": "20-frame dropout at frame 50"},
        "tags": ["safety"],
    },
}

MULTI_DROPOUT = {
    "test_three_dropout_windows": {
        "desc": "Three separate dropout windows produce correct total error count (8+12+5=25)",
        "criteria": ["len(error_frames) == 25"],
        "config_keys": [],
        "category": "Integration",
        "scenario": {"motor": "Cesaroni_H100", "fault": "3 dropout windows: 8, 12, 5 frames"},
    },
    "test_multiple_dropouts_still_lands": {
        "desc": "Flight completes despite three separate 5-frame dropouts",
        "criteria": ["Reached LANDED"],
        "config_keys": [],
        "category": "Integration",
        "scenario": {"motor": "Cesaroni_H100", "fault": "3 × 5-frame dropouts"},
    },
    "test_long_dropout_during_coast": {
        "desc": "25 frames (1 second) of dropout during coast — system survives",
        "criteria": [
            "len(frames) == len(base) (no frames lost)",
            "len(error_frames) == 25",
        ],
        "config_keys": [],
        "category": "Integration",
        "scenario": {"motor": "Cesaroni_H100", "fault": "25-frame dropout at 1/3 flight"},
    },
}

PRESSURE_SPIKE = {
    "test_spike_at_coast_does_not_skip_apogee": {
        "desc": "Single-frame 50 kPa pressure glitch during coast doesn't skip APOGEE",
        "criteria": ["Reached APOGEE"],
        "config_keys": [],
        "category": "Integration",
        "scenario": {"motor": "Cesaroni_H100", "fault": "50 kPa spike at mid-flight"},
        "tags": ["kalman-quality"],
    },
    "test_spike_on_pad_no_false_launch": {
        "desc": "Pressure spike on pad doesn't trigger launch",
        "criteria": ["Final state == PAD"],
        "config_keys": ["LAUNCH_ALT_THRESHOLD", "LAUNCH_VEL_THRESHOLD"],
        "category": "Integration",
        "scenario": {"type": "constant pad", "fault": "50 kPa spike at frame 100"},
        "tags": ["safety"],
    },
    "test_multi_frame_spike_during_boost": {
        "desc": "5-frame 90 kPa pressure spike during boost doesn't prevent reaching APOGEE",
        "criteria": ["Reached APOGEE"],
        "config_keys": [],
        "category": "Integration",
        "scenario": {"motor": "Cesaroni_H100", "fault": "90 kPa spike × 5 frames at frame 80"},
    },
    "test_negative_pressure_spike": {
        "desc": "Impossibly low pressure (100 Pa) is handled gracefully — no NaN/Inf",
        "criteria": ["math.isfinite(alt_filtered) for every frame"],
        "config_keys": [],
        "category": "Integration",
        "scenario": {"motor": "Cesaroni_H100", "fault": "100 Pa spike at frame 100"},
        "tags": ["numerical"],
    },
}

HIGH_ALTITUDE = {
    "test_j350_no_numerical_issues": {
        "desc": "J350 high-altitude flight — all values remain finite (no overflow)",
        "criteria": [
            "math.isfinite(alt_filtered) for every frame",
            "math.isfinite(vel_filtered) for every frame",
            "math.isfinite(pressure_pa) for every non-error frame",
        ],
        "config_keys": [],
        "category": "Integration",
        "scenario": {"motor": "Aerotech_J350", "mass": "5.0 kg", "diameter": "75mm"},
        "tags": ["numerical"],
    },
    "test_j350_all_states": {
        "desc": "J350 reaches BOOST, APOGEE, and LANDED",
        "criteria": ["Reached BOOST", "Reached APOGEE", "Reached LANDED"],
        "config_keys": [],
        "category": "Integration",
        "scenario": {"motor": "Aerotech_J350", "mass": "5.0 kg", "diameter": "75mm"},
    },
    "test_j350_higher_apogee_than_h100": {
        "desc": "J350 reaches significantly higher apogee than H100 (more impulse)",
        "criteria": ["j350.max_altitude > h100.max_altitude"],
        "config_keys": [],
        "category": "Integration",
        "scenario": {"compare": "J350 vs H100"},
    },
}

SHORT_FLIGHT = {
    "test_d12_at_least_boost": {
        "desc": "D12 on 0.3 kg rocket — at least detects launch",
        "criteria": ["Reached BOOST"],
        "config_keys": ["LAUNCH_ALT_THRESHOLD", "LAUNCH_VEL_THRESHOLD"],
        "category": "Integration",
        "scenario": {"motor": "Estes_D12", "mass": "0.3 kg", "diameter": "25mm"},
    },
    "test_d12_no_crash": {
        "desc": "Minimal D12 flight produces valid finite-valued frames",
        "criteria": [
            "len(frames) > 0",
            "math.isfinite(alt_filtered) for every frame",
        ],
        "config_keys": [],
        "category": "Integration",
        "scenario": {"motor": "Estes_D12", "mass": "0.3 kg", "diameter": "25mm"},
    },
}

BELOW_GROUND = {
    "test_negative_agl_no_crash": {
        "desc": "Landing ~50m below launch altitude (valley) — doesn't crash, produces negative AGL",
        "criteria": [
            "len(frames) > 0",
            "min(alt_filtered) < 0 (negative AGL confirmed)",
        ],
        "config_keys": [],
        "category": "Integration",
        "scenario": {"motor": "Cesaroni_H100", "valley": "~50m below (600 Pa)"},
        "tags": ["edge-case"],
    },
    "test_negative_agl_all_values_finite": {
        "desc": "All values finite even with negative altitude",
        "criteria": [
            "math.isfinite(alt_filtered) for every frame",
            "math.isfinite(vel_filtered) for every frame",
        ],
        "config_keys": [],
        "category": "Integration",
        "tags": ["numerical"],
    },
    "test_shallow_valley_still_detects_states": {
        "desc": "10m valley landing — still detects BOOST and APOGEE",
        "criteria": ["Reached BOOST", "Reached APOGEE"],
        "config_keys": [],
        "category": "Integration",
        "scenario": {"motor": "Cesaroni_H100", "valley": "~10m below (120 Pa)"},
    },
}

WIND_GUST = {
    "test_slow_drift_no_false_launch": {
        "desc": "Slow barometric drift (~5m over 400 frames) on pad — no false launch",
        "criteria": ["Final state == PAD"],
        "config_keys": ["LAUNCH_ALT_THRESHOLD", "LAUNCH_VEL_THRESHOLD", "LAUNCH_DETECT_WINDOW"],
        "category": "Integration",
        "scenario": {"type": "gradual drift", "rate": "0.8 Pa/frame"},
        "tags": ["safety", "false-positive"],
    },
    "test_rapid_drift_may_trigger": {
        "desc": "Rapid drift (5 Pa/frame) — verifies drift mechanism is working",
        "criteria": ["len(frames) == 500 (completed without crash)"],
        "config_keys": [],
        "category": "Integration",
        "scenario": {"type": "gradual drift", "rate": "5.0 Pa/frame"},
    },
}

STUCK_SENSOR = {
    "test_stuck_during_coast_no_crash": {
        "desc": "100 frames of frozen barometer during coast — pipeline doesn't crash",
        "criteria": ["len(frames) == len(base) (all frames produced)"],
        "config_keys": [],
        "category": "Integration",
        "scenario": {"motor": "Cesaroni_H100", "fault": "100 frozen frames at 1/3 flight"},
    },
    "test_stuck_produces_frames": {
        "desc": "All frames produced even with stuck sensor (no frames dropped)",
        "criteria": ["len(frames) == len(base)"],
        "config_keys": [],
        "category": "Integration",
    },
    "test_stuck_on_pad_stays_on_pad": {
        "desc": "Frozen readings on pad don't trigger launch",
        "criteria": ["All frames have state == PAD"],
        "config_keys": [],
        "category": "Integration",
        "scenario": {"type": "constant pad", "fault": "200 frozen frames at frame 50"},
        "tags": ["safety"],
    },
}

TEMPERATURE = {
    "test_temperature_ramp_no_crash": {
        "desc": "Rising temperature (+0.05°C/frame) during flight doesn't crash pipeline",
        "criteria": [
            "len(frames) > 0",
            "Reached BOOST",
        ],
        "config_keys": [],
        "category": "Integration",
        "scenario": {"motor": "Cesaroni_H100", "temp_ramp": "+0.05°C/frame"},
    },
    "test_temperature_ramp_logged_correctly": {
        "desc": "Temperature values increase over time in logged frames",
        "criteria": [
            "Last frame temperature > first frame temperature",
        ],
        "config_keys": [],
        "category": "Integration",
        "scenario": {"motor": "Cesaroni_H100", "temp_ramp": "+0.1°C/frame"},
    },
    "test_negative_temperature_ramp": {
        "desc": "Cooling temperature (-0.03°C/frame) works correctly",
        "criteria": [
            "len(frames) > 0",
            "Reached BOOST",
        ],
        "config_keys": [],
        "category": "Integration",
        "scenario": {"motor": "Cesaroni_H100", "temp_ramp": "-0.03°C/frame"},
    },
}

BROWNOUT = {
    "test_low_voltage_appears_in_output": {
        "desc": "Injected low voltage values (2.8/4.2/7.5V) appear correctly in frame data",
        "criteria": [
            "v_3v3_mv == 2800 for all non-error frames",
            "v_5v_mv == 4200 for all non-error frames",
            "v_9v_mv == 7500 for all non-error frames",
        ],
        "config_keys": [],
        "category": "Integration",
        "scenario": {"motor": "Cesaroni_H100", "voltages": "2.8V / 4.2V / 7.5V"},
    },
    "test_voltage_ramp_down": {
        "desc": "Gradually decreasing voltage (battery drain) is logged correctly",
        "criteria": [
            "Last frame v_3v3_mv <= first frame v_3v3_mv",
        ],
        "config_keys": [],
        "category": "Integration",
        "scenario": {"motor": "Cesaroni_H100", "voltages": "draining from nominal"},
    },
    "test_default_voltage_is_nominal": {
        "desc": "Without custom voltage provider, voltages are nominal (3.3/5.0/9.0V)",
        "criteria": [
            "v_3v3_mv == 3300",
            "v_5v_mv == 5000",
            "v_9v_mv == 9000",
        ],
        "config_keys": [],
        "category": "Integration",
    },
}

EXTENDED_PAD = {
    "test_10_minute_pad_then_flight": {
        "desc": "10 minutes idle (15000 frames) then H100 flight — system still works",
        "criteria": [
            "Reached BOOST (launch detected after long idle)",
            "Reached APOGEE",
        ],
        "config_keys": [],
        "category": "Integration",
        "scenario": {"motor": "Cesaroni_H100", "pad_wait": "15000 frames (~10 min)"},
    },
    "test_extended_pad_no_spurious_transitions": {
        "desc": "Long pad wait produces only PAD state, zero transitions",
        "criteria": [
            "states_visited == [PAD]",
            "len(transitions) == 0",
        ],
        "config_keys": ["LAUNCH_ALT_THRESHOLD", "LAUNCH_VEL_THRESHOLD"],
        "category": "Integration",
        "scenario": {"type": "constant pad", "duration": "5000 frames"},
        "tags": ["safety"],
    },
}

SD_WRITE_FAILURE = {
    "test_bin_write_creates_valid_file": {
        "desc": "write_bin=True creates a .bin file that decode_log.py can parse",
        "criteria": [
            "bin_path exists and size > 0",
            "decode_file(bin_path) returns > 0 frames",
        ],
        "config_keys": [],
        "category": "Integration",
        "scenario": {"motor": "Cesaroni_H100", "write_bin": True},
        "tags": ["binary-format", "round-trip"],
    },
    "test_bin_with_sensor_faults_includes_error_frames": {
        "desc": "Binary file includes error frames from sensor faults (flags=0x08)",
        "criteria": [
            "5 decoded frames have flags & 0x08 set",
        ],
        "config_keys": [],
        "category": "Integration",
        "tags": ["binary-format", "error-handling"],
    },
    "test_bin_with_multiple_faults": {
        "desc": "Binary file with two fault windows includes all fault frames (3+4=7)",
        "criteria": [
            "7 decoded frames have flags & 0x08 set",
        ],
        "config_keys": [],
        "category": "Integration",
        "tags": ["binary-format"],
    },
}

SD_FULL = {
    "test_sd_full_sets_failed_flag": {
        "desc": "SD card write creates valid file (pre-failure verification)",
        "criteria": [
            "bin_path exists",
            "File size > 0",
        ],
        "config_keys": [],
        "category": "Integration",
        "tags": ["sd-card"],
    },
    "test_sd_full_mid_write_truncates_cleanly": {
        "desc": "Decoder handles truncated binary file (simulates SD full mid-flight)",
        "criteria": [
            "decode_file succeeds on 50%-truncated file",
            "Decoded frame count > 0 but < full frame count",
        ],
        "config_keys": [],
        "category": "Integration",
        "tags": ["sd-card", "robustness"],
    },
    "test_sd_full_partial_frame_ignored": {
        "desc": "Partial frame at end of file (power loss mid-write) is skipped cleanly",
        "criteria": [
            "decode_file succeeds with 10 bytes chopped off end",
            "Decoded frames > 0",
        ],
        "config_keys": [],
        "category": "Integration",
        "tags": ["sd-card", "robustness"],
    },
    "test_sd_capacity_calculation": {
        "desc": "8 GB SD card can store 2600+ hours at 25 Hz (34 bytes/frame × 25 fps)",
        "criteria": [
            "Calculated hours > 2600",
            "Calculated hours < 3000 (sanity upper bound)",
        ],
        "config_keys": ["SAMPLE_RATE_HZ"],
        "category": "Integration",
        "tags": ["sd-card"],
    },
    "test_logger_sd_failed_flag_behavior": {
        "desc": "When sd_failed is set, write_frame is a no-op (frames silently dropped)",
        "criteria": [
            "frames_written == 10 after writing 10 + 10 (with failure set after first 10)",
            "decode_file finds 10 (or 9) frames",
        ],
        "config_keys": [],
        "category": "Integration",
        "tags": ["sd-card", "error-handling"],
    },
}

MARGINAL_LAUNCH = {
    "test_heavy_rocket_low_motor": {
        "desc": "Heavy rocket on D12 — may barely launch but doesn't crash",
        "criteria": ["len(frames) > 0"],
        "config_keys": [],
        "category": "Integration",
        "scenario": {"motor": "Estes_D12", "mass": "0.5 kg", "diameter": "38mm"},
        "tags": ["edge-case"],
    },
    "test_high_drag_reduces_apogee": {
        "desc": "High Cd (1.2) produces lower apogee than nominal (0.45)",
        "criteria": ["draggy.max_altitude < nominal.max_altitude"],
        "config_keys": [],
        "category": "Integration",
        "scenario": {"motor": "Cesaroni_H100", "cd_nominal": "0.45", "cd_high": "1.2"},
    },
}

ROUND_TRIP = {
    "test_roundtrip_frame_count": {
        "desc": "Binary encode→decode round-trip preserves frame count (±1 tolerance)",
        "criteria": [
            "n_decoded == n_sim or n_decoded == n_sim - 1",
        ],
        "config_keys": [],
        "category": "Integration",
        "scenario": {"motor": "Cesaroni_H100", "write_bin": True},
        "tags": ["binary-format", "round-trip"],
    },
    "test_roundtrip_field_values": {
        "desc": "Decoded field values match simulation within float32 precision (0.5m)",
        "criteria": [
            "State matches exactly at 4 sample points",
            "|alt_filtered| difference < 0.5m",
            "|vel_filtered| difference < 0.5 m/s",
            "v_3v3_mv matches exactly",
        ],
        "config_keys": [],
        "category": "Integration",
        "tags": ["binary-format", "round-trip"],
    },
    "test_roundtrip_states_match": {
        "desc": "State sequence in decoded binary matches simulation exactly",
        "criteria": [
            "sim_states[:n] == dec_states (state-for-state match)",
        ],
        "config_keys": [],
        "category": "Integration",
        "tags": ["binary-format", "round-trip"],
    },
}


# ── Unified lookup ───────────────────────────────────────────

def _merge(*dicts):
    out = {}
    for d in dicts:
        out.update(d)
    return out


ALL = _merge(
    {"test_default_config_passes": CONFIG_DEFAULTS},
    CONFIG_VALIDATION,
    KALMAN_BASICS,
    KALMAN_CONVERGENCE,
    KALMAN_STABILITY,
    FRAME_PACKING,
    MKDIR_HANDLING,
    WRITE_RETRY,
    FULL_FLIGHT_SEQ,
    APOGEE_DWELL,
    BOOST_RECOVERY,
    COAST_TIMEOUT_META,
    LANDING_DETECTION,
    NORMAL_FLIGHT,
    IDEAL_FLIGHT,
    NOISY_FLIGHT,
    ANGLED_FLIGHT,
    FALSE_LAUNCH,
    SENSOR_DROPOUT,
    MULTI_DROPOUT,
    PRESSURE_SPIKE,
    HIGH_ALTITUDE,
    SHORT_FLIGHT,
    BELOW_GROUND,
    WIND_GUST,
    STUCK_SENSOR,
    TEMPERATURE,
    BROWNOUT,
    EXTENDED_PAD,
    SD_WRITE_FAILURE,
    SD_FULL,
    MARGINAL_LAUNCH,
    ROUND_TRIP,
)


CATEGORY_COLORS = {
    "Config": "bright_yellow",
    "Kalman": "bright_magenta",
    "DataLog": "bright_blue",
    "StateMachine": "bright_cyan",
    "Integration": "bright_green",
}
