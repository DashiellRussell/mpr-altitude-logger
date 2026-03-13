import React, { useMemo } from 'react';
import type { FlightFrame, FlightStats, SimSummary } from '@mpr/shared';

interface FlightInsightsProps {
  frames: FlightFrame[];
  stats: FlightStats;
  version: number;
  simSummary?: SimSummary;
}

interface Insight {
  type: 'info' | 'good' | 'warning' | 'critical';
  title: string;
  detail: string;
}

export function FlightInsights({ frames, stats, version, simSummary }: FlightInsightsProps) {
  const insights = useMemo(() => {
    const items: Insight[] = [];
    if (!frames.length) return items;

    const t0 = frames[0].timestamp_ms;

    // ── Kalman filter quality ──
    // Compare raw vs filtered to gauge noise level
    let rawFilteredDiffSum = 0;
    let maxRawFilteredDiff = 0;
    for (const f of frames) {
      const diff = Math.abs(f.alt_raw_m - f.alt_filtered_m);
      rawFilteredDiffSum += diff;
      if (diff > maxRawFilteredDiff) maxRawFilteredDiff = diff;
    }
    const avgDiff = rawFilteredDiffSum / frames.length;

    if (avgDiff < 1) {
      items.push({
        type: 'good',
        title: 'Low sensor noise',
        detail: `Average raw-vs-filtered deviation: ${avgDiff.toFixed(2)}m. Kalman filter is tracking well with minimal correction needed.`,
      });
    } else if (avgDiff > 5) {
      items.push({
        type: 'warning',
        title: 'High sensor noise',
        detail: `Average raw-vs-filtered deviation: ${avgDiff.toFixed(1)}m (peak: ${maxRawFilteredDiff.toFixed(1)}m). Consider increasing KALMAN_R_ALT to smooth more, or check BMP280 wiring.`,
      });
    } else {
      items.push({
        type: 'info',
        title: 'Moderate sensor noise',
        detail: `Average raw-vs-filtered deviation: ${avgDiff.toFixed(2)}m. Filter is correcting reasonable barometric noise.`,
      });
    }

    // ── Sample rate consistency ──
    if (frames.length > 10) {
      const intervals: number[] = [];
      for (let i = 1; i < frames.length; i++) {
        intervals.push(frames[i].timestamp_ms - frames[i - 1].timestamp_ms);
      }
      intervals.sort((a, b) => a - b);
      const median = intervals[Math.floor(intervals.length / 2)];
      const p95 = intervals[Math.floor(intervals.length * 0.95)];
      const jitter = p95 - median;

      if (jitter > median * 0.5) {
        items.push({
          type: 'warning',
          title: 'Timing jitter detected',
          detail: `Median interval: ${median}ms, P95: ${p95}ms (${jitter}ms jitter). SD writes or I2C stalls may be causing timing drift. Check LOG_FLUSH_EVERY and BMP280 response times.`,
        });
      } else {
        items.push({
          type: 'good',
          title: 'Stable sample rate',
          detail: `Median interval: ${median}ms (~${(1000 / median).toFixed(1)} Hz), P95: ${p95}ms. Timing is consistent.`,
        });
      }
    }

    // ── Flight events ──
    const boostTransition = stats.transitions.find((t) => t.to_state === 'BOOST');
    const apogeeTransition = stats.transitions.find((t) => t.to_state === 'APOGEE');
    const landedTransition = stats.transitions.find((t) => t.to_state === 'LANDED');

    if (boostTransition && apogeeTransition) {
      const burnTime = apogeeTransition.time - boostTransition.time;
      const coastTransition = stats.transitions.find((t) => t.to_state === 'COAST');
      if (coastTransition) {
        const motorBurn = coastTransition.time - boostTransition.time;
        const coastTime = apogeeTransition.time - coastTransition.time;
        items.push({
          type: 'info',
          title: 'Flight profile',
          detail: `Motor burn: ${motorBurn.toFixed(1)}s, coast to apogee: ${coastTime.toFixed(1)}s, total ascent: ${burnTime.toFixed(1)}s.`,
        });
      }
    }

    // ── Apogee analysis ──
    if (stats.maxAlt > 0) {
      if (stats.maxVel > 0) {
        // Ballistic coefficient estimate: how efficiently the rocket converts velocity to altitude
        const ballisticEff = stats.maxAlt / (stats.maxVel * stats.maxVel / (2 * 9.81));
        items.push({
          type: 'info',
          title: 'Altitude efficiency',
          detail: `Achieved ${(ballisticEff * 100).toFixed(0)}% of ideal (no-drag) coasting altitude. ${ballisticEff < 0.4 ? 'High drag — check fin alignment and surface finish.' : ballisticEff < 0.7 ? 'Typical for this class of rocket.' : 'Very efficient — low drag profile.'}`,
        });
      }
    }

    // ── Landing analysis ──
    if (stats.landingVel > 0) {
      if (stats.landingVel > 10) {
        items.push({
          type: 'critical',
          title: 'Hard landing',
          detail: `Landing velocity: ${stats.landingVel.toFixed(1)} m/s. This exceeds safe limits (typically <5 m/s). Check recovery system.`,
        });
      } else if (stats.landingVel > 5) {
        items.push({
          type: 'warning',
          title: 'Fast landing',
          detail: `Landing velocity: ${stats.landingVel.toFixed(1)} m/s. On the high side — inspect airframe for damage.`,
        });
      } else {
        items.push({
          type: 'good',
          title: 'Soft landing',
          detail: `Landing velocity: ${stats.landingVel.toFixed(1)} m/s. Within safe recovery limits.`,
        });
      }
    }

    // ── Power rail analysis (v2 only) ──
    if (version >= 2 && stats.v3v3Range) {
      const [min3v3, max3v3] = stats.v3v3Range;
      const drop3v3 = (max3v3 - min3v3) / 1000;
      if (min3v3 / 1000 < 3.0) {
        items.push({
          type: 'critical',
          title: '3V3 rail brownout',
          detail: `3V3 rail dropped to ${(min3v3 / 1000).toFixed(2)}V (min 3.0V for reliable RP2040 operation). Check regulator and battery capacity.`,
        });
      } else if (drop3v3 > 0.2) {
        items.push({
          type: 'warning',
          title: '3V3 rail voltage sag',
          detail: `3V3 rail varied by ${(drop3v3).toFixed(2)}V during flight (${(min3v3 / 1000).toFixed(2)}V-${(max3v3 / 1000).toFixed(2)}V). Monitor for transient loads.`,
        });
      }
    }

    // ── SD card errors ──
    if (stats.hadError) {
      items.push({
        type: 'critical',
        title: 'Errors during flight',
        detail: 'Error flag was set in one or more frames. Check SD card reliability and SPI connections.',
      });
    }

    // ── Sim comparison insights ──
    if (simSummary) {
      const altDelta = ((stats.maxAlt - simSummary.maxAlt) / simSummary.maxAlt) * 100;
      const velDelta = ((stats.maxVel - simSummary.maxVel) / simSummary.maxVel) * 100;

      if (Math.abs(altDelta) > 20) {
        items.push({
          type: 'warning',
          title: `Apogee ${altDelta > 0 ? 'higher' : 'lower'} than predicted`,
          detail: `Actual: ${stats.maxAlt.toFixed(1)}m vs sim: ${simSummary.maxAlt.toFixed(1)}m (${altDelta > 0 ? '+' : ''}${altDelta.toFixed(1)}%). ${altDelta > 0 ? 'Drag may be lower than modeled — increase Cd in OpenRocket.' : 'Drag may be higher than modeled — decrease Cd, or check for airframe damage.'}`,
        });
      } else if (Math.abs(altDelta) < 5) {
        items.push({
          type: 'good',
          title: 'Apogee matches simulation',
          detail: `Actual: ${stats.maxAlt.toFixed(1)}m vs sim: ${simSummary.maxAlt.toFixed(1)}m (${altDelta > 0 ? '+' : ''}${altDelta.toFixed(1)}%). Sim model is well-calibrated.`,
        });
      } else {
        items.push({
          type: 'info',
          title: 'Apogee deviation from sim',
          detail: `Actual: ${stats.maxAlt.toFixed(1)}m vs sim: ${simSummary.maxAlt.toFixed(1)}m (${altDelta > 0 ? '+' : ''}${altDelta.toFixed(1)}%). Consider fine-tuning Cd.`,
        });
      }

      if (Math.abs(velDelta) > 15) {
        items.push({
          type: 'warning',
          title: `Max velocity ${velDelta > 0 ? 'higher' : 'lower'} than predicted`,
          detail: `Actual: ${stats.maxVel.toFixed(1)} m/s vs sim: ${simSummary.maxVel.toFixed(1)} m/s (${velDelta > 0 ? '+' : ''}${velDelta.toFixed(1)}%). ${velDelta > 0 ? 'Motor may have over-performed or mass was lower.' : 'Motor under-performance or higher-than-expected mass.'}`,
        });
      }
    }

    return items;
  }, [frames, stats, version, simSummary]);

  if (insights.length === 0) return null;

  const typeIcon: Record<string, string> = {
    info: 'i',
    good: '+',
    warning: '!',
    critical: 'X',
  };

  return (
    <div className="card">
      <h2>Flight Insights</h2>
      <div className="insights-list">
        {insights.map((insight, i) => (
          <div key={i} className={`insight insight-${insight.type}`}>
            <span className="insight-icon">{typeIcon[insight.type]}</span>
            <div>
              <div className="insight-title">{insight.title}</div>
              <div className="insight-detail">{insight.detail}</div>
            </div>
          </div>
        ))}
      </div>
    </div>
  );
}
