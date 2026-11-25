# OfinTech Desk Helm chart


## Install

```sh
helm install desk deploy/helm/ofintech-desk -n ofintech-desk --create-namespace \
  --set publicUrl=https://desk.example.com \
  --set ingress.enabled=true --set ingress.className=nginx --set ingress.tlsSecretName=desk-tls
```

## Master key

The chart generates it once and keeps it across upgrades.
Back it up:

```sh
kubectl -n ofintech-desk get secret desk-secret -o jsonpath='{.data.master-key}' | base64 -d
```

**With `helm template`, Argo CD or Flux, set `masterKey.existingSecret`**: the chart cannot read the cluster there
and would make a new key on every sync.

```sh
kubectl -n ofintech-desk create secret generic desk-master-key \
  --from-literal=master-key="$(docker run --rm ofintech0/ofintech-desk:1.5.0 python -m desk_engine keygen)"
# then: --set masterKey.existingSecret=desk-master-key
```

`helm uninstall` keeps the data volume and the generated key.

## Settings

See `values.yaml`. Other `DESK_*` settings, such as mail (`DESK_SMTP_*`) and S3 storage (`DESK_S3_*`),
are listed in [docs/DEPLOYMENT.md](../../../docs/DEPLOYMENT.md) and go in `extraEnv`:

```yaml
extraEnv:
  - name: DESK_SMTP_HOST
    value: smtp.example.com
  - name: DESK_SMTP_PASSWORD
    valueFrom:
      secretKeyRef: { name: desk-mail, key: password }
```