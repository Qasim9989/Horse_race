"""No-lookahead in-play lay backtest for bad starts plus 12-tick drift."""
import argparse,csv,glob,os
from collections import defaultdict
import pandas as pd

TICKS=[1.01,1.02,1.03,1.04,1.05,1.06,1.07,1.08,1.09,1.10,1.11,1.12,1.13,1.14,1.15,1.16,1.17,1.18,1.19,1.20,1.21,1.22,1.23,1.24,1.25,1.26,1.27,1.28,1.29,1.30,1.32,1.34,1.36,1.38,1.40,1.42,1.44,1.46,1.48,1.50,1.52,1.54,1.56,1.58,1.60,1.62,1.64,1.66,1.68,1.70,1.72,1.74,1.76,1.78,1.80,1.82,1.84,1.86,1.88,1.90,1.92,1.94,1.96,1.98,2.00,2.02,2.04,2.06,2.08,2.10,2.12,2.14,2.16,2.18,2.20,2.22,2.24,2.26,2.28,2.30,2.32,2.34,2.36,2.38,2.40,2.42,2.44,2.46,2.48,2.50,2.52,2.54,2.56,2.58,2.60,2.62,2.64,2.66,2.68,2.70,2.72,2.74,2.76,2.78,2.80,2.82,2.84,2.86,2.88,2.90,2.92,2.94,2.96,2.98,3.00,3.05,3.10,3.15,3.20,3.25,3.30,3.35,3.40,3.45,3.50,3.55,3.60,3.65,3.70,3.75,3.80,3.85,3.90,3.95,4.00,4.10,4.20,4.30,4.40,4.50,4.60,4.70,4.80,4.90,5.00,5.10,5.20,5.30,5.40,5.50,5.60,5.70,5.80,5.90,6.00,6.20,6.40,6.60,6.80,7.00,7.20,7.40,7.60,7.80,8.00,8.20,8.40,8.60,8.80,9.00,9.20,9.40,9.60,9.80,10.00,10.50,11.00,11.50,12.00,12.50,13.00,13.50,14.00,14.50,15.00,15.50,16.00,16.50,17.00,17.50,18.00,18.50,19.00,19.50,20.00]
def tick_index(price):
    if price<=1.01:return 0
    return min(range(len(TICKS)),key=lambda i:abs(TICKS[i]-price))

def parse(path):
    h=defaultdict(lambda:defaultdict(dict))
    with open(path,encoding='utf-8',errors='ignore',newline='') as f:
        for row in csv.reader(f):
            if len(row)<7 or row[0]=='Time':continue
            t,market,_,runner=row[:4]; vals={}
            for i in range(5,len(row)-1,2):
                k=row[i].replace(' {S}','').replace(' {HL}','').strip(); v=row[i+1].strip()
                try: vals[k]=float(v)
                except: vals[k]=v
            h[t][runner]=vals
    return [(t,h[t]) for t in sorted(h,key=lambda x:pd.to_datetime(x,dayfirst=True,errors='coerce'))]

def simulate(ticks,liability=15,max_start=20,drift_ticks=12):
    first={}; bad_start=set(); bets=[]; winner=None
    for t,runners in ticks:
        for horse,d in runners.items():
            price=d.get('back_price',0); rem=d.get('percentage_remaining',100); pos=d.get('position',0)
            if not isinstance(price,(int,float)) or price<=1.01:continue
            if horse not in first and rem>=95: first[horse]=price
            if horse in first and rem<95 and horse not in bad_start and pos>=4: bad_start.add(horse)
            if rem<1 and pos==1: winner=horse
    if not winner:return []
    fired=set()
    for t,runners in ticks:
        for horse,d in runners.items():
            if horse in fired or horse not in first or horse not in bad_start:continue
            price=d.get('back_price',0); rem=d.get('percentage_remaining',100); pos=d.get('position',0)
            speed5=d.get('average_speed_5secs',0); speed10=d.get('average_speed_10secs',0); stride=d.get('current_stride',0)
            if not isinstance(price,(int,float)) or price<=1.01 or rem<=0 or rem>=95 or pos<=0:continue
            if first[horse]>max_start or tick_index(price)-tick_index(first[horse])<drift_ticks:continue
            decel=(speed5>0 and speed10>0 and speed5<speed10*.92)
            weak_stride=(stride>0 and stride<22.0)
            if not (decel or weak_stride):continue
            bets.append({'horse':horse,'start_price':first[horse],'lay_price':price,'position':pos,'speed5':speed5,'speed10':speed10,'stride':stride,'time':t,'won_lay':horse!=winner,'pnl':(-liability if horse==winner else liability/(price-1)*.98)})
            fired.add(horse)
    return bets

def main(folder,liability=15):
    all_bets=[]; files=glob.glob(os.path.join(folder,'TPDzone*.csv'))
    for path in files:
        try: all_bets.extend(simulate(parse(path),liability))
        except Exception: pass
    n=len(all_bets); wins=sum(x['won_lay'] for x in all_bets); pnl=sum(x['pnl'] for x in all_bets)
    print(f'Files analysed: {len(files)}'); print(f'Lay bets: {n}'); print(f'Successful lays: {wins} ({100*wins/n:.2f}%)' if n else 'Successful lays: 0'); print(f'Liability: £{n*liability:.2f}'); print(f'PnL: £{pnl:.2f}'); print(f'Liability ROI: {100*pnl/(n*liability):.2f}%' if n else 'Liability ROI: 0.00%')
    if all_bets: pd.DataFrame(all_bets).to_csv(os.path.join(folder,'bad_start_12tick_lay_bets.csv'),index=False)

if __name__=='__main__':
 p=argparse.ArgumentParser();p.add_argument('folder');p.add_argument('--liability',type=float,default=15);a=p.parse_args();main(a.folder,a.liability)
