import { describe, it, expect } from 'vitest';
import { parseSimCsv, isOpenRocketCsv } from './sim-parser.js';
import { parseOpenRocketCsv } from './openrocket.js';

describe('parseSimCsv', () => {
  it('parses simple simulation CSV', () => {
    const csv = `time_s,altitude_m,velocity_ms,state
0.0,0.0,0.0,PAD
1.0,50.0,80.0,BOOST
5.0,500.0,0.0,APOGEE
10.0,0.0,-5.0,LANDED`;

    const rows = parseSimCsv(csv);
    expect(rows).toHaveLength(4);
    expect(rows[0].time_s).toBe(0);
    expect(rows[1].altitude_m).toBe(50);
    expect(rows[2].velocity_ms).toBe(0);
    expect(rows[3].state).toBe('LANDED');
  });

  it('skips rows without time_s', () => {
    const csv = `time_s,altitude_m
1.0,100
,200
3.0,300`;

    const rows = parseSimCsv(csv);
    expect(rows).toHaveLength(2);
  });

  it('returns empty for empty input', () => {
    expect(parseSimCsv('')).toHaveLength(0);
    expect(parseSimCsv('header_only')).toHaveLength(0);
  });
});

describe('isOpenRocketCsv', () => {
  it('detects OpenRocket CSV', () => {
    const text = `# some comment
# Event APOGEE occurred at t=12.345 seconds
Time (s),Altitude (m)
0,0
1,100`;
    expect(isOpenRocketCsv(text)).toBe(true);
  });

  it('rejects simple sim CSV', () => {
    const text = `time_s,altitude_m
0,0
1,100`;
    expect(isOpenRocketCsv(text)).toBe(false);
  });
});

describe('parseOpenRocketCsv', () => {
  it('parses OpenRocket CSV with events', () => {
    const csv = `# OpenRocket simulation export
# Event LAUNCH occurred at t=0.000 seconds
# Event BURNOUT occurred at t=1.500 seconds
# Event APOGEE occurred at t=8.200 seconds
# Event GROUND_HIT occurred at t=25.000 seconds
Time (s),Altitude (m),Vertical velocity (m/s)
0.0,0.0,0.0
0.5,25.0,80.0
1.5,150.0,120.0
5.0,450.0,30.0
8.2,500.0,0.5
15.0,200.0,-15.0
25.0,0.0,-3.0`;

    const result = parseOpenRocketCsv(csv);
    expect(result.events).toHaveLength(4);
    expect(result.events[0].event).toBe('LAUNCH');
    expect(result.events[2].event).toBe('APOGEE');
    expect(result.events[2].time_s).toBe(8.2);

    expect(result.rows).toHaveLength(7);
    expect(result.rows[0].time_s).toBe(0);
    expect(result.rows[0].altitude_m).toBe(0);

    // State assignment from events
    expect(result.rows[0].state).toBe('BOOST'); // t=0 >= LAUNCH
    expect(result.rows[2].state).toBe('COAST'); // t=1.5 >= BURNOUT
    expect(result.rows[4].state).toBe('APOGEE'); // t=8.2 >= APOGEE

    expect(result.metadata.nEvents).toBe(4);
    expect(result.metadata.columnsFound).toContain('time_s');
    expect(result.metadata.columnsFound).toContain('altitude_m');
  });

  it('converts units from feet to meters', () => {
    const csv = `Time (s),Altitude (ft)
0.0,0.0
1.0,328.084`;

    const result = parseOpenRocketCsv(csv);
    expect(result.rows[1].altitude_m).toBeCloseTo(100.0, 0);
  });

  it('handles semicolon separator', () => {
    const csv = `Time (s);Altitude (m)
0.0;0.0
1.0;100.0`;

    const result = parseOpenRocketCsv(csv);
    expect(result.rows).toHaveLength(2);
    expect(result.rows[1].altitude_m).toBe(100);
  });

  it('throws on missing time column', () => {
    const csv = `Altitude (m),Velocity (m/s)
0,0
100,50`;

    expect(() => parseOpenRocketCsv(csv)).toThrow('No time column');
  });
});
