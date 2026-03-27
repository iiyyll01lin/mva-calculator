{{/*
Expand the name of the chart.
*/}}
{{- define "mva-platform.name" -}}
{{- default .Chart.Name .Values.nameOverride | trunc 63 | trimSuffix "-" }}
{{- end }}

{{/*
Create a default fully qualified app name.
We truncate at 63 chars because some Kubernetes name fields are limited to that.
*/}}
{{- define "mva-platform.fullname" -}}
{{- if .Values.fullnameOverride }}
{{- .Values.fullnameOverride | trunc 63 | trimSuffix "-" }}
{{- else }}
{{- $name := default .Chart.Name .Values.nameOverride }}
{{- if contains $name .Release.Name }}
{{- .Release.Name | trunc 63 | trimSuffix "-" }}
{{- else }}
{{- printf "%s-%s" .Release.Name $name | trunc 63 | trimSuffix "-" }}
{{- end }}
{{- end }}
{{- end }}

{{/*
Create chart label value (name-version).
*/}}
{{- define "mva-platform.chart" -}}
{{- printf "%s-%s" .Chart.Name .Chart.Version | replace "+" "_" | trunc 63 | trimSuffix "-" }}
{{- end }}

{{/*
Resolve the image tag: component-specific tag, else global imageTag.
Usage: {{ include "mva-platform.imageTag" (dict "image" .Values.apiServer.image "root" .) }}
*/}}
{{- define "mva-platform.imageTag" -}}
{{- if .image.tag -}}
{{- .image.tag -}}
{{- else -}}
{{- .root.Values.imageTag -}}
{{- end -}}
{{- end }}

{{/*
Common labels applied to every resource.
*/}}
{{- define "mva-platform.labels" -}}
helm.sh/chart: {{ include "mva-platform.chart" . }}
{{ include "mva-platform.selectorLabels" . }}
app.kubernetes.io/version: {{ .Chart.AppVersion | quote }}
app.kubernetes.io/managed-by: {{ .Release.Service }}
{{- with .Values.global.commonLabels }}
{{ toYaml . }}
{{- end }}
{{- end }}

{{/*
Selector labels (used in matchLabels — must be stable across upgrades).
*/}}
{{- define "mva-platform.selectorLabels" -}}
app.kubernetes.io/name: {{ include "mva-platform.name" . }}
app.kubernetes.io/instance: {{ .Release.Name }}
{{- end }}

{{/*
ServiceAccount name.
*/}}
{{- define "mva-platform.serviceAccountName" -}}
{{- if .Values.serviceAccount.create }}
{{- default (include "mva-platform.fullname" .) .Values.serviceAccount.name }}
{{- else }}
{{- default "default" .Values.serviceAccount.name }}
{{- end }}
{{- end }}

{{/*
Render image pull secrets list.
*/}}
{{- define "mva-platform.imagePullSecrets" -}}
{{- range .Values.global.imagePullSecrets }}
- name: {{ . }}
{{- end }}
{{- end }}

{{/*
Render secretEnv entries as envFrom / env items.
Usage: {{ include "mva-platform.secretEnvVars" .Values.apiServer.secretEnv }}
*/}}
{{- define "mva-platform.secretEnvVars" -}}
{{- range . }}
- name: {{ .name }}
  valueFrom:
    secretKeyRef:
      name: {{ .secretName }}
      key: {{ .secretKey }}
{{- end }}
{{- end }}
