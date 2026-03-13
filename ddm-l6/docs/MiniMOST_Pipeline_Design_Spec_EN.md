# MiniMOST Automated Time Analysis Pipeline - Design Specification

**Version**: 1.0  
**Date**: 2025-11-30  
**Status**: Spec Ready  
**Author**: WWMT AI & IE Engineering Team

---

## 1. Executive Summary

### 1.1 Purpose
This document defines the design specification for an automated pipeline that generates MiniMOST time analysis from SOP documents and work operation videos. The system combines computer vision (Vision Stream) and natural language processing (Logic Stream) to produce standardized time measurement data.

### 1.2 Scope
- **Input**: SOP documents (PDF/Image), Operation videos (MP4), Configuration data
- **Output**: MiniMOST CSV analysis tables, Difference reports, Validation results
- **Target Accuracy**: 90-95% TMU calculation accuracy with human-in-the-loop validation

### 1.3 Key Features
| Feature              | Description                        |
|----------------------|------------------------------------|
| Dual-Stream Analysis | Vision + Logic parallel processing |
| Multi-modal Fusion   | Combine video data with SOP rules  |
| Automated Validation | Rule-based quality checking        |
| Human-in-the-loop    | IE engineer review interface       |

---

## 2. System Architecture

### 2.1 High-Level Architecture

```
┌─────────────────────────────────────────────────────────────────────────┐
│                         DATA INGESTION LAYER                            │
├─────────────────────────────────────────────────────────────────────────┤
│  ┌──────────────┐  ┌──────────────┐  ┌──────────────┐  ┌──────────────┐ │
│  │ SOP Parser   │  │ Video Pre-   │  │ Config       │  │ Component    │ │
│  │ (OCR/Layout) │  │ processor    │  │ Lookup       │  │ DB           │ │
│  └──────┬───────┘  └──────┬───────┘  └──────┬───────┘  └──────┬───────┘ │
└─────────┼─────────────────┼─────────────────┼─────────────────┼─────────┘
          │                 │                 │                 │
          ▼                 ▼                 ▼                 ▼
┌─────────────────────────────────────────────────────────────────────────┐
│                       DUAL-STREAM ANALYSIS LAYER                        │
├─────────────────────────────────────────────────────────────────────────┤
│  ┌─────────────────────────────┐    ┌─────────────────────────────────┐ │
│  │      VISION STREAM          │    │        LOGIC STREAM             │ │
│  ├─────────────────────────────┤    ├─────────────────────────────────┤ │
│  │ • YOLOv8-Pose (Glove Hand)  │    │ • LLM (Claude/GPT-4o)           │ │
│  │ • Action Recognition        │    │ • RAG (ddm_structure.csv)       │ │
│  │ • Distance Estimation       │    │ • SOP Step Mapping              │ │
│  │ • SIMO Detection            │    │ • Parameter Pre-fill            │ │
│  └──────────────┬──────────────┘    └──────────────┬──────────────────┘ │
└─────────────────┼──────────────────────────────────┼────────────────────┘
                  │                                  │
                  ▼                                  ▼
┌─────────────────────────────────────────────────────────────────────────┐
│                        FUSION & CALCULATION LAYER                       │
├─────────────────────────────────────────────────────────────────────────┤
│  ┌──────────────────┐  ┌──────────────────┐  ┌──────────────────────┐   │
│  │ Priority Resolver│  │ TMU Calculator   │  │ Diff Generator       │   │
│  │ Video > DB > SOP │  │ MOST Sequence    │  │ Video vs SOP         │   │
│  └──────────────────┘  └──────────────────┘  └──────────────────────┘   │
└─────────────────────────────────────────────────────────────────────────┘
                                    │
                                    ▼
┌─────────────────────────────────────────────────────────────────────────┐
│                        VALIDATION & OUTPUT LAYER                        │
├─────────────────────────────────────────────────────────────────────────┤
│  ┌──────────────────┐  ┌──────────────────┐  ┌──────────────────────┐   │
│  │ Rule Validator   │  │ IE Review UI     │  │ Output Generator     │   │
│  │ VAL-01 ~ VAL-09  │  │ Video + CSV      │  │ CSV / JSON / Report  │   │
│  └──────────────────┘  └──────────────────┘  └──────────────────────┘   │
└─────────────────────────────────────────────────────────────────────────┘
```

### 2.2 Data Flow

```
Video (MP4) ──┐
              ├──► Pre-check Gate ──► Vision Stream ──┐
SOP (PDF) ────┘                                       │
                                                      ├──► Fusion Engine ──► Validation ──► Output
Config Lookup ──► Logic Stream ──────────────────────┘
Component DB ──┘
```

