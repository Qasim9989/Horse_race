import csv

filename = r'C:\Users\qasim\AppData\Roaming\Bet Angel\Bet Angel Professional\SVExports\TPDzone_Ascot 25th Jul - 14_55 7f Hcap.csv'

with open(filename, 'r') as f:
    reader = csv.reader(f)
    header = next(reader)
    
    unique_keys = set()
    
    row_count = 0
    section_names = set()
    for row in reader:
        row_count += 1
        if len(row) > 3:
            section_names.add(row[3])
        if row_count > 1000:
            break

print("Unique Section Names Found:")
for k in sorted(list(section_names))[:10]:
    print(f" - {k}")
