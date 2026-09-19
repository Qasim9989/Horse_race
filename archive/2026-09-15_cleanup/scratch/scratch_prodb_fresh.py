import pandas as pd
import pyodbc

c = pyodbc.connect(r"Driver={ODBC Driver 17 for SQL Server};"
                   r"Server=(localdb)\MSSQLLocalDB;Database=PRODB;"
                   r"Trusted_Connection=yes;")
d = pd.read_sql("SELECT RH_DateTime, RH_Name, RH_NoOfRunners FROM dbo.NEW_RH "
                "WHERE RH_DateTime >= '2026-01-01'", c)
d["m"] = d["RH_DateTime"].dt.to_period("M")
g = d.groupby("m").agg(races=("RH_DateTime", "size"),
                       last=("RH_DateTime", "max"))
print("PRODB races per month in 2026:")
print(g.to_string())
print("\nnewest 5 race rows:")
print(d.nlargest(5, "RH_DateTime")[["RH_DateTime", "RH_Name", "RH_NoOfRunners"]]
      .to_string(index=False))
c.close()