---

## 3. Data Schema Specification

### 3.1 Table 1: Video Ground Truth (v3.2)

**Purpose**: Record AI vision analysis results (As-Is actual measurement data)

| Field           | Type    | Required | Description                         | Example                                                                     |
|-----------------|---------|----------|-------------------------------------|-----------------------------------------------------------------------------|
| `action_id`     | String  | Yes      | Unique action identifier            | `V-01`, `V-05.01`                                                           |
| `time_sec`      | Object  | Yes      | Start/End time in seconds           | `{"start": 0.0, "end": 2.0}`                                                |
| `key_frames`    | Object  | Yes      | Key frame markers (S/C/R/E)         | `{"start": 0, "contact": 15, "release": null, "end": 60}`                   |
| `cycle_group`   | String  | No       | Cycle group ID for repeated actions | `CG-01`                                                                     |
| `sop_id`        | String  | Yes      | Reference to SOP Logic table        | `S-02b`                                                                     |
| `hand`          | Enum    | Yes      | Hand usage                          | `RH`, `LH`, `BH`                                                            |
| `object_state`  | Object  | Yes      | Before/After object state           | `{"before": {"DIMM": "tray"}, "after": {"DIMM": "cleaned"}}`                |
| `most_sequence` | Object  | Yes      | MOST parameters with source         | `{"A": {"value": 1, "source": "video"}, "G": {"value": 1, "source": "db"}}` |
| `tmu`           | Integer | No       | Time Measurement Unit               | `50`, `220`                                                                 |
| `simo_overlap`  | String  | No       | SIMO overlap duration               | `1.8s (LH Hold)`                                                            |
| `confidence`    | Float   | Yes      | AI confidence score (0-1)           | `0.92`                                                                      |
| `review_status` | Enum    | Yes      | Review state                        | `Pending`, `Approved`, `Review`                                             |
| `anomaly`       | Object  | No       | Detected anomaly                    | `{"type": "Method Change", "severity": "Info"}`                             |

**JSON Schema**:
```json
{
  "$schema": "http://json-schema.org/draft-07/schema#",
  "type": "object",
  "required": ["action_id", "time_sec", "key_frames", "sop_id", "hand", "object_state", "most_sequence", "confidence", "review_status"],
  "properties": {
    "action_id": {"type": "string", "pattern": "^V-[0-9]{2}(\\.[0-9]{2})?$"},
    "time_sec": {
      "type": "object",
      "properties": {
        "start": {"type": "number"},
        "end": {"type": "number"}
      }
    },
    "confidence": {"type": "number", "minimum": 0, "maximum": 1},
    "review_status": {"enum": ["Pending", "Approved", "Review", "Rejected"]}
  }
}
```

### 3.2 Table 2: SOP Logic (v3.2)

**Purpose**: Define standard operation logic (To-Be standard data)

| Field             | Type    | Required | Description                 | Example                              |
|-------------------|---------|----------|-----------------------------|--------------------------------------|
| `sop_id`          | String  | Yes      | Unique SOP step identifier  | `S-02a`, `S-02b`                     |
| `step_name`       | String  | Yes      | Step name in Chinese        | `打開卡扣`                           |
| `component_id`    | String  | Yes      | Reference to Component DB   | `DIMM_DDR5`                          |
| `pre_condition`   | Object  | Yes      | Required state before step  | `{"Lock": "closed"}`                 |
| `post_condition`  | Object  | Yes      | Expected state after step   | `{"Lock": "open"}`                   |
| `freq_rule`       | String  | Yes      | Frequency rule              | `Fixed: 1`, `Config: DIMM`           |
| `most_params`     | String  | Yes      | Standard MOST parameters    | `A3 G1 M1`                           |
| `validator_rules` | String  | Yes      | Validation logic            | `未全開即插=順序錯誤`                |
| `depends_on`      | String  | No       | Dependency reference        | `S-02a`                              |
| `optional`        | Boolean | Yes      | Whether step can be skipped | `false`                              |
| `severity`        | Enum    | No       | Error severity level        | `Info`, `Warning`, `Error`           |
| `machine_ref`     | Object  | No       | Equipment reference         | `{"eq_id": "EQ_P1", "cycle_sec": 8}` |

### 3.3 Table 3: Diff Report (v3.2)

**Purpose**: Track differences between Video (As-Is) and SOP (To-Be)

