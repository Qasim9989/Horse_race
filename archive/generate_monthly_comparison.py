import pandas as pd

def main():
    # Load Proform System Data
    df_pro = pd.read_csv(r'E:\Test\racing-form-system\reports\MasterLay_Backtest.csv')
    df_pro['RaceDate'] = pd.to_datetime(df_pro['TargetDateTime']).dt.to_period('M')
    res_pro = df_pro.groupby('RaceDate').agg(Bets=('TargetRaceId','count'), PnL=('PnL','sum'))
    res_pro['ROI'] = (res_pro['PnL'] / (res_pro['Bets'] * 100)) * 100
    
    # Load RacingTV Hybrid System Data
    df_rtv = pd.read_csv(r'E:\Test\racing-form-system\reports\Hybrid_RacingTV_Proform_Lay_Bets_20260822_150747.csv')
    df_rtv['RaceDate'] = pd.to_datetime(df_rtv['RaceDate']).dt.to_period('M')
    res_rtv = df_rtv.groupby('RaceDate').agg(Bets=('HorseName','count'), PnL=('LayPNL','sum'))
    # Hybrid script used fixed liability of 15. Scale it up to 100 for comparison, or just calculate ROI directly
    res_rtv['ROI'] = (res_rtv['PnL'] / (res_rtv['Bets'] * 15)) * 100
    
    # Merge for side-by-side
    comparison = pd.merge(res_pro, res_rtv, on='RaceDate', how='outer', suffixes=('_Pro', '_RTV'))
    comparison = comparison.sort_index()
    
    with open(r'C:\Users\qasim\.gemini\antigravity-ide\brain\5c30d050-485e-4a16-92f4-b3589633a119\monthly_roi_comparison.md', 'w') as f:
        f.write("# Month-by-Month ROI Comparison\n\n")
        f.write("This table compares the Proform `FSPEFFRK > 2` system against your Scraped RacingTV `MasterScore` system side-by-side.\n\n")
        f.write("> [!WARNING]\n")
        f.write("> **Proform Data is Missing!** Notice how the Proform database completely stops after May 2026. This explains why the hybrid system's performance metrics drop off a cliff in June/July/August (it couldn't settle the bets properly because the Proform `HIR_BSP` was missing).\n\n")
        f.write("| Month | Proform Bets | Proform ROI | RacingTV Bets | RacingTV ROI |\n")
        f.write("| :--- | ---: | ---: | ---: | ---: |\n")
        
        for idx, row in comparison.iterrows():
            p_bets = f"{int(row['Bets_Pro']):,}" if pd.notna(row['Bets_Pro']) else "MISSING"
            p_roi = f"{row['ROI_Pro']:.2f}%" if pd.notna(row['ROI_Pro']) else "MISSING"
            
            r_bets = f"{int(row['Bets_RTV']):,}" if pd.notna(row['Bets_RTV']) else "0"
            r_roi = f"{row['ROI_RTV']:.2f}%" if pd.notna(row['ROI_RTV']) else "0.00%"
            
            f.write(f"| **{idx}** | {p_bets} | {p_roi} | {r_bets} | {r_roi} |\n")

if __name__ == "__main__":
    main()
