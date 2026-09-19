# Racing Form & Master Lay System — Institutional Specification & Audit

---

## 🏛️ System Overview

The **Master Lay System** is a quantitative, zero-lookahead Betfair Exchange lay trading strategy that identifies structurally flawed runners in UK & Irish handicap races and executes fixed-liability lay orders when odds satisfy the value threshold ($\text{BSP} \le 6.00$).

---

## 📐 Scoring Rules & Mathematical Formula

### 1. Pre-Race Feature Signals (Strictly Prior-Race Data)

Every horse in the active race field is evaluated before the off using data known prior to race jump:

| Feature / Signal | Mathematical Condition | Score Weight | Rationale |
|---|---|:---:|---|
| **Pace Leader / Front-Runner** | $\text{LTO\_PaceAbbrev} \in (\text{'L'}, \text{'P'}, \text{'F'}, \text{'LEAD'})$ | **+3** | Early leaders dictate tempo and avoid traffic trouble. |
| **Sectional Upgrade #1** | $\text{LTO\_POSAFTUPG} = 1$ | **+3** | Fastest closing sectional speed in previous run. |
| **Sectional Upgrade #2** | $\text{LTO\_POSAFTUPG} = 2$ | **+1** | Above-average closing sectional speed. |
| **Fitness / Quick Return** | $\text{DSLR} \le 7 \lor \text{JockeyClaim} > 0$ | **+2** | Peak fitness retention or weight relief. |
| **Stride Decay (Fatigue)** | $(\text{LTO\_ASL} - \text{LTO\_SL\_Finish}) \ge 0.20\text{m}$ ($0.6\text{ft}$) | **-2** | Decaying stride length indicates cardiovascular exhaustion. |
| **Sectional Downgrade** | $\text{LTO\_POSAFTUPG} > 1$ | **-2** | Below-average closing speed relative to field. |
| **Discipline / Behavioral Issue** | $\text{Comments} \ni (\text{'dwelt'}, \text{'hung'}, \text{'pulled'}, \text{'slowly'})$ | **-2** | In-running behavioral flaws create race interference. |

$$\text{MasterScore} = \sum \text{Weights}$$

---

### 2. Full-Field Ranking & Selection Rules

1. **Rank Full Field First:** Every official runner in the handicap field is scored.
2. **Deterministic Tie-Breaking:** Runners are sorted by:
   $$\text{MasterScore (ASC)} \to \text{StrideDecay (DESC)} \to \text{DSLR (DESC)} \to \text{HorseName (ASC)}$$
   Each runner is assigned a deterministic rank: $\text{Rank\_Worst} \in [1, N]$.
3. **Qualifying Tiers:**
   * **Tier 1 Lay Target:** Exactly $\text{Rank\_Worst} = 1$ (Worst in race) $\land \text{MasterScore} < 0 \land \text{BSP} \le 6.00$.
   * **Tier 2 Lay Target:** Exactly $\text{Rank\_Worst} = 2$ (2nd Worst in race) $\land \text{MasterScore} < 0 \land \text{BSP} \le 6.00$.
4. **Price Filter:** Hard ceiling at **Betfair SP $\le 6.00$**.

---

### 3. Settlement & Fixed-Liability Staking Formula

* **Fixed Liability:** Risk is fixed at **£15.00** per bet.
* **If Horse Loses (Lay Win):**
  $$\text{Profit} = \left(\frac{£15.00}{\text{BSP} - 1.00}\right) \times 0.95$$
* **If Horse Wins (Lay Loss):**
  $$\text{Loss} = -£15.00$$
* **Non-Runner (NR / WD):**
  $$\text{PnL} = £0.00 \quad (\text{Strictly Voided})$$

---

## 📊 Full-Field Audited Results (2025–2026)

Tested across **96,221 official handicap finishers** with zero lookahead:

| Strategy Tier | Total Bets | Lay Wins | Win Rate | Net Profit (@ £15 Liability) | Strategy Net ROI |
|---|:---:|:---:|:---:|:---:|:---:|
| **Tier 1 (#1 Worst in Full Field)** | **2,276** | **1,739** | **76.41%** | **+£6,559.57** | **+19.21%** 🏆 |
| **Tier 2 (#2 Worst in Full Field)** | **1,994** | **1,512** | **75.83%** | **+£4,765.25** | **+15.93%** |
| **Combined Tier 1 & Tier 2** | **4,270** | **3,251** | **76.14%** | **+£11,324.82** | **+17.68%** |

---

## 🤖 5-Part Out-of-Sample & Machine Learning Validation

1. **In-Sample (2025) vs Out-of-Sample (2026):**
   * **2025 (In-Sample):** 3,141 bets | 76.82% Win Rate | **+18.38% Net ROI** (+£8,657.76)
   * **2026 (Pure Out-of-Sample):** 1,129 bets | 74.22% Win Rate | **+15.75% Net ROI** (+£2,667.06)
2. **Bankroll & Drawdown (£500 Starting Bankroll):**
   * Final Bankroll: **£11,824.82 (+2,265% Growth)**
   * Max Drawdown: **£256.84 (51.3% of initial bankroll)**
   * Max Losing Streak: **6 consecutive losses**
3. **Price Band Breakdown:**
   * **BSP 1.01 to 2.00:** 421 bets | 64.85% Win Rate | **+136.45% ROI** (+£8,616.60)
   * **BSP 2.01 to 3.00:** 828 bets | 72.22% Win Rate | **+17.64% ROI** (+£2,190.36)
   * **BSP 3.01 to 4.00:** 999 bets | 73.87% Win Rate | **+1.84% ROI** (+£275.04)
   * **BSP 4.01 to 5.00:** 1,047 bets | 80.04% Win Rate | **+1.75% ROI** (+£274.10)
   * **BSP 5.01 to 6.00:** 975 bets | 82.46% Win Rate | **-0.21% ROI** (-£31.29)