| Field               | Type    | Required | Description              | Example                                                        |
|---------------------|---------|----------|--------------------------|----------------------------------------------------------------|
| `diff_id`           | String  | Yes      | Unique diff identifier   | `D-001`                                                        |
| `sop_id`            | String  | Yes      | Related SOP step         | `S-01`                                                         |
| `related_actions`   | Array   | Yes      | Related Video GT actions | `["V-02"]`                                                     |
| `requirement`       | String  | Yes      | SOP requirement          | `Unpack+Bin`                                                   |
| `observation`       | String  | Yes      | Video observation        | `Direct Place`                                                 |
| `diff_type`         | Enum    | Yes      | Difference category      | `Process Skip`, `Method Change`, `Config Diff`, `Missing Data` |
| `tmu_impact`        | Integer | No       | TMU difference           | `-60`                                                          |
| `tmu_impact_pct`    | Float   | No       | TMU percentage change    | `-21.0`                                                        |
| `priority`          | Enum    | Yes      | Priority level           | `High`, `Medium`, `Low`                                        |
| `resolution_status` | Enum    | Yes      | Resolution state         | `Open`, `In Review`, `Resolved`                                |
| `resolved_by`       | String  | No       | Resolver identifier      | `IE_Lead`                                                      |
| `resolved_date`     | Date    | No       | Resolution date          | `2025-11-30`                                                   |
| `evidence`          | String  | No       | Evidence file path       | `./clips/v02.mp4`                                              |

### 3.4 Table 4: Component Difficulty DB (v1.2)

**Purpose**: Provide physical characteristics that cannot be detected by vision

| Field           | Type    | Required | Description                 | Example                 |
|-----------------|---------|----------|-----------------------------|-------------------------|
| `component_id`  | String  | Yes      | Unique component identifier | `DIMM_DDR5`             |
| `name`          | String  | Yes      | Component name              | `DDR5 RAM`              |
| `dimensions_mm` | String  | No       | Physical dimensions         | `133x31x4`              |
| `weight_g`      | Integer | No       | Weight in grams             | `40`                    |
| `default_G`     | String  | No       | Default G parameter         | `G1`                    |
| `default_P`     | String  | No       | Default P parameter         | `P3`                    |
| `default_M`     | String  | No       | Default M parameter         | `M1`                    |
| `default_I`     | String  | No       | Default I parameter         | `I3`                    |
| `typical_A`     | String  | No       | Typical A range             | `A3-A10`                |
| `fits_in`       | String  | No       | Compatible slot/holder      | `SLOT_DDR5`             |
| `force_level`   | Enum    | No       | Required force level        | `Low`, `Medium`, `High` |

### 3.5 Table 5: Config Lookup (v1.1)

**Purpose**: Dynamic configuration lookup for Freq and SKU validation

| Field            | Type    | Required | Description        | Example         |
|------------------|---------|----------|--------------------|-----------------|
| `model`          | String  | Yes      | Product model      | `K860G6`        |
| `sku`            | String  | Yes      | SKU identifier     | `W*3558 (Full)` |
| `cpu_count`      | Integer | Yes      | Number of CPUs     | `2`             |
| `dimm_per_cpu`   | Integer | Yes      | DIMM slots per CPU | `16`            |
| `total_dimm`     | Integer | Yes      | Total DIMM count   | `32`            |
| `ssd_count`      | Integer | No       | SSD count          | `4`             |
| `psu_type`       | String  | No       | PSU specification  | `1600W`         |
| `effective_date` | Date    | Yes      | Effective date     | `2025-01-01`    |

---

## 4. Validation Rules

### 4.1 P0 Validation Rules (Mandatory)

| Rule ID    | Name               | Condition                                                                            | Error Action                |
|------------|--------------------|--------------------------------------------------------------------------------------|-----------------------------|
| **VAL-01** | Cycle Completeness | Each `CG-XX` must have exactly 3 actions (Get → Align → Press)                       | Mark as `Data Incomplete`   |
| **VAL-04** | Freq Consistency   | `COUNT(DISTINCT cycle_group)` must equal `Config Lookup.total_dimm` or generate Diff | Auto-generate `Config Diff` |
| **VAL-07** | Unique SOP ID      | `SELECT COUNT(*) FROM SOP_Logic GROUP BY sop_id HAVING COUNT(*) > 1` must return 0   | Reject import               |
| **VAL-09** | Component Coverage | All `object` in Video GT must exist in Component DB                                  | Mark as `Component Missing` |

### 4.2 P1 Validation Rules (Recommended)

