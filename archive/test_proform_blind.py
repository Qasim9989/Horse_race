import pandas as pd
import pyodbc
import warnings

warnings.filterwarnings("ignore")

def connection_string(database):
    return (
        "Driver={ODBC Driver 17 for SQL Server};"
        "Server=(localdb)\\MSSQLLocalDB;"
        f"Database={database};"
        "Trusted_Connection=yes;"
    )

def connect(database):
    return pyodbc.connect(connection_string(database), timeout=10)

def main():
    print("Loading Proform blind lay data...")
    sql_pro = """
    SELECT
        CAST(RH.RH_DateTime AS DATE) AS RaceDate,
        HIR.HIR_PositionNo AS ProformPositionNo,
        HIR.HIR_BSP AS ProformBSP
    FROM dbo.NEW_RH RH
    JOIN dbo.NEW_HIR HIR ON HIR.HIR_RNo = RH.RH_RNo
    WHERE CAST(RH.RH_DateTime AS DATE) >= '2023-01-01'
      AND (RH.RH_HandicapLimit IS NOT NULL OR LOWER(RH.RH_Name) LIKE '%handicap%')
      AND HIR.HIR_BSP >= 1.01 
      AND HIR.HIR_BSP <= 6.00
    """
    with connect("PRODB") as conn:
        pro = pd.read_sql(sql_pro, conn)
        
    print(f"Total Proform handicap qualifiers (BSP 1.01-6.00): {len(pro)}")
    
    # Settle bets
    # Fixed liability of £100
    liability = 100.0
    
    def calculate_pl(row):
        # Position 1 means the horse won (so the lay bet loses)
        is_winner = str(row['ProformPositionNo']) == '1'
        if is_winner:
            return -liability
        else:
            # We lay £(liability / (BSP - 1)), and win that stake back minus 5% commission
            stake = liability / (row['ProformBSP'] - 1)
            return stake * 0.95
            
    pro['PnL'] = pro.apply(calculate_pl, axis=1)
    pro['IsWinner'] = pro['PnL'] < 0
    
    total_bets = len(pro)
    if total_bets == 0:
        print("No qualifiers found!")
        return

    total_pl = pro['PnL'].sum()
    total_liability = total_bets * liability
    roi = (total_pl / total_liability) * 100 if total_liability > 0 else 0
    wins = pro['IsWinner'].sum()
    win_strike_rate = (wins / total_bets) * 100 if total_bets > 0 else 0
    
    print("\n--- RESULTS FOR PURE PROFORM BLIND LAY SYSTEM (2023-2026) ---")
    print(f"Total Bets: {total_bets:,}")
    print(f"Wins (Horse won, lay lost): {wins:,}")
    print(f"Horse Win Strike Rate: {win_strike_rate:.2f}%")
    print(f"Total P&L (£100 fixed liability): £{total_pl:,.2f}")
    print(f"ROI on Liability: {roi:.2f}%")
    
    # Group by year
    pro['Year'] = pd.to_datetime(pro['RaceDate']).dt.year
    yearly = pro.groupby('Year').agg(
        Bets=('RaceDate', 'count'),
        PnL=('PnL', 'sum')
    )
    yearly['ROI'] = (yearly['PnL'] / (yearly['Bets'] * liability)) * 100
    print("\nYearly Breakdown:")
    print(yearly.to_string())

if __name__ == "__main__":
    main()
