# Architecture Diagrams

This document contains Mermaid.js diagrams that describe the core architecture of the Enterprise MVA Platform. All diagrams render natively on GitHub.

---

## 1. End-to-End Data Flow

The following diagram traces an anomaly event from the factory floor through the full AI reasoning pipeline and back to physical robotic dispatch.

```mermaid
flowchart TD
    A([🏭 Factory Floor\nSensor Stream]) -->|yield_rate, cycle_time_sec\nthroughput_uph| B

    subgraph INGEST ["📥 Data Ingestion"]
        B[Agentic ETL\nDataNormalizationAgent\ndata_ops/agentic_etl.py]
        B -->|TimeSeriesRecord\nPydantic schema| C[Temporal Vector Store\ntools/temporal_rag.py]
    end

    subgraph WATCHDOG ["🔭 IoT Watchdog  —  events/iot_watchdog.py"]
        D[simulate_factory_stream\nasync tick loop]
        E{AnomalyDetector\nyield < 90% for N ticks?}
        D -->|FactoryStreamTick\nevery 5 s| E
    end

    C -.->|historical context| E
    E -->|No anomaly| D
    E -->|AnomalyEvent fires\nper-machine cooldown lock| F

    subgraph SWARM ["🤝 Multi-Agent Swarm Debate  —  agents/debate_room.py"]
        F[run_debate_session]
        F --> G[CostOptimizationAgent\nTurn 1 — Plan A]
        F --> H[QualityAndTimeAgent\nTurn 2 — Plan B]
        G & H --> I{ConsensusJudgeAgent\nFinal Synthesis}

        subgraph EVIDENCE ["Supporting Evidence  async gather"]
            J[TemporalAnalystAgent\nagents/temporal_analyst.py\nIQR + linear regression]
            K[VisionInspectorAgent\nagents/vision_inspector.py\nVLM frame analysis]
        end

        H -.->|asyncio.gather| J & K
        J & K -.->|TemporalAnalysis\nVisionAnalysisResult| I
    end

    I -->|ConsensusResult +\nEd25519 signature| L

    subgraph HITL ["🧑‍💼 Human-in-the-Loop Gate"]
        L[broadcast_emergency_proposal\nSSE → Mission Control UI]
        L --> M{Operator Approval\nPendingAction queue}
    end

    M -->|REJECTED| N([📋 Audit Log Only\nTamperEvidentAuditLog])
    M -->|APPROVED| O

    subgraph DISPATCH ["🤖 Cyber-Physical Dispatch  —  robotics/ros2_bridge.py"]
        O[ROS2CommandTranslator\nNLU keyword mapping]
        O --> P{Command Type}
        P -->|CMD_VEL / NAVIGATION_GOAL| Q[AGV / Mobile Robot]
        P -->|JOINT_TARGET / GRIPPER| R[Robotic Arm  6-DOF]
        P -->|ESTOP emergency override| S[All Assets\nImmediate halt]
    end

    O --> N
    Q & R & S --> N

    subgraph OBS ["📡 Observability"]
        T[LLMOps Telemetry\ntelemetry.py]
        U[Langfuse / OpenTelemetry OTLP]
        T --> U
    end

    F -.->|agent_span traces| T
    I -.->|agent_span traces| T

    style SWARM fill:#1a1a2e,color:#e0e0e0,stroke:#4a90d9
    style HITL fill:#2d1b1b,color:#e0e0e0,stroke:#d94a4a
    style DISPATCH fill:#1b2d1b,color:#e0e0e0,stroke:#4ad94a
    style INGEST fill:#1b1b2d,color:#e0e0e0,stroke:#9a4ad9
    style WATCHDOG fill:#2d2d1b,color:#e0e0e0,stroke:#d9d94a
    style OBS fill:#1b2b2d,color:#e0e0e0,stroke:#4ad9d9
    style EVIDENCE fill:#2a1e3a,color:#e0e0e0,stroke:#7a4ad9
```

---

## 2. Kubernetes Infrastructure

The following diagram shows the production K8s topology in the `mva-platform` namespace, including ingress, service mesh, stateful storage, and the telemetry sidecar.

