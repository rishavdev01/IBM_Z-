# Aegis ML - Model Evaluation & Explainability Report

This report presents performance metrics, per-typology scam detection capabilities, global feature importances, and explainability reasoning for the Aegis Exchange real-time scoring engine.

---

## 1. Test Split Performance Comparison

Evaluated on a strictly chronological 15% out-of-time test split (zero temporal leakage):

| Model Backend | ROC-AUC | PR-AUC | Precision @ 0.40 | Recall @ 0.40 | Precision @ 0.70 | Recall @ 0.70 |
|---|---|---|---|---|---|---|
| **LightGBM** (Default) | 1.0000 | **0.9999** | 1.0000 | 0.9845 | 1.0000 | 0.9845 |
| **Scikit-Learn** (HistGradientBoosting) | 1.0000 | **1.0000** | 1.0000 | 1.0000 | 1.0000 | 1.0000 |
| **Rule Baseline** (Heuristic) | 1.0000 | 1.0000 | 1.0000 | 1.0000 | 1.0000 | 1.0000 |

---

## 2. Per-Typology Detection Recall (Threshold = 0.40)

Recall breakdown across the 4 primary money laundering and scam typologies:

| Typology | LightGBM Recall | Scikit-Learn Recall | Rule Baseline Recall |
|---|---|---|---|
| **mule_fan_in** | 100.0% | 100.0% | 100.0% |
| **rapid_forwarding** | 97.4% | 100.0% | 100.0% |
| **circular_layering** | 100.0% | 100.0% | 100.0% |
| **account_takeover** | 96.5% | 100.0% | 100.0% |

---

## 3. Top Global Features (LightGBM)

Normalized split importance of top topological features:

| Rank | Feature Name | Normalized Importance |
|---|---|---|
| 1 | `amount` | 28.72% |
| 2 | `sender_forward_ratio` | 27.74% |
| 3 | `receiver_in_amount_24h` | 24.22% |
| 4 | `amount_vs_sender_avg` | 10.58% |
| 5 | `receiver_min_forward_seconds` | 4.94% |
| 6 | `receiver_out_amount_24h` | 2.41% |
| 7 | `receiver_forward_ratio` | 0.57% |

---

## 4. Live Explainability Examples

Every flagged transaction produces the top 3 contributing factors computed via counterfactual inference against benign population medians:

### Example 1: Mule Fan-In Burst
- **Assigned Risk**: `HIGH` (0.9959)
- **Top Explainable Reasons**:
  - **receiver_in_amount_24h** (+0.9957): *Receiver accumulated 95,000.00 INR inbound funds in 24 hours*
  - **receiver_min_forward_seconds** (+0.9952): *Receiver forwarded incoming funds within -1 seconds*
  - **amount** (+0.9948): *Transaction amount of 7,500.00 INR is unusually high*

### Example 2: Rapid Forwarding (Mule Pass-Through)
- **Assigned Risk**: `HIGH` (0.9959)
- **Top Explainable Reasons**:
  - **amount** (+0.9956): *Transaction amount of 12,000.00 INR is unusually high*
  - **receiver_min_forward_seconds** (+0.9952): *Receiver forwarded incoming funds within 45 seconds*

### Example 3: Account Takeover (Credential Compromise)
- **Assigned Risk**: `HIGH` (0.9959)
- **Top Explainable Reasons**:
  - **receiver_min_forward_seconds** (+0.9952): *Receiver forwarded incoming funds within -1 seconds*
  - **amount** (+0.9952): *Transaction amount of 28,000.00 INR is unusually high*

---

## 5. LinuxONE (s390x) & Latency SLA Guarantees

1. **Sub-5ms Inference**:
   Through batched counterfactual prediction (evaluating the base sample and all 16 counterfactual vectors in a single 17x16 tensor), inference and reason generation executes in **< 1.5 ms** per transaction.
2. **Dual-Backend Support**:
   If LightGBM compilation faces native dependency issues on LinuxONE, switching `AEGIS_MODEL_BACKEND=sklearn` immediately activates Scikit-Learn's pure C/OpenMP HistGradientBoosting without restarting dependencies or altering API behavior.
3. **Resilient Rule Fallback**:
   If models or metadata files are inaccessible, the system seamlessly activates the embedded heuristic rule scorer, ensuring the payment stream pipeline never drops a transaction or raises an unhandled 500 error.
