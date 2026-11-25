{{/* The Secret and key that hold the master key. */}}
{{- define "ofintech-desk.masterKeySecret" -}}
{{- .Values.masterKey.existingSecret | default (printf "%s-secret" .Release.Name) -}}
{{- end -}}

{{- define "ofintech-desk.masterKeySecretKey" -}}
{{- if .Values.masterKey.existingSecret -}}
{{- .Values.masterKey.existingSecretKey -}}
{{- else -}}
master-key
{{- end -}}
{{- end -}}

{{/* publicUrl: required, scheme and host only, https unless allowHttp. */}}
{{- define "ofintech-desk.publicUrl" -}}
{{- $url := required "publicUrl is required, e.g. --set publicUrl=https://desk.example.com" .Values.publicUrl -}}
{{- if not (regexMatch "^https?://[^/\\s]+$" $url) -}}
{{- fail (printf "publicUrl %q must be a scheme and a host only, e.g. https://desk.example.com (no path, no trailing slash)" $url) -}}
{{- end -}}
{{- if and (hasPrefix "http://" $url) (not .Values.allowHttp) -}}
{{- fail "publicUrl must use https. Set allowHttp=true only for a local test through kubectl port-forward" -}}
{{- end -}}
{{- $url -}}
{{- end -}}
