import React, { useState } from 'react';
import type { FlightFrame, SimSummary, StateTransition } from '@mpr/shared';
import { FlightOverview } from './FlightOverview';
import { AltitudeChart } from './AltitudeChart';
import { VelocityChart } from './VelocityChart';
import { PressureChart } from './PressureChart';
import { PowerChart } from './PowerChart';

const TABS = ['Overview', 'Altitude', 'Velocity', 'Pressure', 'Power'] as const;
type Tab = typeof TABS[number];

interface ChartTabsProps {
  frames: FlightFrame[];
  transitions: StateTransition[];
  simSummary?: SimSummary;
  version: number;
  cursorTime?: number;
}

export function ChartTabs({ frames, transitions, simSummary, version, cursorTime }: ChartTabsProps) {
  const [activeTab, setActiveTab] = useState<Tab>('Overview');

  return (
    <div className="chart-tabs">
      <div className="chart-tab-bar">
        {TABS.map((tab) => (
          <button
            key={tab}
            className={`chart-tab ${activeTab === tab ? 'chart-tab-active' : ''}`}
            onClick={() => setActiveTab(tab)}
          >
            {tab}
          </button>
        ))}
      </div>
      <div className="chart-tab-content">
        {activeTab === 'Overview' && (
          <FlightOverview
            frames={frames}
            simSummary={simSummary}
            transitions={transitions}
            version={version}
            cursorTime={cursorTime}
          />
        )}
        {activeTab === 'Altitude' && (
          <AltitudeChart
            frames={frames}
            simSummary={simSummary}
            transitions={transitions}
            cursorTime={cursorTime}
          />
        )}
        {activeTab === 'Velocity' && (
          <VelocityChart
            frames={frames}
            transitions={transitions}
            simSummary={simSummary}
            cursorTime={cursorTime}
          />
        )}
        {activeTab === 'Pressure' && (
          <PressureChart frames={frames} cursorTime={cursorTime} />
        )}
        {activeTab === 'Power' && (
          <PowerChart frames={frames} version={version} cursorTime={cursorTime} />
        )}
      </div>
    </div>
  );
}
