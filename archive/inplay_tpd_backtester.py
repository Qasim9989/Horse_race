import sys
import os
import csv
import pandas as pd
from collections import defaultdict

def parse_tpd_file(filepath):
    """
    Parses a BetAngel SV export file and returns a list of ticks.
    Each tick is a dictionary of {horse_name: {metrics}} for a specific timestamp.
    """
    history = defaultdict(lambda: defaultdict(dict))
    
    with open(filepath, 'r') as f:
        reader = csv.reader(f)
        try:
            header = next(reader)
        except StopIteration:
            return {}
            
        for row in reader:
            if len(row) < 5: continue
            
            time_str = row[0]
            market = row[1]
            horse_name = row[3]
            
            # The name/value pairs start at index 5
            for i in range(5, len(row) - 1, 2):
                key_raw = row[i]
                val_raw = row[i+1]
                
                # Clean key
                key = key_raw.replace(' {S}', '').replace(' {HL}', '').strip()
                
                try:
                    val = float(val_raw)
                except ValueError:
                    val = val_raw
                    
                history[time_str][horse_name][key] = val
                
    # Convert to chronological list of ticks
    # Since keys are strings like "25/07/2026 13:53:15", we can sort them
    sorted_times = sorted(list(history.keys()), key=lambda x: pd.to_datetime(x, format='%d/%m/%Y %H:%M:%S', errors='coerce'))
    
    ticks = []
    for t in sorted_times:
        ticks.append({
            'time': t,
            'runners': history[t]
        })
        
    return ticks

def simulate_race(ticks, market_name):
    """
    Runs the In-Play triggers on a single race.
    Returns a list of bets placed: [{'horse', 'odds', 'type', 'is_win', 'pnl'}]
    """
    bets = []
    active_lays = set()
    active_backs = set()
    
    # 1. Determine the winner (the horse that finishes with position == 1)
    # We look at the very last tick where horses have position data
    winner = None
    for tick in reversed(ticks):
        if winner: break
        for horse, data in tick['runners'].items():
            if data.get('position') == 1 and data.get('percentage_remaining', 100) < 1.0:
                winner = horse
                break
                
    # Fallback to identify winner if percentage_remaining never hit 0 exactly
    if not winner:
        for tick in reversed(ticks):
            if winner: break
            for horse, data in tick['runners'].items():
                if data.get('position') == 1:
                    winner = horse
                    break
                    
    print(f"Simulating {market_name}... (Winner identified as: {winner})")
    
    # 2. Simulate tick by tick
    for tick in ticks:
        runners = tick['runners']
        
        for horse, data in runners.items():
            perc_rem = data.get('percentage_remaining', 100)
            speed = data.get('current_speed', 0)
            par_speed = data.get('par_speed', 0)
            stride = data.get('current_stride', 0)
            pos = data.get('position', 99)
            odds = data.get('back_price', 0)
            
            # Skip if we don't have valid odds or it's suspended
            if not isinstance(odds, (int, float)) or odds <= 1.01 or odds > 100:
                continue
                
            # Skip if already bet on this horse
            if horse in active_lays:
                continue
                
            # TRIGGER 1: Pace Collapse (Laying the Leader)
            if pos == 1 and 60 <= perc_rem <= 90:
                if speed > 0 and par_speed > 0 and (speed - par_speed) > 2.0:
                    bets.append({'horse': horse, 'odds': odds, 'type': 'LAY', 'reason': 'Pace Collapse', 'time': tick['time']})
                    active_lays.add(horse)
                    
            # TRIGGER 2: Stride Decay (Laying the tired horse)
            if 0 < perc_rem <= 20 and pos <= 3:
                if 0 < stride < 22.0:
                    bets.append({'horse': horse, 'odds': odds, 'type': 'LAY', 'reason': 'Stride Decay', 'time': tick['time']})
                    active_lays.add(horse)
            
            # TRIGGER 3: The "Cruising" Backer (Backing a relaxed horse)
            # High speed, low effort. Just behind the leader.
            if pos in [2, 3] and 30 <= perc_rem <= 60:
                if speed > 35.0 and stride > 24.5 and odds > 2.0:
                    if horse not in active_backs:
                        bets.append({'horse': horse, 'odds': odds, 'type': 'BACK', 'reason': 'Cruising on Bridle', 'time': tick['time']})
                        active_backs.add(horse)
                        
            # TRIGGER 4: Broadcast Delay Momentum Strike (Backing a surging horse)
            # Far back, but speed is massive
            if pos >= 4 and 10 <= perc_rem <= 30:
                if speed > 38.0 and odds > 4.0:
                    if horse not in active_backs:
                        bets.append({'horse': horse, 'odds': odds, 'type': 'BACK', 'reason': 'Momentum Spike', 'time': tick['time']})
                        active_backs.add(horse)
                    
    # 3. Settle PnL
    stake = 10.0 # £10 flat stake/liability
    for b in bets:
        b['is_win'] = (b['horse'] == winner)
        
        if b['type'] == 'LAY':
            if b['is_win']:
                b['pnl'] = -stake
            else:
                lay_stake = stake / (b['odds'] - 1)
                b['pnl'] = lay_stake * 0.98 # 2% commission
                
        elif b['type'] == 'BACK':
            if b['is_win']:
                b['pnl'] = (stake * (b['odds'] - 1)) * 0.98
            else:
                b['pnl'] = -stake
                
    return bets

