# K8s Production Runbook — MVA Platform

**Version:** 2.0.0  
**Cluster targets:** AWS EKS · Google GKE · On-Premises OpenShift  
**Chart path:** `deploy/k8s/mva-platform/`

---

## Table of Contents

1. [Prerequisites](#1-prerequisites)
2. [First-Time Cluster Bootstrap](#2-first-time-cluster-bootstrap)
3. [Secret Provisioning](#3-secret-provisioning)
4. [Deploy the Platform](#4-deploy-the-platform)
5. [Upgrade the Platform](#5-upgrade-the-platform)
6. [Rollback](#6-rollback)
7. [Scaling Operations](#7-scaling-operations)
8. [Graceful Handling of SSE Connections During Rolling Updates](#8-graceful-handling-of-sse-connections-during-rolling-updates)
9. [Monitoring & Alerting](#9-monitoring--alerting)
10. [Disaster Recovery — Redis Failure](#10-disaster-recovery--redis-failure)
11. [Audit Log Operations](#11-audit-log-operations)
12. [Decommission / Uninstall](#12-decommission--uninstall)
13. [Environment-Specific Overrides](#13-environment-specific-overrides)

---

## 1. Prerequisites

| Tool | Minimum Version | Install |
|------|----------------|---------|
| `kubectl` | 1.28 | https://kubernetes.io/docs/tasks/tools/ |
| `helm` | 3.14 | https://helm.sh/docs/intro/install/ |
| `helmfile` (optional) | 0.162 | https://github.com/helmfile/helmfile |
| `kubeseal` (optional) | 0.24 | https://github.com/bitnami-labs/sealed-secrets |
| `aws-cli` / `gcloud` / `oc` | latest | Per cloud provider |

### Verify Connectivity

```bash
kubectl cluster-info
kubectl get nodes -o wide
helm version
```

---

## 2. First-Time Cluster Bootstrap

### 2.1 Create the Namespace with Pod Security Standards

```bash
kubectl create namespace mva-platform

# Apply "restricted" Pod Security Standards label — rejects pods that run as root
# or request unnecessary capabilities before they are even scheduled.
kubectl label namespace mva-platform \
  pod-security.kubernetes.io/enforce=restricted \
  pod-security.kubernetes.io/enforce-version=latest \
  pod-security.kubernetes.io/warn=restricted \
  pod-security.kubernetes.io/warn-version=latest
```

### 2.2 Install the NGINX Ingress Controller (if not pre-installed)

```bash
helm repo add ingress-nginx https://kubernetes.github.io/ingress-nginx
helm repo update

helm install ingress-nginx ingress-nginx/ingress-nginx \
  --namespace ingress-nginx \
  --create-namespace \
  --set controller.replicaCount=2 \
  --set controller.resources.requests.cpu=100m \
  --set controller.resources.requests.memory=128Mi
```

### 2.3 Install cert-manager (TLS automation — recommended)

```bash
helm repo add jetstack https://charts.jetstack.io
helm repo update

helm install cert-manager jetstack/cert-manager \
  --namespace cert-manager \
  --create-namespace \
  --set installCRDs=true

# Create a ClusterIssuer for Let's Encrypt (replace email):
kubectl apply -f - <<'EOF'
apiVersion: cert-manager.io/v1
kind: ClusterIssuer
metadata:
  name: letsencrypt-prod
spec:
  acme:
    server: https://acme-v02.api.letsencrypt.org/directory
    email: platform-team@example.com
    privateKeySecretRef:
      name: letsencrypt-prod
    solvers:
      - http01:
          ingress:
            ingressClassName: nginx
EOF
```

### 2.4 AWS EKS — Install EFS CSI Driver (for RWX PVC)

```bash
# Install the AWS EFS CSI driver via the EKS add-on:
aws eks create-addon \
  --cluster-name <CLUSTER_NAME> \
  --addon-name aws-efs-csi-driver \
  --region <AWS_REGION>

# Create a StorageClass referencing your EFS FileSystem ID:
kubectl apply -f - <<'EOF'
apiVersion: storage.k8s.io/v1
kind: StorageClass
metadata:
  name: efs-sc
provisioner: efs.csi.aws.com
parameters:
  provisioningMode: efs-ap
  fileSystemId: fs-XXXXXXXXXXXXXXXX
  directoryPerms: "700"
  basePath: "/mva-audit"
mountOptions:
  - tls
reclaimPolicy: Retain
volumeBindingMode: Immediate
EOF
```

### 2.5 GKE — Enable Filestore CSI (for RWX PVC)

```bash
gcloud container clusters update <CLUSTER_NAME> \
  --update-addons=GcpFilestoreCsiDriver=ENABLED \
  --zone <ZONE>
```

### 2.6 OpenShift — Security Context Constraints

```bash
# Grant the mva-platform ServiceAccount the 'nonroot' SCC.
oc adm policy add-scc-to-user nonroot \
  -z mva-platform \
  -n mva-platform
```

### 2.7 Add & Update Helm Repositories

```bash
helm repo add bitnami oci://registry-1.docker.io/bitnamicharts
helm repo add kedacore https://kedacore.github.io/charts   # Optional: KEDA
helm repo update
```

### 2.8 Download Helm Dependencies

```bash
cd deploy/k8s/mva-platform
helm dependency update .
```

---

## 3. Secret Provisioning

**Never commit live secrets to version control.**

### Option A — AWS Secrets Manager + External Secrets Operator (Recommended for EKS)

```bash
# Install External Secrets Operator:
helm repo add external-secrets https://charts.external-secrets.io
helm install external-secrets external-secrets/external-secrets \
  --namespace external-secrets \
  --create-namespace

# Store secrets in AWS Secrets Manager:
aws secretsmanager create-secret \
  --name mva/production/platform-secrets \
  --secret-string '{
    "redis-url": "redis://:STRONG_PASSWORD@mva-platform-redis-master.mva-platform:6379/0",
    "redis-password": "STRONG_PASSWORD",
    "jwt-secret-key": "min-32-char-random-string-here-!!",
    "llm-api-key": "sk-..."
  }'

# Apply an ExternalSecret CR (customize the SecretStore name for your IAM role):
kubectl apply -n mva-platform -f - <<'EOF'
apiVersion: external-secrets.io/v1beta1
kind: ExternalSecret
metadata:
  name: mva-platform-secrets
spec:
  refreshInterval: 1h
  secretStoreRef:
    name: aws-secrets-manager
    kind: ClusterSecretStore
  target:
    name: mva-platform-secrets
    creationPolicy: Owner
  dataFrom:
    - extract:
        key: mva/production/platform-secrets
EOF
```

### Option B — Sealed Secrets (GitOps-friendly)

```bash
# Install Sealed Secrets controller:
helm repo add sealed-secrets https://bitnami-labs.github.io/sealed-secrets
helm install sealed-secrets sealed-secrets/sealed-secrets \
  --namespace kube-system

# Seal the secret (requires kubeseal + the controller's public cert):
kubectl create secret generic mva-platform-secrets \
  --from-literal=redis-url='redis://:STRONG_PASSWORD@mva-platform-redis-master.mva-platform:6379/0' \
  --from-literal=redis-password='STRONG_PASSWORD' \
  --from-literal=jwt-secret-key='min-32-char-random-string-here-!!' \
  --from-literal=llm-api-key='sk-...' \
  --dry-run=client -o yaml | \
  kubeseal --controller-namespace kube-system \
           --controller-name sealed-secrets \
           --format yaml > deploy/k8s/sealed-secret.yaml

# Commit sealed-secret.yaml safely — it is encrypted with the cluster's public key.
kubectl apply -n mva-platform -f deploy/k8s/sealed-secret.yaml
```

### Option C — Manual (Dev/Staging only)

```bash
kubectl create secret generic mva-platform-secrets \
  --namespace mva-platform \
  --from-literal=redis-url='redis://:REPLACE_ME@mva-platform-redis-master.mva-platform:6379/0' \
  --from-literal=redis-password='REPLACE_ME' \
  --from-literal=jwt-secret-key='REPLACE_ME_MIN_32_CHARS' \
  --from-literal=llm-api-key='REPLACE_ME'
```

---

## 4. Deploy the Platform

### 4.1 Prepare an Environment Values Override File

```bash
# Create values-production.yaml (do NOT commit secrets here):
cat > deploy/k8s/values-production.yaml <<'EOF'
imageRegistry: "123456789012.dkr.ecr.us-east-1.amazonaws.com"

apiServer:
  image:
    repository: "123456789012.dkr.ecr.us-east-1.amazonaws.com/mva/api-server"
    tag: "2.0.0"

watchdogWorker:
  image:
    repository: "123456789012.dkr.ecr.us-east-1.amazonaws.com/mva/api-server"
    tag: "2.0.0"

missionControl:
  image:
    repository: "123456789012.dkr.ecr.us-east-1.amazonaws.com/mva/mission-control"
    tag: "2.0.0"

ingress:
  host: "mva.my-company.com"
  tlsSecretName: "mva-tls-prod"
  annotations:
    cert-manager.io/cluster-issuer: "letsencrypt-prod"

auditLog:
  pvc:
    storageClassName: "efs-sc"
    storage: "50Gi"

redis:
  auth:
    existingSecret: "mva-platform-secrets"
    existingSecretPasswordKey: "redis-password"
EOF
```

### 4.2 Dry-Run (renders templates without applying)

```bash
helm install mva-platform ./deploy/k8s/mva-platform \
  --namespace mva-platform \
  --values deploy/k8s/values-production.yaml \
  --dry-run --debug 2>&1 | head -200
```

### 4.3 Install

```bash
helm install mva-platform ./deploy/k8s/mva-platform \
  --namespace mva-platform \
  --values deploy/k8s/values-production.yaml \
  --atomic \           # Rolls back automatically on failure.
  --timeout 10m \
  --wait               # Blocks until all Pods are Ready.
```

### 4.4 Verify the Deployment

```bash
# Check all pods are Running:
kubectl -n mva-platform get pods -w

# Expect output similar to:
# mva-platform-api-5d8c7b9f4-xxxxx   2/2  Running  0  2m
# mva-platform-api-5d8c7b9f4-yyyyy   2/2  Running  0  2m
# mva-platform-api-5d8c7b9f4-zzzzz   2/2  Running  0  2m
# mva-platform-watchdog-0             1/1  Running  0  2m
# mva-platform-watchdog-1             1/1  Running  0  2m
# mva-platform-mission-control-xxx    1/1  Running  0  2m

# API health check:
kubectl -n mva-platform exec deploy/mva-platform-api \
  -- python -c "import urllib.request; print(urllib.request.urlopen('http://localhost:8000/api/v1/health').read())"

# Inspect HPA status:
kubectl -n mva-platform get hpa

# Inspect PVC binding:
kubectl -n mva-platform get pvc

# Check ingress:
kubectl -n mva-platform get ingress
curl -k https://mva.my-company.com/api/v1/health
```

---

## 5. Upgrade the Platform

### 5.1 Standard Rolling Upgrade

```bash
# Build and push the new image tag:
docker build -t 123456789012.dkr.ecr.us-east-1.amazonaws.com/mva/api-server:2.1.0 \
  ./ddm-l6/backend
docker push 123456789012.dkr.ecr.us-east-1.amazonaws.com/mva/api-server:2.1.0

# Upgrade the release with the new image tag:
helm upgrade mva-platform ./deploy/k8s/mva-platform \
  --namespace mva-platform \
  --values deploy/k8s/values-production.yaml \
  --set apiServer.image.tag=2.1.0 \
  --atomic \
  --timeout 10m \
  --wait

# Verify the upgrade:
kubectl -n mva-platform rollout status deployment/mva-platform-api
kubectl -n mva-platform get pods -o jsonpath='{.items[*].spec.containers[0].image}'
```

### 5.2 Canary Upgrade (Watchdog StatefulSet)

The StatefulSet `partition` field enables canary-style upgrades:

```bash
# Step 1: set partition=1 so only pod-1 (standby) is upgraded first:
helm upgrade mva-platform ./deploy/k8s/mva-platform \
  --namespace mva-platform \
  --values deploy/k8s/values-production.yaml \
  --set watchdogWorker.image.tag=2.1.0 \
  --set-string 'watchdogWorker.updateStrategy.rollingUpdate.partition=1'

# Step 2: validate pod-1 is healthy (it will be the standby; pod-0 still runs old):
kubectl -n mva-platform get pod mva-platform-watchdog-1

# Step 3: promote pod-0 — set partition=0 to upgrade the primary:
helm upgrade mva-platform ./deploy/k8s/mva-platform \
  --namespace mva-platform \
  --values deploy/k8s/values-production.yaml \
  --set watchdogWorker.image.tag=2.1.0
```

---

## 6. Rollback

```bash
# List Helm release history:
helm history mva-platform --namespace mva-platform

# Roll back to the previous revision:
helm rollback mva-platform --namespace mva-platform --wait

# Roll back to a specific revision (e.g., revision 3):
helm rollback mva-platform 3 --namespace mva-platform --wait

# Verify rollback:
kubectl -n mva-platform rollout status deployment/mva-platform-api
kubectl -n mva-platform describe deployment mva-platform-api | grep Image
```

> **Note:** Rollback does NOT revert the Redis state or PVC data.
> If a schema migration was applied as part of the upgrade, a manual data migration rollback may be required.

---

## 7. Scaling Operations

### 7.1 Manual Horizontal Scale

```bash
# Scale API server to 10 replicas immediately (bypasses HPA floor temporarily):
kubectl -n mva-platform scale deployment/mva-platform-api --replicas=10

# Restore Helm-managed replica count (HPA will take over afterwards):
helm upgrade mva-platform ./deploy/k8s/mva-platform \
  --namespace mva-platform \
  --values deploy/k8s/values-production.yaml \
  --reuse-values
```

### 7.2 Inspect HPA Live Status

```bash
kubectl -n mva-platform get hpa mva-platform-api-hpa -w

# Sample output:
# NAME                     REFERENCE              TARGETS           MINPODS  MAXPODS  REPLICAS
# mva-platform-api-hpa     Deployment/mva-...api  cpu: 34%/70%      3        50       3
```

### 7.3 Pre-warm for Scheduled Traffic Spikes

```bash
# Pre-scale before a planned event (e.g., shift change at 06:00):
kubectl -n mva-platform patch hpa mva-platform-api-hpa \
  --type=merge \
  -p '{"spec": {"minReplicas": 15}}'

# Revert after the event:
kubectl -n mva-platform patch hpa mva-platform-api-hpa \
  --type=merge \
  -p '{"spec": {"minReplicas": 3}}'
```

---

## 8. Graceful Handling of SSE Connections During Rolling Updates

SSE (Server-Sent Events) connections are long-lived HTTP/1.1 streams. Without
careful handling, a pod replacement during a rolling update closes all active
SSE sessions abruptly, which the Mission Control UI experiences as a hard
disconnect.

### Strategy Implemented in This Chart

1. **`maxUnavailable: 0` (RollingUpdate strategy)**  
   New pods are spun up *before* old pods are terminated — zero downtime from the load-balancer perspective.

2. **`preStop` hook (`sleep 10`)**  
   Before SIGTERM is sent to the container, Kubernetes waits 10 seconds.  
   During these 10 seconds the pod is removed from the Service's Endpoint slice so the Ingress controller stops routing *new* connections to it, while *existing* SSE streams continue undisturbed.

3. **`terminationGracePeriodSeconds: 60`**  
   After SIGTERM is delivered, Kubernetes waits up to 60 seconds before SIGKILL.  
   Uvicorn's graceful shutdown sends SSE `\n\n` keep-alive until all stream handlers call `return`, giving the browser time to reconnect to a live pod.

4. **Frontend reconnection logic**  
   The Mission Control UI should implement `EventSource` with exponential back-off reconnect (standard browser behavior for SSE). On reconnect, it re-subscribes to the session's SSE endpoint using the `session_id` preserved in `RedisMemoryStore` — the new pod reconstructs the session from Redis without data loss.

5. **PodDisruptionBudget (`minAvailable: 2`)**  
   Node drain or cluster auto-scaler operations cannot take down more than
   `(replicaCount − minAvailable)` pods simultaneously.

### Verify Graceful Drain Manually

```bash
# Identify a target pod:
kubectl -n mva-platform get pods -l app.kubernetes.io/component=api-server

# Cordon a node (simulate node drain — do NOT use in production without approval):
kubectl cordon <NODE_NAME>
kubectl drain <NODE_NAME> --ignore-daemonsets --delete-emptydir-data \
  --grace-period=60 --timeout=120s

# Watch pod migration:
kubectl -n mva-platform get pods -w

# Uncordon after validation:
kubectl uncordon <NODE_NAME>
```

### Ingress Annotation for SSE Keep-Alive

The `nginx.ingress.kubernetes.io/proxy-read-timeout: "3600"` annotation in
`values.yaml` prevents the Ingress layer from closing idle SSE connections.  
For sessions that need to live longer than one hour, increase this value or
implement keep-alive pings from the backend every 30 seconds.

---

## 9. Monitoring & Alerting

### 9.1 Install Prometheus + Grafana (kube-prometheus-stack)

```bash
helm repo add prometheus-community https://prometheus-community.github.io/helm-charts
helm install kube-prometheus-stack prometheus-community/kube-prometheus-stack \
  --namespace monitoring \
  --create-namespace \
  --set prometheus.prometheusSpec.serviceMonitorSelectorNilUsesHelmValues=false
```

### 9.2 Key Metrics to Monitor

| Metric | Description | Alert Threshold |
|--------|-------------|-----------------|
| `mva_agent_routing_latency_p99` | P99 latency for agent routing decisions | > 10 s |
| `mva_pending_agent_routing_requests` | HPA custom metric — queue depth | > 200 (PagerDuty) |
| `mva_llm_token_usage_total` | Running LLM token budget | > 90% monthly budget |
| `mva_audit_chain_entries_total` | Monotonically increasing audit log counter | Flatline for > 5 min |
| `mva_swarm_debate_active` | Number of concurrent DebateRoom sessions | > 50 (warning) |
| `redis_connected_clients` | Number of Redis clients | > 500 |
| `kube_pod_container_status_restarts_total` | Pod restart count | > 5 in 10 min |

### 9.3 Critical Alerts (PrometheusRule)

```bash
kubectl apply -n mva-platform -f - <<'EOF'
apiVersion: monitoring.coreos.com/v1
kind: PrometheusRule
metadata:
  name: mva-platform-alerts
  labels:
    prometheus: kube-prometheus
    role: alert-rules
spec:
  groups:
    - name: mva-platform.critical
      rules:
        - alert: MVAApiHighErrorRate
          expr: |
            rate(http_requests_total{namespace="mva-platform",status=~"5.."}[5m]) /
            rate(http_requests_total{namespace="mva-platform"}[5m]) > 0.05
          for: 2m
          labels:
            severity: critical
          annotations:
            summary: "MVA API error rate > 5%"

        - alert: MVARedisDown
          expr: |
            up{job="mva-platform-redis-master"} == 0
          for: 1m
          labels:
            severity: critical
          annotations:
            summary: "MVA Redis master is unreachable — session state unavailable"

        - alert: MVAAuditLogStale
          expr: |
            increase(mva_audit_chain_entries_total[10m]) == 0
          for: 5m
          labels:
            severity: warning
          annotations:
            summary: "No audit entries written in the last 10 minutes"
EOF
```

---

## 10. Disaster Recovery — Redis Failure

The platform uses Redis for:
- `RedisMemoryStore`: agent session state (recoverable — user re-submits query)
- `AlignmentStore`: alignment constraint cache (durable — backed by PVC on reload)
- `TamperEvidentAuditLog`: event stream buffer → PVC (durable — XADD to stream)
- Watchdog leader election lock (ephemeral — re-elected within 30 s)

### 10.1 Redis Sentinel Failover (Bitnami Replication Mode)

With `redis.architecture: replication`, Bitnami Redis runs a Sentinel topology.
Failover is automatic (< 30 s) when the master is unavailable.

```bash
# Check Sentinel status:
kubectl -n mva-platform exec deploy/mva-platform-redis-master \
  -- redis-cli -a "$REDIS_PASSWORD" SENTINEL MASTERS

# Force manual failover (for maintenance):
kubectl -n mva-platform exec deploy/mva-platform-redis-master \
  -- redis-cli -a "$REDIS_PASSWORD" SENTINEL FAILOVER mymaster
```

### 10.2 Restore from Backup (RDB Snapshot)

```bash
# Scale down the API servers to prevent writes during restore:
kubectl -n mva-platform scale deployment/mva-platform-api --replicas=0

# Copy an RDB snapshot to the Redis master PVC:
kubectl -n mva-platform cp ./backup/dump.rdb \
  $(kubectl -n mva-platform get pod -l app.kubernetes.io/component=master \
    -o jsonpath='{.items[0].metadata.name}'):/data/dump.rdb

# Restart Redis master to load the snapshot:
kubectl -n mva-platform rollout restart statefulset/mva-platform-redis-master

# Scale API servers back up:
kubectl -n mva-platform scale deployment/mva-platform-api --replicas=3
```

---

## 11. Audit Log Operations

### 11.1 Verify Audit Chain Integrity

```bash
# Exec into any API pod and run the chain verifier:
kubectl -n mva-platform exec -it \
  $(kubectl -n mva-platform get pod -l app.kubernetes.io/component=api-server \
    -o jsonpath='{.items[0].metadata.name}') \
  -c api-server \
  -- python -c "
import json, hashlib
entries = [json.loads(l) for l in open('/app/data/audit/audit_chain.jsonl')]
for i, e in enumerate(entries[1:], 1):
    prev = entries[i-1]
    expected = hashlib.sha256(json.dumps(prev, sort_keys=True, separators=(',',':')).encode()).hexdigest()
    assert e['prev_hash'] == expected, f'CHAIN BROKEN at entry {i}'
print(f'Chain intact: {len(entries)} entries verified.')
"
```

### 11.2 Export Audit Log

```bash
# Copy the audit JSONL out of the cluster for archival:
kubectl -n mva-platform cp \
  $(kubectl -n mva-platform get pod -l app.kubernetes.io/component=api-server \
    -o jsonpath='{.items[0].metadata.name}'):/app/data/audit/audit_chain.jsonl \
  ./audit_export_$(date +%Y%m%d).jsonl
```

### 11.3 Rotate the Ed25519 Signing Key

The provenance engine uses an ephemeral Ed25519 key (process-scoped for PoC).
For production, load from a KMS or Kubernetes Secret and rotate quarterly:

```bash
# Generate a new key pair (example using openssl):
openssl genpkey -algorithm ed25519 -out mva-signing-key.pem
openssl pkey -in mva-signing-key.pem -pubout -out mva-signing-key-pub.pem

# Update the Secret:
kubectl -n mva-platform create secret generic mva-signing-keys \
  --from-file=private-key=mva-signing-key.pem \
  --from-file=public-key=mva-signing-key-pub.pem \
  --dry-run=client -o yaml | kubectl apply -f -

# Trigger a rolling restart to pick up the new key:
kubectl -n mva-platform rollout restart deployment/mva-platform-api
```

---

## 12. Decommission / Uninstall

```bash
# Uninstall the Helm release (Secrets and PVCs annotated with
# 'helm.sh/resource-policy: keep' are RETAINED):
helm uninstall mva-platform --namespace mva-platform

# Verify retained resources:
kubectl -n mva-platform get pvc,secret

# To permanently delete the audit PVC (IRREVERSIBLE — requires authorization):
# kubectl -n mva-platform delete pvc mva-platform-audit-log

# Delete the namespace (removes all remaining resources):
# kubectl delete namespace mva-platform
```

---

## 13. Environment-Specific Overrides

### Staging

```yaml
# values-staging.yaml
apiServer:
  replicaCount: 1
  resources:
    requests:
      cpu: "250m"
      memory: "256Mi"
    limits:
      cpu: "1000m"
      memory: "1Gi"

hpa:
  apiServer:
    minReplicas: 1
    maxReplicas: 5

redis:
  architecture: standalone

auditLog:
  pvc:
    storage: "2Gi"
    storageClassName: "standard"  # Use cluster default (no EFS needed in staging).
    accessMode: ReadWriteOnce     # Single-node PVC is fine for staging.
  auditSidecar:
    enabled: false                # Direct write is acceptable in single-pod staging.

ingress:
  host: "mva-staging.example.com"
  annotations:
    nginx.ingress.kubernetes.io/ssl-redirect: "false"
```

Deploy staging:

```bash
helm upgrade --install mva-platform ./deploy/k8s/mva-platform \
  --namespace mva-staging \
  --create-namespace \
  --values deploy/k8s/values-production.yaml \
  --values deploy/k8s/values-staging.yaml \
  --wait
```

### OpenShift-Specific Overrides

```yaml
# values-openshift.yaml
apiServer:
  securityContext:
    runAsNonRoot: true
    runAsUser: null    # OpenShift assigns arbitrary UIDs automatically.
    readOnlyRootFilesystem: true
    allowPrivilegeEscalation: false
    capabilities:
      drop:
        - ALL

missionControl:
  securityContext:
    runAsNonRoot: true
    runAsUser: null
    readOnlyRootFilesystem: false
    allowPrivilegeEscalation: false
    capabilities:
      drop:
        - ALL

ingress:
  className: ""   # OpenShift uses Routes, not Ingress.
  enabled: false  # Create an OpenShift Route separately.
```
