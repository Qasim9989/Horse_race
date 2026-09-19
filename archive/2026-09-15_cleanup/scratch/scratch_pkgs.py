import importlib.util as u

for m in ("plotly", "matplotlib", "seaborn", "jinja2", "streamlit", "dash",
          "altair", "bokeh", "openpyxl", "pandas", "numpy", "flask"):
    print(f"{m:12s}", "YES" if u.find_spec(m) else "-")