def run_backtest_directory(directory_path):
    print(f"Scanning directory: {directory_path}")
    all_bets = []
    
    files = [f for f in os.listdir(directory_path) if f.endswith('.csv') and 'TPDzone' in f]
    for file in files:
        filepath = os.path.join(directory_path, file)
        # Skip small files that likely have no data
        if os.path.getsize(filepath) < 10000:
            continue
            
        ticks = parse_tpd_file(filepath)
        if not ticks: continue
        
        market = file.replace('.csv', '')
        bets = simulate_race(ticks, market)
        
        for b in bets:
            b['market'] = market
            all_bets.append(b)
            
    df_bets = pd.DataFrame(all_bets)
    if df_bets.empty:
        print("No bets triggered.")
        return
        
    print(f"\n=======================================================")
    print(f"  IN-PLAY TPD BACKTEST RESULTS")
    print(f"=======================================================")
    
    total_bets = len(df_bets)
    winning_lays = len(df_bets[df_bets['is_win'] == False]) # A winning lay is when the horse doesn't win
    total_pnl = df_bets['pnl'].sum()
    
    # Approx Staked for ROI
    # For lays, we risked 'liability' per bet
    total_risked = total_bets * 10.0
    roi = (total_pnl / total_risked) * 100
    
    print(f"Total Markets Analysed: {len(files)}")
    print(f"Total Bets Triggered: {total_bets}")
    print(f"Successful Lays: {winning_lays} ({(winning_lays/total_bets*100):.1f}%)")
    print(f"Total Risked: £{total_risked:.2f}")
    print(f"Total PnL: £{total_pnl:.2f}")
    print(f"ROI: {roi:.2f}%")
    
    print("\n--- Breakdown by Trigger ---")
    df_bets['Trigger'] = df_bets['reason'].apply(lambda x: x.split('(')[0].strip())
    
    for trigger, group in df_bets.groupby('Trigger'):
        tb = len(group)
        pnl = group['pnl'].sum()
        rsk = tb * 10.0
        r = (pnl / rsk) * 100 if rsk > 0 else 0
        
        # Calculate strike rate correctly based on type
        if group['type'].iloc[0] == 'LAY':
            wl = len(group[group['is_win'] == False])
        else:
            wl = len(group[group['is_win'] == True])
            
        print(f"{trigger} ({group['type'].iloc[0]}): {tb} bets | SR: {(wl/tb*100):.1f}% | PnL: £{pnl:.2f} | ROI: {r:.2f}%")

if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Usage: python inplay_tpd_backtester.py <path_to_sv_exports_folder>")
        sys.exit(1)
        
    run_backtest_directory(sys.argv[1])