| Rule ID    | Name               | Condition                                                     | Error Action             |
|------------|--------------------|---------------------------------------------------------------|--------------------------|
| **VAL-02** | TMU Reasonability  | Single action TMU must be in range [10, 500]                  | Flag for review          |
| **VAL-05** | Time Continuity    | `action[n].end_sec ≈ action[n+1].start_sec` (±0.5s tolerance) | Warning                  |
| **VAL-06** | Confidence Check   | If `confidence < 0.7` AND `review_status != 'Approved'`       | Auto-set `Review` status |
| **VAL-08** | Evidence Existence | `evidence` URL must be accessible                             | Warning                  |
| **VAL-10** | Time Ordering      | `V-[n].end_sec < V-[n+1].start_sec` (±0.1s tolerance)         | Warning                  |

---

## 5. Processing Pipeline

### 5.1 Stage I: Data Ingestion

| Step | Input           | Process                                    | Output              | Duration     |
|------|-----------------|--------------------------------------------|---------------------|--------------|
| 1.1  | Video (MP4)     | Downsample to 5-10 fps, extract key frames | Frame sequence      | ~2 min/video |
| 1.2  | SOP (PDF/Image) | OCR + Layout analysis                      | Structured SOP JSON | ~30 sec/page |
| 1.3  | Config          | Query Config Lookup by Model+SKU           | Freq, DIMM count    | <1 sec       |

**Pre-check Gate**:
- Video resolution ≥ 1080p
- SOP required fields present
- Version number matching

### 5.2 Stage II: Dual-Stream Analysis

#### Vision Stream
| Component           | Technology                       | Input             | Output                |
|---------------------|----------------------------------|-------------------|-----------------------|
| Hand Detection      | YOLOv8-Pose (Custom Glove Model) | Video frames      | Hand bounding boxes   |
| Object Detection    | YOLOv8                           | Video frames      | Object bounding boxes |
| Action Recognition  | Temporal CNN                     | Frame sequence    | Action type + timing  |
| Distance Estimation | Reference Object Scale           | Hand trajectory   | Distance in cm        |
| SIMO Detection      | Dual-hand tracking               | Hand trajectories | Overlap duration      |

#### Logic Stream
| Component          | Technology          | Input        | Output                 |
|--------------------|---------------------|--------------|------------------------|
| SOP Parser         | LLM + RAG           | SOP text     | Structured steps       |
| Parameter Pre-fill | Component DB lookup | Component ID | Default G/P/M/I values |
| Syntax Mapping     | ddm_structure.csv   | Action verb  | MOST sequence template |

### 5.3 Stage III: Fusion & Calculation

**Priority Resolution**:
```
Priority: Video Measurement > Component DB > SOP Text Inference
```

| Parameter     | Video Source                   | DB Source             | Fallback         |
|---------------|--------------------------------|-----------------------|------------------|
| A (Distance)  | Hand trajectory (cm → A value) | typical_A             | SOP inference    |
| G (Get)       | Object size detection          | default_G             | G1               |
| P (Placement) | Visual difficulty              | default_P             | SOP verb mapping |
| M (Move)      | Movement type                  | default_M             | SOP verb mapping |
| X (Process)   | Stopwatch timing               | machine_ref.cycle_sec | Fixed value      |
| I (Inspect)   | Static > 1 sec detection       | default_I             | SOP keyword      |

**TMU Calculation**:
```
TMU = (A1 + B + G + A2 + B + P + A3) × 10 × Freq
Where: 1 TMU = 0.036 seconds
```

### 5.4 Stage IV: Validation & Output

| Step | Process                   | Output                |
|------|---------------------------|-----------------------|
| 4.1  | Run VAL-01 ~ VAL-09       | Validation report     |
| 4.2  | Generate Diff Report      | D-001 ~ D-XXX entries |
| 4.3  | Flag low confidence items | Review queue          |
| 4.4  | Export results            | CSV, JSON, PDF report |

---

## 6. User Interface Requirements

### 6.1 IE Review Interface

**Layout**:
```
┌─────────────────────────────────────────────────────────────────────┐
│  [Video Player]                    │  [Generated CSV Table]        │
│  ┌─────────────────────────────┐   │  ┌─────────────────────────┐  │
│  │                             │   │  │ V-01 │ 0.0-2.0 │ RH │...│  │
│  │     Video Playback          │   │  │ V-02 │ 2.0-5.0 │ BH │...│  │
│  │     with Frame Counter      │   │  │ V-03 │ 6.0-13.0│ RH │...│  │
│  │                             │   │  │ ★V-04│13.0-15.0│ BH │...│  │ ← Highlighted
│  └─────────────────────────────┘   │  └─────────────────────────┘  │
│  [Timeline with Action Markers]    │  [Parameter Editor]           │
├─────────────────────────────────────┴───────────────────────────────┤
│  [Validation Warnings]  [Diff Report]  [Export Button]             │
└─────────────────────────────────────────────────────────────────────┘
```

