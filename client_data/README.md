# ServeWell IT Support — Synthetic Dataset

Synthetic data package for the **phData Applied AI Solutions Architect** project challenge. Candidates build an agentic IT support triage system for ServeWell Hospitality, a fictional multi-brand QSR chain.

---

## What's In This Package

```
data/
  stores.csv              120 India store locations (franchise + corporate)
  assets.csv              ~480 IT assets (POS, routers, printers, soft serve, kiosks)
  sla_matrix.csv          Priority × category SLA targets

kb/
  runbooks/               ~30 markdown runbooks (one per issue type)
  faq/                    6 category FAQs (10-15 Q&A each)
  sop/                    3 SOPs (escalation, shift-start, shift-end)
  system-specs/           8 system spec sheets + version-matrix.csv

tickets/
  raw/                    All ticket JSON files (INC-NNNNN.json)
  train/                  Training set ticket files
  train_index.csv         Training set index (~256 tickets)

labels/
  train_labels.json       Ground truth labels for train tickets

```

---

## Ticket Schema

Each `INC-NNNNN.json` file contains a rich ITSM ticket:

```json
{
  "ticket_id": "INC-00142",
  "store_id": "SW-0412",
  "store_name": "ServeWell - Koramangala",
  "submitted_by": "Maria Chen",
  "contact_phone": "+91-98765-43210",
  "date_opened": "2025-11-14T09:32:00+05:30",
  "priority": "P2",
  "category": "POS",
  "subcategory": "Terminal Startup",
  "subject": "POS terminal 2 won't start after power outage",
  "description": "...",
  "asset_id": "POS-0412-T2",
  "system_version": "FoodTech POS v4.2.1",
  "tags": ["pos", "startup", "power"],
  "escalation_flag": false,
  "related_tickets": [],
  "ticket_history": [...],
  "resolution_notes": null,
  "status": "Open"
}
```

---

## Label Schema

```json
{
  "ticket_id": "INC-00142",
  "correct_routing": "l1_guided",
  "relevant_kb_docs": ["runbooks/pos-startup-failure.md"],
  "should_escalate": false,
  "escalation_reason": null,
  "correct_resolution_summary": "Follow cold-start steps in pos-startup-failure.md...",
  "expected_agent_action": "retrieve_runbook_and_guide",
  "evaluator_notes": "Agent should retrieve pos-startup-failure.md before any remote actions."
}
```

`correct_routing` values: `l1_self_service`, `l1_guided`, `l2_escalation`, `non_it`, `needs_clarification`

---

## Loading the Data

```python
import json, csv
from pathlib import Path

ROOT = Path(".")   # adjust as needed

# Load all train tickets
train_tickets = []
with open(ROOT / "tickets/train_index.csv") as f:
    for row in csv.DictReader(f):
        with open(ROOT / "tickets" / row["filename"]) as tf:
            train_tickets.append(json.load(tf))

# Load train labels
with open(ROOT / "labels/train_labels.json") as f:
    train_labels = json.load(f)

# Load reference data
with open(ROOT / "data/stores.csv") as f:
    stores = list(csv.DictReader(f))

with open(ROOT / "data/assets.csv") as f:
    assets = list(csv.DictReader(f))
```

---

## Systems in the Dataset

| Domain        | Systems                                                     |
| ------------- | ----------------------------------------------------------- |
| POS           | FoodTech POS v4.2.x, v5.1.x · OrbitPOS v2.x                 |
| Soft Serve    | CreamTech SC-300 · CreamTech SC-500 · FrostyPro 800         |
| Online Orders | ServeWell Online Portal v3.x · Zomato / Swiggy integrations |
| Network       | NetLink NL-3000 · Cisco SG350                               |
| Printers      | EpsonTM-T88VI · StarMC Print v2                             |
| Kiosks        | ServeWell Kiosk v2.x · TouchPoint K1                        |

---

## Ticket Distribution

| Category        | Count | %   |
| --------------- | ----- | --- |
| POS             | ~80   | 25% |
| Soft Serve      | ~60   | 19% |
| Online Orders   | ~60   | 19% |
| Wi-Fi / Network | ~50   | 16% |
| Printers        | ~40   | 12% |
| Kiosks          | ~30   | 9%  |

Mix of thorough ↔ sparse, L1 self-service ↔ L2 escalation, P1 ↔ P4.  

