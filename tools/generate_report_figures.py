"""Generate figures for the MPR Altitude Logger LaTeX report."""

import matplotlib.pyplot as plt
import matplotlib.patches as mpatches
import numpy as np
from pathlib import Path

OUT_DIR = Path(__file__).parent.parent
plt.rcParams.update({
    'font.size': 10,
    'font.family': 'serif',
    'axes.grid': True,
    'grid.alpha': 0.3,
    'figure.dpi': 300,
})

# ── Figure 1: Simulated flight profile with state regions ────────────

def generate_flight_profile():
    """Simulate a realistic H-class rocket flight and plot altitude, velocity, and states."""
    dt = 0.02  # 50 Hz
    t_max = 120.0
    t = np.arange(0, t_max, dt)
    n = len(t)

    alt = np.zeros(n)
    vel = np.zeros(n)

    # Motor: ~1.8s burn, ~160 N avg thrust
    burn_time = 1.8
    thrust = 160.0
    mass_dry = 2.5
    mass_prop = 0.09
    cd = 0.45
    area = np.pi * (0.054 / 2) ** 2
    g = 9.81

    for i in range(1, n):
        ti = t[i]
        m = mass_dry + max(0, mass_prop * (1 - ti / burn_time)) if ti < burn_time else mass_dry
        rho = 1.225 * np.exp(-alt[i-1] / 8500)
        drag = 0.5 * rho * vel[i-1] * abs(vel[i-1]) * cd * area
        F_thrust = thrust if ti < burn_time else 0
        if alt[i-1] <= 0 and vel[i-1] <= 0 and ti > burn_time + 5:
            # Landed
            alt[i] = 0
            vel[i] = 0
            continue
        # Parachute drag after apogee
        if vel[i-1] < 0 and alt[i-1] > 0:
            cd_chute = 1.5
            chute_area = np.pi * (0.6 / 2) ** 2  # drogue
            if alt[i-1] < 0.25 * np.max(alt[:i]):
                chute_area = np.pi * (1.2 / 2) ** 2  # main
            drag = 0.5 * rho * vel[i-1] * abs(vel[i-1]) * cd_chute * chute_area

        acc = (F_thrust - drag) / m - g
        vel[i] = vel[i-1] + acc * dt
        alt[i] = max(0, alt[i-1] + vel[i] * dt)

    # Determine states
    states = np.zeros(n, dtype=int)  # 0=PAD
    apogee_idx = np.argmax(alt)
    max_alt = alt[apogee_idx]

    for i in range(n):
        if t[i] < 0.5:
            states[i] = 0  # PAD
        elif t[i] < burn_time + 0.1:
            states[i] = 1  # BOOST
        elif i < apogee_idx:
            states[i] = 2  # COAST
        elif i == apogee_idx:
            states[i] = 3  # APOGEE
        elif alt[i] > 0.25 * max_alt:
            states[i] = 4  # DROGUE
        elif alt[i] > 0.5:
            states[i] = 5  # MAIN
        else:
            states[i] = 6  # LANDED

    # Add barometric noise for "raw" altitude
    alt_raw = alt + np.random.normal(0, 0.8, n)
    alt_raw = np.maximum(alt_raw, 0)

    # Colours for state regions
    state_colors = {
        0: '#e8e8e8', 1: '#ffcccc', 2: '#ffe0b2',
        3: '#fff9c4', 4: '#c8e6c9', 5: '#bbdefb', 6: '#e1bee7'
    }
    state_names = {
        0: 'PAD', 1: 'BOOST', 2: 'COAST',
        3: 'APOGEE', 4: 'DROGUE', 5: 'MAIN', 6: 'LANDED'
    }

    fig, (ax1, ax2) = plt.subplots(2, 1, figsize=(7, 5), sharex=True,
                                    gridspec_kw={'height_ratios': [2, 1]})
    fig.subplots_adjust(hspace=0.08)

    # Shade state regions
    prev_state = states[0]
    start_t = t[0]
    for i in range(1, n):
        if states[i] != prev_state or i == n - 1:
            for ax in (ax1, ax2):
                ax.axvspan(start_t, t[i], alpha=0.25, color=state_colors[prev_state], zorder=0)
            # Label at midpoint
            mid = (start_t + t[i]) / 2
            if t[i] - start_t > 1.5:
                ax1.text(mid, max_alt * 0.95, state_names[prev_state],
                        ha='center', va='top', fontsize=7, color='#666666', style='italic')
            prev_state = states[i]
            start_t = t[i]

    # Altitude plot
    ax1.plot(t, alt_raw, color='#cccccc', linewidth=0.4, label='Raw barometric', zorder=1)
    ax1.plot(t, alt, color='#4a9eff', linewidth=1.2, label='Kalman filtered', zorder=2)
    ax1.set_ylabel('Altitude AGL (m)')
    ax1.legend(loc='upper right', fontsize=8, framealpha=0.9)
    ax1.set_xlim(0, min(t_max, t[np.max(np.where(alt > 0))] + 5))

    # Velocity plot
    ax2.plot(t, vel, color='#ff5555', linewidth=1.0)
    ax2.axhline(y=0, color='black', linewidth=0.5, linestyle='-')
    ax2.set_ylabel('Velocity (m/s)')
    ax2.set_xlabel('Time (s)')

    ax1.set_title('Simulated Flight Profile — H-class Motor', fontsize=11, fontweight='bold')

    fig.savefig(OUT_DIR / 'fig_flight_profile.pdf', bbox_inches='tight')
    fig.savefig(OUT_DIR / 'fig_flight_profile.png', bbox_inches='tight')
    print(f'Saved fig_flight_profile.pdf/png')
    plt.close(fig)