```mermaid
graph TB
    subgraph INTERNET ["🌐 External Traffic"]
        USR([Browser / API Client])
    end

    subgraph K8S ["☸️ Kubernetes Cluster — namespace: mva-platform"]

        subgraph INGRESS ["🔀 Ingress Layer"]
            ING[Nginx Ingress Controller\n/  → Mission Control SPA\n/api/* → Backend Service]
        end

        subgraph FRONTEND ["⚛️ Frontend Tier"]
            MC[Mission Control\nDeployment\nNginx static + SPA\nReplicas: 2]
        end

        subgraph BACKEND ["🐍 Backend Tier"]
            API[mva-backend\nDeployment\nFastAPI + Uvicorn\nReplicas: 2–4 HPA]
            API_SVC[mva-backend\nService\nClusterIP :8000]
        end

        subgraph CACHE ["⚡ Cache & Message Bus"]
            REDIS[Redis Cluster\nStatefulSet\n3 nodes\nmva-redis-headless]
            REDIS_SVC[redis\nService\nClusterIP :6379]
        end

        subgraph STORAGE ["💾 Persistent Storage"]
            PVC_AUDIT[audit-log-pvc\nPersistentVolumeClaim\nReadWriteOnce\n10 Gi — audit_chain.jsonl]
            PVC_DB[db-pvc\nPersistentVolumeClaim\nReadWriteOnce\n5 Gi — db_persistent.json]
        end

        subgraph TELEMETRY ["📡 Observability Stack"]
            OTEL[OpenTelemetry\nCollector\nDaemonSet]
            LANGFUSE[Langfuse\nService\nor external SaaS]
            OTEL --> LANGFUSE
        end

        subgraph SECURITY ["🔐 Secrets & Config"]
            SEC[mva-secrets\nSecret\nAPI keys, JWT secret]
            CFG[mva-config\nConfigMap\nenv tuning params]
        end

    end

    USR -->|HTTPS :443| ING
    ING -->|/| MC
    ING -->|/api/*| API_SVC
    API_SVC --> API
    API -->|session state| REDIS_SVC
    REDIS_SVC --> REDIS
    API -->|append audit entries| PVC_AUDIT
    API -->|read/write JSON DB| PVC_DB
    API -.->|OTLP spans| OTEL
    API -.->|env vars| SEC & CFG

    style K8S fill:#0d1117,color:#c9d1d9,stroke:#30363d
    style INGRESS fill:#161b22,color:#c9d1d9,stroke:#4a90d9
    style FRONTEND fill:#161b22,color:#c9d1d9,stroke:#4ad9d9
    style BACKEND fill:#161b22,color:#c9d1d9,stroke:#4ad94a
    style CACHE fill:#161b22,color:#c9d1d9,stroke:#d9a04a
    style STORAGE fill:#161b22,color:#c9d1d9,stroke:#9a4ad9
    style TELEMETRY fill:#161b22,color:#c9d1d9,stroke:#d94a9a
    style SECURITY fill:#1e1410,color:#c9d1d9,stroke:#d94a4a
    style INTERNET fill:#0d1117,color:#c9d1d9,stroke:#30363d
```

---

## 3. Agent Swarm Internal State Machine

The reflection loop and routing logic inside `agent_router_poc.py`.

```mermaid
stateDiagram-v2
    [*] --> Routing : User Request

    state "🧭 SupervisorAgent" as Routing {
        [*] --> Classify
        Classify --> RouteToAgent : intent parsed
    }

    RouteToAgent --> LaborTimeAgent : MiniMOST / TMU query
    RouteToAgent --> BomAgent : BOM structure lookup
    RouteToAgent --> SimulationAgent : cycle-time / cost sim
    RouteToAgent --> DebateRoom : anomaly / complex decision

    state "🔄 Reflection Loop (max 3 retries)" as ReflectionLoop {
        [*] --> ToolExec
        ToolExec --> ValidateOutput : success
        ToolExec --> ReflectOnError : tool failure
        ReflectOnError --> ToolExec : retry with error context
        ValidateOutput --> [*] : passed
    }

    LaborTimeAgent --> ReflectionLoop
    BomAgent --> ReflectionLoop
    SimulationAgent --> ReflectionLoop
    DebateRoom --> ReflectionLoop

    ReflectionLoop --> SynthesisOutput : reflection complete
    ReflectionLoop --> ErrorResponse : max retries exceeded

    state "💬 Synthesis" as SynthesisOutput {
        [*] --> CheckHITL
        CheckHITL --> PendingAction : sensitive action detected
        CheckHITL --> StreamResponse : safe response
    }

    PendingAction --> [*] : awaiting human approval
    StreamResponse --> [*] : SSE to client
    ErrorResponse --> [*]
```

---

## 4. Cryptographic Provenance Chain

How every AI decision and robot command is chained into the tamper-evident audit log.

```mermaid
sequenceDiagram
    participant Agent as 🤖 Agent / VLM
    participant Prov as 🔐 Provenance Engine
    participant Log as 📋 TamperEvidentAuditLog
    participant ROS2 as 🦾 ROS2 Bridge

    Agent->>Prov: hash_payload(decision_dict)
    Note over Prov: SHA-256 of sorted JSON keys
    Prov-->>Agent: payload_hash (hex)

    Agent->>Prov: sign_payload(decision_dict)
    Note over Prov: Ed25519 sign with ephemeral key
    Prov-->>Agent: signature (base64)

    Agent->>Log: append_entry(payload, hash, signature, prev_hash)
    Note over Log: hash chain: entry.prev_hash == last_entry.hash
    Log-->>Agent: entry_id

    Agent->>ROS2: dispatch_command(robot_cmd + signature)
    Note over ROS2: MQTT header carries Ed25519 sig
    ROS2->>Log: append_entry(dispatch_event)
    ROS2-->>Agent: dispatch_ack

    Note over Log: Verify chain integrity anytime:\nmake verify-audit
```