**Key Features**:
- Click CSV row → Jump to video timestamp
- Highlight `Review` status items
- One-click accept AI suggestion
- Batch edit support
- Keyboard shortcuts (N=Next, A=Accept, R=Reject)

### 6.2 Dashboard Metrics

| Metric           | Description                   | Target  |
|------------------|-------------------------------|---------|
| Processing Time  | Time per video                | < 5 min |
| Accuracy Rate    | TMU deviation < 15%           | > 90%   |
| Review Rate      | Items requiring manual review | < 20%   |
| Confidence Score | Average AI confidence         | > 0.85  |

---

## 7. Technology Stack

| Layer          | Component          | Technology                       |
|----------------|--------------------|----------------------------------|
| **Vision**     | Object Detection   | YOLOv8 (Ultralytics)             |
| **Vision**     | Pose Estimation    | YOLOv8-Pose (Custom trained)     |
| **Vision**     | Action Recognition | MS-TCN++ / Temporal Shift Module |
| **Logic**      | LLM                | Claude 3.5 Sonnet / GPT-4o       |
| **Logic**      | RAG                | LangChain + ChromaDB             |
| **Backend**    | API                | FastAPI (Python 3.9+)            |
| **Backend**    | Task Queue         | Celery + Redis                   |
| **Database**   | Primary            | PostgreSQL                       |
| **Frontend**   | UI                 | Streamlit / Gradio               |
| **Annotation** | Labeling Tool      | CVAT / Roboflow                  |

---

## 8. Development Roadmap

| Phase                       | Duration   | Deliverables                                                                 |
|-----------------------------|------------|------------------------------------------------------------------------------|
| **Phase 1: Preparation**    | Week 1-2   | Component_Difficulty_DB, Config_Lookup, JSON Schema, Ground Truth annotation |
| **Phase 2: Prototype**      | Week 3-4   | IE Review UI v0.1, TMU error calculation, Checkpoint mechanism               |
| **Phase 3: Vision Core**    | Week 5-6   | Glove hand detection model (mAP > 0.8), Distance estimation module           |
| **Phase 4: Logic & Fusion** | Week 7-9   | LLM prompt optimization, Multi-modal fusion engine                           |
| **Phase 5: Validation**     | Week 10-12 | End-to-end testing, Model optimization, Deployment                           |

---

## 9. Risk Assessment

| Risk                            | Probability | Impact | Mitigation                                             |
|---------------------------------|-------------|--------|--------------------------------------------------------|
| Poor video quality              | Medium      | High   | Define shooting SOP (angle/lighting/resolution)        |
| Low glove detection accuracy    | High        | Medium | Prioritize Component DB defaults, vision as supplement |
| Insufficient IE involvement     | Medium      | High   | Start with small PoC, demonstrate value before scaling |
| Unstable LLM output format      | Medium      | Medium | Use Function Calling / Structured Output               |
| Inconsistent cross-station data | Medium      | Medium | Establish station boundary definition document         |

---

## 10. Appendices

### Appendix A: MOST Parameter Reference

| Parameter | Index | Distance/Description |
|-----------|-------|----------------------|
| A0        | 0     | ≤2.5 cm              |
| A1        | 1     | ≤5 cm                |
| A3        | 3     | ≤10 cm               |
| A6        | 6     | ≤20 cm               |
| A10       | 10    | ≤35 cm               |
| A16       | 16    | ≤60 cm               |
| A24       | 24    | >60 cm               |

### Appendix B: Confidence Threshold Guidelines

| Confidence | Status | Action           |
|------------|--------|------------------|
| ≥ 0.9      | High   | Auto-approve     |
| 0.7 - 0.9  | Medium | Pending review   |
| < 0.7      | Low    | Mandatory review |

### Appendix C: Diff Type Classification

| Type           | Description                    | Typical Cause               |
|----------------|--------------------------------|-----------------------------|
| Process Skip   | SOP step not observed in video | Upstream station completed  |
| Method Change  | Different method than SOP      | Operator optimization       |
| Config Diff    | Frequency mismatch             | Different SKU configuration |
| Missing Data   | Video incomplete               | Recording stopped early     |
| Sequence Error | Wrong step order               | Training issue              |

---

**Document Control**

| Version | Date       | Author                        | Changes         |
|---------|------------|-------------------------------|-----------------|
| 1.0     | 2025-11-30 | WWMT AI & IE Engineering Team | Initial release |

---

*End of Document*