# ── Figure 2: Frame timing budget ────────────────────────────────────

def generate_timing_budget():
    """Bar chart showing how the 20 ms frame budget is spent."""
    stages = [
        'Collect baro\nresult (I²C)',
        'Kalman\nfilter',
        'State\nmachine',
        'Power rails\n(3× ADC)',
        'SD write\n(40 B frame)',
        'Start next\nconversion',
        'Spin-wait\n(idle)',
    ]
    times_us = [1000, 100, 50, 300, 1000, 100, 17450]
    total = 20000

    colors = ['#4a9eff'] * 6 + ['#e0e0e0']

    fig, ax = plt.subplots(figsize=(7, 3))

    bars = ax.barh(stages, [t / 1000 for t in times_us], color=colors, edgecolor='white', height=0.6)

    for bar, t in zip(bars, times_us):
        w = bar.get_width()
        label = f'{t/1000:.1f} ms' if t >= 100 else f'{t} μs'
        if w > 1.5:
            ax.text(w - 0.1, bar.get_y() + bar.get_height()/2, label,
                    ha='right', va='center', fontsize=8, color='white', fontweight='bold')
        else:
            ax.text(w + 0.15, bar.get_y() + bar.get_height()/2, label,
                    ha='left', va='center', fontsize=8, color='#333333')

    ax.set_xlabel('Time (ms)')
    ax.set_xlim(0, 21)
    ax.axvline(x=20, color='#ff5555', linestyle='--', linewidth=1, label='20 ms budget (50 Hz)')
    ax.axvline(x=2.55, color='#4a9eff', linestyle=':', linewidth=1, label='~2.6 ms active CPU')
    ax.legend(loc='lower right', fontsize=8, framealpha=0.9)
    ax.set_title('Frame Timing Budget — Per-Stage Breakdown', fontsize=11, fontweight='bold')
    ax.invert_yaxis()

    fig.savefig(OUT_DIR / 'fig_timing_budget.pdf', bbox_inches='tight')
    fig.savefig(OUT_DIR / 'fig_timing_budget.png', bbox_inches='tight')
    print(f'Saved fig_timing_budget.pdf/png')
    plt.close(fig)


if __name__ == '__main__':
    generate_flight_profile()
    generate_timing_budget()
    print('Done. Include in LaTeX with:')
    print(r'  \includegraphics[width=\textwidth]{fig_flight_profile.pdf}')
    print(r'  \includegraphics[width=\textwidth]{fig_timing_budget.pdf}')
