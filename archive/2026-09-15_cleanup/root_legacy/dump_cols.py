import pandas as pd
df = pd.read_excel(r'D:\RDB\Res\Apr21\Results - 01042021.xlsx')
with open('E:/Test/racing-form-system/rdb_cols.txt', 'w', encoding='utf-8') as f:
    f.write('\n'.join(str(c) for c in df.columns))
