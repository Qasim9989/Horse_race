"""
IN-PLAY LIVE SECTIONAL & TELEMETRY SIMULATOR
Predicts In-Running Win/Place Probabilities and Lay/Back Triggers based on:
- Distance Remaining (Furlongs)
- Current Position & Leader Delta (Lengths)
- Finishing Speed Efficiency (%) & Stride Length (ft / m)
"""

import sys

def predict_inplay_probabilities(dist_furlongs, position, leader_delta_lengths, fin_speed_pct, stride_length_ft, is_headed=False):
    """
    Returns estimated In-Running Win %, Place %, and Strategic Lay/Back Actions.
    """
    base_place_prob = 15.0
    
    # 1. Position Impact
    if position == 1:
        base_place_prob = 65.0 if not is_headed else 25.0
    elif position <= 3:
        base_place_prob = max(5.0, 45.0 - (leader_delta_lengths * 4.0))
    elif position <= 5:
        base_place_prob = max(2.0, 25.0 - (leader_delta_lengths * 3.0))
    else:
        base_place_prob = max(0.5, 12.0 - (leader_delta_lengths * 1.5))
        
    # 2. Finishing Speed Efficiency Impact
    if fin_speed_pct >= 110.0:
        speed_mult = 1.6
    elif fin_speed_pct >= 105.0:
        speed_mult = 1.3
    elif fin_speed_pct >= 100.0:
        speed_mult = 1.0
    else:
        speed_mult = 0.6
        
    # 3. Stride Length Impact (< 22.3 ft short cadence holds up better late)
    if stride_length_ft <= 22.3:
        stride_mult = 1.25
    elif stride_length_ft <= 24.0:
        stride_mult = 1.0
    else:
        stride_mult = 0.75
        
    final_place_prob = min(99.0, max(0.1, base_place_prob * speed_mult * stride_mult))
    final_win_prob = min(95.0, max(0.05, (final_place_prob * 0.35) if position <= 2 else (final_place_prob * 0.15)))
    
    # Lay / Back Action Triggers
    if is_headed and position == 1:
        action = "[HIGH-CONFIDENCE LAY] Leader Headed & Fading"
    elif final_place_prob >= 35.0 and speed_mult >= 1.3:
        action = "[BACK / SURGE TARGET] Strong Finishing Telemetry"
    elif final_place_prob <= 5.0 and position <= 4:
        action = "[LAY TARGET] Weak Late Speed Efficiency"
    else:
        action = "[HOLD] No Trade Signal"
        
    return {
        "dist_remaining_furlongs": dist_furlongs,
        "position": position,
        "leader_delta_lengths": leader_delta_lengths,
        "finishing_speed_pct": fin_speed_pct,
        "stride_length_ft": stride_length_ft,
        "win_probability_pct": round(final_win_prob, 1),
        "place_probability_pct": round(final_place_prob, 1),
        "recommended_action": action
    }

def run_demo_simulation():
    print("="*80)
    print("IN-PLAY TELEMETRY & SECTIONAL LIVE SIMULATION DEMO")
    print("="*80)
    
    scenarios = [
        {"name": "Scenario 1: Front-Runner Gets Headed 2F Out", "dist": 2.0, "pos": 1, "delta": 0.0, "speed": 94.0, "stride": 25.1, "headed": True},
        {"name": "Scenario 2: High-Cadence Challenger Surging 2F Out", "dist": 2.0, "pos": 2, "delta": 1.5, "speed": 112.0, "stride": 21.8, "headed": False},
        {"name": "Scenario 3: Steady Midfield Runner 3F Out", "dist": 3.0, "pos": 5, "delta": 4.0, "speed": 101.0, "stride": 23.5, "headed": False},
    ]
    
    for s in scenarios:
        res = predict_inplay_probabilities(s['dist'], s['pos'], s['delta'], s['speed'], s['stride'], s['headed'])
        print(f"\n--- {s['name']} ---")
        print(f"   Est. In-Play Win Prob:   {res['win_probability_pct']}%")
        print(f"   Est. In-Play Place Prob: {res['place_probability_pct']}%")
        print(f"   Signal:                  {res['recommended_action']}")
        
    print("="*80)

if __name__ == "__main__":
    run_demo_simulation()
