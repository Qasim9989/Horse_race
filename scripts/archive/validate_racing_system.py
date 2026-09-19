from pathlib import Path
import re

BASE = Path(r"E:\Test\racing-form-system\scripts")

FILES = {
    "custom_date_audit": BASE / "custom_date_audit.py",
    "daily_lay_scanner": BASE / "daily_lay_scanner.py",
    "audit_1year": BASE / "audit_scraped_vs_proform_1year.py",
    "ml_validator": BASE / "ml_weight_and_oos_validator.py",
}

def read_text(path):
    return path.read_text(encoding="utf-8", errors="ignore")

def has(pattern, text, flags=re.I | re.S):
    return re.search(pattern, text, flags) is not None

def report(name, ok, detail):
    status = "PASS" if ok else "FAIL"
    print(f"[{status}] {name:<28} {detail}")

def check_custom_date_audit(text):
    print("\n=== custom_date_audit.py ===")

    report(
        "Exact timestamp OUTER APPLY",
        has(r"OUTER\s+APPLY", text) and has(r"R2\.RH_DateTime\s*<\s*RH\.RH_DateTime", text),
        "Needs exact point-in-time prior-race lookup"
    )

    report(
        "Ranks active runners only",
        has(r"active_indices\s*=\s*df\[\s*~df\['IsNonRunner'\]\s*\]\.index", text) and
        has(r"df\.loc\[active_sorted\.index,\s*'RankWorst'\]", text),
        "RankWorst should be assigned only to active runners"
    )

    report(
        "Exports BetPlaced",
        has(r"'BetPlaced'\s*:\s*bet_placed", text),
        "CSV/XLSX should explicitly store BetPlaced"
    )

    report(
        "Exports LTO timestamp",
        has(r"'LTO_DateTime'\s*:", text),
        "CSV/XLSX should include LTO_DateTime"
    )

    losing_branch = has(r"if\s+runner\['IsWinner'\]\s*:\s*.*?pnl_val\s*=\s*-15", text)
    total_pnl_negative = has(r"total_pnl\s*\+=\s*pnl_val", text)
    report(
        "Losing lays hit total_pnl",
        losing_branch and total_pnl_negative,
        "Check indentation: total_pnl += pnl_val must run for both win and loss bets"
    )

def check_daily_lay_scanner(text):
    print("\n=== daily_lay_scanner.py ===")

    exact_ts = (
        has(r"OUTER\s+APPLY", text) and
        has(r"R2\.RH_DateTime\s*<\s*(RH\.RH_DateTime|T\.TargetDateTime)", text)
    )

    date_cutoff = has(r"RH\.RH_DateTime\s*<\s*'\{target_date_str\}'", text)

    report(
        "Exact timestamp live lookup",
        exact_ts,
        "Live scanner should ideally use exact timestamp logic like the audit script"
    )

    report(
        "Old date-cutoff logic absent",
        not date_cutoff,
        "Scanner still appears to use day-level cutoff, which can diverge from audit logic"
    )

    report(
        "Deterministic ranking",
        has(r"sort_values\(\s*\[\s*'score'\s*,\s*'stride_decay'\s*,\s*'dslr'\s*,\s*'horse'\s*\]", text),
        "Expected deterministic runner sort"
    )

    report(
        "Tier rule = top 2 and score < 0",
        has(r"rank_worst'\]\s*<=\s*2", text) and has(r"score'\]\s*<\s*0", text),
        "Expected Tier 1/2 qualification rule"
    )

def check_backtests(name, text):
    print(f"\n=== {name} ===")

    report(
        "Exact timestamp prior lookup",
        has(r"OUTER\s+APPLY", text) and has(r"R2\.RH_DateTime\s*<\s*RH\.RH_DateTime", text),
        "Backtest should use strict prior-race point-in-time lookup"
    )

    report(
        "Deterministic full-field rank",
        has(r"sort_values\(", text) and has(r"Rank_Worst", text),
        "Expected deterministic field ranking"
    )

    report(
        "BSP filter after ranking logic present",
        has(r"Rank_Worst", text) and has(r"BSP|HIR_BSP", text) and has(r"<=\s*6\.00|<=\s*6\.0", text),
        "Check that BSP is a betting filter, not a pre-ranking field filter"
    )

    report(
        "Fixed lay settlement",
        has(r"\(15\.0\s*/\s*\(.*?-\s*1\.0\)\)\s*\*\s*0\.95", text) and has(r"-15\.00", text),
        "Expected £15 liability lay settlement formula"
    )

def main():
    texts = {}
    for k, path in FILES.items():
        if not path.exists():
            print(f"[MISSING] {path}")
            continue
        texts[k] = read_text(path)

    if "custom_date_audit" in texts:
        check_custom_date_audit(texts["custom_date_audit"])

    if "daily_lay_scanner" in texts:
        check_daily_lay_scanner(texts["daily_lay_scanner"])

    if "audit_1year" in texts:
        check_backtests("audit_scraped_vs_proform_1year.py", texts["audit_1year"])

    if "ml_validator" in texts:
        check_backtests("ml_weight_and_oos_validator.py", texts["ml_validator"])

if __name__ == "__main__":
    main()